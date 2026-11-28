import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# History's "from" of a switch names where Claude Code came from: the account it was on, or "the one-year token for"
# that account when Claude Code was on a kept token. The kept token's record and the lock held while the new login is
# written are two things: a switch from a sign-in is never said to come from a token, and a switch from a token names
# the token's account.
#
# The engine is the real one on a made-up Mac (fakes_accounts.Rig: the suite's stand-in Keychain and Anthropic); the
# page is not running. Made-up tokens and accounts only.
import unittest

from switchboard.accounts import bearer
from test_tokens_with_the_shared_rules import TOKEN, Case


class FromWhere(Case):
    def setUp(self):
        super().setUp()
        self.rig.two_accounts()                         # Claude Code is on b@example.com, number 2

    def said(self):
        return [facts for _, kind, facts in self.rig.events if kind == 'switch'][-1]

    def test_a_switch_from_a_sign_in_names_that_account(self):
        code, _, err = self.run_cli('switch', '1')
        self.assertEqual(code, 0, err)
        self.assertEqual((self.said()['frm'], self.said()['to']), ('b@example.com', 'a@example.com'))

    def test_a_switch_from_a_kept_token_names_its_account(self):
        bearer.add(self.ctx, TOKEN, for_id='uuid-a', alias='a-tok')
        code, _, err = self.run_cli('switch', 'a-tok')
        self.assertEqual(code, 0, err)
        code, _, err = self.run_cli('switch', '2')
        self.assertEqual(code, 0, err)
        self.assertEqual((self.said()['frm'], self.said()['to']), ('the one-year token for a@example.com',
                                                                   'b@example.com'))


if __name__ == '__main__':
    unittest.main()
