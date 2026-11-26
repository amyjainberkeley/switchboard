"""A one-year token in Claude Code's login item is never copied aside and never sent to the profile endpoint.

Two ways it could be: the token's record goes (`switchboard remove NAME`, or `add --token` with a new token for the
same account) while Claude Code is still on it, so the next switch away no longer knows the login is a kept token.
So a token Claude Code is on can't be removed or replaced until Claude Code is switched away from it. And any login in
Claude Code's item with an access token and no refresh token is treated as a token, whether or not a record owns it:
nothing asks Anthropic whose it is, and no recovery copy of it is made. Made-up tokens and accounts only; the Keychain
and Anthropic are the suite's stand-ins."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import os
import unittest
from unittest import mock

from fakes_accounts import Rig
from switchboard.accounts import api, bearer, context, identity, switcher, vault
from switchboard.accounts.api import AccountsError

TOKEN = 'sk-ant-oat01-madeUpOneYearToken-0001-aaaaaaaaaaaa'
TOKEN2 = 'sk-ant-oat01-madeUpOneYearToken-0002-bbbbbbbbbbbb'


class Case(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.ctx = self.rig.ctx
        self.a_text, self.b_text = self.rig.two_accounts()          # Claude Code is on B
        self.asked = []
        real = identity.lookup_profile

        def spy(ctx, access_token, *args, **kwargs):
            self.asked.append(access_token)
            return real(ctx, access_token, *args, **kwargs)
        patch = mock.patch.object(identity, 'lookup_profile', spy)
        patch.start()
        self.addCleanup(patch.stop)

    def tearDown(self):
        switcher._HOOK = None
        self.rig.close()

    def on_token_for_a(self):
        rec, _ = bearer.add(self.ctx, TOKEN, for_id='uuid-a', alias='a-token')
        self.assertTrue(switcher.switch(self.ctx, rec['id'], source='cli').ok)
        return rec

    def assert_token_only_in_its_own_item(self, *tokens):
        """No Keychain item but a kept token's own holds the token, no file holds it, and nothing asked Anthropic's
        profile endpoint with it."""
        for (service, account), value in self.rig.sec.items().items():
            if service == bearer.SERVICES[bearer.TOKEN]:
                continue
            for t in tokens:
                self.assertNotIn(t, value or '', '%s / %s' % (service, account))
        for folder, _, files in os.walk(self.rig.root):
            for name in files:
                with open(os.path.join(folder, name), 'rb') as f:
                    data = f.read()
                for t in tokens:
                    self.assertNotIn(t.encode(), data, os.path.join(folder, name))
        for t in tokens:
            self.assertNotIn(t, self.asked)


class WhileClaudeCodeIsOnIt(Case):
    def test_removing_the_token_in_use_is_refused_until_claude_code_is_switched_away(self):
        rec = self.on_token_for_a()
        with self.assertRaises(AccountsError) as e:
            bearer.remove(self.ctx, rec['id'])
        self.assertEqual(str(e.exception), 'Claude Code is on the one-year token for a@example.com; switch to '
                                           'another account first, then remove it. Nothing was removed.')
        self.assertIn(rec['id'], bearer.records(self.rig.doc()))
        self.assertEqual(self.rig.sec.get('Switchboard token', rec['id']), TOKEN)
        self.assertTrue(switcher.switch(self.ctx, 'uuid-b', source='cli').ok)
        self.assertEqual(self.rig.live_text(), self.b_text)
        self.assertEqual(vault.stashed(self.ctx), [])
        self.assert_token_only_in_its_own_item(TOKEN)
        self.assertEqual(bearer.remove(self.ctx, rec['id']), 'a@example.com')     # switched away: it goes
        self.assertIsNone(self.rig.sec.get('Switchboard token', rec['id']))

    def test_a_new_token_for_the_account_in_use_is_refused_until_claude_code_is_switched_away(self):
        rec = self.on_token_for_a()
        with self.assertRaises(AccountsError) as e:
            bearer.add(self.ctx, TOKEN2, for_id='uuid-a')
        self.assertEqual(str(e.exception), 'Claude Code is on the one-year token for a@example.com; switch to '
                                           'another account first, then keep the new one. Nothing was kept.')
        self.assertEqual(self.rig.sec.get('Switchboard token', rec['id']), TOKEN)
        self.assertEqual(bearer.records(self.rig.doc())[rec['id']]['fp'], bearer.fingerprint(TOKEN))
        again, what = bearer.add(self.ctx, TOKEN, for_id='uuid-a')        # the same token again: kept already
        self.assertEqual((what, again['id']), ('same', rec['id']))
        self.assertTrue(switcher.switch(self.ctx, 'uuid-b', source='cli').ok)
        self.assert_token_only_in_its_own_item(TOKEN, TOKEN2)
        _, what = bearer.add(self.ctx, TOKEN2, for_id='uuid-a')
        self.assertEqual(what, 'replaced')

    def test_removing_the_account_whose_token_is_in_use_is_refused(self):
        rec = self.on_token_for_a()
        with context.use(self.ctx), self.assertRaises(AccountsError) as e:
            api.remove('a@example.com')
        self.assertIn('switch to another account first', str(e.exception))
        self.assertIn('uuid-a', self.rig.doc()['accounts'])
        self.assertIn(rec['id'], bearer.records(self.rig.doc()))
        self.assertTrue(switcher.switch(self.ctx, 'uuid-b', source='cli').ok)
        self.assert_token_only_in_its_own_item(TOKEN)

    def test_removing_a_token_claude_code_is_not_on_still_works(self):
        rec, _ = bearer.add(self.ctx, TOKEN, label='spare')
        self.assertEqual(bearer.remove(self.ctx, rec['id']), 'spare')
        self.assertEqual(bearer.records(self.rig.doc()), {})


class ALoginWithNoRefreshTokenNoRecordOwns(Case):
    """What a list from before this rule, or one edited by hand, can leave: Claude Code on a token nothing owns."""

    def unowned_token_live(self, label_uuid='uuid-a'):
        rec = self.on_token_for_a()
        with vault.changing(self.ctx) as doc:           # its record goes, as a hand edit would take it
            doc['bearers'] = {}
        self.rig.sec.drop('Switchboard token', rec['id'])
        self.assertEqual(json.loads(self.rig.live_text())['claudeAiOauth']['accessToken'], TOKEN)
        if label_uuid is None:
            self.rig.set_config(None)
        return rec

    def test_a_switch_away_neither_asks_anthropic_nor_keeps_a_copy(self):
        for label in ('uuid-a', None):
            with self.subTest(label=label):
                self.unowned_token_live(label)
                res = switcher.switch(self.ctx, 'uuid-b', source='cli')
                self.assertTrue(res.ok)
                self.assertEqual(self.rig.live_text(), self.b_text)
                self.assertEqual(vault.stashed(self.ctx), [])
                self.assertEqual(res.note, 'Claude Code was on a login with no refresh token; no copy of it was made.')
                self.assert_token_only_in_its_own_item(TOKEN)

    def test_a_switch_cut_off_after_the_write_is_settled_without_a_copy(self):
        self.unowned_token_live()

        def stop(name):
            if name == 'credential_written':
                raise KeyboardInterrupt()
        switcher._HOOK = stop
        with self.assertRaises(KeyboardInterrupt):
            switcher.switch(self.ctx, 'uuid-b', source='cli')
        switcher._HOOK = None
        self.assertTrue(switcher.resolve_journal(self.ctx))
        self.assertEqual(self.rig.live_text(), self.b_text)
        self.assertEqual(vault.stashed(self.ctx), [])
        self.assert_token_only_in_its_own_item(TOKEN)


if __name__ == '__main__':
    unittest.main()
