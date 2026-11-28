import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# policy.decide follows the models the chats ask for: a model's own week counts for that model only, a calm Mac
# first, the person's own pick holds, sign lines keep Claude Code's usage sign away while any account can, and no
# move lands on old numbers. Every rule is a snapshot and an answer.
import dataclasses
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard.accounts import api, policy  # noqa: E402
from fakes_autoswitch import T0, account  # noqa: E402

H, D = 3600.0, 86400.0
NOW = T0


def win(name, pct, resets=None, age=0.0, source='endpoint'):
    return api.Window(name, pct, resets, NOW - age, source)


def reading(aid, five=None, week=None, fable=None, opus=None, five_reset=NOW + 3 * H, week_reset=NOW + 4 * D,
            fable_reset=NOW + 4 * D, age=0.0, frozen=False):
    ws = []
    if five is not None:
        ws.append(win('5-hour', five, five_reset, age))
    if week is not None:
        ws.append(win('weekly', week, week_reset, age))
    if fable is not None:
        ws.append(win('model:Fable', fable, fable_reset, age))
    if opus is not None:
        ws.append(win('model:Opus', opus, week_reset, age))
    return api.Reading(aid, tuple(ws), NOW - age, 'endpoint', frozen, None)


def acct(aid, n, ends=None, status='ok'):
    return dataclasses.replace(account(aid, n, status=status), refresh_expires_at=ends)


ACCTS = (acct('a', 1), acct('b', 2), acct('c', 3))
CALM = dict(busy=False, idle_for_s=1800.0, since_switch_s=4 * H, optional_recent=0, load_per_core=0.5,
            battery_pct=90.0, on_ac=True, keep_running=True)   # the words of chats going on (Keep running on);
                                                              # off: test_model_room_one_rule
FABLE = frozenset(['fable'])


def snap(readings, active='a', accounts=ACCTS, settings=None, **kw):
    base = dict(CALM)
    base.update(kw)
    return policy.Snapshot(now=NOW, accounts=tuple(accounts), readings=readings,
                           settings=settings or policy.Settings(enabled=True), active=active, **base)


def settings(**kw):
    kw.setdefault('enabled', True)
    return policy.Settings(**kw)


class WindowsThatCount(unittest.TestCase):
    """Rule 1: a model's own week counts for that model, never for whether the account works at all."""

    def test_a_fable_chat_moves_to_fable_room_with_the_setting_empty(self):
        # Nov 27 01:28: the account in use at Fable 100, weekly 76; another at 0 and 0
        r = {'a': reading('a', week=76, fable=100), 'b': reading('b', week=0, fable=0, week_reset=NOW + 6 * D),
             'c': reading('c', five=0, week=33, fable=55)}
        d = policy.decide(snap(r, asked=FABLE))
        self.assertEqual((d.action, d.kind), ('switch', 'model'))
        self.assertIn('Fable is used up on a@acct.example (100%).', d.reason)
        self.assertIn('has room for Fable', d.reason)

    def test_fable_full_accounts_with_weekly_room_are_never_every_account_out(self):
        # Nov 26 08:38: every account at Fable 95 to 100, weekly 74 to 87; the 5-hour window of one at 100
        r = {'a': reading('a', week=87, fable=100, fable_reset=NOW + 2 * D),
             'b': reading('b', week=74, fable=100, fable_reset=NOW + 3 * D),
             'c': reading('c', five=100, week=17, fable=27)}
        for s in (settings(model='Fable'), settings()):
            d = policy.decide(snap(r, settings=s, asked=FABLE))
            self.assertEqual(d.action, 'stay', s)
            self.assertNotIn('Every account is out', d.reason)
            self.assertIn('No account has room for Fable until', d.reason)
            self.assertIn('(first: c@acct.example)', d.reason)        # its 5-hour window refills in 3 hours
            self.assertIn('Chats run on Opus 5.5 until then.', d.reason)

    def test_the_setting_model_also_counts_its_family_when_no_chat_asks_for_it(self):
        r = {'a': reading('a', week=50, fable=97), 'b': reading('b', week=10, fable=10)}
        self.assertEqual(policy.decide(snap(r, asked=frozenset())).action, 'stay')
        self.assertEqual(policy.decide(snap(r, asked=frozenset(), settings=settings(model='Fable'))).kind, 'model')

    def test_a_family_nobody_is_known_to_use_never_moves_claude_code(self):
        # deep read b: before the first look at the chats (a start under load), the reported model weeks rank and
        # give words, but only the setting's family may cause a model move
        r = {'a': reading('a', week=30, opus=96), 'b': reading('b', week=20, opus=10)}
        d = policy.decide(snap(r, accounts=ACCTS[:2], asked=None))
        self.assertEqual(d.action, 'stay')
        self.assertNotEqual(d.kind, 'model')
        self.assertEqual(policy.decide(snap(r, accounts=ACCTS[:2], asked=None, settings=settings(model='Opus'))).kind,
                         'model')
        self.assertEqual(policy.decide(snap(r, accounts=ACCTS[:2], asked=frozenset(['opus']))).kind, 'model')

    def test_old_stored_settings_are_read(self):
        s = policy.settings_from({'session_at': 90, 'weekly_at': 95, 'model_at': 95, 'strategy': 'an-older-order'})
        self.assertEqual((s.pick_hold, s.sign_lines, s.sign_session_at, s.sign_weekly_at, s.sign_model_at),
                         ('default', False, 60.0, 65.0, 65.0))
        s = policy.settings_from({'pick_hold': 'until_out', 'sign_lines': True, 'sign_weekly_at': 'x'})
        self.assertEqual((s.pick_hold, s.sign_lines, s.sign_weekly_at), ('until_out', True, 65.0))
        self.assertEqual(policy.settings_from({'pick_hold': 'always'}).pick_hold, 'default')

    def test_the_poller_reads_near_the_sign_lines(self):
        # usage.Poller counts an account "near" from 15 points under these lines: from 45 on the 5-hour window and
        # 50 on the weekly and model weeks while sign lines are on, so the account in use is read every 90 s from there
        self.assertEqual(policy.read_lines(settings(sign_lines=True), ('fable',)),
                         {'5-hour': 60.0, 'weekly': 65.0, 'model:fable': 65.0})
        self.assertEqual(policy.read_lines(settings(sign_lines=False)), {'5-hour': 90.0, 'weekly': 95.0})

    def test_a_family_with_no_window_of_its_own_keeps_the_free_status_line_reads(self):
        # review S6: an asked family no account has a window for adds no model: line, so usage.Poller still takes
        # the account in use's numbers from Claude Code's status line instead of spending its reads on the endpoint
        from types import SimpleNamespace
        from switchboard.accounts import usage
        r = {'a': reading('a', week=20, fable=10), 'b': reading('b', week=20)}
        seen = policy.window_families(r)
        self.assertEqual(seen, frozenset(['fable']))
        lines = policy.read_lines(settings(), ('opus',), seen=seen)
        self.assertEqual(lines, {'5-hour': 90.0, 'weekly': 95.0})
        self.assertFalse(usage.Poller._model_counts(SimpleNamespace(lines=lines)))
        self.assertEqual(policy.read_lines(settings(), ('fable', 'opus'), seen=seen),
                         {'5-hour': 90.0, 'weekly': 95.0, 'model:fable': 95.0})
        self.assertEqual(policy.read_lines(settings(), ('opus',)), {'5-hour': 90.0, 'weekly': 95.0, 'model:opus': 95.0})

    def test_sign_lines_are_off_unless_turned_on(self):
        # lead's A3: each extra switch makes every open chat read its whole conversation again uncached
        self.assertFalse(policy.Settings().sign_lines)
        self.assertFalse(policy.settings_from({}).sign_lines)
        self.assertFalse(policy.settings_from({'sign_lines': 'yes'}).sign_lines)
        self.assertTrue(policy.settings_from({'sign_lines': True}).sign_lines)
        # no sign move
        r = {'a': reading('a', week=66), 'b': reading('b', week=16)}
        self.assertEqual(policy.decide(snap(r, accounts=ACCTS[:2], asked=frozenset())).action, 'stay')
        # no sign group in the order of targets: b refills first and is under its lines, not its sign lines
        r = {'a': reading('a', five=95, week=30), 'b': reading('b', week=61, week_reset=NOW + D),
             'c': reading('c', week=10, week_reset=NOW + 3 * D)}
        self.assertEqual(policy.decide(snap(r, asked=frozenset())).target, 'b')
        self.assertEqual(policy.decide(snap(r, asked=frozenset(), settings=settings(sign_lines=True))).target, 'c')
        # the poller's lines are the hard lines
        self.assertEqual(policy.read_lines(policy.settings_from({}), ('fable',)),
                         {'5-hour': 90.0, 'weekly': 95.0, 'model:fable': 95.0})

    def test_families(self):
        self.assertEqual(policy.family_of('claude-fable-5-2'), 'fable')
        self.assertEqual(policy.family_of('claude-opus-5-5[1m]'), 'opus')
        self.assertEqual(policy.family_of('model:Sonnet 5'), 'sonnet')
        self.assertIsNone(policy.family_of('gpt-x'))


class ForcedTargets(unittest.TestCase):
    """Rule 2: forced for everything; targets with room for the asked families first."""

    def test_room_for_every_asked_family_comes_first(self):
        r = {'a': reading('a', five=95, week=30),
             'b': reading('b', five=10, week=40, fable=100, week_reset=NOW + D),     # refills first, no Fable
             'c': reading('c', five=10, week=40, fable=10, week_reset=NOW + 3 * D)}
        d = policy.decide(snap(r, asked=FABLE, settings=settings(sign_lines=False)))
        self.assertEqual((d.action, d.kind, d.target), ('switch', 'forced', 'c'))

    def test_an_account_with_no_room_for_a_family_is_still_a_target(self):
        r = {'a': reading('a', five=95, week=30), 'b': reading('b', five=10, week=40, fable=100)}
        d = policy.decide(snap(r, accounts=ACCTS[:2], asked=FABLE))
        self.assertEqual((d.action, d.kind, d.target), ('switch', 'forced', 'b'))
        self.assertIn('no room for Fable there: chats run on Opus 5.5', d.reason)

    def test_a_target_in_its_sign_ins_last_days_comes_last(self):
        accts = (acct('a', 1), acct('b', 2, ends=NOW + 2 * D), acct('c', 3, ends=NOW + 20 * D))
        r = {'a': reading('a', five=95, week=30), 'b': reading('b', five=1, week=1, week_reset=NOW + D),
             'c': reading('c', five=40, week=50, week_reset=NOW + 5 * D)}
        self.assertEqual(policy.decide(snap(r, accounts=accts, asked=frozenset())).target, 'c')
        del r['c']
        accts = accts[:2]
        self.assertEqual(policy.decide(snap(r, accounts=accts, asked=frozenset())).target, 'b')   # nothing else


class FableFirst(unittest.TestCase):
    """Rule 3: the account in use has no room for an asked family; another has room for everything."""

    def test_a_refusal_in_a_chat_counts_as_no_room(self):
        r = {'a': reading('a', week=30, fable=40), 'b': reading('b', week=20, fable=20)}
        d = policy.decide(snap(r, accounts=ACCTS[:2], asked=FABLE, refused={'a': FABLE}))
        self.assertEqual((d.action, d.kind, d.target), ('switch', 'model', 'b'))
        self.assertIn('a request for it was refused', d.reason)
        # and a refused family is no room on a target either
        d = policy.decide(snap(r, accounts=ACCTS[:2], asked=FABLE, refused={'a': FABLE, 'b': FABLE}))
        self.assertEqual(d.action, 'stay')

    def test_the_target_needs_room_for_every_asked_family_and_fresh_numbers(self):
        r = {'a': reading('a', week=30, fable=100), 'b': reading('b', week=20, fable=20, opus=96),
             'c': reading('c', week=20, fable=20, age=901)}
        d = policy.decide(snap(r, asked=frozenset(['fable', 'opus'])))
        self.assertEqual((d.action, d.kind, d.hold), ('stay', 'model', 'no fresh numbers'))
        self.assertIn('c@acct.example', d.reason)
        self.assertIn('c', policy.unread_candidates(snap(r, asked=frozenset(['fable', 'opus']))))
        d = policy.decide(snap(r, asked=frozenset(['fable', 'opus']), candidates_read=True))
        self.assertEqual(d.action, 'stay')
        self.assertIsNone(d.target)
        # review S10: the words name what b lacks, never "no account has room for Fable"
        self.assertIn('b@acct.example has room for Fable but no room for Opus 5.5, so Claude Code stays.', d.reason)
        self.assertNotIn('No account has room for Fable', d.reason)

    def test_the_target_needs_ten_points_under_its_five_hour_and_weekly_lines(self):
        # deep read: with 5 points a forced move back follows within minutes; a model move is never urgent
        def d(five):
            r = {'a': reading('a', five=30, week=30, fable=100),
                 'b': reading('b', five=five, week=50, fable=40)}
            return policy.decide(snap(r, accounts=ACCTS[:2], asked=FABLE))
        self.assertEqual(d(84.0).action, 'stay')
        self.assertIn('b@acct.example has room for Fable but is near a line (5-hour 84%, weekly 50%)', d(84.0).reason)
        self.assertEqual((d(79.0).action, d(79.0).kind, d(79.0).target), ('switch', 'model', 'b'))

    def test_a_number_at_its_line_moves_right_after_a_switch(self):
        # A switch onto one account 1 s before; Fable at 100 there, room on another.
        # A number cannot flap, so no dwell: the move goes at the first calm moment
        r = {'a': reading('a', week=30, fable=100), 'b': reading('b', week=20, fable=20)}
        d = policy.decide(snap(r, accounts=ACCTS[:2], asked=FABLE, since_switch_s=1.0))
        self.assertEqual((d.action, d.kind, d.target), ('switch', 'model', 'b'))

    def test_two_accounts_that_both_refuse_trade_places_at_most_once_an_hour(self):
        # review S1: a refusal with no number (usage credits, a spend limit) on both accounts; each refusal lapses
        # after 10 minutes once the account is no longer in use
        r = {'a': reading('a', week=20, fable=40), 'b': reading('b', week=20, fable=40)}
        active, last_switch, refused_at, moves = 'a', -4 * H, {}, []
        for step in range(19):                       # three hours, every 10 minutes
            t = step * 600.0
            refused_at[active] = t                   # the chats keep asking on the account in use, and are refused
            refused = dict((x, FABLE) for x, at in refused_at.items() if t - at < 600.0)
            d = policy.decide(snap(r, accounts=ACCTS[:2], asked=FABLE, active=active, refused=refused,
                                   since_switch_s=t - last_switch))
            if d.action == 'switch':
                moves.append(t)
                active, last_switch = d.target, t
        self.assertTrue(moves, moves)
        self.assertTrue(all(b - a >= H for a, b in zip(moves, moves[1:])), moves)
        d = policy.decide(snap(r, accounts=ACCTS[:2], asked=FABLE, refused={'a': FABLE}, since_switch_s=1200.0))
        self.assertIn('with only a refusal to go by it waits an hour', d.hold)

    def test_a_fable_window_refilled_since_it_was_read_is_read_before_any_landing(self):
        r = {'a': reading('a', week=30, fable=100), 'b': reading('b', week=20, fable=None)}
        r['b'] = api.Reading('b', (win('weekly', 20, NOW + D), win('model:Fable', None, None)), NOW, 'endpoint')
        s = snap(r, accounts=ACCTS[:2], asked=FABLE)
        self.assertEqual(policy.decide(s).hold, 'no fresh numbers')
        self.assertEqual(policy.unread_candidates(s), ['b'])


class NeverEveryAccountOut(unittest.TestCase):
    """Rule 4: no hopping between accounts with no room for the family; the words say until when."""

    def test_used_up_everywhere(self):
        r = {'a': reading('a', week=50, fable=100, fable_reset=NOW + 2 * D),
             'b': reading('b', week=10, fable=100, fable_reset=NOW + D)}
        d = policy.decide(snap(r, accounts=ACCTS[:2], asked=FABLE))
        self.assertTrue(d.reason.startswith('Fable is used up on every account until '), d.reason)
        self.assertIn('(first: b@acct.example). Chats run on Opus 5.5 until then.', d.reason)

    def test_no_hop_between_fable_full_accounts(self):
        r = {'a': reading('a', week=50, fable=100, week_reset=NOW + 6 * D),
             'b': reading('b', week=10, fable=100, week_reset=NOW + D),
             'c': reading('c', week=10, fable=99, week_reset=NOW + D)}
        d = policy.decide(snap(r, asked=FABLE))
        self.assertEqual((d.action, d.target), ('stay', None))

    def test_every_account_out_only_when_no_weekly_or_five_hour_room(self):
        r = {'a': reading('a', five=100, week=40), 'b': reading('b', five=100, week=100),
             'c': reading('c', five=99, week=100)}
        d = policy.decide(snap(r, asked=FABLE))
        self.assertEqual(d.hold, 'every account is out')

    def test_still_fable_here_until_it_is_used_up(self):
        r = {'a': reading('a', week=50, fable=97), 'b': reading('b', week=10, fable=100)}
        d = policy.decide(snap(r, accounts=ACCTS[:2], asked=FABLE))
        self.assertIn('Chats stay on Fable on a@acct.example until it is used up, then run on Opus 5.5.', d.reason)


class CalmFirst(unittest.TestCase):
    """Rule 5: every automatic switch waits for a calm Mac, capped by how urgent it is."""

    def r(self, five):
        return {'a': reading('a', five=five, week=10), 'b': reading('b', five=5, week=5)}

    def test_a_blocked_account_never_waits(self):
        # at 100% every request of every chat is refused while a move waits (S: Failure's replay, Nov 27)
        d = policy.decide(snap(self.r(100), load_per_core=6.0))
        self.assertEqual((d.action, d.kind, d.target), ('switch', 'forced', 'b'))
        d = policy.decide(snap(self.r(100), load_per_core=40.0, rate_limited=True))
        self.assertEqual((d.action, d.kind, d.target), ('switch', 'forced', 'b'))

    def test_a_forced_switch_under_100_waits_two_minutes_at_most_too(self):
        # deep read b: the 5-hour window runs from 90 to 100 in about 4 minutes under heavy work
        wk = {'a': reading('a', five=10, week=96), 'b': reading('b', five=5, week=5)}
        for r in (wk, self.r(91), self.r(98)):
            self.assertEqual(policy.decide(snap(r, load_per_core=6.0, waited={'forced': 119.0})).action, 'stay')
            self.assertEqual(policy.decide(snap(r, load_per_core=6.0, waited={'forced': 120.0})).action, 'switch')
        self.assertEqual(policy.decide(snap(self.r(95), load_per_core=1.9)).action, 'switch')
        self.assertEqual(policy.decide(snap(self.r(95), load_per_core=None)).action, 'switch')   # not known

    def test_a_model_switch_waits_an_hour_at_most_whatever_the_load(self):
        # lead's A1: load under 2 for 30 minutes, then under 4 for 30 more, then it goes whatever the load
        r = {'a': reading('a', week=30, fable=100), 'b': reading('b', week=20, fable=20)}

        def d(load, waited):
            return policy.decide(snap(r, accounts=ACCTS[:2], asked=FABLE, load_per_core=load,
                                      waited={'model': waited}))
        self.assertEqual(d(15.0, 59 * 60.0).action, 'stay')
        self.assertEqual(d(15.0, 59 * 60.0).hold, 'waits for the Mac to calm down (load 15 per core), 60 s at most')
        self.assertEqual(d(15.0, 38 * 60.0).hold, 'waits for the Mac to calm down (load 15 per core), 22 min at most')
        self.assertIn('Next: b@acct.example; it waits for the Mac to calm down', d(15.0, 0.0).reason)
        self.assertEqual(d(15.0, 60 * 60.0).action, 'switch')
        self.assertEqual(d(3.0, 29 * 60.0).action, 'stay')
        self.assertEqual(d(3.0, 30 * 60.0).action, 'switch')
        self.assertEqual(d(1.0, 0.0).action, 'switch')
        self.assertEqual(d(3.0, 0.0).kind, 'model')

    def test_model_and_sign_moves_keep_the_guard_rails_of_an_optional_move(self):
        # deep read b: an open Doc or artifact, the restart settle and a low battery hold them (Claude Code working
        # does not: with many chats open it always is)
        r = {'a': reading('a', week=30, fable=100), 'b': reading('b', week=20, fable=20)}

        def d(**kw):
            return policy.decide(snap(r, accounts=ACCTS[:2], asked=FABLE, **kw))
        doc = ({'kind': 'doc', 'url': 'u', 'at': NOW - 60},)
        self.assertEqual((d(obligations=doc).action, d(obligations=doc).kind), ('stay', 'model'))
        self.assertTrue(d(obligations=doc).hold.startswith('a Claude Doc made as a@acct.example'), d(obligations=doc))
        self.assertEqual(d(no_optional='automatic switching was just restarted').action, 'stay')
        self.assertEqual(d(no_optional='whether Claude Code is busy is not known',
                           waited={'model': policy.CALM_MODEL_MAX_S}).action, 'switch')
        self.assertEqual(d(battery_pct=10.0, on_ac=False).hold, 'the battery is at 10%')
        self.assertEqual(d(busy=True, idle_for_s=0.0).action, 'switch')
        on = settings(sign_lines=True)
        rs = {'a': reading('a', week=66), 'b': reading('b', week=16)}
        self.assertEqual(policy.decide(snap(rs, accounts=ACCTS[:2], asked=frozenset(), settings=on,
                                            obligations=doc)).action, 'stay')
        # a forced move still goes
        rf = {'a': reading('a', five=95, week=30), 'b': reading('b', five=5, week=5)}
        self.assertEqual(policy.decide(snap(rf, accounts=ACCTS[:2], obligations=doc, battery_pct=10.0, on_ac=False,
                                            no_optional='restarted')).action, 'switch')

    def test_sign_and_optional_switches_never_go_over_4(self):
        on = settings(sign_lines=True)
        r = {'a': reading('a', week=66), 'b': reading('b', week=16)}
        d = policy.decide(snap(r, accounts=ACCTS[:2], asked=frozenset(), settings=on, load_per_core=15.0,
                               waited={'sign': 99999.0}))
        self.assertEqual((d.action, d.kind), ('stay', 'sign'))
        r = {'a': reading('a', five=20, week=40, week_reset=NOW + 5 * D),
             'b': reading('b', five=10, week=50, week_reset=NOW + 2 * D)}
        d = policy.decide(snap(r, accounts=ACCTS[:2], asked=frozenset(), load_per_core=15.0,
                               waited={'optional': 99999.0}))
        self.assertEqual((d.action, d.kind), ('stay', 'optional'))

    def test_an_optional_hold_for_a_busy_mac_reads_once(self):
        # deep read: "Next: b; waits: waits for the Mac to calm down" said "waits" twice
        r = {'a': reading('a', five=20, week=40, week_reset=NOW + 5 * D),
             'b': reading('b', five=10, week=50, week_reset=NOW + 2 * D)}
        d = policy.decide(snap(r, accounts=ACCTS[:2], asked=frozenset(), load_per_core=3.0))
        self.assertEqual(d.reason, 'Next: b@acct.example; it waits for the Mac to calm down (load 3 per core).')
        accts = (acct('a', 1, ends=NOW + 2 * D), acct('b', 2, ends=NOW + 25 * D))
        r = {'a': reading('a', week=20), 'b': reading('b', week=30)}
        d = policy.decide(snap(r, accounts=accts, asked=frozenset(), load_per_core=3.0))
        self.assertEqual(d.reason, 'Next: b@acct.example; it waits for the Mac to calm down (load 3 per core).')


class HerPickHolds(unittest.TestCase):
    """Rule 6: a pick of the person's own holds against optional, model and sign switches."""

    R = {'a': reading('a', week=30, fable=100), 'b': reading('b', week=20, fable=20)}

    def test_model_moves_wait_two_hours_and_say_so_once(self):
        d = policy.decide(snap(self.R, accounts=ACCTS[:2], asked=FABLE, pick_age_s=600.0))
        self.assertEqual((d.action, d.kind, d.target), ('stay', 'model', 'b'))
        self.assertTrue(d.reason.startswith('You picked a@acct.example at '), d.reason)
        self.assertIn('Fable has room on b@acct.example.', d.reason)
        d = policy.decide(snap(self.R, accounts=ACCTS[:2], asked=FABLE, pick_age_s=2 * H))
        self.assertEqual(d.action, 'switch')

    def test_until_out_and_off(self):
        s = snap(self.R, accounts=ACCTS[:2], asked=FABLE, pick_age_s=30 * H, settings=settings(pick_hold='until_out'))
        self.assertEqual(policy.decide(s).action, 'stay')
        s = snap(self.R, accounts=ACCTS[:2], asked=FABLE, pick_age_s=10.0, settings=settings(pick_hold='off'))
        self.assertEqual(policy.decide(s).action, 'switch')

    def test_a_forced_switch_still_goes(self):
        r = {'a': reading('a', five=95, week=30), 'b': reading('b', five=5, week=5)}
        s = snap(r, accounts=ACCTS[:2], pick_age_s=10.0, settings=settings(pick_hold='until_out'))
        self.assertEqual(policy.decide(s).action, 'switch')

    def test_optional_moves_wait_twelve_hours(self):
        r = {'a': reading('a', five=20, week=40, week_reset=NOW + 5 * D),
             'b': reading('b', five=10, week=50, week_reset=NOW + 2 * D)}
        d = policy.decide(snap(r, accounts=ACCTS[:2], asked=frozenset(), pick_age_s=11 * H))
        self.assertEqual((d.action, d.kind), ('stay', 'optional'))
        self.assertIn('You picked', d.reason)
        self.assertEqual(policy.decide(snap(r, accounts=ACCTS[:2], asked=frozenset(), pick_age_s=12 * H)).action,
                         'switch')


class OptionalNeedsTheFamilies(unittest.TestCase):
    """Rule 7: an optional move needs room for the asked families on the target."""

    def test_never_from_fable_room_to_none(self):
        # Nov 26 17:33: an optional move from Fable 0 to Fable 97 because the other refilled first
        r = {'a': reading('a', week=0, fable=0, week_reset=NOW + 7 * D),
             'b': reading('b', week=79, fable=97, week_reset=NOW + 2 * D)}
        for s in (settings(), settings(sign_lines=False, weekly_at=99.9)):
            d = policy.decide(snap(r, accounts=ACCTS[:2], asked=FABLE, settings=s))
            self.assertEqual((d.action, d.target), ('stay', None), s)


class FreshNumbers(unittest.TestCase):
    """Rule 8: no landing on old numbers, unless Claude Code can't work at all and the read just failed."""

    def accts(self):
        return (acct('a', 1), acct('b', 2))

    def test_keeping_signed_in_lands_on_old_numbers_only_when_it_cannot_work(self):
        old = {'a': reading('a', week=10), 'b': reading('b', week=10, age=6 * H)}
        s = snap(old, accounts=self.accts(), candidates_read=True, read_failed=frozenset(['b']),
                 ends_at=NOW + 300)
        self.assertEqual((policy.decide(s).action, policy.decide(s).hold), ('stay', 'no fresh numbers'))
        s = snap(old, accounts=self.accts(), candidates_read=True, read_failed=frozenset(['b']), lost='was refused')
        self.assertEqual(policy.decide(s).action, 'switch')
        s = snap(old, accounts=self.accts(), candidates_read=True, read_failed=frozenset(), lost='was refused')
        self.assertEqual(policy.decide(s).action, 'stay')


class SignInLastDays(unittest.TestCase):
    """Rule 9 (G5): a sign-in in its last 3 days moves to one that is good for longer, at a calm idle moment."""

    def test_it_moves_and_says_why(self):
        accts = (acct('a', 1, ends=NOW + 2 * D), acct('b', 2, ends=NOW + 25 * D))
        r = {'a': reading('a', week=20), 'b': reading('b', week=30)}
        d = policy.decide(snap(r, accounts=accts, asked=frozenset()))
        self.assertEqual((d.action, d.kind, d.target), ('switch', 'optional', 'b'))
        self.assertIn("a@acct.example's sign-in ends by ", d.reason)
        self.assertIn("b@acct.example's is good until ", d.reason)
        d = policy.decide(snap(r, accounts=accts, asked=frozenset(), busy=True, idle_for_s=0.0))
        self.assertEqual((d.action, d.hold), ('stay', 'Claude Code is working'))
        accts = (acct('a', 1, ends=NOW + 2 * D), acct('b', 2, ends=NOW + 2.5 * D))
        self.assertEqual(policy.decide(snap(r, accounts=accts, asked=frozenset())).action, 'stay')

    def test_it_moves_while_a_family_is_used_up_everywhere(self):
        # review S5: Fable used up on every account until Monday must not leave only the forced move at the end
        accts = (acct('a', 1, ends=NOW + 2 * D), acct('b', 2, ends=NOW + 25 * D))
        r = {'a': reading('a', week=20, fable=100), 'b': reading('b', week=30, fable=100, opus=10)}
        d = policy.decide(snap(r, accounts=accts, asked=frozenset(['fable', 'opus'])))
        self.assertEqual((d.action, d.kind, d.target), ('switch', 'optional', 'b'))
        self.assertIn("a@acct.example's sign-in ends by ", d.reason)
        # but never from an account with room for an asked family to one without it
        r['a'] = reading('a', week=20, fable=10)
        d = policy.decide(snap(r, accounts=accts, asked=frozenset(['fable', 'opus'])))
        self.assertEqual(d.action, 'stay')

    def test_never_onto_an_account_in_its_last_days_for_model_and_ending_moves(self):
        accts = (acct('a', 1), acct('b', 2, ends=NOW + 2 * D))
        r = {'a': reading('a', week=30, fable=100), 'b': reading('b', week=20, fable=20)}
        self.assertEqual(policy.decide(snap(r, accounts=accts, asked=FABLE)).action, 'stay')
        accts = (acct('a', 1, ends=NOW + 2 * D), acct('b', 2, ends=NOW + 2.5 * D))
        self.assertEqual(policy.decide(snap(r, accounts=accts, asked=FABLE)).action, 'stay')


class SignLines(unittest.TestCase):
    """Rule 12: stay under Claude Code's usage sign for as long as any account can (when the setting is on)."""

    ON = settings(sign_lines=True)

    def test_the_move_at_65_with_a_target_at_16(self):
        r = {'a': reading('a', week=66), 'b': reading('b', week=16)}
        d = policy.decide(snap(r, accounts=ACCTS[:2], asked=frozenset(), settings=self.ON))
        self.assertEqual((d.action, d.kind, d.target), ('switch', 'sign', 'b'))
        self.assertEqual(d.reason, 'a@acct.example is at 66% of its week. b@acct.example is at 16%.')

    def test_no_move_when_the_best_target_is_at_62(self):
        r = {'a': reading('a', week=66), 'b': reading('b', week=62)}
        self.assertEqual(policy.decide(snap(r, accounts=ACCTS[:2], asked=frozenset(), settings=self.ON)).action,
                         'stay')

    def test_refills_first_inside_the_under_sign_group(self):
        r = {'a': reading('a', week=70),
             'b': reading('b', week=50, week_reset=NOW + D),
             'c': reading('c', week=10, week_reset=NOW + 3 * D)}
        self.assertEqual(policy.decide(snap(r, asked=frozenset(), settings=self.ON)).target, 'b')
        r['b'] = reading('b', week=61, week_reset=NOW + D)          # under 65 but not by 5: hard lines only
        self.assertEqual(policy.decide(snap(r, asked=frozenset(), settings=self.ON)).target, 'c')

    def test_the_fable_week_counts_for_fable_chats(self):
        r = {'a': reading('a', week=20, fable=66), 'b': reading('b', week=20, fable=10)}
        d = policy.decide(snap(r, accounts=ACCTS[:2], asked=FABLE, settings=self.ON))
        self.assertEqual((d.kind, d.reason), ('sign', 'a@acct.example is at 66% of its Fable week. b@acct.example '
                                                       'is at 10%.'))
        self.assertEqual(policy.decide(snap(r, accounts=ACCTS[:2], asked=frozenset(['opus']),
                                            settings=self.ON)).action, 'stay')

    def test_twenty_minutes_since_the_last_switch(self):
        r = {'a': reading('a', week=66), 'b': reading('b', week=16)}
        d = policy.decide(snap(r, accounts=ACCTS[:2], asked=frozenset(), since_switch_s=600.0, settings=self.ON))
        self.assertEqual((d.action, d.kind), ('stay', 'sign'))
        self.assertIn('waits 20 minutes', d.hold)

    def test_all_over_sign_lines_falls_back_to_the_hard_lines_unchanged(self):
        r = {'a': reading('a', five=95, week=70), 'b': reading('b', five=70, week=75, week_reset=NOW + D),
             'c': reading('c', five=62, week=80, week_reset=NOW + 3 * D)}
        on, off = snap(r, asked=FABLE, settings=self.ON), snap(r, asked=FABLE, settings=settings(sign_lines=False))
        self.assertEqual(policy.decide(on), policy.decide(off))
        self.assertEqual(policy.decide(on).target, 'b')

    def test_sign_lines_off_never_moves_for_a_sign(self):
        r = {'a': reading('a', week=66), 'b': reading('b', week=16)}
        d = policy.decide(snap(r, accounts=ACCTS[:2], asked=frozenset(), settings=settings(sign_lines=False)))
        self.assertEqual(d.action, 'stay')


# refills-first half of the time, as when there were two orders: the only order with optional moves
STRATEGY_DRAW = ('refills-first', 'refills-first', 'most-room', 'stay-then-refills')


def random_snapshot(rnd):
    n = rnd.randint(2, 5)
    ids = 'abcde'[:n]
    accts = []
    for i, x in enumerate(ids):
        ends = None if rnd.random() < 0.6 else NOW + rnd.uniform(-1, 30) * D
        status = 'ok' if rnd.random() < 0.9 else rnd.choice(['needs_login', 'claimed', 'expiring'])
        accts.append(acct(x, i + 1, ends=ends, status=status))
    readings = {}
    for x in ids:
        if rnd.random() < 0.1:
            continue
        ws = []
        age = rnd.choice([0.0, 60.0, 600.0, 901.0, 6 * H])
        if rnd.random() < 0.5:
            ws.append(api.Window('5-hour', rnd.choice([None, rnd.uniform(0, 100), 100.0]), NOW + rnd.uniform(0, 5) * H,
                                 NOW - age, 'endpoint'))
        ws.append(api.Window('weekly', rnd.choice([rnd.uniform(0, 100), rnd.uniform(0, 100), 100.0, None]),
                             NOW + rnd.uniform(0, 7) * D, NOW - age, 'endpoint'))
        if rnd.random() < 0.8:
            ws.append(api.Window('model:Fable', rnd.choice([rnd.uniform(0, 100), 100.0, None]),
                                 NOW + rnd.uniform(0, 7) * D, NOW - rnd.choice([age, 0.0]), 'endpoint'))
        readings[x] = api.Reading(x, tuple(ws), NOW - age, 'endpoint', rnd.random() < 0.05, None)
    s = policy.Settings(enabled=True, model=rnd.choice([None, None, 'Fable']), sign_lines=rnd.random() < 0.7,
                        pick_hold=rnd.choice(policy.PICK_HOLDS), strategy=rnd.choice(STRATEGY_DRAW))
    refused = dict((x, frozenset(['fable'])) for x in ids if rnd.random() < 0.1)
    return policy.Snapshot(
        now=NOW, accounts=tuple(accts), readings=readings, settings=s, active=rnd.choice(ids),
        busy=rnd.random() < 0.3, idle_for_s=rnd.choice([None, 0.0, 400.0, 3600.0]),
        since_switch_s=rnd.choice([None, 60.0, 1300.0, 3 * H]), load_per_core=rnd.choice([None, 0.5, 2.5, 4.5, 9.0]),
        rate_limited=rnd.random() < 0.05, asked=rnd.choice([None, frozenset(), FABLE, frozenset(['fable', 'opus'])]),
        refused=refused, waited=dict((k, rnd.choice([0.0, 200.0, 1000.0, 2000.0, 4000.0]))
                                     for k in ('forced', 'model', 'sign', 'optional')),
        pick_age_s=rnd.choice([None, None, 600.0, 3 * H, 13 * H]),
        candidates_read=rnd.random() < 0.3, read_failed=rnd.choice([None, frozenset(), frozenset(ids[:1])]))


class ManyCases(unittest.TestCase):
    """Thousands of made-up snapshots from a fixed seed: the rules are never broken."""

    def test_the_rules_hold(self):
        rnd = random.Random(2737)
        seen = {}
        for i in range(20000):
            s = random_snapshot(rnd)
            d = policy.decide(s)
            seen[(d.action, d.kind)] = seen.get((d.action, d.kind), 0) + 1
            fams = policy.deciding_families(s)
            by_id = dict((a.id, a) for a in s.accounts)
            views = dict((a.id, policy.view_of(s, a)) for a in s.accounts)
            if d.action == 'switch':
                t = views[d.target]
                self.assertNotEqual(d.target, s.active, i)
                self.assertIn(by_id[d.target].status, policy.OK_STATUSES, i)
                fresh = t.known and t.age is not None and t.age <= policy.FORCED_MAX_AGE
                stale_ok = policy.stay_why(s) is not None and s.read_failed is not None and \
                    d.target in s.read_failed
                # the forced move on older numbers when Claude Code is stopped (refused login or requests): only on an
                # account that could not be read just now (None: the caller does not say which)
                stale_ok = stale_ok or (d.kind == 'forced' and policy._stopped(s, by_id[s.active]) and
                                        (s.read_failed is None or d.target in s.read_failed))
                self.assertTrue(fresh or stale_ok, (i, d))
                if d.kind in ('model', 'sign'):
                    self.assertTrue(policy.serves(t, fams, s.settings), (i, d))
                    self.assertFalse(policy.last_days(t, s.now), (i, d))
                if d.kind in ('sign', 'optional'):
                    self.assertTrue(s.load_per_core is None or s.load_per_core < policy.LOAD_HIGH, (i, d))
                if d.kind == 'model':
                    # a family the setting names waits as a forced move does (the lead's rule, Nov 27)
                    named = any(g in policy.named_families(s.settings)
                                for g in policy.lacks(views[s.active], fams, s.settings))
                    self.assertTrue(s.load_per_core is None or s.load_per_core < policy.LOAD_HIGH or
                                    s.waited.get('model', 0.0) >= (policy.CALM_FORCED_S if named else
                                                                   policy.CALM_MODEL_MAX_S), (i, d))
                    self.assertGreaterEqual(t.room, policy.MODEL_ROOM, (i, d))
                    av = views[s.active]
                    if all(policy._refused_only(av, g, s.settings) for g in policy.lacks(av, fams, s.settings)):
                        self.assertTrue(s.since_switch_s is None or s.since_switch_s >= policy.MODEL_REFUSED_DWELL,
                                        (i, d))
                    self.assertTrue(s.asked is not None or s.settings.model, (i, d))
                if d.kind == 'sign':
                    self.assertTrue(s.settings.sign_lines, i)
                    self.assertTrue(policy.serves(t, fams, s.settings, policy.SIGN_MARGIN, sign=True), (i, d))
                if s.load_per_core is not None and s.load_per_core >= policy.CALM_LOAD and d.kind == 'forced' and \
                        not policy.active_blocked(s):         # one that cannot work at all never waits
                    self.assertGreaterEqual(s.waited.get('forced', 0.0), policy.CALM_FORCED_S, (i, d))
            if d.hold == 'every account is out':
                for v in views.values():
                    if v.id != s.active and policy._eligible(by_id[v.id], s) and v.known and \
                            v.age is not None and v.age <= policy.FORCED_MAX_AGE:
                        self.assertLess(v.raw_room, policy.ESCAPE_ROOM, (i, d))
            self.assertNotIn('Every account is out', d.reason if d.hold != 'every account is out' else '')
            # when no account at all is under its sign lines, the sign lines change nothing
            if s.settings.sign_lines and not any(policy.serves(v, fams, s.settings, policy.SIGN_MARGIN, sign=True)
                                                 for v in views.values()) and \
                    not any(policy.serves(v, fams, s.settings, 0.0, sign=True, max_age=None)
                            for v in views.values()):
                off = dataclasses.replace(s, settings=dataclasses.replace(s.settings, sign_lines=False))
                self.assertEqual(policy.decide(off), d, i)
        for kind in ('forced', 'model', 'sign', 'optional'):
            self.assertGreater(seen.get(('switch', kind), 0), 5, (kind, seen))


if __name__ == '__main__':
    unittest.main()
