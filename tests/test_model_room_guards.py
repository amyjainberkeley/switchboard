import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# Model and sign switches keep the guard rails an optional switch has (caps, the load rule, open work, the settle
# after a restart, a low battery), but never wait for Claude Code to be idle. A model week no chat is known to use
# decides nothing. A forced switch waits for a calm Mac two minutes at most.
import dataclasses
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard.accounts import policy  # noqa: E402
from test_model_room_policy import ACCTS, D, FABLE, NOW, random_snapshot, reading, settings, snap  # noqa: E402

TWO = ACCTS[:2]
DOC = ({'kind': 'doc', 'url': 'u', 'at': NOW - 60},)


def model_r():
    return {'a': reading('a', week=30, fable=100), 'b': reading('b', week=20, fable=20)}


def sign_r():
    return {'a': reading('a', week=66), 'b': reading('b', week=16)}


def optional_r():
    return {'a': reading('a', five=20, week=40, week_reset=NOW + 5 * D),
            'b': reading('b', five=10, week=50, week_reset=NOW + 2 * D)}


class Caps(unittest.TestCase):
    """A sign switch shares the optional switches' cap; a model switch has a backstop cap of its own."""

    ON = settings(sign_lines=True)

    def test_a_sign_switch_obeys_the_optional_cap_and_counts_toward_it(self):
        d = policy.decide(snap(sign_r(), accounts=TWO, asked=frozenset(), settings=self.ON,
                               optional_recent=1, sign_recent=1))
        self.assertEqual((d.action, d.kind, d.target), ('stay', 'sign', 'b'))
        self.assertEqual(d.hold, '2 optional switches in the last 6 hours already')
        d = policy.decide(snap(sign_r(), accounts=TWO, asked=frozenset(), settings=self.ON, optional_recent=2))
        self.assertEqual((d.action, d.kind), ('stay', 'sign'))
        d = policy.decide(snap(sign_r(), accounts=TWO, asked=frozenset(), settings=self.ON, optional_recent=1))
        self.assertEqual((d.action, d.kind), ('switch', 'sign'))
        # an optional switch counts the sign switches too
        d = policy.decide(snap(optional_r(), accounts=TWO, asked=frozenset(), optional_recent=1, sign_recent=1))
        self.assertEqual((d.action, d.kind, d.hold), ('stay', 'optional',
                                                      '2 optional switches in the last 6 hours already'))
        d = policy.decide(snap(optional_r(), accounts=TWO, asked=frozenset(), optional_recent=1, model_recent=3))
        self.assertEqual((d.action, d.kind), ('switch', 'optional'))

    def test_a_sign_switch_on_a_very_busy_mac_says_what_an_optional_one_says(self):
        d = policy.decide(snap(sign_r(), accounts=TWO, asked=frozenset(), settings=self.ON, load_per_core=5.0,
                               waited={'sign': 99999.0}))
        self.assertEqual((d.action, d.kind, d.hold), ('stay', 'sign', 'the Mac is very busy (load 5 per core)'))

    def test_a_model_switch_has_a_cap_of_its_own(self):
        self.assertEqual(policy.MODEL_CAP, 4)
        d = policy.decide(snap(model_r(), accounts=TWO, asked=FABLE, model_recent=4))
        self.assertEqual((d.action, d.kind, d.target), ('stay', 'model', 'b'))
        self.assertEqual(d.hold, '4 switches for a model in the last 6 hours already')
        self.assertIn('Next: b@acct.example; waits: 4 switches for a model', d.reason)
        # the hour on a busy Mac does not lift it
        d = policy.decide(snap(model_r(), accounts=TWO, asked=FABLE, model_recent=4, load_per_core=15.0,
                               waited={'model': 99999.0}))
        self.assertEqual((d.action, d.kind), ('stay', 'model'))
        self.assertEqual(policy.decide(snap(model_r(), accounts=TWO, asked=FABLE, model_recent=3)).action, 'switch')
        # optional and sign switches are not its business, and it never waits for Claude Code to be idle
        d = policy.decide(snap(model_r(), accounts=TWO, asked=FABLE, optional_recent=5, sign_recent=5, busy=True,
                               idle_for_s=0.0))
        self.assertEqual((d.action, d.kind), ('switch', 'model'))

    def test_a_forced_switch_has_no_cap(self):
        rf = {'a': reading('a', five=95, week=30), 'b': reading('b', five=5, week=5)}
        d = policy.decide(snap(rf, accounts=TWO, optional_recent=9, sign_recent=9, model_recent=9))
        self.assertEqual((d.action, d.kind), ('switch', 'forced'))


class NotKnownYet(unittest.TestCase):
    """While nothing says which models the chats use (asked None), only the setting `model`'s family counts."""

    def test_a_reported_model_week_never_picks_the_target_of_a_forced_switch(self):
        r = {'a': reading('a', five=95, week=30),
             'b': reading('b', five=10, week=40, opus=100, week_reset=NOW + D),      # refills first
             'c': reading('c', five=10, week=40, opus=10, week_reset=NOW + 3 * D)}
        self.assertEqual(policy.decide(snap(r, asked=None)).target, 'b')
        self.assertEqual(policy.decide(snap(r, asked=frozenset())).target, 'b')
        # the setting, or a chat known to ask for it, makes it count
        self.assertEqual(policy.decide(snap(r, asked=None, settings=settings(model='Opus'))).target, 'c')
        self.assertEqual(policy.decide(snap(r, asked=frozenset(['opus']))).target, 'c')

    def test_a_reported_model_week_never_holds_an_optional_switch(self):
        r = optional_r()
        r['b'] = reading('b', five=10, week=50, opus=92, week_reset=NOW + 2 * D)
        d = policy.decide(snap(r, accounts=TWO, asked=None))
        self.assertEqual((d.action, d.kind, d.target), ('switch', 'optional', 'b'))
        self.assertEqual(policy.decide(snap(r, accounts=TWO, asked=frozenset(['opus']))).action, 'stay')

    def test_the_families_that_decide(self):
        r = {'a': reading('a', week=30, fable=40, opus=50), 'b': reading('b', week=20)}
        self.assertEqual(policy.deciding_families(snap(r, asked=None)), ())
        self.assertEqual(policy.deciding_families(snap(r, asked=None, settings=settings(model='Fable'))), ('fable',))
        self.assertEqual(policy.deciding_families(snap(r, asked=frozenset(['opus']))), ('opus',))
        # the reported weeks still name the families for the words and the poller's reads
        self.assertEqual(policy.asked_families(snap(r, asked=None)), ('fable', 'opus'))


class ForcedWaitsTwoMinutes(unittest.TestCase):
    """A forced switch waits for a calm Mac two minutes at most, whatever the percent: a 5-hour window can run from
    its line to 100 in a few minutes with many chats working."""

    def d(self, five, week, waited, load=6.0):
        r = {'a': reading('a', five=five, week=week), 'b': reading('b', five=5, week=5)}
        return policy.decide(snap(r, accounts=TWO, load_per_core=load, waited={'forced': waited}))

    def test_two_minutes_at_most(self):
        self.assertEqual(policy.CALM_FORCED_S, 120.0)
        self.assertEqual(self.d(100, 10, 0.0).action, 'switch')       # at 100 it cannot work at all: never waits
        for five, week in ((91, 10), (95, 10), (98, 10), (10, 96), (10, 99)):
            d = self.d(five, week, 119.0)
            self.assertEqual((d.action, d.kind, d.target), ('stay', 'forced', 'b'), (five, week))
            self.assertIn('waits for the Mac to calm down (load 6 per core), ', d.hold)
            self.assertEqual((self.d(five, week, 120.0).action, self.d(five, week, 120.0).kind), ('switch', 'forced'),
                             (five, week))
        self.assertEqual(self.d(91, 10, 0.0, load=1.9).action, 'switch')
        self.assertEqual(self.d(91, 10, 0.0, load=None).action, 'switch')         # a load not known never holds

    def test_one_constant(self):
        for gone in ('CALM_BLOCKED_S', 'CALM_FORCED_NEAR_S', 'CALM_NEAR_PCT'):
            self.assertFalse(hasattr(policy, gone), gone)


WATCH = ({'kind': 'watch', 'url': 'u', 'at': NOW - 60},)
ARTIFACT = ({'kind': 'artifact', 'url': 'u', 'at': NOW - 60, 'until': NOW + 600},)


class AWatchEnds(unittest.TestCase):
    """Second reader: a model switch held by a session watching an artifact (which can last all day with 30 chats)
    goes once it has been wanted CALM_MODEL_MAX_S, as it does for 'optional switches are off'. A Doc or a fresh
    artifact still holds it; a sign switch has no end; a low battery holds until the Mac is plugged in."""

    def d(self, obligations, waited, kind='model', **kw):
        if kind == 'sign':
            return policy.decide(snap(sign_r(), accounts=TWO, asked=frozenset(), settings=settings(sign_lines=True),
                                      obligations=obligations, waited={'sign': waited}, **kw))
        return policy.decide(snap(model_r(), accounts=TWO, asked=FABLE, obligations=obligations,
                                  waited={'model': waited}, **kw))

    def test_a_watch_holds_a_model_switch_an_hour_at_most(self):
        d = self.d(WATCH, policy.CALM_MODEL_MAX_S - 60)
        self.assertEqual((d.action, d.kind, d.target), ('stay', 'model', 'b'))
        self.assertEqual(d.hold, 'a chat is watching an artifact as a@acct.example, 60 s at most')
        d = self.d(WATCH, 0.0)
        self.assertEqual(d.hold, 'a chat is watching an artifact as a@acct.example, 60 min at most')
        d = self.d(WATCH, policy.CALM_MODEL_MAX_S)
        self.assertEqual((d.action, d.kind, d.target), ('switch', 'model', 'b'))

    def test_a_doc_or_a_fresh_artifact_still_holds_it(self):
        for ob in (DOC, ARTIFACT, WATCH + DOC, DOC + WATCH):
            d = self.d(ob, 99999.0)
            self.assertEqual((d.action, d.kind), ('stay', 'model'), ob)
            self.assertNotIn('watching', d.hold, ob)

    def test_a_sign_switch_and_a_low_battery_have_no_end(self):
        d = self.d(WATCH, 99999.0, kind='sign')
        self.assertEqual((d.action, d.kind, d.hold), ('stay', 'sign', 'a chat is watching an artifact as '
                                                                      'a@acct.example'))
        d = self.d((), 99999.0, battery_pct=10.0, on_ac=False)
        self.assertEqual((d.action, d.kind, d.hold), ('stay', 'model', 'the battery is at 10%'))


class ManyGuards(unittest.TestCase):
    """Made-up snapshots from a fixed seed, with the guards set at random: no model or sign switch breaks one."""

    def test_the_guards_hold(self):
        rnd = random.Random(4111)
        seen = {}
        for i in range(16000):
            s = random_snapshot(rnd)
            s = dataclasses.replace(
                s, obligations=rnd.choice([DOC, WATCH, WATCH + DOC]) if rnd.random() < 0.15 else (),
                no_optional=rnd.choice([None, None, None, 'automatic switching was just restarted']),
                battery_pct=rnd.choice([90.0, 90.0, 10.0]), on_ac=rnd.choice([True, False]),
                optional_recent=rnd.choice([0, 0, 0, 1, 2]), sign_recent=rnd.choice([0, 0, 0, 1, 2]),
                model_recent=rnd.randint(2, 5))
            d = policy.decide(s)
            seen[(d.action, d.kind)] = seen.get((d.action, d.kind), 0) + 1
            if d.action != 'switch' or d.kind not in ('model', 'sign'):
                continue
            # a model switch goes past a watch (only) once wanted CALM_MODEL_MAX_S; nothing else goes past any
            self.assertTrue(not s.obligations or (d.kind == 'model' and all(ob['kind'] == 'watch' for ob in
                                                                             s.obligations)
                                                  and s.waited.get('model', 0.0) >= policy.CALM_MODEL_MAX_S), (i, d))
            self.assertFalse(s.on_ac is False and s.battery_pct < policy.BATTERY_LOW, (i, d))
            if d.kind == 'sign':
                self.assertIsNone(s.no_optional, (i, d))
                self.assertLess(s.optional_recent + s.sign_recent, policy.OPTIONAL_CAP, (i, d))
                self.assertTrue(s.load_per_core is None or s.load_per_core <= policy.LOAD_HIGH, (i, d))
            else:
                self.assertTrue(s.no_optional is None or s.waited.get('model', 0.0) >= policy.CALM_MODEL_MAX_S,
                                (i, d))
                self.assertLess(s.model_recent, policy.MODEL_CAP, (i, d))
        for kind in ('model', 'sign'):
            self.assertGreater(seen.get(('switch', kind), 0), 5, (kind, seen))


if __name__ == '__main__':
    unittest.main()
