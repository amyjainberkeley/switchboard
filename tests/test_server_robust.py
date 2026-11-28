import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The page's server under load and odd states: a slow world, a full disk, a clock set back, the old Link Router.
# A throwaway home and data folder; every outside program is a stand-in (a slow one for the load test); servers listen
# on free ports of 127.0.0.1 only; no notification is shown and no program is started.
import io
import json
import os
import shutil
import socket
import statistics
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import browser, core, engine, keep, linkapp, notify, server, statusline
import pagekey_help  # noqa: E402  (the page key, as Switchboard's own programs send it)

REAL_PAGES = (7798, 7799)
DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))
LOOKS = ('switchboard-links', 'switchboard-consent', 'switchboard-check')


def settle_looks(within=15.0):
    """Wait for the page's looks on threads of their own (the link app's status, macOS's consent check, the links check)
    that an earlier test started. One that ends inside the next test calls that test's SNAPSHOT.invalidate() and moves
    its LINKS and CONSENT: TheSlowWorld's two hundred clients then saw a second build 1.2 s after the first."""
    end = time.monotonic() + within
    for t in [t for t in threading.enumerate() if t.name in LOOKS]:
        t.join(max(0.0, end - time.monotonic()))


def loaded():
    """Load per core: past 2, threads at background priority (these tests run under taskpolicy -b) wait their turn for
    seconds whatever the code does (this Mac has run at load 213)."""
    return os.getloadavg()[0] / (os.cpu_count() or 1)


class Sandbox(unittest.TestCase):
    """A throwaway home, data folder and Claude Code folder; a fresh snapshot; no notification, no caffeinate."""

    def setUp(self):
        settle_looks()                  # nothing an earlier test started reaches into this one
        self.tmp = tempfile.mkdtemp(prefix='switchboard-server-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, 'home')
        self.data = os.path.join(self.tmp, 'data')
        self.claude = os.path.join(self.home, '.claude')
        os.makedirs(self.claude)
        env = mock.patch.dict(os.environ, {'HOME': self.home, 'SWITCHBOARD_HOME': self.data,
                                           'CLAUDE_CONFIG_DIR': self.claude})
        env.start()
        self.addCleanup(env.stop)
        for k in ('SWITCHBOARD_STATE', 'SWITCHBOARD_NOTIFY_STATE', 'SWITCHBOARD_ENGINE', 'SWITCHBOARD_PORT'):
            os.environ.pop(k, None)
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = False
        self.shown = []
        self.patch(server, 'NOTIFY_RUN', lambda cmd: self.shown.append(cmd) or 0)
        self.patch(server, 'SNAPSHOT', server.Snapshot())
        for target, values in ((server.HELPERS, {'awake': None}), (server.USAGE, {'page': None, 'at': 0, 'running': False}),
                               (server.LINKS, {'value': None, 'at': None, 'running': False, 'loop': False, 'fast_until': 0.0}),
                               (server.CONSENT, {'state': None, 'at': None, 'asking': False, 'running': False, 'wall': None}),
                               (server.DISK, {'low': False, 'said': False}), (server.QUIET, {'at': None, 'count': 0}),
                               (core._DISK, {'at': None, 'free': None}),
                               (server.STAGE, {'name': None, 'since': None, 'thread': None, 'said': False, 'took': {}}),
                               (server.EASE, {'aware': False, 'on': False, 'load': 0.0}),
                               (server.CLOCK, {'wall': None, 'mono': None}),
                               (core._REPEAT, {'line': None, 'count': 0, 'said_at': 0.0})):
            p = mock.patch.dict(target, values)
            p.start()
            self.addCleanup(p.stop)
        self.patch(statusline, 'precompile', lambda *a, **k: None)
        server.WAKE.clear()
        self.addCleanup(server.WAKE.clear)

    def patch(self, target, name, value, create=False):
        p = mock.patch.object(target, name, value, create=create)
        p.start()
        self.addCleanup(p.stop)

    def serve(self, cls=None, handler=None):
        httpd = (cls or server.PageServer)(('127.0.0.1', 0), handler or server.Handler)
        self.assertNotIn(httpd.server_address[1], REAL_PAGES)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        os.environ['SWITCHBOARD_PORT'] = str(httpd.server_address[1])
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.port = httpd.server_address[1]
        return httpd

    def get(self, path='/api/state', timeout=10):
        req = urllib.request.Request('http://127.0.0.1:%d%s' % (self.port, path), headers=pagekey_help.with_key())
        try:
            with DIRECT.open(req, timeout=timeout) as r:
                return r.status, json.load(r), dict(r.headers)
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.load(e), dict(e.headers)

    def post(self, path, body=None, headers=None):
        h = pagekey_help.with_key({'Content-Type': 'application/json', 'X-Switchboard': '1'})
        h.update(headers or {})
        req = urllib.request.Request('http://127.0.0.1:%d%s' % (self.port, path), data=json.dumps(body or {}).encode(),
                                     headers=h)
        try:
            with DIRECT.open(req, timeout=30) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.load(e)

    def log_lines(self):
        try:
            with open(os.path.join(self.data, 'switchboard.log'), encoding='utf-8') as f:
                return [ln.rstrip('\n').split(' ', 2)[2] for ln in f]
        except FileNotFoundError:
            return []


class Clock(object):
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


# ---------- the snapshot itself: one build at a time, and every request answered in a bounded time ----------

class TheSnapshot(Sandbox):
    """GET /api/state comes from a snapshot one thread builds; a request never waits past its budget."""

    def snapshot(self, build, files=None, **budgets):
        snap = server.Snapshot(build=build, signal=files or (lambda: ('same',)))
        for k, v in budgets.items():
            setattr(snap, k, v)
        return snap

    def body(self, answer):
        code, raw = answer
        return code, json.loads(raw.decode())

    def test_built_once_then_served_from_memory_while_nothing_changed(self):
        calls = []
        snap = self.snapshot(lambda: calls.append(1) or {'accounts': [], 'n': len(calls)})
        for _ in range(20):
            code, got = self.body(snap.answer())
            self.assertEqual((code, got['n'], got['answer']['stale']), (200, 1, False))
        self.assertEqual(len(calls), 1)
        self.assertEqual(set(got['answer']), {'age', 'built', 'stale', 'building', 'took'})

    def test_a_change_builds_again_and_the_request_gets_the_new_one(self):
        n = [0]
        files = ['a']
        snap = self.snapshot(lambda: n.__setitem__(0, n[0] + 1) or {'n': n[0]}, lambda: (files[0],))
        self.assertEqual(self.body(snap.answer())[1]['n'], 1)
        files[0] = 'b'                                    # state.json written by the tick, say
        time.sleep(snap.SIGNAL_EVERY + 0.05)
        self.assertEqual(self.body(snap.answer())[1]['n'], 2)
        snap.invalidate()                                 # an action
        self.assertEqual(self.body(snap.answer())[1]['n'], 3)
        core.write_text(os.path.join(self.data, 'x'), 'y')   # a write by this process
        self.assertEqual(self.body(snap.answer())[1]['n'], 4)

    def test_older_than_its_age_it_is_built_again_at_most_every_two_seconds(self):
        clock = Clock()
        n = [0]
        snap = server.Snapshot(build=lambda: n.__setitem__(0, n[0] + 1) or {'n': n[0]}, signal=lambda: ('same',),
                               clock=clock)
        self.body(snap.answer())
        clock.t += 1.0
        self.assertEqual(self.body(snap.answer())[1]['n'], 1)        # young: as it is
        clock.t += 1.5
        got = self.body(snap.answer())[1]                             # 2.5 s: built again (it waits a moment for it)
        self.assertEqual(got['n'], 2)
        self.assertEqual(n[0], 2)

    def test_a_slow_build_never_holds_a_request_past_its_budget(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        n = [0]

        def build():
            n[0] += 1
            if n[0] > 1:
                gate.wait(30)                              # the second build is stuck (a lock, a disk at load 213)
            return {'n': n[0]}
        files = ['a']
        snap = self.snapshot(build, lambda: (files[0],), CHANGED_WAIT=0.3, STALE_WAIT=0.05)
        self.body(snap.answer())
        files[0] = 'b'
        time.sleep(snap.SIGNAL_EVERY + 0.05)
        t0 = time.monotonic()
        code, got = self.body(snap.answer())                        # a change: waits CHANGED_WAIT at most
        self.assertLess(time.monotonic() - t0, 2.0)
        self.assertEqual((code, got['n'], got['answer']['stale'], got['answer']['building']), (200, 1, True, True))
        for _ in range(5):                                          # and every later one answers at once, the old one
            t0 = time.monotonic()
            code, got = self.body(snap.answer())
            self.assertLess(time.monotonic() - t0, 1.0)
            self.assertEqual((code, got['n']), (200, 1))
        self.assertEqual(n[0], 2)                                   # never a second build beside the stuck one
        gate.set()

    def test_after_an_action_a_request_waits_longer_for_its_result(self):
        n = [0]
        slow = [0.0]

        def build():
            n[0] += 1
            time.sleep(slow[0])
            return {'n': n[0]}
        snap = self.snapshot(build, CHANGED_WAIT=0.1, ACTION_WAIT=5.0)
        self.body(snap.answer())
        slow[0] = 0.6                                               # longer than CHANGED_WAIT, shorter than ACTION_WAIT
        snap.invalidate()
        code, got = self.body(snap.answer())
        self.assertEqual((code, got['n'], got['answer']['stale']), (200, 2, False))

    def test_no_answer_yet_and_a_slow_first_build_is_503_with_a_retry_hint(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        snap = self.snapshot(lambda: gate.wait(30) and {'n': 1}, FIRST_WAIT=0.3)
        t0 = time.monotonic()
        code, got = self.body(snap.answer())
        self.assertLess(time.monotonic() - t0, 2.0)
        self.assertEqual((code, got['busy'], got['retry']), (503, True, 2))
        self.assertIn('busy', got['error'])
        gate.set()
        for _ in range(100):
            if not snap.building:
                break
            time.sleep(0.02)
        self.assertEqual(self.body(snap.answer())[0], 200)

    def test_a_build_that_fails_keeps_the_last_good_answer_or_says_something_went_wrong(self):
        fail = [False]

        def build():
            if fail[0]:
                raise KeyError('/private/secret/path')
            return {'n': 1}
        files = ['a']
        snap = self.snapshot(build, lambda: (files[0],))
        fresh = self.snapshot(lambda: (_ for _ in ()).throw(KeyError('/private/secret/path')))
        self.assertEqual(self.body(fresh.answer()), (500, {'error': 'Something went wrong.'}))
        self.body(snap.answer())
        fail[0], files[0] = True, 'b'
        time.sleep(snap.SIGNAL_EVERY + 0.05)
        code, got = self.body(snap.answer())
        self.assertEqual((code, got['n'], got['answer']['stale']), (200, 1, True))

    def test_another_folder_never_gets_this_ones_answer(self):
        snap = self.snapshot(lambda: {'home': core.data_dir()})
        self.assertEqual(self.body(snap.answer())[1]['home'], self.data)
        other = os.path.join(self.tmp, 'other')
        with mock.patch.dict(os.environ, {'SWITCHBOARD_HOME': other}):
            self.assertEqual(self.body(snap.answer())[1]['home'], other)

    def test_a_number_that_is_not_finite_never_breaks_the_page(self):
        snap = self.snapshot(lambda: {'pct': float('nan'), 'big': float('inf'), 'list': [1.5, float('-inf')]})
        code, raw = snap.answer()
        self.assertNotIn(b'NaN', raw)
        self.assertNotIn(b'Infinity', raw)
        got = json.loads(raw)
        self.assertEqual((got['pct'], got['big'], got['list']), (None, None, [1.5, None]))

    def test_a_build_stuck_for_long_is_said_once_in_the_log_with_where_it_waits(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        clock = Clock(100.0)

        def waiting_for_the_lock():
            gate.wait(30)
            return {'n': 1}
        snap = server.Snapshot(build=waiting_for_the_lock, signal=lambda: ('same',), clock=clock)
        snap.FIRST_WAIT = 0.2
        self.assertEqual(snap.answer()[0], 503)
        clock.t += snap.STUCK + 1
        for _ in range(3):
            snap.answer()
        said = [x for x in self.log_lines() if 'has been building' in x]
        self.assertEqual(len(said), 1, self.log_lines())
        self.assertIn('waiting_for_the_lock', said[0])
        gate.set()

    def test_the_demo_is_answered_from_memory(self):
        core.DEMO = True
        with mock.patch.object(server, 'view', lambda: {'demo': True, 'accounts': []}):
            code, raw = server.SNAPSHOT.answer()
        self.assertEqual((code, json.loads(raw)), (200, {'demo': True, 'accounts': []}))


# ---------- the server: bounded threads, quiet when a client goes away, never a second answer ----------

class BusyHandler(server.Handler):
    """GET /slow holds its thread until released."""
    gate = None
    inside = None

    def _get(self):
        if self.path == '/slow':
            BusyHandler.inside.release()
            BusyHandler.gate.wait(30)
            return self._send(200, {'slow': True})
        return server.Handler._get(self)


class Counting(server.PageServer):
    """PageServer that keeps the most requests it answered at once."""
    MAX_THREADS = 3

    def __init__(self, *a, **k):
        server.PageServer.__init__(self, *a, **k)
        self.now, self.most, self.count_lock = 0, 0, threading.Lock()

    def _answer(self, request, client_address):
        with self.count_lock:
            self.now += 1
            self.most = max(self.most, self.now)
        try:
            server.PageServer._answer(self, request, client_address)
        finally:
            with self.count_lock:
                self.now -= 1


def rst_after(port, path, wait, read=False):
    """A client that asks, waits, then goes away abruptly (an RST: a tab that gave up)."""
    s = socket.create_connection(('127.0.0.1', port), timeout=5)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
    s.sendall(('GET %s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nX-Switchboard-Key: %s\r\n\r\n'
               % (path, port, pagekey_help.with_key()['X-Switchboard-Key'])).encode())
    time.sleep(wait)
    if read:
        s.recv(10)
    s.close()


class TheServer(Sandbox):
    def setUp(self):
        super().setUp()
        self.patch(server, 'view', lambda: {'version': core.VERSION, 'accounts': [], 'demo': False, 'pad': 'z' * 2000000})

    def test_one_more_request_than_its_threads_is_refused_at_once_with_503_and_a_retry_hint(self):
        BusyHandler.gate, BusyHandler.inside = threading.Event(), threading.Semaphore(0)
        self.addCleanup(BusyHandler.gate.set)
        httpd = self.serve(Counting, BusyHandler)
        pool = ThreadPoolExecutor(8)
        self.addCleanup(pool.shutdown, False)
        held = [pool.submit(self.get, '/slow', 30) for _ in range(3)]
        for _ in range(3):
            self.assertTrue(BusyHandler.inside.acquire(timeout=20))
        t0 = time.monotonic()
        code, got, headers = self.get('/api/state', 30)
        self.assertLess(time.monotonic() - t0, 3.0 if loaded() < 2 else 20.0)
        self.assertEqual((code, got['busy'], headers.get('Retry-After')), (503, True, '2'))
        self.assertTrue(server.page_answers(self.port))             # a busy page is still Switchboard's page
        BusyHandler.gate.set()
        self.assertEqual([f.result(30)[0] for f in held], [200, 200, 200])
        self.assertEqual(httpd.most, 3)
        self.assertGreaterEqual(httpd.refused, 1)
        self.assertEqual(self.get('/api/state')[0], 200)             # room again

    def test_a_refused_client_that_sends_nothing_never_stops_the_server_accepting(self):
        BusyHandler.gate, BusyHandler.inside = threading.Event(), threading.Semaphore(0)
        self.addCleanup(BusyHandler.gate.set)
        httpd = self.serve(Counting, BusyHandler)
        httpd.REFUSE_WAIT = 3.0             # refused clients that never send their request (a refusal does not wait)
        pool = ThreadPoolExecutor(8)
        self.addCleanup(pool.shutdown, False)
        held = [pool.submit(self.get, '/slow', 60) for _ in range(3)]
        for _ in range(3):
            self.assertTrue(BusyHandler.inside.acquire(timeout=20))
        silent = [socket.create_connection(('127.0.0.1', self.port), timeout=10) for _ in range(5)]
        self.addCleanup(lambda: [s.close() for s in silent])
        t0 = time.monotonic()
        code = self.get('/api/state', 60)[0]                       # behind five silent ones, still answered
        self.assertEqual(code, 503)
        self.assertLess(time.monotonic() - t0, 25.0 if loaded() < 2 else 60.0)
        BusyHandler.gate.set()
        self.assertEqual([f.result(60)[0] for f in held], [200, 200, 200])

    def test_refused_clients_that_send_late_each_still_read_their_503(self):
        """Why TheSlowWorld's two hundred saw a reset on a loaded Mac: each refusal waited (up to REFUSE_WAIT) for its
        request, which a busy client can take seconds to send, so the two refusers fell behind, their queue filled, and
        the connections past it were closed with no answer at all (ConnectionResetError, or RemoteDisconnected, for
        the client: no Switchboard). Here ten refused clients send their requests a second late, with room for two in
        the queue: every one must read its 503."""
        BusyHandler.gate, BusyHandler.inside = threading.Event(), threading.Semaphore(0)
        self.addCleanup(BusyHandler.gate.set)

        class SmallQueue(Counting):
            REFUSE_QUEUE = 2
        self.serve(SmallQueue, BusyHandler)
        pool = ThreadPoolExecutor(3)
        self.addCleanup(pool.shutdown, False)
        held = [pool.submit(self.get, '/slow', 60) for _ in range(3)]
        for _ in range(3):
            self.assertTrue(BusyHandler.inside.acquire(timeout=20))
        late = [socket.create_connection(('127.0.0.1', self.port), timeout=10) for _ in range(10)]
        self.addCleanup(lambda: [s.close() for s in late])
        time.sleep(1.0)                                            # the requests go out only now (a busy client)
        firsts = []
        for s in late:
            got = b''
            try:
                s.sendall(('GET /api/state HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n\r\n' % self.port).encode())
                while b'\r\n' not in got:
                    part = s.recv(65536)
                    if not part:
                        break
                    got += part
            except OSError as e:
                got = got or repr(e).encode()
            firsts.append(got.split(b'\r\n')[0].decode('utf-8', 'replace') or 'closed with no answer')
        self.assertEqual(firsts, ['HTTP/1.0 503 Service Unavailable'] * 10)
        BusyHandler.gate.set()
        self.assertEqual([f.result(60)[0] for f in held], [200, 200, 200])

    def test_clients_that_go_away_are_a_line_or_two_never_a_traceback(self):
        err = io.StringIO()
        self.patch(sys, 'stderr', err)
        for cls in (server.PageServer, ThreadingHTTPServer):         # its own server, and any other one tests use
            with self.subTest(server=cls.__name__):
                self.serve(cls)
                self.assertEqual(self.get('/api/state')[0], 200)     # the snapshot is built (2 MB: writes block)
                for _ in range(150):
                    rst_after(self.port, '/api/state', 0.01)
                t0 = time.monotonic()
                self.assertEqual(self.get('/api/state', 60)[0], 200)  # and the next call is fast
                self.assertLess(time.monotonic() - t0, 5.0 if loaded() < 2 else 30.0)
        time.sleep(0.5)
        self.assertNotIn('Traceback', err.getvalue())
        self.assertNotIn('During handling', err.getvalue())
        gone = [x for x in self.log_lines() if 'ended by the other side' in x]
        self.assertLessEqual(len(gone), 4, self.log_lines())

    def test_a_failure_after_the_client_left_is_one_line_and_no_second_answer(self):
        err = io.StringIO()
        self.patch(sys, 'stderr', err)
        self.serve()
        started = threading.Event()

        def fails_late():
            started.set()
            time.sleep(0.4)                                          # the client gives up meanwhile
            raise ValueError('made up')
        self.patch(server, 'history_view', fails_late)
        threading.Thread(target=rst_after, args=(self.port, '/api/history', 0.1)).start()
        self.assertTrue(started.wait(10))
        time.sleep(1.0)
        self.assertNotIn('Traceback', err.getvalue())
        self.assertIn("GET /api/history failed: ValueError('made up')", self.log_lines())

    def test_a_handler_that_fails_before_answering_sends_one_plain_500(self):
        self.serve()
        self.patch(server, 'history_view', mock.Mock(side_effect=KeyError('/private/secret/path')))
        self.assertEqual(self.get('/api/history')[:2], (500, {'error': 'Something went wrong.'}))

    def test_a_port_that_is_taken_is_said_plainly(self):
        httpd = self.serve()
        with self.assertRaises(OSError) as cm:
            server.PageServer(('127.0.0.1', httpd.server_address[1]), server.Handler)
        self.assertEqual(cm.exception.errno, 48)                     # EADDRINUSE, not an AttributeError on the way out
        self.assertTrue(server.page_answers(self.port))

    def test_each_refused_request_is_one_line_with_its_reason(self):
        self.serve()
        req = urllib.request.Request('http://127.0.0.1:%d/api/state' % self.port, headers={'Host': 'evil.example'})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            DIRECT.open(req, timeout=10)
        cm.exception.close()
        self.assertEqual(self.post('/api/switch', {'email': 'a@b.example'}, headers={'Origin': 'https://evil.example'})[0], 403)
        for _ in range(3):
            self.assertEqual(self.post('/api/refresh', {}, headers={'X-Switchboard': ''})[0], 403)
        said = [x for x in self.log_lines() if x.startswith('refused')]
        self.assertEqual(said, ['refused GET /api/state: it asked for another host (evil.example)',
                                'refused POST /api/switch: it came from another website (https://evil.example)',
                                "refused POST /api/refresh: it did not carry the page's own header"])
        core.log_flush()
        self.assertIn('(the line above 2 more times)', self.log_lines())

    def test_every_answer_closes_its_connection_and_is_never_cached(self):
        self.serve()
        code, _, headers = self.get('/api/state')
        self.assertEqual((code, headers.get('Connection'), headers.get('Cache-Control')), (200, 'close', 'no-store'))

    def test_a_post_that_is_too_large_or_has_no_length_is_refused_plainly(self):
        self.serve()
        s = socket.create_connection(('127.0.0.1', self.port), timeout=10)
        self.addCleanup(s.close)
        s.sendall(('POST /api/refresh HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nX-Switchboard: 1\r\nX-Switchboard-Key: %s\r\n'
                   'Content-Length: 99999999\r\n\r\n' % (self.port, pagekey_help.with_key()['X-Switchboard-Key'])).encode())
        got = b''
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            got += chunk
        head = got.decode('latin-1')
        self.assertTrue(head.startswith('HTTP/1.0 400'), head)
        self.assertIn('too large', head)

    def test_an_action_makes_the_next_state_a_fresh_one(self):
        n = [0]
        self.patch(server, 'view', lambda: n.__setitem__(0, n[0] + 1) or {'version': core.VERSION, 'accounts': [],
                                                                          'demo': False, 'n': n[0]})
        self.serve()
        self.assertEqual(self.get()[1]['n'], 1)
        self.assertEqual(self.get()[1]['n'], 1)
        self.assertEqual(self.post('/api/refresh')[0], 200)
        self.assertEqual(self.get()[1]['n'], 2)


# ---------- a slow world, and the page still answers at once ----------

class SlowWorld(object):
    """Every outside program takes DELAY seconds (released at once when the test ends, so nothing runs on after it)."""
    DELAY = 5.0

    def __init__(self, delay=None):
        if delay is not None:
            self.DELAY = delay
        self.release = threading.Event()
        self.calls = []

    def sleep(self, what):
        self.calls.append(what)
        self.release.wait(self.DELAY)

    def run(self, cmd, *a, **k):
        self.sleep(cmd)
        return subprocess.CompletedProcess(cmd, 0, b'' if not k.get('text') else '', b'' if not k.get('text') else '')

    def call(self, cmd, *a, **k):
        self.sleep(cmd)
        return 0

    def check_output(self, cmd, *a, **k):
        self.sleep(cmd)
        return b''

    def popen(self, world):
        class Proc(object):
            def __init__(self, cmd, *a, **k):
                self.args, self.pid, self.returncode = cmd, 99999, None
                self.stdin = self.stdout = self.stderr = None
                world.calls.append(cmd)

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                world.release.wait(world.DELAY if timeout is None else min(timeout, world.DELAY))
                self.returncode = 0
                return 0

            def communicate(self, input=None, timeout=None):
                self.wait(timeout)
                return (b'', b'')

            def terminate(self):
                self.returncode = -15

            kill = terminate
        return Proc


class LinkApp(Sandbox):
    def install_link_app(self):
        """A made-up Switchboard Links.app in the throwaway home: linkapp.status would start it (here: a slow stand-in)."""
        import plistlib
        app = os.path.join(self.home, 'Applications', 'Switchboard Links.app', 'Contents')
        os.makedirs(os.path.join(app, 'MacOS'))
        with open(os.path.join(app, 'Info.plist'), 'wb') as f:
            plistlib.dump({'CFBundleIdentifier': linkapp.BUNDLE_ID, 'CFBundleExecutable': 'SwitchboardLinks'}, f)
        exe = os.path.join(app, 'MacOS', 'SwitchboardLinks')
        with open(exe, 'w') as f:
            f.write('#!/bin/sh\nexit 0\n')
        os.chmod(exe, 0o755)
        self.assertTrue(linkapp.installed())


class TheSlowWorld(LinkApp):
    """Every subprocess takes 5 s (the link app's --status, the engine, pmset, osascript), the loop runs, the link
    app's status is refreshed, the state lock is held: GET /api/state still answers in under 200 ms."""
    DELAY = 5.0

    def setUp(self):
        super().setUp()
        self.install_link_app()
        world = self.world = SlowWorld(self.DELAY)
        self.addCleanup(world.release.set)
        for name, fake in (('run', world.run), ('call', world.call), ('check_output', world.check_output),
                           ('check_call', world.call), ('Popen', world.popen(world))):
            self.patch(subprocess, name, fake)
        self.patch(engine, 'listing', lambda: world.sleep('engine list') or {'rows': [], 'repair': None})
        self.patch(server, 'MAC_TICK', 0.2)
        self.patch(server.claudecode, 'CAP', {'running': True, 'at': 0})        # claude-mem's own ticks: not here
        self.patch(server.claudecode, 'HEALTH', {'running': True, 'at': 0})
        stop = threading.Event()
        self.loop = threading.Thread(target=server.loop, kwargs={'stop': stop}, daemon=True)

        def end():
            stop.set()
            world.release.set()
            if self.loop.ident is None:
                return
            server.WAKE.set()
            self.loop.join(60)
            server.WAKE.clear()
            self.assertFalse(self.loop.is_alive(), 'the loop did not stop')
            settle_looks()              # the world is released: what the loop started ends here, still in this world
        self.addCleanup(end)

    def timed(self, n, parallel=1):
        def one(_):
            t0 = time.monotonic()
            code, got, _ = self.get('/api/state', 10)
            return code, time.monotonic() - t0
        with ThreadPoolExecutor(parallel) as pool:
            return list(pool.map(one, range(n)))

    def check(self, results):
        """Every answer is the state, and none waited on the slow world. On a Mac that is not overloaded (load per core
        under 2), every one in under 200 ms. On a busier one, threads at background priority are delayed whatever the
        code does, so there the bound is the world's own: no answer took anywhere near a program's DELAY."""
        codes = [c for c, _ in results]
        took = sorted(t for _, t in results)
        self.assertEqual(set(codes), {200}, codes)
        load = loaded()
        said = 'slowest %.3f s, median %.3f s, load per core %.1f' % (took[-1], statistics.median(took), load)
        if load < 2:
            self.assertLess(took[-1], 0.2, said)
        else:
            self.assertLess(took[-1], self.DELAY / 2, said)
        return took

    def test_the_state_answers_in_under_200_ms_while_every_program_takes_5_s(self):
        with server.LINKS_LOCK:
            server.LINKS['loop'] = True                 # as serve() sets it: the link app's status is the loop's job
        self.serve()
        self.loop.start()
        server.refresh_link_status(force=True)          # its --status is under way (5 s)
        took = self.check(self.timed(20))               # one after another
        took += self.check(self.timed(60, parallel=12)) # and a dozen at once
        self.assertTrue(any('--status' in str(c) for c in self.world.calls), self.world.calls)   # it was slow, elsewhere
        code, got, _ = self.get('/api/state')
        self.assertEqual(got['links']['installed'], True)          # read from its Info.plist, never by starting it
        self.assertIs(got['links']['on'], False)                   # not known yet: not "on"

    def test_with_the_state_lock_held_5_s_the_state_still_answers_from_the_snapshot(self):
        self.serve()
        for _ in range(30):                              # the first answer (on a very busy Mac it may say busy first)
            if self.get('/api/state')[0] == 200:
                break
            time.sleep(1)
        held = threading.Event()

        def writer():
            with core.locked():
                held.set()
                time.sleep(5)                            # a slow writer, holding the state lock
        threading.Thread(target=writer, daemon=True).start()
        self.assertTrue(held.wait(10))
        t0 = time.monotonic()
        code = self.get('/api/state')[0]
        self.assertEqual(code, 200)
        self.assertLess(time.monotonic() - t0, 1.0 if loaded() < 2 else 4.0)

    def test_two_hundred_at_once_at_a_build_that_takes_a_second(self):
        builds = []

        def build():
            builds.append(time.monotonic())
            time.sleep(1.0)
            return {'version': core.VERSION, 'accounts': [], 'demo': False}
        self.patch(server, 'SNAPSHOT', server.Snapshot(build=build, signal=lambda: ('same',)))
        httpd = self.serve(Counting)
        httpd.MAX_THREADS = 16
        httpd.slots = threading.BoundedSemaphore(16)
        # The install's secret is there before the clients come, as in an install: the first page key a client takes
        # makes it, and that write to settings.json is a change the state is rightly built again for at once.
        pagekey_help.with_key()
        started = time.monotonic()
        until = started + 6.0
        codes = []

        def client(_):
            while time.monotonic() < until:
                try:
                    codes.append(self.get('/api/state', 15)[0])
                except OSError as e:
                    codes.append(repr(e))
                time.sleep(0.2)
        with ThreadPoolExecutor(200) as pool:
            list(pool.map(client, range(200)))
        self.assertTrue(codes)
        self.assertEqual(set(codes) - {200, 503}, set())            # every request answered: the state or busy
        self.assertLessEqual(httpd.most, 16)                         # never more threads than its bound
        gaps = [b - a for a, b in zip(builds, builds[1:])]
        self.assertTrue(all(g >= 1.9 for g in gaps), gaps)           # one build at a time, at most every 2 s
        self.assertLessEqual(len(builds), (time.monotonic() - started) / 2.0 + 1)


class NothingStartsOnTheRequestPath(LinkApp):
    """Without a stopwatch: answering /api/state (and building its snapshot) never starts a program. Every way to
    start one fails the test here; the link app is installed, so its status would start it if it were asked inline."""

    def test_no_program_is_started_to_answer_the_state(self):
        self.install_link_app()
        started = []

        def refuse(*a, **k):
            started.append((a[:1], ''.join(__import__('traceback').format_stack(limit=6))))
            raise AssertionError('a program was started on the request path')
        for name in ('run', 'call', 'check_output', 'check_call', 'Popen'):
            self.patch(subprocess, name, refuse)
        with server.LINKS_LOCK:
            server.LINKS['loop'] = True
        self.serve()
        for _ in range(5):
            code, got, _ = self.get('/api/state')
            self.assertEqual(code, 200)
            self.post('/api/refresh')                     # each action: a fresh build
        self.assertEqual(started, [])
        self.assertEqual((got['links']['installed'], got['links']['on']), (True, False))


class OpenFiles(unittest.TestCase):
    def test_launchds_256_open_files_are_raised_toward_the_hard_limit(self):
        import resource
        seen = []
        with mock.patch.object(resource, 'getrlimit', lambda what: (256, 10240)), \
                mock.patch.object(resource, 'setrlimit', lambda what, limits: seen.append(limits)):
            self.assertEqual(server.more_files(), 4096)
        self.assertEqual(seen, [(4096, 10240)])
        with mock.patch.object(resource, 'getrlimit', lambda what: (8192, 10240)), \
                mock.patch.object(resource, 'setrlimit', lambda what, limits: seen.append(limits)):
            self.assertEqual(server.more_files(), 8192)                       # never lowered
        with mock.patch.object(resource, 'getrlimit', lambda what: (256, 1024)), \
                mock.patch.object(resource, 'setrlimit', mock.Mock(side_effect=ValueError('no'))):
            self.assertIsNone(server.more_files())                            # said nothing, broke nothing
        self.assertEqual(seen, [(4096, 10240)])


# ---------- the link app's status: the loop's, never a request's ----------

class TheLinkAppStatus(LinkApp):
    def test_the_state_never_starts_the_link_app_and_the_loop_looks_every_20_s(self):
        self.install_link_app()
        seen = []
        release = threading.Event()
        self.addCleanup(release.set)

        def status(fresh=False):
            seen.append(fresh)
            release.wait(10)
            return {'installed': True, 'on': True, 'browser': 'Switchboard Links'}
        self.patch(linkapp, 'status', status)
        with server.LINKS_LOCK:
            server.LINKS['loop'] = True
        t0 = time.monotonic()
        f = server.facts()
        self.assertLess(time.monotonic() - t0, 5.0)
        self.assertEqual((f['links']['installed'], f['links']['on'], seen), (True, False, []))
        self.assertTrue(server.refresh_link_status(now=100.0, force=True))
        self.assertFalse(server.refresh_link_status(now=100.0, force=True))     # one look at a time
        release.set()
        for _ in range(200):
            if not server.LINKS['running']:
                break
            time.sleep(0.02)
        self.assertEqual(seen, [True])
        self.assertTrue(server.facts()['links']['on'])
        at = server.LINKS['at']
        self.assertFalse(server.refresh_link_status(now=at + 5))                 # under 20 s: not again
        self.assertTrue(server.refresh_link_status(now=at + 21))

    def test_a_command_in_a_terminal_still_asks_the_link_app_itself(self):
        self.patch(linkapp, 'status', lambda fresh=False: {'installed': False, 'on': False, 'browser': None, 'asked': 1})
        self.assertEqual(server.link_status()['asked'], 1)                        # no loop in this process
        self.assertFalse(server.refresh_link_status(force=True))


# ---------- "Let Switchboard check links": macOS's own answer, asked only from the button ----------

class TheLinksCheck(Sandbox):
    def setUp(self):
        super().setUp()
        self.serve()

    def wait_done(self):
        for _ in range(300):
            if not server.CONSENT['running']:
                return
            time.sleep(0.02)

    def consent_api(self, look, ask=None):
        """browser.py's own consent check, faked: consent(fresh) looks without a question; ask_consent() asks."""
        self.patch(browser, 'consent', lambda fresh=False: look())
        self.patch(browser, 'ask_consent', ask or (lambda deadline=120: self.fail('macOS was asked')))

    def test_without_a_consent_check_it_says_it_cannot_tell(self):
        self.patch(browser, 'consent', None)
        code, got = self.post('/api/consent')
        self.assertEqual((code, got['state']), (200, 'unknown'))
        self.assertIn("can't", got['line'])
        self.assertEqual(self.get()[1]['consent']['state'], 'unknown')

    def test_a_quiet_look_never_asks_and_the_button_does(self):
        asked = []
        self.consent_api(lambda: asked.append('look') or 'would_ask',
                         lambda deadline=120: asked.append('ask') or 'allowed')
        t = server.consent_refresh()
        t.join(10)
        self.assertEqual(self.get()[1]['consent']['state'], 'would_ask')
        self.assertIsNone(server.consent_refresh(now=server.CONSENT['at'] + 60))  # quiet looks: every 5 min at most
        code, got = self.post('/api/consent')
        self.assertEqual((code, got['state']), (200, 'allowed'))
        self.assertEqual(asked, ['look', 'look', 'ask'])                            # macOS asked only for the press
        self.assertEqual(self.get()[1]['consent'], {'state': 'allowed', 'line': server.CONSENT_LINES['allowed'],
                                                    'checked': server.CONSENT['wall'], 'can_ask': True})

    def test_a_press_when_macos_already_said_asks_nothing(self):
        self.consent_api(lambda: 'denied')
        self.assertEqual(self.post('/api/consent')[1]['state'], 'denied')

    def test_while_macos_asks_the_press_answers_and_the_answer_comes_later(self):
        answer = threading.Event()
        self.addCleanup(answer.set)
        self.consent_api(lambda: 'would_ask', lambda deadline=120: answer.wait(20) and 'denied')
        self.patch(server, 'CONSENT_WAIT', 0.2)
        t0 = time.monotonic()
        code, got = self.post('/api/consent')
        self.assertLess(time.monotonic() - t0, 10)
        self.assertEqual((code, got['state']), (200, 'asking'))
        self.assertEqual(server.consent_refresh(ask=True), None)                  # never two questions at once
        answer.set()
        self.wait_done()
        state = self.get()[1]['consent']
        self.assertEqual(state['state'], 'denied')
        self.assertIn('System Settings', state['line'])

    def test_a_question_another_press_put_up_is_waited_out(self):
        self.patch(browser, 'CONSENT', dict(browser.CONSENT, asking=True, state=None))
        self.consent_api(lambda: 'would_ask', lambda deadline=120: 'asking')

        def answered():
            time.sleep(0.3)
            with browser._CONSENT_LOCK:
                browser.CONSENT.update(asking=False, state='allowed')
        threading.Thread(target=answered, daemon=True).start()
        server.consent_refresh(ask=True).join(10)
        self.assertEqual(server.consent_view()['state'], 'allowed')

    def test_a_check_that_raises_or_answers_nonsense_is_not_known(self):
        def raises():
            raise OSError('no')
        for look in (raises, lambda: 'maybe'):
            with self.subTest(look=look):
                self.consent_api(look)
                server.consent_refresh(ask=True).join(10)
                self.assertEqual(server.consent_view()['state'], 'unknown')


# ---------- notifications: a test the person asked for, and "did you see it?" ----------

class TheNotificationTest(Sandbox):
    def setUp(self):
        super().setUp()
        self.serve()

    def test_a_test_is_sent_and_the_answer_is_kept_and_shown(self):
        code, got = self.post('/api/notifytest')
        self.assertEqual((code, got['sent']), (200, True))
        self.assertEqual(len(self.shown), 1)
        self.assertEqual(self.shown[0][-2:], [notify.TEST_TITLE, notify.TEST_BODY])
        self.assertIsNone(self.get()[1]['notify_check']['seen'])
        code, got = self.post('/api/notifytest', {'seen': False})
        self.assertEqual((code, got['line']), (200, notify.SCRIPT_EDITOR_OFF))
        self.assertIs(self.get()[1]['notify_check']['seen'], False)
        self.post('/api/notifytest', {'seen': True})
        self.assertIs(self.get()[1]['notify_check']['seen'], True)

    def test_osascript_failing_is_said_plainly(self):
        for code, words in ((1, 'ended with 1'), (None, "couldn't start osascript"), (notify.TIMEOUT, 'took too long')):
            with self.subTest(code=code):
                self.patch(server, 'NOTIFY_RUN', lambda cmd, c=code: c)
                status, got = self.post('/api/notifytest')
                self.assertEqual((status, got['sent']), (200, False))
                self.assertIn(words, got['line'])


# ---------- a disk almost full ----------

class TheDiskAlmostFull(Sandbox):
    def test_one_notification_one_line_extras_paused_and_back_when_there_is_room(self):
        free = [100 << 20]
        self.patch(core, 'disk_free', lambda fresh=False, clock=None: free[0])
        refreshed = []
        self.patch(server.artifacts, 'refresh', lambda: refreshed.append(1))
        self.patch(engine, 'listing', lambda: {'rows': [], 'repair': None})
        self.assertTrue(server.disk_tick(server.NOTIFY_RUN))
        server.disk_tick(server.NOTIFY_RUN)
        self.assertEqual([c[-2] for c in self.shown], ['Your disk is almost full'])
        # what is paused, in the page's own names for those parts, and the one thing to do
        self.assertEqual([e['text'] for e in core.load()['events']], [
            'Your disk is almost full, so Switchboard paused new previews and updates to Artifacts, Chats and Your '
            'week. Free some disk space.'])
        self.assertEqual(server.notify.disk_full().body, 'Switchboard paused new previews and updates to Artifacts, '
                                                         'Chats and Your week until there is room. Free some disk space.')
        server.tick()
        self.assertEqual(refreshed, [])                                 # the artifact scan waits
        # low, yet over the floor a switch needs (50 MB): no sentence about switching (disk_view's 'line')
        self.assertEqual(server.view()['disk'], {'low': True, 'free': 100 << 20, 'line': None})
        free[0] = 30 << 30
        self.assertFalse(server.disk_tick(server.NOTIFY_RUN))
        server.tick()
        self.assertEqual(refreshed, [1])

    def test_a_disk_too_full_for_the_line_still_notifies(self):
        self.patch(core, 'disk_free', lambda fresh=False, clock=None: 1 << 20)
        with mock.patch.object(core, 'write_bytes', side_effect=core.DiskFull(28, 'full', 'state.json')):
            server.disk_tick(server.NOTIFY_RUN)
        self.assertEqual(len(self.shown), 1)


# ---------- a clock set back never stops the readings ----------

class AClockSetBack(Sandbox):
    def test_the_loop_counts_on_the_monotonic_clock(self):
        mono = Clock(5000.0)
        wall = [1.8e9]
        ticks = []
        self.patch(server, 'monotonic', mono)
        self.patch(server, 'tick', lambda: ticks.append((mono.t, wall[0])) or {'rows': None})
        self.patch(server, 'keep_tick', lambda looked: None)
        self.patch(server, 'mac_tick', lambda now=None: None)
        self.patch(keep, 'login_changed', lambda: False)
        self.patch(keep, 'due', lambda now=None: False)
        self.patch(keep, 'wait_for', lambda default, now=None: 0.0)
        self.patch(engine.HOST, 'ensure', lambda now=None: None)
        self.patch(server.claudecode, 'CAP', {'running': True, 'at': 0})
        self.patch(server.claudecode, 'HEALTH', {'running': True, 'at': 0})
        self.patch(server, 'disk_tick', lambda run=None: False)
        self.patch(server, 'load_per_core', lambda: 0.0)          # a quiet Mac: one reading a minute
        passes = [(61, -3600), (5, 0), (60, 7200)]          # (monotonic seconds, wall jump) before passes 2, 3 and 4

        def wait(timeout=None):
            step = passes.pop(0) if passes else (0, 0)
            mono.t += step[0]
            wall[0] += step[1]
            return False
        self.patch(server.WAKE, 'wait', wait)
        with mock.patch.object(server.time, 'time', lambda: wall[0]):
            server.loop(ticks=4)
        # the first pass; after 61 s with the wall clock an hour back; not after 5 more; after 60 more (wall 2 h ahead)
        self.assertEqual([t for t, _ in ticks], [5000.0, 5061.0, 5126.0])

    def test_a_sleep_or_a_clock_set_by_hand_is_one_line_and_one_fresh_reading(self):
        self.assertIsNone(server.clock_step(wall=1000.0, mono=50.0))
        self.assertEqual(server.clock_step(wall=1060.0, mono=110.0), 0.0)         # a minute of each: nothing moved
        self.assertEqual(server.clock_step(wall=1060.0 + 3 * 3600, mono=115.0), 3 * 3600 - 5)   # asleep 3 hours
        self.assertEqual(server.clock_step(wall=-2000.0 + 1060.0 + 3 * 3600, mono=120.0), -2005.0)   # set back
        mono = Clock(5000.0)
        wall = [1.8e9]
        ticks = []
        self.patch(server, 'monotonic', mono)
        self.patch(server, 'tick', lambda: ticks.append(mono.t) or {'rows': None})
        for name, value in (('keep_tick', lambda looked: None), ('mac_tick', lambda now=None: None),
                            ('disk_tick', lambda run=None: False), ('load_per_core', lambda: 0.0)):
            self.patch(server, name, value)
        self.patch(keep, 'login_changed', lambda: False)
        self.patch(keep, 'due', lambda now=None: False)
        self.patch(keep, 'wait_for', lambda default, now=None: 0.0)
        self.patch(engine.HOST, 'ensure', lambda now=None: None)
        self.patch(server.claudecode, 'CAP', {'running': True, 'at': 0})
        self.patch(server.claudecode, 'HEALTH', {'running': True, 'at': 0})
        passes = [(5, 0), (5, 3 * 3600), (5, 0)]                # the Mac sleeps 3 hours before the third pass

        def wait(timeout=None):
            step = passes.pop(0) if passes else (0, 0)
            mono.t += step[0]
            wall[0] += step[0] + step[1]
            return False
        self.patch(server.WAKE, 'wait', wait)
        with mock.patch.object(server.time, 'time', lambda: wall[0]):
            server.loop(ticks=4)
        self.assertEqual(ticks, [5000.0, 5010.0])                # the first pass, and one at wake: not one per minute slept
        said = [x for x in self.log_lines() if x.startswith('the clock moved')]
        self.assertEqual(said, ['the clock moved +180 min against the monotonic clock (the Mac slept, or the time was '
                                'set): reading again'])

    def test_a_question_dated_ahead_is_no_question(self):
        server.asked('remove', 'a@b.example')
        server.ASKED[('remove', 'a@b.example')] = time.time() + 3600   # the clock was set back an hour since
        self.assertFalse(server.answered('remove', 'a@b.example'))

    def test_history_work_dated_ahead_is_read_again(self):
        calls = []
        self.patch(server, 'read_usage', lambda: calls.append(1) or server.USAGE.update(running=False))
        with mock.patch.dict(server.USAGE, {'page': {'days': 7}, 'at': time.time() + 3600, 'running': False}):
            server.usage_view()
            for _ in range(100):
                if calls:
                    break
                time.sleep(0.02)
        self.assertEqual(calls, [1])


# ---------- a Mac under load: the page's own loop does less, and says so ----------

class UnderLoad(Sandbox):
    def test_only_the_pages_own_process_eases_and_by_how_much(self):
        self.assertEqual(server.ease_tick(load=50.0), server.TICK)          # a terminal, a test: never
        self.assertIsNone(server.load_view())
        server.EASE['aware'] = True
        for load, every, on in ((0.5, 60, False), (1.6, 120, False), (3.2, 240, True), (213 / 10.0, 240, True)):
            with self.subTest(load=load):
                self.assertEqual(server.ease_tick(load=load), every)
                self.assertIs(server.easing(), on)
        self.assertEqual(server.load_view(), {'per_core': 21.3, 'easing': True})

    def test_the_extras_wait_and_the_page_is_told(self):
        refreshed = []
        self.patch(server.artifacts, 'refresh', lambda: refreshed.append(1))
        self.patch(engine, 'listing', lambda: {'rows': [], 'repair': None})
        server.EASE['aware'] = True
        server.ease_tick(load=4.0)
        server.tick()
        self.assertEqual(refreshed, [])
        self.assertEqual(server.view()['load'], {'per_core': 4.0, 'easing': True})
        calls = []
        self.patch(server, 'read_usage', lambda: calls.append(1))
        with mock.patch.dict(server.USAGE, {'page': {'days': 7}, 'at': time.time() - 60, 'running': False}):
            server.usage_view(fresh=True)                                    # a minute old: asked for, it would be read
        self.assertEqual(calls, [])                                          # History's work waits too
        server.ease_tick(load=0.4)
        server.tick()
        self.assertEqual(refreshed, [1])


# ---------- where the loop is ----------

class WhereTheLoopIs(Sandbox):
    def test_a_slow_pass_is_one_line_with_each_stages_time(self):
        clock = Clock(100.0)
        self.patch(server, 'monotonic', clock)
        server.stage('reading the accounts')
        clock.t += 8
        server.stage('keeping logins')
        clock.t += 4
        server.stage('waiting')
        server._pass_said(100.0)
        said = [x for x in self.log_lines() if x.startswith('a slow pass')]
        self.assertEqual(len(said), 1)
        self.assertTrue(said[0].startswith('a slow pass: 12 s (reading the accounts 8.0 s, keeping logins 4.0 s; load per core'),
                        said[0])
        server.stage('reading the accounts')
        clock.t += 1
        server.stage('waiting')
        server._pass_said(clock.t - 1)                      # a quick pass says nothing
        self.assertEqual(len([x for x in self.log_lines() if x.startswith('a slow pass')]), 1)

    def test_a_stage_stuck_for_minutes_is_said_once_with_where_it_waits_and_rides_on_the_answer(self):
        clock = Clock(100.0)
        self.patch(server, 'monotonic', clock)
        inside, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def blocked_on_the_engine():
            release.wait(30)

        def loop_like():
            server.STAGE['thread'] = threading.get_ident()
            server.stage('reading the accounts')
            inside.set()
            blocked_on_the_engine()
        threading.Thread(target=loop_like, daemon=True).start()
        self.assertTrue(inside.wait(10))
        self.assertIsNone(server.stage_view(clock.t + 3))
        self.assertEqual(server.stage_view(clock.t + 60), {'stage': 'reading the accounts', 'for': 60.0})
        server.stage_view(clock.t + 121)
        server.stage_view(clock.t + 300)
        said = [x for x in self.log_lines() if "the page's loop has been" in x]
        self.assertEqual(len(said), 1, self.log_lines())
        self.assertIn('reading the accounts for 121 s', said[0])
        self.assertIn('blocked_on_the_engine', said[0])
        snap = server.Snapshot(build=lambda: {'n': 1}, signal=lambda: ('same',), clock=clock)
        clock.t += 200
        self.assertEqual(json.loads(snap.answer()[1])['answer']['loop']['stage'], 'reading the accounts')
        release.set()


# ---------- the status line's own file: renewed, so a page that stopped shows ----------

class TheStatusFile(Sandbox):
    def test_its_time_is_renewed_every_five_minutes_and_on_a_clock_set_back(self):
        st = dict(core.blank_state(), active='ana@work.example')
        path = os.path.join(self.data, 'statusline.json')
        server.write_status_file(st, None, now=1000.0)
        first = os.stat(path).st_mtime_ns
        time.sleep(0.01)
        server.write_status_file(st, None, now=1100.0)
        self.assertEqual(os.stat(path).st_mtime_ns, first)            # nothing new: not written
        server.write_status_file(st, None, now=1000.0 + server.STATUS_FRESH + 1)
        self.assertEqual(core.read_json(path)['at'], 1301)
        server.write_status_file(st, None, now=500.0)                 # the clock went back: its time is renewed
        self.assertEqual(core.read_json(path)['at'], 500)
        self.assertEqual(core.read_json(path)['active'], 'ana@work.example')

    def test_a_tick_late_in_a_second_is_not_a_clock_set_back(self):
        # 'at' was round(now), so a write at 1000.6 s said 1001, and a tick 20 ms later saw its file
        # dated ahead of the clock and wrote it again (test_app.StatusFile failed so, now and then, in every suite)
        st = dict(core.blank_state(), active='ana@work.example')
        path = os.path.join(self.data, 'statusline.json')
        server.write_status_file(st, None, now=1000.6)
        first = os.stat(path).st_mtime_ns
        time.sleep(0.01)
        server.write_status_file(st, None, now=1000.62)
        self.assertEqual(os.stat(path).st_mtime_ns, first)            # nothing new: not written
        self.assertLessEqual(core.read_json(path)['at'], 1000.6)      # never dated past the clock that wrote it


# ---------- the old Link Router still opens links, under the new request handling ----------

class TheOldLinkRouter(Sandbox):
    def test_its_header_may_open_a_link_and_nothing_else(self):
        self.serve()
        opened = []
        self.patch(server.artifacts, 'go_quietly', lambda url, **timing: opened.append(url))
        url = 'https://claude.ai/code/artifact/0f8c2d4e-6a1b-4c3d-9e8f-7a6b5c4d3e2f'
        old = {'X-Claude-Switch': '1', 'X-Switchboard': ''}
        code, got = self.post('/api/go', {'url': url, 'wait': False}, headers=old)
        self.assertEqual((code, got.get('ok')), (200, True))
        for _ in range(100):
            if opened:
                break
            time.sleep(0.02)
        self.assertEqual(opened, [url])
        self.assertEqual(self.post('/api/refresh', {}, headers=old)[0], 403)


if __name__ == '__main__':
    unittest.main()
