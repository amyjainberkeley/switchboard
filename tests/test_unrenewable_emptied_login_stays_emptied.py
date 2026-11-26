"""A sign-in Claude Code emptied stays an emptied sign-in, whatever sits beside it in the login item.

When Anthropic refuses a sign-in, Claude Code empties its login object and keeps the rest of the item: MCP logins, a
plugin's own secrets. One of those may hold an API key or a token. The item is then still the emptied sign-in of the
account its settings name: it reads as emptied for that account, exactly as without the sibling, so auto-switch moves
off it and the page says to sign in again. Nothing in it is handed to a usage reader, and, since its text holds a key
and no refresh token, a switch away still makes no copy of it. Made-up tokens and accounts only; the Keychain and
Anthropic are the suite's stand-ins."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import os
import unittest

from fakes_accounts import Rig
from switchboard.accounts import context, roster, switcher, vault
from switchboard.accounts.api import PRESENT

KEY = 'sk-ant-api03-madeUpPluginKey-0701-cccccccccccccccccc'
TOKEN = 'sk-ant-oat01-madeUpOneYearToken-0702-aaaaaaaaaaaa'
EMPTIED = {'claudeAiOauth': {'accessToken': '', 'refreshToken': '', 'expiresAt': None}}
BESIDE = {
    "a plugin's own API key": {'pluginSecrets': {'made-up-plugin': {'ANTHROPIC_API_KEY': KEY}}},
    'a key at the top of the item': {'primaryApiKey': KEY},
    'an MCP login holding a token': {'mcpOAuth': {'made-up-server': {'accessToken': TOKEN}}},
}


class Case(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        self.rig.two_accounts()                                     # Claude Code's settings name B

    def tearDown(self):
        self.use.__exit__(None, None, None)
        self.rig.close()

    def view(self, beside):
        self.rig.set_live(json.dumps(dict(EMPTIED, **beside)))
        roster.forget_live(self.ctx)
        lv = roster.live_view(self.ctx)
        return lv.state, lv.account_id, lv.email, lv.why

    def held_elsewhere(self, secret):
        """Every Keychain item but Claude Code's own, and every recovery file, that holds `secret`. (A sibling Claude
        Code shares across accounts stays in its own item, so that item is not looked in.)"""
        out = [k for k, v in self.rig.sec.items().items()
               if k != (self.rig.service, self.rig.account) and secret in (v or '')]
        for folder, _, files in os.walk(vault._recovery_dir(self.ctx)):
            for name in files:
                with open(os.path.join(folder, name), 'rb') as f:
                    if secret.encode() in f.read():
                        out.append(name)
        return out

class ItReadsAsEmptied(Case):
    def test_for_the_account_its_settings_name_as_without_the_sibling(self):
        plain = self.view({})
        self.assertEqual(plain[:2], ('wiped', 'uuid-b'))
        self.assertIn('emptied', plain[3])
        for what, beside in BESIDE.items():
            self.assertEqual(self.view(beside), plain, what)

    def test_each_account_is_read_with_its_own_saved_sign_in(self):
        for what, beside in BESIDE.items():
            self.view(beside)
            for aid in ('uuid-a', 'uuid-b'):
                got = roster.access_token(self.ctx, aid)
                self.assertEqual(got.kind, PRESENT, (what, aid, got.why))
                self.assertEqual(got.value, json.loads(self.rig.vault(aid))['claudeAiOauth']['accessToken'],
                                 (what, aid))


class ASwitchAwayCopiesNothingHoldingTheKey(Case):
    def test_no_copy_is_made(self):
        for what, beside in BESIDE.items():
            self.rig.set_live(json.dumps(dict(EMPTIED, **beside)))
            res = switcher.switch(self.ctx, 'uuid-a', kind='manual', source='cli', reason='test', interactive=True)
            self.assertTrue(res.ok, (what, res.note))
            self.assertEqual(vault.stashed(self.ctx), [], what)
            for secret in (KEY, TOKEN):
                self.assertEqual(self.held_elsewhere(secret), [], what)
            self.tearDown()                                         # the next case on a made-up Mac of its own
            self.setUp()


if __name__ == '__main__':
    unittest.main()
