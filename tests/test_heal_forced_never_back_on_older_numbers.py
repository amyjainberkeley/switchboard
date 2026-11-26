import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# A move on older numbers never lands on an account a forced switch left while its newest number is older than that
# switch: what was known of it then predates the moment Claude Code was stopped on it. Read again since, it is judged
# by that reading as ever. When that leaves nothing, the answer is the stay it was before. An ordinary forced switch on
# fresh numbers is not changed. The loop gives the policy which accounts a forced switch left, and when, from the list's
# own record of switches. Made-up *.example accounts; stand-ins only.
import time
import unittest

from fakes_accounts import Rig
from fakes_autoswitch import T0, account
from switchboard.accounts import api, autoloop, context, policy, switcher

H = 3600.0
NOW = T0
ACCTS = (account('a', 1), account('b', 2))


def win(name, pct, age):
    return api.Window(name, pct, NOW + 3 * H if name == '5-hour' else NOW + 4 * 86400, NOW - age, 'endpoint')


def reading(aid, five, week, age):
    return api.Reading(aid, (win('5-hour', five, age), win('weekly', week, age)), NOW - age, 'endpoint', False, None)


def snap(active, readings, **kw):
    return policy.Snapshot(now=NOW, accounts=ACCTS, readings=readings, settings=policy.Settings(enabled=True),
                           active=active, busy=False, idle_for_s=1800.0, since_switch_s=60.0, optional_recent=0,
                           load_per_core=0.5, battery_pct=90.0, on_ac=True, **kw)


OLD = {'a': reading('a', 40, 30, 3 * H), 'b': reading('b', 35, 30, 3 * H)}       # every read refused for hours


class NeverStraightBack(unittest.TestCase):
    def test_a_limited_then_b_limited_moves_once_then_stays(self):
        d1 = policy.decide(snap('a', OLD, rate_limited=True))
        self.assertEqual((d1.action, d1.target), ('switch', 'b'))
        self.assertIn('older numbers', d1.reason)
        left = (('a', NOW - 60),)                                   # the forced switch left A a minute ago
        d2 = policy.decide(snap('b', OLD, rate_limited=True, left_forced=left))
        self.assertEqual((d2.action, d2.kind, d2.hold), ('stay', 'forced', 'no fresh numbers'), d2.reason)
        self.assertIn('no other account has fresh numbers', d2.reason)

    def test_read_again_since_it_is_a_landing_place_again(self):
        left = (('a', NOW - 3 * H),)                                # left three hours ago
        r = dict(OLD, a=reading('a', 40, 30, 2 * H))                # and read an hour after that (not fresh now)
        d = policy.decide(snap('b', r, rate_limited=True, left_forced=left))
        self.assertEqual((d.action, d.target), ('switch', 'a'))
        self.assertIn('older numbers', d.reason)

    def test_an_ordinary_forced_switch_on_fresh_numbers_is_the_same(self):
        r = {'a': reading('a', 100, 30, 60.0), 'b': reading('b', 20, 30, 60.0)}
        for left in ((), (('b', NOW - 30),)):
            with self.subTest(left=left):
                d = policy.decide(snap('a', r, rate_limited=True, left_forced=left))
                self.assertEqual((d.action, d.target), ('switch', 'b'))
                self.assertNotIn('older numbers', d.reason)


class TheLoopTellsThePolicy(unittest.TestCase):
    def test_the_accounts_a_forced_switch_left_come_from_the_list_of_switches(self):
        rig = Rig()
        self.addCleanup(rig.close)
        use = context.use(rig.ctx)
        use.__enter__()
        self.addCleanup(use.__exit__, None, None, None)
        rig.two_accounts()                                          # on B
        before = time.time()
        self.assertTrue(switcher.switch(rig.ctx, 'uuid-a', kind='forced', source='auto', interactive=True).ok)
        self.assertTrue(switcher.switch(rig.ctx, 'uuid-b', kind='manual', source='cli', interactive=True).ok)
        got = dict(autoloop.left_by_force())
        self.assertEqual(sorted(got), ['uuid-b'])                   # only a forced switch counts
        self.assertGreaterEqual(got['uuid-b'], before)


if __name__ == '__main__':
    unittest.main()
