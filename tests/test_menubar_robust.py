import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The menu bar when the page is busy, stops answering or isn't running:
# the last good numbers stay, a page that refuses is "isn't running" (and Start), one that does not answer in time is
# "busy", and past 60 s without a good reading the bars go grey and the menu says since when.
# No AppKit: the pure model, the page's stand-ins on free ports, and the real menu bar class on stand-in PyObjC.
import json
import os
import queue
import shutil
import socket
import sys
import tempfile
import threading
import time
import types
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import menubar

TZ = timezone(timedelta(hours=-8))
WALL = datetime(2026, 11, 13, 14, 2, 30, tzinfo=TZ).timestamp()          # Fri 2:02 PM
REAL_PAGES = (7798, 7799)


def acct(slot, email, weekly, active=False, session=None):
    return {'email': email, 'name': email, 'slot': slot, 'active': active, 'needs_login': False, 'disabled': False,
            'unmanaged': False, 'trusted': True, 'line': None, 'usage_at': WALL - 60,
            'usage': {'session': session, 'weekly': {'pct': weekly, 'resets': '2026-11-14T20:00:00+00:00'},
                      'scoped': [], 'spend': None}}


def view(**more):
    return dict({'now': WALL, 'active': 'ana@work.example', 'next': None,
                 'auto': {'available': True, 'enabled': False, 'session_at': 90.0, 'weekly_at': 95.0},
                 'setup': {'engine': True, 'accounts': True},
                 'accounts': [acct(1, 'ana@work.example', 96, active=True), acct(2, 'ana@home.example', 30)]}, **more)


class TheModel(unittest.TestCase):
    """What the item and the menu show across readings, on the monotonic clock."""

    def test_a_busy_page_keeps_the_last_numbers_and_says_busy(self):
        w = menubar.Watch()
        w.took(view(), None, 100.0, WALL)
        self.assertEqual(w.view(100.0), (view(), None))
        w.took(None, 'busy', 115.0, WALL + 15)
        state, status = w.view(115.0, TZ)
        self.assertEqual(state, view())
        self.assertEqual(status, {'kind': 'busy', 'stale': False, 'line': 'Switchboard is busy'})
        items = menubar.menu(state, status=status, tz=TZ)
        self.assertEqual(items[0], ('line', 'Switchboard is busy'))
        self.assertEqual([x[1]['email'] for x in items if x[0] == 'row'], ['ana@work.example', 'ana@home.example'])
        self.assertNotIn(('do', 'Start', 'start'), items)
        r = menubar.reading(state, status)
        self.assertTrue(r['red'])                                   # still current enough: its red stays

    def test_sixty_seconds_without_a_good_reading_greys_the_bars_and_says_since_when(self):
        w = menubar.Watch()
        w.took(view(), None, 100.0, WALL)
        w.took(None, 'busy', 161.0, WALL + 61)
        state, status = w.view(161.0, TZ)
        self.assertEqual(status, {'kind': 'busy', 'stale': True, 'line': 'Switchboard is busy · not updated since 2:02 PM'})
        r = menubar.reading(state, status)
        self.assertEqual((r['red'], r['stale']), (False, True))
        self.assertEqual(r['label'], 'Switchboard: ana@work.example, weekly 96% · Switchboard is busy · not updated '
                                     'since 2:02 PM')
        fills = [op[-1] for op in menubar.item_ops(r) if op[-1] != 'ink30']
        self.assertEqual(fills, ['ink50'])                          # grey, never red
        rows = [x[1] for x in menubar.menu(state, status=status, tz=TZ) if x[0] == 'row']
        self.assertTrue(all(r['stale'] for r in rows))
        colours = set(op[-1] for row in rows for op in menubar.row_ops(row) if op[0] == 'box')
        self.assertFalse(colours & {'accent', 'red'}, colours)
        pcts = [op[5][0][3] for row in rows for op in menubar.row_ops(row) if op[0] == 'text' and op[6] == 'right']
        self.assertEqual(set(pcts), {'ink3'})

    def test_it_recovers_on_the_next_answer(self):
        w = menubar.Watch()
        w.took(view(), None, 100.0, WALL)
        w.took(None, 'busy', 200.0, WALL + 100)
        self.assertTrue(w.status(200.0)['stale'])
        w.took(view(next='ana@home.example'), None, 215.0, WALL + 115)
        self.assertIsNone(w.status(215.0))
        self.assertEqual(w.state['next'], 'ana@home.example')

    def test_a_page_that_refuses_twice_isnt_running_and_offers_start(self):
        w = menubar.Watch()
        w.took(view(), None, 100.0, WALL)
        w.took(None, 'down', 115.0, WALL + 15)
        self.assertIsNone(w.status(115.0))                          # once: a page restarting is refused for a moment
        w.took(None, 'down', 130.0, WALL + 30)
        status = w.status(130.0, TZ)
        self.assertEqual(status, {'kind': 'down', 'stale': False, 'line': "Switchboard isn't running"})
        items = menubar.menu(w.state, status=status, tz=TZ)
        self.assertEqual(items[:2], [('line', "Switchboard isn't running"), ('do', 'Start', 'start')])
        w.took(None, 'down', 170.0, WALL + 70)
        self.assertEqual(w.status(170.0, TZ)['line'], "Switchboard isn't running · not updated since 2:02 PM")
        w.took(None, 'busy', 185.0, WALL + 85)                      # starting: busy, the count of refusals starts again
        self.assertEqual(w.status(185.0, TZ)['kind'], 'busy')

    def test_with_no_good_state_yet(self):
        w = menubar.Watch()
        w.took(None, 'busy', 1.0, WALL)
        state, status = w.view(1.0)
        self.assertEqual(menubar.menu(state, status=status), [('line', 'Switchboard is busy'), ('separator',),
                                                              ('do', 'Quit', 'quit')])
        self.assertEqual(menubar.reading(state, status)['label'], 'Switchboard is busy')
        w = menubar.Watch()
        w.took(None, 'down', 1.0, WALL)
        w.took(None, 'down', 2.0, WALL)
        state, status = w.view(2.0)
        self.assertEqual(menubar.menu(state, status=status), menubar.menu(None))      # the same as with no state: Start
        self.assertEqual(menubar.reading(state, status)['label'], "Switchboard isn't running")

    def test_a_page_that_answers_with_an_old_state_of_its_own_is_old_too(self):
        w = menubar.Watch()
        w.took(dict(view(), answer={'age': 90.0, 'stale': True}), None, 100.0, WALL)
        status = w.status(100.0, TZ)
        self.assertEqual(status, {'kind': 'busy', 'stale': True, 'line': 'Not updated since 2:01 PM'})

    def test_an_answer_that_cannot_be_read_says_so(self):
        w = menubar.Watch()
        w.took(view(), None, 100.0, WALL)
        w.took(None, 'unreadable', 115.0, WALL + 15)
        self.assertEqual(w.status(115.0, TZ), {'kind': 'busy', 'stale': False, 'line': "Switchboard's answer can't be read"})

    def test_the_wall_clock_jumping_changes_only_the_words(self):
        w = menubar.Watch()
        w.took(view(), None, 100.0, WALL)
        w.took(None, 'busy', 110.0, WALL - 3600)                    # the clock set back an hour: 10 s, not -1 h
        self.assertFalse(w.status(110.0)['stale'])
        w.took(None, 'busy', 130.0, WALL + 7200)                    # and forward two hours: 30 s, not 2 h
        self.assertFalse(w.status(130.0)['stale'])


class ItsOwnLog(unittest.TestCase):
    """menubar.log, the menu bar job's output, is rotated by the menu bar itself (core.rotate_own_output)."""

    def test_its_log_is_rotated_only_when_it_is_its_own_output_and_never_raises(self):
        seen = []
        with mock.patch.object(menubar.core, 'rotate_own_output', lambda path=None, **k: seen.append(path) or True):
            self.assertTrue(menubar.rotate_log())
        self.assertTrue(seen[0].endswith(os.path.join('', 'menubar.log')))
        with mock.patch.object(menubar.core, 'rotate_own_output', mock.Mock(side_effect=OSError('gone'))):
            self.assertFalse(menubar.rotate_log())


# ---------- reading the page: why a reading failed ----------

class Quiet(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        mode = self.server.mode
        if mode == 'hang':
            self.server.hold.wait(30)
        code, body = (503, {'error': 'busy', 'busy': True}) if mode == 'busy' else (200, self.server.answer)
        data = json.dumps(body).encode() if mode != 'garbage' else b'not json'
        try:
            self.send_response(code)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            pass


class StandIn(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        ThreadingHTTPServer.__init__(self, ('127.0.0.1', 0), Quiet)
        self.mode, self.answer, self.hold = 'ok', view(), threading.Event()
        threading.Thread(target=self.serve_forever, daemon=True).start()


class PageSandbox(unittest.TestCase):
    """A stand-in page on a free port, and a throwaway data folder."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-menubar-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = mock.patch.dict(os.environ, {'SWITCHBOARD_HOME': os.path.join(self.tmp, 'data'),
                                           'HOME': os.path.join(self.tmp, 'home')})
        env.start()
        self.addCleanup(env.stop)
        self.page = StandIn()
        self.assertNotIn(self.page.server_address[1], REAL_PAGES)
        self.addCleanup(self.page.server_close)
        self.addCleanup(self.page.shutdown)
        self.addCleanup(self.page.hold.set)
        os.environ['SWITCHBOARD_PORT'] = str(self.page.server_address[1])

    def read(self, timeout=5):
        why = []
        return menubar.get_state(timeout=timeout, why=why), (why[0] if why else None)


class ThePage(PageSandbox):
    def test_each_failure_has_its_reason(self):
        self.assertEqual(self.read(), (view(), None))
        self.page.mode = 'busy'
        self.assertEqual(self.read(), (None, 'busy'))
        self.page.mode = 'garbage'
        self.assertEqual(self.read(), (None, 'unreadable'))
        self.page.mode = 'hang'
        t0 = time.monotonic()
        self.assertEqual(self.read(timeout=0.5), (None, 'busy'))
        self.assertLess(time.monotonic() - t0, 10)
        s = socket.socket()
        s.bind(('127.0.0.1', 0))
        closed = s.getsockname()[1]
        s.close()                                                   # nothing listens there now
        os.environ['SWITCHBOARD_PORT'] = str(closed)
        self.assertEqual(self.read(), (None, 'down'))
        self.assertIsNone(menubar.get_state(timeout=1))             # get_state too: nothing

    def test_a_page_that_stops_answering_goes_grey_in_60_s_and_comes_back_on_the_next_answer(self):
        """A fake state server that stops answering."""
        w, mono = menubar.Watch(), 1000.0
        w.took(*self.read(), mono, WALL)
        self.page.mode = 'hang'
        for step in (15, 15, 15):                                   # 45 s: busy, the numbers still current
            mono += step
            w.took(*self.read(timeout=0.3), mono, WALL)
            self.assertEqual(w.status(mono)['kind'], 'busy')
            self.assertFalse(w.status(mono)['stale'])
            self.assertTrue(menubar.reading(*w.view(mono))['red'])
        mono += 16                                                  # 61 s
        w.took(*self.read(timeout=0.3), mono, WALL)
        self.assertTrue(w.status(mono)['stale'])
        self.assertTrue(menubar.reading(*w.view(mono))['stale'])
        self.page.hold.set()
        self.page.mode = 'ok'
        mono += 15
        w.took(*self.read(), mono, WALL)
        self.assertIsNone(w.status(mono))
        self.assertFalse(menubar.reading(*w.view(mono))['stale'])


# ---------- the real menu bar class keeps the last good state ----------

def stand_in_pyobjc(calls):
    """objc, AppKit, Foundation and PyObjCTools as plain Python, just enough for menubar._ui() to make its classes."""
    def module(name, **names):
        m = types.ModuleType(name)
        m.__dict__.update(names)

        def missing(attr):
            if attr.startswith('__'):
                raise AttributeError(attr)
            return mock.MagicMock(name='%s.%s' % (name, attr))
        m.__getattr__ = missing
        return m

    class NSObject(object):
        @classmethod
        def alloc(cls):
            return cls.__new__(cls)

        def init(self):
            return self

    class NSView(NSObject):
        def initWithFrame_(self, frame):
            return self

    helper = module('PyObjCTools.AppHelper', callAfter=lambda f, *a: calls.put((f, a)), installMachInterrupt=lambda: None)
    return {'objc': module('objc', super=super, python_method=lambda f: f),
            'AppKit': module('AppKit', NSView=NSView), 'Foundation': module('Foundation', NSObject=NSObject),
            'PyObjCTools': module('PyObjCTools', AppHelper=helper), 'PyObjCTools.AppHelper': helper}


class TheMenuBar(PageSandbox):
    def setUp(self):
        super().setUp()
        self.calls = queue.Queue()
        p = mock.patch.dict(sys.modules, stand_in_pyobjc(self.calls))
        p.start()
        self.addCleanup(p.stop)
        menubar._UI.clear()
        self.addCleanup(menubar._UI.clear)
        self.bar = menubar._ui()['MenuBar'].alloc().init()
        self.bar.status, self.bar.menu = mock.MagicMock(), mock.MagicMock()
        self.drawn = []
        self.bar.fill = lambda items: self.drawn.append(items)
        self.bar.show_item = lambda r: self.drawn.append(r)

    def reading(self):
        self.bar.read()
        f, args = self.calls.get(timeout=30)
        f(*args)

    def test_a_failed_reading_keeps_the_last_good_state_on_screen(self):
        self.reading()
        self.assertEqual(self.bar.state, view())
        self.page.mode = 'busy'
        self.reading()
        self.assertEqual(self.bar.state, view())                    # kept
        menu_items = self.drawn[-1]
        self.assertEqual(menu_items[0], ('line', 'Switchboard is busy'))
        self.assertIn('row', [x[0] for x in menu_items])


if __name__ == '__main__':
    unittest.main()
