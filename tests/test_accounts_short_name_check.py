"""A short name is checked before anything is signed in to (`switchboard add --as SHORT`): the same rule
`switchboard alias` keeps, asked without changing anything. A name that is taken, that is only digits, that looks like
an email, or that is an account's id is refused; the account it is kept for may already have it."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import unittest

from fakes_accounts import Rig
from switchboard.accounts import api, roster, vault


class TheCheck(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.addCleanup(self.rig.close)
        self.ctx = self.rig.ctx
        self.rig.keep('uuid-n', 'maya@northwind.example', 'org-n')
        self.rig.keep('uuid-f', 'maya@fernhill.example', 'org-f')
        roster.set_alias(self.ctx, 'maya@northwind.example', 'work')

    def refused(self, word, keep_for=None):
        with self.assertRaises(api.AccountsError) as e:
            roster.check_alias(self.ctx, word, keep_for)
        return str(e.exception)

    def test_a_free_name_comes_back_as_it_is_kept(self):
        self.assertEqual(roster.check_alias(self.ctx, '  Home '), 'home')
        self.assertEqual(roster.check_alias(self.ctx, 'side.project_2'), 'side.project_2')

    def test_what_alias_refuses_is_refused_here_in_the_same_words(self):
        for word in ('12', 'maya@harbor.example', '-x', 'two words', 'ü'):
            with self.subTest(word=word):
                said = self.refused(word)
                with self.assertRaises(api.AccountsError) as e:
                    roster.set_alias(self.ctx, 'maya@fernhill.example', word)
                self.assertEqual(str(e.exception), said)

    def test_a_taken_name_is_refused_unless_it_is_the_accounts_own(self):
        self.assertEqual(self.refused('WORK'), '"work" is already the short name of maya@northwind.example.')
        self.assertEqual(roster.check_alias(self.ctx, 'work', 'uuid-n'), 'work')
        self.assertEqual(self.refused('work', 'uuid-f'), '"work" is already the short name of maya@northwind.example.')
        self.assertEqual(self.refused('uuid-f'), '"uuid-f" is an account id; choose another short name.')

    def test_it_changes_nothing(self):
        before = vault.load(self.ctx)
        roster.check_alias(self.ctx, 'home')
        self.refused('work')
        after = vault.load(self.ctx)
        self.assertEqual(before['accounts'], after['accounts'])
        self.assertEqual(before['order'], after['order'])

    def test_off_is_refused_since_switchboard_folder_off_reads_it_as_its_own_word(self):
        said = '"off" is a word of its own in switchboard folder off; choose another short name.'
        for word in ('off', 'OFF', ' Off '):
            with self.subTest(word=word):
                self.assertEqual(self.refused(word), said)
                with self.assertRaises(api.AccountsError) as e:
                    roster.set_alias(self.ctx, 'maya@fernhill.example', word)
                self.assertEqual(str(e.exception), said)
        self.assertEqual(roster.check_alias(self.ctx, 'office'), 'office')

    def test_no_sentence_says_alias(self):
        for word in ('12', 'WORK', 'uuid-f', 'off'):
            with self.subTest(word=word):
                self.assertNotIn('alias', self.refused(word))
        with self.assertRaises(api.AccountsError) as e:
            roster.find_id(self.ctx, '')
        self.assertEqual(str(e.exception), 'Name an account: its number, email, short name or id.')

    def test_no_name_is_none(self):
        self.assertIsNone(roster.check_alias(self.ctx, ''))
        self.assertIsNone(roster.check_alias(self.ctx, None))

    def test_the_api_names_it(self):
        self.assertTrue(callable(getattr(api, 'check_alias', None)))


if __name__ == '__main__':
    unittest.main()
