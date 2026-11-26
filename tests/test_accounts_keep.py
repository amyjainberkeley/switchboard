"""Keeping logins: a new /login becomes an
account only with proof, a rotation by Claude Code brings the saved copy up to date, and what was removed on purpose
stays out."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import os
import threading
import time
import unittest

from fakes_accounts import Rig, identity_block, login_text
from switchboard.accounts import capture, facts, net, roster, vault


class Clock:
    def __init__(self):
        self.jump = 0.0

    def mono(self):
        return time.monotonic() + self.jump


class KeepCase(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.rig = Rig(mono=self.clock.mono)
        self.ctx = self.rig.ctx
        self.a_text, self.b_text = self.rig.two_accounts()          # Claude Code is on B
        capture.reset()

    def tearDown(self):
        self.rig.close()

    def keep(self):
        return capture.keep_pass(self.ctx, force=True)


class Rotation(KeepCase):
    def test_a_rotation_by_claude_code_is_saved_over_the_older_copy(self):
        rotated = self.rig.claude_renews()
        self.assertEqual(self.keep(), 'Saved the newer login Claude Code made for b@example.com')
        self.assertEqual(self.rig.vault('uuid-b'), rotated)
        self.assertEqual(json.loads(self.rig.vault('uuid-b', 'Switchboard login (previous)'))['generations'][0]['text'],
                         self.b_text)
        self.assertIsNone(self.keep())                                 # nothing new: nothing written
        self.assertEqual(self.rig.vault('uuid-b'), rotated)

    def test_the_keychain_is_read_only_on_a_change_signal_or_every_three_minutes(self):
        capture.keep_pass(self.ctx)
        reads = self.rig.kc.count('read', self.rig.service)
        for _ in range(5):
            capture.keep_pass(self.ctx)
        self.assertEqual(self.rig.kc.count('read', self.rig.service), reads)
        self.rig.set_config(identity_block('uuid-b', 'b@example.com', 'org-b', name='Renamed'))
        capture.keep_pass(self.ctx)                                    # the config changed: one more look
        self.assertEqual(self.rig.kc.count('read', self.rig.service), reads + 1)
        self.clock.jump += 181
        capture.keep_pass(self.ctx)
        self.assertEqual(self.rig.kc.count('read', self.rig.service), reads + 2)


class NewLogins(KeepCase):
    def test_a_new_sign_in_becomes_an_account_with_proof(self):
        c = self.rig.sign_in('uuid-c', 'c@example.com', 'org-c')
        self.assertEqual(self.keep(), 'Kept c@example.com · 3 accounts')
        doc = self.rig.doc()
        self.assertEqual(doc['accounts']['uuid-c']['proof']['kind'], 'profile')
        self.assertEqual(doc['accounts']['uuid-c']['identity']['emailAddress'], 'c@example.com')
        self.assertEqual(self.rig.vault('uuid-c'), c)
        self.assertEqual(self.rig.events[-1][2]['h'], 'kept')

    def test_one_email_in_two_organizations_is_kept_as_two_accounts(self):
        self.rig.sign_in('uuid-c', 'c@example.com', 'org-personal')
        self.keep()
        self.rig.sign_in('uuid-c', 'c@example.com', 'org-team', name='Team')
        self.keep()
        doc = self.rig.doc()
        self.assertIn('uuid-c', doc['accounts'])
        self.assertIn('uuid-c/org-team', doc['accounts'])
        self.assertEqual(doc['accounts']['uuid-c/org-team']['org_id'], 'org-team')
        with self.assertRaises(Exception) as e:
            roster.find_id(self.ctx, 'c@example.com')
        self.assertEqual(getattr(e.exception, 'kind', None), 'ambiguous')

    def test_a_login_in_another_config_folder_is_never_kept_by_the_keeper(self):
        folder = os.path.join(self.rig.root, 'throwaway')
        os.makedirs(folder)
        env = {'CLAUDE_CONFIG_DIR': folder, 'USER': 'tester'}
        self.rig.sec.put(facts.keychain_service(env), 'tester', self.rig.server.add('uuid-t', 't@example.com', 'o'))
        with open(os.path.join(folder, '.claude.json'), 'w') as f:
            json.dump({'oauthAccount': identity_block('uuid-t', 't@example.com', 'o')}, f)
        self.keep()
        self.assertNotIn('uuid-t', self.rig.doc()['accounts'])
        run = os.path.join(self.rig.data, 'run', 'run-x')            # nor from inside one of our own run folders
        os.makedirs(run)
        self.rig.env['CLAUDE_CONFIG_DIR'] = run
        self.rig.sign_in('uuid-c', 'c@example.com', 'org-c')
        self.assertIsNone(self.keep())
        self.assertNotIn('uuid-c', self.rig.doc()['accounts'])

    def test_a_keep_that_fails_on_a_keychain_timeout_retries_in_a_minute_and_says_so_once(self):
        self.rig.sign_in('uuid-c', 'c@example.com', 'org-c')
        self.rig.kc.rule('read', service=self.rig.service, why='timeout', times=-1)
        note = capture.keep_pass(self.ctx)
        self.assertIn('trying again in a minute', note)
        reads = self.rig.kc.count('read', self.rig.service)
        self.assertIsNone(capture.keep_pass(self.ctx))                  # resting: no Keychain call at all
        self.assertEqual(self.rig.kc.count('read', self.rig.service), reads)
        self.clock.jump += 61
        self.assertIsNone(capture.keep_pass(self.ctx))                  # still failing: said only once
        self.rig.kc.rules.clear()
        self.clock.jump += 61
        self.assertEqual(capture.keep_pass(self.ctx), 'Kept c@example.com · 3 accounts')

    def test_a_keep_racing_the_pages_add_keeps_one_account(self):
        self.rig.sign_in('uuid-c', 'c@example.com', 'org-c')
        errors = []

        def add():
            try:
                roster.add_current(self.ctx)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=add), threading.Thread(target=self.keep)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        doc = self.rig.doc()
        self.assertEqual(sorted(doc['accounts']), ['uuid-a', 'uuid-b', 'uuid-c'])
        self.assertEqual(doc['order'].count('uuid-c'), 1)
        self.assertEqual([e for e in errors if getattr(e, 'kind', '') != 'other'], [])

    def test_an_account_removed_on_purpose_stays_out_until_it_is_added_again(self):
        roster.remove(self.ctx, 'a@example.com')
        self.rig.keep('uuid-c', 'c@example.com', 'org-c')
        self.rig.sign_in('uuid-a', 'a@example.com', 'org-a')
        self.assertIsNone(self.keep())
        self.assertNotIn('uuid-a', self.rig.doc()['accounts'])
        roster.add_current(self.ctx)                                    # the person adds it: it is back
        self.assertIn('uuid-a', self.rig.doc()['accounts'])

    def test_bytes_read_only_from_a_fallback_copy_are_never_kept(self):
        c = self.rig.server.add('uuid-c', 'c@example.com', 'org-c')
        with open(facts.credentials_file(self.rig.env, self.rig.home), 'w') as f:
            f.write(c)
        self.rig.set_config(identity_block('uuid-c', 'c@example.com', 'org-c'))
        self.rig.kc.rule('read', service=self.rig.service, why='locked', times=-1)
        self.keep()
        self.assertNotIn('uuid-c', self.rig.doc()['accounts'])

    def test_a_label_naming_a_saved_account_with_another_login_waits_for_proof(self):
        stranger = login_text('sk-ant-oat01-z', 'sk-ant-ort01-z', int((time.time() + 3600) * 1000))
        self.rig.set_live(stranger)
        self.rig.set_config(identity_block('uuid-a', 'a@example.com', 'org-a'))
        self.rig.server.script.append(('profile', net.NetError('timeout', False)))
        self.assertIn("can't match", self.keep())
        self.assertEqual(self.rig.vault('uuid-a'), self.a_text)
        self.assertEqual(sorted(self.rig.doc()['accounts']), ['uuid-a', 'uuid-b'])

    def test_a_label_naming_an_account_not_saved_yet_is_kept_when_anthropic_cannot_be_asked(self):
        self.rig.sign_in('uuid-c', 'c@example.com', 'org-c')
        self.rig.server.script.append(('profile', net.NetError('dns', False)))
        self.keep()
        doc = self.rig.doc()
        self.assertEqual(doc['accounts']['uuid-c']['proof']['kind'], 'label')
        capture.reset()
        self.rig.server.script.append(('profile', (200, {'account': {'uuid': 'uuid-z', 'email': 'z@x.example'},
                                                          'organization': {'uuid': 'org-z'}})))
        note = self.keep()                                             # checked later: label and login disagree
        self.assertIn('disagree', note)
        self.assertIn('conflict', self.rig.doc()['accounts']['uuid-c'])

    def test_a_fresh_sign_in_of_an_account_marked_needs_sign_in_replaces_it(self):
        with vault.changing(self.ctx) as doc:
            a = doc['accounts']['uuid-a']
            a['strike'] = {'fp': a['fp'], 'at': time.time(), 'why': 'invalid_grant'}
        fresh = self.rig.sign_in('uuid-a', 'a@example.com', 'org-a')
        self.assertEqual(self.keep(), 'Kept the new sign-in of a@example.com')
        doc = self.rig.doc()
        self.assertEqual(self.rig.vault('uuid-a'), fresh)
        self.assertNotIn('strike', doc['accounts']['uuid-a'])
        self.assertIn(vault.status_of(self.ctx, doc, 'uuid-a'), ('ok', 'expiring'))

    def test_adding_one_account_never_touches_anothers_saved_login(self):
        for i, letter in enumerate('cdef'):
            self.rig.keep('uuid-' + letter, letter + '@example.com', 'org-' + letter)
            self.assertEqual(self.rig.vault('uuid-a'), self.a_text)
            self.assertTrue(self.rig.holds_valid('uuid-a'))
            self.assertTrue(self.rig.holds_valid('uuid-b'))

    def test_claude_code_moving_to_another_saved_account_by_itself_is_said_as_its_switch(self):
        self.keep()
        self.rig.set_live(self.a_text)
        self.rig.set_config(identity_block('uuid-a', 'a@example.com', 'org-a'))
        self.keep()
        said = [e for e in self.rig.events if e[0] == 'Claude Code is now on a@example.com']
        self.assertEqual(len(said), 1)
        self.assertEqual(said[0][2]['by'], 'claude-code')

    def test_the_keeper_reads_the_config_file_claude_code_reads(self):
        legacy = os.path.join(self.rig.home, '.claude', '.config.json')
        self.rig.set_live(self.rig.server.add('uuid-c', 'c@example.com', 'org-c'))
        with open(legacy, 'w') as f:                                    # Claude Code reads this one
            json.dump({'oauthAccount': identity_block('uuid-c', 'c@example.com', 'org-c')}, f)
        self.rig.server.script.append(('profile', net.NetError('dns', False)))
        self.keep()
        self.assertIn('uuid-c', self.rig.doc()['accounts'])
        self.assertEqual(self.rig.vault('uuid-a'), self.a_text)


if __name__ == '__main__':
    unittest.main()
