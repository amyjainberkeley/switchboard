import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The page's loop (server.loop) is never ended by one call that fails: every step is caught and said in the log as its
# neighbours are, and the next pass runs. Between ticks it starts the loop that reads the accounts again as soon as its
# pause allows. Every outside step is a stand-in; no process is started.
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import engine, keep, server
from test_server_robust import Clock, Sandbox


class Boom(Exception):
    pass


def boom(*a, **k):
    raise Boom('made up')


class LoopBase(Sandbox):
    """The page's loop with every outside step a stand-in, and a wait that moves the clock 10 s."""

    def setUp(self):
        Sandbox.setUp(self)
        self.mono = Clock(5000.0)
        self.patch(server, 'monotonic', self.mono)
        self.ticks = []
        self.patch(server, 'tick', lambda: self.ticks.append(self.mono.t) or {'rows': None})
        for name, value in (('keep_tick', lambda looked: None), ('mac_tick', lambda now=None: None),
                            ('disk_tick', lambda run=None: False), ('load_per_core', lambda: 0.0),
                            ('decide_tick', lambda: None), ('chats_tick', lambda: None),
                            ('readings_pass', lambda: None), ('refresh_link_status', lambda **k: None),
                            ('consent_refresh', lambda **k: None)):
            self.patch(server, name, value)
        self.patch(server.artifacts, 'published_tick', lambda: None)
        self.patch(keep, 'login_changed', lambda: False)
        self.patch(keep, 'due', lambda now=None: False)
        self.patch(keep, 'wait_for', lambda default, now=None: 0.0)
        self.patch(engine.HOST, 'ensure', lambda: None)
        self.patch(server.claudecode, 'CAP', {'running': True, 'at': 0})
        self.patch(server.claudecode, 'HEALTH', {'running': True, 'at': 0})

        def wait(timeout=None):
            self.mono.t += 10               # under a tick: the next pass asks keep.due() whether to read now
            return False
        self.patch(server.WAKE, 'wait', wait)

    def run_loop(self):
        server.loop(ticks=3)            # raises if one call ends it
        return self.log_lines()


class OneBadCall(LoopBase):
    def test_each_step_that_was_not_caught_is_caught_and_said(self):
        for target, name in ((server, 'ease_tick'), (keep, 'due'), (keep, 'wait_for'), (server, 'clock_step'),
                             (server, '_pass_said'), (server, 'keep_tick'), (server, 'providers_tick')):
            with self.subTest(step=name), mock.patch.object(target, name, boom):
                self.ticks[:] = []
                said = self.run_loop()
                self.assertTrue(self.ticks, name)                       # the accounts were still read
                self.assertTrue(any('made up' in x for x in said), said)

    def test_a_host_that_cannot_say_when_or_start_again_never_ends_the_loop(self):
        # The host's methods are looked up inside the caught call, not before it: a host without them (an older one,
        # or a stand-in) costs a line in the log, never the loop, and the wait is the usual one.
        class Bare(object):
            def ensure(self):
                pass
        self.patch(engine, 'HOST', Bare())
        waits = []
        self.patch(server.WAKE, 'wait', lambda timeout=None: waits.append(timeout) or self.mono.__setattr__(
            't', self.mono.t + 10))
        said = self.run_loop()
        self.assertTrue(self.ticks)
        self.assertTrue(any('starting the loop again failed' in x for x in said), said)
        self.assertEqual(waits[0], 0.0)                 # keep.wait_for's answer here, not the fallback tick

    def test_a_thread_that_cannot_start_never_ends_the_loop(self):
        self.patch(server.claudecode, 'CAP', {'running': False, 'at': 0})
        self.patch(server.claudecode, 'HEALTH', {'running': False, 'at': 0})
        self.patch(server.core, 'DEMO', False)

        class NoThreads(object):
            def __init__(self, *a, **k):
                pass

            def start(self):
                raise RuntimeError("can't start new thread")
        with mock.patch.object(server.threading, 'Thread', NoThreads):
            said = self.run_loop()
        self.assertEqual(len(self.ticks), 1)
        self.assertTrue(any("can't start new thread" in x for x in said), said)


class BetweenTicks(LoopBase):
    def test_every_pass_starts_a_loop_that_ended_and_waits_no_longer_than_its_pause(self):
        revived, waits = [], []
        self.patch(engine.HOST, 'revive', lambda: revived.append(1))
        self.patch(engine.HOST, 'due_in', lambda: 2.0)

        def wait(timeout=None):
            waits.append(timeout)
            self.mono.t += 10
            return False
        self.patch(server.WAKE, 'wait', wait)
        self.patch(keep, 'wait_for', lambda default, now=None: default)
        server.loop(ticks=3)
        self.assertEqual(len(revived), 3)                   # every pass, not only the ones that read the accounts
        self.assertEqual(waits, [2.0, 2.0])                 # the pause, not the usual 20 s

    def test_with_no_restart_due_the_wait_is_the_usual_one(self):
        waits = []
        self.patch(engine.HOST, 'due_in', lambda: None)

        def wait(timeout=None):
            waits.append(timeout)
            return False
        self.patch(server.WAKE, 'wait', wait)
        self.patch(keep, 'wait_for', lambda default, now=None: default)
        server.loop(ticks=2)
        self.assertEqual(waits, [server.MAC_TICK])


class NoPythonForTheLoop(LoopBase):
    def test_under_launchd_the_page_ends_so_launchd_starts_it_on_the_launchers_python(self):
        self.patch(engine.HOST, 'no_python', True, create=True)
        with mock.patch.object(server.watch, 'background', lambda: True):
            with self.assertRaises(SystemExit) as ended:
                server.loop(ticks=3)
        self.assertEqual(ended.exception.code, server.watch.EXIT_PYTHON)
        self.assertTrue(any('Python' in x for x in self.log_lines()))

    def test_run_by_hand_or_after_too_many_starts_it_stays(self):
        self.patch(engine.HOST, 'no_python', True, create=True)
        with mock.patch.object(server.watch, 'background', lambda: False):
            server.loop(ticks=2)                            # nothing would start it again
        for _ in range(5):
            server.watch.note_start()
        with mock.patch.object(server.watch, 'background', lambda: True):
            server.loop(ticks=2)                            # it has started 5 times in ten minutes already


if __name__ == '__main__':
    unittest.main()
