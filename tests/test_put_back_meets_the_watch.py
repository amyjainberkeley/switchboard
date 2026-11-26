import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The put-back stage beside the loop's watch on itself and the page's own watchdog. A put-back that never comes back
# waits for the Keychain, so it never ends the loop (SKIPS_EXIT); a look from the loop's sleep that fails is caught and
# never ends the loop; a look is not a finished pass ("last decided" and the host's "no pass" count passes only); the
# page's own watchdog waits while a put-back is tried, as it waits for a switch; and in one pass with a switch cut off
# part-way and Claude Code's login missing, at most one of the two writes the login. Stand-ins only (fakes_autoswitch,
# fakes_accounts); made-up *.example accounts; nothing is shown.
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from fakes_autoswitch import set_policy
from switchboard import core, watch
from switchboard.accounts import api, autoloop, switcher, tokens
from test_autoswitch_loop import LoopCase, ok
from test_put_back_loop import RigLoop


class APutBackThatNeverComesBack(LoopCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(tokens.STOPPING.clear)
        self.answers(ok(20.0, 40.0), ok(20.0, 40.0), ok(20.0, 40.0))
        self.engine.live_state = api.ABSENT         # Claude Code's login is gone: every pass runs the put-back stage

    def test_it_waits_for_the_keychain_so_it_never_ends_the_loop(self):
        release, looks = self.blocker(), []

        def put_back():
            looks.append(1)
            self.clock.block_here(release)          # a Keychain dialog nobody answers
        loop = self.loop(put_back=put_back)
        for _ in range(3 * autoloop.SKIPS_EXIT):
            loop.run_pass()
            self.clock.advance(60)
        self.assertFalse(loop.stop.is_set())
        self.assertEqual(loop.exit_code, 0)
        self.assertEqual(len(looks), 1)             # never two of one stage at once
        self.assertIn('putting back skipped: the last one is still running', self.log_text())
        self.assertEqual(autoloop.STAGE_WAITS['putting back'], 'the Keychain')


class ALookFromTheSleep(LoopCase):
    def test_one_whose_watch_window_cannot_be_worked_out_is_caught_and_never_ends_the_loop(self):
        loop = self.loop(put_back=lambda: api.PutBack('wait', watch=True))
        loop.missing_watch = True
        with mock.patch.object(autoloop, 'since_switch', side_effect=RuntimeError('made-up failure')):
            loop.sleep(30.0)                        # it used to raise out of the sleep, and so out of run()
        said = "when to look at Claude Code's login again could not be worked out: RuntimeError"
        self.assertEqual(self.log_text().count(said), 1, self.log_text())       # once, not every few seconds

    def test_a_look_that_raises_is_caught_too(self):
        loop = self.loop()
        loop.missing_watch = True
        with mock.patch.object(loop, '_put_back', side_effect=RuntimeError('made-up look')):
            loop.sleep(30.0)
        self.assertIn("a look at Claude Code's login failed: RuntimeError", self.log_text())

    def test_the_loop_goes_on_after_one(self):
        loop = self.loop(put_back=lambda: api.PutBack('wait', watch=True))
        loop.missing_watch = True
        passes = []
        real = loop.run_pass

        def run_pass():
            passes.append(1)
            if len(passes) >= 3:
                loop.stop.set()
            return real()
        loop.run_pass = run_pass
        with mock.patch.object(autoloop, 'since_switch', side_effect=RuntimeError('made-up failure')):
            loop.run()
        self.assertEqual(len(passes), 3)


class ALookIsNotAPass(LoopCase):
    def test_looks_while_it_sleeps_leave_the_last_pass_as_it_was(self):
        self.answers(ok(20.0, 40.0), ok(20.0, 40.0), ok(20.0, 40.0))
        looks = []
        loop = self.loop(put_back=lambda: looks.append(self.clock.mono()) or api.PutBack('wait', watch=True))
        self.engine.live_state = api.ABSENT
        res = loop.run_pass()
        hb = self.heartbeat()
        before = (hb.get('last_pass_at'), hb.get('last_pass_mono'), hb.get('next_pass_at'), hb.get('pass_n'))
        loop.sleep(res.delay)
        self.assertGreaterEqual(len(looks), 5)      # one in the pass, then one every WATCH_EVERY_S while it sleeps
        hb = self.heartbeat()
        self.assertEqual((hb.get('last_pass_at'), hb.get('last_pass_mono'), hb.get('next_pass_at'), hb.get('pass_n')),
                         before)


class ThePagesOwnWatchdog(LoopCase):
    def watchdog(self):
        t = [10000.0]
        fired = []
        w = watch.PageWatch(stage=lambda: ('reading the accounts', 0.0), building=lambda: None,
                            switching=watch.switch_in_progress, mono=lambda: t[0], wall=lambda: 1.7e9 + t[0],
                            exit=fired.append)
        return w

    def test_it_waits_while_a_put_back_is_tried_as_it_waits_for_a_switch(self):
        w = self.watchdog()
        core.write_json(core.data_path(autoloop.HEARTBEAT), {'stage': 'putting back'})
        self.assertTrue(watch.switch_in_progress())
        self.assertIsNone(w.look())                 # the page's loop is stuck past PAGE_STUCK, but a put-back is tried
        core.write_json(core.data_path(autoloop.HEARTBEAT), {'stage': 'switching'})
        self.assertIsNone(w.look())
        core.write_json(core.data_path(autoloop.HEARTBEAT), {'stage': 'sleeping'})
        self.assertFalse(watch.switch_in_progress())
        self.assertIsNotNone(w.look())              # neither: a stuck page is ended


class NothingSaidTwiceOrFalsely(unittest.TestCase):
    """The watch's four kinds of note against Claude Code's login missing, emptied, or being put back: none of them
    is said in the seconds a lost login takes to come back (the put-back's own note is the one), and an emptied login
    that a switch can leave is no trouble either."""

    def setUp(self):
        from test_watch_notes import BEATING, NOW, ON, account
        self.on, self.beating, self.now, self.account = ON, BEATING, NOW, account
        self.a, self.b = 'ana@one.example', 'ben@two.example'

    def find(self, active, decision, **a):
        st = {'active': active, 'accounts': {self.a: self.account(**a), self.b: self.account()}}
        return watch.troubles(self.on, self.beating, st, decision, self.now,
                              keychain_words=autoloop.KEYCHAIN_WORDS['dialog'])

    def test_a_missing_login_is_none_of_the_four(self):
        stay = api.Decision('stay', reason='No switch now: Claude Code is not signed in.')
        self.assertEqual(self.find(None, stay), {})

    def test_a_login_being_put_back_is_none_of_the_four(self):
        self.assertEqual(self.find(self.a, api.Decision('stay', reason='Claude Code has room.')), {})

    def test_an_emptied_login_with_a_switch_to_make_is_none_of_the_four(self):
        move = api.Decision('switch', target='b', kind='forced', reason="Its login was refused")
        self.assertEqual(self.find(self.a, move, needs_login=True), {})

    def test_over_the_seconds_a_lost_login_takes_to_come_back_no_note_goes(self):
        from test_watch_notes import Clock
        clock = Clock()
        spells = watch.Spells(mono=lambda: clock.mono, wall=lambda: clock.wall,
                              path=os.path.join(core.data_dir(), 'spells-made-up.json'))
        stay = api.Decision('stay', reason='No switch now: Claude Code is not signed in.')
        notes = []
        for active in (None, None, None, self.a, self.a, self.a):    # missing for a few seconds, then back
            notes += spells.look(self.find(active, stay), active=active)
            clock.tick(5)
        self.assertEqual(notes, [])


class ACutOffSwitchAndALostLogin(RigLoop):
    """The loop's own data folder and the engine's are one folder, as on a Mac."""

    def setUp(self):
        RigLoop.setUp(self)
        os.makedirs(self.rig.data, exist_ok=True)
        os.environ['SWITCHBOARD_HOME'] = self.rig.data      # tearDown puts the earlier one back
        self.loop = autoloop.Loop(deps=self.loop.d, clock=self.clock, rand=lambda: 0.5)

    def live_writes(self):
        return sum(1 for c in self.rig.kc.calls if c[0] == 'write' and c[1] == self.rig.service)

    def test_in_one_pass_at_most_one_of_the_two_writes_claude_codes_login(self):
        set_policy(enabled=True)
        switcher._write_journal(self.ctx, {'id': 'made-up', 'step': 'writing', 'from_id': 'uuid-b',
                                           'to_id': 'uuid-a', 'orig_text_fp': 'made-up-fp'})
        self.lose_login()
        before = self.live_writes()
        self.loop.run_pass()
        # The put-back (early in the pass) leaves a login while a switch is part-way; the pass's last step settles
        # the record. Neither wrote Claude Code's login in this pass, so nothing is a mix of the two.
        self.assertFalse(os.path.exists(switcher.journal_path(self.ctx)))
        self.assertEqual(self.live_writes(), before)
        self.assertFalse(self.has_login())
        self.assertIn('Put away the record of a switch that was cut off', ' '.join(e[0] for e in self.repairs()))
        self.loop.sleep(30.0)                       # the next looks: the record is gone, so the login is put back
        self.assertTrue(self.has_login())
        put = [e for e in self.repairs() if e[0].startswith('Claude Code lost its sign-in; Switchboard put')]
        self.assertEqual(len(put), 1)
        self.assertEqual([r.action for r in self.told], ['put_back'])


if __name__ == '__main__':
    unittest.main()
