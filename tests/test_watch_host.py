import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The loop's host (engine.AutoHost) beyond the heartbeat: a loop that beats but finishes no pass is replaced (and not
# in the normal slow cases), a heartbeat that never moves stops the restarts after three in thirty minutes, a loop
# that ended starts again within seconds, a Python that is gone is replaced by one that is there, and a stuck loop
# that is not ours and holds the lease is ended. Popen and killpg are stand-ins (tests/test_app_engine.py); no process
# is started and no signal is sent.
import os
import signal
import subprocess
import sys
import threading
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import core, engine
from test_app_engine import Base, Clock, FakeProc


class HostBase(Base):
    def setUp(self):
        Base.setUp(self)
        self.clock = Clock()
        self.host = engine.AutoHost(monotonic=lambda: self.clock.mono, wall=lambda: self.clock.wall)
        self.procs, self.killed = [], []
        self.host.popen = self.popen
        self.host.killpg = self.killpg
        self.host.looked_for_stale = True
        self.fake.auto['enabled'] = True
        self.logged = []
        p = mock.patch.object(engine, 'log', side_effect=self.logged.append)
        p.start()
        self.addCleanup(p.stop)

    def popen(self, args, **kw):
        p = FakeProc(args, **kw)
        self.procs.append(p)
        return p

    def killpg(self, pid, sig):
        self.killed.append((pid, sig))
        proc = next(p for p in self.procs if p.pid == pid)
        if sig == signal.SIGKILL or (sig == signal.SIGTERM and proc.ends_on_term):
            proc.returncode = -sig

    def beat(self, stage='sleeping', stuck=0.0, last=None, nxt=None):
        """A heartbeat of the running loop: beating (stuck 0) with its last finished pass at `last` (wall)."""
        self.fake.status.update(pid=self.procs[-1].pid, stage=stage, stuck_for_s=stuck, last_pass_at=last,
                                next_pass_at=nxt)

    def run_for(self, seconds, every=60, **beat):
        """`seconds` of awake time, a look each minute, the loop beating all along."""
        for _ in range(int(seconds // every)):
            self.clock.tick(every)
            self.beat(**beat)
            self.host.ensure()


class NoPassIsAFailure(HostBase):
    def test_a_loop_that_beats_but_finishes_no_pass_is_replaced_and_said(self):
        self.host.ensure()
        self.clock.tick(30)
        last = self.clock.wall
        self.beat(last=last, nxt=last + 60)                 # one pass, then every pass fails (it still beats)
        self.host.ensure()
        self.run_for(engine.AutoHost.NO_PASS + 60 - 60, last=last, nxt=last + 60)
        self.assertEqual(len(self.procs), 1)                # 12 min past the planned pass: not yet
        self.run_for(120, last=last, nxt=last + 60)
        self.assertEqual(self.killed[0], (self.procs[0].pid, signal.SIGTERM))
        self.assertEqual(len(self.procs), 2)                # a fresh one at once
        self.assertTrue(any('no decision' in x for x in self.logged), self.logged)
        self.assertIn('made no decision', self.host.status()['restarted'])
        events = [e['text'] for e in core.load()['events']]
        self.assertTrue(any(t.startswith('Restarted automatic switching (it had made no decision for') for t in events),
                        events)

    def test_not_while_it_waits_out_the_pause_it_planned(self):
        self.host.ensure()
        self.clock.tick(30)
        last = self.clock.wall
        self.run_for(engine.AutoHost.NO_PASS + 590, last=last, nxt=last + 600)  # every account is out: 10 min apart
        self.assertEqual(self.killed, [])

    def test_not_while_auto_switch_is_off(self):
        self.fake.hold('ana@work.example')                 # an account kept: the loop reads with auto-switch off
        self.fake.auto['enabled'] = False
        self.host.ensure()
        self.run_for(3 * engine.AutoHost.NO_PASS, last=self.clock.wall)
        self.assertEqual(self.killed, [])

    def test_not_while_a_switch_is_being_written(self):
        self.host.ensure()
        last = self.clock.wall
        self.run_for(2 * engine.AutoHost.NO_PASS, stage='switching', last=last)
        self.assertEqual(self.killed, [])

    def test_not_for_a_big_first_pass_and_a_sleep_never_counts(self):
        self.host.ensure()
        self.run_for(engine.AutoHost.NO_PASS - 60)          # the first pass reads every account: no pass yet
        self.assertEqual(self.killed, [])
        last = self.clock.wall
        self.beat(last=last, nxt=last + 60)
        self.host.ensure()
        self.clock.tick(5, wall=5 * 3600)                   # five hours asleep
        self.beat(last=last, nxt=last + 60)
        self.host.ensure()
        self.run_for(engine.AutoHost.NO_PASS, last=last, nxt=last + 60)
        self.assertEqual(self.killed, [])                   # counted from the wake, not from the last pass


class AHeartbeatThatNeverMoves(HostBase):
    def test_three_restarts_in_thirty_minutes_then_it_stops_restarting_and_says_why(self):
        self.fake.status.update(pid=None, stage=None, stuck_for_s=None, last_pass_at=None)
        self.host.ensure()
        for _ in range(16):                                 # the file is never written: nothing ever moves
            self.clock.tick(60)
            self.host.ensure()
        self.assertEqual(len(self.procs), 4)                # three restarts, at 4, 8 and 12 minutes; none at 16
        problem = self.host.status()['problem']
        self.assertIn('stopped restarting', problem)
        self.assertEqual(len([x for x in self.logged if 'stopped restarting' in x]), 1)
        for _ in range(20):
            self.clock.tick(60)
            self.host.ensure()
        self.assertEqual(len(self.procs), 4)

    def test_a_heartbeat_that_moves_again_lets_the_watchdog_work_again(self):
        self.fake.status.update(pid=None, stage=None, stuck_for_s=None, last_pass_at=None)
        self.host.ensure()
        for _ in range(16):
            self.clock.tick(60)
            self.host.ensure()
        self.assertTrue(self.host.gave_up)
        self.beat(last=self.clock.wall)
        self.host.ensure()
        self.assertIsNone(self.host.gave_up)
        self.assertNotIn('stopped restarting', self.host.status()['problem'] or '')


class QuickRestart(HostBase):
    def test_a_first_and_second_death_start_again_within_seconds(self):
        self.host.ensure()
        self.procs[-1].returncode = 1
        self.host.revive()                                  # between ticks
        self.assertEqual(len(self.procs), 1)
        self.assertAlmostEqual(self.host.due_in(), engine.AutoHost.PAUSES[0])
        self.assertLessEqual(engine.AutoHost.PAUSES[0], 5)
        self.clock.tick(engine.AutoHost.PAUSES[0])
        self.host.revive()
        self.assertEqual(len(self.procs), 2)
        self.procs[-1].returncode = 1
        self.clock.tick(1)
        self.host.revive()
        self.assertLessEqual(self.host.due_in(), 15)
        self.clock.tick(engine.AutoHost.PAUSES[1])
        self.host.revive()
        self.assertEqual(len(self.procs), 3)
        self.assertEqual(engine.AutoHost.PAUSES[-1], 900)   # the long pause is only for a loop that keeps dying

    def test_revive_starts_nothing_for_a_loop_stopped_on_purpose_or_one_that_runs(self):
        self.host.ensure()
        self.host.revive()
        self.assertEqual(len(self.procs), 1)
        self.assertIsNone(self.host.due_in())
        self.host.stop(wait=0)
        self.clock.tick(3600)
        self.host.revive()
        self.assertEqual(len(self.procs), 1)

    def test_a_loop_that_ends_wakes_the_page_at_once(self):
        woke = threading.Event()
        done = threading.Event()

        class Ends(FakeProc):
            def wait(self, timeout=None):
                if timeout is None:
                    done.wait(10)
                    self.returncode = 1
                    return 1
                return FakeProc.wait(self, timeout)
        self.host.popen = lambda args, **kw: self.procs.append(Ends(args, **kw)) or self.procs[-1]
        self.host.on_exit = woke.set
        self.host.ensure()
        self.assertFalse(woke.is_set())
        done.set()
        self.assertTrue(woke.wait(10))


class APythonThatIsGone(HostBase):
    def test_the_launchers_python_then_the_recorded_ones(self):
        here = sys.executable
        gone = os.path.join(self.data, 'gone', 'python3')
        self.host.candidates = lambda: [gone, here]
        self.host.ensure()
        self.assertEqual(self.procs[-1].args[0], here)
        self.assertTrue(any('gone' in x for x in self.logged), self.logged)

    def test_candidates_come_from_this_python_the_launcher_and_the_record(self):
        os.makedirs(os.path.join(self.data, 'bin'), exist_ok=True)
        with open(os.path.join(self.data, 'bin', 'switchboard-page'), 'w') as f:
            f.write('#!/bin/sh\nAPP_PY="/Applications/Switchboard.app/Contents/MacOS/python"\nif true; then :; fi\n'
                    'PY="/opt/example/bin/python3"\nexec "$PY" serve\n')
        core.write_json(core.data_path(os.path.join('runtime', 'python.json')),
                        {'python': '/usr/local/example/python3', 'page_python': '/opt/example/bin/python3'})
        self.assertEqual(self.host.candidates(), [sys.executable, '/Applications/Switchboard.app/Contents/MacOS/python',
                                                  '/opt/example/bin/python3', '/usr/local/example/python3'])

    def test_with_none_left_it_says_so_and_waits_its_pause_instead_of_trying_every_tick(self):
        self.host.candidates = lambda: [os.path.join(self.data, 'gone', 'python3')]
        self.host.ensure()
        self.assertEqual(self.procs, [])
        self.assertTrue(self.host.no_python)
        self.assertGreater(self.host.due_in(), 0)
        self.assertIn('Python', self.host.status()['problem'])

    def test_a_python_that_cannot_start_is_passed_over_and_a_failure_waits_its_pause(self):
        calls = []

        def popen(args, **kw):
            calls.append(args[0])
            raise OSError(8, 'Exec format error')
        self.host.popen = popen
        self.host.candidates = lambda: [sys.executable, sys.executable + '-also']
        with mock.patch.object(engine.os.path, 'isfile', lambda p: True), \
                mock.patch.object(engine.os, 'access', lambda p, m: True):
            self.host.ensure()
            self.assertEqual(calls, [sys.executable, sys.executable + '-also'])
            self.host.ensure()
            self.assertEqual(len(calls), 2)                 # not again at the next tick: its pause first
            self.clock.tick(engine.AutoHost.PAUSES[0])
            self.host.ensure()
            self.assertEqual(len(calls), 4)


class AStuckStrangerHoldsTheLease(HostBase):
    def test_a_third_busy_exit_in_a_row_ends_the_stuck_stranger_once(self):
        ended = []
        self.host.stranger_watchdog = lambda wait_s: ended.append(wait_s) or 'Restarted automatic switching: it was stuck'
        for n in range(3):
            self.host.ensure()
            self.procs[-1].returncode = 5                   # "Automatic switching already runs"
            self.clock.tick(1)
            self.host.ensure()
            self.clock.tick(engine.AutoHost.PAUSES[-1])
            if n < 2:
                self.assertEqual(ended, [])
        self.host.ensure()
        self.assertEqual(ended, [engine.AutoHost.TERM_WAIT])
        self.assertTrue(any('lease' in x for x in self.logged), self.logged)

    def test_other_exits_never_end_anyone(self):
        ended = []
        self.host.stranger_watchdog = lambda wait_s: ended.append(wait_s)
        for code in (5, 5, 1, 5, 5, 1):
            self.host.ensure()
            self.procs[-1].returncode = code
            self.clock.tick(1)
            self.host.ensure()
            self.clock.tick(engine.AutoHost.PAUSES[-1])
        self.assertEqual(ended, [])


if __name__ == '__main__':
    unittest.main()
