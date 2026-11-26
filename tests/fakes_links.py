"""Fakes for the link tests (tests/test_links_*.py): a fake Chrome, a fake page, a fresh home per test.

Nothing here reaches the real browser, the real page or the real home: the fake Chrome stands in for osascript,
pgrep, open, macOS's consent API, the clock and the load, through the names browser.py keeps for them; the fake page
is a small HTTP server on a port the system picks.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from switchboard import artifacts, browser, core, linkapp

A, B, C, G = 'ana@work.example', 'ana@home.example', 'ana@side.example', 'ana@gmail.example'
ID1, ID2, ID3, ID4 = 'a' * 20, 'b' * 20, 'c' * 20, 'd' * 20
LINK = 'https://claude.ai/artifact/%s'


class Home(unittest.TestCase):
    """A fresh home (HOME), Claude folder and data folder for each test, and Switchboard's caches cleared."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-b4-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, 'home')
        self.data = os.path.join(self.tmp, 'data')
        os.makedirs(self.home)
        env = mock.patch.dict(os.environ, {'HOME': self.home, 'SWITCHBOARD_HOME': self.data,
                                           'CLAUDE_CONFIG_DIR': os.path.join(self.home, '.claude')})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop('SWITCHBOARD_PORT', None)        # put back by the patch above when the test ends
        self.reset_caches()
        self.addCleanup(self.reset_caches)

    def reset_caches(self):
        browser.HINTS.update(at=0, key=None, value={})
        browser.APPS.clear()
        browser.BLIND['until'] = 0
        browser.TABS.update(state=None, why=None, until=0.0, backoff=0.0, said=None)
        browser.CONSENT.update(state=None, code=None, at=-1e9, asking=False)
        artifacts._MAP['last'] = None
        artifacts.REPROVE.update(running=False, looked=-1e9, forced=None)
        linkapp.CACHE.update(at=0, value=None, path=None)
        linkapp.HANDLERS.update(at=0, value=None)
        linkapp.PAGE['up'] = False
        browser.PAGE_PROCESS['on'] = False

    def patch(self, target, name, value):
        p = mock.patch.object(target, name, value)
        p.start()
        self.addCleanup(p.stop)

    def state(self, active=None, **accounts):
        """Save a state with these accounts ({email: fields}), in this order."""
        st = core.blank_state()
        for i, (email, more) in enumerate(accounts.items()):
            a = core.ensure_account(st, email)
            a.update(connected=True, slot=i + 1, **more)
        if active:
            st['active'] = active
            st['timeline'] = [[active, time.time() - 3600]]
        core.save(st)
        return st

    def page(self, aid, owner=None, src=None, profile=None, title=None, t=None):
        """Add an artifact to the saved state."""
        with core.locked() as st:
            it = artifacts.blank_item(aid, t or time.time() - 600)
            it.update(owner=owner, src=src, profile=profile, title=title, ot=t or time.time() - 600)
            st['artifacts'].insert(0, it)
            st['scan_v'] = artifacts.SCAN_V
        return it


class FakeChrome(object):
    """Google Chrome as browser.py sees it: profiles in Local State, extension folders, and tabs that appear and
    change title on a fake clock.

    logins: {profile: the account its claude.ai is signed in as, or None when signed out}
    owners: {artifact id: the account that can open it}; titles: {artifact id: its title}
    """

    def __init__(self, test, profiles, logins=None, extensions=(), running=True, owners=None, titles=None):
        self.test = test
        self.profiles = dict(profiles)
        self.logins = dict(logins or {})
        self.owners = dict(owners or {})
        self.titles = dict(titles or {})
        self.running = running
        self.t = 1000.0
        self.tabs = []
        self.closed = []
        self.opened = []
        self.next_tab = 100
        self.delay = 1.0            # until a new tab shows up
        self.start_delay = 8.0      # a cold start, before any tab shows up
        self.settle = 1.0           # "Claude" (loading) until the real title
        self.hold = 0.0             # seconds of "Just a moment..." before the page
        self.outage = False
        self.consent = 0            # what macOS's consent API answers (an OSStatus)
        self.consent_calls = []
        self.osa = 'ok'             # 'ok', 'hang', 'denied', 'busy'
        self.osa_calls = 0
        self.load = 0.2
        d = os.path.join(browser.support(), 'Google', 'Chrome')
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'Local State'), 'w') as f:
            json.dump({'profile': {'info_cache': dict((p, {'name': n}) for p, n in self.profiles.items())}}, f)
        for p in self.profiles:
            os.makedirs(os.path.join(d, p, 'Extensions'), exist_ok=True)
        for p in extensions:
            os.makedirs(os.path.join(d, p, 'Extensions', browser.CLAUDE_EXTENSION, '1.2.3_0'), exist_ok=True)
        os.makedirs(os.path.join(test.home, 'Applications', 'Google Chrome.app'), exist_ok=True)
        self.dir = d
        browser.APPS.clear()
        test.patch(browser, 'app_dirs', lambda: [os.path.join(test.home, 'Applications')])
        test.patch(browser, '_run', self.run)
        test.patch(browser, '_spawn', self.spawn)
        test.patch(browser, '_mono', lambda: self.t)
        test.patch(browser, '_sleep', self.sleep)
        test.patch(browser, 'CONSENT_CALL', self.consent_call)
        test.patch(browser, 'LOAD', lambda: self.load)
        test.patch(browser, 'PAGE_PROCESS', {'on': True})     # the page's own process: the consent API speaks

    def sleep(self, s):
        self.t += s

    def consent_call(self, bundle, ask):
        self.consent_calls.append((bundle, ask))
        c = self.consent
        return c(bundle, ask) if callable(c) else c

    # ---- open ----

    def spawn(self, args):
        assert args[0] == browser.OPEN, args
        background = args[1] == '-gna'
        profile = next((a.split('=', 1)[1] for a in args if a.startswith('--profile-directory=')), None)
        url = args[-1]
        self.opened.append((profile, url, background))
        start = self.t
        if not self.running:
            self.running = True
            start += self.start_delay
        self.next_tab += 1
        appear = start + self.delay
        plan = self.plan(profile, url, appear)
        self.tabs.append({'win': 7, 'id': self.next_tab, 'plan': plan, 'profile': profile})

    def plan(self, profile, url, at):
        m = re.search(r'/artifact/([A-Za-z0-9_-]{16,})', url)
        if not m:
            return [(at, url, 'Claude')]
        aid = m.group(1)
        out = []
        if self.hold:
            out.append((at, url, 'Just a moment...'))
            at += self.hold
        login = self.logins.get(profile, 'nobody') if profile else self.logins.get(None, 'nobody')
        if self.outage:
            return out + [(at, url, 'Claude'), (at + self.settle, url, 'Something went wrong')]
        if login is None:
            return out + [(at, 'https://claude.ai/login?returnTo=%2Fartifact%2F' + aid, 'Claude')]
        if self.owners.get(aid) == login:
            return out + [(at, url, 'Claude'), (at + self.settle, url, '%s – Claude' % self.titles.get(aid, 'Page'))]
        return out + [(at, url, 'Claude'), (at + self.settle, url, 'Page not found – Claude')]

    # ---- osascript and pgrep ----

    def visible(self):
        rows = []
        for tab in self.tabs:
            seen = [x for x in tab['plan'] if x[0] <= self.t]
            if seen:
                _, url, title = seen[-1]
                rows.append((tab['win'], tab['id'], url, title))
        return rows

    def run(self, cmd, timeout):
        if cmd[0] == browser.PGREP:
            return subprocess.CompletedProcess(cmd, 0 if self.running else 1, '', '')
        assert cmd[0] == browser.OSASCRIPT, cmd
        script = cmd[2]
        m = re.search(r'window id (\d+) whose id is (\d+)', script)
        if m:
            win, tid = int(m.group(1)), int(m.group(2))
            self.closed.append(tid)
            self.tabs = [t for t in self.tabs if not (t['win'] == win and t['id'] == tid)]
            return subprocess.CompletedProcess(cmd, 0, '', '')
        self.osa_calls += 1
        assert 'is running then' in script, 'the tab script must never launch the browser'
        if self.osa == 'hang':
            self.t += timeout
            raise subprocess.TimeoutExpired(cmd, timeout)
        if self.osa == 'denied':
            return subprocess.CompletedProcess(cmd, 1, '', 'execution error: Not authorized to send Apple events to '
                                                          'Google Chrome. (-1743)')
        if self.osa == 'busy':
            return subprocess.CompletedProcess(cmd, 1, '', 'execution error: Google Chrome got an error: '
                                                          'Connection is invalid. (-609)')
        if not self.running:
            return subprocess.CompletedProcess(cmd, 0, '\n', '')
        out = ''.join('%d\t%d\t%s\t%s\n' % row for row in self.visible() if 'claude.ai' in row[2])
        return subprocess.CompletedProcess(cmd, 0, out, '')


class FakePage(object):
    """A stand-in for Switchboard's page on a port the system picks: it answers POSTs as told ('ok' at once, 'slow'
    after `delay` seconds, 'error' 500, 'said' 400 with `answer` as its words, 'refused' 403) and writes down what it
    was sent, with its headers."""

    def __init__(self, test, mode='ok', delay=5.0, answer=None):
        self.mode, self.delay, self.answer = mode, delay, answer or {}
        self.got = []
        page = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get('Content-Length') or 0))
                page.got.append({'path': self.path, 'headers': dict((k.lower(), v) for k, v in self.headers.items()),
                                 'body': json.loads(body or b'{}'), 'at': time.time()})
                if page.mode == 'slow':
                    time.sleep(page.delay)
                code = {'error': 500, 'said': 400, 'refused': 403}.get(page.mode, 200)
                data = json.dumps(dict(page.answer, ok=True) if code == 200 else
                                  page.answer if code == 400 else {'error': 'forbidden'}).encode()
                try:
                    self.send_response(code)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except OSError:
                    pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        assert self.port not in (7798, 7799)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        test.addCleanup(self.server.server_close)
        test.addCleanup(self.server.shutdown)


def time_bound(seconds):
    """A wall-clock bound for a test that runs at background priority: `seconds` on a Mac that is not overloaded,
    more while the load per core is over 2 (a starved process can't keep a real-time promise; the test then still
    proves it did not wait for the slow side, whose delay is much longer)."""
    try:
        lpc = os.getloadavg()[0] / max(1, os.cpu_count() or 1)
    except OSError:
        lpc = 0
    return seconds if lpc <= 2 else seconds * 5


def free_port():
    """A port nothing listens on (bound and closed again), never one of Switchboard's real ones."""
    import socket
    while True:
        s = socket.socket()
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
        s.close()
        if port not in (7798, 7799):
            return port


# ---------- the link app, built from router/ into a temporary folder (once per source) ----------

_LINKS = {}


def link_app():
    """The link app's program built from router/SwitchboardLinks.swift (cached by the source's hash in the temp
    folder), or None when there is no Swift compiler here."""
    if 'path' in _LINKS:
        return _LINKS['path']
    import hashlib
    src = os.path.join(ROOT, 'router', 'SwitchboardLinks.swift')
    swiftc = shutil.which('swiftc')
    if not swiftc or subprocess.run(['/usr/bin/xcode-select', '-p'], capture_output=True).returncode != 0:
        _LINKS['path'] = None
        return None
    with open(src, 'rb') as f:
        digest = hashlib.sha256(f.read()).hexdigest()[:16]
    out_dir = os.path.join(tempfile.gettempdir(), 'switchboard-b4-links-%s' % digest)
    exe = os.path.join(out_dir, 'SwitchboardLinks')
    if not os.path.exists(exe):
        os.makedirs(out_dir, exist_ok=True)
        tmp = exe + '.%d' % os.getpid()
        r = subprocess.run([swiftc, '-Onone', '-o', tmp, src], capture_output=True, timeout=900)
        if r.returncode != 0:
            raise AssertionError('the link app did not build:\n' + r.stderr.decode('utf-8', 'replace')[-3000:])
        os.replace(tmp, exe)
    _LINKS['path'] = exe
    return exe
