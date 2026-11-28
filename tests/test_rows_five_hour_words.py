import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The 5-hour line is in every row, on every face. A plan with no 5-hour limit says so in words in the 5-hour place,
# above Weekly, with no bar and no percent: "no 5-hour limit" in the terminal list, "No 5-hour limit" in the menu bar's
# rows, "No 5-hour limit on this plan" on the page. A number not known yet (a reading never made, a row with no window,
# a 5-hour window sent with no number) keeps what each face shows for it today. One rule (term.no_five) for a Claude
# account's row and another assistant's row alike.
#
# Made-up *.example accounts only. No network, no engine, no page, no Chrome.
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import menubar, term  # noqa: E402
from test_rows_two_limits import FULL, NEVER, NOW, OLD, TEAM, TZ, ULTRA, at, view, w  # noqa: E402

CODEX = 'kai@northwind.example'


def codex(windows):
    """One row of the state's 'provider_rows' (the join's row shape)."""
    return {'provider': 'codex', 'name': 'Codex', 'vendor': 'OpenAI', 'email': CODEX, 'plan': 'Plus',
            'windows': windows, 'read_at': NOW - 40, 'state': 'room' if windows else 'unknown', 'back_at': None,
            'fresh': True, 'why': None, 'can': True, 'app': True, 'in_use': False, 'next': False, 'chat': None}


def win(label, pct, resets_at=NOW + 5 * 86400):
    return {'label': label, 'used_pct': pct, 'resets_at': resets_at, 'line': 95.0}


WEEK_ONLY = [win('weekly', 21.0)]


class TheOneRule(unittest.TestCase):
    def test_an_account(self):
        def words(usage, face='term'):
            return term.no_five(face, account={'usage': usage})
        self.assertEqual(words({'session': None, 'weekly': w(27)}), 'no 5-hour limit')     # its plan has none
        self.assertEqual(words({'weekly': w(27)}), 'no 5-hour limit')
        self.assertEqual(words({'session': w(40), 'weekly': w(27)}), '')                   # a number
        self.assertEqual(words({'session': w(None), 'weekly': w(27)}), '')                 # sent with no number
        self.assertEqual(words(None), '')                                                  # never read
        self.assertEqual(words({}), '')                                                    # nothing in it
        self.assertEqual(words({'session': None}, 'menu'), 'No 5-hour limit')
        self.assertEqual(words({'session': None}, 'page'), 'No 5-hour limit on this plan')

    def test_another_assistants_row(self):
        def words(windows):
            return term.no_five('term', row=codex(windows))
        self.assertEqual(words(WEEK_ONLY), 'no 5-hour limit')                               # no 5-hour window
        self.assertEqual(words([win('5-hour', 8.0)] + WEEK_ONLY), '')
        self.assertEqual(words([win('5-hour', None)] + WEEK_ONLY), '')                      # not known
        self.assertEqual(words([]), '')                                                     # no window yet


class TheTerminal(unittest.TestCase):
    def blocks(self, v):
        got = term.view(term.read(v, tz=TZ))
        return dict((b.split('\n')[0].split()[1], b.split('\n')[1:]) for b in '\n'.join(got).split('\n\n')[1:])

    def test_a_plan_with_no_5_hour_limit_says_so_above_weekly(self):
        b = self.blocks(view())
        for email in (ULTRA, FULL):
            self.assertEqual(b[email][0], '   no 5-hour limit', email)
            self.assertTrue(b[email][1].startswith('   weekly  '), email)
        self.assertEqual(b[TEAM][0][:11], '   5-hour  ')                                        # a number: its bar

    def test_a_number_not_known_keeps_what_it_shows(self):
        b = self.blocks(view())
        for email in (OLD, NEVER):
            self.assertEqual(len(b[email]), 1, email)
            self.assertNotIn('5-hour', '\n'.join(b[email]), email)
        v = view()
        v['accounts'][1]['usage']['session'] = w(None, at(13, 18))                             # sent with no number
        self.assertEqual(len(self.blocks(v)[TEAM]), 1)

    def test_the_words_are_quiet_never_a_bar_or_a_percent(self):
        got = '\n'.join(term.view(term.read(view(), tz=TZ), colour=True))
        self.assertIn('   \x1b[2mno 5-hour limit\x1b[0m', got)
        line = [x for x in term.read(view(), tz=TZ)['accounts'] if x['email'] == ULTRA][0]['limits'][0]
        self.assertEqual((line['label'], line['pct'], line['over'], line.get('none')), ('5-hour', None, False, True))

    def test_another_assistants_row_has_the_same_rule(self):
        b = self.blocks(view(provider_rows=[codex(WEEK_ONLY)]))
        self.assertEqual(b[CODEX][0], '   no 5-hour limit')
        self.assertTrue(b[CODEX][1].startswith('   weekly  '))
        b = self.blocks(view(provider_rows=[codex([])]))                                     # no window yet
        self.assertEqual(b[CODEX], [])
        self.assertIn('numbers not known', '\n'.join(term.view(term.read(view(provider_rows=[codex([])]), tz=TZ))))

    def test_a_narrow_terminal_still_fits(self):
        for width in (80, 60):
            got = term.view(term.read(view(provider_rows=[codex(WEEK_ONLY)]), tz=TZ), width=width)
            self.assertLessEqual(max(len(x) for x in got), width)
            self.assertIn('   no 5-hour limit', got)
        # the words never take room from the bars: the same bar width as with no such line
        bars = [len(re.search('[━─]+', x).group()) for x in got if re.search('[━─]', x)]
        self.assertEqual(set(bars), {8})


class TheMenuBar(unittest.TestCase):
    def rows(self, v):
        return dict((r['email'], r) for r in menubar.rows(v, TZ))

    def texts(self, row):
        return [(op[2], op[5][0][0]) for op in menubar.row_ops(row) if op[0] == 'text']

    def test_a_plan_with_no_5_hour_limit_says_so_in_the_5_hour_bar_row(self):
        r = self.rows(view())[ULTRA]
        self.assertIsNone(r['five'])                                              # no bar, no percent
        self.assertEqual(r['no_five'], 'No 5-hour limit')
        self.assertEqual(self.texts(r)[:2], [(28.0, 'No 5-hour limit'), (46.0, 'Weekly')])   # above Weekly
        self.assertEqual(menubar.row_height(r), menubar.ROW_H)                    # the same height as a 5-hour row
        self.assertIn('no 5-hour limit, weekly 27%', menubar.row_label(r))
        words = [op for op in menubar.row_ops(r) if op[0] == 'text' and op[5][0][0] == 'No 5-hour limit'][0]
        self.assertEqual(words[5][0][3], 'ink3')                                  # grey, quiet
        self.assertLessEqual(words[1] + words[3], menubar.ROW_W - menubar.PAD)    # within the row's width

    def test_a_number_not_known_keeps_what_it_shows(self):
        rows = self.rows(view())
        for email in (OLD, NEVER):
            self.assertEqual(rows[email]['no_five'], '', email)
            self.assertEqual([t for _, t in self.texts(rows[email])][0], 'Weekly', email)
        self.assertEqual(menubar.row_height(rows[OLD]), 54.0)

    def test_another_assistants_row_has_the_same_rule(self):
        r = self.rows(view(provider_rows=[codex(WEEK_ONLY)]))[CODEX]
        self.assertEqual((r['five'], r['no_five']), (None, 'No 5-hour limit'))
        self.assertEqual(self.texts(r)[:2], [(28.0, 'No 5-hour limit'), (46.0, 'Weekly')])
        r = self.rows(view(provider_rows=[codex([])]))[CODEX]                     # no window yet: as today
        self.assertEqual((r['no_five'], r['foot']), ('', 'Numbers not known'))


class ThePage(unittest.TestCase):
    """The page says the same words (read from its file: no browser here)."""

    def setUp(self):
        with open(os.path.join(HERE, '..', 'switchboard', 'web', 'index.html'), encoding='utf-8') as f:
            self.page = f.read()

    def test_the_same_words_as_the_one_rule(self):
        self.assertIn("const NO_FIVE = '%s';" % term.NO_FIVE['page'], self.page)

    def test_drawn_in_the_5_hour_place_not_hidden(self):
        self.assertIn("l.none === NO_FIVE) return '<div class=\"c c-nofive\" role=\"cell\">' + esc(NO_FIVE)", self.page)
        self.assertIn('.c-nofive { grid-area: five;', self.page)                          # in the 5-hour place
        self.assertEqual(self.page.count(':not(:has(> .c-five, > .c-nofive))'), 3)       # the row keeps that place
        self.assertIn("raw && Object.keys(raw).length && !raw.session && !a.five_seen ? NO_FIVE : 'Not known'",
                      self.page)                                                    # an account
        self.assertIn("useCell({none: five ? 'Not known' : NO_FIVE})", self.page)          # another assistant's row


    def test_a_row_with_a_spend_line_keeps_its_spend_place(self):
        # The rules for a row with no 5-hour line must not out-rank the later spend-row rules: each :not(:has(...))
        # adds a class to a selector's weight, so two of them in one selector would beat `.arow:has(> .c-spend)`.
        for line in self.page.splitlines():
            if '{' in line and ':has(' in line:
                self.assertLessEqual(line.split('{')[0].count(':not(:has('), 1, line)

if __name__ == '__main__':
    unittest.main()
