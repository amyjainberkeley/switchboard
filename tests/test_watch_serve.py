import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# serve() and the watchers: the page's start is counted and its stop mark cleared; its watchdog is armed only when
# launchd runs it; when another program holds its port under launchd it runs everything but its page (the loop that
# reads and switches, the notes), says so once, and opens the page by itself when the port is free. A terminal run
# with the port taken still ends with the plain line. Every outside step of serve() is a stand-in; ports are free
# ones of 127.0.0.1; no notification is shown (NOTIFY_RUN records), no process exits.
import os
import socket
import sys
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import connect, core, engine, lanes, linkapp, notify_hooks, server, statusline, watch
from test_server_robust import Sandbox


class Serve(Sandbox):
    def setUp(self):
        Sandbox.setUp(self)
        self.looped = []
        for name, value in (('loop', lambda *a, **k: self.looped.append(1)), ('refresh_link_status', lambda **k: None),
                            ('consent_refresh', lambda **k: None), ('load_week', lambda: None),
                            ('usage_view', lambda fresh=False: None), ('resolve_journal', lambda: None),
                            ('owner_map_now', lambda: None), ('statusline_status', lambda: {'on': False}),
                            ('statusline_every_minute', lambda: None), ('start_session_hooks', lambda: None),
                            ('more_files', lambda want=4096: 4096)):
            self.patch(server, name, value)
        self.patch(server.signal, 'signal', lambda *a: None)
        self.patch(connect, 'sweep', lambda: None)
        self.patch(linkapp, 'announce_port', lambda: None)
        self.patch(lanes, 'resume', lambda: None)
        self.patch(statusline, 'repair', lambda: [])
        self.patch(notify_hooks, 'repair', lambda: [])
        self.patch(notify_hooks, 'status', lambda: {'in': False})
        self.patch(engine.HOST, 'on_exit', None, create=True)
        self.armed = []
        self.patch(server, 'watch_page', lambda: self.armed.append(1))
        self.made = []
        real = server.ThreadingHTTPServer
        made = self.made

        class Recorded(real):           # every server serve() starts is closed after the test
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                made.append(self)
        self.patch(server, 'ThreadingHTTPServer', Recorded)
        self.addCleanup(lambda: [(s.shutdown(), s.server_close()) for s in made])

    def free_port(self):
        s = socket.socket()
        s.bind(('127.0.0.1', 0))
        n = s.getsockname()[1]
        s.close()
        return n

    def holder(self):
        s = socket.socket()
        s.bind(('127.0.0.1', 0))
        s.listen(4)
        return s

    def test_a_start_is_counted_and_a_stop_mark_cleared(self):
        os.environ['SWITCHBOARD_PORT'] = str(self.free_port())
        watch.note_stopped()
        self.assertIsNotNone(watch.stopped())
        with mock.patch.object(watch, 'background', lambda: False):
            server.serve()
        self.assertEqual(self.looped, [1])
        self.assertEqual(watch.starts_within(60), 1)
        self.assertIsNone(watch.stopped())
        self.assertEqual(self.armed, [])                    # a page run by hand is never ended by its own watchdog

    def test_under_launchd_the_watchdog_is_armed(self):
        os.environ['SWITCHBOARD_PORT'] = str(self.free_port())
        with mock.patch.object(watch, 'background', lambda: True):
            server.serve()
        self.assertEqual(self.armed, [1])

    def test_a_stop_by_sigterm_leaves_the_mark_the_menu_bar_reads(self):
        self.patch(lanes, 'stop', lambda: None)
        self.patch(engine.HOST, 'stop', lambda wait=45.0: None)
        with self.assertRaises(SystemExit):
            server.on_sigterm()
        self.assertIsNotNone(watch.stopped())

    def test_under_launchd_a_taken_port_keeps_everything_but_the_page_and_opens_it_when_free(self):
        holder = self.holder()
        n = holder.getsockname()[1]
        os.environ['SWITCHBOARD_PORT'] = str(n)
        self.patch(server, 'PORT_AGAIN', 0.2)
        with mock.patch.object(watch, 'background', lambda: True):
            server.serve()                                  # never raises: it runs the loop all the same
        self.assertEqual(self.looped, [1])
        self.assertEqual(watch.port_mark()['port'], n)
        notes = [c for c in self.shown if any('in use by another program' in str(x) for x in c)]
        self.assertEqual(len(notes), 1, self.shown)
        body = ' '.join(str(x) for x in notes[0])
        self.assertIn('Auto-switch still works', body)
        self.assertIn('switchboard update --port', body)
        self.assertTrue(any('Port %d is in use by another program' % n in x for x in self.log_lines()))
        holder.close()                                      # the other program lets the port go
        for _ in range(100):
            if watch.port_mark() is None:
                break
            time.sleep(0.05)
        self.assertIsNone(watch.port_mark())
        self.assertTrue(server.page_answers(n, timeout=5) or self.made)
        self.assertTrue(any(s.server_address[1] == n for s in self.made))

    def test_a_port_taken_again_after_a_restart_is_not_said_twice(self):
        holder = self.holder()
        self.addCleanup(holder.close)
        os.environ['SWITCHBOARD_PORT'] = str(holder.getsockname()[1])
        self.patch(server, 'PORT_AGAIN', 3600.0)
        with mock.patch.object(watch, 'background', lambda: True):
            server.serve()
            server.serve()                                  # the page started again, the port still taken
        notes = [c for c in self.shown if any('in use by another program' in str(x) for x in c)]
        self.assertEqual(len(notes), 1)

    def test_in_a_terminal_a_taken_port_is_still_the_plain_line(self):
        holder = self.holder()
        self.addCleanup(holder.close)
        os.environ['SWITCHBOARD_PORT'] = str(holder.getsockname()[1])
        with mock.patch.object(watch, 'background', lambda: False):
            with self.assertRaises(core.SwitchError) as said:
                server.serve()
        self.assertIn('is in use by another program', str(said.exception))
        self.assertEqual(self.looped, [])
        self.assertIsNone(watch.port_mark())


class TheWatchdogThread(Sandbox):
    def test_watch_page_starts_one_daemon_thread_on_the_pages_own_stage_and_snapshot(self):
        made = []

        class Fake(object):
            def __init__(self, **kw):
                made.append(kw)

            def run(self):
                pass
        self.patch(watch, 'PageWatch', Fake)
        t = server.watch_page()
        t.join(5)
        self.assertTrue(t.daemon)
        kw = made[0]
        server.stage('reading the accounts')
        self.assertEqual(kw['stage']()[0], 'reading the accounts')
        self.assertIsNone(kw['building']())
        self.assertIs(kw['switching'], watch.switch_in_progress)


if __name__ == '__main__':
    unittest.main()
