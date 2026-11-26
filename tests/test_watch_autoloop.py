import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The automatic loop's own watch on itself: a stage still running after many passes ends the loop (so its host starts
# a fresh one), a few skipped passes never do; a switch cut off part-way is settled by a later pass, not only at a
# start, and not while the Mac settles after a wake; the heartbeat hands the host the wait it planned. Every outside
# thing is fake (tests/fakes_autoswitch.py); nothing is switched for real.
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import core
from switchboard.accounts import autoloop, switcher, tokens
from test_autoswitch_loop import LoopCase, ok


class AStageThatNeverComesBack(LoopCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(tokens.STOPPING.clear)
        self.answers(ok(20.0, 40.0), ok(20.0, 40.0), ok(20.0, 40.0))

    def hang_accounts(self):
        """The account list's read hangs (a stage that does not wait for the Keychain) until the event is set."""
        release, real = self.blocker(), self.engine.accounts

        def accounts(*a, **k):
            if not release.is_set():
                self.clock.block_here(release)
            return real(*a, **k)
        self.engine.accounts = accounts
        return release

    def test_skipped_many_passes_in_a_row_it_ends_the_loop_with_its_own_code(self):
        self.hang_accounts()
        loop = self.loop()
        for _ in range(autoloop.SKIPS_EXIT):
            self.assertFalse(loop.stop.is_set())
            loop.run_pass()
            self.clock.advance(60)
        loop.run_pass()                                     # the tenth skip in a row
        self.assertTrue(loop.stop.is_set())
        self.assertEqual(loop.exit_code, autoloop.EXIT_STUCK_STAGE)
        self.assertIn('accounts has not come back for %d passes' % autoloop.SKIPS_EXIT, self.log_text())

    def test_a_few_skips_then_the_stage_back_never_end_it(self):
        release = self.hang_accounts()
        loop = self.loop()
        for _ in range(autoloop.SKIPS_EXIT - 2):
            loop.run_pass()
            self.clock.advance(60)
        release.set()                                       # the slow read answers at last
        for t in [t for t in loop.hanging.values()]:
            t.join(10)
        for _ in range(2 * autoloop.SKIPS_EXIT):
            loop.run_pass()
            self.clock.advance(60)
        self.assertFalse(loop.stop.is_set())
        self.assertEqual(loop.exit_code, 0)

    def test_a_long_keychain_dialog_never_ends_it(self):
        # A Keychain read that never answers is a dialog only the person can answer: a fresh loop would only ask
        # again (one more dialog). It costs its stage at every pass, never the loop (test_autoswitch_replay too).
        self.engine.block_live = self.blocker()
        loop = self.loop()
        for _ in range(3 * autoloop.SKIPS_EXIT):
            loop.run_pass()
            self.clock.advance(60)
        self.assertFalse(loop.stop.is_set())
        self.assertEqual(loop.exit_code, 0)
        self.assertIn('live skipped: the last one is still running', self.log_text())

    def test_the_number_of_passes_is_minutes_not_seconds(self):
        self.assertGreaterEqual(autoloop.SKIPS_EXIT, 5)


class ACutOffSwitchSettledLater(LoopCase):
    def setUp(self):
        super().setUp()
        self.answers(ok(20.0, 40.0), ok(20.0, 40.0), ok(20.0, 40.0))
        self.settled = []

        def resolve():
            self.settled.append(self.clock.mono())
            try:
                os.remove(core.data_path(autoloop.JOURNAL))
            except OSError:
                pass
            return 'Finished a switch that was cut off.'
        self.engine.resolve_journal = resolve

    def journal(self):
        core.write_json(core.data_path(autoloop.JOURNAL), {'to_id': 'a2'})

    def test_it_is_the_engines_own_file(self):
        self.assertEqual(autoloop.JOURNAL, switcher.JOURNAL)

    def test_a_pass_that_finds_one_settles_it_with_the_function_the_start_uses(self):
        loop = self.loop()
        loop.run_pass()
        self.assertEqual(self.settled, [])                  # nothing to settle: nothing asked of the Keychain
        self.journal()                                      # the start could not check it (the Keychain was locked)
        self.clock.advance(60)
        loop.run_pass()
        self.assertEqual(len(self.settled), 1)
        self.assertIn('Finished a switch that was cut off.', self.log_text())

    def test_one_that_cannot_be_settled_yet_is_tried_again_after_a_while_not_every_pass(self):
        self.engine.resolve_journal = lambda: self.settled.append(self.clock.mono()) or \
            'A switch was cut off; Switchboard will settle it as soon as the Keychain answers.'
        self.journal()
        loop = self.loop()
        for _ in range(10):
            loop.run_pass()
            self.clock.advance(60)
        self.assertEqual(len(self.settled), 2)              # at 0 and after JOURNAL_AGAIN, not ten times
        self.assertGreaterEqual(self.settled[1] - self.settled[0], autoloop.JOURNAL_AGAIN)

    def test_not_while_the_mac_settles_after_a_wake(self):
        self.journal()
        loop = self.loop()
        loop.waking = True                                  # just woke: the network and the Keychain come back slowly
        loop.run_pass()
        self.assertEqual(self.settled, [])


class TheHeartbeatHandsOverItsPlan(LoopCase):
    def test_status_carries_the_next_pass_it_planned(self):
        self.answers(ok(20.0, 40.0), ok(20.0, 40.0), ok(20.0, 40.0))
        loop = self.loop()
        res = loop.run_pass()
        st = autoloop.status(self.clock)
        self.assertAlmostEqual(st['next_pass_at'] - st['last_pass_at'], res.delay, delta=1.0)


if __name__ == '__main__':
    unittest.main()
