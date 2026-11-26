import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Tests for the wiring of the app: the plan in the state (and the page agreeing with it), the weekly
# readings and the week ahead, History's Work read in the background, the status line's small file
# and its on/off switch, keeping the Mac awake and notifications on the server's tick, the commands
# `usage`, `awake` and `statusline`, and every endpoint.
#
# The engine, caffeinate and notifications are fakes here. Every folder is a throwaway one.
import calendar
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import __main__ as cli
from switchboard import (artifacts, awake, browser, core, demo, engine, keep, linkapp, models, notify, plan,
                         server, statusline, usage)

D, H = 86400, 3600
REAL = usage.summarize                                         # before any test swaps it for a fake
PRECOMPILE = statusline.precompile
REAL_AUTO = engine.auto_settings
FIXTURE = os.path.join(HERE, 'fixtures', 'usage', 'projects')
FIXTURE_NOW = calendar.timegm((2026, 11, 12, 0, 0, 0))       # after the made-up history, which ends Nov 11
CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
A, B, C = 'ana@work.example', 'ana@home.example', 'ana@side.example'
AC = "Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t80%; charged; 0:00 remaining present: true\n"
LOW_BATTERY = "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t10%; discharging; 0:40 remaining present: true\n"
LID_OPEN = '| |   "AppleClamshellState" = No\n'
# Every word a Usage sentence may use: numbers and these fixed words, never a title or a label.
NOTE_WORDS = set(('all mostly and workflow workflows helper helpers chat chats agent agents the largest took started from '
                  'your own turns in of these had no model set request requests smaller fable opus sonnet haiku other').split())


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace('+00:00', 'Z')


def row(n, email, active, week, five=0, week_in=3 * D, five_in=2 * H, age=1, **more):
    """One account as engine.listing describes it (the app's own row shape)."""
    now = time.time()
    r = engine._row(id='acct-%d' % n, number=n, email=email, active=active, alias=email.split('@')[1].split('.')[0],
                    usage={'session': {'pct': five, 'resets': iso(now + five_in), 'pace': None, 'lasts': None},
                           'weekly': {'pct': week, 'resets': iso(now + week_in), 'pace': None, 'lasts': None},
                           'scoped': [], 'spend': None},
                    usage_at=now - age, source='endpoint')
    r.update(more)
    return r


class FakeProc(object):
    """Stands in for caffeinate."""
    started = []

    def __init__(self, cmd, **kw):
        self.cmd, self.rc = cmd, None
        FakeProc.started.append(cmd)

    def poll(self):
        return self.rc

    def terminate(self):
        self.rc = -15

    def kill(self):
        self.rc = -9

    def wait(self, timeout=None):
        return self.rc


class Sandbox(unittest.TestCase):
    """A throwaway home, data folder and Claude Code folder; a fake engine; no real notification or caffeinate."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-app-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, 'home')
        self.data = os.path.join(self.tmp, 'data')
        self.claude = os.path.join(self.home, '.claude')
        self.projects = os.path.join(self.claude, 'projects')
        os.makedirs(self.projects)
        env = mock.patch.dict(os.environ, {'HOME': self.home, 'SWITCHBOARD_HOME': self.data, 'CLAUDE_CONFIG_DIR': self.claude})
        env.start()
        self.addCleanup(env.stop)
        for k in ('SWITCHBOARD_STATE', 'SWITCHBOARD_NOTIFY_STATE', 'SWITCHBOARD_ENGINE', 'SWITCHBOARD_PORT'):
            os.environ.pop(k, None)
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = False
        self.shown = []
        self.patch(server, 'NOTIFY_RUN', lambda cmd: self.shown.append(cmd) or 0)
        for target, values in ((server.HELPERS, {'awake': None}), (server.USAGE, {'page': None, 'at': 0, 'running': False})):
            p = mock.patch.dict(target, values)
            p.start()
            self.addCleanup(p.stop)
        self.rows = []
        self.auto = {'available': True, 'service': True, 'enabled': True, 'session_at': 90, 'weekly_at': 95,
                     'model_at': 95, 'strategy': 'refills-first', 'models': []}
        self.patch(engine, 'rows', lambda: [dict(r) for r in self.rows])
        self.patch(engine, 'listing', lambda: {'rows': [dict(r) for r in self.rows], 'live': None, 'disagree': False})
        self.patch(engine, 'auto_settings', lambda: dict(self.auto))
        self.patch(engine, 'foreign_switcher', lambda folder=None: None)
        self.patch(keep, 'keep_pass', lambda: None)             # the engine's login keeping has its own tests
        self.patch(artifacts, 'refresh', lambda: None)          # the history scan has its own tests
        self.patch(statusline, 'precompile', lambda *a, **k: None)   # never writes bytecode into the source folder
        FakeProc.started = []

    def patch(self, target, name, value):
        p = mock.patch.object(target, name, value)
        p.start()
        self.addCleanup(p.stop)

    def notes(self):
        """The titles of the notifications that would have been shown."""
        return [c[8] for c in self.shown]          # osascript -e 'on run argv' ... -e 'end run' -- TITLE BODY

    def fake_awake(self, power=AC):
        helper = awake.Awake(run=lambda cmd, timeout=5: power if 'pmset' in cmd[0] else LID_OPEN, popen=FakeProc,
                             root=self.projects)
        server.HELPERS['awake'] = helper
        return helper

    def transcript(self, age=0):
        path = os.path.join(self.projects, '-x', 'aaaaaaaa-0000-4000-8000-000000000001.jsonl')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'a') as f:
            f.write(json.dumps({'type': 'assistant', 'message': {'stop_reason': 'end_turn', 'content': []}}) + '\n')
        t = time.time() - age
        os.utime(path, (t, t))
        os.utime(os.path.dirname(path), (t, t))
        return path

    def three(self):
        self.rows = [row(1, A, True, 40), row(2, B, False, 20, week_in=2 * D), row(3, C, False, 92, week_in=1 * D)]


# ---------- paths ----------

class PathsAreReadWhenUsed(Sandbox):
    def test_every_home_path_follows_the_environment_at_call_time(self):
        def where():
            return [core.data_dir(), core.STATE_PATH, core.LOG_PATH, core.LOGIN_DIR, core.CLAUDE_JSON, core.projects(),
                    engine.AutoHost().pid_file(), engine.AutoHost().log_file(), browser.support(),
                    linkapp.app_path(), statusline.state_path(), statusline.settings_path(), statusline.live_dir(),
                    awake.projects_root(), notify.state_path()]
        first = where()
        self.assertTrue(all(p.startswith(self.tmp) for p in first), first)
        other = tempfile.mkdtemp(prefix='switchboard-app-other-')
        self.addCleanup(shutil.rmtree, other, True)
        with mock.patch.dict(os.environ, {'HOME': other, 'SWITCHBOARD_HOME': os.path.join(other, 'd'),
                                          'CLAUDE_CONFIG_DIR': os.path.join(other, 'c')}):
            second = where()
        self.assertTrue(all(p.startswith(other) for p in second), second)


# ---------- the plan ----------

class Plan(Sandbox):
    def facts(self):
        return {'profiles': {}, 'browser': {'name': 'Google Chrome', 'present': False, 'tabs_blocked': False},
                'engine': {'installed': True, 'claude': True},
                'links': {'installed': False, 'on': False, 'browser': None, 'home': ''}, 'auto': dict(self.auto),
                'setup_hidden': True, 'now': time.time(), 'demo': False}

    def state(self):
        st = core.blank_state()
        server.apply_rows(st, self.rows)
        return st

    def test_the_lines_are_the_ones_automatic_switching_uses(self):
        self.assertEqual(server.plan_settings({'session_at': 90, 'weekly_at': 95}),
                         {'session_line': 90, 'weekly_line': 95, 'model_line': 95.0, 'models': [], 'weekly_set': True,
                          'margin': 5.0, 'landing_room': 5.0, 'strategy': 'refills-first'})
        self.assertEqual(server.plan_settings({'session_at': 80, 'weekly_at': 85, 'model_at': 92,
                                               'strategy': 'most-room', 'models': ['Fable']}),
                         {'session_line': 80, 'weekly_line': 85, 'model_line': 92, 'models': ['Fable'],
                          'weekly_set': True, 'margin': 5.0, 'landing_room': 5.0, 'strategy': 'most-room'})
        # the engine's defaults: 90 for the 5-hour window, 95 for the weekly and a model's week, what refills first
        self.assertEqual(server.plan_settings({}), {'session_line': 90.0, 'weekly_line': 95.0, 'model_line': 95.0,
                                                    'models': [], 'weekly_set': True, 'margin': 5.0,
                                                    'landing_room': 5.0, 'strategy': 'refills-first'})
        self.assertEqual(server.plan_settings({'strategy': 'best'})['strategy'], 'refills-first')    # not one of ours

    def test_the_state_carries_plan_py_s_order_and_next(self):
        self.three()
        st, now = self.state(), time.time()
        view = server.render(st, self.facts())
        lines = server.plan_settings(self.auto)
        ranked = plan.order(server.plan_accounts(st), lines, now)
        self.assertEqual([o['email'] for o in view['plan']['order']], [j['name'] for j in ranked])
        self.assertEqual([o['rank'] for o in view['plan']['order']], [1, 2, 3])
        self.assertEqual(view['plan']['next'], plan.next_account(server.plan_accounts(st), lines, now)['account']['name'])
        self.assertEqual(view['plan']['next'], B)                 # C is past 85% of its week: no landing room
        side = next(o for o in view['plan']['order'] if o['email'] == C)
        self.assertEqual((side['usable'], side['reason'], side['left']), (False, 'not enough room', 3))
        self.assertEqual(set(view['plan']['order'][0]), {'email', 'rank', 'usable', 'reason', 'left', 'back_at'})

    def test_signed_out_and_unmanaged_accounts_are_never_next(self):
        self.rows = [row(1, A, True, 40), row(3, C, False, 10, status='needs_login')]
        self.assertIsNone(server.render(self.state(), self.facts())['plan']['next'])
        self.rows = [row(1, A, True, 40), row(2, B, False, 10, unmanaged=True)]
        view = server.render(self.state(), self.facts())
        self.assertIsNone(view['plan']['next'])
        self.assertEqual(view['plan']['why'], 'no other account has room')

    def test_an_order_entry_with_no_account_is_skipped(self):
        self.three()
        st = self.state()
        st['order'].append('gone@nowhere.example')
        self.assertEqual(len(server.plan_view(st, server.plan_settings(self.auto), time.time())['order']), 3)

    def test_the_page_draws_slot_order_and_the_servers_next(self):
        with open(os.path.join(HERE, '..', 'switchboard', 'web', 'index.html'), encoding='utf-8') as f:
            html = f.read()
        self.assertIn('const bySlot = () => S.accounts.slice().sort((x, y) => (x.slot || 99) - (y.slot || 99));', html)
        body = html[html.index('function nextEmail()'):html.index('function linesOf(')]
        self.assertIn("const n = 'next' in S ? S.next", body)
        self.assertIn('if (!S.auto || !S.auto.enabled) return null;', body)

    def test_next_is_the_plans_and_only_while_automatic_switching_runs(self):
        self.three()
        st = self.state()
        v = server.render(st, dict(self.facts(), auto=dict(self.auto, on=True)))
        self.assertEqual((v['next'], v['plan']['next']), (B, B))
        v = server.render(st, dict(self.facts(), auto=dict(self.auto, on=False)))
        self.assertEqual((v['next'], v['plan']['next']), (None, B))     # the plan still knows; nothing is marked
        self.rows = [row(1, A, True, 40), row(2, B, False, 10, status='needs_login')]
        v = server.render(self.state(), dict(self.facts(), auto=dict(self.auto, on=True)))
        self.assertIsNone(v['next'])                                    # never one that needs a fresh login


class PlanWords(Sandbox):
    """Under each email: its plan and organisation, the words the page and the terminal both write."""

    def test_the_tier_and_the_organisation(self):
        cases = ((dict(plan='Ultra', tier='default_claude_max_20x', org_type='claude_max', org="ana@x.example's Organization"),
                  'Max 20x · Personal'),
                 (dict(plan='Max 5x', tier='default_claude_max_5x', org='Ana Lee’s Organization'), 'Max 5x · Personal'),
                 (dict(plan='Team', tier='default_claude_max_5x', org_type='claude_team', org='Harbor & Co'), 'Team · Harbor & Co'),
                 (dict(plan='Enterprise', org_type='claude_enterprise', org='Northwind'), 'Enterprise · Northwind'),
                 (dict(plan='Pro', tier='default_claude_ai', org_type='claude_pro', org="b@y.example's Organization"), 'Pro · Personal'),
                 (dict(plan='Ultra'), 'Max 20x'), (dict(org='Northwind'), 'Northwind'), (dict(), ''))
        for a, words in cases:
            with self.subTest(account=a):
                self.assertEqual(server.plan_line(a), words)

    def test_read_from_the_engines_plan_and_kept_in_the_state(self):
        self.rows = [row(1, A, True, 40, org_name="ana@work.example's Organization", plan='Ultra'),
                     row(2, B, False, 10, org_name='Home Co', plan='Team'),
                     row(3, C, False, 10, plan='Pro')]
        server.tick()
        v = server.render(core.load(), dict(Plan.facts(self)))
        got = dict((a['email'], (a['plan_line'], a['plan'])) for a in v['accounts'])
        self.assertEqual(got[A], ('Max 20x · Personal', 'Ultra'))
        self.assertEqual(got[B], ('Team · Home Co', 'Team'))
        self.assertEqual(got[C], ('Pro', 'Pro'))
        self.rows[0]['plan'] = None                                     # a later row without it keeps what was read
        server.tick()
        a = next(x for x in server.render(core.load(), dict(Plan.facts(self)))['accounts'] if x['email'] == A)
        self.assertEqual(a['plan_line'], 'Max 20x · Personal')


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class ChromePage(unittest.TestCase):
    """The real page, served by the real handler (demo state set by each test), in headless Chrome with its own
    throwaway profile and no window. Chrome missing: skipped. Chrome present but the page draws nothing: a failure."""

    PRELUDE = r"""
(function () {
  const sent = [], realFetch = window.fetch;
  window.fetch = function (u, o) { if (o && o.method === 'POST') sent.push([String(u), JSON.parse(o.body || '{}')]); return realFetch.apply(this, arguments); };
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const $ = (q) => document.querySelector(q), all = (q) => [].slice.call(document.querySelectorAll(q));
  const done = (x) => { const p = document.createElement('pre'); p.id = 'result'; p.textContent = JSON.stringify(x); document.body.appendChild(p); };
  (async () => {
    for (let i = 0; i < 120 && !document.querySelector('main[data-drawn]'); i++) await wait(50);
    try {
%s
    } catch (e) { done({error: String(e && e.stack || e)}); }
  })();
})();
"""

    def setUp(self):
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = True
        demo.STATE.clear()
        demo.FACTS.clear()
        self.addCleanup(demo.STATE.clear)
        self.addCleanup(demo.FACTS.clear)
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        port = self.httpd.server_address[1]
        env = mock.patch.dict(os.environ, {'SWITCHBOARD_PORT': str(port)})
        env.start()
        self.addCleanup(env.stop)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = 'http://127.0.0.1:%d/' % port

    def dom(self, path='', budget=8000):
        """The page as Chrome built it after `budget` milliseconds of its own (virtual) time."""
        profile = tempfile.mkdtemp(prefix='switchboard-chrome-')
        self.addCleanup(shutil.rmtree, profile, True)
        out = os.path.join(profile, 'dom.html')
        with open(out, 'wb') as f:
            # Output to a file, not a pipe (Chrome's helpers would hold a pipe open), in its own process group so a
            # stuck run can be ended whole; a mock keychain, because $HOME is a temp folder here.
            p = subprocess.Popen([CHROME, '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
                                  '--disable-extensions', '--use-mock-keychain', '--password-store=basic',
                                  '--user-data-dir=' + profile, '--virtual-time-budget=%d' % budget, '--dump-dom',
                                  self.url + path],
                                 stdout=f, stderr=subprocess.DEVNULL, start_new_session=True)
            try:
                p.wait(timeout=90)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait()
                self.fail('headless Chrome did not finish the page in 90 s')
        with open(out, encoding='utf-8', errors='replace') as f:
            dom = f.read()
        if '<main' not in dom:
            self.fail('headless Chrome drew no page')
        return dom

    def run_page(self, body, path='', budget=8000):
        """Run `body` (JavaScript; it ends by calling done(value)) inside the real page and return the value."""
        js = (self.PRELUDE % body).encode()

        def page(handler, name):
            with open(os.path.join(core.WEB, name), 'rb') as f:
                data = f.read()
            if name == 'index.html':
                data = data.replace(b'</body>', b'<script>' + js + b'</script></body>')
            handler._send(200, data, 'text/html; charset=utf-8')
        with mock.patch.object(server.Handler, '_page', page):
            dom = self.dom(path, budget)
        m = re.search(r'<pre id="result">(.*?)</pre>', dom, re.S)
        if not m:
            self.fail('the page script did not finish')
        import html as htmllib
        value = json.loads(htmllib.unescape(m.group(1)))
        if isinstance(value, dict) and value.get('error'):
            self.fail(value['error'])
        return value

    def drawn(self, path=''):
        """(the accounts' emails in the order drawn, the email of the row marked Next) from the page as Chrome built
        it (the accounts table's rows)."""
        dom = self.dom(path)
        dom = dom[dom.find('<main'):dom.find('</main>')]          # the drawn page, not the script's own text
        blocks = re.split(r'<div class="ag arow[ "]', dom)[1:]
        emails = [re.search(r'<span class="em" title="[^"]*">([^<]*)</span>', b).group(1) for b in blocks]
        nxt = [e for e, b in zip(emails, blocks) if 'class="badge next"' in b]
        self.assertLessEqual(len(nxt), 1)
        return emails, nxt[0] if nxt else None

    def expected(self):
        """(the server's accounts in slot order, the server's Next)."""
        with urllib.request.urlopen(self.url + 'api/state', timeout=10) as r:
            s = json.load(r)
        return [a['email'] for a in sorted(s['accounts'], key=lambda a: a['slot'])], s['next']


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class PageAgreesWithTheServer(ChromePage):
    """The page lists the accounts in slot order, the numbers the keys and the terminal use, whatever the plan's
    order, and marks the server's Next in place."""

    def test_order_and_next_match(self):
        want = self.expected()
        v = demo.view()
        self.assertNotEqual([o['email'] for o in v['plan']['order']], want[0])   # the plan's order differs here
        self.assertEqual(want[1], v['plan']['next'])                               # Next is the plan's, while on
        self.assertIsNotNone(want[1])
        self.assertEqual(self.drawn(), want)

    def test_after_a_switch_they_still_match(self):
        req = urllib.request.Request(self.url + 'api/switch', data=json.dumps({'email': 'maya@fernhill.example'}).encode(),
                                     headers={'Content-Type': 'application/json', 'X-Switchboard': '1'})
        urllib.request.urlopen(req, timeout=10).close()
        want = self.expected()
        names, nxt = self.drawn()
        self.assertEqual((names, nxt), want)


# ---------- the status line's small file ----------

class StatusFile(Sandbox):
    def line(self):
        out = io.StringIO()
        statusline.main(stdin=io.BytesIO(b'{}'), stdout=out, env={'NO_COLOR': '1'})
        return out.getvalue().strip()

    def test_each_tick_writes_it_and_the_line_reads_only_it(self):
        self.three()
        server.tick()
        path = os.path.join(self.data, 'statusline.json')
        with open(path) as f:
            small = json.load(f)
        self.assertEqual((small['active'], small['next']), (A, B))
        self.assertNotIn('accounts', small)                     # names are the emails themselves, so no aliases ride along
        self.assertLess(os.path.getsize(path), 400)
        os.unlink(os.path.join(self.data, 'state.json'))       # the line never needs the big file
        self.assertEqual(self.line(), '● %s · next: %s' % (A, B))

    def test_rewritten_only_when_it_changes_and_never_half_written(self):
        self.three()
        server.tick()
        path = os.path.join(self.data, 'statusline.json')
        first = os.stat(path).st_mtime_ns
        server.tick()
        self.assertEqual(os.stat(path).st_mtime_ns, first)
        self.rows[0]['active'], self.rows[1]['active'] = False, True
        server.tick()
        with open(path) as f:
            self.assertEqual(json.load(f)['active'], B)
        self.assertEqual([n for n in os.listdir(self.data) if n.endswith('.tmp')], [])

    def test_the_server_never_writes_where_switchboard_state_points(self):
        other = os.path.join(self.tmp, 'state-somewhere.json')
        with open(other, 'w') as f:
            f.write('{"keep": "me"}')
        os.environ['SWITCHBOARD_STATE'] = other
        self.three()
        server.tick()
        with open(other) as f:
            self.assertEqual(f.read(), '{"keep": "me"}')
        self.assertTrue(os.path.exists(os.path.join(self.data, 'statusline.json')))

    def test_rewritten_when_the_file_on_disk_differs(self):
        self.three()
        server.tick()
        path = os.path.join(self.data, 'statusline.json')
        with open(path, 'w') as f:
            f.write('{}')                                      # someone else wrote it
        server.tick()
        with open(path) as f:
            self.assertEqual(json.load(f)['active'], A)

    def test_one_failing_step_does_not_skip_the_others(self):
        self.three()
        server.tick()
        refreshed = []
        self.patch(artifacts, 'refresh', lambda: refreshed.append(1))
        self.patch(server, 'write_status_file', mock.Mock(side_effect=OSError('disk full')))
        self.rows[0]['active'], self.rows[1]['active'] = False, True
        server.tick()
        self.assertEqual(refreshed, [1])
        self.assertEqual(self.notes(), ['Switched to %s' % B])

    def test_a_switch_from_the_page_shows_at_once(self):
        self.three()
        server.tick()
        self.patch(engine, 'switch', lambda target, **kw: None)
        server.switch_to(B)
        self.assertEqual(self.line(), '● %s · next: %s' % (B, A))


# ---------- History's work, last 7 days ----------

class WhereItWent(Sandbox):
    def summary(self):
        return REAL(FIXTURE, FIXTURE_NOW - 7 * D, FIXTURE_NOW)

    def test_the_page_shape_from_made_up_history(self):
        page = usage.for_page(self.summary(), 7)
        self.assertEqual(set(page), {'days', 'cuts', 'moved'})
        self.assertEqual(set(page['cuts']), {'work', 'who', 'model', 'kind'})
        for name, rows in page['cuts'].items():
            with self.subTest(cut=name):
                self.assertEqual(sum(r['share'] for r in rows), 100)
                self.assertTrue(all(isinstance(r['share'], int) and r['share'] > 0 for r in rows))
                self.assertEqual([r['share'] for r in rows if not r['label'].endswith(' more')],
                                 sorted((r['share'] for r in rows if not r['label'].endswith(' more')), reverse=True))
                self.assertLessEqual(len(rows), usage.PAGE_ROWS)
                for r in rows:
                    self.assertLessEqual(len(r['note'].split()), 12, r['note'])
                    words = set(w.lower() for w in re.findall(r'[A-Za-z]+', r['note']))
                    self.assertLessEqual(words, NOTE_WORDS, r['note'])
        self.assertLessEqual({r['label'] for r in page['cuts']['who']}, {'Workflows', 'Helpers', 'You, in chat'})
        self.assertLessEqual({r['label'] for r in page['cuts']['model']}, set(usage.FAMILIES))
        self.assertLessEqual({r['label'] for r in page['cuts']['kind']}, set(usage.PAGE_KIND.values()))
        s = self.summary()
        self.assertEqual(page['moved'], {'share': usage.moved_share(s), 'to': 'Sonnet', 'from': ['Opus']})   # 30 at today's prices

    def test_whole_percents_add_to_100_and_never_flip(self):
        for weights in ([1, 1, 1], [33.4, 33.3, 33.3], [5, 0, 95], [0.2] * 9 + [98.2], [7], [2, 1]):
            got = usage.whole_percents(weights)
            self.assertEqual(sum(got), 100, weights)
            for i in range(len(weights)):
                for j in range(len(weights)):
                    if weights[i] > weights[j]:
                        self.assertGreaterEqual(got[i], got[j], weights)
        self.assertEqual(usage.whole_percents([0, 0]), [0, 0])

    def test_more_than_eight_rows_fold_into_n_more(self):
        rows = usage._rows([('chat %d' % i, 20 - i, lambda p: '') for i in range(12)], lambda n: '%d smaller chats.' % n)
        self.assertEqual(len(rows), 8)
        self.assertEqual((rows[-1]['label'], rows[-1]['note']), ('5 more', '5 smaller chats.'))
        self.assertEqual(sum(r['share'] for r in rows), 100)

    def test_nothing_in_the_window(self):
        page = usage.for_page(usage.summarize(FIXTURE, 0, 1), 7)
        self.assertEqual(page, {'days': 7, 'cuts': {}, 'moved': None})

    def test_a_request_never_waits_for_a_read(self):
        gate, seen, real = threading.Event(), [], self.summary()

        def slow(root, since, until, cache_path=None, tz=None, **k):
            seen.append((root, cache_path, until - since))
            gate.wait(10)
            return real
        self.patch(usage, 'summarize', slow)
        t = time.time()
        self.assertEqual(server.usage_view(), {'state': 'reading'})
        self.assertEqual(server.usage_view(), {'state': 'reading'})
        self.assertLess(time.time() - t, 0.5)
        gate.set()
        for _ in range(200):
            if server.USAGE['page'] is not None and not server.USAGE['running']:
                break
            time.sleep(0.02)
        got = server.usage_view()
        self.assertEqual(got['state'], 'ready')
        self.assertEqual(got['cuts'], usage.for_page(real, 7)['cuts'])
        # the week, then the week before it (the week under the accounts; test_insights_week.TheServer)
        self.assertEqual(seen, [(self.projects, os.path.join(self.data, 'usage-cache.json'), 7 * D)] * 2)

    def test_read_again_at_most_every_few_minutes_or_when_the_page_asks(self):
        calls = []                                  # one per read: the week's (the week before it is read with it)
        self.patch(usage, 'summarize', lambda *a, **k: ('series_since' in k and calls.append(1)) or self.summary())

        def settle():
            for _ in range(200):
                if not server.USAGE['running']:
                    return
                time.sleep(0.01)
        server.usage_view()
        settle()
        server.usage_view()
        server.usage_view(fresh=True)                         # asked, but the numbers are seconds old
        settle()
        self.assertEqual(len(calls), 1)
        server.USAGE['at'] -= server.USAGE_ASKED
        server.usage_view(fresh=True)
        settle()
        self.assertEqual(len(calls), 2)
        server.USAGE['at'] -= server.USAGE_ASKED
        server.usage_view()                                   # not asked, and under five minutes old
        settle()
        self.assertEqual(len(calls), 2)
        server.USAGE['at'] -= server.USAGE_EVERY
        server.usage_view()
        settle()
        self.assertEqual(len(calls), 3)

    def test_a_failed_read_says_not_measured(self):
        self.patch(usage, 'summarize', mock.Mock(side_effect=ValueError('broken')))
        server.read_usage()
        self.assertEqual(server.usage_view(), {'days': 7, 'cuts': {}, 'moved': None, 'failed': True, 'state': 'ready'})

    def test_the_demo_is_filled_and_adds_up(self):
        core.DEMO = True
        page = server.usage_view()
        self.assertEqual(page['state'], 'ready')
        for rows in page['cuts'].values():
            self.assertEqual(sum(r['share'] for r in rows), 100)
            for r in rows:
                self.assertLessEqual(set(w.lower() for w in re.findall(r'[A-Za-z]+', r['note'])), NOTE_WORDS, r['note'])


# ---------- keeping the Mac awake, and notifications ----------

class MacHelpers(Sandbox):
    def test_both_are_on_until_turned_off(self):
        self.assertEqual(server.helper_settings(), {'awake': True, 'notify': True})
        server.act('/api/awake', {'on': False})
        server.act('/api/notify', {'on': False})
        self.assertEqual(server.helper_settings(), {'awake': False, 'notify': False})
        self.assertEqual((core.settings()['awake'], core.settings()['notify']), (False, False))

    def test_holds_while_claude_code_works_and_lets_go_after(self):
        helper = self.fake_awake()
        path = self.transcript()
        server.mac_tick()
        self.assertTrue(helper.holding())
        self.assertEqual(FakeProc.started, [['/usr/bin/caffeinate', '-i', '-s', '-w', str(os.getpid())]])
        os.utime(path, (time.time() - 3600, time.time() - 3600))
        helper.transcripts.full_at = None                   # look at every file again, as the next full scan would
        server.mac_tick()
        self.assertFalse(helper.holding())

    def test_turning_it_off_lets_go_at_once_and_holds_nothing_more(self):
        helper = self.fake_awake()
        self.transcript()
        server.mac_tick()
        self.assertTrue(helper.holding())
        server.act('/api/awake', {'on': False})
        self.assertFalse(helper.holding())
        server.mac_tick()
        self.assertFalse(helper.holding())
        self.assertEqual(len(FakeProc.started), 1)

    def test_plug_in_is_said_once(self):
        self.fake_awake(power=LOW_BATTERY)
        self.transcript()
        server.mac_tick()
        server.mac_tick()
        self.assertEqual(self.notes(), ['Plug in your Mac'])

    def test_notifications_off_says_nothing(self):
        server.act('/api/notify', {'on': False})
        self.fake_awake(power=LOW_BATTERY)
        self.transcript()
        server.mac_tick()
        self.three()
        server.tick()
        self.rows[0]['active'], self.rows[1]['active'] = False, True
        server.tick()
        self.assertEqual(self.shown, [])

    def test_a_switch_the_engine_made_is_told_once(self):
        self.three()
        server.tick()
        self.rows[0]['active'], self.rows[1]['active'] = False, True
        server.tick()
        server.tick()
        self.assertEqual([n for n in self.notes() if n.startswith('Switched')], ['Switched to %s' % B])

    def test_a_switch_made_from_here_is_not_told(self):
        self.three()
        server.tick()
        self.patch(engine, 'switch', lambda target, **kw: None)
        server.switch_to(B)
        self.rows[0]['active'], self.rows[1]['active'] = False, True
        server.tick()
        self.assertEqual([n for n in self.notes() if n.startswith('Switched')], [])

    def test_near_the_line_and_the_refill(self):
        self.rows = [row(1, A, True, 40), row(2, B, False, 80)]
        server.tick()
        self.assertEqual(self.notes(), [])
        self.rows[0] = row(1, A, True, 91)                    # past the weekly line (95) less the landing margin (5)
        server.tick()
        server.tick()
        self.assertEqual(self.notes(), ['%s is near its limit' % A])
        self.rows[0] = row(1, A, True, 3, week_in=7 * D)      # its week refilled
        server.tick()
        self.assertEqual(self.notes(), ['%s is near its limit' % A, '%s refilled' % A])

    def test_an_account_first_seen_near_its_line_is_not_told(self):
        self.rows = [row(1, A, True, 40), row(2, B, False, 90)]
        server.tick()                                          # the first time Switchboard sees them
        self.rows.append(row(3, C, False, 93))
        server.tick()
        server.tick()
        self.assertEqual(self.notes(), [])

    def test_the_loop_runs_the_limits_and_the_awake_check(self):
        helper = self.fake_awake()
        self.transcript()
        self.three()
        server.loop(ticks=1)
        self.assertTrue(os.path.exists(os.path.join(self.data, 'statusline.json')))
        self.assertTrue(helper.holding())

    def test_the_demo_holds_nothing_and_says_nothing(self):
        core.DEMO = True
        self.fake_awake(power=LOW_BATTERY)
        self.transcript()
        self.assertIsNone(server.mac_tick())
        self.assertEqual((FakeProc.started, self.shown), ([], []))


# ---------- the status line switch ----------

class StatusLineSwitch(Sandbox):
    THEIRS = '{\n  "model": "x",\n  "statusLine": {"type": "command", "command": "~/mine.sh"},\n  "theme": "dark"\n}\n'

    def settings_file(self):
        return os.path.join(self.claude, 'settings.json')

    def read(self):
        with open(self.settings_file(), 'rb') as f:
            return f.read()

    def test_on_then_off_gives_back_the_same_bytes(self):
        with open(self.settings_file(), 'w') as f:
            f.write(self.THEIRS)
        before = self.read()
        server.act('/api/statusline', {'on': True})
        now = json.loads(self.read())
        self.assertTrue(statusline.is_ours(now['statusLine']))
        self.assertEqual((now['model'], now['theme']), ('x', 'dark'))
        self.assertTrue(server.statusline_status()['on'])
        server.act('/api/statusline', {'on': False})
        self.assertEqual(self.read(), before)
        self.assertEqual(server.statusline_status(), {'on': False, 'theirs': True})

    def test_no_file_before_means_no_file_after(self):
        server.act('/api/statusline', {'on': True})
        self.assertTrue(os.path.exists(self.settings_file()))
        server.act('/api/statusline', {'on': False})
        self.assertFalse(os.path.exists(self.settings_file()))

    def test_turning_it_on_leaves_bytecode_python_checks_against_the_file(self):
        folder = os.path.join(self.tmp, 'pkg')
        os.makedirs(folder)
        shutil.copy(statusline.__file__, folder)
        made = PRECOMPILE(script=os.path.join(folder, 'statusline.py'))
        self.assertEqual(made, os.path.join(folder, '__pycache__', 'statusline.%s.pyc' % sys.implementation.cache_tag))
        with open(made, 'rb') as f:
            head = f.read(16)
        st = os.stat(os.path.join(folder, 'statusline.py'))
        self.assertEqual(int.from_bytes(head[4:8], 'little'), 0)                               # checked by time and size,
        self.assertEqual(int.from_bytes(head[8:12], 'little'), int(st.st_mtime) & 0xFFFFFFFF)   # so a changed file is
        self.assertEqual(int.from_bytes(head[12:16], 'little'), st.st_size & 0xFFFFFFFF)        # compiled afresh
        self.assertIsNone(PRECOMPILE(script=os.path.join(self.tmp, 'nowhere', 'statusline.py')))

    def test_turning_it_on_writes_the_small_file_for_it(self):
        self.three()
        server.tick()
        os.unlink(os.path.join(self.data, 'statusline.json'))
        server.act('/api/statusline', {'on': True})
        self.assertTrue(os.path.exists(os.path.join(self.data, 'statusline.json')))

    def test_the_command_runs_the_file_itself_and_nothing_else_of_switchboard(self):
        cmd = server.statusline_command()
        self.assertTrue(statusline.is_ours({'type': 'command', 'command': cmd}))
        self.assertTrue(cmd.startswith(sys.executable + ' -I -S -c '), cmd)
        self.assertIn('D = "%s";' % os.path.dirname(os.path.abspath(statusline.__file__)), cmd)
        self.three()
        server.tick()
        payload = json.dumps({'session_id': 's1', 'rate_limits': {'seven_day': {'used_percentage': 49,
                                                                                'resets_at': time.time() + 3 * D}}})
        r = subprocess.run(['/bin/sh', '-c', cmd], input=payload.encode(), capture_output=True, timeout=60,
                           env=dict(os.environ, NO_COLOR='1', COLUMNS='200'))
        self.assertEqual((r.returncode, r.stderr), (0, b''))
        self.assertRegex(r.stdout.decode(), r'^● %s · 51%% left · refills \w{3} [^·]+ · next: %s\n$'
                         % (re.escape(A), re.escape(B)))


# ---------- the endpoints ----------

class Endpoints(Sandbox):
    def setUp(self):
        super().setUp()
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        os.environ['SWITCHBOARD_PORT'] = str(self.httpd.server_address[1])
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.patch(linkapp, 'status', lambda fresh=False: {'installed': False, 'on': False, 'browser': None})

    def ask(self, path, body=None):
        req = urllib.request.Request('http://127.0.0.1:%d%s' % (self.httpd.server_address[1], path),
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json', 'X-Switchboard': '1'})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.load(e)

    def test_get_usage_answers_at_once_then_with_the_cuts(self):
        self.patch(usage, 'summarize', lambda *a, **k: REAL(FIXTURE, FIXTURE_NOW - 7 * D, FIXTURE_NOW))
        code, body = self.ask('/api/usage')
        self.assertEqual((code, body), (200, {'state': 'reading'}))
        for _ in range(300):
            code, body = self.ask('/api/usage')
            if body.get('state') == 'ready':
                break
            time.sleep(0.02)
        self.assertEqual(body['state'], 'ready')
        self.assertEqual(set(body['cuts']), {'work', 'who', 'model', 'kind'})
        self.assertEqual(self.ask('/api/usage?fresh=1')[1]['state'], 'ready')

    def test_get_state_carries_the_plan_the_week_ahead_and_the_switches(self):
        self.three()
        server.tick()
        code, s = self.ask('/api/state')
        self.assertEqual(code, 200)
        self.assertEqual(s['plan']['next'], B)
        self.assertIn('forecast', s)
        # Notifications shows on only once Claude Code's "done" and "needs you" hooks are in; Switchboard's
        # own notes stay on until turned off (helper_settings, MacHelpers).
        self.assertEqual(s['mac'], {'awake': True, 'notify': False, 'statusline': False, 'statusline_theirs': False,
                                    'notify_hooks': False, 'hooks_off': None})

    def test_post_awake_notify_and_statusline(self):
        for path in ('/api/awake', '/api/notify', '/api/statusline'):
            with self.subTest(path=path):
                self.assertEqual(self.ask(path, {'on': False})[0], 200)
        self.assertEqual(self.ask('/api/state')[1]['mac'], {'awake': False, 'notify': False, 'statusline': False,
                                                            'statusline_theirs': False, 'notify_hooks': False,
                                                            'hooks_off': None})
        self.assertEqual(self.ask('/api/statusline', {'on': True})[1], {'changed': True, 'ok': True})
        self.assertTrue(self.ask('/api/state')[1]['mac']['statusline'])
        self.assertEqual(self.ask('/api/statusline', {'on': True})[1], {'changed': False, 'ok': True})
        self.assertEqual(self.ask('/api/statusline', {'on': False})[1], {'changed': True, 'ok': True})

    def test_a_settings_file_that_is_not_json_is_left_alone_and_says_why(self):
        with open(os.path.join(self.claude, 'settings.json'), 'w') as f:
            f.write('{"model": "x",}')
        code, body = self.ask('/api/statusline', {'on': True})
        self.assertEqual(code, 400)
        self.assertIn("isn't valid JSON", body['error'])
        with open(os.path.join(self.claude, 'settings.json')) as f:
            self.assertEqual(f.read(), '{"model": "x",}')

    def test_the_demo_answers_every_page_and_changes_nothing_on_disk(self):
        core.DEMO = True
        demo.STATE.clear()
        demo.FACTS.clear()
        self.addCleanup(demo.STATE.clear)
        self.addCleanup(demo.FACTS.clear)
        s = self.ask('/api/state')[1]
        self.assertEqual(len(s['accounts']), 5)
        self.assertTrue(s['plan']['next'])
        self.assertFalse(s['forecast']['lasts'])
        self.assertEqual(self.ask('/api/usage')[1]['state'], 'ready')
        for path in ('/api/awake', '/api/notify', '/api/statusline'):
            self.assertTrue(self.ask(path, {'on': path != '/api/awake'})[1]['demo'])
        self.assertEqual(self.ask('/api/state')[1]['mac'], {'awake': False, 'notify': True, 'statusline': True,
                                                            'statusline_theirs': True})
        self.assertFalse(os.path.exists(os.path.join(self.claude, 'settings.json')))
        self.assertFalse(os.path.exists(self.data))



# ---------- the plan follows the engine's own settings ----------

def five_shaped(strategy='refills-first', model=None, now=None):
    """Five made-up accounts: in use 9% used, refills in a day; 65% used with a
    Fable week 92% used, two days; 93%, three days; 17%, three days; unused, six days. Lines 90, 95 and 95."""
    now = time.time() if now is None else now
    st = core.blank_state()
    rows = (('atlas', 9, 1, None), ('birch', 65, 2, 92), ('cedar', 93, 3, None), ('delta', 17, 3, None),
            ('ember', 0, 6, None))
    for slot, (name, week, days, fable) in enumerate(rows, 1):
        email = '%s@made-up.example' % name
        a = core.ensure_account(st, email)
        a.update(connected=True, slot=slot, alias=name, org=name.title(), org_id='made-up-%d' % slot,
                 profile='Profile %d' % slot, profile_src='checked', usage_at=now - 30, models_seen=['Fable'],
                 usage={'session': {'pct': 0, 'resets': None, 'pace': None, 'lasts': None},
                        'weekly': {'pct': week, 'resets': iso(now + days * D), 'pace': None, 'lasts': None},
                        'scoped': [{'name': 'Fable', 'pct': fable, 'resets': iso(now + days * D)}] if fable else [],
                        'spend': None})
    st['active'] = 'atlas@made-up.example'
    st['tick_at'] = now - 20
    facts = {'profiles': dict(('Profile %d' % i, 'Profile %d' % i) for i in range(1, 6)),
             'browser': {'name': 'Google Chrome', 'present': True, 'tabs_blocked': False},
             'engine': {'installed': True, 'install': {'state': 'idle', 'error': None}, 'can_install': True, 'claude': True},
             'links': {'installed': True, 'on': True, 'browser': 'Switchboard', 'home': '', 'key': 'demo'},
             'auto': {'available': True, 'service': True, 'enabled': True, 'on': True, 'session_at': 90, 'weekly_at': 95,
                      'model_at': 95, 'weekly_set': True, 'strategy': strategy, 'model': model,
                      'models': [model] if model else []},
             'mac': {'awake': True, 'notify': True, 'statusline': False, 'statusline_theirs': False},
             'models': False, 'setup_hidden': True, 'demo': True, 'connecting': []}
    return st, facts


# (strategy, counted model) -> (the order, Next), by plan.py's rule (tests/test_plan.py works the same five out): the
# account in use first while under its lines; then refills first (the soonest weekly reset, then the most room) or the
# most room; birch's Fable week (92%) is within 5 points of its line only when Fable counts; cedar (93%) never has room.
FIVE_CASES = {
    ('refills-first', None): (['atlas', 'birch', 'delta', 'ember', 'cedar'], 'birch'),
    ('refills-first', 'Fable'): (['atlas', 'delta', 'ember', 'birch', 'cedar'], 'delta'),
    ('most-room', None): (['atlas', 'ember', 'delta', 'birch', 'cedar'], 'ember'),
    ('most-room', 'Fable'): (['atlas', 'ember', 'delta', 'birch', 'cedar'], 'ember'),
}


class EngineSettings(Sandbox):
    """What automatic switching runs on is the engine's (accounts.api.auto_settings); the page's pickers change it
    through engine.set_auto, with our two orders only."""

    def setUp(self):
        super().setUp()
        self.calls = []
        from switchboard.accounts import api
        self.patch(api, 'set_auto', lambda **kw: self.calls.append(dict((k, v) for k, v in kw.items() if v is not None))
                   or {})
        self.patch(engine.HOST, 'ensure', lambda: None)

    def test_the_pickers_call_the_engines_own_setting(self):
        server.act('/api/auto', {'strategy': 'most-room'})
        server.act('/api/auto', {'model': 'Fable'})
        server.act('/api/auto', {'model': ''})
        server.act('/api/auto', {'model_at': 92})
        self.assertEqual(self.calls, [{'strategy': 'most-room'}, {'model': 'Fable'}, {'model': ''}, {'model_at': 92.0}])
        for bad in ({'strategy': 'best'}, {'strategy': 'most-room-first'}, {'strategy': 'any-refills-first'},
                    {'model': 'rm -rf /'}, {'model': 5}, {'model_at': 101}):
            with self.subTest(body=bad), self.assertRaises(core.SwitchError):
                server.act('/api/auto', bad)
        self.assertEqual(len(self.calls), 4)                            # nothing else reached the engine

    def test_the_page_offers_our_three_orders(self):
        with open(os.path.join(HERE, '..', 'switchboard', 'web', 'index.html'), encoding='utf-8') as f:
            html = f.read()
        self.assertIn("const ORDERS = [['refills-first', 'Refills first'], ['most-room', 'Most room'], "
                      "['stay-then-refills', 'Stay, then refills first']];", html)

    def test_a_tick_reads_model_windows(self):
        self.rows = [row(1, A, True, 40),
                     row(2, B, False, 20, usage={'weekly': {'pct': 20, 'resets': iso(time.time() + D)}, 'session': None,
                                                 'scoped': [{'name': 'Fable', 'pct': 0, 'resets': None}], 'spend': None},
                         models_seen=['Fable'])]
        server.tick()
        st = core.load()
        self.assertEqual(st['accounts'][B]['models_seen'], ['Fable'])
        self.assertNotIn('threshold', st['accounts'][A])                # the engine's rows never set it
        self.assertEqual(server.render(st, dict(Plan.facts(self)))['auto']['models_seen'], ['Fable'])


class FiveAccounts(Sandbox):
    def test_server_plan_under_each_strategy_and_model(self):
        for (strategy, model), (want, nxt) in sorted(FIVE_CASES.items(), key=str):
            with self.subTest(strategy=strategy, model=model):
                st, facts = five_shaped(strategy, model)
                view = server.render(st, dict(facts, now=time.time()))
                self.assertEqual([a['name'] for a in view['accounts']], [a['email'] for a in view['accounts']])
                names = dict((e, a.get('alias')) for e, a in st['accounts'].items())     # the fixture's short labels
                self.assertEqual([names[o['email']] for o in view['plan']['order']], want)
                self.assertEqual(names[view['plan']['next']], nxt)

    def test_a_model_week_is_red_from_its_line(self):
        with open(os.path.join(HERE, '..', 'switchboard', 'web', 'index.html'), encoding='utf-8') as f:
            html = f.read()
        self.assertIn('const MODEL_LINE = 95;', html)
        self.assertIn('model: num(au.model_at) ? au.model_at : MODEL_LINE};', html)
        self.assertIn('l.over = !l.unknown && l.line != null && l.pct >= l.line;', html)
        self.assertIn('line: countsModel(m.name) ? L.model : null', html)      # the model automatic switching counts

    def test_the_demo_reads_soonest_refill_first(self):
        core.DEMO = True
        demo.STATE.clear()
        demo.FACTS.clear()
        self.addCleanup(demo.STATE.clear)
        self.addCleanup(demo.FACTS.clear)
        v = demo.view()
        self.assertEqual(v['auto']['strategy'], 'refills-first')
        names = dict((e, a.get('alias')) for e, a in demo.STATE['accounts'].items())     # the demo's own short labels
        # the account in use first while under its lines, then soonest refill first; personal's week (91%) has no room
        self.assertEqual([names[o['email']] for o in v['plan']['order']], ['work', 'ivy', 'fernhill', 'harbor', 'personal'])


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class FiveAccountsInThePage(ChromePage):
    """The real page, in headless Chrome, draws the five made-up accounts in slot order and marks the server's Next
    under every strategy, with and without a counted model."""

    def test_order_and_next_match(self):
        for (strategy, model), (want, nxt) in sorted(FIVE_CASES.items(), key=str):
            with self.subTest(strategy=strategy, model=model):
                st, facts = five_shaped(strategy, model)
                demo.STATE.clear()
                demo.STATE.update(st)
                demo.FACTS.clear()
                demo.FACTS.update(facts)
                demo.CLOCK['at'] = time.time()      # the fixture's times are its own: none carried from the last case
                slots = ['%s@made-up.example' % n for n in ('atlas', 'birch', 'cedar', 'delta', 'ember')]
                self.assertEqual(self.expected(), (slots, '%s@made-up.example' % nxt))
                self.assertEqual(self.drawn(), (slots, '%s@made-up.example' % nxt))   # slot order; the plan's Next


# ---------- models by the job ----------

class ModelsBase(Sandbox):
    """Only a temp CLAUDE_CONFIG_DIR, a made-up managed-settings folder and a stated Claude Code version."""
    ORIGINAL = '{\n  "model": "opus",\n  "env": {\n    "OTHER": "1"\n  },\n  "theme": "dark"\n}\n'

    def setUp(self):
        super().setUp()
        self.managed = os.path.join(self.tmp, 'managed')
        os.makedirs(self.managed)
        self.patch(server, 'MODELS_KW', {'environ': {}, 'managed_dir': self.managed,
                                         'versions': [{'where': '/x/claude', 'version': '2.1.309', 'primary': True}]})
        self.settings = os.path.join(self.claude, 'settings.json')
        with open(self.settings, 'w') as f:
            f.write(self.ORIGINAL)

    def read(self):
        with open(self.settings, 'rb') as f:
            return f.read()


class ModelsSetting(ModelsBase):
    def test_on_sets_one_variable_and_off_gives_the_same_bytes_back(self):
        before = self.read()
        self.assertEqual(server.models_view()['enabled'], False)
        self.assertEqual(server.act('/api/models', {'enabled': True}), {'changed': True, 'note': None})
        d = json.loads(self.read())
        self.assertEqual(d['env'], {'OTHER': '1', 'CLAUDE_CODE_SUBAGENT_MODEL': 'sonnet'})
        self.assertEqual((d['model'], d['theme']), ('opus', 'dark'))
        self.assertFalse(os.path.exists(os.path.join(self.claude, 'agents')))     # no Explore file, ever
        self.assertTrue(server.models_view()['enabled'])
        self.assertEqual(server.act('/api/models', {'enabled': False}), {'changed': True, 'note': None})
        self.assertEqual(self.read(), before)
        self.assertFalse(server.models_view()['enabled'])

    def test_off_after_the_file_changed_takes_out_only_its_key(self):
        server.act('/api/models', {'enabled': True})
        d = json.loads(self.read())
        d['permissions'] = {'allow': ['Bash(ls)']}                     # Claude Code or you changed the file since
        with open(self.settings, 'w') as f:
            json.dump(d, f, indent=2)
        server.act('/api/models', {'enabled': False})
        d = json.loads(self.read())
        self.assertNotIn('CLAUDE_CODE_SUBAGENT_MODEL', d['env'])
        self.assertEqual(d['permissions'], {'allow': ['Bash(ls)']})

    def test_open_chats_never_stop_it_and_get_one_true_line(self):
        self.transcript()                                               # a chat written seconds ago
        self.assertEqual(server.act('/api/models', {'enabled': True}), {'changed': True, 'note': 'next'})
        self.assertEqual(server.act('/api/models', {'enabled': False}), {'changed': True, 'note': 'restart'})

    def test_something_else_already_decides_it(self):
        with open(self.settings, 'w') as f:
            f.write('{"env": {"CLAUDE_CODE_SUBAGENT_MODEL": "haiku"}}')
        v = server.models_view()
        self.assertEqual((v['enabled'], v['decided_by']), (False, {'what': 'own', 'where': 'your user settings'}))
        with self.assertRaises(core.SwitchError):
            server.act('/api/models', {'enabled': True})
        with open(self.settings) as f:
            self.assertEqual(f.read(), '{"env": {"CLAUDE_CODE_SUBAGENT_MODEL": "haiku"}}')
        with open(os.path.join(self.managed, 'managed-settings.json'), 'w') as f:
            f.write('{"env": {"CLAUDE_CODE_SUBAGENT_MODEL_FORCE": "1"}}')
        self.assertEqual(server.models_view()['decided_by'], {'what': 'force', 'where': 'managed settings'})

    def test_an_old_claude_code_is_named_not_worked_around(self):
        self.patch(server, 'MODELS_KW', dict(server.MODELS_KW, versions=[{'where': '/x', 'version': '2.1.126',
                                                                           'primary': True}]))
        self.assertEqual(server.models_view()['blocked'], 'Needs Claude Code 2.1.251 or newer.')
        with self.assertRaises(core.SwitchError):
            server.act('/api/models', {'enabled': True})
        self.assertEqual(self.read(), self.ORIGINAL.encode())

    def test_last_week_from_history(self):
        self.patch(usage, 'summarize', lambda *a, **k: REAL(FIXTURE, FIXTURE_NOW - 7 * D, FIXTURE_NOW))
        server.read_usage()
        v = server.models_view()
        self.assertEqual((v['moved'], v['exact']), (30, True))           # 30.3% at today's prices: H1 and W2
        self.assertEqual([r['helper'] for r in v['log']][:2], ['Workflow steps', 'Helpers'])
        self.assertTrue(all(sum(m['share'] for m in r['models']) <= 100 for r in v['log']))

    def test_the_page_says_the_switch_and_nothing_more(self):
        with open(os.path.join(HERE, '..', 'switchboard', 'web', 'index.html'), encoding='utf-8') as f:
            html = f.read()
        self.assertIn("(why ? '<span class=\"muted\">' + esc(why) + '</span>' : '') + sw('models', !!(d && d.enabled), label, !d || (!!why && !d.enabled))", html)
        for gone in ('Look things up', 'Hardest problems', 'Build and fix', 'Read, review, research', 'SAMPLE'):
            self.assertNotIn(gone, html)
        self.assertIn("(!m.statusline && m.statusline_theirs ? '<span class=\"muted\">Replaces yours</span>' : '')", html)


class ModelsOverHttp(ModelsBase):
    """The first click in the app that edits Claude Code's own settings: only this Mac's own page may make it."""

    ask = Endpoints.ask

    def setUp(self):
        ModelsBase.setUp(self)
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        os.environ['SWITCHBOARD_PORT'] = str(self.httpd.server_address[1])
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def raw(self, body, **headers):
        req = urllib.request.Request('http://127.0.0.1:%d/api/models' % self.httpd.server_address[1],
                                     data=json.dumps(body).encode(), headers=dict({'Content-Type': 'application/json'}, **headers))
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status
        except urllib.error.HTTPError as e:
            with e:
                return e.code

    def test_without_the_pages_header_or_from_elsewhere_nothing_changes(self):
        before = self.read()
        self.assertEqual(self.raw({'enabled': True}), 403)                                   # no header
        self.assertEqual(self.raw({'enabled': True}, **{'X-Switchboard': '1', 'Origin': 'https://evil.example'}), 403)
        self.assertEqual(self.raw({'enabled': True}, **{'X-Switchboard': '1', 'Host': 'evil.example'}), 403)
        self.assertEqual(self.raw({'enabled': True}, **{'X-Switchboard': '1', 'Origin': 'null'}), 403)
        self.assertEqual(self.read(), before)
        self.assertEqual(self.raw({'enabled': True}, **{'X-Switchboard': '1'}), 200)        # the page itself
        self.assertNotEqual(self.read(), before)

    def test_get_and_post(self):
        code, v = self.ask('/api/models')
        self.assertEqual((code, v['enabled'], v['model']), (200, False, 'Sonnet'))
        self.assertEqual(self.ask('/api/models', {'enabled': 'yes'})[0], 400)
        self.assertEqual(self.ask('/api/models', {'enabled': True}), (200, {'changed': True, 'note': None, 'ok': True}))
        self.assertTrue(self.ask('/api/models')[1]['enabled'])


# ---------- limits, clocks, errors, names and backups ----------

class SwitchLineLimits(Sandbox):
    """A number outside 50 to 99.9, or not a finite number, is refused with a 400 and changes nothing."""

    def setUp(self):
        super().setUp()
        self.calls = []
        from switchboard.accounts import api
        self.patch(api, 'set_auto', lambda **kw: self.calls.append(dict((k, v) for k, v in kw.items() if v is not None))
                   or {})

    def test_refused_and_nothing_reaches_the_engine(self):
        for bad in (0, 5, 49.9, 100, 99.95, float('nan'), float('inf'), -float('inf'), '90', True, [90]):
            for key in ('session_at', 'weekly_at', 'model_at'):
                with self.subTest(key=key, value=bad), self.assertRaises(core.SwitchError):
                    server.act('/api/auto', {key: bad})
        self.assertEqual(self.calls, [])

    def test_the_engine_setting_is_never_clamped_or_truncated(self):
        for value in (50, 97.5, 99.9, 90):
            server.act('/api/auto', {'session_at': value})
            self.assertEqual(self.calls[-1], {'session_at': float(value)})
        with self.assertRaises(core.SwitchError):
            engine.set_auto(session_at=0)                    # the engine side refuses too, it never clamps to 50
        self.assertEqual(len(self.calls), 4)

    def test_over_http_a_nan_is_a_plain_400(self):
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        os.environ['SWITCHBOARD_PORT'] = str(httpd.server_address[1])
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        req = urllib.request.Request('http://127.0.0.1:%d/api/auto' % httpd.server_address[1],
                                     data=b'{"session_at": NaN}', headers={'Content-Type': 'application/json',
                                                                           'X-Switchboard': '1'})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=10)
        with cm.exception as e:
            self.assertEqual((e.code, json.load(e)), (400, {'error': 'Pick a number from 50 to 99.9.'}))
        self.assertEqual(self.calls, [])


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class NumberBoxesInThePage(ChromePage):
    def test_an_emptied_or_out_of_range_box_sends_nothing(self):
        got = self.run_page("""
      const shown = [];
      for (const v of ['', '5', '100', '99.95', 'abc', '95']) {
        const box = document.getElementById('line5');        // the header repaints after each change
        box.focus(); box.value = v; box.dispatchEvent(new Event('change', {bubbles: true})); await wait(500);
        shown.push(document.getElementById('line5').value);
      }
      done({sent: sent.filter((x) => x[0] === '/api/auto').map((x) => x[1]), shown});
""")
        self.assertEqual(got['sent'], [{'session_at': 95}])
        self.assertEqual(got['shown'], ['90', '90', '90', '90', '90', '95'])     # a refused number puts the old one back

    def test_escape_puts_the_old_number_back_and_enter_keeps_the_new_one(self):
        got = self.run_page("""
      let box = document.getElementById('lineW');
      box.focus(); box.value = '97';
      box.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})); await wait(300);
      const afterEscape = document.getElementById('lineW').value;
      box = document.getElementById('lineW');
      box.focus(); box.value = '97';
      box.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true})); await wait(600);
      done({afterEscape, sent: sent.filter((x) => x[0] === '/api/auto').map((x) => x[1]), now: document.getElementById('lineW').value});
""")
        self.assertEqual(got['afterEscape'], '95')
        self.assertEqual(got['sent'], [{'weekly_at': 97}])
        self.assertEqual(got['now'], '97')


class FutureTimes(Sandbox):
    """A file or a reading dated ahead of the clock (a restore, a synced folder, a clock set back) is never
    "just written": more than a few seconds ahead counts as not recent."""

    def test_a_transcript_dated_in_the_future_does_not_hold_the_mac_awake(self):
        helper = self.fake_awake()
        path = self.transcript()
        later = time.time() + 30 * D
        os.utime(path, (later, later))
        self.assertFalse(helper.working()[0])
        self.assertFalse(helper.working(time.time() + 20 * D)[0])
        server.mac_tick()
        self.assertEqual(FakeProc.started, [])
        soon = time.time() + 2                                   # a clock a little ahead is still "now"
        os.utime(path, (soon, soon))
        helper.transcripts.full_at = None
        self.assertTrue(helper.working()[0])

    def test_open_chats_are_not_counted_from_future_files(self):
        path = self.transcript()
        later = time.time() + 3600
        os.utime(path, (later, later))
        self.assertEqual(models.busy(self.claude), 0)

    def test_a_live_file_dated_in_the_future_is_pruned(self):
        live = os.path.join(self.data, 'live')
        os.makedirs(live)
        for name, dt in (('future.json', 3 * D), ('now.json', 0)):
            path = os.path.join(live, name)
            open(path, 'w').close()
            os.utime(path, (time.time() + dt, time.time() + dt))
        statusline.prune(live, every=0)
        self.assertEqual(sorted(os.listdir(live)), ['.pruned', 'now.json'])

    def test_a_note_remembered_in_the_future_does_not_silence_it(self):
        path = os.path.join(self.data, 'notify.json')
        os.makedirs(self.data, exist_ok=True)
        with open(path, 'w') as f:
            json.dump({'v': 1, 'seen': {'plug-in': {'at': time.time() + 5 * D, 'instance': None}}}, f)
        self.assertTrue(notify.send(notify.plug_in('Battery low.'), run=lambda cmd: 0, path=path))

    def test_show_ignores_a_live_file_dated_in_the_future(self):
        live = os.path.join(self.data, 'live')
        os.makedirs(live)
        for name, pct, dt in (('future.json', 99, D), ('real.json', 49, 0)):
            path = os.path.join(live, name)
            with open(path, 'w') as f:
                json.dump({'rate_limits': {'seven_day': {'used_percentage': pct, 'resets_at': time.time() + 3 * D}}}, f)
            os.utime(path, (time.time() + dt, time.time() + dt))
        self.assertEqual(cli._latest_live(live)['rate_limits']['seven_day']['used_percentage'], 49)


class UnreadableSettings(ModelsBase):
    """Every request gets a JSON answer, a settings file that can't be read is said plainly, and a switch
    is never shown off when the truth is unknown."""

    def setUp(self):
        super().setUp()
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        os.environ['SWITCHBOARD_PORT'] = str(self.httpd.server_address[1])
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.patch(linkapp, 'status', lambda fresh=False: {'installed': False, 'on': False, 'browser': None})

    ask = Endpoints.ask

    def lock(self):
        os.chmod(self.settings, 0)
        self.addCleanup(os.chmod, self.settings, 0o600)

    def test_models_says_it_cannot_read_instead_of_dropping_the_connection(self):
        self.lock()
        code, v = self.ask('/api/models')
        self.assertEqual((code, v['enabled'], v['unreadable']), (200, None, True))
        code, v = self.ask('/api/models', {'enabled': True})
        self.assertEqual((code, v), (400, {'error': "Claude Code's settings file can't be read."}))

    def test_the_status_line_switch_is_unknown_not_off(self):
        self.lock()
        self.assertEqual(server.statusline_status(), {'on': None, 'theirs': None, 'unreadable': True})
        mac = self.ask('/api/state')[1]['mac']
        self.assertEqual((mac['statusline'], mac['statusline_theirs']), (None, None))

    def test_any_failure_is_json_without_the_exception_text(self):
        self.patch(server, 'view', mock.Mock(side_effect=KeyError('/private/secret/path')))
        self.assertEqual(self.ask('/api/state'), (500, {'error': 'Something went wrong.'}))
        self.patch(server, 'act', mock.Mock(side_effect=OSError('/private/secret/path')))
        self.assertEqual(self.ask('/api/refresh', {}), (500, {'error': 'Something went wrong.'}))


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class UnknownSwitchesInThePage(ChromePage):
    def test_unknown_switches_say_so(self):
        demo.view()
        demo.FACTS['mac'].update(statusline=None, statusline_theirs=None)
        with mock.patch.object(demo, 'models', lambda: {'enabled': None, 'unreadable': True, 'decided_by': None,
                                                       'blocked': None, 'model': 'Sonnet', 'moved': 37, 'log': []}):
            got = self.run_page("""
      location.hash = '#settings'; await wait(1100);
      const rows = all('#sec-settings .row').map((r) => ({title: r.querySelector('.r-title').textContent, ctl: r.querySelector('.r-ctl').textContent,
        toggle: !!r.querySelector('.r-ctl .sw')}));
      done(rows);
""")
        rows = dict((r['title'], r) for r in got)
        self.assertEqual(rows['Status line'], {'title': 'Status line', 'ctl': 'Can\u2019t read settings', 'toggle': False})
        self.assertEqual(rows['Sonnet for helpers'], {'title': 'Sonnet for helpers', 'ctl': 'Can\u2019t read settings', 'toggle': False})
        self.assertTrue(rows['Notifications']['toggle'])                  # a known one keeps its switch


class ReadingAges(Sandbox):
    """A reading keeps the number it reports even after its reset time passes (that reset only ranks as
    unknown). A reading is shown up to an hour old (older is not known: a dash on the page, never a guessed number),
    and is a landing place only while it is fresh (15 minutes); one dated ahead of the clock is never trusted."""

    def view(self):
        return server.render(core.load(), dict(Plan.facts(self)))

    def test_a_past_reset_does_not_empty_the_account(self):
        now = time.time()
        self.rows = [row(1, A, True, 50), row(2, B, False, 97, week_in=-60)]
        server.tick()
        v = self.view()
        b = next(o for o in v['plan']['order'] if o['email'] == B)
        self.assertEqual((b['usable'], b['reason'], b['left']), (False, 'over its line', 0))
        self.assertEqual(next(a for a in v['accounts'] if a['email'] == B)['usage']['weekly']['pct'], 97)
        self.assertIsNone(v['plan']['next'])
        x = plan.order([dict(name='b', weekly_used_pct=97, weekly_resets_at=now - 60)], None, now)[0]
        self.assertEqual((x['reason'], x['left_pct']), ('over its line', 0))

    def test_what_is_too_old_or_from_the_future_is_unknown(self):
        cases = ((120, {}, True, True), (600, {'frozen': True}, True, True), (1800, {}, True, False),
                 (1800, {'usage_error': 'No internet'}, True, False), (7200, {}, False, False), (-3600, {}, False, False))
        for age, more, shown, planned in cases:
            with self.subTest(age=age, more=more):
                with core.locked() as st:
                    st['accounts'].pop(B, None)
                self.rows = [row(1, A, True, 50), row(2, B, False, 20, age=age, **more)]
                server.tick()
                v = self.view()
                self.assertEqual(next(a for a in v['accounts'] if a['email'] == B)['trusted'], shown)
                self.assertEqual(v['plan']['next'], B if planned else None)
                if not planned:
                    self.assertEqual(next(o for o in v['plan']['order'] if o['email'] == B)['reason'], 'no trusted reading')


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class ReadingAgesInThePage(ChromePage):
    def test_the_row_shows_the_plans_number_or_a_dash(self):
        demo.view()
        now = time.time()
        st = demo.STATE['accounts']
        # Reset passed, trusted. Ten minutes back, clear of the page's "now" minute: the demo keeps its times a fixed
        # distance from now, so a minute back would sit on that line instead of crossing it while the page loads.
        st['maya@fernhill.example']['usage']['weekly'].update(pct=97, resets=iso(now - 600))
        st['m.reyes@harbor.example']['usage_at'] = now - 7200                                   # too old to trust
        got = self.run_page("""
      await wait(300);
      const reset = (l) => (l.querySelector(':scope > .rf') || {}).textContent || null;
      done(all('#acc .arow').map((b) => [b.querySelector('.em').textContent, (b.querySelector('.note[data-kind="age"]') || {}).textContent || '',
        [].slice.call(b.querySelectorAll('.c[data-kind]')).map((l) => [l.querySelector('.pct').textContent, reset(l), l.className])]));
""")
        rows = dict((email, (meta, lims)) for email, meta, lims in got)
        studio = rows['maya@fernhill.example'][1]
        # the number the engine reports; its reset has passed, and no "Not started" is said: nothing under it
        self.assertEqual(studio[1][:2], ['97%', None])
        self.assertIn('over', studio[1][2])                           # and at or over the weekly line
        meta, lims = rows['m.reyes@harbor.example']
        # a weekly dash, no reset line (and no 5-hour line: it is drawn only when there is a 5-hour number to show)
        self.assertEqual([(l[0], l[1]) for l in lims], [('\u2014', None)])
        self.assertTrue(all('unknown' in l[2] for l in lims))
        self.assertEqual(meta, 'checked 2 hours ago')                 # and how old the reading is, under its own numbers


class WeekAhead(Sandbox):
    def test_the_account_in_use_from_its_own_reading(self):
        now = time.time()
        self.rows = [row(1, A, True, 70, week_in=3 * D), row(2, B, False, 10)]
        server.tick()
        f = server.forecast_view(core.load(), server.plan_settings(self.auto), now)
        self.assertEqual((f['email'], f['lasts']), (A, False))
        self.assertAlmostEqual(datetime.fromisoformat(f['out_at']).timestamp(), now + 30 / (70 / 96.0) * H, delta=120)

    def test_nothing_without_a_trusted_reading(self):
        self.rows = [row(1, A, True, 70, week_in=3 * D, age=9000)]
        server.tick()
        self.assertIsNone(server.forecast_view(core.load(), server.plan_settings(self.auto), time.time()))

    def test_a_weekly_log_in_the_state_is_dropped_at_the_tick(self):
        with core.locked() as st:
            st['weekly_log'] = {A: [[1, 2]]}                     # left by an earlier build
        self.three()
        server.tick()
        self.assertNotIn('weekly_log', core.load())

    def test_the_page_never_says_out_now(self):
        with open(os.path.join(HERE, '..', 'switchboard', 'web', 'index.html'), encoding='utf-8') as f:
            html = f.read()
        self.assertNotIn('Out now', html)


class NamesInNotifications(Sandbox):
    """A notification names an account by its whole email, as every other surface does: never "Account 2", and never
    its alias."""

    def test_an_account_without_an_alias(self):
        def bare(n, email, active, week):
            r = row(n, email, active, week)
            r.pop('alias')
            return r
        self.rows = [bare(1, 'ana.lee@mail.example', True, 40), bare(2, 'bo@harbor.example', False, 80)]
        server.tick()
        self.rows = [bare(1, 'ana.lee@mail.example', False, 40), bare(2, 'bo@harbor.example', True, 91)]
        server.tick()
        self.assertEqual(self.notes(), ['Switched to bo@harbor.example', 'bo@harbor.example is near its limit'])

    def test_an_alias_is_not_shown(self):
        self.three()
        server.tick()
        self.rows[0]['active'], self.rows[1]['active'] = False, True
        server.tick()
        self.assertEqual(self.notes(), ['Switched to %s' % B])                 # not "Switched to home"


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class StaleAndSlowInThePage(ChromePage):
    def test_an_old_picture_says_how_old_it_is(self):
        demo.view()
        demo.STATE.update(tick_at=time.time() - 300, ticking=False)    # nothing read for five minutes
        for a in demo.STATE['accounts'].values():                       # so every reading is as old (the
            a['usage_at'] = time.time() - 300                           # header says the account in use's own age)
        start = time.time()
        got = self.run_page("""
      await wait(300);
      done($('#top .checked').textContent);
""")
        # the age is the server's clock when the page asked, minus the reading's: on a busy Mac Chrome may ask a minute
        # late, so only the minutes that really passed are allowed (5 on a quiet Mac), rounded half up as the page does
        last = int((300 + time.time() - start) / 60 + 0.5)
        self.assertIn(got, ['checked %d min ago' % m for m in range(5, last + 1)])

    def test_a_fresh_picture_says_when_it_was_checked(self):
        # the account in use's own reading, not the server's tick (the demo's readings are 40 s old)
        demo.view()
        demo.STATE['tick_at'] = time.time() - 30
        self.assertEqual(self.run_page("""
      await wait(300);
      done($('#top .checked').textContent);
"""), 'checked just now')

    def test_one_state_request_at_a_time_and_none_hangs_for_ever(self):
        got = self.run_page("""
      await wait(300);
      let open = 0, most = 0, calls = 0, aborted = 0;
      const was = window.fetch;
      window.fetch = function (u, o) {
        if (String(u) !== '/api/state') return was.apply(this, arguments);
        calls++; open++; most = Math.max(most, open);
        return new Promise((res, rej) => {                     // a server that never answers
          const sig = o && o.signal;
          if (sig) sig.addEventListener('abort', () => { open--; aborted++; rej(new Error('timed out')); });
        });
      };
      document.dispatchEvent(new Event('visibilitychange'));
      await wait(21000);
      done({calls, most, aborted, lost: /busy|reach/.test($('#top .checked').textContent)});
""", budget=30000)
        self.assertEqual(got['most'], 1)                       # never two at once
        self.assertGreaterEqual(got['aborted'], 2)             # each gives up after 8 s ...
        self.assertTrue(got['lost'])                           # ... and the page says Switchboard is busy


class SettingsWriters(ModelsBase):
    """One backup per settings file, private (0600), however many changes. The last look at the file is
    right before it is replaced; a failed write leaves no record behind; a read-only settings file is left alone."""

    def backups(self):
        return sorted(n for n in os.listdir(self.claude) if 'switchboard' in n and n.startswith('settings.json'))

    def test_one_backup_for_many_changes(self):
        os.chmod(self.settings, 0o644)
        for on in (True, False, True, False):
            server.act('/api/statusline', {'on': on})
        for on in (True, False, True, False):
            server.act('/api/models', {'enabled': on})
            if on:                                                 # Claude Code wrote to the file since
                d = json.loads(self.read())
                d['theme'] = 'light' if d['theme'] == 'dark' else 'dark'
                with open(self.settings, 'w') as f:
                    json.dump(d, f, indent=2)
        self.assertEqual(self.backups(), ['settings.json.switchboard-backup'])
        self.assertEqual(os.stat(os.path.join(self.claude, 'settings.json.switchboard-backup')).st_mode & 0o777, 0o600)

    def test_a_write_by_claude_code_in_between_is_kept(self):
        plan_ = statusline.install_plan(self.settings, command_line=server.statusline_command())
        real = statusline._save_record

        def meanwhile(target, record):                             # Claude Code saves its settings just then
            real(target, record)
            with open(self.settings, 'a') as f:
                f.write(' ')
        self.patch(statusline, '_save_record', meanwhile)
        with self.assertRaises(statusline.PlanError):
            statusline.apply(plan_)
        self.assertEqual(self.read(), (self.ORIGINAL + ' ').encode())
        self.assertEqual(statusline._records(), {})

    def test_a_failed_write_leaves_no_record(self):
        plan_ = statusline.install_plan(self.settings, command_line=server.statusline_command())
        real = statusline._write_atomic

        def fail_on_settings(path, data, mode):
            if os.path.basename(path) == 'settings.json':
                raise OSError(28, 'No space left on device')
            return real(path, data, mode)
        self.patch(statusline, '_write_atomic', fail_on_settings)
        with self.assertRaises(statusline.PlanError) as e:          # one plain line, not a raw OSError
            statusline.apply(plan_)
        self.assertIn('No space left on device). Nothing was changed.', str(e.exception))
        self.assertEqual(statusline._records(), {})
        self.assertEqual(self.read(), self.ORIGINAL.encode())

    def test_a_read_only_settings_file_is_left_alone(self):
        os.chmod(self.settings, 0o444)
        self.addCleanup(os.chmod, self.settings, 0o600)
        with self.assertRaises(core.SwitchError) as cm:
            server.act('/api/statusline', {'on': True})
        self.assertIn('read-only', str(cm.exception))
        with self.assertRaises(core.SwitchError) as cm:
            server.act('/api/models', {'enabled': True})
        self.assertIn('read-only', str(cm.exception))
        self.assertEqual(self.read(), self.ORIGINAL.encode())


class OneNumber(ModelsBase):
    """Usage and Models show one number for helpers with no model set: the share that would move to Sonnet."""

    def test_usage_and_models_say_the_same_share(self):
        self.patch(usage, 'summarize', lambda *a, **k: REAL(FIXTURE, FIXTURE_NOW - 7 * D, FIXTURE_NOW))
        server.read_usage()
        u, m = server.usage_view(), server.models_view()
        # at today's prices (a cache read 0.05, the made-up tree's 1-hour writes 2): H1 and W2, 10313 of 34053.65
        self.assertEqual(u['moved'], {'share': 30, 'to': 'Sonnet', 'from': ['Opus']})
        self.assertEqual(m['moved'], 30)
        self.assertNotIn('unset', u)
        notes = ' '.join(r['note'] for rows in u['cuts'].values() for r in rows)
        self.assertNotIn('no model set', notes)

    def test_the_demo_too(self):
        core.DEMO = True
        demo.STATE.clear()
        demo.FACTS.clear()
        self.addCleanup(demo.STATE.clear)
        self.addCleanup(demo.FACTS.clear)
        self.assertEqual(server.usage_view()['moved']['share'], server.models_view()['moved'])


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class WhereItWentInThePage(ChromePage):
    """The week's work is under the accounts (your week), not in Activity: Projects with the top five, each a
    thin bar split by who, the rest as one "N more" row; who did it in the chart's legend; Models as their own small
    multiple; all from the server's /api/usage. Sonnet for helpers is the server's switch."""

    def test_the_top_five_the_rest_and_the_two_lines(self):
        got = self.run_page("""
      await wait(1500);
      const w = $('#wk-mults');
      const rows = (id) => all('#' + id + ' li').map((x) => x.querySelector('.pl').textContent);
      done({head: $('#p-work').textContent, top: all('#wk-mults section:first-child li:not(.more)').map((x) => x.querySelector('.pl').textContent),
            bars: all('#wk-mults section:first-child li:not(.more) .pbar').length, more: (w.querySelector('li.more .pl') || {}).textContent || null,
            who: $('#wk-legend ul').textContent, model: all('#wk-mults .models li .pl').map((x) => x.textContent).join(' · '),
            notes: all('#wk-mults section:first-child li:not(.more)').map((x) => x.title), work: !!$('#work'), activity: $('#sec-activity').textContent});
""")
        work = demo.usage()['cuts']['work']
        self.assertEqual(got['head'], 'Projects')                          # under the accounts, in your week
        self.assertFalse(got['work'])
        self.assertNotIn('last 7 days', got['activity'])                   # not in Activity
        self.assertEqual(got['top'], ['%s%d%%' % (x['label'], x['share']) for x in work[:5]])
        self.assertEqual(got['bars'], 5)
        self.assertEqual(got['more'], '20 more6%')                  # Billing, Security review and "18 more": 20, 2 + 2 + 2
        self.assertEqual(got['who'], 'Workflows 81%Helpers 10%You, in chat 9%')
        self.assertEqual(got['model'], 'Opus72% · Sonnet20% · Fable8%')
        self.assertEqual(got['notes'], [x['note'] for x in work[:5]])

    def test_sonnet_for_helpers_is_the_servers_switch(self):
        got = self.run_page("""
      await wait(800);
      location.hash = '#settings'; await wait(1100);
      const sw = $('#row-models .sw'), before = sw.getAttribute('aria-checked');
      sw.click(); await wait(800);
      done({label: $('#row-models .r-title').textContent, before, sent: sent.filter((x) => x[0] === '/api/models').map((x) => x[1]),
            after: $('#row-models .sw').getAttribute('aria-checked')});
""")
        self.assertEqual((got['label'], got['before']), ('Sonnet for helpers', 'false'))
        self.assertEqual(got['sent'], [{'enabled': True}])
        self.assertEqual(got['after'], 'true')


def local_clock(t, now):
    """The page's clock words for an epoch time, in this Mac's time zone: "6:10 PM" today, "Sat 12:00 PM" otherwise."""
    lt, ln = time.localtime(t), time.localtime(now)
    words = '%d:%02d %s' % ((lt.tm_hour % 12) or 12, lt.tm_min, 'AM' if lt.tm_hour < 12 else 'PM')
    return words if lt[:3] == ln[:3] else time.strftime('%a ', lt) + words


def local_until(t, now):
    """The page's "in ..." words: the nearest whole unit."""
    m = (t - now) / 60.0
    if round(m) < 60:
        return 'in %d min' % max(1, round(m))
    h = round(m / 60)
    if h < 24:
        return 'in %d hour%s' % (h, '' if h == 1 else 's')
    d = round(m / 1440)
    return 'in %d day%s' % (d, '' if d == 1 else 's')


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class TheFinalPage(ChromePage):
    """The accounts table, on the demo's made-up accounts: each
    account a row of the table (its email alone, its plan without what the email says, a 5-hour and a weekly cell
    with its % and a bar marked at its switch line, when it refills on one line, In use / Use / Next; an old
    reading's age under its numbers), slot order but never a slot number, the Use button or Cmd-K and a short name
    switches (no number keys), Next only while auto-switch is on, red only at a switch line, and a Fable week's own
    reset on hover only when it differs."""

    ALIASES = ('work', 'personal', 'fernhill', 'harbor', 'ivy')

    def hold_the_demos_times(self):
        """At each view the demo moves its times forward by the time since the last, so a reset a test sets reached the
        page as many seconds later as the page took to load, and on a busy Mac a minute later ("Sat 12:01 AM" for
        12:00). The tests that set a reset to the second hold the demo's times still; its readings stay
        trusted for 15 minutes."""
        p = mock.patch.object(demo, '_keep_fresh', lambda now: demo.CLOCK.update(at=now))
        p.start()
        self.addCleanup(p.stop)

    def blocks(self, path=''):
        return self.run_page("""
      await wait(300);
      const reset = (l) => (l.querySelector(':scope > .rf') || {}).textContent || null;
      done(all('#acc .arow').map((b) => ({email: b.querySelector('.em').textContent, who: b.querySelector('.c-who .who').textContent,
        slot: b.dataset.slot, state: b.querySelector('.c-state').textContent,
        plan: ((s) => { s.querySelectorAll('.chn').forEach((x) => x.remove()); return s.textContent.replace(/ · $/, ''); })(b.querySelector('.sub').cloneNode(true)),
        age: (b.querySelector('.note[data-kind="age"]') || {}).textContent || '', on: b.classList.contains('is-on'),
        resets: ((r) => (r ? [r.textContent.replace(/^Refills /, ''), r.getAttribute('title')] : [null, null]))(b.querySelector('.c-week > .rf')),
        weekTip: b.querySelector('.c-week').title,
        lims: [].slice.call(b.querySelectorAll('.c[data-kind], .note[data-kind="model"]')).map((l) => l.classList.contains('note')
          ? {kind: 'model', over: l.classList.contains('over'), label: l.textContent.split(' ')[0], pct: l.textContent.split(' ')[1],
             reset: (l.title.split(' · refills ')[1]) || null}
          : {kind: l.dataset.kind, over: l.classList.contains('over'), label: l.querySelector('.ml').textContent,
             pct: l.querySelector('.pct').textContent, bar: l.querySelector('.bar > i').getAttribute('style'),
             zero: l.querySelector('.bar > i').classList.contains('zero'), tick: l.querySelector('.bar > b').getAttribute('style'),
             reset: reset(l)})})));
""", path)

    def test_each_limit_is_stacked(self):
        # each limit a cell of the table, its % then its bar, the bar's mark at its switch line
        # on its limit's own line; work's week is ahead of its pace: when it would be full, in the refill's place
        reset = re.compile(r'^(Refills|Full by) (\w{3} )?\d{1,2}(:\d{2})? [AP]M$')
        lines = {'five': 90, 'week': 95}
        for b in self.blocks():
            for l in b['lims']:
                if l['kind'] == 'model' or l['pct'] == '\u2014':     # personal's reading is too old to show: a dash
                    continue
                with self.subTest(email=b['email'], limit=l['kind']):
                    self.assertEqual(l['label'], {'five': '5-hour', 'week': 'Weekly'}[l['kind']])
                    self.assertRegex(l['pct'], r'^\d{1,3}%$')
                    self.assertEqual(l['bar'], 'width:%s%%' % l['pct'][:-1])     # the bar fills as used
                    self.assertEqual(l['zero'], l['pct'] == '0%')
                    self.assertEqual(l['tick'], 'left:%d%%' % lines[l['kind']])  # the thin mark at its switch line
                    if l['reset'] is not None:
                        self.assertRegex(l['reset'], reset)
        ivy = next(b for b in self.blocks() if b['email'] == 'maya@ivy.example')
        self.assertEqual([l['kind'] for l in ivy['lims']], ['five', 'week'])      # its Fable week: no line of its own

    def test_an_account_is_named_by_its_email(self):
        # a row is named by its email
        v = demo.view()
        got = self.run_page("""
      await wait(300);
      done({h1: $('h1').textContent, labels: all('#acc [aria-label^="Use "], #acc [aria-label^="Sign in"]').map((x) => x.getAttribute('aria-label'))});
""")
        self.assertEqual(got['h1'], 'Claude Code is on maya@northwind.example')
        # a row that needs a sign-in shows one button beside Use (harbor's profile is signed out of claude.ai, and
        # fernhill's sign-in ends in 3 days); one that needs a fresh sign-in has that alone (personal's ended)
        want = ['Use ' + a['email'] for a in v['accounts'] if not a['active'] and not a['needs_login']]
        want += ['Sign in to claude.ai as ' + a['email'] for a in v['accounts'] if not a['active'] and a.get('signed_out')]
        want += ['Sign in again as maya.reyes@mail.example', 'Sign in again as maya@fernhill.example']
        self.assertEqual(sorted(got['labels']), sorted(want))
        self.assertIn('Sign in to claude.ai as m.reyes@harbor.example', got['labels'])
        rows = self.blocks()
        self.assertEqual([b['who'] for b in rows], [b['email'] for b in rows])     # the email alone, no short name
        self.assertEqual([b['plan'] for b in rows], ['Team', 'Max 20x', 'Team', 'Enterprise', 'Max 5x'])   # without what the email says
        # fresh readings: the title line's age covers them; personal's, a day old (it needs a sign-in), says its own
        self.assertEqual(dict((b['email'], b['age']) for b in rows if b['age']), {'maya.reyes@mail.example': 'checked 1 day ago'})

    def test_slot_order_whatever_the_use(self):
        got = self.blocks()
        self.assertEqual([b['slot'] for b in got], ['1', '2', '3', '4', '5'])
        demo.STATE['accounts']['maya@ivy.example']['slot'] = 1            # the engine moved it to the top
        demo.STATE['accounts']['maya@northwind.example']['slot'] = 5
        moved = self.blocks()
        self.assertEqual([x['email'] for x in moved], ['maya@ivy.example', 'maya.reyes@mail.example', 'maya@fernhill.example',
                                                       'm.reyes@harbor.example', 'maya@northwind.example'])
        self.assertEqual([x['on'] for x in moved], [False, False, False, False, True])   # In use is marked in place

    def test_a_click_switches_and_in_use_moves_at_once(self):
        got = self.run_page("""
      const was = window.fetch;
      window.fetch = function (u, o) {                       // the server answers a switch only after 700 ms
        const r = was.apply(this, arguments);
        return String(u) === '/api/switch' ? new Promise((res) => setTimeout(() => res(r), 700)) : r;
      };
      const row = (e) => all('#acc .arow').find((b) => b.querySelector('.em').textContent === e);
      row('maya@fernhill.example').querySelector('[data-do="use"]').click();
      await wait(50);
      const at_once = {state: row('maya@fernhill.example').querySelector('.c-state').textContent, h1: $('h1').textContent,
                       old: row('maya@northwind.example').querySelector('.c-state').textContent,
                       order: all('#acc .em').map((x) => x.textContent)};
      await wait(1500);
      done({at_once, after: row('maya@fernhill.example').querySelector('.c-state').textContent,
            sent: sent.filter((x) => x[0] === '/api/switch').map((x) => x[1]),
            inUse: {use: !!row('maya@fernhill.example').querySelector('[data-do="use"]')}});
""")
        on = 'In use' + 'Sign in'           # fernhill's sign-in ends within the week: Sign in again under In use
        self.assertEqual(got['at_once']['state'], on)
        self.assertEqual(got['at_once']['h1'], 'Claude Code is on maya@fernhill.example')
        self.assertEqual(got['at_once']['old'], 'Use')
        self.assertEqual(got['at_once']['order'], ['maya@northwind.example', 'maya.reyes@mail.example', 'maya@fernhill.example',
                                                   'm.reyes@harbor.example', 'maya@ivy.example'])   # nothing reorders
        self.assertEqual(got['sent'], [{'email': 'maya@fernhill.example'}])
        self.assertEqual(got['after'], on)
        self.assertEqual(got['inUse'], {'use': False})                      # the account in use has no Use
        self.assertEqual(demo.STATE['active'], 'maya@fernhill.example')

    def test_in_use_stays_moved_until_a_reading_shows_the_switch(self):
        got = self.run_page("""
      await wait(300);
      const was = window.fetch;
      const old = await (await was('/api/state')).text();
      let stale = old;                                       // every reading, for now, still says the old account
      window.fetch = function (u, o) {
        if (String(u) === '/api/state' && stale) return Promise.resolve(new Response(stale, {status: 200, headers: {'Content-Type': 'application/json'}}));
        return was.apply(this, arguments);
      };
      const row = (e) => all('#acc .arow').find((b) => b.querySelector('.em').textContent === e);
      const state = (e) => row(e).querySelector('.c-state').textContent;
      row('maya@fernhill.example').querySelector('[data-do="use"]').click();
      await wait(2500);
      const during = [state('maya@fernhill.example'), state('maya@northwind.example'), $('h1').textContent];
      stale = null; await wait(2500);
      done({during, after: [state('maya@fernhill.example'), state('maya@northwind.example')],
            sent: sent.filter((x) => x[0] === '/api/switch').length});
""")
        on = 'In use' + 'Sign in'           # fernhill's sign-in ends within the week: Sign in again under In use
        self.assertEqual(got['during'], [on, 'Use', 'Claude Code is on maya@fernhill.example'])   # no jump back
        self.assertEqual(got['after'], [on, 'Use'])
        self.assertEqual(got['sent'], 1)

    def test_keys_switch_and_are_left_alone_while_typing(self):
        # No number keys; Cmd-K, an account's short name (or part of its email) and Enter switch; keys typed in a
        # field are left alone
        got = self.run_page("""
      const key = (k, more, t) => (t || document).dispatchEvent(new KeyboardEvent('keydown', Object.assign({key: k, bubbles: true}, more || {})));
      const switches = () => sent.filter((x) => x[0] === '/api/switch').map((x) => x[1].email);
      await wait(300);
      key('3'); key('1'); key('9'); key('3', {altKey: true}); await wait(300);
      const numbers = switches().length;
      location.hash = '#activity'; await wait(400);
      $('#mq').focus(); key('g', {}, $('#mq')); key('a', {}, $('#mq')); key('?', {}, $('#mq')); await wait(300);
      const typing = [location.hash, $('#kbd').hidden];
      $('#mq').blur();
      key('k', {metaKey: true}); await wait(200);
      const pal = !$('#pal').hidden, q = $('#pal-q');
      q.value = 'fernhill'; q.dispatchEvent(new Event('input', {bubbles: true})); await wait(100);
      const first = ($('.pal-it[aria-selected="true"] .lb') || {}).textContent;
      key('Enter', {}, q); await wait(1500);
      key('k', {ctrlKey: true}); await wait(200);
      q.value = 'ivy'; q.dispatchEvent(new Event('input', {bubbles: true})); await wait(100);
      key('Enter', {}, q); await wait(1500);
      done({numbers, typing, pal, first, all: switches(), h1: $('#h-live').textContent, hash: location.hash});
""", budget=12000)
        self.assertEqual(got['numbers'], 0)                                 # 1-9 move nothing
        self.assertEqual(got['typing'], ['#activity', True])                # G A and ? typed in a field do nothing
        self.assertTrue(got['pal'])
        self.assertEqual(got['first'], 'Use maya@fernhill.example')   # named by its email, found by its alias
        self.assertEqual(got['all'], ['maya@fernhill.example', 'maya@ivy.example'])
        self.assertEqual((got['h1'], got['hash']), ('Claude Code is on maya@ivy.example', '#accounts'))

    def test_next_only_while_auto_switch_is_on(self):
        got = self.run_page("""
      await wait(300);
      const nexts = () => all('#acc .next').map((n) => n.closest('.arow').querySelector('.em').textContent);
      const before = nexts();
      $('#auto .sw').click();
      const at_once = nexts(), off = $('#auto').classList.contains('off');
      await wait(1500);
      done({before, at_once, off, later: nexts(), sent: sent.filter((x) => x[0] === '/api/auto').map((x) => x[1])});
""")
        self.assertEqual(got['before'], ['maya@ivy.example'])
        self.assertEqual((got['at_once'], got['off'], got['later']), ([], True, []))
        self.assertEqual(got['sent'], [{'enabled': False}])
        self.assertIsNone(demo.view()['next'])

    def test_red_only_at_or_over_a_switch_line(self):
        demo.view()
        u = lambda e: demo.STATE['accounts'][e]['usage']       # noqa: E731
        u('maya@northwind.example')['session']['pct'], u('maya@northwind.example')['weekly']['pct'] = 90, 94
        u('maya.reyes@mail.example')['weekly']['pct'] = 95                  # Ultra: no 5-hour limit
        demo.STATE['accounts']['maya.reyes@mail.example']['usage_at'] = time.time() - 40     # read just now, to show
        u('maya@ivy.example')['scoped'][0]['pct'] = 95                    # at the model line (95)
        u('maya@fernhill.example')['scoped'] = [{'name': 'Fable', 'pct': 94, 'resets': u('maya@fernhill.example')['weekly']['resets']}]
        got = self.run_page("""
      await wait(300);
      const red = () => all('#acc .over[data-kind]').map((l) => l.closest('.arow').querySelector('.em').textContent + ' ' + l.dataset.kind);
      const notes = () => all('#acc .mnote').map((n) => n.closest('.arow').querySelector('.em').textContent + ' ' + n.textContent);
      const before = red(), noted = notes();
      const box = $('#lineW'); box.focus(); box.value = '96';
      box.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
      const at_once = red();
      done({before, noted, at_once, label: getComputedStyle($('#acc .c.over .ml')).color, ink: getComputedStyle($('#acc .em')).color,
            pct: getComputedStyle($('#acc .c.over .pct')).color, fill: getComputedStyle($('#acc .c.over .bar > i')).backgroundColor,
            model: getComputedStyle($('#acc .mnote')).color,
            onFill: getComputedStyle(all('#acc .arow.is-on .bar.over > i')[0]).backgroundColor});
""")
        self.assertEqual(got['before'], ['maya@northwind.example five', 'maya.reyes@mail.example week'])
        self.assertEqual(got['at_once'], ['maya@northwind.example five'])   # follows the new line at once
        # a model's week has no line: at or over its line (harbor's 97, ivy's 95) it is the weekly line's red note;
        # fernhill's 94 is under it, and says nothing
        self.assertEqual(got['noted'], ['m.reyes@harbor.example Fable 97%', 'maya@ivy.example Fable 95%'])
        self.assertNotEqual(got['label'], got['pct'])                  # the label is never red
        self.assertEqual((got['pct'], got['fill'], got['model']), ('rgb(220, 38, 38)',) * 3)
        self.assertEqual(got['onFill'], 'rgb(220, 38, 38)')            # red beats blue, in use or not

    def test_a_fable_week_says_its_reset_only_when_it_differs(self):
        # (it was the Fable line's own hover; a model's week has no line now, and the weekly line's hover says it)
        self.hold_the_demos_times()
        demo.view()
        ivy = demo.STATE['accounts']['maya@ivy.example']['usage']
        tip = next(b for b in self.blocks() if b['email'] == 'maya@ivy.example')['weekTip'].split('\n')
        self.assertEqual(tip[1], 'Fable’s own week: 80% used, switches at 95%')  # the same moment as the weekly reset
        later = core.parse_iso(ivy['weekly']['resets']) + D
        ivy['scoped'][0]['resets'] = iso(later)
        tip = next(b for b in self.blocks() if b['email'] == 'maya@ivy.example')['weekTip'].split('\n')
        now = time.time()
        self.assertEqual(tip[1], 'Fable’s own week: 80% used, switches at 95%. Refills ' + local_clock(later, now))

    def test_a_reset_stamped_just_before_the_minute_reads_as_that_minute(self):
        # Anthropic stamps resets a moment early (21:59:59.65Z): the page says the minute it means, as the terminal does
        self.hold_the_demos_times()
        demo.view()
        target = (int(time.time() // 3600) + 30) * 3600            # an hour mark, a day or so ahead
        demo.STATE['accounts']['maya@ivy.example']['usage']['weekly']['resets'] = iso(target - 25)
        ivy = next(b for b in self.blocks() if b['email'] == 'maya@ivy.example')
        now = time.time()
        self.assertEqual(ivy['resets'][1], '%s, %s' % (local_clock(target, now), local_until(target, now)))   # Refills, on hover
        self.assertEqual(ivy['resets'][0], local_clock(target, now).replace(':00 ', ' '))                 # "Thu 2 PM"
        self.assertEqual(ivy['lims'][1]['reset'], 'Refills ' + ivy['resets'][0])


class PlainRefusals(ModelsBase):
    """A refusal from models.py reaches the page as one short line, never its long technical text or a path."""

    def test_short_lines(self):
        cases = (("Claude Code's settings file changed after this plan was made (by you or by Claude Code). Nothing "
                  "was written; make a new plan.", "Claude Code's settings changed just now. Nothing was written; try again."),
                 ("Switchboard's record changed since this plan was made. Make a new plan.",
                  "Claude Code's settings changed just now. Nothing was written; try again."),
                 ("Switchboard could not make this edit to /x/settings.json without changing something else; nothing "
                  "was written.", "Switchboard could not make that change. Nothing was written."),
                 ("The backup of /x/settings.json (/x/b) is missing or was changed. Nothing was changed.",
                  "Switchboard could not make that change. Nothing was written."))
        for raw, short in cases:
            with self.subTest(raw=raw):
                self.patch(models, 'apply', mock.Mock(side_effect=models.Refused(raw)))
                with self.assertRaises(core.SwitchError) as cm:
                    server.act('/api/models', {'enabled': True})
                self.assertEqual(str(cm.exception), short)

    def test_the_switch_names_the_model_the_server_sends(self):
        with open(os.path.join(HERE, '..', 'switchboard', 'web', 'index.html'), encoding='utf-8') as f:
            html = f.read()
        self.assertIn("label = ((d && d.model) || 'Sonnet') + ' for helpers'", html)
        self.assertIn("title: () => ((modelsData && modelsData.model) || 'Sonnet') + ' for helpers'", html)


# ---------- the command line ----------

class CommandLine(Sandbox):
    def run_cli(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(list(args))
        return code, out.getvalue()

    def test_usage_prints_what_the_page_shows(self):
        shutil.rmtree(self.projects)
        shutil.copytree(FIXTURE, self.projects)
        with mock.patch.object(cli.time, 'time', return_value=FIXTURE_NOW):
            code, out = self.run_cli('usage', '--json')
            page = json.loads(out)
            self.assertEqual(page, usage.for_page(REAL(self.projects, FIXTURE_NOW - 7 * D, FIXTURE_NOW), 7.0))
            code, text = self.run_cli('usage', '--days', '3')
        self.assertEqual(code, 0)
        self.assertTrue(text.startswith('Work, last 3 days (shares of weighted tokens)\n  '), text)   # the page's words
        self.assertNotIn('\nWork\n', text)                       # the projects are under that heading, as on the page
        self.assertTrue(os.path.exists(os.path.join(self.data, 'usage-cache.json')))
        with self.assertRaises(core.SwitchError):
            self.run_cli('usage', '--days', 'many')

    def test_awake_once_only_looks(self):
        self.patch(awake.Awake, 'power', lambda self: {'on_ac': True, 'battery_pct': 80, 'lid_closed': False})
        self.transcript()
        code, out = self.run_cli('awake', '--once')
        self.assertEqual(code, 0)
        self.assertIn('working:  yes', out)
        self.assertIn('holding:  no', out)
        code, out = self.run_cli('awake', '--once', '--json')
        self.assertTrue(json.loads(out)['working'])
        self.assertEqual(FakeProc.started, [])

    def test_statusline_on_off_and_show(self):
        path = os.path.join(self.claude, 'settings.json')
        with open(path, 'w') as f:
            f.write(StatusLineSwitch.THEIRS)
        self.assertIn('off, another one is set', self.run_cli('statusline', 'show')[1])
        code, out = self.run_cli('statusline', 'on')
        self.assertEqual(code, 1)
        self.assertIn('--replace', out)
        with open(path) as f:
            self.assertEqual(f.read(), StatusLineSwitch.THEIRS)
        code, out = self.run_cli('statusline', 'on', '--replace')
        self.assertEqual(code, 0)
        self.assertIn('saved at', out)
        self.assertIn('Status line in Claude Code: on', self.run_cli('statusline', 'show')[1])
        self.assertEqual(self.run_cli('statusline', 'off')[0], 0)
        with open(path) as f:
            self.assertEqual(f.read(), StatusLineSwitch.THEIRS)

    def test_doctor_knows_when_the_status_line_lost_its_python(self):
        self.assertIsNone(cli.statusline_works())
        server.act('/api/statusline', {'on': True})
        self.assertTrue(cli.statusline_works())
        path = os.path.join(self.claude, 'settings.json')
        with open(path) as f:
            d = json.load(f)
        d['statusLine']['command'] = d['statusLine']['command'].replace(sys.executable, os.path.join(self.tmp, 'gone', 'python3'))
        with open(path, 'w') as f:
            json.dump(d, f)
        self.assertFalse(cli.statusline_works())

    def test_version_help_and_an_unknown_command_answer(self):
        self.assertEqual(self.run_cli('version'), (0, 'switchboard %s\n' % core.version_text()))   # its build
        code, out = self.run_cli('help')
        for word in ('switchboard status', 'switchboard use NAME', 'switchboard usage', 'switchboard awake --once',
                     'switchboard statusline on|off|show', 'switchboard demo'):
            self.assertIn(word, out)
        self.assertEqual(self.run_cli('nonsense')[0], 2)


if __name__ == '__main__':
    unittest.main()
