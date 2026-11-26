"""A login that can't be renewed and that no record owns is no account's, for reading usage.

Claude Code may be on such a login while its settings still name a kept account: a one-year token whose record was
removed by hand, a token Claude Code was given by itself, an item written by hand. The usage reader is never handed
it: each account's usage is read with that account's own saved sign-in, as on any other day, and no numbers are read
with the token and filed under the account the settings name. Made-up tokens and accounts only; the Keychain and
Anthropic are the suite's stand-ins."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import unittest

from fakes_accounts import Rig, identity_block
from switchboard.accounts import bearer, capture, context, roster, switcher, vault
from switchboard.accounts.api import PRESENT

TOKEN = 'sk-ant-oat01-madeUpOneYearToken-0601-aaaaaaaaaaaa'


class Case(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        self.rig.two_accounts()                                     # Claude Code is on B

    def tearDown(self):
        self.use.__exit__(None, None, None)
        self.rig.close()

    def saved_access(self, aid):
        return json.loads(self.rig.vault(aid))['claudeAiOauth']['accessToken']

    def assert_each_reads_its_own(self):
        roster.forget_live(self.ctx)
        capture.reset()
        for aid in ('uuid-a', 'uuid-b'):
            for fresh in (False, True):
                got = roster.access_token(self.ctx, aid, fresh=fresh)
                self.assertEqual(got.kind, PRESENT, (aid, fresh, got.why))
                self.assertNotEqual(got.value, TOKEN, (aid, fresh))
                self.assertEqual(got.value, self.saved_access(aid), (aid, fresh))
        lv = roster.live_view(self.ctx)
        self.assertEqual((lv.state, lv.account_id, lv.email), ('other', None, None))


class NoAccountsUsageIsReadWithIt(Case):
    def test_a_token_whose_record_was_removed_by_hand(self):
        rec, _ = bearer.add(self.ctx, TOKEN, for_id='uuid-a')
        self.assertTrue(switcher.switch(self.ctx, rec['id'], source='cli').ok)
        self.assertEqual(self.rig.config()['oauthAccount']['accountUuid'], 'uuid-a')    # its settings name a
        with vault.changing(self.ctx) as doc:
            doc['bearers'] = {}
        self.assert_each_reads_its_own()

    def test_a_token_claude_code_was_given_with_the_settings_of_an_account(self):
        self.rig.set_live(bearer.login_text(TOKEN))
        self.rig.set_config(identity_block('uuid-a', 'a@example.com', 'org-a'))
        self.assert_each_reads_its_own()

    def test_a_bare_token_with_the_settings_of_an_account(self):
        self.rig.set_live(TOKEN)
        self.rig.set_config(identity_block('uuid-a', 'a@example.com', 'org-a'))
        self.assert_each_reads_its_own()


class ASignInIsReadAsBefore(Case):
    def test_the_account_in_use_is_read_with_claude_codes_own_login(self):
        roster.forget_live(self.ctx)
        got = roster.access_token(self.ctx, 'uuid-b')
        self.assertEqual(got.value, json.loads(self.rig.live_text())['claudeAiOauth']['accessToken'])
        self.assertEqual(roster.live_view(self.ctx).account_id, 'uuid-b')


if __name__ == '__main__':
    unittest.main()
