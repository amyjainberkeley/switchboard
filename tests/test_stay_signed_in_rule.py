import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# Keeping Claude Code signed in, the rule alone (policy.decide is pure): when the sign-in in use is lost and its saved
# login can't be put back, or when it stops working within ten minutes, Claude Code moves to another signed-in account,
# also with automatic switching off. The table of ends_at(), the targets, the words. Made-up *.example accounts.
import dataclasses
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard.accounts import api, autoloop, policy, putback  # noqa: E402
from fakes_autoswitch import T0, account  # noqa: E402

H, D = 3600.0, 86400.0
NOW = T0
LOST_WORDS = dict(putback.MARKS)


def win(name, pct, resets=None, age=0.0):
    return api.Window(name, pct, resets, NOW - age, 'endpoint')


def reading(aid, five=10.0, week=20.0, age=0.0, week_reset=NOW + 4 * D):
    return api.Reading(aid, (win('5-hour', five, NOW + 3 * H, age), win('weekly', week, week_reset, age)),
                       NOW - age, 'endpoint', False, None)


def acct(aid, n, status='ok', ends=NOW + 20 * D, fp=None):
    return dataclasses.replace(account(aid, n, status=status), refresh_expires_at=ends, fingerprint=fp or 'fp-' + aid)


ACCTS = (acct('a', 1), acct('b', 2), acct('c', 3))
CALM = dict(busy=False, idle_for_s=1800.0, since_switch_s=4 * H, optional_recent=0, load_per_core=0.5,
            battery_pct=90.0, on_ac=True)


def snap(readings=None, active='a', accounts=ACCTS, enabled=True, stay=True, **kw):
    base = dict(CALM)
    base.update(kw)
    if readings is None:
        readings = dict((a.id, reading(a.id)) for a in accounts)
    return policy.Snapshot(now=base.pop('now', NOW), accounts=tuple(accounts), readings=readings,
                           settings=policy.Settings(enabled=enabled, stay_signed_in=stay), active=active, **base)


def lost(reason='was refused by Anthropic', **kw):
    """Claude Code lost its sign-in: the live login reads as absent, so it is not ok, and the put-back can't help."""
    kw.setdefault('live_ok', False)
    kw.setdefault('live_why', 'Claude Code is not signed in')
    return snap(lost=reason, **kw)


class TheSetting(unittest.TestCase):
    def test_on_by_default_and_a_missing_key_means_on(self):
        self.assertTrue(policy.Settings().stay_signed_in)
        self.assertTrue(policy.settings_from({}).stay_signed_in)
        self.assertTrue(policy.settings_from({'enabled': False}).stay_signed_in)
        self.assertTrue(policy.settings_from(None).stay_signed_in)

    def test_only_false_turns_it_off(self):
        self.assertFalse(policy.settings_from({'stay_signed_in': False}).stay_signed_in)
        for v in (True, 'no', 0, None, 1):
            self.assertTrue(policy.settings_from({'stay_signed_in': v}).stay_signed_in, v)

    def test_it_is_its_own_switch_beside_automatic_switching(self):
        s = policy.settings_from({'enabled': True, 'stay_signed_in': False})
        self.assertEqual((s.enabled, s.stay_signed_in), (True, False))
        s = policy.settings_from({'enabled': False})
        self.assertEqual((s.enabled, s.stay_signed_in), (False, True))


class Lost(unittest.TestCase):
    def test_each_reason_the_saved_login_cant_be_put_back_moves_it_at_once(self):
        for key in autoloop.LOST_MARKS:
            d = policy.decide(lost(LOST_WORDS[key]))
            self.assertEqual((d.action, d.kind, d.target), ('switch', 'forced', 'b'), key)
            self.assertTrue(d.reason.startswith('a@acct.example lost its sign-in and its saved login %s; '
                                                'b@acct.example is signed in and has room' % LOST_WORDS[key]),
                            d.reason)

    def test_the_put_back_limit_moves_it_too(self):
        d = policy.decide(lost('was put back 4 times within an hour and was lost again each time'))
        self.assertEqual((d.action, d.target), ('switch', 'b'))
        self.assertIn('a@acct.example lost its sign-in and its saved login was put back 4 times', d.reason)

    def test_with_nothing_lost_a_sign_in_that_cant_be_read_stays_as_today(self):
        d = policy.decide(snap(live_ok=False, live_why='Claude Code is not signed in'))
        self.assertEqual(d.action, 'stay')
        self.assertEqual(d.reason, 'No switch now: Claude Code is not signed in.')

    def test_not_on_a_saved_account_nothing_moves(self):
        self.assertEqual(policy.decide(lost(active=None)).action, 'stay')
        self.assertEqual(policy.decide(lost(active='zz')).action, 'stay')

    def test_settling_and_offline_wait_as_every_switch_does(self):
        self.assertEqual(policy.decide(lost(settling=True)).reason,
                         'The Mac just woke; waiting for the network before any switch.')
        self.assertEqual(policy.decide(lost(offline=True)).action, 'stay')


class TheEnd(unittest.TestCase):
    """ends_at(access expiry, sign-in end): Claude Code renews 300 s before the access token expires, and only while
    the sign-in's end is ahead."""

    E = NOW + 6 * H          # the live login's access expiry

    def test_an_end_before_the_renewal_moment_stops_it_at_that_moment(self):
        self.assertEqual(policy.ends_at(self.E, self.E - 300 - 3600), self.E - 300)
        self.assertEqual(policy.ends_at(self.E, self.E - 300), self.E - 300)

    def test_the_slack_is_the_same_sign_in_window_and_is_strict(self):
        self.assertEqual(policy.END_SLACK_S, 120.0)
        self.assertEqual(policy.ends_at(self.E, self.E - 300 + 119.999), self.E - 300)
        self.assertIsNone(policy.ends_at(self.E, self.E - 300 + 120.0))
        self.assertIsNone(policy.ends_at(self.E, self.E - 300 + 120.001))

    def test_an_end_well_after_renews_as_usual(self):
        self.assertIsNone(policy.ends_at(self.E, self.E + 3 * D))
        self.assertIsNone(policy.ends_at(self.E, self.E + 1))

    def test_an_unknown_end_or_access_expiry_says_nothing(self):
        self.assertIsNone(policy.ends_at(self.E, None))
        self.assertIsNone(policy.ends_at(None, self.E))
        self.assertIsNone(policy.ends_at(None, None))
        self.assertIsNone(policy.ends_at(True, self.E))
        self.assertIsNone(policy.ends_at(self.E, float('nan')))

    def test_an_access_token_that_outlives_the_end_by_hours_works_until_it_expires(self):
        self.assertEqual(policy.ends_at(self.E, self.E - 5 * H), self.E - 300)

    def test_an_end_moved_five_seconds_down_at_a_renewal_gives_the_same_answer(self):
        for end in (self.E - 3600, self.E - 300 + 60, self.E + 600, self.E + 3 * D):
            self.assertEqual(policy.ends_at(self.E, end), policy.ends_at(self.E, end - 5), end)


class EndsSoon(unittest.TestCase):
    """The clock stays at NOW (the numbers stay fresh) and the moment it stops moves instead."""

    def at(self, left, **kw):
        return policy.decide(snap(ends_at=NOW + left, **kw))

    def test_it_moves_from_ten_minutes_before_it_stops(self):
        d = self.at(600.5)
        self.assertEqual(d.action, 'stay')                              # today's: under its lines
        d = self.at(600)
        self.assertEqual((d.action, d.kind, d.target), ('switch', 'forced', 'b'))
        self.assertTrue(d.reason.startswith("a@acct.example's sign-in ends in 10 minutes; b@acct.example"), d.reason)
        d = self.at(299)
        self.assertIn("a@acct.example's sign-in ends in 5 minutes", d.reason)
        d = self.at(59)
        self.assertIn('ends in 1 minute;', d.reason)

    def test_one_that_has_stopped_says_so(self):
        d = self.at(-5)
        self.assertEqual(d.action, 'switch')
        self.assertIn("a@acct.example's sign-in has ended", d.reason)

    def test_not_while_the_live_login_cant_be_read(self):
        d = self.at(300, live_ok=False, live_why="Claude Code's login can't be read right now")
        self.assertEqual(d.action, 'stay')

    def test_no_end_known_no_move(self):
        d = policy.decide(snap(ends_at=None))
        self.assertEqual(d.action, 'stay')


class Targets(unittest.TestCase):
    def test_one_signed_in_account_with_room(self):
        accts = (acct('a', 1), acct('b', 2, status='needs_login'), acct('c', 3))
        d = policy.decide(lost(accounts=accts))
        self.assertEqual(d.target, 'c')

    def test_ranked_as_a_forced_switch_is(self):
        r = {'a': reading('a'), 'b': reading('b', five=5.0, week=5.0, week_reset=NOW + 5 * D),
             'c': reading('c', week_reset=NOW + 2 * D)}
        self.assertEqual(policy.decide(lost(readings=r)).target, 'c')            # the default order: refills first
        most = dataclasses.replace(lost(readings=r), settings=policy.Settings(enabled=True, strategy='most-room'))
        self.assertEqual(policy.decide(most).target, 'b')                        # most room

    def test_old_numbers_only_waits_for_one_read_then_goes_and_says_they_are_old(self):
        accts = (acct('a', 1), acct('b', 2))
        r = {'a': reading('a'), 'b': reading('b', five=30.0, week=40.0, age=3 * H + 300)}
        d = policy.decide(lost(accounts=accts, readings=r))
        self.assertEqual((d.action, d.hold), ('stay', 'no fresh numbers'))
        s = lost(accounts=accts, readings=r, candidates_read=True)
        d = policy.decide(s)
        self.assertEqual((d.action, d.kind, d.target), ('switch', 'forced', 'b'))
        self.assertIn('b@acct.example is signed in, but its numbers are 3 h 5 min old (5-hour 30%, weekly 40%)',
                      d.reason)
        self.assertIn("b@acct.example's numbers are 3 h 5 min old", policy.switch_note(d, s))

    def test_of_old_numbers_the_most_room_goes_and_no_numbers_come_last(self):
        accts = (acct('a', 1), acct('b', 2), acct('c', 3), acct('d', 4))
        r = {'a': reading('a'), 'b': reading('b', five=80.0, age=2 * H), 'c': reading('c', five=20.0, age=9 * H)}
        d = policy.decide(lost(accounts=accts, readings=r, candidates_read=True))
        self.assertEqual(d.target, 'c')
        d = policy.decide(lost(accounts=(acct('a', 1), acct('d', 2)), readings={'a': reading('a')},
                               candidates_read=True))
        self.assertEqual(d.target, 'd')
        self.assertIn('its numbers are not known yet', d.reason)

    def test_the_only_other_account_ends_within_the_hour_too_it_stays_and_says_so(self):
        accts = (acct('a', 1), acct('b', 2, status='expiring', ends=NOW + 30 * 60))
        d = policy.decide(lost(accounts=accts))
        self.assertEqual((d.action, d.kind, d.hold), ('stay', 'forced', 'no other account'))
        self.assertIn("no other account is signed in: b@acct.example's sign-in ends in 30 minutes", d.reason)
        d = policy.decide(snap(accounts=accts, now=NOW, ends_at=NOW + 300))
        self.assertEqual(d.action, 'stay')
        self.assertIn("a@acct.example's sign-in ends in 5 minutes, and no other account", d.reason)

    def test_the_hour_and_ten_minutes_is_the_edge(self):
        edge = policy.LEAVE_AHEAD_S + 3600
        d = policy.decide(lost(accounts=(acct('a', 1), acct('b', 2, ends=NOW + edge))))
        self.assertEqual(d.action, 'stay')
        d = policy.decide(lost(accounts=(acct('a', 1), acct('b', 2, ends=NOW + edge + 1))))
        self.assertEqual((d.action, d.target), ('switch', 'b'))
        d = policy.decide(lost(accounts=(acct('a', 1), acct('b', 2, ends=None))))
        self.assertEqual(d.target, 'b')                                  # an end not known is not "soon"

    def test_none_signed_in_it_stays_and_names_each_account_and_what_it_needs(self):
        accts = (acct('a', 1), acct('b', 2, status='needs_login'), acct('c', 3, status='conflict'),
                 acct('d', 4, status='claimed'), acct('e', 5, status='unreadable'))
        d = policy.decide(lost(accounts=accts))
        self.assertEqual(d.action, 'stay')
        for words in ('b@acct.example needs a sign-in', 'c@acct.example has a saved login that may belong to another',
                      'd@acct.example is in use by a `switchboard run`', "e@acct.example's saved login can't be read"):
            self.assertIn(words, d.reason)
        self.assertTrue(d.reason.startswith('a@acct.example lost its sign-in and its saved login was refused'))

    def test_an_account_left_for_a_lost_sign_in_is_no_target_until_it_is_signed_in_again(self):
        accts = (acct('a', 1), acct('b', 2))
        d = policy.decide(lost(active='b', accounts=accts, left=frozenset(['a'])))
        self.assertEqual(d.action, 'stay')
        self.assertIn('a@acct.example lost its sign-in and was not signed in again since', d.reason)
        self.assertEqual(policy.decide(lost(active='b', accounts=accts)).target, 'a')

    def test_no_other_switch_goes_back_to_an_account_left_for_a_lost_sign_in(self):
        r = {'a': reading('a', five=92.0), 'b': reading('b', five=1.0, week=1.0), 'c': reading('c')}
        self.assertEqual(policy.decide(snap(readings=r)).target, 'b')
        self.assertEqual(policy.decide(snap(readings=r, left=frozenset(['b']))).target, 'c')
        self.assertEqual(policy.decide(snap(readings=r, left=frozenset(['b']), stay=False)).target, 'b')
        self.assertEqual(policy.unread_candidates(snap(readings={}, left=frozenset(['b']))), ['c'])

    def test_an_account_moved_off_a_moment_ago_is_not_moved_off_again(self):
        d = policy.decide(lost(moved_recently=frozenset(['a'])))
        self.assertEqual((d.action, d.hold), ('stay', 'moved recently'))
        self.assertEqual(policy.decide(lost(moved_recently=frozenset(['b']))).action, 'switch')

    def test_no_account_under_its_lines_still_one_that_can_work(self):
        r = {'a': reading('a'), 'b': reading('b', five=93.0), 'c': reading('c', five=100.0)}
        d = policy.decide(lost(readings=r))
        self.assertEqual(d.target, 'b')
        self.assertIn('no account is under its lines, but b@acct.example is signed in and can still work', d.reason)

    def test_every_other_account_out_it_stays(self):
        r = {'a': reading('a'), 'b': reading('b', week=100.0), 'c': reading('c', five=100.0)}
        d = policy.decide(lost(readings=r))
        self.assertEqual((d.action, d.hold), ('stay', 'no account has room'))
        self.assertIn('no other signed-in account has room', d.reason)


def ruled(s, rules=(), ranks=()):
    """s with accounts' rules ('last', 'out') and ranks, as the settings store them."""
    return dataclasses.replace(s, settings=dataclasses.replace(s.settings, rules=tuple(sorted(rules)),
                                                               ranks=tuple(sorted(ranks))))


class TheRulesOfAutoSwitch(unittest.TestCase):
    """The move takes the forced switch's targets and order: never an account kept out of auto-switch, one used last
    only when no other has room, a lower rank first; and it moves Claude Code off an account kept out, saying so."""

    def test_an_account_kept_out_is_never_the_target_and_another_signed_in_one_is(self):
        d = policy.decide(ruled(lost(), rules=[('b', 'out')]))
        self.assertEqual((d.action, d.kind, d.target), ('switch', 'forced', 'c'))

    def test_with_only_an_account_kept_out_left_it_stays_and_names_it(self):
        accts = (acct('a', 1), acct('b', 2))
        d = policy.decide(ruled(lost(accounts=accts), rules=[('b', 'out')]))
        self.assertEqual((d.action, d.kind, d.hold), ('stay', 'forced', 'no other account'))
        self.assertIn('no other account auto-switch may use is signed in: b@acct.example is kept out of auto-switch',
                      d.reason)

    def test_it_moves_off_an_account_kept_out_and_says_so(self):
        s = ruled(snap(ends_at=NOW + 300), rules=[('a', 'out')])
        d = policy.decide(s)
        self.assertEqual((d.action, d.kind, d.target), ('switch', 'forced', 'b'))
        self.assertIn("a@acct.example's sign-in ends in 5 minutes; it is kept out of auto-switch, but Claude Code "
                      "can't stay signed in there", d.reason)
        self.assertIn('although it was on an account kept out of auto-switch', policy.switch_note(d, s))

    def test_used_last_only_when_no_other_has_room_and_a_lower_rank_first(self):
        accts = (acct('a', 1), acct('b', 2), acct('c', 3), acct('d', 4))
        rules, ranks = [('b', 'last')], [('c', 1)]

        def target(full=()):
            r = dict((x.id, reading(x.id, five=100.0 if x.id in full else 10.0)) for x in accts)
            return policy.decide(ruled(lost(accounts=accts, readings=r), rules, ranks))
        d = target()
        self.assertEqual(d.target, 'c')                                  # ranked 1, before d (no rank) and b (last)
        self.assertIn('c@acct.example, rank 1, is signed in and has room', d.reason)
        self.assertEqual(target(full=('c',)).target, 'd')
        d = target(full=('c', 'd'))
        self.assertEqual(d.target, 'b')                                  # used last: only now
        self.assertIn('b@acct.example, used last, is signed in and has room', d.reason)


class WithAutomaticSwitchingOff(unittest.TestCase):
    def test_the_two_reasons_still_move_it(self):
        d = policy.decide(lost(enabled=False))
        self.assertEqual((d.action, d.target), ('switch', 'b'))
        d = policy.decide(snap(enabled=False, ends_at=NOW + 300))
        self.assertEqual((d.action, d.target), ('switch', 'b'))

    def test_a_limit_never_moves_it(self):
        r = {'a': reading('a', five=100.0), 'b': reading('b'), 'c': reading('c')}
        d = policy.decide(snap(readings=r, enabled=False))
        self.assertEqual(d.reason, 'Automatic switching is off.')
        self.assertEqual(policy.decide(snap(readings=r, enabled=False, ends_at=NOW + 3 * H)).action, 'stay')

    def test_with_the_setting_off_nothing_moves(self):
        self.assertEqual(policy.decide(lost(enabled=False, stay=False)).reason, 'Automatic switching is off.')
        self.assertEqual(policy.decide(snap(enabled=False, stay=False, ends_at=NOW)).reason,
                         'Automatic switching is off.')

    def test_with_the_setting_off_and_switching_on_it_is_todays_rule(self):
        self.assertEqual(policy.decide(lost(stay=False)).reason, 'No switch now: Claude Code is not signed in.')
        self.assertEqual(policy.decide(snap(stay=False, ends_at=NOW)),
                         policy.decide(snap(stay=False)))


class Words(unittest.TestCase):
    def note(self, s):
        d = policy.decide(s)
        self.assertEqual(d.action, 'switch')
        return policy.switch_note(d, s)

    def test_a_lost_sign_in(self):
        self.assertEqual(self.note(lost()),
                         'a@acct.example lost its sign-in and its saved login was refused by Anthropic. Switchboard '
                         'moved Claude Code to b@acct.example. Sign in to a@acct.example again when you like.')

    def test_a_sign_in_that_ends(self):
        self.assertEqual(self.note(snap(ends_at=NOW + 600)),
                         "a@acct.example's sign-in ends in 10 minutes. Switchboard moved Claude Code to "
                         "b@acct.example. Sign in to a@acct.example again when you like.")

    def test_one_that_has_ended(self):
        self.assertEqual(self.note(snap(ends_at=NOW - 1)),
                         "a@acct.example's sign-in has ended. Switchboard moved Claude Code to b@acct.example. Sign in "
                         "to a@acct.example again when you like.")

    def test_it_says_when_automatic_switching_is_off(self):
        self.assertIn('moved Claude Code to b@acct.example, although automatic switching is off. Sign in',
                      self.note(lost(enabled=False)))
        self.assertIn('b@acct.example, although automatic switching is off. Sign in',
                      self.note(snap(enabled=False, ends_at=NOW + 600)))

    def test_no_promise_in_either_note(self):
        # B's sign-in can end too, and a chat that asked while the login was gone stays at its sign-in screen: no
        # note may promise what happens to the chats, or say never or always
        old = {'a': reading('a'), 'b': reading('b', age=3 * H), 'c': reading('c', age=3 * H)}
        cases = [lost(LOST_WORDS[k]) for k in autoloop.LOST_MARKS]
        cases += [snap(ends_at=NOW + 600), snap(ends_at=NOW - 1), lost(enabled=False),
                  snap(enabled=False, ends_at=NOW + 600), lost(readings=old, candidates_read=True)]
        # a sign-in that still works for 10 minutes never moves on old numbers
        self.assertEqual(policy.decide(snap(readings=old, candidates_read=True, ends_at=NOW + 600)).hold,
                         'no fresh numbers')
        for s in cases:
            d = policy.decide(s)
            self.assertEqual(d.action, 'switch', d.reason)
            for short in (False, True):
                text = policy.stay_note(d, s, short=short).lower()
                for word in ('keep working', 'never', 'always'):
                    self.assertNotIn(word, text, (short, text))

    def test_whole_emails_and_never_a_token(self):
        accts = (dataclasses.replace(acct('a', 1), email='ana.long.name@work.example'),
                 dataclasses.replace(acct('b', 2), email='ana@home.example'))
        text = self.note(lost(accounts=accts))
        self.assertIn('ana.long.name@work.example lost its sign-in', text)
        self.assertIn('to ana@home.example.', text)
        self.assertNotIn('sk-ant', text)
        self.assertNotIn('fp-', text)

    def test_the_history_sentence_keeps_every_part(self):
        # (only the notification is built to fit; the history's sentence says it all)
        old = {'a': reading('a'), 'b': reading('b', age=2 * H + 300), 'c': reading('c', age=2 * H + 300)}
        self.assertEqual(self.note(lost(enabled=False, readings=old, candidates_read=True)),
                         'a@acct.example lost its sign-in and its saved login was refused by Anthropic. Switchboard '
                         "moved Claude Code to b@acct.example, although automatic switching is off. b@acct.example's "
                         'numbers are 2 h 5 min old (5-hour 10%, weekly 20%). Sign in to a@acct.example again when '
                         'you like.')

    def test_a_move_counts_as_blocked_and_other_switches_keep_their_words(self):
        self.assertTrue(policy.active_blocked(lost()))
        self.assertTrue(policy.active_blocked(snap(ends_at=NOW + 60)))
        self.assertFalse(policy.active_blocked(snap(ends_at=NOW + 3 * H)))
        r = {'a': reading('a', five=92.0), 'b': reading('b'), 'c': reading('c')}
        s = snap(readings=r)
        d = policy.decide(s)
        self.assertEqual(policy.switch_note(d, s), d.reason)


# The notification's body (stay_note(short=True)): notify.py cuts a body past 240 characters, and what it cut was the
# end, the sign-in-again sentence, inside its email. So the body is built from what must stay, each email whole: why
# (one clause), where Claude Code is now, the sign-in-again sentence; then that the numbers are old, then that
# automatic switching is off, each only when the whole body still fits, whole or not at all.
LONG_A = 'a' * 27 + '@work.example'                     # 40 characters each
LONG_B = 'b' * 27 + '@home.example'
LIMIT_WORDS = ('was put back again and again within minutes and was lost again each time',
               'was put back %d times within an hour and was lost again each time' % putback.PUT_BACK_MAX)
WHY_SHORT = {'lost': 'Claude Code lost its sign-in.', 'ends': "Claude Code's sign-in ends in 10 minutes.",
             'ended': "Claude Code's sign-in has ended."}
OFF = ', although automatic switching is off'


def short_parts(why, a, b, old=None, off=False):
    """The body from its parts, in the order it reads."""
    return '%s Switchboard moved Claude Code to %s%s.%s Sign in to %s again when you like.' % (
        WHY_SHORT[why], b, OFF if off else '', (" %s's numbers are %s." % (b, old)) if old else '', a)


class TheNotification(unittest.TestCase):
    def accounts(self, a=LONG_A, b=LONG_B):
        return (dataclasses.replace(acct('a', 1), email=a), dataclasses.replace(acct('b', 2), email=b))

    def body(self, s):
        d = policy.decide(s)
        self.assertEqual((d.action, d.target), ('switch', 'b'), d.reason)
        return policy.stay_note(d, s, short=True)

    def every_move(self, a=LONG_A, b=LONG_B):
        """(kind, snapshot, the old-numbers words or None) for every kind of move with its longest words."""
        accts = self.accounts(a, b)
        numbers = (({'a': reading('a'), 'b': reading('b')}, None),
                   ({'a': reading('a'), 'b': reading('b', age=2 * H + 300)}, '2 h 5 min old'),
                   ({'a': reading('a')}, 'not known yet'))
        for readings, old in numbers:
            for enabled in (True, False):
                base = dict(accounts=accts, readings=readings, enabled=enabled, candidates_read=True)
                for words in [LOST_WORDS[k] for k in autoloop.LOST_MARKS] + list(LIMIT_WORDS):
                    yield 'lost', lost(words, **base), old
                if old is None:         # a sign-in that still works for 10 minutes never moves on old numbers
                    yield 'ends', snap(ends_at=NOW + 600, **base), old
                yield 'ended', snap(ends_at=NOW - 1, **base), old

    def test_with_two_40_character_emails_every_body_fits_and_keeps_what_must_stay(self):
        from switchboard import notify
        for why, s, old in self.every_move():
            body = self.body(s)
            with self.subTest(why=why, lost=s.lost, enabled=s.settings.enabled, old=old):
                self.assertLessEqual(len(body), policy.NOTE_ROOM, body)
                self.assertEqual(notify._clean(body, 240), body)            # shown as it is: nothing cut
                self.assertTrue(body.startswith(WHY_SHORT[why]), body)
                self.assertIn(' Switchboard moved Claude Code to %s' % LONG_B, body)
                self.assertTrue(body.endswith(' Sign in to %s again when you like.' % LONG_A), body)
                self.assertIn(body, [short_parts(why, LONG_A, LONG_B, o, f) for o in (None, old)
                                     for f in (False, True)])

    def test_each_optional_part_is_there_when_it_fits_and_left_out_whole_when_not(self):
        s = lost(accounts=self.accounts('a@acct.example', 'b@acct.example'), enabled=False, candidates_read=True,
                 readings={'a': reading('a'), 'b': reading('b', age=2 * H + 300)})
        self.assertEqual(self.body(s), short_parts('lost', 'a@acct.example', 'b@acct.example', '2 h 5 min old', True))
        # with 40-character emails the old numbers do not fit; that automatic switching is off does
        s = lost(accounts=self.accounts(), enabled=False, candidates_read=True,
                 readings={'a': reading('a'), 'b': reading('b', age=2 * H + 300)})
        self.assertEqual(self.body(s), short_parts('lost', LONG_A, LONG_B, None, True))
        # 40 and 30 characters: the old numbers fit, and then that it is off does not
        b30 = 'b' * 17 + '@home.example'
        s = lost(accounts=self.accounts(LONG_A, b30), enabled=False, candidates_read=True,
                 readings={'a': reading('a'), 'b': reading('b', age=2 * H + 300)})
        self.assertEqual(self.body(s), short_parts('lost', LONG_A, b30, '2 h 5 min old', False))
        self.assertLessEqual(len(short_parts('lost', LONG_A, b30, '2 h 5 min old', False)), policy.NOTE_ROOM)
        self.assertGreater(len(short_parts('lost', LONG_A, b30, '2 h 5 min old', True)), policy.NOTE_ROOM)

    def test_short_emails_keep_every_part_and_switching_on_says_nothing_of_it(self):
        s = snap(accounts=self.accounts('a@acct.example', 'b@acct.example'), ends_at=NOW + 600)
        self.assertEqual(self.body(s), "Claude Code's sign-in ends in 10 minutes. Switchboard moved Claude Code to "
                                       "b@acct.example. Sign in to a@acct.example again when you like.")

    def test_the_rule_holds_over_many_made_up_emails(self):
        import random
        rnd = random.Random(20261125)
        for n in range(3000):
            la, lb = rnd.randint(12, 65), rnd.randint(12, 65)       # two emails of up to 131 characters together fit
            a = 'x' * (la - len('@a.example')) + '@a.example'
            b = 'y' * (lb - len('@b.example')) + '@b.example'
            why = rnd.choice(('lost', 'ends', 'ended'))
            enabled = rnd.random() < 0.5
            age = rnd.choice((0.0, 2 * H + 300, None))
            readings = {'a': reading('a')}
            if age is not None:
                readings['b'] = reading('b', age=age)
            old = None if age == 0.0 else '2 h 5 min old' if age else 'not known yet'
            base = dict(accounts=self.accounts(a, b), readings=readings, enabled=enabled, candidates_read=True)
            s = lost(rnd.choice([LOST_WORDS[k] for k in autoloop.LOST_MARKS] + list(LIMIT_WORDS)), **base) \
                if why == 'lost' else snap(ends_at=NOW + 600 if why == 'ends' else NOW - 1, **base)
            if why == 'ends' and old is not None:
                self.assertEqual(policy.decide(s).hold, 'no fresh numbers')   # no move on old numbers
                continue
            body = self.body(s)
            where = (n, la, lb, why, enabled, old, body)
            self.assertLessEqual(len(body), policy.NOTE_ROOM, where)
            fits = [(o, f) for o in ((None, old) if old else (None,)) for f in ((False, True) if not enabled else
                                                                                 (False,))
                    if len(short_parts(why, a, b, o, f)) <= policy.NOTE_ROOM]
            # the old numbers first, then that it is off: the first that fits of the orders below is the body
            best = [(o, f) for o, f in ((old, not enabled), (old, False), (None, not enabled), (None, False))
                    if (o, f) in fits][0]
            self.assertEqual(body, short_parts(why, a, b, *best), where)


if __name__ == '__main__':
    unittest.main()
