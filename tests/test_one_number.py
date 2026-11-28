import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# One number for another provider's row, on every face: the list `switchboard` prints, the menu bar's menu, the one
# notification ("Type switchboard and press 5.") and the line a Continue press gives to run in a terminal all number
# it by one rule (term.provider_numbers): after the highest number of the Claude accounts the page's state lists, one
# each in the rows' order. An account the state no longer lists (not connected) keeps its slot in the stored state and
# takes no number; an entry of the rows that is not a row takes none either. `switchboard <that number>` reaches it.
#
# Made-up accounts and numbers only; nothing starts and no notification is shown.
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import __main__ as cli  # noqa: E402
from switchboard import comeup, core, menubar, server, term  # noqa: E402
from test_comeup import FIVE_BACK, NOW, WEEK_BACK, board, codex, w  # noqa: E402
from test_rows_same_email import row  # noqa: E402

EMAILS = {1: 'ana@harbor.example', 2: 'ana@ivy.example', 4: 'ana@lab.example', 6: 'ana@gone.example'}
AT_LINES = {'session': w(93, FIVE_BACK), 'weekly': w(96, WEEK_BACK), 'scoped': []}


def stored():
    """The page's stored state: Claude accounts at slots 1, 2 and 4, each at the person's lines, and one at slot 6
    that the engine no longer lists (it keeps its slot and is not connected)."""
    st = core.blank_state()
    rows = [row(n, e, 'Org %d' % n, 'o-%d' % n, usage=AT_LINES, usage_at=NOW - 60) for n, e in sorted(EMAILS.items())]
    server.apply_rows(st, rows)
    server.apply_rows(st, rows[:3])
    return st


def facts():
    return {'profiles': {}, 'browser': {'name': 'Google Chrome', 'present': False, 'tabs_blocked': False},
            'engine': {'installed': True, 'claude': True}, 'links': {'installed': False, 'on': False},
            'auto': {'session_at': 90, 'weekly_at': 95}, 'setup_hidden': True, 'now': NOW, 'demo': False}


class OneNumber(unittest.TestCase):
    def view(self, st, provider_rows):
        """The page's state for st (server.render), with automatic switching on and these provider rows."""
        v = server.render(st, facts())
        return dict(v, now=NOW, auto={'enabled': True, 'session_at': 90.0, 'weekly_at': 95.0, 'model_at': 90.0},
                    provider_rows=provider_rows)

    def numbers(self, st, s, at):
        """(the list's, the menu's, the notification's, the press's) number of the row at place `at`."""
        listed = [a['slot'] for a in term.read(s)['accounts'] if a.get('provider')]
        menu = [r['slot'] for r in menubar.provider_rows(s)]
        said = comeup.note(s, board())
        pressed = server.provider_number('codex', s['provider_rows'], st=st)
        return listed, menu, said and said[1].rsplit(' ', 1)[-1], comeup.row_number(s, s['provider_rows'][at]), pressed

    def test_slots_1_2_and_4_and_one_no_longer_connected(self):
        st = stored()
        self.assertEqual(sorted(a['slot'] for a in st['accounts'].values()), [1, 2, 4, 6])
        s = self.view(st, [codex(next=False)])
        self.assertEqual([a['slot'] for a in s['accounts']], [1, 2, 4])          # slot 6 is not listed
        self.assertEqual(self.numbers(st, s, 0), ([5], [5], '5.', 5, 5))
        self.assertIn('5  ana@northwind.example', '\n'.join(term.view(term.read(s))))
        moved = []
        with mock.patch.object(cli, 'current_view', lambda fresh=False: s), \
                mock.patch.object(cli, 'move_to', lambda pid, why=False: moved.append(pid) or 0):
            self.assertEqual(cli.go_to('5'), 0)
        self.assertEqual(moved, ['codex'])

    def test_an_entry_that_is_not_a_row_takes_no_number(self):
        st = stored()
        for damaged in ({}, {'provider': ''}, {'provider': None, 'state': 'room'}):
            with self.subTest(damaged=damaged):
                s = self.view(st, [damaged, codex(next=False)])
                self.assertEqual(self.numbers(st, s, 1), ([5], [5], '5.', 5, 5))

    def test_the_rule_itself(self):
        accounts = [{'slot': 1}, {'slot': 4}, {'slot': None}, {'slot': True}, 'x']
        self.assertEqual(term.provider_numbers(accounts, [codex(), None, {'provider': ''}, codex(email='b')]),
                         [5, None, None, 6])
        self.assertEqual(term.provider_numbers([], [codex()]), [1])
        self.assertEqual(term.provider_numbers(None, 'damaged'), [])


if __name__ == '__main__':
    unittest.main()
