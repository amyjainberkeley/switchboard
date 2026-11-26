"""`switchboard leftovers` judges a recovery copy by what it held when it was made, never by reading it.

A copy is called the only copy of a token only when, as it was made, its text held a token or an API key and no
refresh token: a mark written beside it then says so. A copy of an emptied login, of MCP logins only, or of any text
with no token in it has no refresh fingerprint either, but it is no token: it is listed, and freed once its account
saves a login, exactly as before tokens were kept. A copy made before the mark existed may be a token's: it is kept,
and never called a token.
Listing never reads a copy's secret. Made-up tokens and accounts only; the Keychain is the suite's stand-in."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import unittest

from fakes_accounts import Rig
from switchboard import leftovers
from switchboard.accounts import bearer, live, recovery, vault

TOKEN = 'sk-ant-oat01-madeUpOneYearToken-1001-aaaaaaaaaaaa'
NO_TOKEN = {
    'an emptied login': json.dumps({'claudeAiOauth': {'accessToken': '', 'refreshToken': ''}}),
    'MCP logins only': json.dumps({'mcpOAuth': {'made-up-server': {'accessToken': 'mcp-made-up-1002'}}}),
    'a text that is no login': 'made-up text, no login in it',
}


class Case(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.addCleanup(self.rig.close)
        self.ctx = self.rig.ctx
        self.rig.two_accounts()                                     # Claude Code is on B
        self.at = self.ctx.wall()

    def copy(self, text, account_id='uuid-a'):
        key = 'safety:' + live.parse(text).text_fp
        ok, _ = vault.stash(self.ctx, key, text, account_id=account_id, reason='before switching to b@example.com')
        self.assertTrue(ok)
        return key

    def row(self, key):
        return next(r for r in recovery.listing(self.ctx) if r['key'] == key)

    def resaved(self):
        with vault.changing(self.ctx) as doc:
            doc['accounts']['uuid-a']['saved_at'] = self.at + 3600.0


class ACopyWithNoTokenInIt(Case):
    def test_it_is_no_token_and_its_account_saving_a_login_frees_it(self):
        for what, text in NO_TOKEN.items():
            with self.subTest(what):
                key = self.copy(text)
                row = self.row(key)
                self.assertFalse(row['token'])
                self.assertEqual(row['why'], 'the login in use before a switch to b@example.com')
                self.assertNotIn('token', leftovers.what_to_do(row))
                self.resaved()
                row = self.row(key)
                self.assertEqual((row['needed'], leftovers.what_to_do(row)), (False, 'safe to clear'))
                vault.drop_stash(self.ctx, key)
                with vault.changing(self.ctx) as doc:
                    doc['accounts']['uuid-a']['saved_at'] = self.at - 3600.0

    def test_a_copy_made_before_the_mark_is_kept_and_never_called_a_token(self):
        key = self.copy(bearer.login_text(TOKEN))
        with vault._manifest(self.ctx) as m:                        # as an older Switchboard listed it: no mark
            for e in m['entries']:
                e.pop('token', None)
        self.resaved()
        row = self.row(key)
        self.assertEqual((row['token'], row['needed'], row['need']), (False, True, 'only_copy'))
        self.assertNotIn('token', leftovers.what_to_do(row))


class ACopyOfAToken(Case):
    def test_it_is_marked_when_made_and_judged_without_being_read(self):
        key = self.copy(bearer.login_text(TOKEN))
        entry = next(e for e in vault.stashed(self.ctx) if e['key'] == key)
        self.assertIs(entry.get('token'), True)
        self.assertNotIn(TOKEN, json.dumps(entry))
        self.resaved()
        before = self.rig.kc.count('read', service=vault.SERVICE_RECOVERY)
        row = self.row(key)
        self.assertEqual(self.rig.kc.count('read', service=vault.SERVICE_RECOVERY), before)
        self.assertEqual((row['token'], row['needed'], row['need']), (True, True, 'only_copy'))

    def test_a_copy_kept_in_a_file_carries_the_mark_too(self):
        text = bearer.login_text(TOKEN)
        key = 'safety:' + live.parse(text).text_fp
        self.rig.kc.rule('write', service=vault.SERVICE_RECOVERY, account=key, why='denied')
        self.assertEqual(vault.stash(self.ctx, key, text, account_id='uuid-a', reason='before switching to b'),
                         (True, 'file'))
        with vault._manifest(self.ctx) as m:                        # a manifest that lost its line
            m['entries'] = []
        self.assertTrue(self.row(key)['token'])
        sign_in = next(iter(NO_TOKEN.values()))
        key2 = 'safety:' + live.parse(sign_in).text_fp
        self.rig.kc.rule('write', service=vault.SERVICE_RECOVERY, account=key2, why='denied')
        vault.stash(self.ctx, key2, sign_in, account_id='uuid-a', reason='before switching to b')
        with vault._manifest(self.ctx) as m:
            m['entries'] = []
        self.assertFalse(self.row(key2)['token'])
        self.assertIs(next(e for e in vault.stashed(self.ctx) if e['key'] == key2).get('token'), False)


if __name__ == '__main__':
    unittest.main()
