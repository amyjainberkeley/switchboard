"""`switchboard current` and `status --json` on a login with no refresh token that no record owns.

Claude Code is on a one-year token whose record is gone, so no account of Switchboard's is that login, while Claude
Code's settings still name a@example.com. The page says Claude Code is on none of the accounts, and a@example.com's
own row says it is not in use. `status --json` must say the same at the top (`in_use` null), and `current` must not
print a@example.com as the account in use: it says in one sentence that Switchboard does not keep that login and
exits 1. Another token is still kept, so `current` reads Claude Code's login item, as `status --json` does. The
engine is the real one on a made-up Mac (fakes_accounts.Rig); the page is not running. Made-up tokens only."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import unittest
from unittest import mock

from switchboard import engine
from switchboard.accounts import bearer, roster, vault
from test_cli_tokens import TOKEN, Case

OWN = TOKEN.replace('0101', '0303')
NOT_KEPT = "Claude Code is on a login with no refresh token, which is no saved account's.\n"


class LoginNobodyKeeps(Case):
    def setUp(self):
        super().setUp()
        patch = mock.patch.object(engine, 'claude_json', self.rig.config_path)    # the made-up Mac's settings
        patch.start()
        self.addCleanup(patch.stop)
        self.run_cli('add', '--token', '-', '--for', 'a@example.com', '--as', 'a-tok', stdin=TOKEN)
        self.run_cli('add', '--token', '-', '--label', 'spare', stdin=OWN)
        code, _, err = self.run_cli('switch', 'a-tok')
        self.assertEqual(code, 0, err)
        with vault.account_lock(self.rig.ctx, timeout=10.0):          # the token's record is gone; the spare stays
            doc = vault.load(self.rig.ctx)
            doc['bearers'] = dict((k, v) for k, v in bearer.records(doc).items() if v.get('alias') != 'a-tok')
            vault.save(self.rig.ctx, doc)
        roster.forget_live()
        self.assertEqual(engine.identity()[0], 'a@example.com')     # Claude Code's settings still name it

    def test_status_json_names_no_account_in_use(self):
        doc = json.loads(self.run_cli('status', '--json')[1])
        self.assertEqual([(a['email'], a['in_use']) for a in doc['accounts'] if a['email'] == 'a@example.com'],
                         [('a@example.com', False)])
        self.assertIsNone(doc['in_use'])
        self.assertEqual([a for a in doc['accounts'] if a['in_use']], [])

    def test_current_says_switchboard_does_not_keep_it_and_exits_1(self):
        code, out, err = self.run_cli('current')
        self.assertEqual((code, out, err), (1, '', NOT_KEPT))
        code, out, _ = self.run_cli('current', '--json')
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out), {'schema': 1, 'email': None, 'signed_in': True, 'in_switchboard': False,
                                           'number': None, 'short_name': None, 'kind': None, 'label': None})

    def test_back_on_a_sign_in_it_is_as_before(self):
        code, _, err = self.run_cli('switch', 'b@example.com')
        self.assertEqual(code, 0, err)
        code, out, err = self.run_cli('current')
        self.assertEqual((code, out, err), (0, 'b@example.com\n', ''))
        self.assertEqual(json.loads(self.run_cli('status', '--json')[1])['in_use'], 'b@example.com')

    def test_a_sign_in_not_in_switchboard_still_prints_its_email(self):
        self.rig.sign_in('uuid-c', 'c@example.com', 'org-c')        # a full sign-in, with a refresh token
        roster.forget_live()
        code, out, err = self.run_cli('current')
        self.assertEqual((code, out, err), (1, 'c@example.com\n',
                                            'c@example.com is not in Switchboard yet. Add it with: switchboard add\n'))
        code, out, _ = self.run_cli('current', '--json')
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out), {'schema': 1, 'email': 'c@example.com', 'signed_in': True,
                                           'in_switchboard': False, 'number': None, 'short_name': None,
                                           'kind': 'sign_in', 'label': None})
        self.assertEqual(json.loads(self.run_cli('status', '--json')[1])['in_use'], 'c@example.com')

    def test_no_part_of_a_token_is_printed(self):
        for argv in (('current',), ('current', '--json'), ('status', '--json')):
            _, out, err = self.run_cli(*argv)
            self.assert_secret_nowhere(out, err)
            for t in (TOKEN, OWN):
                self.assertNotIn(t[12:], out + err)


if __name__ == '__main__':
    unittest.main()
