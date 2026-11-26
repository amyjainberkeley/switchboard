"""A recovery copy listed before Switchboard marked what each copy holds is kept as a possible only copy.

Every safety copy is now marked when it is made: whether its text held a token or a key and no refresh token. A copy
with no refresh fingerprint that an older Switchboard listed carries no mark, and it may hold a one-year token, the
only copy of one. It can't be told apart without reading it, which `leftovers` never does, so a login its account
saved since does not free it: it stays listed as needed, the only copy of a login that was in use, until it is named
with --only-copy, as a copy with no account always was. It is never called a token. A copy made now that holds no
token is marked so and is freed exactly as before tokens were kept. Made-up tokens and accounts only; the Keychain is
the suite's stand-in."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import unittest

from fakes_accounts import Rig
from switchboard import leftovers
from switchboard.accounts import bearer, live, recovery, vault

TOKEN = 'sk-ant-oat01-madeUpOneYearToken-0901-aaaaaaaaaaaa'
OLD = {
    'a token': bearer.login_text(TOKEN),
    'a bare token': TOKEN,
    'an emptied login': json.dumps({'claudeAiOauth': {'accessToken': '', 'refreshToken': ''}}),
}


class Case(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.addCleanup(self.rig.close)
        self.ctx = self.rig.ctx
        self.rig.two_accounts()                                     # Claude Code is on B
        self.at = self.ctx.wall()

    def copy(self, text, in_file=False):
        key = 'safety:' + live.parse(text).text_fp
        if in_file:
            self.rig.kc.rule('write', service=vault.SERVICE_RECOVERY, account=key, why='denied')
        ok, _ = vault.stash(self.ctx, key, text, account_id='uuid-a', reason='before switching to b@example.com')
        self.assertTrue(ok)
        return key

    def unmark(self):
        """As an older Switchboard listed its copies: no mark beside any of them."""
        with vault._manifest(self.ctx) as m:
            for e in m['entries']:
                e.pop('token', None)

    def row(self, key):
        return next(r for r in recovery.listing(self.ctx) if r['key'] == key)

    def resaved(self):
        with vault.changing(self.ctx) as doc:
            doc['accounts']['uuid-a']['saved_at'] = self.at + 3600.0


class AnOldCopyWithNoMark(Case):
    def test_its_account_saving_a_login_does_not_free_it(self):
        for what, text in OLD.items():
            with self.subTest(what):
                key = self.copy(text)
                self.unmark()
                self.resaved()
                row = self.row(key)
                self.assertEqual((row['needed'], row['need'], row['token']), (True, 'only_copy', False), what)
                self.assertEqual(row['why'], 'the login in use before a switch to b@example.com')
                self.assertNotIn('token', leftovers.what_to_do(row))
                vault.drop_stash(self.ctx, key)

    def test_clearing_every_copy_keeps_it_and_naming_it_with_the_flag_clears_it(self):
        key = self.copy(bearer.login_text(TOKEN))
        self.unmark()
        self.resaved()
        before = self.rig.kc.count('read', service=vault.SERVICE_RECOVERY)
        recovery.clear(self.ctx, everything=True)
        self.assertIn(key, [e['key'] for e in vault.stashed(self.ctx)])
        self.assertEqual(self.rig.kc.count('read', service=vault.SERVICE_RECOVERY), before)     # never read to tell
        recovery.clear(self.ctx, ids=[self.row(key)['id']], only_copy=True)
        self.assertNotIn(key, [e['key'] for e in vault.stashed(self.ctx)])


class ACopyMadeNow(Case):
    def test_it_is_marked_either_way_in_the_list_and_in_its_file(self):
        for in_file in (False, True):
            for text, want in ((bearer.login_text(TOKEN), True), (OLD['an emptied login'], False)):
                with self.subTest(in_file=in_file, token=want):
                    key = self.copy(text, in_file=in_file)
                    if in_file:
                        with vault._manifest(self.ctx) as m:            # a manifest that lost its line
                            m['entries'] = [e for e in m['entries'] if e['key'] != key]
                    entry = next(e for e in vault.stashed(self.ctx) if e['key'] == key)
                    self.assertIs(entry.get('token'), want)
                    vault.drop_stash(self.ctx, key)

    def test_one_with_no_token_in_it_is_freed_by_a_later_save_as_before(self):
        key = self.copy(OLD['an emptied login'])
        self.resaved()
        row = self.row(key)
        self.assertEqual((row['needed'], row['token'], leftovers.what_to_do(row)), (False, False, 'safe to clear'))


if __name__ == '__main__':
    unittest.main()
