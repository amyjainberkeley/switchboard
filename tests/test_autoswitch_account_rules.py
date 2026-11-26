import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# An account's rule in auto-switch: 'out' (kept out of auto-switch: never moved onto by itself, never moved away from
# by itself) and 'last' (used last: only when no other account can be landed on). policy.decide and policy.need are
# pure, so every rule is a snapshot and an answer; plan.py, the order the page shows, agrees. Made-up accounts only.
import dataclasses
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import plan
from switchboard.accounts import api, policy
from fakes_autoswitch import T0, account
from test_autoswitch_policy import ACCTS, CALM, reading, snap

H, D = 3600.0, 86400.0
NOW = T0


def ruled(strategy='refills-first', **rules):
    return policy.Settings(enabled=True, strategy=strategy, rules=tuple(sorted(rules.items())))


def said(d):
    return (d.action, d.kind, d.target)


class TheSetting(unittest.TestCase):
    def test_rules_are_frozen_and_hashable_and_none_is_in(self):
        s = ruled(b='last', c='out')
        self.assertEqual(hash(s), hash(ruled(c='out', b='last')))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            s.rules = ()
        self.assertEqual([policy.rule_of(s, x) for x in 'abc'], ['in', 'last', 'out'])
        self.assertEqual(policy.Settings().rules, ())

    def test_rules_stored_by_hand_as_junk_read_as_none(self):
        for junk in (['b', 'out'], 'out', 7, None, {'b': 'never', 'c': 7, '': 'out', 'd': 'OUT', 'e': 'in'}):
            with self.subTest(junk=junk):
                self.assertEqual(policy.settings_from({'enabled': True, 'rules': junk}).rules, ())
        s = policy.settings_from({'rules': {'c': 'out', 'b': 'last', 'd': 'in', 'e': ['last']}})
        self.assertEqual(s.rules, (('b', 'last'), ('c', 'out')))


class KeptOut(unittest.TestCase):
    def test_on_a_kept_out_account_under_its_lines_it_stays_and_says_so(self):
        r = {'a': reading('a', five=20.0, week=20.0), 'b': reading('b', five=5.0, week=5.0, week_reset=NOW + D)}
        d = policy.decide(snap(r, settings=ruled(a='out')))
        self.assertEqual(said(d), ('stay', None, None))
        self.assertEqual(d.reason, 'a@acct.example is kept out of auto-switch, so Claude Code stays on it.')
        self.assertIsNone(d.hold)

    def test_at_or_over_a_line_or_refused_it_stays_says_which_and_is_held_like_no_room(self):
        tail = '. It is kept out of auto-switch, so Claude Code stays on it. Move by hand when you want to.'
        good = {'b': reading('b', five=5.0, week=5.0)}
        cases = [
            (dict(readings=dict(good, a=reading('a', five=92.0, week=20.0))),
             'a@acct.example 5-hour at 92% (line 90%)'),
            (dict(readings=dict(good, a=reading('a', five=100.0, week=20.0))),
             'a@acct.example 5-hour at 100% (line 90%)'),
            (dict(readings=dict(good, a=reading('a', five=10.0, week=95.0))),
             'a@acct.example weekly at 95% (line 95%)'),
            (dict(readings=dict(good, a=reading('a', five=10.0, week=10.0)), rate_limited=True),
             'a@acct.example is refusing requests now'),
            (dict(readings=dict(good, a=reading('a', five=10.0, week=10.0)), live_refused=True),
             "a@acct.example's login was refused"),
            (dict(readings=dict(good, a=reading('a', five=10.0, week=10.0)),
                  accounts=(account('a', 1, status='needs_login'), account('b', 2))),
             "a@acct.example's login was refused"),
        ]
        for kw, why in cases:
            with self.subTest(why=why):
                rs = kw.pop('readings')
                d = policy.decide(snap(rs, settings=ruled(a='out'), **kw))
                self.assertEqual(said(d), ('stay', 'forced', None))
                self.assertEqual(d.hold, 'kept out of auto-switch')
                self.assertEqual(d.reason, why + tail)

    def test_never_the_target_even_when_it_is_the_only_one_with_room(self):
        r = {'a': reading('a', five=92.0, week=20.0), 'b': reading('b', five=5.0, week=5.0),
             'c': reading('c', five=95.0, week=50.0)}
        d = policy.decide(snap(r, settings=ruled(b='out')))
        self.assertEqual(said(d), ('stay', 'forced', None))
        self.assertEqual(d.hold, 'no account has room')
        self.assertTrue(d.reason.startswith('a@acct.example 5-hour at 92% (line 90%); no other account has room '
                                            '(b@acct.example is kept out of auto-switch), so it stays. First refill: '),
                        d.reason)
        self.assertEqual(said(policy.decide(snap(r))), ('switch', 'forced', 'b'))      # with no rule: b

    def test_blocked_and_the_only_one_with_room_kept_out_every_account_is_out(self):
        r = {'a': reading('a', five=100.0, week=20.0), 'b': reading('b', five=5.0, week=5.0),
             'c': reading('c', five=97.0, week=50.0)}
        d = policy.decide(snap(r, settings=ruled(b='out')))
        self.assertEqual((said(d), d.hold), (('stay', 'forced', None), 'every account is out'))
        self.assertTrue(d.reason.startswith('Every account is out (b@acct.example is kept out of auto-switch). '
                                            'Back at '), d.reason)
        self.assertNotIn('b@acct.example, ', d.reason)               # the first refill is never a kept-out one

    def test_every_other_account_kept_out(self):
        r = {'a': reading('a', five=92.0, week=20.0), 'b': reading('b', five=5.0, week=5.0),
             'c': reading('c', five=6.0, week=6.0)}
        d = policy.decide(snap(r, settings=ruled(b='out', c='out')))
        self.assertEqual((said(d), d.hold), (('stay', 'forced', None), 'no other account'))
        self.assertEqual(d.reason, 'a@acct.example 5-hour at 92% (line 90%), and there is no other account to switch '
                                   'to (b@acct.example and c@acct.example are kept out of auto-switch).')

    def test_every_account_kept_out(self):
        r = {'a': reading('a', five=100.0, week=20.0), 'b': reading('b', five=5.0, week=5.0),
             'c': reading('c', five=6.0, week=6.0)}
        d = policy.decide(snap(r, settings=ruled(a='out', b='out', c='out')))
        self.assertEqual((said(d), d.hold), (('stay', 'forced', None), 'kept out of auto-switch'))

    def test_a_kept_out_account_with_no_room_is_not_named(self):
        r = {'a': reading('a', five=92.0, week=20.0), 'b': reading('b', five=95.0, week=5.0),
             'c': reading('c', five=95.0, week=50.0)}
        d = policy.decide(snap(r, settings=ruled(b='out')))
        self.assertNotIn('kept out', d.reason)

    def test_it_is_never_waited_for_or_read_as_a_landing_place(self):
        r = {'a': reading('a', five=92.0, week=20.0), 'b': reading('b', five=5.0, week=5.0, age=2000)}
        s = snap(r, settings=ruled(b='out'), accounts=ACCTS[:2])
        self.assertEqual(policy.unread_candidates(s), [])
        self.assertEqual(policy.unread_candidates(snap(r, accounts=ACCTS[:2])), ['b'])
        self.assertEqual(policy.decide(s).hold, 'no other account')

    def test_the_first_refill_counts_only_accounts_auto_switch_may_use(self):
        r = {'a': reading('a', five=100.0, week=20.0, five_reset=NOW + 4 * H),
             'b': reading('b', five=100.0, week=5.0, five_reset=NOW + 1 * H),
             'c': reading('c', five=100.0, week=50.0, five_reset=NOW + 2 * H)}
        self.assertEqual(policy.earliest_refill(snap(r))[1], 'b@acct.example')
        self.assertEqual(policy.earliest_refill(snap(r, settings=ruled(b='out')))[1], 'c@acct.example')
        self.assertEqual(policy.earliest_refill(snap(r, settings=ruled(a='out', b='out', c='out')))[1],
                         'a@acct.example')                          # the account in use is counted even so

    def test_no_optional_switch_onto_it(self):
        r = {'a': reading('a', five=20.0, week=40.0, week_reset=NOW + 5 * D),
             'b': reading('b', five=10.0, week=50.0, week_reset=NOW + 2 * D),
             'c': reading('c', five=10.0, week=30.0, week_reset=NOW + 6 * D)}
        self.assertEqual(said(policy.decide(snap(r))), ('switch', 'optional', 'b'))
        self.assertEqual(said(policy.decide(snap(r, settings=ruled(b='out')))), ('stay', None, None))


class UsedLast(unittest.TestCase):
    def test_a_forced_switch_takes_one_that_is_not_last_first(self):
        r = {'a': reading('a', five=92.0, week=20.0), 'b': reading('b', five=10.0, week=60.0, week_reset=NOW + 4 * D),
             'c': reading('c', five=0.0, week=0.0, week_reset=NOW + 1 * D)}
        for strategy in policy.STRATEGIES:
            with self.subTest(strategy=strategy):
                self.assertEqual(said(policy.decide(snap(r, settings=ruled(strategy)))), ('switch', 'forced', 'c'))
                d = policy.decide(snap(r, settings=ruled(strategy, c='last')))
                self.assertEqual(said(d), ('switch', 'forced', 'b'))
                self.assertNotIn('used last', d.reason)

    def test_a_last_account_that_is_the_only_one_with_room_is_landed_on_and_says_so(self):
        r = {'a': reading('a', five=92.0, week=20.0), 'b': reading('b', five=95.0, week=60.0),
             'c': reading('c', five=5.0, week=5.0)}
        d = policy.decide(snap(r, settings=ruled(c='last')))
        self.assertEqual(said(d), ('switch', 'forced', 'c'))
        self.assertIn('; c@acct.example, used last, has room (85 points before its nearest line', d.reason)

    def test_two_last_accounts_keep_todays_order(self):
        accts = ACCTS + (account('d', 4),)
        r = {'a': reading('a', five=92.0, week=20.0), 'b': reading('b', five=10.0, week=10.0, week_reset=NOW + 2 * D),
             'c': reading('c', five=10.0, week=50.0, week_reset=NOW + 1 * D), 'd': reading('d', five=95.0, week=0.0)}
        self.assertEqual(policy.decide(snap(r, accounts=accts, settings=ruled(b='last', c='last'))).target, 'c')
        self.assertEqual(policy.decide(snap(r, accounts=accts, settings=ruled(strategy='most-room', b='last',
                                                                              c='last'))).target, 'b')
        self.assertEqual(policy.decide(snap(r, accounts=accts)).target, 'c')       # today's order, with no rule

    def test_a_last_account_with_room_comes_before_running_another_past_its_line(self):
        r = {'a': reading('a', five=100.0, week=20.0), 'b': reading('b', five=93.0, week=10.0),
             'c': reading('c', five=5.0, week=5.0)}
        self.assertEqual(said(policy.decide(snap(r))), ('switch', 'forced', 'c'))
        d = policy.decide(snap(r, settings=ruled(c='last')))
        self.assertEqual(said(d), ('switch', 'forced', 'c'))        # b could still work, past its line: not before c
        self.assertEqual(d.reason, 'a@acct.example 5-hour at 100% (line 90%); c@acct.example, used last, has room (85 '
                                   'points before its nearest line, weekly refills Fri 6 AM)')

    def test_the_escape_takes_one_that_is_not_last_before_a_last_one(self):
        r = {'a': reading('a', five=100.0, week=20.0), 'b': reading('b', five=93.0, week=10.0),
             'c': reading('c', five=92.0, week=5.0)}
        self.assertEqual(said(policy.decide(snap(r))), ('switch', 'forced', 'c'))       # more raw room: c, today
        d = policy.decide(snap(r, settings=ruled(c='last')))
        self.assertEqual(said(d), ('switch', 'forced', 'b'))
        self.assertEqual(d.reason, 'a@acct.example 5-hour at 100% (line 90%); no account is under its lines, but '
                                   'b@acct.example can still work (5-hour 93%, weekly 10%)')

    def test_an_escape_onto_a_last_account_says_so(self):
        r = {'a': reading('a', five=100.0, week=20.0), 'b': reading('b', five=97.0, week=10.0),
             'c': reading('c', five=93.0, week=5.0)}
        d = policy.decide(snap(r, settings=ruled(c='last')))
        self.assertEqual(said(d), ('switch', 'forced', 'c'))
        self.assertIn('no account is under its lines, but c@acct.example, used last, can still work', d.reason)

    def test_an_optional_switch_never_goes_to_a_last_account(self):
        r = {'a': reading('a', five=20.0, week=40.0, week_reset=NOW + 5 * D),
             'b': reading('b', five=10.0, week=50.0, week_reset=NOW + 2 * D),
             'c': reading('c', five=10.0, week=30.0, week_reset=NOW + 3 * D)}
        self.assertEqual(said(policy.decide(snap(r))), ('switch', 'optional', 'b'))
        self.assertEqual(said(policy.decide(snap(r, settings=ruled(b='last')))), ('switch', 'optional', 'c'))
        d = policy.decide(snap(r, settings=ruled(b='last', c='last')))
        self.assertEqual(said(d), ('stay', None, None))
        d = policy.decide(snap(r, busy=True, settings=ruled(b='last')))
        self.assertEqual(said(d), ('stay', 'optional', 'c'))                         # a held one names c, never b

    def test_on_a_last_account_an_early_move_needs_no_refill_gain(self):
        r = {'a': reading('a', five=20.0, week=50.0, week_reset=NOW + 1 * D),
             'b': reading('b', five=10.0, week=60.0, week_reset=NOW + 4 * D),
             'c': reading('c', five=10.0, week=80.0, week_reset=NOW + 6 * D)}
        self.assertEqual(said(policy.decide(snap(r))), ('stay', None, None))          # a refills first: today, stays
        d = policy.decide(snap(r, settings=ruled(a='last')))
        self.assertEqual(said(d), ('switch', 'optional', 'b'))
        self.assertEqual(d.reason, 'a@acct.example is used last, and b@acct.example has room (35 points before its '
                                   'nearest line)')

    def test_on_a_last_account_the_best_by_todays_ranking_goes_first(self):
        r = {'a': reading('a', five=20.0, week=50.0, week_reset=NOW + 1 * D),
             'b': reading('b', five=10.0, week=60.0, week_reset=NOW + 4 * D),
             'c': reading('c', five=10.0, week=70.0, week_reset=NOW + 2 * D)}
        self.assertEqual(policy.decide(snap(r, settings=ruled(a='last'))).target, 'c')     # refills sooner
        r['c'] = reading('c', five=10.0, week=70.0, week_reset=NOW + 2 * D, age=700)     # not fresh: ready ones first
        self.assertEqual(policy.decide(snap(r, settings=ruled(a='last'))).target, 'b')

    def test_on_a_last_account_every_hold_still_holds(self):
        r = {'a': reading('a', five=20.0, week=50.0, week_reset=NOW + 1 * D),
             'b': reading('b', five=10.0, week=60.0, week_reset=NOW + 4 * D)}
        accts = ACCTS[:2]
        for kw, words in ((dict(busy=True), 'Claude Code is working'), (dict(since_switch_s=60.0), 'the last switch'),
                          (dict(optional_recent=2), '2 optional switches'), (dict(battery_pct=10.0, on_ac=False),
                                                                             'battery'),
                          (dict(no_optional='automatic switching was just restarted'), 'restarted')):
            with self.subTest(hold=words):
                d = policy.decide(snap(r, accounts=accts, settings=ruled(a='last'), **kw))
                self.assertEqual(said(d), ('stay', 'optional', 'b'))
                self.assertIn(words, d.hold)
                self.assertTrue(d.reason.startswith('Next: b@acct.example; waits: '), d.reason)

    def test_on_a_last_account_with_no_room_elsewhere_it_stays_and_most_room_never_moves_early(self):
        r = {'a': reading('a', five=20.0, week=50.0, week_reset=NOW + 1 * D),
             'b': reading('b', five=10.0, week=85.1, week_reset=NOW + 4 * D),
             'c': reading('c', five=10.0, week=0.0, week_reset=NOW + 4 * D)}
        d = policy.decide(snap(r, settings=ruled(a='last', c='last')))
        self.assertEqual(said(d), ('stay', None, None))
        self.assertEqual(d.reason, 'a@acct.example is used last, and no account used before it has room now.')
        d = policy.decide(snap(r, settings=ruled('most-room', a='last')))
        self.assertEqual((said(d), d.reason), (('stay', None, None), 'a@acct.example is under its lines.'))


class Need(unittest.TestCase):
    def test_never_onto_or_away_from_a_kept_out_account_and_a_last_one_is_fine(self):
        r = {'a': reading('a', five=20.0, week=20.0), 'b': reading('b', five=10.0, week=50.0),
             'c': reading('c', five=10.0, week=50.0)}
        d = policy.need(snap(r, settings=ruled(b='out')), 'b', 'Claude in Chrome is signed in as b')
        self.assertEqual(said(d), ('stay', 'need', 'b'))
        self.assertEqual(d.reason, 'b@acct.example is kept out of auto-switch, so Claude Code is not moved there.')
        d = policy.need(snap(r, settings=ruled(a='out')), 'c', 'Claude in Chrome is signed in as c')
        self.assertEqual(said(d), ('stay', 'need', 'c'))
        self.assertEqual(d.reason, 'a@acct.example is kept out of auto-switch, so Claude Code stays on it.')
        self.assertEqual(said(policy.need(snap(r, settings=ruled(b='last')), 'b', 'x')), ('switch', 'need', 'b'))


class Together(unittest.TestCase):
    def test_a_rule_for_an_account_that_was_removed_changes_nothing(self):
        r = {'a': reading('a', five=92.0, week=20.0), 'b': reading('b', five=10.0, week=50.0)}
        for s in (snap(r), snap(r, busy=True)):
            gone = dataclasses.replace(s, settings=ruled(gone='out', ghost='last'))
            self.assertEqual(policy.decide(gone), policy.decide(s))
            self.assertEqual(policy.need(gone, 'b', 'x'), policy.need(s, 'b', 'x'))


# ---------- never, over many made-up snapshots ----------

STATUSES = ('ok', 'ok', 'ok', 'ok', 'expiring', 'needs_login', 'unreadable', 'claimed', 'conflict')
RULE_MIX = ('in', 'in', 'last', 'out')


def _window(rng, name, line_ish, calm, stuck=False):
    if calm or stuck:               # numbers a quiet pass reads: known, fresh, mostly under the lines (when the account
        pct = round(rng.uniform(0, line_ish - 2), 1)        # in use is stuck, often past a line but under 100)
        if stuck and rng.random() < 0.4:
            pct = round(rng.uniform(line_ish, 96), 1)
        return api.Window(name, pct, NOW + rng.randint(60, 7 * 24 * 60) * 60.0, NOW - rng.uniform(0, 300), 'endpoint')
    pct = rng.choice([None, 0.0, 5.0, line_ish - 6, line_ish - 4, line_ish, 97.0, 100.0,
                      round(rng.uniform(0, 100), 1)])
    reset = rng.choice([None, NOW - 60, NOW + rng.randint(1, 7 * 24 * 60) * 60.0])
    at = NOW - rng.choice([0.0, 30.0, 500.0, 700.0, 1000.0, -100.0])
    return api.Window(name, pct, reset, at, rng.choice(['endpoint', 'status_line']))


def generated(seed, count):
    """`count` made-up snapshots: 2 to 5 accounts in every status, readings of every shape (missing, unknown, old,
    future, frozen, over every line), both orders, a model or none, every rule in every mix, and the conditions that
    hold a switch. About a third are quiet passes (every account signed in, fresh numbers, nothing holding an early
    switch), so early switches are reached as often as the rest."""
    rng = random.Random(seed)
    for _ in range(count):
        calm = rng.random() < 0.35
        stuck = not calm and rng.random() < 0.25     # the account in use at 100%, the others near their lines
        ids = ['g%d' % k for k in range(rng.randint(2, 5))]
        accts = tuple(account(x, k + 1, status='ok' if calm or stuck else rng.choice(STATUSES))
                      for k, x in enumerate(ids))
        model = rng.choice([None, None, 'Fable'])
        readings = {}
        for x in ids:
            if not (calm or stuck) and rng.random() < 0.08:
                continue
            ws = [w for w in (_window(rng, '5-hour', 90.0, calm, stuck) if rng.random() < 0.9 else None,
                              _window(rng, 'weekly', 95.0, calm, stuck),
                              _window(rng, 'model:Fable', 95.0, calm, stuck) if rng.random() < 0.5 else None)
                  if w is not None]
            readings[x] = api.Reading(x, tuple(ws), min(w.read_at for w in ws), 'endpoint',
                                      frozen=not calm and rng.random() < 0.1)
        rules = dict((x, r) for x, r in ((x, rng.choice(RULE_MIX)) for x in ids) if r != 'in')
        if rng.random() < 0.1:
            rules['removed-account'] = rng.choice(policy.RULES)
        settings = policy.Settings(enabled=calm or rng.random() < 0.95, strategy=rng.choice(policy.STRATEGIES),
                                   model=model, rules=tuple(sorted(rules.items())))
        active = rng.choice(ids + ([None, 'not-saved'] if not (calm or stuck) and rng.random() < 0.05 else []))
        if stuck:
            readings[active] = api.Reading(active, (api.Window('weekly', 100.0, NOW + 3 * D, NOW - 30, 'endpoint'),),
                                           NOW - 30, 'endpoint')
            yield policy.Snapshot(now=NOW, accounts=accts, readings=readings, settings=settings, active=active, **CALM)
            continue
        if calm:
            yield policy.Snapshot(now=NOW, accounts=accts, readings=readings, settings=settings, active=active,
                                  **dict(CALM, busy=rng.random() < 0.1))
            continue
        yield policy.Snapshot(
            now=NOW, accounts=accts, readings=readings, settings=settings, active=active,
            live_ok=rng.random() < 0.95, disagree=rng.random() < 0.05, busy=rng.random() < 0.2,
            idle_for_s=rng.choice([None, 0.0, 200.0, 1800.0]), since_switch_s=rng.choice([None, 60.0, 4 * H]),
            optional_recent=rng.choice([0, 0, 1, 2]), load_per_core=rng.choice([None, 0.5, 6.0]),
            battery_pct=rng.choice([None, 90.0, 10.0]), on_ac=rng.choice([None, True, False]),
            settling=rng.random() < 0.03, offline=rng.random() < 0.03, rate_limited=rng.random() < 0.1,
            claimed=frozenset(x for x in ids if rng.random() < 0.05), live_refused=rng.random() < 0.05,
            no_optional=rng.choice([None] * 19 + ['automatic switching was just restarted']),
            obligations=({'kind': 'doc', 'url': 'u', 'at': NOW - 60},) if rng.random() < 0.05 else ())


SEED, COUNT = 1124, 4000


def landed(s, d):
    """A forced switch onto an account with room under every line (not the escape past a line). A move on older
    numbers (policy._on_older_numbers: Claude Code stopped and no account read fresh) is neither a landing nor the
    escape, so these checks leave it out; its own rules are checked in test_heal_forced_on_older_numbers."""
    if d.action != 'switch' or d.kind != 'forced' or policy.on_older_numbers(d, s):
        return False
    a = next(x for x in s.accounts if x.id == d.target)
    v = policy.view(a, s.readings.get(a.id), s.settings, s.now)
    return v.room is not None and v.room >= policy.LAND_MARGIN


def escaped(s, d):
    return d.action == 'switch' and d.kind == 'forced' and not landed(s, d) and not policy.on_older_numbers(d, s)


class NeverOverManySnapshots(unittest.TestCase):
    def test_never(self):
        seen = dict.fromkeys(('named', 'out stays', 'onto last', 'escape onto last', 'last before an escape', 'forced',
                              'optional', 'held optional', 'off last', 'need', 'need to last', 'no rule'), 0)
        for n, s in enumerate(generated(SEED, COUNT)):
            rule = lambda x: policy.rule_of(s.settings, x)          # noqa: E731
            d = policy.decide(s)
            where = (n, s.active, s.settings.rules, d)
            if d.target is not None:                    # a switch, or a held "Next: X; waits", which the page shows
                self.assertNotEqual(rule(d.target), 'out', where)                                   # (a)
                seen['named'] += 1
            if d.action == 'switch':
                self.assertNotEqual(rule(s.active), 'out', where)                                   # (b)
                seen[d.kind] += 1
            if rule(s.active) == 'out':
                self.assertEqual(d.action, 'stay', where)                                           # (b)
                seen['out stays'] += 1
            if d.kind == 'optional' and d.target is not None:
                self.assertNotEqual(rule(d.target), 'last', where)                                  # (d)
                if rule(s.active) == 'last':
                    seen['off last'] += 1
                if d.action == 'stay':
                    seen['held optional'] += 1
            last = frozenset(a.id for a in s.accounts if a.id != s.active and rule(a.id) == 'last')
            if last:                                                                                # (c)
                fewer = policy.decide(dataclasses.replace(s, claimed=s.claimed | last))
                unmarked = policy.decide(dataclasses.replace(s, settings=dataclasses.replace(
                    s.settings, rules=tuple((x, r) for x, r in s.settings.rules if r != 'last'))))
                if landed(s, d) and d.target in last:          # a landing on a last account: none elsewhere
                    self.assertFalse(landed(s, fewer), (where, fewer))
                    seen['onto last'] += 1
                if landed(s, fewer):                           # a landing without the last ones: the same one
                    self.assertEqual(said(d), said(fewer), (where, fewer))
                if escaped(s, d) and d.target in last:         # an escape onto a last account: nothing else at all
                    # (on fresh numbers: without the last ones nothing may be fresh, and then a stopped account moves
                    # on older numbers, which only happens when no account was read fresh)
                    self.assertFalse(fewer.action == 'switch' and not policy.on_older_numbers(fewer, s), (where, fewer))
                    seen['escape onto last'] += 1
                if escaped(s, d):                              # no escape while any account can be landed on
                    self.assertFalse(landed(s, unmarked), (where, unmarked))
                if landed(s, unmarked):                        # the marks taken off would land: so does the full one
                    self.assertTrue(landed(s, d), (where, unmarked))
                if escaped(s, fewer):                          # an escape without the last ones is taken only when
                    self.assertTrue(escaped(s, d) or landed(s, d), (where, fewer))      # no last one has room
                    if landed(s, d):
                        seen['last before an escape'] += 1
                    else:
                        self.assertEqual(said(d), said(fewer), (where, fewer))
            if not any(a.id == x for a in s.accounts for x, _ in s.settings.rules):                 # (e)
                bare = dataclasses.replace(s, settings=dataclasses.replace(s.settings, rules=()))
                self.assertEqual(d, policy.decide(bare), where)
                seen['no rule'] += 1
            for a in s.accounts:                                                                    # need: (a), (b)
                nd = policy.need(s, a.id, 'something needs it')
                if nd.action == 'switch':
                    self.assertNotEqual(rule(nd.target), 'out', (where, nd))
                    self.assertNotEqual(rule(s.active), 'out', (where, nd))
                    seen['need'] += 1
                    seen['need to last'] += rule(nd.target) == 'last'
        for k, v in seen.items():                       # the snapshots really reach every case they check
            self.assertGreater(v, 5, (k, seen))


# ---------- plan.py agrees ----------

def agreeing(seed, count):
    """Snapshots both modules judge alike: every account signed in, numbers fresh and not frozen, the account in use
    over a line but not blocked (no escape: plan.py never lands over a line), refills and room never tied."""
    rng = random.Random(seed)
    for _ in range(count):
        ids = ['p%d' % k for k in range(rng.randint(2, 5))]
        accts = tuple(account(x, k + 1) for k, x in enumerate(ids))
        days = rng.sample(range(1, 7 * 24), len(ids))
        rooms = rng.sample(range(0, 95), len(ids))
        readings, planned = {}, []
        for k, x in enumerate(ids):
            week = float(95 - rooms[k]) if k else float(rng.randint(10, 80))
            five = float(rng.randint(90, 99)) if k == 0 else float(rng.choice([rng.randint(0, 84), 88, 92]))
            wr = NOW + days[k] * H
            readings[x] = api.Reading(x, (api.Window('5-hour', five, NOW + 3 * H), api.Window('weekly', week, wr)),
                                      NOW - rng.uniform(0, 600), 'endpoint')
            planned.append({'name': x, 'weekly_used_pct': week, 'weekly_resets_at': wr, 'five_hour_used_pct': five,
                            'five_hour_resets_at': NOW + 3 * H, 'scoped': [], 'active': k == 0})
        rules = dict((x, r) for x, r in ((x, rng.choice(RULE_MIX)) for x in ids) if r != 'in')
        for a in planned:
            if a['name'] in rules:
                a['rule'] = rules[a['name']]
        strategy = rng.choice(policy.STRATEGIES)
        settings = policy.Settings(enabled=True, strategy=strategy, rules=tuple(sorted(rules.items())))
        s = policy.Snapshot(now=NOW, accounts=accts, readings=readings, active=ids[0], settings=settings, **CALM)
        yield s, planned, {'strategy': strategy, 'session_line': 90, 'weekly_line': 95, 'margin': policy.LAND_MARGIN}


def widely(seed, count):
    """The cases agreeing() leaves out, numbers built the same way: the account in use blocked (weekly at 100%: a
    landing, or the escape past a line that plan.py never models), under its lines, or over one; kept-out and used-last
    accounts, with room and without, side by side. Yields (mode, snapshot, plan's accounts, plan's settings)."""
    rng = random.Random(seed)
    for _ in range(count):
        mode = rng.choice(('blocked', 'under', 'over'))
        ids = ['w%d' % k for k in range(rng.randint(2, 5))]
        accts = tuple(account(x, k + 1) for k, x in enumerate(ids))
        days = rng.sample(range(1, 7 * 24), len(ids))
        rooms = rng.sample(range(0, 95), len(ids))
        readings, planned = {}, []
        for k, x in enumerate(ids):
            if k == 0:
                week = 100.0 if mode == 'blocked' else float(rng.randint(10, 60))
                five = float(rng.randint(90, 99)) if mode == 'over' else float(rng.randint(10, 60))
            else:
                week = float(95 - rooms[k])
                five = float(rng.choice([rng.randint(0, 84), 88, 92]))
            wr = NOW + days[k] * H
            readings[x] = api.Reading(x, (api.Window('5-hour', five, NOW + 3 * H), api.Window('weekly', week, wr)),
                                      NOW - rng.uniform(0, 600), 'endpoint')
            planned.append({'name': x, 'weekly_used_pct': week, 'weekly_resets_at': wr, 'five_hour_used_pct': five,
                            'five_hour_resets_at': NOW + 3 * H, 'scoped': [], 'active': k == 0})
        rules = dict((x, r) for x, r in ((x, rng.choice(('in', 'last', 'out') if k else RULE_MIX))
                                         for k, x in enumerate(ids)) if r != 'in')
        for a in planned:
            if a['name'] in rules:
                a['rule'] = rules[a['name']]
        strategy = rng.choice(policy.STRATEGIES)
        settings = policy.Settings(enabled=True, strategy=strategy, rules=tuple(sorted(rules.items())))
        s = policy.Snapshot(now=NOW, accounts=accts, readings=readings, active=ids[0], settings=settings, **CALM)
        yield mode, s, planned, {'strategy': strategy, 'session_line': 90, 'weekly_line': 95,
                                 'margin': policy.LAND_MARGIN}


class PlanAgrees(unittest.TestCase):
    def test_a_kept_out_account_is_no_landing_place_and_one_used_last_comes_after(self):
        given = [{'name': 'in-use', 'weekly_used_pct': 96, 'weekly_resets_at': NOW + D, 'active': True},
                 {'name': 'out', 'weekly_used_pct': 5, 'weekly_resets_at': NOW + 2 * D, 'rule': 'out'},
                 {'name': 'last', 'weekly_used_pct': 5, 'weekly_resets_at': NOW + 2 * D, 'rule': 'last'},
                 {'name': 'plain', 'weekly_used_pct': 60, 'weekly_resets_at': NOW + 6 * D}]
        ranked = dict((a['name'], a) for a in plan.order(given, {}, NOW))
        self.assertEqual((ranked['out']['usable'], ranked['out']['reason']), (False, 'kept out of auto-switch'))
        self.assertLess(ranked['plain']['rank'], ranked['last']['rank'])
        self.assertEqual(plan.next_account(given, {}, NOW)['account']['name'], 'plain')
        given[3]['weekly_used_pct'] = 93
        self.assertEqual(plan.next_account(given, {}, NOW)['account']['name'], 'last')
        given[2]['rule'] = 'out'
        self.assertEqual(plan.next_account(given, {}, NOW)['reason'], 'no other account has room')

    def test_on_a_kept_out_account_nothing_is_next(self):
        given = [{'name': 'in-use', 'weekly_used_pct': 96, 'weekly_resets_at': NOW + D, 'active': True, 'rule': 'out'},
                 {'name': 'plain', 'weekly_used_pct': 5, 'weekly_resets_at': NOW + 6 * D}]
        self.assertEqual(plan.next_account(given, {}, NOW), {'account': None, 'reason': 'kept out of auto-switch',
                                                             'back_at': None})

    def test_with_no_rule_the_order_is_todays(self):
        given = [{'name': n, 'weekly_used_pct': u, 'weekly_resets_at': NOW + d * D, 'active': n == 'a'}
                 for n, u, d in (('a', 50, 1), ('b', 96, 2), ('c', 20, 3), ('d', 10, 5))]
        with_in = [dict(a, rule='in') for a in given]
        for strategy in plan.STRATEGIES:
            today = plan.order(given, {'strategy': strategy}, NOW)
            self.assertEqual([dict((k, v) for k, v in x.items() if k != 'rule')
                              for x in plan.order(with_in, {'strategy': strategy}, NOW)], today)
            self.assertEqual(plan.next_account(given, {'strategy': strategy}, NOW)['account']['name'],
                             'd' if strategy == 'most-room' else 'c')

    def test_the_same_answer_as_the_policy(self):
        compared = stays = 0
        for n, (s, planned, lines) in enumerate(agreeing(77, 2000)):
            d = policy.decide(s)
            nxt = plan.next_account(planned, lines, NOW)
            where = (n, s.settings, d, nxt)
            if policy.rule_of(s.settings, s.active) == 'out':
                self.assertEqual((d.action, nxt['account'], nxt['reason']), ('stay', None, 'kept out of auto-switch'),
                                 where)
                continue
            self.assertEqual(d.kind, 'forced', where)
            if d.action == 'switch':
                self.assertIsNotNone(nxt['account'], where)
                self.assertEqual(d.target, nxt['account']['name'], where)
                compared += 1
            else:
                self.assertIsNone(nxt['account'], where)
                stays += 1
        self.assertGreater(compared, 500)
        self.assertGreater(stays, 50)

    def test_the_same_answer_as_the_policy_with_the_account_in_use_blocked_or_under_its_lines(self):
        """Next is where a forced switch goes. The account in use blocked: the policy's landing is Next, and its escape
        past a line (plan.py never models one) is no Next. Under its lines: Next is where the policy goes once the
        account in use is over a line (the same snapshot with its 5-hour at 95%)."""
        seen = dict.fromkeys(('blocked', 'escape', 'under', 'over', 'stay', 'last while one kept out has room', 'out'),
                             0)
        for n, (mode, s, planned, lines) in enumerate(widely(78, 3000)):
            rule = lambda x: policy.rule_of(s.settings, x)          # noqa: E731
            nxt = plan.next_account(planned, lines, NOW)
            if rule(s.active) == 'out':
                d = policy.decide(s)
                self.assertEqual((d.action, nxt['account'], nxt['reason']), ('stay', None, 'kept out of auto-switch'),
                                 (n, mode, s.settings, d, nxt))
                seen['out'] += 1
                continue
            if mode == 'under':
                r = s.readings[s.active]
                over = dataclasses.replace(r, windows=(api.Window('5-hour', 95.0, NOW + 3 * H),) + r.windows[1:])
                d = policy.decide(dataclasses.replace(s, readings=dict(s.readings, **{s.active: over})))
            else:
                d = policy.decide(s)
            where = (n, mode, s.settings, d, nxt)
            self.assertEqual(d.kind, 'forced', where)
            if escaped(s, d):
                self.assertIsNone(nxt['account'], where)
                seen['escape'] += 1
            elif d.action == 'switch':
                self.assertIsNotNone(nxt['account'], where)
                self.assertEqual(d.target, nxt['account']['name'], where)
                seen[mode] += 1
                if rule(d.target) == 'last' and any(
                        rule(a.id) == 'out' and a.id != s.active and
                        policy.view(a, s.readings[a.id], s.settings, NOW).room >= policy.LAND_MARGIN
                        for a in s.accounts):
                    seen['last while one kept out has room'] += 1
            else:
                self.assertIsNone(nxt['account'], where)
                seen['stay'] += 1
        for k, v in seen.items():
            self.assertGreater(v, 5, (k, seen))

    def test_on_a_used_last_account_next_can_name_one_the_early_move_waits_for(self):
        # Next keeps today's meaning, where a forced switch would go (5 points under every line). An early move off a
        # used-last account needs 10, so with 5 to 10 points elsewhere Next names that account and the policy stays.
        given = [{'name': 'a', 'weekly_used_pct': 50, 'five_hour_used_pct': 20, 'weekly_resets_at': NOW + D,
                  'active': True, 'rule': 'last'},
                 {'name': 'b', 'weekly_used_pct': 88, 'five_hour_used_pct': 10, 'weekly_resets_at': NOW + 4 * D}]
        self.assertEqual(plan.next_account(given, {}, NOW)['account']['name'], 'b')
        r = {'a': reading('a', five=20.0, week=50.0, week_reset=NOW + 1 * D),
             'b': reading('b', five=10.0, week=88.0, week_reset=NOW + 4 * D)}
        d = policy.decide(snap(r, accounts=ACCTS[:2], settings=ruled(a='last')))
        self.assertEqual((said(d), d.reason), (('stay', None, None),
                                               'a@acct.example is used last, and no account used before it has room now.'))

    def test_the_account_in_use_keeps_its_place_in_the_order_whatever_its_rule(self):
        for used in (50, 96):                   # under its lines: first; over one: by when it is back, as today
            given = [{'name': 'b', 'weekly_used_pct': 20, 'weekly_resets_at': NOW + 3 * D},
                     {'name': 'in-use', 'weekly_used_pct': used, 'weekly_resets_at': NOW + D, 'active': True},
                     {'name': 'c', 'weekly_used_pct': 97, 'weekly_resets_at': NOW + 5 * D}]
            today = plan.order(given, {}, NOW)
            for rule in ('out', 'last'):
                with self.subTest(used=used, rule=rule):
                    got = plan.order([dict(a, rule=rule) if a.get('active') else a for a in given], {}, NOW)
                    self.assertEqual([dict((k, v) for k, v in j.items() if k != 'rule') for j in got], today)
            self.assertEqual([j['name'] for j in today], ['in-use', 'b', 'c'] if used == 50 else ['b', 'in-use', 'c'])


if __name__ == '__main__':
    unittest.main()
