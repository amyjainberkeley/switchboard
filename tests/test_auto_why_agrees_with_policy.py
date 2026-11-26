import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# `switchboard auto why` says what the policy does: the lines of one check change the shared lines only (an account
# with a line of its own keeps it, and the document and the closing lines say so); an account in use that is kept out
# moves nowhere, so no other account "could take it"; a forced move passes over an account used last while another
# has room; in an order that never moves early the closing line says so; an account ranked after the one in use gets
# the rank as its reason. Then the same over thousands of made-up snapshots with rules, ranks, own lines and all
# three orders: every account it says could take it is one a move could land on, and the reason it gives for the rest
# is the true one. Made-up accounts only (*.example).
import dataclasses
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import autocheck
from switchboard.accounts import autoloop, policy
from fakes_autoswitch import set_policy
from test_auto_check import CheckCase, D, T0, ok
from test_autoswitch_account_rules import generated
from test_autoswitch_policy import NOW, reading, snap

A, B, C = 'a@acct.example', 'b@acct.example', 'c@acct.example'
SEED, COUNT = 2511, 4000


def judged(s, *flags):
    opts = autocheck.parse(['why'] + list(flags))
    used = dataclasses.replace(s, settings=autocheck.check_settings(opts, s.settings))
    return autocheck.judge(opts, used, s.settings)


def by_email(doc):
    return dict((c['email'], c) for c in doc['candidates'])


class OneCheckLines(unittest.TestCase):
    def test_an_account_with_its_own_line_keeps_it_and_the_document_says_so(self):
        s = snap({'a': reading('a', five=65.0, week=30.0), 'b': reading('b', five=10.0, week=20.0)},
                 settings=policy.Settings(enabled=True, own=(('a', (('session_at', 60.0),)),)))
        code, doc = judged(s, '--five-hour', '95')
        self.assertEqual((code, doc['to']), (autoloop.EXIT_SWITCHED, B))        # still judged at its own 60
        self.assertEqual(doc['settings']['five_hour_line'], 95)
        self.assertEqual(doc['settings']['own_lines'],
                         [{'email': A, 'number': 1, 'five_hour_line': 60, 'week_line': None}])
        lines = autocheck.lines_of(doc)
        self.assertIn('For this check only: 5-hour line 95%.', lines)
        self.assertIn('An account with a line of its own keeps it: %s.' % A, lines)

    def test_ranks_are_in_the_document(self):
        s = snap({'a': reading('a', five=20.0, week=30.0), 'b': reading('b', five=10.0, week=20.0)},
                 settings=policy.Settings(enabled=True, ranks=(('b', 1),)))
        _, doc = judged(s)
        self.assertEqual(doc['settings']['ranks'], [{'email': B, 'number': 2, 'rank': 1}])


class WhereItDisagreed(unittest.TestCase):
    def test_an_account_in_use_kept_out_moves_nowhere(self):
        for five in (20.0, 95.0):                                       # under its line, and over it
            s = snap({'a': reading('a', five=five, week=30.0), 'b': reading('b', five=10.0, week=20.0),
                      'c': reading('c', five=10.0, week=20.0)},
                     settings=policy.Settings(enabled=True, rules=(('a', 'out'),)))
            code, doc = judged(s)
            with self.subTest(five=five):
                self.assertEqual(policy.decide(s).action, 'stay')
                self.assertEqual([c['why_not'] for c in doc['candidates']], ['in_use_kept_out'] * 2)
                self.assertFalse(any(c['can_take_it'] for c in doc['candidates']))
                self.assertIn('%s: %s, in use, is kept out of auto-switch, so nothing moves from it.' % (B, A),
                              doc['reasons'])

    def test_a_forced_move_passes_over_an_account_used_last(self):
        s = snap({'a': reading('a', five=95.0, week=30.0), 'b': reading('b', five=10.0, week=20.0),
                  'c': reading('c', five=5.0, week=5.0)},
                 settings=policy.Settings(enabled=True, rules=(('c', 'last'),)))
        _, doc = judged(s)
        self.assertEqual((doc['to'], doc['forced']), (B, True))
        self.assertEqual((by_email(doc)[C]['why_not'], by_email(doc)[C]['can_take_it']), ('used_last', True))

    def test_in_an_order_that_never_moves_early_the_closing_line_says_so(self):
        for order in ('most-room', 'stay-then-refills'):
            s = snap({'a': reading('a', five=20.0, week=30.0),
                      'b': reading('b', five=10.0, week=20.0, week_reset=NOW + D)},
                     settings=policy.Settings(enabled=True, strategy=order))
            _, doc = judged(s)
            with self.subTest(order=order):
                self.assertEqual(doc['action'], 'stay')
                self.assertTrue(doc['would_change'].endswith('and not before: in this order a move waits for a '
                                                             'line.'), doc['would_change'])

    def test_an_account_ranked_after_the_one_in_use_says_its_rank(self):
        s = snap({'a': reading('a', five=20.0, week=30.0),
                  'b': reading('b', five=10.0, week=10.0, week_reset=NOW + D)},
                 settings=policy.Settings(enabled=True, ranks=(('a', 1), ('b', 2))))
        code, doc = judged(s)
        self.assertEqual((code, doc['action']), (autoloop.EXIT_NOTHING, 'stay'))
        b = by_email(doc)[B]
        self.assertEqual((b['why_not'], b['can_take_it']), ('ranked_after', True))
        self.assertEqual(b['words'], 'has room, but rank 2, after %s (rank 1): a move that can wait never goes to a '
                                     'later rank' % A)


class StayThenRefills(CheckCase):
    def test_a_check_in_the_third_order_waits_for_a_line_then_takes_what_refills_first(self):
        set_policy()
        self.answers(ok(20.0, 40.0), ok(10.0, 20.0, week_reset=T0 + D), ok(10.0, 20.0, week_reset=T0 + 2 * D))
        code, out, _ = self.check('--order', 'stay-then-refills')
        self.assertEqual(code, autoloop.EXIT_NOTHING, out)                # no early move in this order
        self.assertEqual(self.engine.switches, [])
        self.assertIn('For this check only: order stay-then-refills.', out)
        self.answers(ok(95.0, 40.0), ok(10.0, 20.0, week_reset=T0 + 2 * D), ok(10.0, 20.0, week_reset=T0 + D))
        self.clock.advance(3600)
        code, out, _ = self.check('--order', 'stay-then-refills')
        self.assertEqual(code, autoloop.EXIT_SWITCHED, out)
        self.assertEqual([x['to'] for x in self.engine.switches], ['a3'])     # at its line: what refills first


def with_ranks_and_own_lines(seed, count):
    """The made-up snapshots of test_autoswitch_account_rules, each given ranks and lines of their own at random."""
    rng = random.Random(seed)
    for s in generated(seed, count):
        ids = [a.id for a in s.accounts]
        ranks = tuple(sorted((x, rng.randint(1, 3)) for x in ids if rng.random() < 0.5))
        own = tuple(sorted((x, tuple((k, float(rng.choice((60, 80, 92, 99)))) for k in ('session_at', 'weekly_at')
                                     if rng.random() < 0.5)) for x in ids if rng.random() < 0.3))
        own = tuple((x, lines) for x, lines in own if lines)
        yield dataclasses.replace(s, settings=dataclasses.replace(s.settings, ranks=ranks, own=own))


class ManySnapshots(unittest.TestCase):
    def test_what_it_says_could_take_it_could_and_each_other_reason_is_the_true_one(self):
        seen = set()
        for n, s in enumerate(with_ranks_and_own_lines(SEED, COUNT)):
            code, doc = judged(s)
            used = dataclasses.replace(s, settings=autocheck.check_settings(autocheck.parse(['why']), s.settings))
            d = policy.decide(used)
            alone = autocheck._cleared(used)
            ids = [a.id for a in used.accounts]
            in_use_rule = policy.rule_of(used.settings, used.active) if used.active in ids else None
            views = dict((a.id, policy._view(used, a)) for a in used.accounts)
            for c in doc['candidates']:
                aid = ids[c['number'] - 1]
                a, v, rule = used.accounts[c['number'] - 1], views[aid], policy.rule_of(used.settings, aid)
                where = (n, aid, c['why_not'], d, used.settings)
                if c['why_not'] is None:                            # could take it: a move could land there
                    if not (d.action == 'switch' and d.target == aid):     # (the pick is where it lands)
                        self.assertEqual(policy.need(alone, aid).action, 'switch', where)
                    self.assertNotEqual(rule, 'out', where)
                    self.assertNotEqual(in_use_rule, 'out', where)
                    if d.action == 'switch' and d.kind == 'forced' and rule == 'last':
                        self.assertEqual(policy.rule_of(used.settings, d.target), 'last', where)
                    if d.action == 'switch':
                        self.assertEqual(c['words'] == 'is the one it picks', d.target == aid, where)
                    seen.add('could')
                elif c['why_not'] == 'kept_out':
                    self.assertEqual(rule, 'out', where)
                elif c['why_not'] == 'needs_sign_in':
                    self.assertEqual(a.status, 'needs_login', where)
                elif c['why_not'] == 'in_use_kept_out':
                    self.assertEqual(in_use_rule, 'out', where)
                    self.assertEqual(d.action, 'stay', where)
                elif c['why_not'] == 'used_last':
                    self.assertEqual(rule, 'last', where)
                elif c['why_not'] == 'ranked_after':
                    av = views[used.active]
                    self.assertGreater(policy._rank_key(v), policy._rank_key(av), where)
                    self.assertNotEqual(d.target, aid, where)
                elif c['why_not'] == 'no_gain':
                    self.assertEqual(used.settings.strategy, 'refills-first', where)
                    self.assertNotEqual(rule, 'last', where)
                    mine, theirs = policy._rank_key(v), policy._rank_key(views[used.active])
                    if in_use_rule != 'last' and mine < theirs:     # ranked before: only too little room holds it
                        self.assertTrue(not v.known or v.room < max(policy.OPTIONAL_ROOM, policy.LAND_MARGIN), where)
                    elif in_use_rule != 'last':                     # off an account used last, ranks do not hold
                        self.assertEqual(mine, theirs, where)
                    self.assertNotEqual(d.target, aid, where)
                seen.add(c['why_not'])
            if d.action == 'switch':                                # the pick is a candidate it says could take it
                picked = [c for c in doc['candidates'] if ids[c['number'] - 1] == d.target]
                self.assertEqual([c['why_not'] for c in picked], [None], (n, d, picked))
        for word in ('could', 'kept_out', 'in_use_kept_out', 'used_last', 'ranked_after', 'no_gain'):
            self.assertIn(word, seen)


if __name__ == '__main__':
    unittest.main()
