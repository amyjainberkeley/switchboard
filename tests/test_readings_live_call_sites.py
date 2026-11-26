import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""The two places Claude Code's own numbers come in (a real tick() and the terminal's own look),
and the terminal's calls to the page going straight to it when a proxy is set."""
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import __main__ as cli
from switchboard import artifacts, core, engine, server, statusline
from fakes_accounts_api import install
import test_app

A, B = 'maya@northwind.example', 'maya@ivy.example'


class LiveNumbersAtBothCallSites(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-merge-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        home = os.path.join(self.tmp, 'home')
        os.makedirs(os.path.join(home, '.claude'))
        env = mock.patch.dict(os.environ, {'HOME': home, 'SWITCHBOARD_HOME': os.path.join(self.tmp, 'data'),
                                           'CLAUDE_CONFIG_DIR': os.path.join(home, '.claude')})
        env.start()
        self.addCleanup(env.stop)
        now = time.time()
        iso = test_app.iso
        self.wa = now + 3 * 86400
        # The engine's reading for A is held back by a rate limit (429): its last good numbers, 20 minutes old,
        # frozen. B is fresh.
        def week(pct, resets):
            return {'session': None, 'weekly': {'pct': pct, 'resets': iso(resets), 'pace': None, 'lasts': None},
                    'scoped': [], 'spend': None}
        self.rows = [engine._row(id='acct-1', number=1, email=A, active=True, usage=week(30, self.wa),
                                 usage_at=now - 1200, source='endpoint', frozen=True, usage_error='rate limited'),
                     engine._row(id='acct-2', number=2, email=B, usage=week(5, now + 4 * 86400), usage_at=now - 5,
                                 source='endpoint')]
        install(self)                                   # nothing reaches the engine itself (accounts.api)
        for target, name, value in ((engine, 'listing', lambda: {'rows': [dict(r) for r in self.rows], 'live': None,
                                                                 'disagree': False}),
                                    (engine, 'auto_settings', lambda: {'available': True, 'enabled': False,
                                                                       'session_at': 90, 'weekly_at': 95,
                                                                       'model_at': 95, 'models': []}),
                                    (engine, 'foreign_switcher', lambda folder=None: None),
                                    (statusline, 'precompile', lambda *a, **k: None),
                                    (server, 'NOTIFY_RUN', lambda cmd: 0), (artifacts, 'refresh', lambda: None)):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)

    def live(self, age, pct):
        rec = statusline.live_record({'session_id': 's1', 'rate_limits': {'seven_day': {'used_percentage': pct,
                                                                                         'resets_at': self.wa}}},
                                     {'active': A}, time.time() - age, env={})
        os.makedirs(statusline.live_dir(), exist_ok=True)
        with open(os.path.join(statusline.live_dir(), 's1.json'), 'w') as f:
            json.dump(rec, f)

    def week(self, view):
        a = next(x for x in view['accounts'] if x['email'] == A)
        return a['usage']['weekly']['pct'] if a['trusted'] and a['usage'] else None

    def test_the_tick_and_the_terminal_show_claude_codes_own_numbers(self):
        server.tick()                                  # first look: A becomes the account in use (timeline starts now)
        self.assertEqual(self.week(server.view()), 30)  # the engine's frozen reading, 20 min old (a 429): trusted
        with core.locked() as st:                      # the account came to A an hour ago
            st['timeline'] = [[A, time.time() - 3600]]
        self.live(age=45, pct=61)
        server.tick()
        self.assertEqual(self.week(server.view()), 61)          # the page and the menu bar (GET /api/state)
        self.assertEqual(self.week(cli.local_view()), 61)       # the terminal, with no page running
        shutil.rmtree(statusline.live_dir())
        server.tick()
        self.assertEqual(self.week(server.view()), 30)          # back to the engine's own once the record is gone


class _Page(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):              # a page of this test's data folder (core.folder_mark): a change may go to it
        body = json.dumps({'accounts': [], 'version': 'test', 'demo': False, 'folder': core.folder_mark()}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length') or 0))
        self.do_GET()


class NoProxyForThePage(unittest.TestCase):
    """With http_proxy set (common at work), `switchboard status`, `switchboard 2` and `switchboard auto on` still
    reach the page on this Mac: the proxy would take the call elsewhere and a running page would look stopped."""

    def setUp(self):
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), _Page)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        dead = socket.socket()                     # a proxy address nothing answers on
        dead.bind(('127.0.0.1', 0))
        proxy = 'http://127.0.0.1:%d' % dead.getsockname()[1]
        dead.close()
        env = mock.patch.dict(os.environ, {'SWITCHBOARD_PORT': str(self.httpd.server_address[1]),
                                           'http_proxy': proxy, 'HTTP_PROXY': proxy})
        env.start()
        self.addCleanup(env.stop)
        for k in ('no_proxy', 'NO_PROXY'):
            if k in os.environ:
                p = mock.patch.dict(os.environ)
                p.start()
                self.addCleanup(p.stop)
                del os.environ[k]

    def test_the_proxy_would_have_taken_it(self):
        # How it was: urllib's default opener (what urlopen builds once per process, reading http_proxy then) sends
        # 127.0.0.1 through the proxy when no_proxy does not list it.
        with mock.patch.object(cli, '_page_open',
                               lambda req, timeout: urllib.request.build_opener().open(req, timeout=timeout)):
            self.assertFalse(cli.page_running())

    def test_status_switch_and_auto_reach_the_page(self):
        self.assertTrue(cli.page_running())
        self.assertEqual(cli.page_state()['accounts'], [])
        self.assertEqual(cli.ask_page('/api/auto', {'enabled': True}, timeout=5)['accounts'], [])


if __name__ == '__main__':
    unittest.main()
