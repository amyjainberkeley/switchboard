import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# The page's takeaways over HTTP: GET /api/optimize gives the week's takeaways, POST /api/optimize makes or takes back
# one's change and gives the new list. Both are guarded as /api/models is: this Mac's own host, no other website's
# origin, and a POST only with the page's own header. The test port is the run's SWITCHBOARD_PORT.
import json
import os
import shutil
import sys
import threading
import time
import types
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))

from switchboard import core, optimize, server  # noqa: E402
from switchboard.router import settings as rsettings  # noqa: E402
import pagekey_help  # noqa: E402  (the page key, as Switchboard's own programs send it)

PAGE = {'tokens': {'input': 0, 'output': 20000, 'cache_write': 80000, 'cache_read': 10000000, 'cache_write_1h': 0},
        'cuts': {'who': [{'label': 'Workflows', 'share': 20}, {'label': 'Helpers', 'share': 50},
                         {'label': 'You, in chat', 'share': 30}]}}
TRYIT = 'switchboard.router.tryit'         # the router's own trial-run module; not in this tree, so stand-ins here


class Over(unittest.TestCase):
    def setUp(self):
        self.router = core.data_path('router')
        if os.path.isdir(self.router):
            shutil.rmtree(self.router)
        self.addCleanup(lambda: os.path.isdir(self.router) and shutil.rmtree(self.router))
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = False
        t = mock.patch.dict(sys.modules, {TRYIT: None})         # no trial-run module unless a test puts one
        t.start()
        self.addCleanup(t.stop)
        p = mock.patch.dict(server.USAGE, {'page': PAGE})
        p.start()
        self.addCleanup(p.stop)
        self.port = server.port()
        self.httpd = ThreadingHTTPServer(('127.0.0.1', self.port), server.Handler)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def ask(self, body=None, **headers):
        h = pagekey_help.with_key({'Content-Type': 'application/json'})    # every /api/ route but /api/state
        h.update(headers)
        req = urllib.request.Request('http://127.0.0.1:%d/api/optimize' % self.port,
                                     data=None if body is None else json.dumps(body).encode(), headers=h)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.load(e)

    def test_get_gives_the_weeks_takeaways(self):
        code, got = self.ask()
        self.assertEqual(code, 200)
        self.assertEqual(got, {'takeaways': optimize.takeaways(PAGE, optimize.router_state())})
        self.assertEqual([t['id'] for t in got['takeaways']], ['rereading', 'who'])
        self.assertFalse(os.path.exists(self.router))                           # a look writes nothing

    def test_get_before_the_week_is_read_is_empty(self):
        with mock.patch.dict(server.USAGE, {'page': None}):
            self.assertEqual(self.ask(), (200, {'takeaways': []}))

    def test_get_from_elsewhere_is_refused(self):
        for h in ({'Origin': 'https://evil.example'}, {'Host': 'evil.example'}, {'Sec-Fetch-Site': 'cross-site'}):
            with self.subTest(headers=h):
                self.assertEqual(self.ask(**h)[0], 403)

    def test_post_without_the_pages_header_or_from_elsewhere_changes_nothing(self):
        body = {'id': 'who', 'do': 'try'}
        self.assertEqual(self.ask(body)[0], 403)
        for h in ({'Origin': 'https://evil.example'}, {'Host': 'evil.example'}, {'Origin': 'null'}):
            with self.subTest(headers=h):
                self.assertEqual(self.ask(body, **dict(h, **{'X-Switchboard': '1'}))[0], 403)
        self.assertFalse(os.path.exists(self.router))

    def test_a_press_no_takeaway_on_screen_offers_changes_nothing(self):
        # on this week 'who' is advice only and no takeaway offers the helpers' cache
        for body in ({'id': 'who', 'do': 'try'}, {'id': 'storing', 'do': 'on'}):
            with self.subTest(body=body):
                self.assertEqual(self.ask(body, **{'X-Switchboard': '1'}), (400, {'error': optimize.NO_SUCH}))
        self.assertFalse(os.path.exists(self.router))

    def test_post_acts_and_gives_the_new_list(self):
        # with the router's trial-run module there, the press calls its on() and off() and nothing else
        m = types.ModuleType(TRYIT)
        m.calls, m.trying = [], False

        def on():
            m.calls.append('on')
            m.trying = True
            return ['Trial run on.', 'It notes new Claude Code chats only.']

        def off():
            m.calls.append('off')
            m.trying = False
            return ['Trial run off.']
        m.on, m.off = on, off
        m.state = lambda: {'trying': m.trying, 'was_on': False, 'was_trial': False}
        set_switch = mock.patch.object(rsettings, 'set_switch', lambda *a: self.fail('a router switch was set here'))
        with mock.patch.dict(sys.modules, {TRYIT: m}), set_switch:
            code, got = self.ask()
            self.assertEqual([t['label'] for t in [x['change'] for x in got['takeaways'] if x['change']]], ['Try it'])
            code, got = self.ask({'id': 'who', 'do': 'try'}, **{'X-Switchboard': '1'})
            self.assertEqual(code, 200)
            self.assertEqual((got['says'], got['more'], got['ok']),
                             ('Trial run on.', ['It notes new Claude Code chats only.'], True))
            self.assertEqual([t['state'] for t in got['takeaways'] if t['id'] == 'who'], ['trying'])
            code, got = self.ask({'id': 'who', 'do': 'off'}, **{'X-Switchboard': '1'})
            self.assertEqual(code, 200)
            self.assertEqual((got['says'], got['more']), ('Trial run off.', []))
            self.assertEqual([t['state'] for t in got['takeaways'] if t['id'] == 'who'], ['off'])
        self.assertEqual(m.calls, ['on', 'off'])
        self.assertFalse(os.path.exists(self.router))

    def test_a_refusal_is_a_400_with_its_sentence(self):
        self.assertEqual(self.ask({'id': 'who', 'do': 'on'}, **{'X-Switchboard': '1'}),
                         (400, {'error': optimize.NO_SUCH}))
        self.assertEqual(self.ask({'id': 'x'}, **{'X-Switchboard': '1'}), (400, {'error': optimize.NO_SUCH}))
        self.assertFalse(os.path.exists(self.router))

    def test_a_request_never_counts_and_the_loop_does_on_its_own_thread(self):
        calls = []
        m = types.ModuleType('switchboard.router.cache_count')
        m.projects_folders = lambda: ['/nowhere/projects']

        def count(folders, limit_s=None):
            calls.append((threading.current_thread().name, limit_s))
            return {'records_read': 900, 'requests': 120, 'restores': 25, 'restore_tokens': 400000,
                    'cache_writes': 60, 'by_family': {}}
        m.count = count
        m.say = lambda r: ['Helpers stored their whole context again %d times after a wait.' % r['restores']]
        unset = {'state': 'unset', 'value': None, 'set_by': None, 'new_enough': True, 'problem': None, 'notes': [],
                 'lines': []}
        self.addCleanup(server.CACHE_COUNT.update, dict(server.CACHE_COUNT))
        server.CACHE_COUNT.update(got=None, at=None, running=False)
        with mock.patch.dict(sys.modules, {'switchboard.router.cache_count': m}), \
                mock.patch.object(optimize.cache_setting, 'status', lambda **k: dict(unset)):
            self.assertEqual([t['id'] for t in self.ask()[1]['takeaways']], ['rereading', 'who'])
            self.assertEqual(self.ask({'id': 'storing', 'do': 'on'}, **{'X-Switchboard': '1'}),
                             (400, {'error': optimize.NO_SUCH}))
            self.assertEqual(calls, [])                                         # no request asked for the count
            self.assertTrue(server.cache_count_tick(now=1000.0))
            for _ in range(600):
                if not server.CACHE_COUNT['running']:
                    break
                time.sleep(0.1)
            self.assertEqual(len(calls), 1)
            self.assertNotEqual(calls[0][0], threading.main_thread().name)      # on a thread of its own
            self.assertEqual(calls[0][1], server.CACHE_COUNT_LIMIT)
            self.assertEqual(server.CACHE_COUNT_LIMIT, 2)
            self.assertFalse(server.cache_count_tick(now=1000.0 + 599))         # at most every ten minutes
            got = self.ask()[1]['takeaways']
            self.assertEqual([t['id'] for t in got], ['rereading', 'who', 'storing'])
            self.assertEqual(got[2]['measured'], {'value': 25, 'unit': 'times', 'basis': None})
            self.assertEqual(len(calls), 1)                                     # the GET only read the last count
            self.assertTrue(server.cache_count_tick(now=1000.0 + 600))
            for _ in range(600):
                if not server.CACHE_COUNT['running']:
                    break
                time.sleep(0.1)
            self.assertEqual(len(calls), 2)

    def test_the_demo_does_not_count(self):
        self.addCleanup(server.CACHE_COUNT.update, dict(server.CACHE_COUNT))
        server.CACHE_COUNT.update(got=None, at=None, running=False)
        core.DEMO = True
        self.assertFalse(server.cache_count_tick(now=1000.0))

    def test_the_demo_shows_none(self):
        core.DEMO = True
        self.assertEqual(server.optimize_view(), {'takeaways': []})


if __name__ == '__main__':
    unittest.main()
