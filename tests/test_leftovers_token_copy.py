"""A recovery copy of a login with no refresh token (a one-year token) says what it is in `switchboard leftovers`.

A switch no longer copies such a login aside, but a copy kept by other means is marked as it is made (vault.stash),
and that mark is what `leftovers` goes by (test_leftovers_judged_by_what_a_copy_holds.py). It is never any account's
sign-in, so an account saving a login since does not free it, and it is never said to be the only copy of a sign-in.
It is the only copy of that token, so `leftovers clear --all` keeps it and only `clear ID --only-copy` takes it.
Made-up tokens and accounts only; the Keychain is the suite's stand-in."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import unittest

from fakes_accounts import Rig
from switchboard import leftovers
from switchboard.accounts import bearer, live, recovery, vault

TOKEN = 'sk-ant-oat01-madeUpOneYearToken-0001-aaaaaaaaaaaa'
SAID = 'the only copy of a token that was in use, with no refresh token; Switchboard keeps no other copy of it'


class AnOldCopyOfAToken(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.addCleanup(self.rig.close)
        self.ctx = self.rig.ctx
        self.rig.two_accounts()                                     # Claude Code is on B
        text = bearer.login_text(TOKEN)
        self.key = 'safety:' + live.parse(text).text_fp
        self.at = self.ctx.wall()

    def copy(self, account_id=None):
        ok, _ = vault.stash(self.ctx, self.key, bearer.login_text(TOKEN), account_id=account_id,
                            reason='before switching to b@example.com')
        self.assertTrue(ok)
        return next(r for r in recovery.listing(self.ctx) if r['key'] == self.key)

    def test_it_says_what_it_is_and_is_kept_by_clear_all(self):
        row = self.copy()
        self.assertEqual((row['needed'], row['need'], row['needed_by']), (True, 'only_copy', None))
        self.assertEqual(row['why'], 'a token with no refresh token, in use before a switch to b@example.com')
        self.assertEqual(leftovers.what_to_do(row), 'needed: ' + SAID)
        self.assertEqual(leftovers.kept_said(row), 'Kept %s: it is %s. To give this copy up for good: switchboard '
                                                   'leftovers clear %s --only-copy.' % (row['id'], SAID, row['id']))
        res = recovery.clear(self.ctx, everything=True)
        self.assertEqual(([r['key'] for r in res['kept']], res['cleared']), ([self.key], []))
        res = recovery.clear(self.ctx, [row['id']], only_copy=True)
        self.assertEqual([r['key'] for r in res['cleared']], [self.key])

    def test_an_account_that_saved_a_login_since_does_not_make_it_safe_to_clear(self):
        with vault.changing(self.ctx) as doc:
            doc['accounts']['uuid-a']['saved_at'] = self.at + 3600.0
        row = self.copy('uuid-a')
        self.assertEqual((row['needed'], row['need']), (True, 'only_copy'))
        self.assertNotIn('login', leftovers.what_to_do(row))
        self.assertNotEqual(leftovers.what_to_do(row), 'safe to clear')

    def test_while_claude_code_holds_that_same_token_it_is_safe_to_clear(self):
        self.rig.set_live(bearer.login_text(TOKEN))
        row = self.copy()
        self.assertEqual((row['needed'], leftovers.what_to_do(row)), (False, 'safe to clear'))


if __name__ == '__main__':
    unittest.main()
