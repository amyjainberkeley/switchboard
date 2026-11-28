import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# Finer rules for auto-switch, in the policy: an account's own switch lines, ranks (1 is used first), the order that
# stays on the account in use until a line and then lands on what refills first, several models' weeks, and the best
# landing asked for by hand (policy.best). policy.decide is pure, so every rule is a snapshot and an answer. Beside the
# examples, checks over many made-up snapshots from fixed seeds: with none of these set every answer is the one it was,
# and with them set no rule is ever broken. Made-up accounts only.
import dataclasses
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard.accounts import api, policy
from fakes_autoswitch import T0, account
from test_autoswitch_policy import ACCTS, CALM, reading, snap

H, D = 3600.0, 86400.0
NOW = T0
OLD_ORDERS = ('refills-first', 'most-room')


def on(**kw):
    return policy.Settings(enabled=True, **kw)


def own(**lines):
    """Settings.own from {account id: {'session_at': N, 'weekly_at': N}}."""
    return policy.own_from(lines)


def ranks(**r):
    return tuple(sorted(r.items()))


def said(d):
    return (d.action, d.kind, d.target)


class TheSetting(unittest.TestCase):
    def test_read_from_the_stored_settings(self):
        s = policy.settings_from({'own_lines': {'a': {'session_at': 80, 'weekly_at': 90.5}, 'b': {'weekly_at': 70}},
                                  'ranks': {'a': 2, 'b': 1}, 'model': ['Fable', 'Opus 5']})
        self.assertEqual(s.own, (('a', (('session_at', 80.0), ('weekly_at', 90.5))), ('b', (('weekly_at', 70.0),))))
        self.assertEqual(s.ranks, (('a', 2), ('b', 1)))
        self.assertEqual((s.model, s.models), ('Fable', ('Fable', 'Opus 5')))
        self.assertEqual(policy.own_of(s, 'b'), {'weekly_at': 70.0})
        self.assertEqual((policy.rank_of(s, 'b'), policy.rank_of(s, 'c')), (1, None))
        again = {'model': ['Fable', 'Opus 5'], 'ranks': {'b': 1, 'a': 2},
                 'own_lines': {'b': {'weekly_at': 70}, 'a': {'weekly_at': 90.5, 'session_at': 80}}}
        self.assertEqual(hash(s), hash(policy.settings_from(again)))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            s.ranks = ()

    def test_damaged_values_read_as_not_set(self):
        for junk in ([], 'a', 7, None, {'a': 80}, {'a': {'session_at': 120}}, {'a': {'session_at': 49.9}},
                     {'a': {'session_at': True}}, {'a': {'five': 80}}, {'': {'session_at': 80}},
                     {'a': {'session_at': float('nan')}}, {'a': {'session_at': float('inf')}},
                     {'a': {'session_at': '80'}}, {'a': {'model_at': 80}}, {7: {'session_at': 80}}):
            with self.subTest(own_lines=junk):
                self.assertEqual(policy.settings_from({'own_lines': junk}).own, ())
        for junk in ([], 'a', None, {'a': 0}, {'a': 100}, {'a': 1.5}, {'a': 1.0}, {'a': True}, {'a': '1'},
                     {'': 1}, {'a': -1}, {7: 1}):
            with self.subTest(ranks=junk):
                self.assertEqual(policy.settings_from({'ranks': junk}).ranks, ())
        s = policy.settings_from({'own_lines': {'a': {'session_at': 120, 'weekly_at': 80}}})
        self.assertEqual(s.own, (('a', (('weekly_at', 80.0),)),))     # the good line of the two is kept

    def test_one_model_is_stored_and_read_as_before(self):
        for stored, model in (('Fable', 'Fable'), ('  Fable  5.2 ', 'Fable 5.2'), (['Fable'], 'Fable'),
                              (['Fable', 'fable '], 'Fable'), (7, None), ([7, ''], None), (['  '], None), ('', None),
                              (None, None)):
            with self.subTest(stored=stored):
                s = policy.settings_from({'model': stored})
                self.assertEqual((s.model, s.models), (model, ()))
                self.assertEqual(s, policy.Settings(model=model))

    def test_nothing_new_set_is_the_settings_as_they_were(self):
        d = {'enabled': True, 'session_at': 85, 'weekly_at': 93, 'model': 'Fable', 'strategy': 'most-room',
             'rules': {'a': 'last'}}
        self.assertEqual(policy.settings_from(d), policy.Settings(enabled=True, session_at=85.0, weekly_at=93.0,
                                                                  model='Fable', strategy='most-room',
                                                                  rules=(('a', 'last'),)))

    def test_the_three_orders(self):
        self.assertEqual(policy.STRATEGIES, ('refills-first', 'most-room', 'stay-then-refills'))
        self.assertEqual(policy.settings_from({'strategy': 'stay-then-refills'}).strategy, 'stay-then-refills')


class OwnLines(unittest.TestCase):
    def test_a_switch_goes_at_the_accounts_own_line(self):
        r = {'a': reading('a', five=82.0, week=20.0), 'b': reading('b', five=5.0, week=5.0)}
        self.assertEqual(said(policy.decide(snap(r))), ('stay', None, None))
        d = policy.decide(snap(r, settings=on(own=own(a={'session_at': 80}))))
        self.assertEqual(said(d), ('switch', 'forced', 'b'))
        self.assertTrue(d.reason.startswith('a@acct.example 5-hour at 82% (its own line, 80%); b@acct.example has '
                                            'room'), d.reason)

    def test_an_accounts_own_line_is_its_alone(self):
        r = {'a': reading('a', five=82.0, week=20.0), 'b': reading('b', five=82.0, week=5.0)}
        d = policy.decide(snap(r, accounts=ACCTS[:2], settings=on(own=own(b={'session_at': 80}))))
        self.assertEqual(said(d), ('stay', None, None))             # a is judged by the shared 90

    def test_it_never_lands_within_the_margin_of_the_targets_own_line(self):
        r = {'a': reading('a', five=92.0, week=20.0),
             'b': reading('b', five=10.0, week=66.0, week_reset=NOW + 1 * D),
             'c': reading('c', five=10.0, week=50.0, week_reset=NOW + 3 * D)}
        self.assertEqual(policy.decide(snap(r)).target, 'b')
        self.assertEqual(policy.decide(snap(r, settings=on(own=own(b={'weekly_at': 70})))).target, 'c')
        self.assertEqual(policy.decide(snap(r, settings=on(own=own(b={'weekly_at': 71})))).target, 'b')

    def test_a_higher_own_line_makes_room(self):
        r = {'a': reading('a', five=92.0, week=20.0), 'b': reading('b', five=92.0, week=5.0)}
        accts = ACCTS[:2]
        self.assertEqual(policy.decide(snap(r, accounts=accts)).hold, 'no account has room')
        d = policy.decide(snap(r, accounts=accts, settings=on(own=own(b={'session_at': 98}))))
        self.assertEqual(said(d), ('switch', 'forced', 'b'))

    def test_a_need_switch_and_the_first_refill_read_the_own_line(self):
        r = {'a': reading('a', five=10.0, week=20.0), 'b': reading('b', five=10.0, week=68.0)}
        s = on(own=own(b={'weekly_at': 70}))
        self.assertEqual(said(policy.need(snap(r), 'b')), ('switch', 'need', 'b'))
        d = policy.need(snap(r, settings=s), 'b')
        self.assertEqual((said(d), d.reason), (('stay', 'need', 'b'),
                                               'b@acct.example has no room (weekly at 68% (its own line, 70%)).'))
        r = {'a': reading('a', five=100.0, week=20.0, five_reset=NOW + 2 * H),
             'b': reading('b', five=10.0, week=75.0, week_reset=NOW + 1 * H)}
        self.assertEqual(policy.earliest_refill(snap(r, settings=s))[1:], ('b@acct.example', 'weekly'))

    def test_an_optional_switch_needs_its_room_under_the_own_line(self):
        r = {'a': reading('a', five=20.0, week=40.0, week_reset=NOW + 5 * D),
             'b': reading('b', five=10.0, week=60.0, week_reset=NOW + 2 * D)}
        self.assertEqual(said(policy.decide(snap(r, accounts=ACCTS[:2]))), ('switch', 'optional', 'b'))
        d = policy.decide(snap(r, accounts=ACCTS[:2], settings=on(own=own(b={'weekly_at': 68}))))
        self.assertEqual(said(d), ('stay', None, None))

    def test_the_model_line_stays_shared(self):
        s = policy.settings_from({'model': 'Fable', 'own_lines': {'a': {'model_at': 60}}})
        self.assertEqual(s.own, ())
        r = {'a': reading('a', five=10.0, week=10.0, fable=70.0), 'b': reading('b', five=5.0, week=5.0, fable=5.0)}
        self.assertEqual(policy.decide(snap(r, settings=dataclasses.replace(s, enabled=True))).action, 'stay')


class Ranks(unittest.TestCase):
    R = {'a': reading('a', five=92.0, week=20.0),
         'b': reading('b', five=10.0, week=60.0, week_reset=NOW + 1 * D),
         'c': reading('c', five=10.0, week=20.0, week_reset=NOW + 3 * D)}

    def test_a_lower_rank_always_wins(self):
        self.assertEqual(policy.decide(snap(self.R)).target, 'b')                 # refills first
        for strategy in policy.STRATEGIES:
            with self.subTest(strategy=strategy):
                d = policy.decide(snap(self.R, settings=on(strategy=strategy, ranks=ranks(b=2, c=1))))
                self.assertEqual(said(d), ('switch', 'forced', 'c'))

    def test_inside_one_rank_the_order_decides(self):
        for strategy, want in (('refills-first', 'b'), ('most-room', 'c'), ('stay-then-refills', 'b')):
            with self.subTest(strategy=strategy):
                d = policy.decide(snap(self.R, settings=on(strategy=strategy, ranks=ranks(b=1, c=1))))
                self.assertEqual(d.target, want)

    def test_an_account_with_no_rank_comes_after_every_ranked_one(self):
        self.assertEqual(policy.decide(snap(self.R, settings=on(ranks=ranks(c=9)))).target, 'c')
        self.assertEqual(policy.decide(snap(self.R, settings=on(ranks=ranks(a=1, c=9)))).target, 'c')

    def test_a_ranked_account_with_no_room_is_passed_over(self):
        r = dict(self.R, c=reading('c', five=88.0, week=20.0, week_reset=NOW + 3 * D))
        self.assertEqual(policy.decide(snap(r, settings=on(ranks=ranks(c=1)))).target, 'b')

    def test_used_last_comes_after_every_rank_and_kept_out_is_never_used(self):
        self.assertEqual(policy.decide(snap(self.R, settings=on(ranks=ranks(c=1), rules=(('c', 'last'),)))).target,
                         'b')
        d = policy.decide(snap(self.R, settings=on(ranks=ranks(c=1), rules=(('c', 'out'),))))
        self.assertEqual(d.target, 'b')
        d = policy.decide(snap(self.R, settings=on(ranks=ranks(b=2, c=1), rules=(('b', 'out'), ('c', 'out')))))
        self.assertEqual((d.action, d.hold), ('stay', 'no other account'))

    def test_an_optional_switch_never_goes_to_a_later_rank(self):
        r = {'a': reading('a', five=20.0, week=40.0, week_reset=NOW + 5 * D),
             'b': reading('b', five=10.0, week=60.0, week_reset=NOW + 2 * D)}
        accts = ACCTS[:2]
        self.assertEqual(said(policy.decide(snap(r, accounts=accts))), ('switch', 'optional', 'b'))
        for rk in (ranks(a=1, b=2), ranks(a=1)):
            with self.subTest(ranks=rk):
                self.assertEqual(said(policy.decide(snap(r, accounts=accts, settings=on(ranks=rk)))),
                                 ('stay', None, None))
        self.assertEqual(said(policy.decide(snap(r, accounts=accts, settings=on(ranks=ranks(a=1, b=1))))),
                         ('switch', 'optional', 'b'))

    def test_an_earlier_rank_with_room_is_an_early_move_under_every_hold(self):
        r = {'a': reading('a', five=20.0, week=40.0, week_reset=NOW + 1 * D),
             'b': reading('b', five=10.0, week=60.0, week_reset=NOW + 5 * D)}
        accts = ACCTS[:2]
        self.assertEqual(said(policy.decide(snap(r, accounts=accts))), ('stay', None, None))
        d = policy.decide(snap(r, accounts=accts, settings=on(ranks=ranks(a=2, b=1))))
        self.assertEqual(said(d), ('switch', 'optional', 'b'))
        self.assertEqual(d.reason, 'a@acct.example is rank 2, and b@acct.example, rank 1, has room (35 points before '
                                   'its nearest line)')
        d = policy.decide(snap(r, accounts=accts, settings=on(ranks=ranks(b=1)), busy=True))
        self.assertEqual((said(d), d.hold), (('stay', 'optional', 'b'), 'Claude Code is working'))
        r['b'] = reading('b', five=10.0, week=88.0, week_reset=NOW + 5 * D)      # 7 points: not enough to move early
        self.assertEqual(said(policy.decide(snap(r, accounts=accts, settings=on(ranks=ranks(b=1))))),
                         ('stay', None, None))
        for strategy in ('most-room', 'stay-then-refills'):                      # these wait for a line
            with self.subTest(strategy=strategy):
                r['b'] = reading('b', five=10.0, week=60.0, week_reset=NOW + 5 * D)
                d = policy.decide(snap(r, accounts=accts, settings=on(ranks=ranks(b=1), strategy=strategy)))
                self.assertEqual(said(d), ('stay', None, None))

    def test_the_reason_names_the_rank_only_when_it_decided(self):
        d = policy.decide(snap(self.R, settings=on(ranks=ranks(c=1))))
        self.assertTrue(d.reason.startswith('a@acct.example 5-hour at 92% (line 90%); c@acct.example, rank 1, has '
                                            'room ('), d.reason)
        d = policy.decide(snap(self.R, settings=on(ranks=ranks(b=1, c=1))))
        self.assertIn('; b@acct.example has room (', d.reason)
        d = policy.decide(snap(self.R, settings=on(ranks=ranks(c=1), rules=(('b', 'last'), ('c', 'last')))))
        self.assertIn('; c@acct.example, used last, rank 1, has room (', d.reason)
        d = policy.best(snap(dict(self.R, a=reading('a', five=20.0, week=20.0)), settings=on(ranks=ranks(c=1))))
        self.assertTrue(d.reason.startswith('c@acct.example, rank 1, has room ('), d.reason)

    def test_the_escape_past_a_line_is_ranked_as_before(self):
        r = {'a': reading('a', five=100.0, week=20.0), 'b': reading('b', five=93.0, week=20.0),
             'c': reading('c', five=91.0, week=20.0)}
        for rk in ((), ranks(b=1)):
            with self.subTest(ranks=rk):
                self.assertEqual(policy.decide(snap(r, settings=on(ranks=rk))).target, 'c')


class StayThenRefills(unittest.TestCase):
    def test_it_never_leaves_before_a_line(self):
        r = {'a': reading('a', five=20.0, week=40.0, week_reset=NOW + 5 * D),
             'b': reading('b', five=10.0, week=10.0, week_reset=NOW + 2 * H)}
        self.assertEqual(said(policy.decide(snap(r))), ('switch', 'optional', 'b'))
        d = policy.decide(snap(r, settings=on(strategy='stay-then-refills')))
        self.assertEqual((said(d), d.reason), (('stay', None, None), 'a@acct.example is under its lines.'))

    def test_at_a_line_it_lands_on_what_refills_first(self):
        r = {'a': reading('a', five=92.0, week=50.0),
             'b': reading('b', five=10.0, week=60.0, week_reset=NOW + 2 * D),
             'c': reading('c', five=10.0, week=5.0, week_reset=NOW + 3 * D)}
        self.assertEqual(policy.decide(snap(r, settings=on(strategy='most-room'))).target, 'c')
        stay_then = policy.decide(snap(r, settings=on(strategy='stay-then-refills')))
        self.assertEqual(stay_then, policy.decide(snap(r, settings=on(strategy='refills-first'))))
        self.assertEqual(said(stay_then), ('switch', 'forced', 'b'))
        self.assertIn('weekly refills', stay_then.reason)

    def test_on_an_account_used_last_it_stays_too(self):
        r = {'a': reading('a', five=20.0, week=40.0), 'b': reading('b', five=10.0, week=10.0)}
        s = on(strategy='stay-then-refills', rules=(('a', 'last'),))
        d = policy.decide(snap(r, accounts=ACCTS[:2], settings=s))
        self.assertEqual(said(d), ('stay', None, None))


def model_reading(aid, five, week, models):
    ws = [api.Window('5-hour', five, NOW + 3 * H, NOW, 'endpoint'), api.Window('weekly', week, NOW + 4 * D, NOW,
                                                                                'endpoint')]
    ws += [api.Window('model:' + name, pct, NOW + 4 * D, NOW, 'endpoint') for name, pct in models.items()]
    return api.Reading(aid, tuple(ws), NOW, 'endpoint', False, None)


class SeveralModels(unittest.TestCase):
    # Each model named counts as asked for: its own week decides where its chats can run (a move of kind 'model'),
    # never whether an account can work at all. The chats here ask for no model, so only the setting counts.
    def test_each_model_is_judged_against_the_model_line(self):
        r = {'a': model_reading('a', 10, 10, {'Fable 5.2': 20, 'Opus 5': 96}),
             'b': model_reading('b', 5, 5, {'Fable 5.2': 5, 'Opus 5': 5})}
        accts = ACCTS[:2]
        none = frozenset()
        self.assertEqual(said(policy.decide(snap(r, accounts=accts, settings=on(model='Fable'), asked=none))),
                         ('stay', None, None))
        d = policy.decide(snap(r, accounts=accts, settings=on(model='Fable', models=('Fable', 'Opus 5')), asked=none))
        self.assertEqual(said(d), ('switch', 'model', 'b'))
        self.assertIn('Opus 5.5 is at 96% on a@acct.example (line 95%)', d.reason)
        r['a'] = model_reading('a', 10, 10, {'Fable 5.2': 97, 'Opus 5': 20})
        d = policy.decide(snap(r, accounts=accts, settings=policy.settings_from({'enabled': True,
                                                                                 'model': ['Fable', 'Opus 5']}),
                               asked=none))
        self.assertEqual(said(d), ('switch', 'model', 'b'))
        self.assertIn('Fable is at 97% on a@acct.example (line 95%)', d.reason)

    def test_a_target_over_any_counted_model_line_is_still_a_landing_place(self):
        # Over a model's own line an account still works, on the next model: a forced move may land there, and says so
        r = {'a': model_reading('a', 92, 10, {}), 'b': model_reading('b', 5, 5, {'Opus 5': 96})}
        s = on(models=('Fable', 'Opus 5'), model='Fable')
        d = policy.decide(snap(r, accounts=ACCTS[:2], settings=s, asked=frozenset()))
        self.assertEqual(said(d), ('switch', 'forced', 'b'))
        # at 96 Opus still works there (refused at 100): the words say so, Keep running on or off
        for keep in (False, True):
            d = policy.decide(snap(r, accounts=ACCTS[:2], settings=s, asked=frozenset(), keep_running=keep))
            self.assertIn('(Opus 5.5 is at 96% there, line 95%: chats stay on it until it is used up)', d.reason)
        r['b'] = model_reading('b', 5, 5, {'Opus 5': 100})
        d = policy.decide(snap(r, accounts=ACCTS[:2], settings=s, asked=frozenset()))
        self.assertIn('(no room for Opus 5.5 there until ', d.reason)          # Keep running off: no step-down said
        d = policy.decide(snap(r, accounts=ACCTS[:2], settings=s, asked=frozenset(), keep_running=True))
        self.assertIn('no room for Opus 5.5 there: chats run on Sonnet 5.5', d.reason)


class Best(unittest.TestCase):
    R = {'a': reading('a', five=20.0, week=20.0),
         'b': reading('b', five=10.0, week=60.0, week_reset=NOW + 1 * D),
         'c': reading('c', five=10.0, week=20.0, week_reset=NOW + 3 * D)}

    def test_where_a_forced_switch_would_land_with_none_of_the_waits(self):
        busy = dict(busy=True, idle_for_s=5.0, since_switch_s=60.0, optional_recent=9, load_per_core=40.0,
                    battery_pct=3.0, on_ac=False)
        for settings, want in ((on(), 'b'), (policy.Settings(), 'b'), (on(strategy='most-room'), 'c'),
                               (on(ranks=ranks(c=1)), 'c'), (on(rules=(('a', 'out'),)), 'b')):
            with self.subTest(settings=settings):
                d = policy.best(snap(self.R, settings=settings, **busy))
                self.assertEqual(said(d), ('switch', 'manual', want))
        d = policy.best(snap(self.R))
        self.assertTrue(d.reason.startswith('b@acct.example has room (35 points before its nearest line, weekly '
                                            'refills '), d.reason)

    def test_the_same_landing_as_a_forced_switch(self):
        over = dict(self.R, a=reading('a', five=92.0, week=20.0))
        for settings in (on(), on(strategy='most-room'), on(ranks=ranks(c=1)), on(rules=(('b', 'last'),)),
                         on(own=own(b={'weekly_at': 63}))):
            with self.subTest(settings=settings):
                self.assertEqual(policy.best(snap(self.R, settings=settings)).target,
                                 policy.decide(snap(over, settings=settings)).target)

    def test_never_kept_out_and_used_last_only_when_nothing_else_has_room(self):
        self.assertEqual(policy.best(snap(self.R, settings=on(rules=(('b', 'out'),)))).target, 'c')
        d = policy.best(snap(self.R, settings=on(rules=(('b', 'last'), ('c', 'last')))))
        self.assertEqual((d.target, d.reason[:40]), ('b', 'b@acct.example, used last, has room (35 '))

    def test_with_none_it_says_when_the_first_one_refills(self):
        r = dict(self.R, b=reading('b', five=100.0, week=20.0, five_reset=NOW + 2 * H),
                 c=reading('c', five=10.0, week=93.0, week_reset=NOW + 30 * H))
        d = policy.best(snap(r))
        self.assertEqual((said(d), d.hold), (('stay', 'manual', None), 'no account has room'))
        self.assertTrue(d.reason.startswith('No other account has room. First refill: '), d.reason)
        self.assertTrue(d.reason.endswith(' (b@acct.example, 5-hour).'), d.reason)
        d = policy.best(snap(dict(self.R, b=reading('b', five=1.0, week=1.0, age=2000)), accounts=ACCTS[:2]))
        self.assertEqual((d.hold, d.reason), ('no fresh numbers', 'No other account has fresh numbers with room: '
                                              'b@acct.example was not read in the last 15 minutes.'))
        d = policy.best(snap(self.R, accounts=ACCTS[:1]))
        self.assertEqual((d.hold, d.reason), ('no other account', 'There is no other account to switch to.'))
        d = policy.best(snap(self.R, accounts=(ACCTS[0], account('b', 2, status='needs_login'))))
        self.assertEqual(d.hold, 'no other account')


# ---------- many made-up snapshots ----------

PCTS = (0.0, 5.0, 40.0, 79.0, 80.0, 84.0, 85.0, 88.0, 89.9, 90.0, 92.0, 94.0, 95.0, 97.0, 100.0)


def made_up(rng, extra=True, orders=None, calm=False):
    """One made-up snapshot, as (Settings keywords, Snapshot keywords but `settings`), so that any build of the
    policy can be given the same one. extra: own lines, ranks, several models and every order may be set. calm:
    nothing holds an optional switch back and every number is fresh, so that optional switches are tried often."""
    n = rng.randint(2, 5)
    ids = ['m%d' % k for k in range(n)]
    accts = tuple(account(x, k + 1, status=rng.choice(('ok',) * 8 + ('expiring', 'needs_login')))
                  for k, x in enumerate(ids))
    readings = {}
    for x in ids:
        if rng.random() < 0.08:
            continue
        age = rng.choice((0.0, 30.0, 300.0) if calm else (0.0, 30.0, 300.0, 700.0, 1000.0, 5000.0))
        ws = []
        if rng.random() < 0.85:
            ws.append(api.Window('5-hour', rng.choice(PCTS + (rng.uniform(0, 100),)),
                                 NOW + rng.uniform(-600, 5 * H), NOW - age, 'endpoint'))
        ws.append(api.Window('weekly', rng.choice(PCTS + (rng.uniform(0, 100), None)),
                             NOW + rng.choice((rng.uniform(-H, 7 * D), rng.uniform(0, 7 * D))), NOW - age,
                             'endpoint'))
        for name in ('Fable 5.2', 'Opus 5'):
            if rng.random() < 0.3:
                ws.append(api.Window('model:' + name, rng.choice(PCTS), NOW + rng.uniform(0, 7 * D), NOW - age,
                                     'endpoint'))
        readings[x] = api.Reading(x, tuple(ws), NOW - age, 'endpoint', not calm and rng.random() < 0.08, None)
    rules = dict((x, r) for x, r in ((x, rng.choice(('in',) * 4 + ('last', 'out'))) for x in ids) if r != 'in')
    sk = dict(enabled=rng.random() < 0.95, session_at=rng.choice((90.0, 85.0, 70.5)),
              weekly_at=rng.choice((95.0, 90.0)), model_at=rng.choice((95.0, 80.0)),
              model=rng.choice((None, None, 'Fable', 'Opus 5')),
              strategy=rng.choice(orders or (policy.STRATEGIES if extra else OLD_ORDERS)),
              rules=tuple(sorted(rules.items())))
    if extra:
        lines = {}
        for x in ids:
            if rng.random() < 0.4:
                keys = rng.choice((('session_at',), ('weekly_at',), policy.OWN_KEYS))
                lines[x] = dict((k, round(rng.uniform(50, 99.9), 1)) for k in keys)
        sk['own'] = policy.own_from(lines)
        if rng.random() < 0.7:
            sk['ranks'] = tuple(sorted((x, rng.randint(1, 3)) for x in ids if rng.random() < 0.6))
        if rng.random() < 0.3:
            sk['models'] = ('Fable', 'Opus 5')
            sk['model'] = 'Fable'
    kw = dict(now=NOW, accounts=accts, readings=readings, active=rng.choice(ids + ids + [None]),
              busy=rng.random() < 0.1, idle_for_s=rng.choice((None, 10.0, 600.0, 600.0)),
              since_switch_s=rng.choice((None, 60.0, 4 * H, 4 * H)), optional_recent=rng.choice((0, 0, 0, 1, 2)),
              load_per_core=rng.choice((None, 0.5, 0.5, 9.0)), battery_pct=rng.choice((None, 90.0, 90.0, 10.0)),
              on_ac=rng.choice((None, True, False)), rate_limited=rng.random() < 0.05,
              live_refused=rng.random() < 0.03, claimed=frozenset(x for x in ids if rng.random() < 0.05),
              obligations=({'kind': 'doc', 'at': NOW - 60},) if rng.random() < 0.03 else ())
    if calm:
        kw.update(CALM, active=ids[0], rate_limited=False, live_refused=False, obligations=())
    return sk, kw


def snapshot(sk, kw):
    # the chats ask for no model: these snapshots are about lines and ranks (the models named in the settings count)
    return policy.Snapshot(settings=policy.Settings(**sk), **dict({'asked': frozenset()}, **kw))


def landing_places(s):
    """Every view a forced switch may land on now (fresh numbers, room under every line by the margin), but the
    account in use, one kept out, one claimed or not signed in."""
    out = []
    for a in s.accounts:
        if not policy._eligible(a, s) or policy.rule_of(s.settings, a.id) == 'out':
            continue
        v = policy.view(a, s.readings.get(a.id), s.settings, s.now)
        if v.known and v.age is not None and v.age <= policy.FORCED_MAX_AGE and v.room >= policy.LAND_MARGIN:
            out.append(v)
    return out


def rank_key(v):
    return policy._rank_key(v)


class ManySnapshots(unittest.TestCase):
    def test_every_account_ranked_alike_is_the_same_as_no_ranks(self):
        rng = random.Random(4101)
        for n in range(6000):
            sk, kw = made_up(rng, extra=False)
            s = snapshot(sk, kw)
            same = dataclasses.replace(s, settings=dataclasses.replace(
                s.settings, ranks=tuple(sorted((a.id, 2) for a in s.accounts))))
            self.assertEqual(policy.decide(same), policy.decide(s), n)
            self.assertEqual(policy.best(same), policy.best(s), n)

    def test_own_lines_equal_to_the_shared_ones_decide_the_same(self):
        rng = random.Random(4102)
        for n in range(6000):
            sk, kw = made_up(rng, extra=False)
            s = snapshot(sk, kw)
            st = s.settings
            same = dataclasses.replace(s, settings=dataclasses.replace(st, own=policy.own_from(dict(
                (a.id, {'session_at': st.session_at, 'weekly_at': st.weekly_at}) for a in s.accounts
                if rng.random() < 0.7))))
            a, b = policy.decide(same), policy.decide(s)
            self.assertEqual((a.action, a.target, a.kind, a.hold), (b.action, b.target, b.kind, b.hold), n)

    def test_the_new_order_moves_where_refills_first_does_and_only_at_a_line(self):
        rng = random.Random(4103)
        moved = 0
        for n in range(6000):
            sk, kw = made_up(rng, orders=('stay-then-refills',))
            s = snapshot(sk, kw)
            d = policy.decide(s)
            if d.action == 'switch':
                self.assertIn(d.kind, ('forced', 'model'), (n, d))     # a model's own week moves under any order
                moved += 1
            if d.kind == 'forced':
                refills = dataclasses.replace(s, settings=dataclasses.replace(s.settings, strategy='refills-first'))
                self.assertEqual(d, policy.decide(refills), n)
        self.assertGreater(moved, 300)

    def test_no_rule_is_ever_broken(self):
        rng = random.Random(4104)
        seen = dict.fromkeys(('forced', 'optional', 'escape', 'ranked', 'own', 'best', 'best none', 'model'), 0)
        for n in range(26000):
            sk, kw = made_up(rng, calm=n % 4 == 0)
            s = snapshot(sk, kw)
            d = policy.decide(s)
            where = (n, s.settings, s.active, d)
            rule = lambda x: policy.rule_of(s.settings, x)          # noqa: E731
            places = landing_places(s)
            if s.active is not None and rule(s.active) == 'out':
                self.assertEqual(d.action, 'stay', where)
            if d.action == 'switch':
                self.assertNotEqual(rule(d.target), 'out', where)
                self.assertNotEqual(d.target, s.active, where)
                t = next((v for v in places if v.id == d.target), None)
                if d.kind == 'forced' and t is None:            # the escape: only with no landing place at all
                    self.assertEqual(places, [], where)
                    self.assertTrue(policy.active_blocked(s), where)
                    seen['escape'] += 1
                else:
                    self.assertIsNotNone(t, where)               # never at or past its own line, with the margin
                    seen[d.kind] += 1
                    seen['own'] += bool(policy.own_of(s.settings, d.target))
                    if d.kind == 'model':                       # room for the models named: not every landing has it
                        pass
                    elif d.kind == 'forced':
                        group = [v for v in places if rule(v.id) != 'last'] or places
                        self.assertIn(t, group, where)          # used last only when nothing else has room
                        self.assertEqual(rank_key(t), min(rank_key(v) for v in group), where)
                        seen['ranked'] += t.rank is not None
                    else:
                        self.assertNotEqual(rule(d.target), 'last', where)
                        av = policy.view(dict((a.id, a) for a in s.accounts)[s.active], s.readings.get(s.active),
                                         s.settings, NOW)
                        if rule(s.active) != 'last':
                            self.assertLessEqual(rank_key(t), rank_key(av), where)
                        better = [v for v in places if rank_key(v) < rank_key(t) and rule(v.id) != 'last'
                                  and v.room >= policy.OPTIONAL_ROOM]
                        self.assertEqual(better, [], where)
            if s.settings.strategy != 'refills-first' and d.kind == 'optional':
                self.fail(where)
            b = policy.best(s)
            if b.action == 'switch':
                t = next((v for v in places if v.id == b.target), None)
                self.assertIsNotNone(t, (n, b))
                group = [v for v in places if rule(v.id) != 'last'] or places
                self.assertIn(t, group, (n, b))
                self.assertEqual(rank_key(t), min(rank_key(v) for v in group), (n, b))
                seen['best'] += 1
                if d.kind == 'forced' and d.action == 'switch' and d.target in [v.id for v in places]:
                    self.assertEqual(d.target, b.target, (n, d, b))   # where auto-switch lands, --best lands
            else:
                self.assertEqual(places, [], (n, b))
                seen['best none'] += 1
        for k, v in seen.items():
            if k != 'model':                            # counted, not aimed at
                self.assertGreater(v, 30, (k, seen))


def agreeing(seed, count):
    """Snapshots both the policy and plan.py (the page's order and Next) judge alike, with own lines, ranks, rules and
    every order: every account signed in, numbers fresh and not frozen, the account in use over its line but not
    blocked (no escape, which plan.py never models), refills never tied. Yields (snapshot, plan's accounts, plan's
    settings)."""
    from switchboard import plan            # noqa: F401  (imported where it is used, as the page imports it)
    rng = random.Random(seed)
    for _ in range(count):
        ids = ['p%d' % k for k in range(rng.randint(2, 5))]
        accts = tuple(account(x, k + 1) for k, x in enumerate(ids))
        days = rng.sample(range(1, 7 * 24), len(ids))
        own_lines, ranked, readings, planned = {}, {}, {}, []
        for k, x in enumerate(ids):
            if rng.random() < 0.4:
                own_lines[x] = dict((key, float(rng.randint(60, 99))) for key in
                                    rng.choice((('session_at',), ('weekly_at',), policy.OWN_KEYS)))
            if rng.random() < 0.5:
                ranked[x] = rng.randint(1, 3)
            five_line = own_lines.get(x, {}).get('session_at', 90.0)
            if k == 0:
                five, week = float(rng.randint(int(five_line), 99)), float(rng.randint(10, 50))
            else:
                five, week = float(rng.choice([rng.randint(0, 84), 88, 92, 96])), float(rng.randint(0, 99))
            wr = NOW + days[k] * H
            readings[x] = api.Reading(x, (api.Window('5-hour', five, NOW + 3 * H), api.Window('weekly', week, wr)),
                                      NOW - rng.uniform(0, 600), 'endpoint')
            p = {'name': x, 'weekly_used_pct': week, 'weekly_resets_at': wr, 'five_hour_used_pct': five,
                 'five_hour_resets_at': NOW + 3 * H, 'scoped': [], 'active': k == 0}
            for key, name in (('session_at', 'session_line'), ('weekly_at', 'weekly_line')):
                if key in own_lines.get(x, {}):
                    p[name] = own_lines[x][key]
            if x in ranked:
                p['rank'] = ranked[x]
            planned.append(p)
        rules = dict((x, r) for x, r in ((x, rng.choice(('in', 'in', 'in', 'last', 'out'))) for x in ids) if r != 'in')
        for p in planned:
            if p['name'] in rules:
                p['rule'] = rules[p['name']]
        strategy = rng.choice(policy.STRATEGIES)
        settings = policy.Settings(enabled=True, strategy=strategy, rules=tuple(sorted(rules.items())),
                                   own=policy.own_from(own_lines), ranks=tuple(sorted(ranked.items())))
        s = policy.Snapshot(now=NOW, accounts=accts, readings=readings, active=ids[0], settings=settings, **CALM)
        yield s, planned, {'strategy': strategy, 'session_line': 90, 'weekly_line': 95, 'margin': policy.LAND_MARGIN}


class PlanAgrees(unittest.TestCase):
    def test_next_is_where_the_policy_lands(self):
        from switchboard import plan
        seen = dict.fromkeys(('switch', 'stay', 'out', 'ranked', 'own', 'last'), 0)
        for n, (s, planned, lines) in enumerate(agreeing(4105, 5000)):
            d = policy.decide(s)
            nxt = plan.next_account(planned, lines, NOW)
            where = (n, s.settings, d, nxt)
            if policy.rule_of(s.settings, s.active) == 'out':
                self.assertEqual((d.action, nxt['account']), ('stay', None), where)
                seen['out'] += 1
                continue
            self.assertEqual(d.kind, 'forced', where)
            if d.action == 'switch':
                self.assertIsNotNone(nxt['account'], where)
                self.assertEqual(d.target, nxt['account']['name'], where)
                seen['switch'] += 1
                seen['ranked'] += policy.rank_of(s.settings, d.target) is not None
                seen['own'] += bool(policy.own_of(s.settings, d.target))
                seen['last'] += policy.rule_of(s.settings, d.target) == 'last'
            else:
                self.assertIsNone(nxt['account'], where)
                seen['stay'] += 1
        for k, v in seen.items():
            self.assertGreater(v, 30, (k, seen))

    def test_the_page_order_puts_a_lower_rank_first(self):
        from switchboard import plan
        given = [{'name': 'in-use', 'weekly_used_pct': 96, 'weekly_resets_at': NOW + D, 'active': True},
                 {'name': 'b', 'weekly_used_pct': 50, 'weekly_resets_at': NOW + 2 * D},
                 {'name': 'c', 'weekly_used_pct': 10, 'weekly_resets_at': NOW + 5 * D, 'rank': 1},
                 {'name': 'd', 'weekly_used_pct': 10, 'weekly_resets_at': NOW + 3 * D, 'rank': 1, 'rule': 'last'}]
        self.assertEqual([j['name'] for j in plan.order(given, {}, NOW)], ['c', 'b', 'd', 'in-use'])
        given[2]['weekly_line'] = 12.0
        ranked = dict((j['name'], j) for j in plan.order(given, {}, NOW))
        self.assertEqual((ranked['c']['usable'], ranked['c']['reason']), (False, 'not enough room'))
        self.assertEqual(plan.next_account(given, {'strategy': 'stay-then-refills'}, NOW)['account']['name'], 'b')


if __name__ == '__main__':
    unittest.main()
