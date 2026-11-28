import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# One rule for a model's own week, everywhere (the lead's rule, Fri Nov 27): an account is unusable only by its own
# 5-hour and weekly windows (policy.unusable_by_windows, the one answer decide(), plan.py, autocheck.py and the
# providers' board use); a model's week (Fable's) decides where chats can run on that model, never whether an account
# can work. This morning's numbers (06:26): the Team account at 5-hour 91 with three accounts at weekly 77, 81 and 87
# whose Fable week was 98 to 100; her build said "Every account is out" for six hours.
import dataclasses
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import autocheck, plan  # noqa: E402
from switchboard.accounts import api, policy  # noqa: E402
from switchboard.providers import contract, place  # noqa: E402
from fakes_autoswitch import T0, account  # noqa: E402

H, D = 3600.0, 86400.0
NOW = T0
FIVE_RESET = NOW + 2 * H
FABLE_RESET = NOW + 3 * D
# weekly resets: B soonest, then C, then D, then the Team account, then E (refills-first picks B)
WEEK_RESET = {'team': NOW + 5 * D, 'b': NOW + 1 * D, 'c': NOW + 2 * D, 'd': NOW + 4 * D, 'e': NOW + 6 * D}
MORNING = {'team': (91, 45, 70), 'b': (20, 77, 100), 'c': (20, 81, 98), 'd': (20, 87, 100), 'e': (20, 96, 80)}
ACCTS = tuple(account(aid, n) for n, aid in enumerate(('team', 'b', 'c', 'd', 'e'), 1))
CALM = dict(busy=False, idle_for_s=1800.0, since_switch_s=4 * H, optional_recent=0, load_per_core=0.5,
            battery_pct=90.0, on_ac=True)


def reading(aid, five, week, fable):
    ws = (api.Window('5-hour', five, FIVE_RESET, NOW, 'endpoint'),
          api.Window('weekly', week, WEEK_RESET[aid], NOW, 'endpoint'),
          api.Window('model:Fable', fable, FABLE_RESET, NOW, 'endpoint'))
    return api.Reading(aid, ws, NOW, 'endpoint', False, None)


def morning(**change):
    nums = dict(MORNING)
    nums.update(change)
    return dict((aid, reading(aid, *n)) for aid, n in nums.items())


def snap(readings, **kw):
    base = dict(CALM, settings=policy.Settings(enabled=True, model='Fable'))
    base.update(kw)
    return policy.Snapshot(now=NOW, accounts=ACCTS, readings=readings, active='team', **base)


class TheOneAnswer(unittest.TestCase):
    """policy.unusable_by_windows: plain arguments, its own two windows, never a model's week."""

    def test_this_mornings_accounts(self):
        u = policy.unusable_by_windows
        self.assertEqual(u(NOW, 91, FIVE_RESET, 45, WEEK_RESET['team']), (True, FIVE_RESET))
        for aid in ('b', 'c', 'd'):
            five, week, _ = MORNING[aid]
            self.assertEqual(u(NOW, five, FIVE_RESET, week, WEEK_RESET[aid]), (False, None), aid)
        self.assertEqual(u(NOW, 20, FIVE_RESET, 96, WEEK_RESET['e']), (True, WEEK_RESET['e']))

    def test_its_own_lines_unknown_numbers_and_resets(self):
        u = policy.unusable_by_windows
        self.assertEqual(u(NOW, 85, FIVE_RESET, 10, NOW + D, session_line=80.0), (True, FIVE_RESET))
        self.assertEqual(u(NOW, 85, FIVE_RESET, 10, NOW + D), (False, None))
        self.assertEqual(u(NOW, None, None, None, None), (False, None))                 # nothing known: never
        self.assertEqual(u(NOW, 100, FIVE_RESET, 97, NOW + D), (True, NOW + D))         # both held: the later
        self.assertEqual(u(NOW, 95, None, 10, NOW + D), (True, None))                   # its reset not known
        self.assertEqual(u(NOW, 95, NOW - 60, 10, NOW + D), (True, None))               # its reset already passed

    def test_the_views_answer_is_the_same(self):
        r = morning()
        s = snap(r)
        for a in ACCTS:
            v = policy.view_of(s, a)
            five, week, _ = MORNING[a.id]
            self.assertEqual(policy.view_unusable(v, NOW),
                             policy.unusable_by_windows(NOW, five, FIVE_RESET, week, WEEK_RESET[a.id]), a.id)


class ThisMorning(unittest.TestCase):

    def test_a_forced_switch_to_an_account_with_weekly_room(self):
        d = policy.decide(snap(morning(), asked=frozenset()))
        self.assertEqual((d.action, d.kind, d.target), ('switch', 'forced', 'b'))      # refills-first: B first
        self.assertIn('team@acct.example 5-hour at 91% (line 90%)', d.reason)
        self.assertIn('(no room for Fable there until ', d.reason)          # B: Fable 100
        self.assertNotIn('Every account is out', d.reason)
        self.assertNotIn('run on', d.reason)                  # Keep running is off: no chat goes on by itself
        on = policy.decide(snap(morning(), asked=frozenset(), keep_running=True))
        self.assertEqual(on.target, 'b')
        self.assertIn('(no room for Fable there: chats run on Opus 5.5)', on.reason)

    def test_the_order_of_landings_is_the_joined_lines_own(self):
        s = snap(morning(), asked=frozenset(), settings=policy.Settings(enabled=True, model='Fable',
                                                                       strategy='most-room'))
        self.assertEqual(policy.decide(s).target, 'b')       # most room: B (13 under its weekly line) over C and D
        r = morning(c=(20, 81, 50))                           # C has room for Fable: it comes first
        self.assertEqual(policy.decide(snap(r, asked=frozenset())).target, 'c')

    def test_the_plan_says_b_c_d_usable(self):
        rows = dict((j['name'], j) for j in plan.order(plan_accounts(MORNING), plan_lines(), NOW))
        for aid in ('b', 'c', 'd'):
            self.assertEqual((rows[aid]['usable'], rows[aid]['reason']), (True, None), aid)
            self.assertEqual(rows[aid]['notes'], ['no room for Fable'], aid)
        self.assertEqual((rows['team']['usable'], rows['team']['reason'], rows['team']['back_at']),
                         (False, 'over its line', FIVE_RESET))
        self.assertEqual((rows['e']['usable'], rows['e']['reason'], rows['e']['back_at']),
                         (False, 'over its line', WEEK_RESET['e']))
        self.assertEqual(rows['e']['notes'], [])
        self.assertEqual(plan.next_account(plan_accounts(MORNING), plan_lines(), NOW)['account']['name'], 'b')
        self.assertEqual(plan.next_account(plan_accounts(MORNING), plan_lines(), NOW)['account']['name'],
                         policy.decide(snap(morning(), asked=frozenset())).target)       # the page's Next agrees

    def test_auto_check_says_b_c_d_can_take_it(self):
        s = snap(morning(), asked=frozenset())
        d = policy.decide(s)
        doc = autocheck.explain(s, d)
        by = dict((c['email'].split('@')[0], c) for c in doc['candidates'])
        for aid in ('b', 'c', 'd'):
            self.assertTrue(by[aid]['can_take_it'], (aid, by[aid]))
        self.assertEqual(by['e']['why_not'], 'at_line')
        self.assertIn('weekly 96%', by['e']['words'])
        self.assertFalse(any('Every account is out' in x or 'Nothing has room' in x for x in doc['reasons']))

    def test_the_providers_board_has_room(self):
        def seat(aid):
            five, week, fable = MORNING[aid]
            ws = (contract.Window('5-hour', five, FIVE_RESET, line=90.0),
                  contract.Window('weekly', week, WEEK_RESET[aid], line=95.0),
                  contract.Window('Fable', fable, FABLE_RESET, line=95.0, seat_wide=False))
            return contract.Limits(provider='claude', seat=aid, windows=ws, read_at=NOW, source='engine')
        room = place.room_of('claude', 'Claude', [seat(a) for a in MORNING], NOW, 900.0)
        self.assertEqual(room.state, contract.ROOM)
        only = place.room_of('claude', 'Claude', [seat('team'), seat('e')], NOW, 900.0)
        self.assertEqual((only.state, only.back_at), (contract.OUT, FIVE_RESET))

    def test_the_claude_adapter_marks_a_model_week(self):
        from switchboard.providers.claude import ClaudeProvider
        mk = ClaudeProvider._window
        fable = mk(None, api.Window('model:Fable', 100, FABLE_RESET, NOW, 'endpoint'), {})
        week = mk(None, api.Window('weekly', 50, NOW + D, NOW, 'endpoint'), {})
        self.assertEqual((fable.seat_wide, week.seat_wide), (False, True))

    def test_with_the_team_account_under_its_lines_it_stays(self):
        d = policy.decide(snap(morning(team=(50, 45, 70)), asked=frozenset()))
        self.assertEqual((d.action, d.target, d.kind), ('stay', None, None))
        self.assertIn('No other account has room for Fable until ', d.reason)
        self.assertNotIn('Every account is out', d.reason)
        rows = plan.order(plan_accounts(dict(MORNING, team=(50, 45, 70))), plan_lines(), NOW)
        self.assertEqual(rows[0]['name'], 'team')            # the account in use stays first


class TheLandingsWords(unittest.TestCase):
    """The reader's must (Nov 27, 14:28): at Fable 95 to 99 a Fable chat still works on the landing (refused at 100),
    so its words say so; the stop or step-down words only when Fable is used up or refused there."""

    def d(self, c_fable, keep):
        r = morning(b=(20, 77, 50), c=(20, 81, c_fable))       # B now has Fable room: C must refill first to win
        r['b'] = reading('b', 92, 77, 50)                        # B over its 5-hour line: no landing
        return policy.decide(snap(r, asked=frozenset(), keep_running=keep))

    def test_at_98_chats_stay_on_fable_there(self):
        for keep in (False, True):
            d = self.d(98, keep)
            self.assertEqual((d.action, d.kind, d.target), ('switch', 'forced', 'c'), keep)
            self.assertIn('(Fable is at 98% there, line 95%: chats stay on it until it is used up)', d.reason)
            self.assertNotIn('no room for Fable', d.reason)

    def test_at_100_the_words_follow_keep_running(self):
        off = self.d(100, False)
        self.assertEqual(off.target, 'c')
        self.assertIn('(no room for Fable there until ', off.reason)
        self.assertNotIn('run on', off.reason)
        on = self.d(100, True)
        self.assertIn('(no room for Fable there: chats run on Opus 5.5)', on.reason)

    def test_a_blocked_accounts_history_line_keeps_its_last_sentence(self):
        from switchboard import history
        long = tuple(dataclasses.replace(a, email='%s-%s@made-up.example' % (a.id, 'x' * (8 - len(a.id))))
                     for a in ACCTS)
        self.assertTrue(all(len(a.email) == 25 for a in long))
        s = dataclasses.replace(snap(morning(team=(100, 45, 70)), asked=frozenset()), accounts=long)
        d = policy.decide(s)
        self.assertEqual((d.action, d.target), ('switch', 'b'))
        note = policy.switch_note(d, s)
        self.assertTrue(note.endswith('If a reply stopped, send your message again.'), note)
        self.assertLessEqual(len(note), policy.HISTORY_ROOM)
        self.assertTrue(history.clean(note, history.REASON_MAX).endswith('send your message again.'))


class NeverWaitsWhenItCannotWork(unittest.TestCase):
    """S: Failure's replay (Nov 27): at 100% every request of every chat is refused while a move waits, so a forced
    move off an account that cannot work at all goes on the pass that sees it, whatever the load."""

    def test_this_morning_at_100_on_a_busy_mac(self):
        d = policy.decide(snap(morning(team=(100, 45, 70)), asked=frozenset(), load_per_core=6.0))
        self.assertEqual((d.action, d.kind, d.target), ('switch', 'forced', 'b'))

    def test_at_91_on_a_busy_mac_it_still_waits_two_minutes_at_most(self):
        d = policy.decide(snap(morning(), asked=frozenset(), load_per_core=6.0))
        self.assertEqual((d.action, d.kind, d.target), ('stay', 'forced', 'b'))
        self.assertIn('at most', d.hold)

    def test_a_lost_sign_in_goes_at_once(self):
        d = policy.decide(snap(morning(team=(50, 45, 70)), asked=frozenset(), load_per_core=6.0,
                               lost='was refused by Anthropic'))
        self.assertEqual((d.action, d.kind, d.target), ('switch', 'forced', 'b'))


class ReadNearALine(unittest.TestCase):
    """S: Failure's point 3: the account in use is read at the poller's fastest pace (NEAR_EVERY, 90 s) from 15 points
    under a hard line. With sign lines off read_lines gives the hard lines, and the poller's own NEAR_POINTS (15) makes
    "near" start 15 under them, so 75 on the 5-hour window and 80 on the week."""

    def test_near_from_fifteen_under_the_hard_lines(self):
        from switchboard.accounts import usage
        lines = policy.read_lines(policy.Settings(enabled=True, model='Fable'), ('fable',), 'team')
        self.assertEqual((lines['5-hour'], lines['weekly']), (90.0, 95.0))
        self.assertEqual((usage.NEAR_POINTS, usage.NEAR_EVERY), (15.0, 90.0))
        self.assertTrue(usage._near_wins({'5-hour': (75.0, FIVE_RESET)}, lines))
        self.assertFalse(usage._near_wins({'5-hour': (74.0, FIVE_RESET)}, lines))
        self.assertTrue(usage._near_wins({'weekly': (80.0, NOW + D)}, lines))


class NamedAndAsked(unittest.TestCase):
    """A family the setting names is a model move like an asked one; it waits for a calm Mac 2 minutes at most."""

    def r(self):
        return {'team': reading('team', 10, 40, 100), 'b': reading('b', 10, 20, 10)}

    def accts(self, s):
        return dataclasses.replace(s, accounts=ACCTS[:2])

    def test_a_named_family_waits_two_minutes_at_most(self):
        busy = dict(load_per_core=15.0, asked=frozenset())
        d = policy.decide(self.accts(snap(self.r(), waited={'model': 60.0}, **busy)))
        self.assertEqual((d.action, d.kind, d.target), ('stay', 'model', 'b'))
        self.assertIn('60 s at most', d.hold)
        d = policy.decide(self.accts(snap(self.r(), waited={'model': policy.CALM_FORCED_S}, **busy)))
        self.assertEqual((d.action, d.kind, d.target), ('switch', 'model', 'b'))

    def test_an_asked_family_not_named_keeps_its_hour(self):
        s = dataclasses.replace(snap(self.r(), load_per_core=15.0, asked=frozenset(['fable']),
                                     waited={'model': policy.CALM_FORCED_S}),
                                settings=policy.Settings(enabled=True))
        d = policy.decide(self.accts(s))
        self.assertEqual((d.action, d.kind), ('stay', 'model'))
        self.assertIn('at most', d.hold)

    def test_her_pick_holds_a_named_family_too(self):
        d = policy.decide(self.accts(snap(self.r(), asked=frozenset(), pick_age_s=600.0)))
        self.assertEqual((d.action, d.kind), ('stay', 'model'))
        self.assertTrue(d.hold.startswith('you picked'), d.hold)

    def test_a_model_week_nobody_named_or_asks_for_decides_nothing(self):
        s = dataclasses.replace(snap(self.r(), asked=frozenset()), settings=policy.Settings(enabled=True))
        self.assertNotEqual(policy.decide(self.accts(s)).kind, 'model')


class TheWordsFollowKeepRunning(unittest.TestCase):

    def test_no_room_anywhere(self):
        r = {'team': reading('team', 10, 40, 100), 'b': reading('b', 10, 20, 100)}
        base = dataclasses.replace(snap(r, asked=frozenset()), accounts=ACCTS[:2])
        off = policy.decide(base)
        self.assertIn('Fable is used up on every account until ', off.reason)
        self.assertIn('Chats that ask for Fable stop there until then; choose another model in the chat to go on.',
                      off.reason)
        self.assertNotIn('run on', off.reason)
        on = policy.decide(dataclasses.replace(base, keep_running=True))
        self.assertIn('Chats run on Opus 5.5 until then.', on.reason)


def plan_accounts(nums):
    out = []
    for aid, (five, week, fable) in nums.items():
        out.append({'name': aid, 'weekly_used_pct': week, 'weekly_resets_at': WEEK_RESET[aid],
                    'five_hour_used_pct': five, 'five_hour_resets_at': FIVE_RESET,
                    'scoped': [{'name': 'Fable', 'used_pct': fable, 'resets_at': FABLE_RESET}],
                    'disabled': False, 'needs_login': False, 'active': aid == 'team'})
    return out


def plan_lines():
    return {'session_line': 90, 'weekly_line': 95, 'model_line': 95, 'margin': 5, 'strategy': 'refills-first',
            'models': ['Fable']}


if __name__ == '__main__':
    unittest.main()
