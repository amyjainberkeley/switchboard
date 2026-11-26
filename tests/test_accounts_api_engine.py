"""The account-engine half of switchboard/accounts/api.py, as the rest of Switchboard calls it, plus one test per
engine rule (each named after what it proves), and a switch end to end through the real Keychain
wrapper and a child-process stand-in for /usr/bin/security."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import os
import time
import unittest
from unittest import mock

from fakes_accounts import Rig, identity_block, login_text
from switchboard.accounts import (api, capture, ccfiles, context, facts, identity, live, net, roster, switcher,
                                  tokens, vault)
from switchboard.accounts.api import AccountsError


class ApiCase(unittest.TestCase):
    real_keychain = False

    def setUp(self):
        self.rig = Rig(real_keychain=self.real_keychain)
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        self.a_text, self.b_text = self.rig.two_accounts()          # Claude Code is on B

    def tearDown(self):
        self.use.__exit__(None, None, None)
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        self.rig.close()


class TheListAndNames(ApiCase):
    def test_accounts_come_in_the_persons_order_from_metadata_alone(self):
        calls = len(self.rig.kc.calls)
        got = api.accounts()
        self.assertEqual(len(self.rig.kc.calls), calls)               # no Keychain, no network
        self.assertEqual([(a.email, a.order) for a in got], [('a@example.com', 1), ('b@example.com', 2)])
        a = got[0]
        self.assertEqual((a.id, a.org_id, a.plan, a.fingerprint), ('uuid-a', 'org-a', 'max',
                                                                   live.parse(self.a_text).fingerprint))
        self.assertGreater(a.refresh_expires_at, time.time())
        self.assertEqual(a.status, 'ok')

    def test_find_takes_a_number_an_id_an_alias_or_an_email(self):
        api.set_alias('2', 'Work')
        self.assertEqual(api.find('1').id, 'uuid-a')
        self.assertEqual(api.find('uuid-b').email, 'b@example.com')
        self.assertEqual(api.find('WORK').id, 'uuid-b')
        self.assertEqual(api.find('A@Example.com').id, 'uuid-a')
        for bad in ('3', 'nobody@example.com', '', 'zzz'):
            with self.assertRaises(AccountsError) as e:
                api.find(bad)
            self.assertEqual(e.exception.kind, 'not_found')

    def test_aliases_are_unique_and_never_look_like_a_number_an_email_or_a_flag(self):
        api.set_alias('a@example.com', 'Home')
        self.assertEqual(api.find('1').alias, 'home')
        for bad in ('123', '-x', 'a@b.example', 'has space', 'home'):
            with self.assertRaises(AccountsError):
                api.set_alias('2', bad)
        api.set_alias('home', None)
        self.assertIsNone(api.find('1').alias)

    def test_reorder_sets_the_order_and_unknown_ids_are_refused(self):
        api.reorder(['uuid-b'])
        self.assertEqual([a.id for a in api.accounts()], ['uuid-b', 'uuid-a'])
        with self.assertRaises(AccountsError):
            api.reorder(['uuid-b', 'nope'])
        self.assertEqual([a.id for a in api.accounts()], ['uuid-b', 'uuid-a'])

    def test_removing_an_account_never_touches_the_live_login(self):
        api.remove('b@example.com')                                    # B is the live login
        self.assertEqual(self.rig.live_text(), self.b_text)
        self.assertEqual(self.rig.config()['oauthAccount']['accountUuid'], 'uuid-b')
        self.assertIsNone(self.rig.vault('uuid-b'))
        self.assertIsNone(self.rig.vault('uuid-b', 'Switchboard login (previous)'))
        self.assertEqual([a.id for a in api.accounts()], ['uuid-a'])
        self.assertIn('uuid-b', self.rig.doc()['removed'])
        self.assertEqual(self.rig.events[-1][0], 'Removed b@example.com from Switchboard')

    def test_an_unreadable_account_list_is_an_error_never_an_empty_list(self):
        # With a good copy beside it, the list is restored from that copy (test_heal_accounts_restore.py); with none
        # it stays an error. So the copies go first, the copy of its last save among them.
        for n in range(1, vault.BACKUPS + 1):
            if os.path.exists('%s.%d' % (vault.store_path(self.ctx), n)):
                os.unlink('%s.%d' % (vault.store_path(self.ctx), n))
        os.unlink(self.ctx.path(vault.LAST_GOOD))
        with open(vault.store_path(self.ctx), 'w') as f:
            f.write('{"accounts": {"uuid-a": ')
        with self.assertRaises(AccountsError):
            api.accounts()
        with self.assertRaises(AccountsError):
            api.switch('1')


class TheLiveView(ApiCase):
    def test_live_names_the_owner_by_the_credential_and_says_when_the_label_disagrees(self):
        got = api.live()
        self.assertEqual((got.state, got.account_id, got.email, got.proof, got.disagree),
                         (api.PRESENT, 'uuid-b', 'b@example.com', 'fingerprint', False))
        self.rig.set_config(identity_block('uuid-a', 'a@example.com', 'org-a'))   # Claude Desktop relabels
        roster.forget_live()
        got = api.live()
        self.assertEqual((got.account_id, got.disagree), ('uuid-b', True))
        self.assertIn('disagree', got.why)

    def test_live_reads_the_keychain_at_most_once_per_change(self):
        roster.forget_live()
        before = self.rig.kc.count('read', self.rig.service)
        for _ in range(5):
            api.live()
        self.assertEqual(self.rig.kc.count('read', self.rig.service), before + 1)
        self.rig.set_config(identity_block('uuid-b', 'b@example.com', 'org-b', name='New name'))
        api.live()
        self.assertEqual(self.rig.kc.count('read', self.rig.service), before + 2)

    def test_live_says_absent_unreadable_and_emptied_apart(self):
        roster.forget_live()
        self.rig.kc.rule('read', service=self.rig.service, why='timeout', times=2)
        got = api.live()
        self.assertEqual((got.state, got.why), ('unreadable', 'timeout'))
        self.rig.kc.rules.clear()
        wiped = json.loads(self.b_text)
        wiped['claudeAiOauth'].update({'accessToken': '', 'refreshToken': ''})
        self.rig.set_live(json.dumps(wiped))
        roster.forget_live()
        self.assertEqual(api.live().state, 'wiped')
        self.rig.kc.drop(self.rig.service, self.rig.account)
        roster.forget_live()
        self.assertEqual(api.live().state, 'absent')


class AddCurrent(ApiCase):
    def test_add_refuses_an_emptied_login_and_a_missing_one_and_a_failed_read_never_asks_to_sign_in(self):
        wiped = json.loads(self.b_text)
        wiped['claudeAiOauth'].update({'accessToken': '', 'refreshToken': '', 'expiresAt': 0})
        self.rig.set_live(json.dumps(wiped))
        with self.assertRaises(AccountsError) as e:
            api.add_current()
        self.assertEqual(e.exception.kind, 'dead_login')
        self.assertEqual(self.rig.vault('uuid-b'), self.b_text)          # never kept over a working one
        self.rig.kc.drop(self.rig.service, self.rig.account)
        with self.assertRaises(AccountsError) as e:
            api.add_current()
        self.assertEqual(e.exception.kind, 'not_found')
        self.rig.set_live(self.b_text)
        self.rig.kc.rule('read', service=self.rig.service, why='locked', times=2)
        with self.assertRaises(AccountsError) as e:
            api.add_current()
        self.assertEqual(e.exception.kind, 'keychain_locked')
        self.assertNotIn('sign in', str(e.exception).lower())

    def test_add_refuses_a_login_anthropic_says_is_someone_elses(self):
        c = self.rig.server.add('uuid-c', 'c@example.com', 'org-c')
        self.rig.set_live(c)
        self.rig.set_config(identity_block('uuid-d', 'd@example.com', 'org-d'))
        with self.assertRaises(AccountsError) as e:
            api.add_current()
        self.assertEqual(e.exception.kind, 'disagree')
        self.assertIn('c@example.com', str(e.exception))
        self.assertEqual(sorted(self.rig.doc()['accounts']), ['uuid-a', 'uuid-b'])

    def test_add_never_files_an_unproven_login_under_a_label_kept_with_another_login(self):
        self.rig.set_live(login_text('sk-ant-oat01-u', 'sk-ant-ort01-u', int((time.time() + 3600) * 1000)))
        self.rig.set_config(identity_block('uuid-a', 'a@example.com', 'org-a'))
        self.rig.server.script.append(('profile', net.NetError('dns', False)))
        with self.assertRaises(AccountsError) as e:
            api.add_current()
        self.assertEqual(e.exception.kind, 'network')
        self.assertEqual(self.rig.vault('uuid-a'), self.a_text)

    def test_adding_the_account_already_kept_refreshes_its_saved_copy(self):
        rotated = self.rig.claude_renews()
        acc = api.add_current()
        self.assertEqual(acc.id, 'uuid-b')
        self.assertEqual(self.rig.vault('uuid-b'), rotated)
        self.assertEqual(len(api.accounts()), 2)


class SwitchThroughApi(ApiCase):
    def test_api_switch_by_email_runs_the_transaction_and_records_it(self):
        res = api.switch('a@example.com', kind='forced', source='auto', reason='b@example.com reached its line')
        self.assertTrue(res.ok)
        self.assertEqual((res.kind, res.source, res.to_id), ('forced', 'auto', 'uuid-a'))
        self.assertEqual(self.rig.events[-1][2]['by'], 'auto')
        self.assertEqual(api.live().account_id, 'uuid-a')

    def test_resolve_journal_and_health_report_a_cut_off_switch(self):
        self.rig.kc.rule('write', service=self.rig.service, why='timeout', land=True)
        res = api.switch('1')
        self.assertFalse(res.ok)
        h = api.health()
        self.assertEqual(h['journal']['to'], 'uuid-a')
        self.assertTrue(any('cut off' in w for w in h['warnings']))
        self.assertEqual(api.resolve_journal(), 'Repaired the cut-off switch: Claude Code is on a@example.com')
        self.assertIsNone(api.health()['journal'])
        self.assertIsNone(api.resolve_journal())

    def test_health_names_the_store_claims_recovery_copies_and_claude_codes_version(self):
        h = api.health()
        self.assertEqual(h['store']['schema'], 1)
        self.assertTrue(h['store']['readable'])
        self.assertEqual(h['store']['accounts'], 2)
        self.assertEqual(h['claude_code']['checked'], facts.CHECKED)
        self.assertEqual(h['claims'], [])
        self.assertEqual(h['recovery'], [])
        self.assertIn('needs_approval_or_slow', h['keychain'])


class EngineRules(ApiCase):
    """The engine's rules for refresh tokens, labels, reads, writes and logs: one test per rule."""

    def test_a_refresh_token_is_spent_only_when_its_answer_will_be_saved_and_nobody_else_holds_it(self):
        self.rig.set_saved_expiry('uuid-b', time.time() - 60)              # B is live: Claude Code holds it
        self.assertEqual(tokens.refresh(self.ctx, 'uuid-b', budget_s=60).why, 'live')
        self.rig.set_saved_expiry('uuid-a', time.time() - 60)
        self.rig.kc.rule('write', service='Switchboard login', account='uuid-a', why='denied', times=-1)
        res = tokens.refresh(self.ctx, 'uuid-a', budget_s=60)
        self.assertEqual(res.outcome, 'stashed')
        self.assertTrue(self.rig.holds_valid('uuid-a'))

    def test_whose_login_it_is_comes_from_the_credential_never_from_a_label_or_slot_number(self):
        self.rig.set_config(identity_block('uuid-a', 'a@example.com', 'org-a'))
        self.assertEqual(identity.prove(self.ctx, live.parse(self.rig.live_text()), self.rig.doc(),
                                        self.rig.config()['oauthAccount']).account_id, 'uuid-b')

    def test_label_and_login_drifting_apart_is_shown_as_a_state(self):
        self.rig.set_config(identity_block('uuid-a', 'a@example.com', 'org-a'))
        roster.forget_live()
        self.assertTrue(api.live().disagree)

    def test_could_not_read_is_neither_empty_nor_dead(self):
        self.rig.kc.rule('read', service='Switchboard login', account='uuid-a', why='timeout', times=-1)
        self.rig.set_saved_expiry('uuid-a', time.time() - 60)
        res = tokens.refresh(self.ctx, 'uuid-a', budget_s=60)
        self.assertEqual(res.outcome, 'transient')
        a = [x for x in api.accounts() if x.id == 'uuid-a'][0]
        self.assertEqual(a.status, 'unreadable')                         # never "needs sign-in"

    def test_the_outgoing_login_is_written_only_after_a_fresh_ownership_check_under_the_locks(self):
        reads = []
        self.rig.kc.on_call = lambda v, s, a: reads.append((v, s, os.path.isdir(
            facts.refresh_lock(self.rig.env, self.rig.home)))) if s == self.rig.service and v == 'read' else None
        api.switch('1')
        self.rig.kc.on_call = None
        self.assertTrue(any(locked for _, _, locked in reads))         # re-read while Claude Code's lock was held

    def test_without_proof_nothing_is_written_into_a_slot_and_a_safety_copy_is_kept(self):
        self.rig.set_live(login_text('sk-ant-oat01-q', 'sk-ant-ort01-q', int((time.time() - 9) * 1000)))
        self.rig.set_config(identity_block('uuid-a', 'a@example.com', 'org-a'))
        res = api.switch('2')
        self.assertIn('safety copy', res.note)
        self.assertEqual(self.rig.vault('uuid-a'), self.a_text)

    def test_the_budget_covers_the_whole_operation_including_name_lookup(self):
        self.rig.set_saved_expiry('uuid-a', time.time() - 60)
        self.rig.server.dns_ok = False
        self.assertEqual(tokens.refresh(self.ctx, 'uuid-a', budget_s=60).why, 'dns')
        self.rig.server.dns_ok = True
        self.assertEqual(tokens.refresh(self.ctx, 'uuid-a', budget_s=24).why, 'no_time')
        self.assertEqual(self.rig.server.token_posts(), [])            # nothing went out either time

    def test_watchers_stay_quiet_when_they_cannot_help(self):
        with vault.changing(self.ctx) as doc:
            doc['upkeep']['uuid-a'] = {'fails': 10 ** 6, 'last_try': time.time()}
        tokens._note_upkeep(self.ctx, 'uuid-a', tokens.Result('transient', 'uuid-a'))   # no overflow
        u = self.rig.doc()['upkeep']['uuid-a']
        self.assertLessEqual(u['next_at'] - u['last_try'], tokens.BACKOFF_CAP_S + 1)
        self.rig.set_saved_expiry('uuid-a', time.time() - 60)
        self.assertEqual(tokens.upkeep_pass(self.ctx, budget_s=60), [])      # resting: no request

    def test_writes_are_atomic_and_an_undo_never_writes_in_place(self):
        writes = []
        real = ccfiles.atomic_write
        with mock.patch.object(ccfiles, 'atomic_write', lambda *a, **k: (writes.append(a[0]), real(*a, **k))[1]):
            api.switch('1')
        self.assertIn(self.rig.config_path(), writes)
        self.assertEqual([n for n in os.listdir(self.rig.home) if n.endswith('.tmp')], [])

    def test_refreshes_switches_and_refusals_are_said_and_logged_without_secrets(self):
        self.rig.set_saved_expiry('uuid-a', time.time() - 60)
        tokens.refresh(self.ctx, 'uuid-a', budget_s=60)
        api.switch('1')
        text = '\n'.join(self.rig.logs + [e[0] for e in self.rig.events])
        self.assertIn('Refreshing the saved login of a@example.com', text)
        self.assertIn('Switched Claude Code to a@example.com', text)
        for value in self.rig.kc.items().values():
            for tok in ('accessToken', 'refreshToken'):
                try:
                    secret = json.loads(value)['claudeAiOauth'][tok]
                except (ValueError, KeyError, TypeError):
                    continue
                self.assertNotIn(secret, text)


class EngineRequirements(ApiCase):
    """What the engine requires of its saved logins, switches, refreshes, files and facts: one test per requirement."""

    def test_every_saved_login_has_fingerprints_a_refresh_stamp_and_an_owner(self):
        a = self.rig.doc()['accounts']['uuid-a']
        login = live.parse(self.a_text)
        self.assertEqual((a['fp'], a['access_fp'], a['rx_ms'], a['uuid'], a['org_id']),
                         (login.fingerprint, login.access_fp, login.refresh_expires_ms, 'uuid-a', 'org-a'))

    def test_each_account_keeps_its_earlier_generations(self):
        for _ in range(3):
            self.rig.claude_renews()
            capture.keep_pass(self.ctx, force=True)
        gens = json.loads(self.rig.vault('uuid-b', 'Switchboard login (previous)'))['generations']
        self.assertEqual(len(gens), vault.PREVIOUS_KEPT)
        self.assertEqual(len(self.rig.doc()['accounts']['uuid-b']['previous_fps']), vault.PREVIOUS_KEPT)

    def test_active_is_the_account_whose_lineage_the_live_item_holds_not_the_label(self):
        self.rig.set_config(identity_block('uuid-a', 'a@example.com', 'org-a'))
        roster.forget_live()
        self.assertEqual(api.live().account_id, 'uuid-b')

    def test_reads_are_present_absent_or_unreadable_and_a_stand_in_copy_is_never_spent(self):
        self.rig.kc.rule('read', service=self.rig.service, why='locked', times=-1)
        with open(facts.credentials_file(self.rig.env, self.rig.home), 'w') as f:
            f.write(self.b_text)
        lr = live.read_live(self.ctx)
        self.assertEqual((lr.read.kind, lr.read.provenance, lr.read.degraded), ('present', 'file', True))
        self.rig.set_saved_expiry('uuid-a', time.time() - 60)
        self.assertEqual(tokens.refresh(self.ctx, 'uuid-a', budget_s=60).why, 'live_unreadable')

    def test_display_reads_are_cached_by_a_change_signal(self):
        roster.forget_live()
        n = self.rig.kc.count('read')
        api.live()
        api.live()
        self.assertEqual(self.rig.kc.count('read'), n + 1)

    def test_only_the_account_login_key_is_replaced_in_the_live_item(self):
        blob = json.loads(self.rig.live_text())
        blob['mcpOAuth'] = {'s': 'live'}
        self.rig.set_live(json.dumps(blob))
        api.switch('1')
        self.assertEqual(json.loads(self.rig.live_text())['mcpOAuth'], {'s': 'live'})

    def test_a_slot_is_written_only_with_proof_under_the_locks_and_with_a_safety_copy_first(self):
        order = []
        real_stash = vault.stash
        with mock.patch.object(vault, 'stash', lambda *a, **k: (order.append('stash'), real_stash(*a, **k))[1]):
            real_save = vault.save_login
            with mock.patch.object(vault, 'save_login', lambda *a, **k: (order.append('save'), real_save(*a, **k))[1]):
                self.rig.claude_renews()
                api.switch('1')
        self.assertEqual(order[:2], ['stash', 'save'])

    def test_never_a_wiped_shell_an_oauth_less_store_or_older_over_newer(self):
        wiped = json.loads(self.b_text)
        wiped['claudeAiOauth'].update({'accessToken': '', 'refreshToken': ''})
        self.rig.set_live(json.dumps(wiped))
        api.switch('1')
        self.assertEqual(self.rig.vault('uuid-b'), self.b_text)

    def test_a_cut_off_switch_is_journaled_and_undone_only_by_recorded_steps(self):
        self.rig.kc.rule('write', service=self.rig.service, why='timeout', land=False)
        api.switch('1')
        r, j = switcher.read_journal(self.ctx)
        self.assertEqual(j['step'], 'write_unknown')
        self.assertEqual(api.resolve_journal(), 'Repaired the switch that was cut off')
        self.assertEqual(self.rig.config()['oauthAccount']['accountUuid'], 'uuid-b')

    def test_every_file_is_written_atomically_and_owner_only(self):
        api.switch('1')
        for path in (vault.store_path(self.ctx), self.rig.config_path()):
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600, path)

    def test_a_refresh_is_a_transaction_with_a_marker_and_its_successor_saved_first(self):
        self.rig.set_saved_expiry('uuid-a', time.time() - 60)
        marks = []
        self.rig.server.on_post = lambda rt: marks.append(dict(self.rig.doc()['refreshing']))
        tokens.refresh(self.ctx, 'uuid-a', budget_s=60)
        self.assertIn('uuid-a', marks[0])                                 # the marker was there before the POST
        self.assertEqual(self.rig.doc()['refreshing'], {})

    def test_idle_logins_are_renewed_on_their_own_schedule_and_live_holders_are_skipped(self):
        self.rig.set_saved_expiry('uuid-a', time.time() - 60)
        self.rig.set_saved_expiry('uuid-b', time.time() - 60)
        self.assertEqual(api.upkeep_pass(120), ['Renewed the saved login of a@example.com'])
        self.assertTrue(self.rig.holds_valid('uuid-b'))

    def test_only_a_server_refusal_of_bytes_nobody_else_holds_strikes_and_a_new_login_clears_it(self):
        self.rig.set_saved_expiry('uuid-a', time.time() - 60)
        self.rig.server.script.append(('token', (400, {'error': 'invalid_grant'})))
        tokens.refresh(self.ctx, 'uuid-a', budget_s=60)
        self.assertEqual([a.status for a in api.accounts() if a.id == 'uuid-a'], ['needs_login'])
        self.rig.keep('uuid-a', 'a@example.com', 'org-a')
        self.assertNotEqual([a.status for a in api.accounts() if a.id == 'uuid-a'], ['needs_login'])

    def test_after_a_wake_nothing_is_posted_until_the_network_has_answered(self):
        self.rig.set_saved_expiry('uuid-a', time.time() - 60)
        tokens.WAKE.observe(self.ctx)
        tokens.WAKE.woke, tokens.WAKE.reach_since = self.ctx.mono(), None
        self.assertEqual(tokens.refresh(self.ctx, 'uuid-a', budget_s=60).why, 'just_woke')
        self.assertEqual(self.rig.server.token_posts(), [])

    def test_claude_codes_config_file_and_keychain_item_are_resolved_as_claude_code_does(self):
        self.assertEqual(self.rig.service, 'Claude Code-credentials')
        legacy = os.path.join(self.rig.home, '.claude', '.config.json')
        with open(legacy, 'w') as f:
            json.dump({'oauthAccount': identity_block('uuid-b', 'b@example.com', 'org-b')}, f)
        self.assertEqual(facts.global_config(self.rig.env, self.rig.home), legacy)
        api.switch('1')
        with open(legacy) as f:
            self.assertEqual(json.load(f)['oauthAccount']['accountUuid'], 'uuid-a')

    def test_a_switch_writes_the_credential_and_the_account_block_together_and_moves_the_login_file(self):
        path = facts.credentials_file(self.rig.env, self.rig.home)
        with open(path, 'w') as f:
            f.write(self.b_text)
        api.switch('1')
        with open(path) as f:
            self.assertEqual(live.parse(f.read()).fingerprint, live.parse(self.a_text).fingerprint)
        self.assertEqual(self.rig.config()['oauthAccount']['accountUuid'], 'uuid-a')


class FactsWithoutRunningClaudeCode(unittest.TestCase):
    def test_the_engine_never_runs_claude_code_to_learn_its_facts(self):
        with open(facts.__file__, encoding='utf-8') as f:
            self.assertNotIn('subprocess', f.read())                    # facts come from the binary's strings


class RealToolEndToEnd(ApiCase):
    """Through keychain.Keychain and a child process per call (generous deadlines: a loaded Mac starts slowly)."""
    real_keychain = True

    def test_a_switch_through_the_security_tool_writes_hex_reads_back_and_keeps_both_logins(self):
        res = api.switch('a@example.com', source='page')
        self.assertTrue(res.ok, res)
        self.assertEqual(live.parse(self.rig.live_text()).fingerprint, live.parse(self.a_text).fingerprint)
        self.assertEqual(self.rig.vault('uuid-b'), self.b_text)
        adds = self.rig.sec.calls('add')
        self.assertTrue(adds and all('-U' in c['flags'] and '-T' not in c['flags'] for c in adds))
        self.assertTrue(self.rig.holds_valid('uuid-a') and self.rig.holds_valid('uuid-b'))
        self.assertEqual(self.rig.sec.max_concurrent(), 1)


if __name__ == '__main__':
    unittest.main()
