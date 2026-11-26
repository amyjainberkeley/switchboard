"""api.add_login(config_dir), the page's Add account. Claude Code's own sign-in ran with
CLAUDE_CONFIG_DIR=<a throwaway folder>, so its login sits in that folder's own Keychain item (or its .credentials.json)
and its label in that folder's .claude.json. The engine keeps it with Anthropic's proof of the owner, like add_current
(a new account, or a newer generation of a kept one; never older over newer), removes the folder's item once the kept
copy has read back, and touches the live login only when it is this account's and it is gone, emptied or refused, or
an earlier sign-in of it (then the new one goes live through the switch)."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import os
import time
import unittest

from fakes_accounts import Rig, identity_block
from switchboard.accounts import api, context, facts, live, net, roster, switcher, tokens, vault
from switchboard.accounts.api import ABSENT, AccountsError


class AddLoginCase(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        self.a_text, self.b_text = self.rig.two_accounts()          # Claude Code is on B; A is idle
        self.n = 0

    def tearDown(self):
        self.use.__exit__(None, None, None)
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        self.rig.close()

    def sign_in_folder(self, uuid, email, org='org-x', where='keychain', label=None, text=None):
        """What `claude auth login` leaves in a throwaway CLAUDE_CONFIG_DIR: its own login and its label."""
        self.n += 1
        d = os.path.join(self.rig.data, 'logins', 'f%d' % self.n)
        os.makedirs(d, mode=0o700)
        text = text or self.rig.server.add(uuid, email, org)
        env = {'CLAUDE_CONFIG_DIR': d}
        svc = facts.keychain_service(env)
        if where == 'keychain':
            self.rig.kc.put(svc, self.rig.account, text)
        elif where == 'file':
            with open(os.path.join(d, '.credentials.json'), 'w') as f:
                f.write(text)
        blk = identity_block(uuid, email, org) if label is None else label
        if blk:
            with open(os.path.join(d, '.claude.json'), 'w') as f:
                json.dump({'oauthAccount': blk, 'hasCompletedOnboarding': True}, f)
        return d, svc, text

    def fp(self, text):
        return live.parse(text).fingerprint

    def live_oauth(self):
        return json.loads(self.rig.live_text())['claudeAiOauth']


class ANewAccount(AddLoginCase):
    def test_is_kept_with_anthropics_proof_and_the_folders_item_goes(self):
        d, svc, text = self.sign_in_folder('uuid-c', 'c@example.com', 'org-c')
        acc = api.add_login(d)
        self.assertEqual((acc.id, acc.email, acc.org_id, acc.status, acc.order), ('uuid-c', 'c@example.com', 'org-c',
                                                                                 'ok', 3))
        self.assertEqual(self.rig.vault('uuid-c'), text)
        self.assertEqual(self.rig.doc()['accounts']['uuid-c']['proof']['kind'], 'profile')
        self.assertIsNone(self.rig.kc.get(svc, self.rig.account))                 # the throwaway item is gone
        self.assertEqual(self.rig.live_text(), self.b_text)                         # the live login never touched
        self.assertEqual(self.rig.config()['oauthAccount']['accountUuid'], 'uuid-b')
        self.assertEqual([e for e in self.rig.events if 'Switched' in e[0]], [])

    def test_the_login_may_come_from_the_folders_credentials_file(self):
        d, svc, text = self.sign_in_folder('uuid-c', 'c@example.com', 'org-c', where='file')
        self.assertEqual(api.add_login(d).id, 'uuid-c')
        self.assertEqual(self.rig.vault('uuid-c'), text)

    def test_the_owner_is_asked_of_anthropic_with_no_lock_held(self):
        held = []
        request = self.rig.server.request

        def watch(method, url, *a, **kw):
            if url == facts.PROFILE_URL:
                held.append((vault.holds('switch_lock'), vault.holds('account_lock')))
            return request(method, url, *a, **kw)
        self.rig.server.request = watch
        d, _, _ = self.sign_in_folder('uuid-c', 'c@example.com', 'org-c')
        api.add_login(d)
        self.assertEqual(held, [(False, False)])

    def test_a_label_that_names_someone_else_than_anthropic_does_is_refused(self):
        d, svc, _ = self.sign_in_folder('uuid-c', 'c@example.com', 'org-c',
                                        label=identity_block('uuid-a', 'a@example.com', 'org-a'))
        before = self.rig.vault('uuid-a')
        with self.assertRaises(AccountsError) as e:
            api.add_login(d)
        self.assertEqual(e.exception.kind, 'disagree')
        self.assertEqual(self.rig.vault('uuid-a'), before)
        self.assertNotIn('uuid-c', self.rig.doc()['accounts'])

    def test_with_no_answer_from_anthropic_the_label_is_enough_only_for_an_account_not_kept_yet(self):
        self.rig.server.script.append(('profile', net.NetError('dns')))
        d, _, text = self.sign_in_folder('uuid-c', 'c@example.com', 'org-c')
        self.assertEqual(api.add_login(d).id, 'uuid-c')
        self.assertEqual(self.rig.doc()['accounts']['uuid-c']['proof']['kind'], 'label')
        self.rig.server.script.append(('profile', net.NetError('dns')))
        before = self.rig.vault('uuid-a')
        d2, _, _ = self.sign_in_folder('uuid-a', 'a@example.com', 'org-a')
        with self.assertRaises(AccountsError) as e:
            api.add_login(d2)
        self.assertEqual(e.exception.kind, 'network')
        self.assertEqual(self.rig.vault('uuid-a'), before)


class AKeptAccount(AddLoginCase):
    def test_gets_its_newer_sign_in_and_the_replaced_one_stays_an_earlier_generation(self):
        d, svc, text = self.sign_in_folder('uuid-a', 'a@example.com', 'org-a')
        acc = api.add_login(d)
        self.assertEqual((acc.id, acc.fingerprint), ('uuid-a', self.fp(text)))
        self.assertEqual(self.rig.vault('uuid-a'), text)
        earlier = json.loads(self.rig.vault('uuid-a', 'Switchboard login (previous)'))['generations']
        self.assertEqual(earlier[0]['text'], self.a_text)
        self.assertIsNone(self.rig.kc.get(svc, self.rig.account))
        self.assertEqual(len(api.accounts()), 2)

    def test_never_gets_an_older_sign_in_over_a_newer_one(self):
        stored_rx = live.parse(self.a_text).refresh_expires_ms
        newer = json.loads(self.rig.server.add('uuid-a', 'a@example.com', 'org-a'))
        newer['claudeAiOauth']['refreshTokenExpiresAt'] = stored_rx - 86400 * 1000      # signed in before
        d, svc, _ = self.sign_in_folder('uuid-a', 'a@example.com', 'org-a',
                                        text=json.dumps(newer, separators=(',', ':')))
        api.add_login(d)
        self.assertEqual(self.rig.vault('uuid-a'), self.a_text)
        self.assertIsNone(self.rig.kc.get(svc, self.rig.account))                 # not needed: a newer one is kept

    def test_the_live_account_gets_the_new_sign_in_saved_and_live_as_the_same_account(self):
        d, _, text = self.sign_in_folder('uuid-b', 'b@example.com', 'org-b')
        api.add_login(d)
        self.assertEqual(self.rig.vault('uuid-b'), text)
        # Claude Code's own login is an earlier sign-in of B: the newer one goes live through the switch
        # (tests/test_accounts_newer_sign_in_live.py), said as B's new sign-in, never as a switch
        self.assertEqual(self.live_oauth()['refreshToken'], json.loads(text)['claudeAiOauth']['refreshToken'])
        self.assertEqual([e for e in self.rig.events if 'Switched' in e[0]], [])
        self.assertIn('Claude Code is on the new sign-in of b@example.com', [e[0] for e in self.rig.events])


class TheLiveLogin(AddLoginCase):
    def wipe_live(self):
        blob = json.loads(self.rig.live_text())
        blob['claudeAiOauth'].update({'accessToken': '', 'refreshToken': ''})
        self.rig.set_live(json.dumps(blob, separators=(',', ':')))
        roster.forget_live()

    def assert_live_is(self, text):
        self.assertEqual(self.live_oauth()['refreshToken'], json.loads(text)['claudeAiOauth']['refreshToken'])
        self.assertEqual(self.rig.config()['oauthAccount']['accountUuid'], 'uuid-b')
        self.assertEqual(switcher.read_journal(self.ctx)[0].kind, ABSENT)
        self.assertEqual(api.live().account_id, 'uuid-b')

    def test_an_emptied_login_of_that_account_gets_the_new_sign_in_through_the_switch(self):
        self.wipe_live()
        d, _, text = self.sign_in_folder('uuid-b', 'b@example.com', 'org-b')
        api.add_login(d)
        self.assert_live_is(text)
        self.assertEqual(self.rig.vault('uuid-b'), text)
        self.assertIn('Claude Code is on the new sign-in of b@example.com', [e[0] for e in self.rig.events])

    def test_a_login_gone_while_the_settings_name_that_account_gets_the_new_sign_in(self):
        self.rig.kc.drop(self.rig.service, self.rig.account)
        roster.forget_live()
        d, _, text = self.sign_in_folder('uuid-b', 'b@example.com', 'org-b')
        api.add_login(d)
        self.assert_live_is(text)

    def test_a_login_anthropic_refused_gets_the_new_sign_in(self):
        with vault.changing(self.ctx) as doc:
            doc['accounts']['uuid-b']['strike'] = {'fp': self.fp(self.b_text), 'at': time.time(),
                                                   'why': 'invalid_grant'}
        d, _, text = self.sign_in_folder('uuid-b', 'b@example.com', 'org-b')
        api.add_login(d)
        self.assert_live_is(text)

    def test_an_emptied_login_of_an_account_not_kept_yet_gets_its_new_sign_in_too(self):
        text_c = self.rig.server.add('uuid-c', 'c@example.com', 'org-c')     # Claude Code on C, never kept here
        blob = json.loads(text_c)
        blob['claudeAiOauth'].update({'accessToken': '', 'refreshToken': ''})
        self.rig.set_live(json.dumps(blob, separators=(',', ':')))
        self.rig.set_config(identity_block('uuid-c', 'c@example.com', 'org-c'))
        roster.forget_live()
        d, _, text = self.sign_in_folder('uuid-c', 'c@example.com', 'org-c')
        self.assertEqual(api.add_login(d).id, 'uuid-c')
        self.assertEqual(self.live_oauth()['refreshToken'], json.loads(text)['claudeAiOauth']['refreshToken'])
        self.assertEqual(api.live().account_id, 'uuid-c')

    def test_an_emptied_login_of_another_account_is_left_as_it_is(self):
        self.wipe_live()
        wiped = self.rig.live_text()
        d, _, _ = self.sign_in_folder('uuid-c', 'c@example.com', 'org-c')
        api.add_login(d)
        self.assertEqual(self.rig.live_text(), wiped)
        self.assertEqual([e for e in self.rig.events if 'Switched' in e[0]], [])

    def test_when_the_switch_cant_go_the_account_is_still_kept_and_that_is_said(self):
        self.wipe_live()
        wiped = self.rig.live_text()
        self.rig.kc.rule('write', service=self.rig.service, why='locked', times=1)
        d, _, text = self.sign_in_folder('uuid-b', 'b@example.com', 'org-b')
        acc = api.add_login(d)
        self.assertEqual(acc.id, 'uuid-b')
        self.assertEqual(self.rig.vault('uuid-b'), text)
        self.assertEqual(self.rig.live_text(), wiped)
        self.assertTrue(any(e[0].startswith('Kept the new sign-in of b@example.com; Claude Code is not on it yet')
                            for e in self.rig.events), self.rig.events)


class Refusals(AddLoginCase):
    def test_a_folder_with_no_login_or_an_incomplete_one_is_refused_plainly(self):
        d, _, _ = self.sign_in_folder('uuid-c', 'c@example.com', 'org-c', where=None)
        with self.assertRaises(AccountsError) as e:
            api.add_login(d)
        self.assertEqual(e.exception.kind, 'unreadable_live')
        blob = json.loads(self.rig.server.add('uuid-c', 'c@example.com', 'org-c'))
        blob['claudeAiOauth']['refreshToken'] = ''
        d2, _, _ = self.sign_in_folder('uuid-c', 'c@example.com', 'org-c', text=json.dumps(blob))
        with self.assertRaises(AccountsError) as e:
            api.add_login(d2)
        self.assertEqual(e.exception.kind, 'unreadable_live')
        with self.assertRaises(AccountsError) as e:
            api.add_login(os.path.join(self.rig.data, 'logins', 'gone'))
        self.assertEqual(e.exception.kind, 'unreadable_live')
        self.assertNotIn('uuid-c', self.rig.doc()['accounts'])

    def test_claude_codes_own_folder_and_a_run_folder_are_never_taken_for_a_sign_in_folder(self):
        for folder in (os.path.join(self.rig.home, '.claude'), self.rig.home, 'relative/folder'):
            with self.assertRaises(AccountsError) as e:
                api.add_login(folder)
            self.assertEqual(e.exception.kind, 'other', folder)
        run = os.path.join(self.rig.data, 'run', 'run-x')
        os.makedirs(run)
        with self.assertRaises(AccountsError):
            api.add_login(run)
        self.assertEqual(self.rig.live_text(), self.b_text)                         # never removed or read as one

    def test_the_folder_claude_code_runs_in_is_never_taken_for_one(self):
        other = Rig(extra_env={'CLAUDE_CONFIG_DIR': os.path.join(self.rig.root, 'cc')})
        try:
            os.makedirs(other.env['CLAUDE_CONFIG_DIR'])
            with self.assertRaises(AccountsError) as e:
                roster.add_login(other.ctx, other.env['CLAUDE_CONFIG_DIR'])
            self.assertEqual(e.exception.kind, 'other')
        finally:
            other.close()

    def test_an_account_list_that_turns_unreadable_meanwhile_is_a_plain_refusal(self):
        d, svc, text = self.sign_in_folder('uuid-c', 'c@example.com', 'org-c')
        real_load, calls = vault.load, []

        def load(ctx):
            calls.append(1)
            if len(calls) > 1:
                raise vault.VaultError('unreadable', "Switchboard's account list can't be read; nothing was changed.")
            return real_load(ctx)
        vault.load = load
        try:
            with self.assertRaises(AccountsError) as e:
                api.add_login(d)
        finally:
            vault.load = real_load
        self.assertIn("account list can't be read", str(e.exception))
        self.assertEqual(self.rig.kc.get(svc, self.rig.account), text)

    def test_the_folders_item_stays_until_the_kept_copy_has_read_back(self):
        self.rig.kc.rule('write', service='Switchboard login', why='timeout', times=1)
        d, svc, text = self.sign_in_folder('uuid-c', 'c@example.com', 'org-c')
        with self.assertRaises(AccountsError):
            api.add_login(d)
        self.assertEqual(self.rig.kc.get(svc, self.rig.account), text)           # still there: nothing lost
        self.assertNotIn('uuid-c', self.rig.doc()['accounts'])


if __name__ == '__main__':
    unittest.main()
