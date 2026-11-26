import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# A renewal of a kept login is one request whose answer must arrive: Anthropic retires the old refresh token the
# moment it answers. So the Mac is held awake across that one request (the same caffeinate the page holds while
# Claude Code works, bounded by the request's own deadline and let go at once after), and an idle renewal that can
# wait does not start while the lid is shut on battery (the Mac sleeps then whatever is held).
import subprocess
import time
import unittest

from fakes_accounts import Rig
from switchboard import awake
from switchboard.accounts import context, net, tokens


class Hold(object):
    """A stand-in for the hold: records each one, and whether the POST came while it was held."""

    def __init__(self):
        self.held = []
        self.active = False
        self.during_post = []

    def __call__(self, seconds):
        hold = self

        class _Held(object):
            def __enter__(self):
                hold.held.append(seconds)
                hold.active = True
                return True

            def __exit__(self, *exc):
                hold.active = False
                return False
        return _Held()


class RenewalCase(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.ctx = self.rig.ctx
        self.hold = Hold()
        self.ctx.hold_awake = self.hold
        self.rig.two_accounts()
        self.rig.server.on_post = lambda rt: self.hold.during_post.append(self.hold.active)
        self.before = len(self.rig.server.requests)

    def tearDown(self):
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        self.rig.close()

    def posts(self):
        return [r for r in self.rig.server.requests[self.before:] if r[0] == 'POST']


class TheRequestIsHeldAwake(RenewalCase):
    def test_the_renewal_post_happens_inside_one_hold_bounded_by_its_deadline_and_let_go_after(self):
        self.rig.set_saved_expiry('uuid-a', time.time() - 60)
        res = tokens.refresh(self.ctx, 'uuid-a', budget_s=60)
        self.assertEqual(res.outcome, 'refreshed', res)
        self.assertEqual(self.hold.during_post, [True])
        self.assertEqual(len(self.hold.held), 1)
        self.assertLessEqual(self.hold.held[0], tokens.RESPONSE_TIMEOUT_S)
        self.assertFalse(self.hold.active)                          # let go once the answer is in

    def test_the_hold_is_let_go_when_the_answer_never_comes(self):
        self.rig.set_saved_expiry('uuid-a', time.time() - 60)
        self.rig.server.script.append(('token', 'cut'))
        res = tokens.refresh(self.ctx, 'uuid-a', budget_s=60)
        self.assertTrue(res.spent_maybe, res)
        self.assertEqual(self.hold.during_post, [True])
        self.assertFalse(self.hold.active)

    def test_a_context_without_a_hold_still_renews(self):
        self.ctx.hold_awake = None
        self.rig.set_saved_expiry('uuid-a', time.time() - 60)
        self.assertEqual(tokens.refresh(self.ctx, 'uuid-a', budget_s=60).outcome, 'refreshed')

    def test_the_engine_s_own_context_holds_with_the_page_s_caffeinate(self):
        self.assertIs(context.Context(env={}, home='/nowhere', data_dir='/nowhere', keychain=None,
                                      http=None).hold_awake, None)    # a made-up context holds nothing by itself
        self.assertIs(context.default().hold_awake, awake.held_awake)


class LidShutOnBattery(RenewalCase):
    def test_an_idle_renewal_waits_while_the_lid_is_shut_on_battery(self):
        self.rig.set_saved_expiry('uuid-a', time.time() - 60)
        self.ctx.sleeping_soon = lambda: True
        notes = tokens.upkeep_pass(self.ctx, budget_s=60)
        self.assertEqual(self.posts(), [])
        self.assertEqual(notes, [])
        self.assertTrue(any('lid is shut on battery' in x for x in self.rig.logs))
        self.ctx.sleeping_soon = lambda: False                     # lid open, or on power: as ever
        tokens.upkeep_pass(self.ctx, budget_s=60)
        self.assertEqual(len(self.posts()), 1)

    def test_nothing_is_asked_when_nothing_is_due(self):
        asked = []
        self.ctx.sleeping_soon = lambda: asked.append(1) or True
        tokens.upkeep_pass(self.ctx, budget_s=60)
        self.assertEqual(asked, [])                                 # pmset and ioreg run only when a renewal is due

    def test_a_switch_s_own_renewal_never_waits_for_the_lid(self):
        self.rig.set_saved_expiry('uuid-a', time.time() - 60)
        self.ctx.sleeping_soon = lambda: True
        self.assertEqual(tokens.refresh(self.ctx, 'uuid-a', budget_s=60, reason='switch').outcome, 'refreshed')

    def test_lid_and_power_are_read_from_the_mac_s_own_answers(self):
        def run(answers):
            return lambda cmd, timeout=5: answers[cmd[0]]
        batt = "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t80%; discharging\n"
        ac = "Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t80%; charged\n"
        shut, open_ = '"AppleClamshellState" = Yes', '"AppleClamshellState" = No'
        cases = [((batt, shut), True), ((ac, shut), False), ((batt, open_), False), (('', ''), False),
                 ((batt, ''), False)]
        for (pm, io), want in cases:
            with self.subTest(pm=pm[:30], io=io):
                self.assertIs(awake.lid_shut_on_battery(run({awake.PMSET[0]: pm, awake.IOREG[0]: io})), want)


class TheHoldItself(unittest.TestCase):
    def test_it_is_the_page_s_caffeinate_with_a_deadline_and_it_is_let_go(self):
        made = []

        class Proc(object):
            def __init__(self, argv, **kw):
                made.append(self)
                self.argv, self.ended = argv, False

            def poll(self):
                return 0 if self.ended else None

            def terminate(self):
                self.ended = True

            def wait(self, timeout=None):
                return 0

            def kill(self):
                self.ended = True
        with awake.held_awake(29.2, popen=Proc, pid=4242) as on:
            self.assertTrue(on)
            self.assertEqual(made[0].argv, [awake.CAFFEINATE, '-i', '-s', '-w', '4242', '-t', '30'])
            self.assertFalse(made[0].ended)
        self.assertTrue(made[0].ended)

    def test_a_hold_that_cannot_start_holds_nothing_and_never_raises(self):
        def refuse(argv, **kw):
            raise OSError('no caffeinate here')
        with awake.held_awake(10, popen=refuse) as on:
            self.assertFalse(on)


if __name__ == '__main__':
    unittest.main()
