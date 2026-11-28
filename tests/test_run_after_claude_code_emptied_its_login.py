"""`switchboard run` as the account Claude Code is on, after Claude Code emptied its own login (Anthropic refused its
renewal: the login object stays in the live item with both tokens blank). The live login is not that account's login
any more, so the run is not a plain one on the default login: it starts from the login Switchboard kept for the account,
in a private folder, as a run as any other account does, and leaves the emptied live item as it is."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import os
import time
import unittest

from switchboard.accounts import runas
from test_accounts_runas import RunCase


def emptied(text):
    """What Claude Code leaves after Anthropic refused its login: the login object with its tokens blank."""
    blob = json.loads(text)
    blob['claudeAiOauth'].update({'accessToken': '', 'refreshToken': ''})
    return json.dumps(blob, separators=(',', ':'))


class AfterClaudeCodeEmptiedItsLogin(RunCase):
    def setUp(self):
        super().setUp()
        self.wiped = emptied(self.b_text)                            # Claude Code is on B, and emptied B's login
        self.rig.set_live(self.wiped)

    def test_the_run_starts_from_the_kept_login_in_a_private_folder(self):
        plain = []
        code = runas.run_as(self.ctx, 'uuid-b', ['-p', 'hi'], env=self.env(), timeout=120, before=plain.append)
        self.assertEqual((code, plain), (0, [False]))
        seen = self.seen()
        folder = seen['env']['CLAUDE_CONFIG_DIR']
        self.assertTrue(folder and os.path.basename(os.path.dirname(folder)) == 'run', folder)
        kept = json.loads(self.rig.vault('uuid-b'))['claudeAiOauth']
        self.assertEqual(json.loads(seen['seed'])['claudeAiOauth']['refreshToken'], kept['refreshToken'])
        self.assertEqual(seen['claude_json']['oauthAccount']['accountUuid'], 'uuid-b')
        self.assertEqual(self.rig.live_text(), self.wiped)            # the emptied live item: left as it is
        self.assertEqual(self.rig.doc()['claims'], {})                # and the claim let go when it ended

    def test_an_expired_kept_login_is_handed_over_unspent_for_claude_code_to_renew_in_the_run(self):
        # The account is still the one Claude Code is on, so Switchboard never spends its refresh token itself
        # (tokens.refresh: live_same_account). Claude Code in the run renews it; finish() saves that back. When
        # Anthropic refuses it there too, Claude Code in the run asks for a sign-in in its own words.
        self.rig.set_saved_expiry('uuid-b', time.time() - 60)
        kept = self.rig.vault('uuid-b')
        self.spec(rotate=True)
        code = runas.run_as(self.ctx, 'uuid-b', [], env=self.env(), timeout=120)
        self.assertEqual(code, 0)
        self.assertEqual(self.rig.server.token_posts(), [])            # nothing spent by Switchboard
        self.assertEqual(json.loads(self.seen()['seed'])['claudeAiOauth'], json.loads(kept)['claudeAiOauth'])
        self.assertEqual(json.loads(self.rig.vault('uuid-b'))['claudeAiOauth']['refreshToken'],
                         'sk-ant-ort01-renewed-in-run')                 # what the run renewed is kept
        self.assertEqual(self.rig.live_text(), self.wiped)
        self.assertEqual(self.rig.doc()['claims'], {})


if __name__ == '__main__':
    unittest.main()
