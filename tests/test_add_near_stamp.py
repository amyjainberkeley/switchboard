"""Adding a login whose end stamp is a little under a kept account's never puts it in that account's slot.

A renewal by Claude Code can lower a sign-in's stamp a little, so a stamp a little under a kept account's may be that
account's renewal. A login of ANOTHER account signed in just before it has such a stamp too. The page's Add
(roster.add_current, roster.add_login) never takes such a stamp alone as proof: a label naming another account rules it
out, and Anthropic (or, with no answer, the label) says whose the login is.
"""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import os
import time
import unittest

from fakes_accounts import identity_block
from same_signin_fakes import email_of, keep_at, new_rig, org_of, sign_in_at
from switchboard.accounts import facts, net, roster, tokens


class Case(unittest.TestCase):
    def setUp(self):
        self.rig, self.clock = new_rig()
        self.ctx = self.rig.ctx
        self.server = self.rig.server
        self.R = int((time.time() + 25 * 86400) * 1000)
        self.n = 0

    def tearDown(self):
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        self.rig.close()

    def no_answer(self):
        self.server.script.append(('profile', net.NetError('timeout', True, 'no answer')))

    def accounts(self):
        return sorted(self.rig.doc()['accounts'])

    def folder(self, uuid, rx_ms):
        """What Claude Code's own sign-in leaves in a throwaway config folder, with the end stamp chosen."""
        self.n += 1
        d = os.path.join(self.rig.data, 'logins', 'f%d' % self.n)
        os.makedirs(d, mode=0o700)
        text = self.server.add(uuid, email_of(uuid), org_of(uuid))
        acct = self.server.accounts[uuid]
        acct['lineages'][acct['latest']]['rx_ms'] = int(rx_ms)
        blob = json.loads(text)
        blob['claudeAiOauth']['refreshTokenExpiresAt'] = int(rx_ms)
        text = json.dumps(blob, separators=(',', ':'))
        self.rig.kc.put(facts.keychain_service({'CLAUDE_CONFIG_DIR': d}), self.rig.account, text)
        with open(os.path.join(d, '.claude.json'), 'w') as f:
            json.dump({'oauthAccount': identity_block(uuid, email_of(uuid), org_of(uuid))}, f)
        return d, text


class AddCurrent(Case):
    def test_a_login_just_under_a_kept_accounts_stamp_is_kept_as_the_account_anthropic_names(self):
        a = keep_at(self.rig, 'uuid-a', self.R)
        b = sign_in_at(self.rig, 'uuid-b', self.R - 20000)              # the settings name B
        acc = roster.add_current(self.ctx)
        self.assertEqual(acc.id, 'uuid-b')
        self.assertEqual(self.rig.vault('uuid-a'), a)
        self.assertEqual(self.rig.vault('uuid-b'), b)
        self.assertEqual(self.accounts(), ['uuid-a', 'uuid-b'])

    def test_with_no_answer_from_anthropic_the_label_names_a_new_account(self):
        a = keep_at(self.rig, 'uuid-a', self.R)
        b = sign_in_at(self.rig, 'uuid-b', self.R - 20000)
        self.no_answer()
        acc = roster.add_current(self.ctx)
        self.assertEqual(acc.id, 'uuid-b')
        self.assertEqual(self.rig.vault('uuid-a'), a)
        self.assertEqual(self.rig.vault('uuid-b'), b)

    def test_a_renewal_of_the_kept_account_is_still_its_own(self):
        keep_at(self.rig, 'uuid-a', self.R)
        live = json.loads(self.rig.live_text())
        renewed = self.server.refresh_for(self.rig.live_text())
        blob = json.loads(renewed)
        blob['claudeAiOauth']['refreshTokenExpiresAt'] = live['claudeAiOauth']['refreshTokenExpiresAt'] - 400
        renewed = json.dumps(blob, separators=(',', ':'))
        self.rig.set_live(renewed)
        acc = roster.add_current(self.ctx)
        self.assertEqual(acc.id, 'uuid-a')
        self.assertEqual(self.rig.vault('uuid-a'), renewed)
        self.assertEqual(self.accounts(), ['uuid-a'])


if __name__ == '__main__':
    unittest.main()
