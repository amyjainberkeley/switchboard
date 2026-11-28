import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Tests for the switching order automatic switching uses (our own rule, the engine's policy's) and "the week ahead"
# (switchboard/plan.py).
#
# All accounts here are made up. Expected numbers are worked out by hand in the comments.
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import plan

NOW = 1_800_000_000          # a whole minute, so "same minute" ties are easy to build
D = 86400
H = 3600
LINES = {'weekly_line': 95, 'session_line': 90, 'model_line': 95, 'margin': 5}


def acct(name, used, resets_in_days, **more):
    a = {'name': name, 'weekly_used_pct': used, 'weekly_resets_at': NOW + resets_in_days * D,
         'five_hour_used_pct': None, 'five_hour_resets_at': None, 'scoped': [],
         'disabled': False, 'needs_login': False, 'active': False}
    a.update(more)
    return a


def names(ranked):
    return [a['name'] for a in ranked]


def five_accounts(models=()):
    """Five made-up accounts, in slot order: the one in use 9% used and refilling in a day; one 65% used whose Fable
    week is 92% used, two days; 93% used, three days; 17%, three days; an unused one, six days. Lines 90 (5-hour), 95
    (weekly) and 95 (a counted model's week); a landing place keeps 5 points under every counted line."""
    return [acct('in-use', 9, 1, active=True),
            acct('fable-92', 65, 2, scoped=[{'name': 'Fable', 'used_pct': 92, 'resets_at': NOW + 2 * D}]),
            acct('ninety-three', 93, 3), acct('seventeen', 17, 3), acct('unused', 0, 6)]


def lines(strategy, models=()):
    return dict(LINES, strategy=strategy, models=list(models))


class OurOrder(unittest.TestCase):
    """Room is the points under the nearest counted line: in-use 86, fable-92 30 (its Fable week counted: 3),
    ninety-three 2, seventeen 78, unused 95. A landing place has at least 5 everywhere. The account in use stays first
    while it is under its lines; then refills first (soonest weekly reset, then the most room) or the most room."""

    CASES = {
        # strategy, counted models: (order, next)
        ('refills-first', ()): (['in-use', 'fable-92', 'seventeen', 'unused', 'ninety-three'], 'fable-92'),
        ('refills-first', ('Fable',)): (['in-use', 'seventeen', 'unused', 'fable-92', 'ninety-three'], 'seventeen'),
        ('most-room', ()): (['in-use', 'unused', 'seventeen', 'fable-92', 'ninety-three'], 'unused'),
        ('most-room', ('Fable',)): (['in-use', 'unused', 'seventeen', 'fable-92', 'ninety-three'], 'unused'),
    }

    def test_five_accounts_under_each_strategy(self):
        for (strategy, models), (want, nxt) in self.CASES.items():
            with self.subTest(strategy=strategy, models=models):
                self.assertEqual(names(plan.order(five_accounts(), lines(strategy, models), NOW)), want)
                self.assertEqual(plan.next_account(five_accounts(), lines(strategy, models), NOW)['account']['name'],
                                 nxt)

    def test_what_the_rows_say(self):
        ranked = dict((a['name'], a) for a in plan.order(five_accounts(), lines('most-room', ['Fable']), NOW))
        self.assertEqual([(ranked[n]['rank'], ranked[n]['usable'], ranked[n]['reason'], ranked[n]['left_pct'])
                          for n in ('in-use', 'unused', 'seventeen', 'fable-92', 'ninety-three')],
                         [(1, True, None, 86), (2, True, None, 95), (3, True, None, 78), (4, True, None, 30),
                          (5, False, 'not enough room', 2)])
        # a counted model's week is a note and an order, never a reason (the lead's rule, Nov 27)
        self.assertEqual((ranked['fable-92']['blocked_by'], ranked['fable-92']['back_at'], ranked['fable-92']['notes']),
                         ([], None, ['no room for Fable']))
        self.assertEqual((ranked['ninety-three']['blocked_by'], ranked['ninety-three']['back_at']), (['weekly'], NOW + 3 * D))
        self.assertFalse(any(a['fallback'] for a in ranked.values()))      # nothing is landed on over a line

    def test_a_per_model_week_counts_only_when_named(self):
        a = [acct('me', 10, 1, active=True), acct('other', 10, 2, scoped=[{'name': 'Opus', 'used_pct': 99,
                                                                          'resets_at': NOW + D}])]
        self.assertEqual(plan.next_account(a, lines('refills-first', ['Fable']), NOW)['account']['name'], 'other')
        # names: any case; a counted week over its line is a note, and the account is still the next (no other)
        ranked = dict((x['name'], x) for x in plan.order(a, lines('refills-first', ['opus']), NOW))
        self.assertEqual((ranked['other']['usable'], ranked['other']['notes']), (True, ['no room for Opus']))
        self.assertEqual(plan.next_account(a, lines('refills-first', ['opus']), NOW)['account']['name'], 'other')
        self.assertEqual(plan.next_account(a, lines('refills-first', ['none']), NOW)['account']['name'], 'other')

    def test_inputs_are_not_changed(self):
        accounts = five_accounts()
        before = repr(accounts)
        plan.order(accounts, lines('refills-first', ['Fable']), NOW)
        self.assertEqual(repr(accounts), before)

    def test_equal_keys_keep_the_given_order(self):
        given = [acct('me', 50, 1, active=True), acct('b', 10, 2), acct('a', 10, 2), acct('c', 10, 2)]
        self.assertEqual(names(plan.order(given, lines('refills-first'), NOW)), ['me', 'b', 'a', 'c'])
        self.assertEqual(names(plan.order(given, lines('most-room'), NOW)), ['me', 'b', 'a', 'c'])

    def test_never_within_the_margin_of_a_line(self):
        given = [acct('me', 50, 1, active=True), acct('ninety', 90, 1), acct('ninety-one', 91, 1)]
        ranked = dict((a['name'], a) for a in plan.order(given, lines('refills-first'), NOW))
        self.assertEqual((ranked['ninety']['usable'], ranked['ninety-one']['usable'],
                          ranked['ninety-one']['reason']), (True, False, 'not enough room'))   # 5 points is enough
        wide = dict(LINES, strategy='refills-first', margin=10)
        self.assertIsNone(plan.next_account(given, wide, NOW)['account'])

    def test_unknown_or_past_resets_go_last_when_ranking_by_refill(self):
        given = [acct('me', 50, 1, active=True), dict(acct('unknown', 10, 1), weekly_resets_at=None),
                 acct('soon', 40, 2), acct('past', 10, -1)]
        self.assertEqual(names(plan.order(given, lines('refills-first'), NOW)), ['me', 'soon', 'unknown', 'past'])

    def test_the_five_hour_window_and_its_line(self):
        def five(used):
            return acct('other', 10, 2, five_hour_used_pct=used, five_hour_resets_at=NOW + 2 * H)
        me = acct('me', 10, 1, active=True)
        self.assertEqual(plan.next_account([me, five(85)], LINES, NOW)['account']['name'], 'other')
        nxt = plan.next_account([me, five(86)], LINES, NOW)                 # 4 points under 90: not enough room
        self.assertEqual((nxt['account'], nxt['back_at']), (None, NOW + 2 * H))
        x = plan.order([me, five(86)], LINES, NOW)[1]
        self.assertEqual((x['reason'], x['blocked_by']), ('not enough room', ['session']))

    def test_the_account_in_use_over_a_line_is_not_first(self):
        given = [acct('me', 96, 1, active=True), acct('other', 10, 2)]
        ranked = plan.order(given, lines('refills-first'), NOW)
        self.assertEqual([(a['name'], a['usable'], a['reason']) for a in ranked],
                         [('other', True, None), ('me', False, 'over its line')])

    def test_everything_over_its_lines(self):
        given = [acct('me', 96, 1, active=True), acct('a', 97, 2), acct('b', 99.5, 4)]
        nxt = plan.next_account(given, lines('most-room'), NOW)
        self.assertEqual((nxt['account'], nxt['reason'], nxt['back_at']), (None, 'no other account has room', NOW + 2 * D))

    def test_other_reasons(self):
        given = [acct('me', 10, 1, active=True), acct('out', 0, 1, needs_login=True), acct('old', 0, 1, trusted=False),
                 acct('none', None, 1), acct('gone', 0, 1, disabled=True), acct('full', 100, 1)]
        reasons = dict((a['name'], a['reason']) for a in plan.order(given, LINES, NOW))
        self.assertEqual(reasons, {'me': None, 'out': 'needs sign-in', 'old': 'no trusted reading',
                                   'none': 'no reading yet', 'gone': 'not kept here', 'full': 'at its limit'})

    def test_our_defaults(self):
        x = plan.order([acct('ninety-one', 91, 1)], None, NOW)[0]                 # weekly line 95, margin 5
        self.assertEqual((x['reason'], x['left_pct']), ('not enough room', 4))
        self.assertEqual(plan._settings(None)['strategy'], 'refills-first')
        self.assertEqual(plan._settings({'strategy': 'best'})['strategy'], 'refills-first')    # not one of ours


class Next(unittest.TestCase):
    def test_skips_the_account_in_use(self):
        week = five_accounts()
        week[0]['active'], week[3]['active'] = False, True                       # now on seventeen
        nxt = plan.next_account(week, lines('refills-first'), NOW)
        self.assertEqual((nxt['account']['name'], nxt['reason']), ('in-use', None))

    def test_no_other_account(self):
        nxt = plan.next_account([acct('only', 10, 1, active=True)], LINES, NOW)
        self.assertEqual((nxt['account'], nxt['reason']), (None, 'no other account'))

    def test_others_signed_out_have_no_return_time(self):
        nxt = plan.next_account([acct('me', 10, 1, active=True), acct('out', 0, 1, needs_login=True)], LINES, NOW)
        self.assertEqual((nxt['account'], nxt['reason'], nxt['back_at']), (None, 'no other account has room', None))


def sample_week():
    # Five made-up accounts (given out of order on purpose), for what refills unused
    return [acct('six-day', 0, 6.6), acct('ninety-two', 92, 3.5), acct('tomorrow', 0, 1, active=True),
            acct('seventeen', 17, 3.6), acct('two-day', 65, 2)]


class WeekAhead(unittest.TestCase):
    """"Out by" is when the weekly allowance reaches 100% at this week's own average pace (used so far
    over the time since the window opened, seven days before its reset), shown only after 12 hours and 10% used."""

    def test_runs_out_before_its_refill(self):
        # window opened 4 days ago (resets in 3): 70% in 96 h is 0.729 an hour; 30 more points take 41.1 h
        w = plan.week_ahead(70, NOW + 3 * D, NOW, NOW)
        self.assertFalse(w['lasts'])
        self.assertAlmostEqual(w['out_at'], NOW + 30 / (70 / 96.0) * H, delta=1)

    def test_lasts_to_its_refill(self):
        self.assertEqual(plan.week_ahead(48, NOW + 3 * D, NOW, NOW), {'lasts': True, 'out_at': None})   # 0.5 an hour

    def test_it_is_about_100_percent_not_the_switch_line(self):
        # 91% used after 5 days, resets in 2: 9 points at 0.758 an hour is 11.9 h, before the reset, never "now"
        w = plan.week_ahead(91, NOW + 2 * D, NOW, NOW)
        self.assertAlmostEqual(w['out_at'], NOW + 9 / (91 / 120.0) * H, delta=1)
        self.assertGreater(w['out_at'], NOW)

    def test_nothing_from_too_little(self):
        self.assertIsNone(plan.week_ahead(30, NOW + 7 * D - 6 * H, NOW, NOW))       # open 6 hours
        self.assertIsNone(plan.week_ahead(8, NOW + 6 * D, NOW, NOW))                # 8% used after a day
        # a burst: 0, 4, 8% in the first hour of the week
        self.assertIsNone(plan.week_ahead(8, NOW + 7 * D - H, NOW, NOW))

    def test_nothing_from_a_reading_that_cannot_say(self):
        self.assertIsNone(plan.week_ahead(100, NOW + D, NOW, NOW))                  # already out: the row says so
        self.assertIsNone(plan.week_ahead(50, NOW - 60, NOW, NOW))                  # its reset has gone by
        self.assertIsNone(plan.week_ahead(50, None, NOW, NOW))
        self.assertIsNone(plan.week_ahead(50, NOW + 3 * D, NOW + H, NOW))           # a reading from the future
        self.assertIsNone(plan.week_ahead(None, NOW + 3 * D, NOW, NOW))

    def test_it_uses_the_readings_own_time(self):
        # read 2 hours ago at 70% (window then open 94 h): the pace is 70/94, counted from the reading
        w = plan.week_ahead(70, NOW + 3 * D, NOW - 2 * H, NOW)
        self.assertAlmostEqual(w['out_at'], NOW - 2 * H + 30 / (70 / 94.0) * H, delta=1)


class Wasted(unittest.TestCase):
    def test_a_week_at_two_points_an_hour(self):
        week = sample_week()
        best = plan.wasted_if(week, ['tomorrow', 'two-day', 'seventeen', 'six-day'], NOW, 2.0, LINES)
        # tomorrow: 95 room, 24 h left at 2/h: uses 48, 47 refill unused. two-day 30 (15 h), seventeen
        # 78 (39 h), six-day 95 (47.5 h) all fit. ninety-two's 3 points are under the landing margin. 47 + 3.
        self.assertAlmostEqual(best['best'], 50.0)
        self.assertAlmostEqual(best['wasted'], 50.0)
        self.assertAlmostEqual(best['extra'], 0.0)
        self.assertEqual(best['best_order'], ['tomorrow', 'two-day', 'seventeen', 'six-day'])
        worst = plan.wasted_if(week, ['six-day', 'seventeen', 'ninety-two', 'two-day', 'tomorrow'], NOW, 2.0, LINES)
        # six-day 95 (to 47.5 h); seventeen resets at 86.4 h: 38.9 h x 2 = 77.8 of 78; two-day and
        # tomorrow have reset by then: 30 + 95 lost. 0.2 + 30 + 95 + 3 = 128.2
        self.assertAlmostEqual(worst['wasted'], 128.2)
        self.assertAlmostEqual(worst['extra'], 78.2)

    def test_refills_first_is_never_worse(self):
        import itertools
        week = sample_week()
        usable = ['tomorrow', 'two-day', 'seventeen', 'six-day']
        for rate in (0.5, 1, 2, 5, 40):
            best = plan.wasted_if(week, usable, NOW, rate, LINES)['wasted']
            for perm in itertools.permutations(usable):
                self.assertGreaterEqual(plan.wasted_if(week, list(perm), NOW, rate, LINES)['wasted'] + 1e-9, best)

    def test_order_entries_work_as_names(self):
        week = sample_week()
        ranked = plan.order(week, lines('refills-first'), NOW)
        self.assertAlmostEqual(plan.wasted_if(week, ranked, NOW, 2.0, LINES)['extra'], 0.0)

    def test_no_rate_no_answer(self):
        for rate in (None, 0, -1, True):
            self.assertIsNone(plan.wasted_if(sample_week(), [], NOW, rate, LINES))

    def test_left_out_accounts_refill_unused(self):
        r = plan.wasted_if(sample_week(), ['tomorrow'], NOW, 2.0, LINES)
        self.assertAlmostEqual(r['wasted'], 301 - 48)                    # all room 95+30+3+78+95, less 48 used


if __name__ == '__main__':
    unittest.main()
