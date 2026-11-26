import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The page watches itself (watch.PageWatch): a page whose loop or state build has not moved for ten minutes of awake
# time exits so launchd starts a fresh one, and it does NOT in the normal slow cases (a busy Mac, a long step, a wake,
# a switch being written, a big first reading), nor more than three times an hour. The page's starts are counted.
# Clocks are stand-ins; exit is a stand-in that only records (no process ever exits); no launchd, no notification.
import fcntl
import json
import os
import shutil
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import core, watch


class Clock(object):
    def __init__(self):
        self.mono, self.wall = 5000.0, 1.8e9

    def tick(self, s, wall=None):
        self.mono += s
        self.wall += s if wall is None else wall


class Base(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(core.data_dir(), ignore_errors=True)
        os.makedirs(core.data_dir(), exist_ok=True)
        self.clock = Clock()
        self.logged = []
        p = mock.patch.object(watch, 'log', side_effect=self.logged.append)
        p.start()
        self.addCleanup(p.stop)


class PageWatchBase(Base):
    def setUp(self):
        Base.setUp(self)
        self.stage = ['reading the accounts', self.clock.mono]
        self.build = [None]
        self.switch = [False]
        self.exits = []
        self.w = watch.PageWatch(stage=lambda: tuple(self.stage), building=lambda: self.build[0],
                                 switching=lambda: self.switch[0], mono=lambda: self.clock.mono,
                                 wall=lambda: self.clock.wall, exit=self.exits.append)

    def step(self, name=None):
        """The loop moves on to its next step now."""
        self.stage[:] = [name or self.stage[0], self.clock.mono]

    def look(self):
        why = self.w.look()
        if why:
            self.w.fire(why)
        return why


class FiresOnAPageThatHangs(PageWatchBase):
    def test_a_loop_stuck_in_one_step_for_ten_minutes_exits_the_page_once_with_one_line(self):
        self.look()
        self.clock.tick(watch.PAGE_STUCK + 1)
        why = self.look()
        self.assertIn('reading the accounts', why)
        self.assertEqual(self.exits, [watch.EXIT_STUCK])
        said = [x for x in self.logged if 'launchd starts a fresh page' in x]
        self.assertEqual(len(said), 1, self.logged)
        self.assertIn('reading the accounts', said[0])
        with open(core.data_path(watch.PAGE_WATCH)) as f:
            self.assertEqual(len(json.load(f)['fired']), 1)            # counted for the hour, across restarts

    def test_a_state_build_stuck_for_ten_minutes_exits_the_page(self):
        self.build[0] = self.clock.mono
        for _ in range(int(watch.PAGE_STUCK // 60) + 1):
            self.clock.tick(60)
            self.step()                                                 # the loop itself moves on
            self.look()
        self.assertEqual(self.exits, [watch.EXIT_STUCK])

    def test_the_page_starting_up_counts_as_a_step(self):
        self.stage[:] = [None, None]                                    # no step named yet: nothing to measure
        self.clock.tick(3 * watch.PAGE_STUCK)
        self.assertIsNone(self.look())
        self.assertEqual(self.exits, [])


class DoesNotFireInNormalSlowCases(PageWatchBase):
    def test_a_busy_mac_whose_loop_moves_slowly_but_moves(self):
        for _ in range(60):                                             # an hour of four-minute steps
            self.clock.tick(240)
            self.step()
            self.assertIsNone(self.look())
        self.assertEqual(self.exits, [])

    def test_a_long_step_under_the_limit(self):
        self.clock.tick(watch.PAGE_STUCK - 1)                           # a long Keychain answer, a big first reading
        self.assertIsNone(self.look())
        self.build[0] = self.clock.mono
        self.step()
        self.clock.tick(watch.PAGE_STUCK - 1)                           # a first state build that reads a big week
        self.assertIsNone(self.look())
        self.assertEqual(self.exits, [])

    def test_a_sleep_never_counts_and_the_first_minutes_after_a_wake_are_left_alone(self):
        self.look()
        self.clock.tick(5, wall=3 * 3600)                               # three hours asleep, five seconds awake
        self.assertIsNone(self.look())
        self.clock.tick(watch.WAKE_GRACE - 10)                          # the network comes back slowly
        self.assertIsNone(self.look())
        self.clock.tick(watch.PAGE_STUCK - watch.WAKE_GRACE)            # still under the limit from the wake
        self.assertIsNone(self.look())
        self.assertEqual(self.exits, [])
        self.clock.tick(60)                                             # stuck for the full limit after the wake
        self.assertIsNotNone(self.look())
        self.assertEqual(self.exits, [watch.EXIT_STUCK])

    def test_never_while_a_switch_is_being_written(self):
        self.switch[0] = True
        self.look()
        for _ in range(4):
            self.clock.tick(watch.PAGE_STUCK)
            self.assertIsNone(self.look())
        self.assertEqual(self.exits, [])
        self.assertEqual(len([x for x in self.logged if 'a switch is being written' in x]), 1)
        self.switch[0] = False
        self.clock.tick(15)
        self.look()
        self.assertEqual(self.exits, [watch.EXIT_STUCK])

    def test_at_most_three_times_an_hour_then_it_stops_and_says_so(self):
        for n in range(3):
            self.clock.tick(watch.PAGE_STUCK + 1)
            self.look()
            self.step()
        self.assertEqual(len(self.exits), 3)
        self.clock.tick(watch.PAGE_STUCK + 1)
        self.assertIsNone(self.look())
        self.clock.tick(watch.PAGE_STUCK + 1)
        self.assertIsNone(self.look())
        self.assertEqual(len(self.exits), 3)
        said = [x for x in self.logged if 'not again' in x]
        self.assertEqual(len(said), 1, self.logged)
        self.assertEqual(watch.watchdog_fired(now=self.clock.wall), 3)
        self.clock.tick(3600, wall=3600)                                # an hour later it may again
        self.step()
        self.clock.tick(watch.PAGE_STUCK + 1)
        self.look()
        self.assertEqual(len(self.exits), 4)


class SwitchInProgress(Base):
    def test_a_held_switch_lock_or_a_loop_in_its_switching_stage(self):
        self.assertFalse(watch.switch_in_progress())
        f = open(core.data_path('switch.lock'), 'a')
        self.addCleanup(f.close)
        fcntl.flock(f, fcntl.LOCK_EX)
        self.assertTrue(watch.switch_in_progress())
        f.close()
        self.assertFalse(watch.switch_in_progress())
        core.write_json(core.data_path('auto-heartbeat.json'), {'stage': 'switching'})
        self.assertTrue(watch.switch_in_progress())


class Starts(Base):
    def test_more_than_three_starts_in_ten_minutes_are_said(self):
        for _ in range(3):
            watch.note_start(now=self.clock.wall)
            self.clock.tick(60)
        self.assertIsNone(watch.restart_words(now=self.clock.wall))
        watch.note_start(now=self.clock.wall)
        self.assertEqual(watch.restart_words(now=self.clock.wall),
                         'Switchboard restarted 4 times in the last 10 minutes.')
        self.clock.tick(600)
        self.assertIsNone(watch.restart_words(now=self.clock.wall))     # ten quiet minutes: nothing to say

    def test_a_start_dated_ahead_of_the_clock_counts_for_nothing(self):
        for _ in range(5):
            watch.note_start(now=self.clock.wall + 7200)
        self.assertIsNone(watch.restart_words(now=self.clock.wall))

    def test_a_damaged_file_is_a_fresh_count(self):
        with open(core.data_path(watch.STARTS), 'w') as f:
            f.write('{not json')
        watch.note_start(now=self.clock.wall)
        self.assertEqual(watch.starts_within(600, now=self.clock.wall), 1)


if __name__ == '__main__':
    unittest.main()
