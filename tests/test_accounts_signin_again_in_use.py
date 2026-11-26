"""What signing in again does for the account Claude Code is on, while its sign-in is about to end and after.

Anthropic ends each sign-in on a set date (refreshTokenExpiresAt); a renewal keeps it, a new sign-in sets it fresh.
The page's Sign in (and Sign in to all) runs Claude Code's own sign-in in a throwaway folder and hands it to the engine
(api.add_login). For an account Claude Code is not on, that new login is what the next switch puts live. For the
account Claude Code is on, the engine puts the new login live at once, through the switch transaction, when Claude
Code's own is an earlier sign-in of it (roster._older_live) or can't work any more (roster._dead_live). So:

- before the date, the page's sign-in again gives Claude Code the new sign-in, with its new date, said as the same
  account's new sign-in (not a switch);
- /login in Claude Code gives Claude Code the new sign-in too, and Switchboard keeps it as that account's (capture);
- after the date, the page's sign-in again gives Claude Code the new login.

Made-up accounts on the engine's own fakes (tests/fakes_accounts.py): a fake Keychain and a fake Anthropic.
"""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import os
import time
import unittest

from fakes_accounts import Rig, identity_block
from switchboard.accounts import api, capture, context, facts, roster, tokens

D = 86400


class SignInAgainOnTheAccountInUse(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        self.rig.keep('uuid-a', 'a@example.com', 'org-a')
        self.n = 0

    def tearDown(self):
        self.use.__exit__(None, None, None)
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        self.rig.close()

    def on_b(self, days):
        """Claude Code on B, kept, with a sign-in that ends `days` from now (in the past when negative)."""
        text = self.rig.server.add('uuid-b', 'b@example.com', 'org-b', rx_days=days)
        self.rig.set_live(text)
        self.rig.set_config(identity_block('uuid-b', 'b@example.com', 'org-b'))
        roster.add_current(self.ctx)
        roster.forget_live()
        return text

    def sign_in_folder(self):
        """What the page's sign-in again leaves: Claude Code's own new login in a throwaway folder's Keychain item."""
        self.n += 1
        d = os.path.join(self.rig.data, 'logins', 'f%d' % self.n)
        os.makedirs(d, mode=0o700)
        text = self.rig.server.add('uuid-b', 'b@example.com', 'org-b')
        self.rig.kc.put(facts.keychain_service({'CLAUDE_CONFIG_DIR': d}), self.rig.account, text)
        with open(os.path.join(d, '.claude.json'), 'w') as f:
            json.dump({'oauthAccount': identity_block('uuid-b', 'b@example.com', 'org-b')}, f)
        return d, text

    def ends(self, text):
        return json.loads(text)['claudeAiOauth']['refreshTokenExpiresAt'] / 1000.0

    def b(self):
        return next(a for a in api.accounts() if a.id == 'uuid-b')

    def test_before_its_date_the_pages_sign_in_again_gives_claude_code_the_new_sign_in(self):
        old = self.on_b(3)
        self.assertEqual(self.b().status, 'expiring')
        self.assertAlmostEqual(self.b().refresh_expires_at, time.time() + 3 * D, delta=60)
        d, new = self.sign_in_folder()
        api.add_login(d)
        self.assertNotEqual(self.rig.live_text(), old)
        self.assertEqual(json.loads(self.rig.live_text())['claudeAiOauth']['refreshToken'],
                         json.loads(new)['claudeAiOauth']['refreshToken'])
        self.assertAlmostEqual(self.ends(self.rig.live_text()), self.ends(new), delta=1)     # Claude Code's date moved
        self.assertEqual(api.live().account_id, 'uuid-b')                                   # the same account
        self.assertEqual([e for e in self.rig.events if 'Switched' in e[0]], [])
        self.assertIn('Claude Code is on the new sign-in of b@example.com', [e[0] for e in self.rig.events])
        self.assertEqual(self.rig.vault('uuid-b'), new)
        self.assertAlmostEqual(self.b().refresh_expires_at, self.ends(new), delta=1)
        self.assertEqual(self.b().status, 'ok')

    def test_login_in_claude_code_gives_claude_code_the_new_sign_in_and_switchboard_keeps_it(self):
        self.on_b(3)
        new = self.rig.sign_in('uuid-b', 'b@example.com', 'org-b')     # /login in Claude Code, as the same account
        capture.keep_pass(self.ctx, force=True)
        self.assertEqual(self.rig.live_text(), new)
        self.assertEqual(self.rig.vault('uuid-b'), new)
        self.assertAlmostEqual(self.b().refresh_expires_at, self.ends(new), delta=1)
        self.assertEqual(self.b().status, 'ok')

    def test_after_its_date_the_pages_sign_in_again_gives_claude_code_the_new_login(self):
        self.on_b(-1)
        self.assertEqual(self.b().status, 'needs_login')
        d, new = self.sign_in_folder()
        api.add_login(d)
        self.assertEqual(json.loads(self.rig.live_text())['claudeAiOauth']['refreshToken'],
                         json.loads(new)['claudeAiOauth']['refreshToken'])
        self.assertEqual(api.live().account_id, 'uuid-b')
        self.assertIn('Claude Code is on the new sign-in of b@example.com', [e[0] for e in self.rig.events])
        self.assertEqual(self.b().status, 'ok')


class TheDocsSayIt(unittest.TestCase):
    def test_troubleshooting_says_the_account_in_use_signs_in_from_the_page_like_any_other(self):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'docs', 'troubleshooting.md'),
                  encoding='utf-8') as f:
            doc = f.read()
        entry = doc[doc.index('## An account asks for a sign-in again'):]
        entry = entry[:entry.index('\n## ')]
        self.assertIn('Press Sign in to all, in the line above the accounts (it shows while a sign-in has ended or '
                      'ends within the week): it signs in again to each account that needs it, one after another',
                      entry)
        self.assertIn('The account Claude Code is on signs in the same way, and Claude Code moves to its new sign-in '
                      'at once.', entry)
        self.assertNotIn('renews only the copy Switchboard keeps', entry)
        # the date is the latest a sign-in ends (Claude Code: "Sign-in ends: between <date less 3 days> and <date>")
        self.assertIn('a sign-in can end any time in the three days before it', entry)
        self.assertIn('Claude Code shows the same for the account it is on: `claude auth status --text`.', entry)


if __name__ == '__main__':
    unittest.main()
