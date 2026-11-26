import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# Signing in again is never an interruption for the account Claude Code is on, pass by pass in the automatic loop with
# every outside thing fake:
# - its login refused (Claude Code emptied it) or its sign-in ended, with no newer sign-in kept: Claude Code moves to
#   another account with room at once, and the ended account is not chosen again until it is signed in again;
# - its login emptied while a newer sign-in of the same account is kept: the keep pass puts that one back live, and the
#   loop reads the login again before it decides, so Claude Code stays on the same account (never a jump to another).
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard.accounts import api
from fakes_autoswitch import FakeClock, FakeEngine, FakeNet, FakeTokens, Home, T0, account, make_loop, set_policy, \
    usage_body

H, D = 3600.0, 86400.0


def ok(five, week, week_reset=None):
    return (200, {}, usage_body(five=five, week=week, five_reset=T0 + 4 * H, week_reset=week_reset or T0 + 4 * D))


class Engine(FakeEngine):
    """The fake engine, with Claude Code's login emptied when `wiped` (as Claude Code leaves it after Anthropic refused
    it; the account is the one its settings name), and a keep pass that puts a newer kept sign-in back when `newer`."""

    wiped = False
    newer = False
    keeps = 0

    def live(self):
        if not self.wiped:
            return FakeEngine.live(self)
        self.live_calls += 1
        a = next((x for x in self.accts if x.id == self.active), None)
        return api.Live(state='wiped', account_id=self.active, email=a.email if a else None, proof='label',
                        why="Claude Code's login was emptied (Anthropic refused it)")

    def keep_pass(self):
        self.keeps += 1
        if self.wiped and self.newer:
            self.wiped = False
            return 'Claude Code is on the new sign-in of %s' % next(a.email for a in self.accts if a.id == self.active)
        return None


class Case(Home):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock()
        self.accts = [account('a1', 1), account('a2', 2), account('a3', 3)]
        self.engine = Engine(self.accts, 'a1', self.clock)
        self.net = FakeNet(self.clock)
        set_policy()
        self.net.set('tok-a1', ok(20.0, 30.0, week_reset=T0 + 1 * D))     # refills first: nothing to move for
        self.net.set('tok-a2', ok(10.0, 20.0, week_reset=T0 + 2 * D))
        self.net.set('tok-a3', ok(10.0, 20.0, week_reset=T0 + 3 * D))

    def loop(self):
        return make_loop(self.engine, self.clock, self.net, FakeTokens())


class WithNoNewerSignInKept(Case):
    def test_a_refused_login_moves_claude_code_to_another_account_with_room_at_once(self):
        self.engine.wiped = True
        res = self.loop().run_pass()
        self.assertEqual([(s['to'], s['kind']) for s in self.engine.switches], [('a2', 'forced')])
        self.assertIn("a1@acct.example's login was refused", self.engine.switches[0]['reason'])
        self.assertEqual(res.decision.kind, 'forced')

    def test_an_ended_sign_in_moves_it_at_once_and_is_not_chosen_again_until_signed_in_again(self):
        self.engine.accts[0] = account('a1', 1, status='needs_login')          # its date passed
        loop = self.loop()
        loop.run_pass()
        self.assertEqual([(s['to'], s['kind']) for s in self.engine.switches], [('a2', 'forced')])
        self.net.set('tok-a2', ok(95.0, 20.0, week_reset=T0 + 2 * D))           # a2 at its line now
        self.clock.advance(400)
        loop.run_pass()
        self.assertEqual([s['to'] for s in self.engine.switches], ['a2', 'a3'])   # a1 is out of the choosing
        self.net.set('tok-a3', ok(95.0, 20.0, week_reset=T0 + 3 * D))
        self.engine.accts[0] = account('a1', 1)                                 # signed in again
        self.clock.advance(400)
        loop.run_pass()
        self.assertEqual([s['to'] for s in self.engine.switches], ['a2', 'a3', 'a1'])


class WithANewerSignInKept(Case):
    def test_an_emptied_login_put_right_by_the_keep_pass_is_not_a_switch_to_another_account(self):
        self.engine.wiped = True
        self.engine.newer = True
        res = self.loop().run_pass()
        self.assertEqual(self.engine.keeps, 1)
        self.assertEqual(self.engine.switches, [])
        self.assertEqual(self.engine.active, 'a1')
        self.assertNotEqual(res.decision.kind, 'forced')


if __name__ == '__main__':
    unittest.main()
