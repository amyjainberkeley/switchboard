"""`switchboard move NAME N` and `switchboard swap NAME NAME`: an account to a place in the list, or two accounts
changing places, from the terminal. Through the running page when it answers (POST /api/move, the call its row menu
makes), else here; either way the list as it is now is printed, numbers and whole emails. Made-up accounts only: the
engine is a stand-in (tests/fakes_accounts_api.py) and so is the running page (test_term.Page)."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import os
import random
import unittest

from switchboard import core, more
from test_term import Page, Sandbox

A, B, C = 'ana@northwind.example', 'ana@fernhill.example', 'ana@harbor.example'


def listed(*emails):
    return ''.join('  %d  %s\n' % (n, e) for n, e in enumerate(emails, 1))


class Three(Sandbox):
    def setUp(self):
        super().setUp()
        self.ids = dict((e, self.fake.hold(e, 'org-' + e.split('@')[1].split('.')[0])) for e in (A, B, C))
        self.fake.by_id[self.ids[C]]['alias'] = 'harbor'

    def page(self):
        """The running page, as a stand-in: it moves the account as the real page does (more.move)."""
        page = Page({'accounts': []},
                    on_post=lambda path, body: path == '/api/move' and more.move(self.ids[body['email']], body['to']))
        self.addCleanup(page.stop)
        os.environ['SWITCHBOARD_PORT'] = str(page.server_address[1])
        return page


class Move(Three):
    def test_an_account_goes_to_a_place_and_the_others_keep_their_order(self):
        self.assertEqual(self.run_cli('move', C, '1'), (0, 'Moved %s from 3 to 1.\n' % C + listed(C, A, B)))
        self.assertEqual(self.emails(), [C, A, B])
        self.assertEqual(self.run_cli('move', '1', '3'), (0, 'Moved %s from 1 to 3.\n' % C + listed(A, B, C)))
        self.assertEqual(self.run_cli('move', 'harbor', '2'), (0, 'Moved %s from 3 to 2.\n' % C + listed(A, C, B)))
        self.assertEqual(self.emails(), [A, C, B])

    def test_twice_with_the_same_words_is_safe(self):
        self.run_cli('move', A, '3')
        before = len(self.calls('reorder'))
        self.assertEqual(self.run_cli('move', A, '3'), (0, '%s is already at 3.\n' % A + listed(B, C, A)))
        self.assertEqual(len(self.calls('reorder')), before)              # nothing was changed the second time

    def test_a_place_that_does_not_exist_is_refused_with_the_list(self):
        for n in ('4', '0', '99'):
            with self.subTest(n=n):
                self.assertEqual(self.run_cli('move', A, n),
                                 (1, 'There is no place %s: Switchboard has 3 accounts.\n' % n + listed(A, B, C)))
        self.assertEqual(self.calls('reorder'), [])

    def test_wrong_words_exit_2_with_the_form(self):
        for args in ((), (A,), (A, 'first'), (A, '1', '2'), (A, '-1'), (A, '1.5')):
            with self.subTest(args=args):
                self.assertEqual(self.run_cli('move', *args), (2, 'switchboard move NAME N\n'))
        self.assertEqual(self.calls('reorder'), [])

    def test_an_unknown_name_is_refused_as_everywhere(self):
        with self.assertRaises(core.SwitchError) as e:
            self.run_cli('move', 'nobody@made-up.example', '1')
        self.assertEqual(str(e.exception), 'No account called nobody@made-up.example. Run switchboard to see them.')

    def test_it_goes_through_the_running_page(self):
        page = self.page()
        self.assertEqual(self.run_cli('move', B, '1'), (0, 'Moved %s from 2 to 1.\n' % B + listed(B, A, C)))
        self.assertEqual(page.posts, [('/api/move', {'email': B, 'to': 1}, '1')])

    def test_the_demo_refuses_it(self):
        core.DEMO = True
        with self.assertRaises(core.SwitchError) as e:
            self.run_cli('move', A, '2')
        self.assertIn('The demo has made-up accounts', str(e.exception))


class Swap(Three):
    def test_two_accounts_change_places(self):
        self.assertEqual(self.run_cli('swap', A, 'harbor'),
                         (0, 'Swapped %s (1 to 3) and %s (3 to 1).\n' % (A, C) + listed(C, B, A)))
        self.assertEqual(len(self.calls('reorder')), 1)                   # one change, not two
        self.assertEqual(self.run_cli('swap', '2', '1'),
                         (0, 'Swapped %s (2 to 1) and %s (1 to 2).\n' % (B, C) + listed(B, C, A)))

    def test_twice_puts_them_back(self):
        self.run_cli('swap', A, B)
        self.run_cli('swap', A, B)
        self.assertEqual(self.emails(), [A, B, C])

    def test_the_same_account_twice_changes_nothing(self):
        with self.assertRaises(core.SwitchError) as e:
            self.run_cli('swap', A, '1')
        self.assertEqual(str(e.exception), '%s is named twice; nothing was changed.' % A)
        self.assertEqual(self.calls('reorder'), [])

    def test_wrong_words_exit_2_with_the_form(self):
        for args in ((), (A,), (A, B, C)):
            with self.subTest(args=args):
                self.assertEqual(self.run_cli('swap', *args), (2, 'switchboard swap NAME NAME\n'))

    def test_it_goes_through_the_running_page(self):
        page = self.page()
        self.assertEqual(self.run_cli('swap', A, C),
                         (0, 'Swapped %s (1 to 3) and %s (3 to 1).\n' % (A, C) + listed(C, B, A)))
        self.assertEqual(page.posts, [('/api/move', {'email': A, 'to': 3}, '1'),
                                      ('/api/move', {'email': C, 'to': 1}, '1')])

class TheDocs(unittest.TestCase):
    def test_where_move_is_described_a_script_is_told_numbers_change(self):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'docs', 'commands.md'),
                  encoding='utf-8') as f:
            row = next(line for line in f if line.startswith('| `switchboard move NAME N` |'))
        self.assertIn('A number is a place in the list and changes when the list changes, so a script names an '
                      'account by its email or short name.', row)


class TheRuleOverManyCases(Sandbox):
    """move and swap over thousands of made-up lists (a fixed seed): every account stays in the list once, the one moved
    lands where it was asked, and the others keep their order; a swap changes exactly the two places."""

    def test_many_lists(self):
        rnd = random.Random(20261125)
        from switchboard import places
        for _ in range(3000):
            n = rnd.randint(1, 9)
            ids = ['acct-%d' % i for i in range(n)]
            rnd.shuffle(ids)
            who, to = rnd.choice(ids), rnd.randint(1, n)
            got = places.moved(ids, who, to)
            self.assertEqual(sorted(got), sorted(ids))
            self.assertEqual(got.index(who) + 1, to)
            self.assertEqual([x for x in got if x != who], [x for x in ids if x != who])
            a, b = rnd.choice(ids), rnd.choice(ids)
            if a == b:
                continue
            got = places.swapped(ids, a, b)
            self.assertEqual((got.index(a), got.index(b)), (ids.index(b), ids.index(a)))
            self.assertEqual([x for x in got if x not in (a, b)], [x for x in ids if x not in (a, b)])
            self.assertEqual(places.swapped(got, a, b), ids)


if __name__ == '__main__':
    unittest.main()
