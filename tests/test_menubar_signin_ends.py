import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The menu bar says when a sign-in ends, in the short words of term.signin_words: a row whose sign-in ends within the
# week gets the mark "Sign-in ends by Mon" and, under it in the menu, "Sign in again as …" (the row itself still uses the
# account); a row whose sign-in ended keeps its "Sign in again" and gains "ended by Mon". The account in use gets its
# "Sign in again as …" like any other (no "/login" line), and "Sign in to all" comes once under the rows, the page's
# own queue (POST /api/signins); while that runs, where it is. VoiceOver reads the whole words.
#
# Made-up accounts in test_menubar's made-up state (Fri Nov 13 2026, 3:31 PM); nothing here draws or opens a menu.
import os
import sys
import unittest
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import menubar, term
import test_menubar as made_up          # its made-up state of the page, as the menu bar reads it
from test_menubar import TZ, find, row_of, view

LAB, HARBOR, MAIL = 'ana@lab.example', 'ana@harbor.example', 'ana@mail.example'


def t(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=TZ).timestamp()


def state(**ends):
    """test_menubar's five accounts with these sign-in dates: email -> (time, needs_login)."""
    s = view()
    for email, (at, need) in ends.items():
        find(s, email).update(signin_ends_at=at, needs_login=need)
    return s


def after(items, email):
    """The menu item right after an account's row."""
    i = next(i for i, x in enumerate(items) if x[0] == 'row' and x[1]['email'] == email)
    return items[i + 1]


class TheRows(unittest.TestCase):
    def test_a_sign_in_that_ends_within_the_week_gets_its_mark_and_keeps_use(self):
        s = state(**{LAB: (t(16, 9), False)})
        r = row_of(s, LAB)
        self.assertEqual(r['marks'], [('Sign-in ends by Mon', 'next'), ('Next', 'next')])
        self.assertEqual(r['action'], 'use')
        self.assertEqual(r['signin'], 'Sign-in ends by Mon 9:00 AM')
        self.assertEqual(after(menubar.menu(s, tz=TZ), LAB), ('do', 'Sign in again as ' + LAB, 'signin'))

    def test_one_that_ended_keeps_sign_in_again_and_says_when(self):
        s = state(**{HARBOR: (t(9, 20, 38), True)})
        r = row_of(s, HARBOR)
        self.assertEqual(r['marks'], [('ended by Mon', 'next'), ('Sign in again', 'use')])
        self.assertEqual(r['action'], 'signin')
        self.assertEqual(after(menubar.menu(s, tz=TZ), HARBOR)[0], 'row')      # its own click signs in again

    def test_the_account_in_use_signs_in_again_like_any_other(self):
        s = state(**{MAIL: (t(15, 2, 6), False)})
        r = row_of(s, MAIL)
        self.assertEqual(r['marks'], [('Sign-in ends by Sun', 'next'), ('In use', 'in-use')])
        self.assertIsNone(r['action'])
        items = menubar.menu(s, tz=TZ)
        self.assertEqual(after(items, MAIL), ('do', 'Sign in again as ' + MAIL, 'signin'))
        self.assertFalse([x for x in items if x[0] == 'line' and '/login' in x[1]])

    def test_sign_in_to_all_once_under_the_rows_and_where_it_is_while_it_runs(self):
        s = state(**{MAIL: (t(15, 2, 6), False), LAB: (t(16, 9), False)})
        items = menubar.menu(s, tz=TZ)
        self.assertEqual([x for x in items if x[0] == 'do' and x[2] == 'signinall'],
                         [('do', term.SIGNIN_ALL, 'signinall')])
        last = max(i for i, x in enumerate(items) if x[0] == 'row')
        self.assertLess(last, items.index(('do', term.SIGNIN_ALL, 'signinall')))
        s['signins'] = {'state': 'running', 'current': LAB, 'done': 1, 'of': 2}
        items = menubar.menu(s, tz=TZ)
        self.assertNotIn(('do', term.SIGNIN_ALL, 'signinall'), items)
        self.assertIn(('line', 'Signing in to %s: approve it in the tab that opened.' % LAB), items)
        self.assertEqual([x for x in menubar.menu(view(), tz=TZ) if x[0] == 'do' and x[2] == 'signinall'], [])

    def test_sign_in_to_all_follows_the_pages_own_lists(self):
        # One source with the note above the page's accounts (signins.due, in the state's `signins`): Sign in to all
        # is offered while that note shows (a sign-in that ended, or one that ends this week), not by a rule of the
        # menu bar's own: lists that count nothing offer nothing, whatever the rows. A row's own Sign in again stays.
        s = state(**{LAB: (t(16, 9), False), HARBOR: (t(20, 9), True)})
        s['signins'] = {'state': 'idle', 'due': [HARBOR, LAB], 'ended': [], 'week': [LAB]}
        items = menubar.menu(s, tz=TZ)
        self.assertEqual([x for x in items if x[0] == 'do' and x[2] == 'signinall'], [('do', term.SIGNIN_ALL, 'signinall')])
        s['signins'] = {'state': 'idle', 'due': [HARBOR], 'ended': [], 'week': []}
        s = dict(s, accounts=s['accounts'])
        find(s, LAB).update(signin_ends_at=t(25, 9))
        items = menubar.menu(s, tz=TZ)
        self.assertEqual([x for x in items if x[0] == 'do' and x[2] == 'signinall'], [])
        self.assertEqual(row_of(s, HARBOR)['action'], 'signin')                  # its own Sign in again stays

    def test_the_account_in_use_once_ended_signs_in_again(self):
        s = state(**{MAIL: (t(13, 9), True)})
        r = row_of(s, MAIL)
        self.assertEqual(r['marks'], [('ended by today', 'next'), ('In use', 'in-use')])
        self.assertEqual(after(menubar.menu(s, tz=TZ), MAIL), ('do', 'Sign in again as ' + MAIL, 'signin'))

    def test_nothing_for_a_date_far_off_not_known_or_refused_before_it(self):
        s = state(**{LAB: (t(25, 9), False), HARBOR: (t(14, 9), True)})
        items = menubar.menu(s, tz=TZ)
        for r in menubar.rows(s, TZ):
            with self.subTest(account=r['email']):
                self.assertNotIn('Sign-in', ' '.join(m for m, _ in r['marks']))
                self.assertNotIn('ended', ' '.join(m for m, _ in r['marks']))
                self.assertEqual(r['signin'], '')
        self.assertEqual([x for x in items if x[0] == 'do' and x[2] == 'signin'], [])
        same = view()                                                       # the menu as it was without the dates
        find(same, HARBOR)['needs_login'] = True
        self.assertEqual(menubar.shape(items), menubar.shape(menubar.menu(same, tz=TZ)))
        self.assertEqual([r['marks'] for r in menubar.rows(s, TZ)], [r['marks'] for r in menubar.rows(same, TZ)])

    def test_a_date_that_is_not_a_sane_time_says_nothing_and_breaks_nothing(self):
        for at in (-9e10, 0, 5e11):
            with self.subTest(at=at):
                s = state(**{HARBOR: (at, True), LAB: (at, False), MAIL: (at, False)})
                self.assertEqual([r['signin'] for r in menubar.rows(s, TZ)], [''] * 5)
                self.assertEqual([x for x in menubar.menu(s, tz=TZ) if x[0] == 'do' and x[2] == 'signin'], [])

    def test_voiceover_reads_the_whole_words(self):
        s = state(**{LAB: (t(16, 9), False), HARBOR: (t(9, 20, 38), True)})
        self.assertEqual(menubar.row_label(row_of(s, LAB)),
                         'Use ana@lab.example, next, Sign-in ends by Mon 9:00 AM, weekly 65%')
        self.assertEqual(menubar.row_label(row_of(s, HARBOR)),
                         'Sign in again as ana@harbor.example, Sign-in ended by Mon 8:38 PM, 5-hour 0%, '
                         'weekly 17%')

    def test_a_new_item_rebuilds_the_menu(self):
        before = menubar.menu(view(), tz=TZ)
        soon = menubar.menu(state(**{LAB: (t(16, 9), False)}), tz=TZ)
        self.assertNotEqual(menubar.shape(before), menubar.shape(soon))
        self.assertEqual(made_up.NOW, t(13, 15, 31) + 25)                   # the made-up state's own clock


if __name__ == '__main__':
    unittest.main()
