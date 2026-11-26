import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# When a sign-in ends, in words (term.signin_words): Anthropic ends each sign-in on a set date, a renewal keeps it and
# only a new sign-in moves it. Every surface says it the same way from a week ahead, in this Mac's own time zone:
#
#   Sign-in ends by today 8:38 PM · Sign-in ends by tomorrow 1:15 AM · Sign-in ends by Sat 1:15 AM
#   Sign-in ended by yesterday 8:38 PM · Sign-in ended by Fri 8:38 PM · Sign-in ended by Nov 11 (over 6 days back)
#
# and, short, in the menu bar: "Sign-in ends by Sat", "ended by Fri". Nothing for a date over 7 days away or not known, and
# an account that needs a sign-in for another reason (refused before its date) keeps the words it had. The account
# Claude Code is on has the same words as any other: it signs in again from the page too
# (test_accounts_signin_again_in_use), so no "/login" line.
#
# The page says the same words (test_page_signin_ends holds the two together). Made-up times in a fixed time zone.
import json
import math
import os
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import term
import test_term  # its made-up state of the page, as the terminal reads it

TZ = timezone(timedelta(hours=-8))
D = 86400


def t(day, hour, minute=0, month=11, year=2026):
    return datetime(year, month, day, hour, minute, tzinfo=TZ).timestamp()


NOW = t(18, 10)                         # Wed Nov 18 2026, 10:00 AM


def words(at, needs_login=False, short=False, now=NOW):
    return term.signin_words(at, now, needs_login, tz=TZ, short=short)


class BeforeTheDate(unittest.TestCase):
    def test_today_tomorrow_and_a_weekday(self):
        self.assertEqual(words(t(18, 20, 38)), 'Sign-in ends by today 8:38 PM')
        self.assertEqual(words(t(19, 1, 15)), 'Sign-in ends by tomorrow 1:15 AM')
        self.assertEqual(words(t(21, 1, 15)), 'Sign-in ends by Sat 1:15 AM')
        self.assertEqual(words(t(24, 23, 59)), 'Sign-in ends by Tue 11:59 PM')

    def test_midnight_and_noon(self):
        self.assertEqual(words(t(19, 0)), 'Sign-in ends by tomorrow 12:00 AM')         # tonight's midnight is tomorrow
        self.assertEqual(words(t(18, 12)), 'Sign-in ends by today 12:00 PM')
        self.assertEqual(words(t(20, 0)), 'Sign-in ends by Fri 12:00 AM')
        self.assertEqual(words(t(22, 12)), 'Sign-in ends by Sun 12:00 PM')

    def test_to_the_nearest_minute_and_its_day(self):
        self.assertEqual(words(t(18, 23, 59) + 45), 'Sign-in ends by tomorrow 12:00 AM')
        self.assertEqual(words(t(18, 20, 38) + 20), 'Sign-in ends by today 8:38 PM')

    def test_a_week_ahead_names_its_date_not_todays_weekday(self):
        # under 7 days, but on the same weekday as today: "Wed" would read as today
        self.assertEqual(words(t(25, 9)), 'Sign-in ends by Nov 25 9:00 AM')
        self.assertEqual(words(NOW + 7 * D), 'Sign-in ends by Nov 25 10:00 AM')

    def test_over_7_days_away_or_not_known_says_nothing(self):
        # not a sane time (a corrupt saved date: negative, zero, far past any year) is not known either, and never
        # raises, with or without a sign-in needed
        for at in (NOW + 7 * D + 60, t(30, 9), None, 'soon', True, float('nan'), float('inf'), -float('inf'), 1e300,
                   -9e10, -1, 0, 1e11, 5e11):
            with self.subTest(at=at):
                self.assertEqual(words(at), '')
                self.assertEqual(words(at, needs_login=True), '')

    def test_refused_before_its_date_keeps_the_words_it_had(self):
        self.assertEqual(words(t(19, 1, 15), needs_login=True), '')

    def test_across_a_new_year(self):
        now = t(31, 22, month=12)
        self.assertEqual(words(t(1, 1, month=1, year=2027), now=now), 'Sign-in ends by tomorrow 1:00 AM')
        self.assertEqual(words(t(3, 1, month=1, year=2027), now=now), 'Sign-in ends by Sun 1:00 AM')


class AfterTheDate(unittest.TestCase):
    def test_today_yesterday_a_weekday_then_the_date(self):
        self.assertEqual(words(t(18, 3, 5), needs_login=True), 'Sign-in ended by today 3:05 AM')
        self.assertEqual(words(t(17, 20, 38), needs_login=True), 'Sign-in ended by yesterday 8:38 PM')
        self.assertEqual(words(t(13, 20, 38), needs_login=True), 'Sign-in ended by Fri 8:38 PM')
        self.assertEqual(words(t(12, 20, 38), needs_login=True), 'Sign-in ended by Thu 8:38 PM')      # 6 days back
        self.assertEqual(words(t(11, 20, 38), needs_login=True), 'Sign-in ended by Nov 11')           # over 6 days back
        self.assertEqual(words(t(1, 9), needs_login=True), 'Sign-in ended by Nov 1')
        self.assertEqual(words(t(30, 23, month=10), needs_login=True), 'Sign-in ended by Oct 30')

    def test_seconds_before_midnight_it_ended_today_never_tomorrow(self):
        now = t(18, 23, 59) + 50                     # 11:59:50 PM; it ended ten seconds ago, at a minute that rounds up
        self.assertEqual(words(now - 10, needs_login=True, now=now), 'Sign-in ended by today 11:59 PM')
        self.assertEqual(words(now - 100, needs_login=True, now=now), 'Sign-in ended by today 11:58 PM')
        self.assertEqual(words(now + 5, now=now), 'Sign-in ends by tomorrow 12:00 AM')     # still to come: midnight

    def test_only_for_an_account_that_needs_a_sign_in(self):
        # the engine calls a login past its date needs_login at its next look: until then nothing is said
        self.assertEqual(words(t(18, 9, 59)), '')


class Short(unittest.TestCase):
    def test_the_menu_bars_words(self):
        self.assertEqual(words(t(21, 1, 15), short=True), 'Sign-in ends by Sat')
        self.assertEqual(words(t(18, 20, 38), short=True), 'Sign-in ends by today')
        self.assertEqual(words(t(19, 1, 15), short=True), 'Sign-in ends by tomorrow')
        self.assertEqual(words(t(25, 9), short=True), 'Sign-in ends by Nov 25')
        self.assertEqual(words(t(13, 20, 38), needs_login=True, short=True), 'ended by Fri')
        self.assertEqual(words(t(17, 20, 38), needs_login=True, short=True), 'ended by yesterday')
        self.assertEqual(words(t(11, 20, 38), needs_login=True, short=True), 'ended by Nov 11')
        self.assertEqual(words(t(30, 9), short=True), '')


class AnAccountsLine(unittest.TestCase):
    def test_the_account_in_use_has_the_same_words_as_any_other(self):
        a = {'email': 'ana@mail.example', 'signin_ends_at': t(19, 2, 6), 'active': True}
        self.assertEqual(term.signin_line(a, NOW, TZ), 'Sign-in ends by tomorrow 2:06 AM')
        self.assertEqual(term.signin_line(dict(a, active=False), NOW, TZ), 'Sign-in ends by tomorrow 2:06 AM')

    def test_how_many_in_the_words_of_the_pages_note(self):
        self.assertEqual(term.signin_count(0, 2), '2 sign-ins end this week.')
        self.assertEqual(term.signin_count(0, 1), '1 sign-in ends this week.')
        self.assertEqual(term.signin_count(1, 2), '1 sign-in has ended and 2 end this week.')
        self.assertEqual(term.signin_count(2, 1), '2 sign-ins have ended and 1 ends this week.')
        self.assertEqual(term.signin_count(3, 0), '3 sign-ins have ended.')
        with open(os.path.join(HERE, '..', 'switchboard', 'web', 'index.html'), encoding='utf-8') as f:
            page = f.read()
        body = page[page.index('function signinCount(ended, soon) {'):]
        body = body[:body.index('\n}\n')]
        for words in ("' sign-ins have ended'", "'1 sign-in has ended'", "' this week.'", "'1 sign-in ends'",
                      "' sign-ins end'"):
            self.assertIn(words, body)                                     # the page builds them from the same parts
        self.assertEqual(term.SIGNIN_ALL, 'Sign in to all')
        self.assertIn("const SIGNIN_ALL = 'Sign in to all';", page)

    def test_once_ended_the_page_signs_it_in_again_in_use_or_not(self):
        a = {'signin_ends_at': t(17, 20, 38), 'active': True, 'needs_login': True}
        self.assertEqual(term.signin_line(a, NOW, TZ), 'Sign-in ended by yesterday 8:38 PM')
        self.assertEqual(term.signin_line(dict(a, active=False), NOW, TZ), 'Sign-in ended by yesterday 8:38 PM')

    def test_nothing_without_a_date_or_far_from_it(self):
        self.assertEqual(term.signin_line({'active': True}, NOW, TZ), '')
        self.assertEqual(term.signin_line({'signin_ends_at': NOW + 20 * D, 'active': True}, NOW, TZ), '')

    def test_the_comments_say_the_shape_with_no_example_dates(self):
        # product files carry no dates, examples included: the sign-in words' docstrings and the page's comments
        dated = re.compile(r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d|\d:\d\d [AP]M')
        for f in (term.signin_day, term.signin_words, term.signin_line, term.status):
            with self.subTest(function=f.__name__):
                self.assertIsNone(dated.search(f.__doc__), f.__doc__)
        with open(os.path.join(HERE, '..', 'switchboard', 'web', 'index.html'), encoding='utf-8') as f:
            page = f.read()
        block = page[page.index('// When a sign-in ends, in the words'):page.index('function signinOf(a)')]
        comments = '\n'.join(re.findall(r'//[^\n]*', block))
        self.assertIsNone(dated.search(comments), comments)

    def test_the_why_is_one_sentence_with_no_number_of_days(self):
        self.assertEqual(term.SIGNIN_WHY, 'Anthropic ends each sign-in by a set date. Renewing does not move it; '
                                          'signing in again does.')
        self.assertFalse(any(c.isdigit() for c in term.SIGNIN_WHY))
        self.assertTrue(math.isclose(term.SIGNIN_SOON, 7 * D) and math.isclose(term.SIGNIN_NEAR, 3 * D))

    def test_the_date_is_the_latest_it_ends_so_the_words_say_by(self):
        # Claude Code (2.1.329, 2.1.330): "Sign-in ends: between <its date less 3 days> and <its date>"
        self.assertEqual(term.SIGNIN_WINDOW, 3 * D)
        self.assertEqual(term.signin_earliest(t(21, 1, 15)), t(18, 1, 15))
        self.assertIsNone(term.signin_earliest(None))
        self.assertIsNone(term.signin_earliest(-1))
        for at, need in ((t(21, 1, 15), False), (t(17, 20, 38), True)):
            with self.subTest(at=at):
                self.assertIn(' by ', words(at, needs_login=need))
                self.assertIn(' by ', words(at, needs_login=need, short=True))


# ---------- the list `switchboard` prints ----------

class TheList(unittest.TestCase):
    """test_term's five made-up accounts (Fri Nov 13 2026, 3:31 PM): the words after each row's age."""

    def rows(self, **ends):
        view = json.loads(json.dumps(test_term.VIEW))
        for a in view['accounts']:
            if a['email'] in ends:
                a['signin_ends_at'], a['needs_login'] = ends[a['email']]
        return [x for x in test_term.lines(view) if x[:1].isdigit()]

    def at(self, day, hour, minute=0):
        return datetime(2026, 11, day, hour, minute, tzinfo=test_term.TZ).timestamp()

    def test_the_words_after_the_age(self):
        got = self.rows(**{'ana@lab.example': (self.at(16, 9), False), 'ana@harbor.example': (self.at(9, 20, 38), True),
                           'ana@mail.example': (self.at(15, 2, 6), False),
                           'ana@northwind.example': (self.at(30, 9), False)})          # far off: nothing
        self.assertEqual(got, [
            '1  ana@northwind.example  Max 20x · Personal  checked 2 min ago',
            '2  ana@fernhill.example   Max 20x · Personal  checked 10 min ago',
            '3  ana@harbor.example     Team · Harbor       sign in again · checked 5 min ago · Sign-in ended by Mon 8:38 PM',
            '4  ana@lab.example        Max 20x · Personal  next · checked 5 min ago · Sign-in ends by Mon 9:00 AM',
            '5  ana@mail.example       Max 20x · Personal  in use · checked 3 min ago · Sign-in ends by Sun 2:06 AM'])

    def test_with_no_dates_the_list_is_as_it_was(self):
        self.assertEqual('\n'.join(test_term.lines()), test_term.PLAIN)
        self.assertEqual(self.rows(**{'ana@harbor.example': (self.at(16, 9), True)})[2],     # refused before its date
                         '3  ana@harbor.example     Team · Harbor       sign in again · checked 5 min ago')

    def test_the_account_in_use_once_ended_is_said_so(self):
        self.assertEqual(self.rows(**{'ana@mail.example': (self.at(13, 9), True)})[4],
                         '5  ana@mail.example       Max 20x · Personal  in use · checked 3 min ago · Sign-in ended '
                         'by today 9:00 AM')

    def test_a_date_that_is_not_a_sane_time_says_nothing_and_breaks_nothing(self):
        for at in (-9e10, 0, 5e11):
            with self.subTest(at=at):
                rows = self.rows(**{'ana@harbor.example': (at, True), 'ana@lab.example': (at, False)})
                self.assertEqual(rows[2], '3  ana@harbor.example     Team · Harbor       sign in again · checked 5 min ago')
                self.assertEqual(rows[3], '4  ana@lab.example        Max 20x · Personal  next · checked 5 min ago')

    def test_a_narrow_terminal_puts_them_on_the_status_line(self):
        view = json.loads(json.dumps(test_term.VIEW))
        view['accounts'][3]['signin_ends_at'] = self.at(16, 9)                     # ana@lab.example
        got = test_term.lines(view, width=60)
        i = got.index('4  ana@lab.example        Max 20x · Personal')
        self.assertEqual(got[i + 1].strip(), 'next · checked 5 min ago · Sign-in ends by Mon 9:00 AM')


if __name__ == '__main__':
    unittest.main()
