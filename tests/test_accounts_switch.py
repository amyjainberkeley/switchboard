"""The switch transaction: Claude Code's login moves to another
account, the outgoing login is kept only for its proven owner, and no login is ever lost."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import os
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

from fakes_accounts import FileKeychain, Rig, identity_block, login_text
from switchboard.accounts import facts, live, switcher, tokens, vault
from switchboard.accounts.api import AccountsError

HERE = os.path.dirname(os.path.abspath(__file__))


class SwitchCase(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.ctx = self.rig.ctx
        self.a_text, self.b_text = self.rig.two_accounts()          # Claude Code is on B
        self.server = self.rig.server

    def tearDown(self):
        switcher._HOOK = None
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        self.rig.close()

    def label(self):
        return (self.rig.config().get('oauthAccount') or {}).get('accountUuid')

    def live_writes(self):
        return self.rig.kc.count('write', self.rig.service)

    def assert_nothing_lost(self, *uuids):
        for uuid in uuids or ('uuid-a', 'uuid-b'):
            self.assertTrue(self.rig.holds_valid(uuid), uuid)


class TheSwitch(SwitchCase):
    def test_a_switch_moves_claude_code_to_the_target_and_keeps_the_outgoing_login(self):
        res = switcher.switch(self.ctx, 'uuid-a', kind='manual', source='page')
        self.assertTrue(res.ok)
        self.assertEqual((res.from_id, res.to_id, res.rolled_back), ('uuid-b', 'uuid-a', False))
        self.assertEqual(live.parse(self.rig.live_text()).fingerprint, live.parse(self.a_text).fingerprint)
        self.assertEqual(self.label(), 'uuid-a')
        self.assertEqual(self.rig.vault('uuid-b'), self.b_text)
        self.assertFalse(os.path.exists(switcher.journal_path(self.ctx)))
        self.assertEqual(vault.stashed(self.ctx), [])               # the outgoing login is saved: no safety copy left
        self.assertEqual(self.rig.events[-1][0], 'Switched Claude Code to a@example.com')
        self.assertEqual(self.rig.events[-1][2]['by'], 'you')
        self.assertEqual(self.rig.doc()['switches'][-1]['to'], 'uuid-a')
        self.assert_nothing_lost()

    def test_machine_wide_siblings_stay_as_the_live_item_has_them_and_account_parts_travel(self):
        blob = json.loads(self.rig.vault('uuid-a'))
        blob.update({'mcpOAuth': {'srv': 'stale-v1'}, 'mcpXaaIdp': {'idp': 'old'}, 'trustedDeviceToken': 'tdt-a',
                     'organizationUuid': 'org-a', 'someNewKey': 'x-a'})
        self.rig.sec.put('Switchboard login', 'uuid-a', json.dumps(blob))
        live_blob = json.loads(self.rig.live_text())
        live_blob.update({'mcpOAuth': {'srv': 'live-v2'}, 'designOauth': {'grant': 'live'},
                          'pluginSecrets': {'p': 1}, 'trustedDeviceToken': 'tdt-b'})
        self.rig.set_live(json.dumps(live_blob))
        switcher.switch(self.ctx, 'uuid-a')
        now = json.loads(self.rig.live_text())
        self.assertEqual(now['mcpOAuth'], {'srv': 'live-v2'})            # the Mac's, as it is now
        self.assertEqual(now['designOauth'], {'grant': 'live'})
        self.assertEqual(now['pluginSecrets'], {'p': 1})
        self.assertNotIn('mcpXaaIdp', now)                             # not brought back from a saved copy
        self.assertEqual(now['trustedDeviceToken'], 'tdt-a')           # the account's own parts travel with it
        self.assertEqual(now['someNewKey'], 'x-a')
        self.assertEqual(now['claudeAiOauth'], blob['claudeAiOauth'])

    def test_the_identity_block_changes_with_the_login_and_the_rest_of_claude_codes_config_stays(self):
        cfg = self.rig.config()
        cfg.update({'projects': {'/p': {'allowedTools': ['Read']}}, 'mcpServers': {'m': {'command': 'x'}}})
        with open(self.rig.config_path(), 'w') as f:
            json.dump(cfg, f)
        switcher.switch(self.ctx, 'uuid-a')
        after = self.rig.config()
        self.assertEqual(after['oauthAccount']['emailAddress'], 'a@example.com')
        self.assertEqual(after['projects'], {'/p': {'allowedTools': ['Read']}})
        self.assertEqual(after['mcpServers'], {'m': {'command': 'x'}})

    def test_claude_codes_plaintext_login_file_gets_the_new_login_so_a_running_session_notices(self):
        path = facts.credentials_file(self.rig.env, self.rig.home)
        with open(path, 'w') as f:
            f.write(self.rig.live_text())
        os.utime(path, (time.time() - 600, time.time() - 600))
        switcher.switch(self.ctx, 'uuid-a')
        with open(path) as f:
            self.assertEqual(f.read(), self.rig.live_text())
        self.assertGreater(os.stat(path).st_mtime, time.time() - 60)


class NothingWrittenWhenUnsure(SwitchCase):
    def snapshot(self):
        return (self.rig.live_text(), json.dumps(self.rig.config(), sort_keys=True), self.rig.vault('uuid-a'),
                self.rig.vault('uuid-b'), os.path.exists(switcher.journal_path(self.ctx)))

    def test_an_unreadable_live_login_stops_the_switch_with_nothing_written(self):
        before = self.snapshot()
        for why, kind in (('timeout', 'keychain_slow'), ('locked', 'keychain_locked'), ('denied', 'keychain_denied'),
                          ('busy', 'keychain_slow'), ('no_keychain', 'keychain_locked')):
            self.rig.kc.rule('read', service=self.rig.service, why=why, times=2)
            with self.assertRaises(AccountsError) as e:
                switcher.switch(self.ctx, 'uuid-a')
            self.assertEqual(e.exception.kind, kind, why)
            self.assertNotIn('sign in', str(e.exception).lower())       # a failed read never asks for a sign-in
            self.rig.kc.rules.clear()
        self.assertEqual(self.snapshot(), before)

    def test_a_live_login_readable_only_from_the_fallback_file_stops_the_switch(self):
        with open(facts.credentials_file(self.rig.env, self.rig.home), 'w') as f:
            f.write(self.b_text)
        self.rig.kc.rule('read', service=self.rig.service, why='locked', times=-1)
        with self.assertRaises(AccountsError) as e:
            switcher.switch(self.ctx, 'uuid-a')
        self.assertEqual(e.exception.kind, 'unreadable_live')
        self.assertEqual(self.label(), 'uuid-b')

    def test_an_unparseable_claude_config_stops_the_switch_before_anything_is_written(self):
        with open(self.rig.config_path(), 'w') as f:
            f.write('{"projects": {')
        live_before = self.rig.live_text()
        with self.assertRaises(AccountsError):
            switcher.switch(self.ctx, 'uuid-a')
        self.assertEqual(self.rig.live_text(), live_before)
        with open(self.rig.config_path()) as f:
            self.assertEqual(f.read(), '{"projects": {')

    def test_claude_code_holding_its_login_lock_means_nothing_is_changed(self):
        os.mkdir(facts.refresh_lock(self.rig.env, self.rig.home))
        before = self.snapshot()
        with mock.patch.object(switcher, 'CC_LOCK_BUDGET_S', 1.0):
            with self.assertRaises(AccountsError) as e:
                switcher.switch(self.ctx, 'uuid-a')
        self.assertEqual(e.exception.kind, 'claude_busy')
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(os.path.isdir(facts.refresh_lock(self.rig.env, self.rig.home)))   # never stolen

    def test_the_disk_floor_refuses_a_switch_that_could_not_be_undone(self):
        self.rig.free = 10 * 1024 * 1024
        before = self.snapshot()
        with self.assertRaises(AccountsError) as e:
            switcher.switch(self.ctx, 'uuid-a')
        self.assertEqual(e.exception.kind, 'disk')
        self.assertEqual(self.snapshot(), before)

    def test_a_target_claimed_by_a_run_is_never_switched_to(self):
        with vault.changing(self.ctx) as doc:
            doc['claims']['uuid-a'] = {'pid': os.getpid(), 'start': 'self-start', 'dir': '/x'}
        with self.assertRaises(AccountsError) as e:
            switcher.switch(self.ctx, 'uuid-a')
        self.assertEqual(e.exception.kind, 'claimed')

    def test_a_missing_saved_login_says_so_and_changes_nothing(self):
        self.rig.sec.drop('Switchboard login', 'uuid-a')
        with self.assertRaises(AccountsError) as e:
            switcher.switch(self.ctx, 'uuid-a')
        self.assertEqual(e.exception.kind, 'dead_login')
        self.assertEqual(self.label(), 'uuid-b')
        self.rig.sec.put('Switchboard login', 'uuid-a', self.a_text)
        self.rig.kc.rule('read', service='Switchboard login', account='uuid-a', why='locked', times=-1)
        with self.assertRaises(AccountsError) as e:
            switcher.switch(self.ctx, 'uuid-a')
        self.assertEqual(e.exception.kind, 'keychain_locked')           # unreadable is not missing


class SelfSwitch(SwitchCase):
    def test_switching_to_the_account_already_live_writes_nothing_over_it(self):
        writes = self.live_writes()
        res = switcher.switch(self.ctx, 'uuid-b')
        self.assertTrue(res.ok)
        self.assertIn('Already on', res.reason)
        self.assertEqual(self.live_writes(), writes)
        self.assertEqual(self.rig.live_text(), self.b_text)
        self.assertEqual(self.rig.vault('uuid-b'), self.b_text)

    def test_a_newer_generation_live_is_saved_on_a_self_switch_never_the_other_way(self):
        rotated = self.rig.claude_renews()
        switcher.switch(self.ctx, 'uuid-b')
        self.assertEqual(self.rig.vault('uuid-b'), rotated)
        self.assertEqual(self.rig.live_text(), rotated)


class Ownership(SwitchCase):
    def test_a_relabelled_config_never_writes_one_accounts_login_into_another(self):
        # Claude Desktop rewrote the label to A while Claude Code still holds B's login
        self.rig.set_config(identity_block('uuid-a', 'a@example.com', 'org-a'))
        res = switcher.switch(self.ctx, 'uuid-b')
        self.assertTrue(res.ok)
        self.assertIn('Already on', res.reason)
        self.assertEqual(self.label(), 'uuid-b')                       # the label follows the login
        self.assertEqual(self.rig.vault('uuid-a'), self.a_text)
        # now B's login rotated with its access token expired (the profile answers 401): the stamp proves it is B's
        self.rig.set_config(identity_block('uuid-a', 'a@example.com', 'org-a'))
        rotated = self.rig.claude_renews()
        self.server.script.append(('profile', (401, {})))
        switcher.switch(self.ctx, 'uuid-a')
        self.assertEqual(self.rig.vault('uuid-b'), rotated)
        self.assertEqual(self.rig.vault('uuid-a'), self.a_text)
        self.assertEqual(live.parse(self.rig.live_text()).fingerprint, live.parse(self.a_text).fingerprint)
        self.assert_nothing_lost()

    def test_a_login_nobody_can_prove_stays_in_a_safety_copy_and_no_slot_is_written(self):
        stranger = login_text('sk-ant-oat01-stranger', 'sk-ant-ort01-stranger', int((time.time() - 3600) * 1000))
        self.rig.set_live(stranger)                                    # no stamp, expired: nothing proves whose
        self.rig.set_config(identity_block('uuid-a', 'a@example.com', 'org-a'))
        res = switcher.switch(self.ctx, 'uuid-b')
        self.assertTrue(res.ok)
        self.assertIn('safety copy', res.note)
        self.assertEqual(self.rig.vault('uuid-a'), self.a_text)
        self.assertEqual(self.rig.vault('uuid-b'), self.b_text)
        kept = [e for e in vault.stashed(self.ctx) if e['key'].startswith('safety:')]
        self.assertEqual(len(kept), 1)
        self.assertEqual(vault.read_stash(self.ctx, kept[0]['key']).value, stranger)

    def test_a_long_running_session_writing_its_old_login_back_never_poisons_a_slot(self):
        # after a switch to A, a session still holding B renews B and writes it over the live item
        switcher.switch(self.ctx, 'uuid-a')
        b_now = self.server.refresh_for(self.b_text)
        self.rig.set_live(b_now)                                       # the label still names A
        switcher.switch(self.ctx, 'uuid-a')                            # back to A: B's rotation goes to B's slot
        self.assertEqual(self.rig.vault('uuid-b'), b_now)
        self.assertEqual(live.parse(self.rig.vault('uuid-a')).fingerprint, live.parse(self.a_text).fingerprint)
        self.assertEqual(live.parse(self.rig.live_text()).fingerprint, live.parse(self.a_text).fingerprint)
        self.assert_nothing_lost()

    def test_a_live_item_without_an_account_login_never_replaces_a_saved_one(self):
        self.rig.set_live(json.dumps({'mcpOAuth': {'srv': 'only'}}))
        switcher.switch(self.ctx, 'uuid-a')
        self.assertEqual(self.rig.vault('uuid-b'), self.b_text)
        now = json.loads(self.rig.live_text())
        self.assertEqual(now['mcpOAuth'], {'srv': 'only'})
        self.assertEqual(now['claudeAiOauth']['refreshToken'], json.loads(self.a_text)['claudeAiOauth']['refreshToken'])

    def test_an_emptied_login_is_never_kept_over_a_working_one(self):
        wiped = json.loads(self.b_text)
        wiped['claudeAiOauth'].update({'accessToken': '', 'refreshToken': '', 'expiresAt': 0})
        self.rig.set_live(json.dumps(wiped))
        switcher.switch(self.ctx, 'uuid-a')
        self.assertEqual(self.rig.vault('uuid-b'), self.b_text)

    def test_an_older_copy_never_overwrites_a_newer_saved_login(self):
        # B signed in again elsewhere and that newer login was kept; the live item still has the old one
        newer = self.server.add('uuid-b', 'b@example.com', 'org-b')
        with vault.changing(self.ctx) as doc:
            vault.save_login(self.ctx, 'uuid-b', newer)
            vault.note_login(doc['accounts']['uuid-b'], live.parse(newer), 'profile', time.time())
        switcher.switch(self.ctx, 'uuid-a')
        self.assertEqual(self.rig.vault('uuid-b'), newer)

    def test_a_refused_saved_login_never_wins_over_the_working_one_in_use(self):
        newer = self.server.add('uuid-b', 'b@example.com', 'org-b')    # a later sign-in, then refused
        with vault.changing(self.ctx) as doc:
            vault.save_login(self.ctx, 'uuid-b', newer)
            b = doc['accounts']['uuid-b']
            vault.note_login(b, live.parse(newer), 'profile', time.time())
            b['strike'] = {'fp': live.parse(newer).fingerprint, 'at': time.time(), 'why': 'invalid_grant'}
        switcher.switch(self.ctx, 'uuid-a')
        self.assertEqual(self.rig.vault('uuid-b'), self.b_text)        # the login that works is the one kept
        self.assertNotIn('strike', self.rig.doc()['accounts']['uuid-b'])

    def test_when_the_ages_of_two_logins_of_one_account_cannot_be_told_both_are_kept(self):
        other = json.loads(self.server.add('uuid-b', 'b@example.com', 'org-b'))
        for k in ('expiresAt', 'refreshTokenExpiresAt'):
            other['claudeAiOauth'].pop(k)
        self.rig.set_live(json.dumps(other))                           # Anthropic says it is B's; no stamps
        res = switcher.switch(self.ctx, 'uuid-a')
        self.assertTrue(res.ok)
        self.assertIn('both were kept', res.note)
        self.assertEqual(self.rig.vault('uuid-b'), self.b_text)
        kept = [e for e in vault.stashed(self.ctx) if e['key'].startswith('safety:')]
        self.assertEqual(vault.read_stash(self.ctx, kept[0]['key']).value, json.dumps(other))

    def test_the_direction_of_every_copy_follows_the_generations(self):
        # live newer than the saved copy (same sign-in, later expiry) -> the saved copy follows it first
        rotated = self.rig.claude_renews()
        switcher.switch(self.ctx, 'uuid-a')
        self.assertEqual(self.rig.vault('uuid-b'), rotated)
        # live older than the saved copy (same sign-in, earlier expiry) -> the saved copy stays
        switcher.switch(self.ctx, 'uuid-b')
        older = json.loads(self.rig.live_text())
        older['claudeAiOauth']['expiresAt'] -= 3600 * 1000
        older['claudeAiOauth']['accessToken'] = 'sk-ant-oat01-older'
        self.rig.set_live(json.dumps(older))
        saved = self.rig.vault('uuid-b')
        switcher.switch(self.ctx, 'uuid-a')
        self.assertEqual(self.rig.vault('uuid-b'), saved)

    def test_a_rotation_between_the_first_look_and_the_locked_look_is_identified_again(self):
        reads = [0]
        rotated = []

        def hook(verb, service, account):
            if verb == 'read' and service == self.rig.service:
                reads[0] += 1
                if reads[0] == 2:                                     # Claude Code renews B right before the lock
                    rotated.append(self.rig.claude_renews())

        self.rig.kc.on_call = hook
        switcher.switch(self.ctx, 'uuid-a')
        self.rig.kc.on_call = None
        self.assertEqual(self.rig.vault('uuid-b'), rotated[0])
        self.assert_nothing_lost()

    def test_a_login_from_an_account_not_yet_kept_is_kept_with_proof_before_the_switch(self):
        c_text = self.rig.sign_in('uuid-c', 'c@example.com', 'org-c')  # /login C, not added yet
        switcher.switch(self.ctx, 'uuid-a')
        doc = self.rig.doc()
        self.assertIn('uuid-c', doc['accounts'])
        self.assertEqual(doc['accounts']['uuid-c']['proof']['kind'], 'profile')
        self.assertEqual(self.rig.vault('uuid-c'), c_text)
        self.assert_nothing_lost('uuid-a', 'uuid-b', 'uuid-c')


class Undo(SwitchCase):
    def test_a_mismatch_on_read_back_puts_the_original_login_and_label_back(self):
        state = {'written': False}

        def hook(verb, service, account):
            if service != self.rig.service:
                return
            if verb == 'write':
                state['written'] = True
            elif verb == 'read' and state['written'] and not state.get('meddled'):
                state['meddled'] = True                               # someone wrote over it before the read-back
                self.rig.set_live(self.b_text)

        self.rig.kc.on_call = hook
        res = switcher.switch(self.ctx, 'uuid-a')
        self.rig.kc.on_call = None
        self.assertFalse(res.ok)
        self.assertTrue(res.rolled_back)
        self.assertIn('undone', res.note)
        self.assertEqual(self.rig.live_text(), self.b_text)
        self.assertEqual(self.label(), 'uuid-b')
        self.assertEqual(self.rig.vault('uuid-a'), self.a_text)
        self.assertFalse(os.path.exists(switcher.journal_path(self.ctx)))
        self.assert_nothing_lost()

    def test_a_keychain_write_that_times_out_leaves_a_journal_that_the_next_start_settles(self):
        for lands, word in ((True, 'Repaired the cut-off switch: Claude Code is on a@example.com'),
                            (False, 'Put away the record of a switch that was cut off: Claude Code holds the '
                                    'login it had before, so nothing was changed.')):
            self.rig.kc.rule('write', service=self.rig.service, why='timeout', land=lands)
            res = switcher.switch(self.ctx, 'uuid-a')
            self.assertFalse(res.ok)
            self.assertFalse(res.rolled_back)
            self.assertIn('finish or undo', res.note)
            self.assertTrue(os.path.exists(switcher.journal_path(self.ctx)))
            self.assertEqual(switcher.resolve_journal(self.ctx), word)
            self.assertFalse(os.path.exists(switcher.journal_path(self.ctx)))
            self.assertEqual(self.label(), 'uuid-a' if lands else 'uuid-b')
            self.assert_nothing_lost()
            if lands:
                switcher.switch(self.ctx, 'uuid-b')

    def test_another_switch_waits_for_the_running_one(self):
        gate, release = threading.Event(), threading.Event()

        def hook(step):
            if step == 'live_saved':
                gate.set()
                release.wait(10)

        switcher._HOOK = hook
        first = threading.Thread(target=lambda: switcher.switch(self.ctx, 'uuid-a'))
        first.start()
        gate.wait(10)
        switcher._HOOK = None
        with mock.patch.object(switcher, 'SWITCH_LOCK_WAIT_S', 0.3):
            with self.assertRaises(AccountsError) as e:
                switcher.switch(self.ctx, 'uuid-b')
        self.assertIn('Another switch', str(e.exception))
        release.set()
        first.join(20)
        self.assertEqual(self.label(), 'uuid-a')


class TargetRenewal(SwitchCase):
    def test_an_expired_target_is_renewed_before_it_is_written_so_claude_code_adopts_it_without_a_post(self):
        self.rig.set_saved_expiry('uuid-a', time.time() - 60)
        posts = len(self.server.token_posts())
        switcher.switch(self.ctx, 'uuid-a')
        written = live.parse(self.rig.live_text())
        self.assertFalse(written.expired(time.time()))
        self.assertEqual(written.refresh_token, self.server.valid_refresh('uuid-a'))
        self.assertEqual(len(self.server.token_posts()), posts + 1)

    def test_a_target_renewal_that_may_have_been_spent_stops_the_switch(self):
        self.rig.set_saved_expiry('uuid-a', time.time() - 60)
        self.server.script.append(('token', 'cut'))
        with self.assertRaises(AccountsError) as e:
            switcher.switch(self.ctx, 'uuid-a')
        self.assertEqual(e.exception.kind, 'network')
        self.assertEqual(self.rig.live_text(), self.b_text)            # a maybe-dead token is never written

    def test_a_target_refused_by_anthropic_asks_for_a_sign_in_and_changes_nothing(self):
        self.rig.set_saved_expiry('uuid-a', time.time() - 60)
        self.server.script.append(('token', (400, {'error': 'invalid_grant'})))
        with self.assertRaises(AccountsError) as e:
            switcher.switch(self.ctx, 'uuid-a')
        self.assertEqual(e.exception.kind, 'dead_login')
        self.assertIn('sign', str(e.exception).lower())
        self.assertEqual(self.rig.live_text(), self.b_text)


class CutOff(unittest.TestCase):
    """A switch killed with SIGKILL at each step; the next start leaves Claude Code consistent and no login
    is lost, and at every step both accounts' logins exist somewhere."""

    STEPS = ('journal', 'safety', 'live_saved', 'writing', 'credential_written', 'config_lock', 'config_written',
             'verify', 'done')

    def test_a_switch_killed_at_any_step_is_settled_at_the_next_start_and_loses_no_login(self):
        for step in self.STEPS:
            with self.subTest(step=step):
                self._one(step)

    def _one(self, step):
        rig = Rig()
        try:
            kc_path = os.path.join(rig.root, 'keychain.json')
            rig.kc = rig.sec = FileKeychain(kc_path)
            rig.ctx.keychain = rig.kc
            a_text, b_text = rig.two_accounts()
            spec = {'env': rig.env, 'home': rig.home, 'data': rig.data, 'keychain': kc_path, 'kill_at': step,
                    'target': 'uuid-a'}
            env = dict(os.environ)
            env.update({'HOME': rig.home, 'SWITCHBOARD_HOME': rig.data, 'PYTHONDONTWRITEBYTECODE': '1'})
            env.pop('CLAUDE_CONFIG_DIR', None)
            p = subprocess.run([sys.executable, os.path.join(HERE, 'cut_off_switch.py'), json.dumps(spec)],
                               env=env, capture_output=True, timeout=300)
            if step != 'done':
                self.assertEqual(p.returncode, -9, p.stderr.decode()[-2000:])
            for uuid in ('uuid-a', 'uuid-b'):                          # at the moment of death: both still exist
                self.assertTrue(rig.holds_valid(uuid), (step, uuid))
            note = switcher.resolve_journal(rig.ctx)
            self.assertFalse(os.path.exists(switcher.journal_path(rig.ctx)), (step, note))
            cur = live.parse(rig.live_text())
            label = (rig.config().get('oauthAccount') or {}).get('accountUuid')
            owner = 'uuid-a' if cur.fingerprint == live.parse(a_text).fingerprint else \
                'uuid-b' if cur.fingerprint == live.parse(b_text).fingerprint else None
            self.assertIsNotNone(owner, step)
            self.assertEqual(label, owner, (step, note))                 # label and login agree
            for uuid in ('uuid-a', 'uuid-b'):
                self.assertTrue(rig.holds_valid(uuid), (step, uuid))
            leftover = [p_ for p_ in (facts.refresh_lock(rig.env, rig.home), facts.legacy_lock(rig.env, rig.home),
                                      facts.config_lock(rig.env, rig.home)) if os.path.exists(p_)]
            self.assertEqual(leftover, [], step)                          # its orphaned lock folders are cleared
            res = switcher.switch(rig.ctx, 'uuid-b' if owner == 'uuid-a' else 'uuid-a')   # and switching works again
            self.assertTrue(res.ok, step)
        finally:
            rig.close()


if __name__ == '__main__':
    unittest.main()
