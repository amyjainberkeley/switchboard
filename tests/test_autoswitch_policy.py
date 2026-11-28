import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# policy.decide is pure, so every rule is a snapshot and an answer.
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard.accounts import api, policy
from fakes_autoswitch import T0, account

H, D = 3600.0, 86400.0
NOW = T0


def win(name, pct, resets=None, age=0.0, source='endpoint'):
    return api.Window(name, pct, resets, NOW - age, source)


def reading(aid, five=None, week=None, fable=None, five_reset=NOW + 3 * H, week_reset=NOW + 4 * D,
            fable_reset=NOW + 4 * D, age=0.0, frozen=False, source='endpoint'):
    ws = []
    if five is not None:
        ws.append(win('5-hour', five, five_reset, age, source))
    if week is not None:
        ws.append(win('weekly', week, week_reset, age, source))
    if fable is not None:
        ws.append(win('model:Fable', fable, fable_reset, age, 'endpoint'))
    return api.Reading(aid, tuple(ws), NOW - age, source, frozen, None)


ACCTS = (account('a', 1), account('b', 2), account('c', 3))
CALM = dict(busy=False, idle_for_s=1800.0, since_switch_s=4 * H, optional_recent=0, load_per_core=0.5,
            battery_pct=90.0, on_ac=True)


def snap(readings, active='a', accounts=ACCTS, settings=None, **kw):
    base = dict(CALM)
    base.update(kw)
    return policy.Snapshot(now=NOW, accounts=tuple(accounts), readings=readings,
                           settings=settings or policy.Settings(enabled=True), active=active, **base)


class Forced(unittest.TestCase):
    def test_each_line_forces_a_switch_on_its_own_window(self):
        cases = [dict(five=90.0, week=10.0), dict(five=10.0, week=95.0)]
        for c in cases:
            d = policy.decide(snap({'a': reading('a', **c), 'b': reading('b', five=5.0, week=5.0)}))
            self.assertEqual((d.action, d.kind, d.target), ('switch', 'forced', 'b'), c)
        # a model's own week counts for that model only: a switch of its own kind, never a forced one
        fable = policy.Settings(enabled=True, model='Fable')
        d = policy.decide(snap({'a': reading('a', five=10, week=10, fable=95.0),
                                'b': reading('b', five=5, week=5, fable=5.0)}, settings=fable))
        self.assertEqual((d.action, d.kind), ('switch', 'model'))
        self.assertIn('Fable is at 95% on a@acct.example', d.reason)

    def test_a_model_no_chat_asks_for_never_decides(self):
        r = {'a': reading('a', five=10, week=10, fable=100.0), 'b': reading('b', five=5, week=5)}
        d = policy.decide(snap(r, asked=frozenset(['opus'])))
        self.assertEqual(d.action, 'stay')
        self.assertIsNone(d.target)
        # with nothing known about the chats yet, a reported model week moves nothing (only the setting's family can)
        self.assertNotEqual(policy.decide(snap(r)).kind, 'model')
        self.assertEqual(policy.decide(snap(r, settings=policy.Settings(enabled=True, model='Fable'))).kind, 'model')

    def test_a_refused_login_or_refused_requests_force_it(self):
        accts = (account('a', 1, status='needs_login'), account('b', 2))
        d = policy.decide(snap({'a': reading('a', five=1, week=1), 'b': reading('b', five=1, week=1)}, accounts=accts))
        self.assertEqual((d.action, d.kind, d.target), ('switch', 'forced', 'b'))
        d = policy.decide(snap({'a': reading('a', five=1, week=1), 'b': reading('b', five=1, week=1)},
                               rate_limited=True))
        self.assertEqual((d.action, d.kind), ('switch', 'forced'))

    def test_refills_first_takes_the_soonest_weekly_refill_and_ties_go_to_more_room(self):
        r = {'a': reading('a', five=92.0, week=50.0),
             'b': reading('b', five=10.0, week=60.0, week_reset=NOW + 2 * D),
             'c': reading('c', five=10.0, week=20.0, week_reset=NOW + 3 * D)}
        self.assertEqual(policy.decide(snap(r)).target, 'b')
        r['c'] = reading('c', five=10.0, week=20.0, week_reset=NOW + 2 * D + 20)      # the same minute: more room
        self.assertEqual(policy.decide(snap(r)).target, 'c')

    def test_most_room_takes_the_most_room(self):
        r = {'a': reading('a', five=92.0, week=50.0),
             'b': reading('b', five=10.0, week=60.0, week_reset=NOW + 2 * D),
             'c': reading('c', five=10.0, week=20.0, week_reset=NOW + 3 * D)}
        d = policy.decide(snap(r, settings=policy.Settings(enabled=True, strategy='most-room')))
        self.assertEqual(d.target, 'c')

    def test_never_lands_within_five_points_of_a_line(self):
        r = {'a': reading('a', five=95.0, week=10.0),
             'b': reading('b', five=85.1, week=10.0, week_reset=NOW + D),      # 4.9 points under 90
             'c': reading('c', five=80.0, week=10.0, week_reset=NOW + 3 * D)}
        self.assertEqual(policy.decide(snap(r)).target, 'c')

    def test_numbers_older_than_15_minutes_are_no_landing_place_and_frozen_ones_come_last(self):
        r = {'a': reading('a', five=95.0, week=10.0),
             'b': reading('b', five=10.0, week=10.0, week_reset=NOW + D, age=901),
             'c': reading('c', five=10.0, week=10.0, week_reset=NOW + 3 * D)}
        self.assertEqual(policy.decide(snap(r)).target, 'c')
        r['b'] = reading('b', five=10.0, week=10.0, week_reset=NOW + D, frozen=True)
        self.assertEqual(policy.decide(snap(r)).target, 'c')
        del r['c']
        self.assertEqual(policy.decide(snap(r)).target, 'b')

    def test_a_truly_blocked_account_escapes_to_one_that_can_still_work(self):
        r = {'a': reading('a', five=100.0, week=40.0),
             'b': reading('b', five=93.0, week=40.0),         # past its 90 line, 7 points of real room
             'c': reading('c', five=97.0, week=40.0)}
        d = policy.decide(snap(r))
        self.assertEqual((d.action, d.kind, d.target), ('switch', 'forced', 'b'))
        self.assertIn('can still work', d.reason)
        r['a'] = reading('a', five=92.0, week=40.0)            # over its line, not blocked: it stays
        d = policy.decide(snap(r))
        self.assertEqual((d.action, d.kind, d.hold), ('stay', 'forced', 'no account has room'))

    def test_every_account_out_stays_and_says_when_the_first_refills(self):
        r = {'a': reading('a', five=100.0, week=40.0, five_reset=NOW + 2 * H),
             'b': reading('b', five=100.0, week=100.0, five_reset=NOW + H, week_reset=NOW + 3 * H),
             'c': reading('c', five=99.0, week=100.0, week_reset=NOW + 5 * H)}
        s = snap(r)
        d = policy.decide(s)
        self.assertEqual((d.action, d.kind, d.hold), ('stay', 'forced', 'every account is out'))
        self.assertTrue(d.reason.startswith('Every account is out. Back at '))
        first = policy.earliest_refill(s)
        self.assertEqual((first[0], first[1]), (NOW + 2 * H, 'a@acct.example'))   # b needs both its resets: 3 h

    def test_an_unread_candidate_is_waited_for_not_called_out(self):
        r = {'a': reading('a', five=100.0, week=40.0), 'b': reading('b', five=100.0, week=40.0)}
        d = policy.decide(snap(r))
        self.assertEqual((d.action, d.hold), ('stay', 'no fresh numbers'))
        self.assertIn('c@acct.example', d.reason)

    def test_a_reading_from_the_future_is_never_trusted(self):
        r = {'a': reading('a', five=95.0, week=10.0), 'b': reading('b', five=10.0, week=10.0, age=-600),
             'c': reading('c', five=99.0, week=10.0)}
        d = policy.decide(snap(r))
        self.assertNotEqual(d.target if d.action == 'switch' else None, 'b')

    def test_a_line_read_in_the_future_never_forces_a_switch(self):
        r = {'a': reading('a', five=99.0, week=10.0, age=-600), 'b': reading('b', five=10.0, week=10.0)}
        self.assertEqual(policy.decide(snap(r)).action, 'stay')

    def test_holds_never_block_a_forced_switch(self):
        r = {'a': reading('a', five=91.0, week=10.0), 'b': reading('b', five=10.0, week=10.0)}
        s = snap(r, busy=True, idle_for_s=0.0, since_switch_s=60.0, optional_recent=5, load_per_core=30.0,
                 battery_pct=8.0, on_ac=False, obligations=({'kind': 'doc', 'url': 'u', 'at': NOW - 60},),
                 no_optional='restarted', waited={'forced': policy.CALM_FORCED_S})
        self.assertEqual(policy.decide(s).action, 'switch')     # (a busy Mac holds it, for 15 minutes at most)

    def test_nothing_moves_when_it_cannot_be_safe(self):
        r = {'a': reading('a', five=99.0, week=10.0), 'b': reading('b', five=10.0, week=10.0)}
        for kw in (dict(live_ok=False, live_why='timeout'), dict(settling=True), dict(offline=True)):
            self.assertEqual(policy.decide(snap(r, **kw)).action, 'stay', kw)
        self.assertEqual(policy.decide(snap(r, settings=policy.Settings(enabled=False))).action, 'stay')
        self.assertEqual(policy.decide(snap(r, active='zz')).action, 'stay')
        claimed = (account('a', 1), account('b', 2, status='claimed'))
        self.assertEqual(policy.decide(snap(r, accounts=claimed)).action, 'stay')


class Optional(unittest.TestCase):
    def base(self):
        return {'a': reading('a', five=20.0, week=40.0, week_reset=NOW + 5 * D),
                'b': reading('b', five=10.0, week=50.0, week_reset=NOW + 2 * D),
                'c': reading('c', five=10.0, week=30.0, week_reset=NOW + 6 * D)}

    def test_it_goes_when_every_condition_holds(self):
        d = policy.decide(snap(self.base()))
        self.assertEqual((d.action, d.kind, d.target), ('switch', 'optional', 'b'))
        self.assertIn('refills first', d.reason)

    def test_each_hold_says_why_in_plain_words(self):
        r = self.base()
        cases = [
            (dict(busy=True, idle_for_s=0.0), 'Claude Code is working'),
            (dict(idle_for_s=200.0), 'was working'),
            (dict(obligations=({'kind': 'doc', 'url': 'u', 'at': NOW - 120},)), 'Claude Doc'),
            (dict(obligations=({'kind': 'artifact', 'url': 'u', 'at': NOW - 120},)), 'artifact was published'),
            (dict(obligations=({'kind': 'watch', 'url': 'u', 'at': NOW - 120},)), 'watching'),
            (dict(since_switch_s=30 * 60.0), 'the last switch was'),
            (dict(optional_recent=2), '2 optional switches'),
            (dict(load_per_core=5.0), 'very busy'),
            (dict(battery_pct=20.0, on_ac=False), 'battery'),
            (dict(disagree=True), 'disagree'),
            (dict(no_optional='automatic switching was just restarted'), 'restarted'),
        ]
        for kw, words in cases:
            d = policy.decide(snap(r, **kw))
            self.assertEqual((d.action, d.kind, d.target), ('stay', 'optional', 'b'), kw)
            self.assertIn(words, d.hold, kw)
            self.assertTrue(d.reason.startswith('Next: b@acct.example'), d.reason)
        d = policy.decide(snap(r, battery_pct=20.0, on_ac=True))
        self.assertEqual(d.action, 'switch')                       # charging: no battery hold

    def test_numbers_must_be_fresh_and_not_frozen_on_both_sides(self):
        for change in (dict(b=reading('b', five=10.0, week=50.0, week_reset=NOW + 2 * D, age=601)),
                       dict(a=reading('a', five=20.0, week=40.0, week_reset=NOW + 5 * D, age=601)),
                       dict(b=reading('b', five=10.0, week=50.0, week_reset=NOW + 2 * D, frozen=True)),
                       dict(a=reading('a', five=20.0, week=40.0, week_reset=NOW + 5 * D, frozen=True))):
            r = self.base()
            r.update(change)
            d = policy.decide(snap(r))
            self.assertEqual(d.action, 'stay', change)
            self.assertTrue(d.hold, change)

    def test_status_line_numbers_are_not_frozen_by_an_endpoint_429(self):
        r = self.base()
        r['a'] = reading('a', five=20.0, week=40.0, week_reset=NOW + 5 * D, frozen=True, source='status_line')
        self.assertEqual(policy.decide(snap(r)).action, 'switch')

    def test_the_gain_must_be_real(self):
        r = self.base()
        r['b'] = reading('b', five=10.0, week=50.0, week_reset=NOW + 5 * D - 59 * 60)
        self.assertEqual(policy.decide(snap(r)).action, 'stay')
        r['b'] = reading('b', five=10.0, week=50.0, week_reset=NOW + 5 * D - 61 * 60)
        self.assertEqual(policy.decide(snap(r)).action, 'switch')
        r['b'] = reading('b', five=10.0, week=85.1, week_reset=NOW + 2 * D)     # 9.9 points of room
        self.assertEqual(policy.decide(snap(r)).action, 'stay')
        r['b'] = reading('b', five=10.0, week=85.0, week_reset=NOW + 2 * D)
        self.assertEqual(policy.decide(snap(r)).action, 'switch')     # sign lines off (the default): as before
        # with sign lines on: past its sign line, from one under them
        self.assertEqual(policy.decide(snap(r, settings=policy.Settings(enabled=True, sign_lines=True))).action,
                         'stay')

    def test_most_room_never_moves_before_a_line_and_an_unknown_refill_never_moves(self):
        d = policy.decide(snap(self.base(), settings=policy.Settings(enabled=True, strategy='most-room')))
        self.assertEqual((d.action, d.target), ('stay', None))
        r = self.base()
        r['a'] = reading('a', five=20.0, week=40.0, week_reset=None)
        d = policy.decide(snap(r))
        self.assertEqual(d.action, 'stay')
        self.assertIn('not known', d.reason)


class AFlapNeverHappens(unittest.TestCase):
    """Random reading streams: optional switches always at least 45 minutes apart, never more than 2 in 6 hours, never
    A to B to A within an hour; forced switches never held by any of that."""

    def test_random_streams(self):
        rng = random.Random(1716)
        kinds = {'optional': 0, 'forced': 0, 'sign': 0, 'model': 0}
        for run in range(40):
            accts = tuple(account(x, i + 1) for i, x in enumerate('abcd'))
            pct = dict((a.id, rng.uniform(0, 60)) for a in accts)
            resets = dict((a.id, NOW + rng.uniform(1, 7) * D) for a in accts)
            active, t = 'a', NOW
            switches = []
            for step in range(6 * 60):                                    # six hours, a pass a minute
                t += 60
                for a in accts:
                    if a.id == active:
                        pct[a.id] = min(100.0, pct[a.id] + rng.uniform(0, 0.6))
                    if resets[a.id] <= t:
                        resets[a.id] += 7 * D
                        pct[a.id] = 0.0
                readings = dict((a.id, api.Reading(a.id, (api.Window('weekly', round(pct[a.id], 1), resets[a.id],
                                                                     t - rng.uniform(0, 300), 'endpoint'),),
                                                   t - 300, 'endpoint')) for a in accts)
                since = None if not switches else (t - switches[-1][0])
                recent = sum(1 for s in switches if s[3] == 'optional' and t - s[0] <= 6 * H)
                s = policy.Snapshot(now=t, accounts=accts, readings=readings,
                                    settings=policy.Settings(enabled=True, sign_lines=False), active=active,
                                    idle_for_s=3600.0,
                                    since_switch_s=since, optional_recent=recent)
                d = policy.decide(s)
                if d.action == 'switch':
                    switches.append((t, active, d.target, d.kind))
                    kinds[d.kind] += 1
                    active = d.target
            opt = [x for x in switches if x[3] == 'optional']
            for i, x in enumerate(switches):
                if x[3] == 'optional' and i:
                    self.assertGreaterEqual(x[0] - switches[i - 1][0], policy.DWELL, (run, switches))
            for i in range(len(opt)):
                window = [y for y in opt if 0 <= y[0] - opt[i][0] <= 6 * H]
                self.assertLessEqual(len(window), policy.OPTIONAL_CAP, (run, opt))
            for x, y in zip(opt, opt[1:]):
                self.assertFalse(x[1] == y[2] and y[0] - x[0] <= H, ('A-B-A', run, opt))
        self.assertGreater(kinds['optional'], 10)          # the streams really do switch, both ways
        self.assertGreater(kinds['forced'], 10)

    def test_forced_switches_ignore_the_dwell(self):
        r = {'a': reading('a', five=95.0, week=10.0), 'b': reading('b', five=10.0, week=10.0)}
        d = policy.decide(snap(r, since_switch_s=10.0, optional_recent=9))
        self.assertEqual((d.action, d.kind), ('switch', 'forced'))


class NeedSwitches(unittest.TestCase):
    def test_it_goes_only_with_room_and_fresh_numbers(self):
        r = {'a': reading('a', five=20.0, week=20.0), 'b': reading('b', five=10.0, week=50.0)}
        d = policy.need(snap(r), 'b', 'Claude in Chrome is signed in as b')
        self.assertEqual((d.action, d.kind, d.target), ('switch', 'need', 'b'))
        r['b'] = reading('b', five=86.0, week=50.0)
        d = policy.need(snap(r), 'b')
        self.assertEqual(d.action, 'stay')
        self.assertIn('no room', d.reason)
        r['b'] = reading('b', five=10.0, week=50.0, age=901)
        self.assertIn('no fresh numbers', policy.need(snap(r), 'b').reason)
        self.assertIn('already on', policy.need(snap(r), 'a').reason)
        self.assertEqual(policy.need(snap(r), 'nobody').action, 'stay')
        claimed = (account('a', 1), account('b', 2, status='claimed'))
        r['b'] = reading('b', five=10.0, week=50.0)
        self.assertEqual(policy.need(snap(r, accounts=claimed), 'b').action, 'stay')


class WhatASwitchSays(unittest.TestCase):
    def test_a_limit_reached_says_to_send_the_message_again_and_never_offers_to(self):
        r = {'a': reading('a', five=100.0, week=10.0), 'b': reading('b', five=10.0, week=10.0)}
        s = snap(r)
        d = policy.decide(s)
        note = policy.switch_note(d, s)
        self.assertIn('Switched to b@acct.example. If a reply stopped, send your message again.', note)
        for words in ('re-run', 'rerun', 'resend it', "I'll", 'I will', 'sign in', '/login'):
            self.assertNotIn(words, note)
        r['a'] = reading('a', five=92.0, week=10.0)              # at its line, still working: no resume line
        s = snap(r)
        self.assertNotIn('send your message again', policy.switch_note(policy.decide(s), s))


class ModelNames(unittest.TestCase):
    def test_a_model_and_its_versions_match_and_other_versions_do_not(self):
        self.assertTrue(policy.same_model('Fable', 'fable 5.2'))
        self.assertTrue(policy.same_model('Fable  5.2', 'FABLE'))
        self.assertFalse(policy.same_model('Opus 4', 'Opus 5'))
        self.assertFalse(policy.same_model('Fab', 'Fable'))
        lines = policy.lines_for(policy.Settings(enabled=True, model='Fable'))
        self.assertEqual(policy.window_line(lines, 'model:Fable 5'), 95.0)
        self.assertIsNone(policy.window_line(lines, 'model:Opus'))


class SettingsFromDisk(unittest.TestCase):
    def test_forgiving_on_read(self):
        s = policy.settings_from({'session_at': 120, 'weekly_at': float('nan'), 'model_at': 'x', 'strategy': 'best',
                                  'model': '  Fable  ', 'enabled': 'yes'})
        self.assertEqual((s.session_at, s.weekly_at, s.model_at, s.strategy, s.model, s.enabled),
                         (99.9, 95.0, 95.0, 'refills-first', 'Fable', False))
        self.assertEqual(policy.lines_for(s), {'5-hour': 99.9, 'weekly': 95.0, 'model:Fable': 95.0})


if __name__ == '__main__':
    unittest.main()
