"""Refreshing saved logins as a transaction: a refresh token works once, so it is spent only when nobody else
holds it and the answer will be saved."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import os
import threading
import time
import unittest
from unittest import mock

from fakes_accounts import Rig
from switchboard.accounts import live, net, tokens, vault
from switchboard.accounts.live import fingerprint


class Clock:
    """Real time plus a jump the test can add (a sleep, or a caller's deadline passing)."""

    def __init__(self):
        self.wall_jump = 0.0
        self.mono_jump = 0.0

    def wall(self):
        return time.time() + self.wall_jump

    def mono(self):
        return time.monotonic() + self.mono_jump


class RefreshCase(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.rig = Rig(wall=self.clock.wall, mono=self.clock.mono)
        self.ctx = self.rig.ctx
        self.a_text, self.b_text = self.rig.two_accounts()
        self.server = self.rig.server
        self.posts_before = len(self.server.token_posts())

    def tearDown(self):
        deadline = time.time() + 20
        while tokens._INFLIGHT and time.time() < deadline:
            time.sleep(0.05)
        self.rig.close()

    def posts(self):
        return self.server.token_posts()[self.posts_before:]

    def expire_a(self):
        return self.rig.set_saved_expiry('uuid-a', time.time() - 60)


class TheTransaction(RefreshCase):
    def test_a_saved_login_is_refreshed_inside_one_transaction_and_its_successor_saved_first(self):
        self.expire_a()
        res = tokens.refresh(self.ctx, 'uuid-a', budget_s=60)
        self.assertEqual(res.outcome, 'refreshed', res)
        valid = self.server.valid_refresh('uuid-a')
        saved = live.parse(self.rig.vault('uuid-a'))
        self.assertEqual(saved.refresh_token, valid)
        self.assertEqual(saved.refresh_expires_ms, live.parse(self.a_text).refresh_expires_ms)  # kept by the refresh
        prev = json.loads(self.rig.vault('uuid-a', 'Switchboard login (previous)'))['generations']
        self.assertEqual(live.parse(prev[0]['text']).fingerprint, fingerprint(json.loads(self.a_text)
                                                                               ['claudeAiOauth']['refreshToken']))
        doc = self.rig.doc()
        self.assertEqual(doc['refreshing'], {})
        self.assertEqual(doc['accounts']['uuid-a']['fp'], saved.fingerprint)
        self.assertEqual(len(self.posts()), 1)
        logs = '\n'.join(self.rig.logs)
        self.assertIn('Refreshing the saved login of a@example.com', logs)       # said above debug
        self.assertIn('Refreshed the saved login of a@example.com', logs)
        self.assertNotIn(valid, logs)                                            # never a token in a log
        self.assertNotIn(json.loads(self.a_text)['claudeAiOauth']['refreshToken'], logs)

    def test_the_post_carries_claude_codes_client_and_our_own_honest_user_agent(self):
        seen = []
        real = self.server.request

        def spy(method, url, headers=None, body=None, timeout_s=10.0):
            seen.append((url, headers, json.loads(body) if body else None))
            return real(method, url, headers, body, timeout_s)

        self.server.request = spy
        self.expire_a()
        tokens.refresh(self.ctx, 'uuid-a', budget_s=60)
        url, headers, body = [s for s in seen if s[2]][0]
        self.assertEqual(body['grant_type'], 'refresh_token')
        self.assertEqual(body['client_id'], '9d1c250a-e61b-44d9-88ed-5944d1962f5e')
        self.assertTrue(headers['User-Agent'].startswith('Switchboard/'))
        self.assertNotIn('claude', headers['User-Agent'].lower().replace('switchboard', ''))

    def test_consumers_of_one_lineage_spend_one_grant(self):
        self.expire_a()
        self.server.post_delay = 0.3
        out = []
        threads = [threading.Thread(target=lambda: out.append(tokens.refresh(self.ctx, 'uuid-a', budget_s=60)))
                   for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(self.posts()), 1)
        self.assertEqual(sorted(r.outcome for r in out).count('refreshed'), 1)
        self.assertTrue(self.rig.holds_valid('uuid-a'))


class NobodyElseHoldsIt(RefreshCase):
    def test_a_saved_login_that_claude_code_also_holds_is_never_refreshed_even_when_the_label_names_another(self):
        # Claude Code is on A's very login while ~/.claude.json was relabelled to B
        self.rig.set_live(self.rig.vault('uuid-a'))
        self.expire_a()
        self.rig.set_live(self.rig.vault('uuid-a'))
        res = tokens.refresh(self.ctx, 'uuid-a', budget_s=60)
        self.assertEqual((res.outcome, res.why), ('skipped', 'live'))
        self.assertEqual(self.posts(), [])

    def test_another_generation_of_the_live_sign_in_is_never_refreshed(self):
        self.expire_a()
        self.rig.set_live(self.server.refresh_for(self.rig.vault('uuid-a')))      # Claude Code renewed A itself
        res = tokens.refresh(self.ctx, 'uuid-a', budget_s=60)
        self.assertEqual((res.outcome, res.why), ('skipped', 'live_same_login'))
        self.assertEqual(len(self.posts()), 1)                    # only Claude Code's own renewal

    def test_an_unreadable_or_stand_in_live_login_means_no_refresh(self):
        self.expire_a()
        self.rig.kc.rule('read', service=self.rig.service, why='timeout', times=-1)
        self.assertEqual(tokens.refresh(self.ctx, 'uuid-a', budget_s=60).why, 'live_unreadable')
        with open(os.path.join(self.rig.home, '.claude', '.credentials.json'), 'w') as f:
            f.write(self.b_text)                                  # Claude Code's file stands in: maybe old
        self.assertEqual(tokens.refresh(self.ctx, 'uuid-a', budget_s=60).why, 'live_unreadable')
        self.assertEqual(self.posts(), [])

    def test_a_run_as_claim_or_a_claim_not_yet_settled_blocks_the_refresh(self):
        self.expire_a()
        with vault.changing(self.ctx) as doc:
            doc['claims']['uuid-a'] = {'pid': os.getpid(), 'start': 'self-start', 'dir': '/nowhere'}
        self.assertEqual(tokens.refresh(self.ctx, 'uuid-a', budget_s=60).why, 'claimed')
        self.rig.procs.table.clear()                              # its process is gone: not yet settled
        self.assertEqual(tokens.refresh(self.ctx, 'uuid-a', budget_s=60).why, 'claim_unreaped')
        self.assertEqual(self.posts(), [])

    def test_a_redirected_secure_store_refuses_to_spend(self):
        self.rig.env['CLAUDE_SECURESTORAGE_CONFIG_DIR'] = os.path.join(self.rig.root, 'other-store')
        self.assertEqual(tokens.refresh(self.ctx, 'uuid-a', budget_s=60).why, 'store_unmirrored')


class WholeDeadline(RefreshCase):
    def test_a_refresh_cut_off_after_wake_never_loses_the_login(self):
        self.expire_a()
        self.server.dns_ok = False                                # names don't resolve: nothing goes out
        self.assertEqual(tokens.refresh(self.ctx, 'uuid-a', budget_s=60).why, 'dns')
        tokens.WAKE.observe(self.ctx)
        self.clock.wall_jump += 3 * 3600                          # the Mac slept three hours
        self.assertEqual(tokens.refresh(self.ctx, 'uuid-a', budget_s=60).why, 'just_woke')
        self.server.dns_ok = True
        self.assertEqual(tokens.refresh(self.ctx, 'uuid-a', budget_s=60).why, 'just_woke')   # 30 s of network
        self.clock.mono_jump += 31
        self.assertEqual(tokens.refresh(self.ctx, 'uuid-a', budget_s=20).why, 'no_time')      # < 25 s left: no POST
        self.assertEqual(self.posts(), [])
        self.assertEqual(self.rig.doc()['refreshing'], {})
        # the answer comes after the caller has given up: the worker still saves it
        self.server.on_post = lambda rt: setattr(self.clock, 'mono_jump', self.clock.mono_jump + 500)
        self.server.post_delay = 0.4
        res = tokens.refresh(self.ctx, 'uuid-a', budget_s=60)
        self.assertEqual(res.outcome, 'unanswered')
        self.assertTrue(res.spent_maybe)
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        self.assertEqual(live.parse(self.rig.vault('uuid-a')).refresh_token, self.server.valid_refresh('uuid-a'))
        self.assertEqual(self.rig.doc()['refreshing'], {})
        self.assertEqual(len(self.posts()), 1)

    def test_a_refresh_that_reached_the_server_but_lost_its_answer_is_marked_and_said_truthfully(self):
        self.expire_a()
        self.server.script.append(('token', 'cut'))
        res = tokens.refresh(self.ctx, 'uuid-a', budget_s=60)
        self.assertEqual(res.outcome, 'transient')
        self.assertTrue(res.spent_maybe)
        self.assertEqual(self.rig.doc()['refreshing']['uuid-a']['fp'], res.consumed_fp)     # the marker stays
        self.assertIn('may be used up', '\n'.join(self.rig.logs))                          # logged, not debug
        again = tokens.refresh(self.ctx, 'uuid-a', budget_s=60)
        self.assertEqual(again.outcome, 'permanent')
        self.assertTrue(self.rig.doc()['accounts']['uuid-a']['strike']['after_cut_off'])
        self.assertIn('cut off', self.rig.events[-1][0])

    def test_a_request_that_never_left_clears_its_marker_and_spends_nothing(self):
        self.expire_a()
        self.server.script.append(('token', net.NetError('connect', False)))
        res = tokens.refresh(self.ctx, 'uuid-a', budget_s=60)
        self.assertEqual((res.outcome, res.spent_maybe), ('transient', False))
        self.assertEqual(self.rig.doc()['refreshing'], {})
        self.assertTrue(self.rig.holds_valid('uuid-a'))


class SavedOrKept(RefreshCase):
    def test_a_keychain_write_that_fails_after_a_refresh_keeps_the_successor_and_the_next_run_saves_it(self):
        self.expire_a()
        self.rig.kc.rule('write', service='Switchboard login', account='uuid-a', why='timeout', times=-1)
        res = tokens.refresh(self.ctx, 'uuid-a', budget_s=60)
        self.assertEqual(res.outcome, 'stashed')
        self.assertTrue(self.rig.holds_valid('uuid-a'))           # in the recovery item
        self.assertEqual(self.rig.doc()['refreshing']['uuid-a']['fp'], res.consumed_fp)
        self.rig.kc.rules.clear()
        posts = len(self.posts())
        again = tokens.refresh(self.ctx, 'uuid-a', budget_s=60)
        self.assertEqual(again.outcome, 'adopted')
        self.assertEqual(len(self.posts()), posts)                # nothing spent to get it back
        self.assertEqual(live.parse(self.rig.vault('uuid-a')).refresh_token, self.server.valid_refresh('uuid-a'))
        self.assertEqual(self.rig.doc()['refreshing'], {})
        self.assertEqual(vault.stashed(self.ctx), [])

    def test_when_the_keychain_refuses_everything_the_successor_goes_to_an_owner_only_file(self):
        self.expire_a()
        self.rig.kc.rule('write', service='Switchboard login', why='locked', times=-1)
        self.rig.kc.rule('write', service='Switchboard recovery', why='locked', times=-1)
        res = tokens.refresh(self.ctx, 'uuid-a', budget_s=60)
        self.assertEqual(res.outcome, 'stashed')
        folder = os.path.join(self.rig.data, 'recovery')
        files = [n for n in os.listdir(folder) if n.startswith('successor')]
        self.assertEqual(len(files), 1)
        self.assertEqual(os.stat(os.path.join(folder, files[0])).st_mode & 0o777, 0o600)
        self.assertTrue(self.rig.holds_valid('uuid-a'))
        self.rig.kc.rules.clear()
        self.assertEqual(tokens.refresh(self.ctx, 'uuid-a', budget_s=60).outcome, 'adopted')
        self.assertEqual(live.parse(self.rig.vault('uuid-a')).refresh_token, self.server.valid_refresh('uuid-a'))

    def test_when_no_store_takes_it_the_successor_is_held_in_memory_and_saved_next_time(self):
        self.expire_a()
        self.rig.kc.rule('write', service='Switchboard login', why='locked', times=-1)
        self.rig.kc.rule('write', service='Switchboard recovery', why='locked', times=-1)
        blocker = os.path.join(self.rig.root, 'not-a-folder')
        open(blocker, 'w').close()                                 # the recovery folder can't be made either
        with mock.patch.object(vault, '_recovery_dir', lambda ctx: os.path.join(blocker, 'recovery')):
            res = tokens.refresh(self.ctx, 'uuid-a', budget_s=60)
        self.assertEqual(res.outcome, 'unpersisted')
        self.assertIn('uuid-a', tokens._UNPERSISTED)
        self.rig.kc.rules.clear()
        self.assertEqual(tokens.refresh(self.ctx, 'uuid-a', budget_s=60).outcome, 'adopted')
        self.assertEqual(live.parse(self.rig.vault('uuid-a')).refresh_token, self.server.valid_refresh('uuid-a'))

    def test_a_login_replaced_while_its_refresh_was_out_is_not_overwritten(self):
        self.expire_a()
        newer = self.server.add('uuid-a', 'a@example.com', 'org-a')          # a re-login lands meanwhile

        def land(rt):
            with vault.account_lock(self.ctx):
                vault.save_login(self.ctx, 'uuid-a', newer)
                doc = vault.load(self.ctx)
                vault.note_login(doc['accounts']['uuid-a'], live.parse(newer), 'profile', time.time())
                vault.save(self.ctx, doc)

        self.server.on_post = land
        res = tokens.refresh(self.ctx, 'uuid-a', budget_s=60)
        self.assertEqual(res.outcome, 'adopted')
        self.assertEqual(self.rig.vault('uuid-a'), newer)
        self.assertEqual([e['reason'] for e in vault.stashed(self.ctx)], ['replaced while refreshing'])


class Verdicts(RefreshCase):
    def test_invalid_grant_strikes_once_bound_to_the_posted_bytes_and_clears_when_the_login_changes(self):
        self.expire_a()
        self.server.script.append(('token', (400, {'error': 'invalid_grant',
                                                   'error_description': 'Refresh token not found or invalid'})))
        res = tokens.refresh(self.ctx, 'uuid-a', budget_s=60)
        self.assertEqual(res.outcome, 'permanent')
        doc = self.rig.doc()
        self.assertEqual(doc['accounts']['uuid-a']['strike']['fp'], res.consumed_fp)
        self.assertEqual(vault.status_of(self.ctx, doc, 'uuid-a'), 'needs_login')
        warnings = [e for e in self.rig.events if 'needs sign-in' in e[0]]
        self.assertEqual(len(warnings), 1)
        posts = len(self.posts())
        self.assertEqual(tokens.refresh(self.ctx, 'uuid-a', budget_s=60).why, 'needs_login')
        self.assertEqual(len(self.posts()), posts)               # a refused login is never posted again
        self.rig.keep('uuid-a', 'a@example.com', 'org-a')         # signing in again lifts it
        doc = self.rig.doc()
        self.assertNotIn('strike', doc['accounts']['uuid-a'])
        self.assertIn(vault.status_of(self.ctx, doc, 'uuid-a'), ('ok', 'expiring'))

    def test_invalid_client_strikes_nobody(self):
        self.expire_a()
        self.server.script.append(('token', (401, {'error': 'invalid_client'})))
        self.assertEqual(tokens.refresh(self.ctx, 'uuid-a', budget_s=60).outcome, 'systemic')
        self.assertNotIn('strike', self.rig.doc()['accounts']['uuid-a'])

    def test_server_errors_rate_limits_timeouts_and_wrapped_messages_never_strike(self):
        self.expire_a()
        for action in ((500, {'error': 'invalid_grant'}), (429, {}), (400, {'error': {'message': 'invalid_grant'}}),
                       (403, {'detail': 'invalid_grant'}), net.NetError('timeout', False)):
            self.server.script.append(('token', action))
            res = tokens.refresh(self.ctx, 'uuid-a', budget_s=60)
            self.assertEqual(res.outcome, 'transient', action)
            self.assertNotIn('strike', self.rig.doc()['accounts']['uuid-a'])
        self.assertTrue(self.rig.holds_valid('uuid-a'))

    def test_a_200_that_cannot_be_read_counts_as_maybe_spent_never_as_fine(self):
        self.expire_a()
        self.server.script.append(('token', 'garbled'))
        res = tokens.refresh(self.ctx, 'uuid-a', budget_s=60)
        self.assertEqual((res.outcome, res.spent_maybe), ('transient', True))
        self.assertIn('uuid-a', self.rig.doc()['refreshing'])

    def test_a_token_answer_naming_another_account_sets_the_successor_aside(self):
        self.expire_a()
        real = self.server._token

        def other(body, timeout_s):
            status, h, data = real(body, timeout_s)
            obj = json.loads(data)
            obj['account']['uuid'] = 'uuid-zzz'
            return status, h, json.dumps(obj).encode()

        self.server._token = other
        res = tokens.refresh(self.ctx, 'uuid-a', budget_s=60)
        self.assertEqual((res.outcome, res.why), ('stashed', 'identity_conflict'))
        self.assertIn('conflict', self.rig.doc()['accounts']['uuid-a'])
        self.assertTrue(self.rig.holds_valid('uuid-a'))


class IdleUpkeep(RefreshCase):
    def test_idle_saved_logins_are_renewed_on_their_own_schedule_and_the_live_lineage_never(self):
        self.expire_a()
        doc = self.rig.doc()
        self.assertTrue(tokens.due(doc, 'uuid-a', time.time()))
        self.assertFalse(tokens.due(doc, 'uuid-b', time.time()))  # fresh: not due
        self.rig.set_saved_expiry('uuid-b', time.time() - 60)      # B is the live lineage: due, but never spent
        notes = tokens.upkeep_pass(self.ctx, budget_s=120)
        self.assertEqual(notes, ['Renewed the saved login of a@example.com'])
        self.assertEqual([p[2] for p in self.posts()], [fingerprint(json.loads(self.a_text)
                                                                    ['claudeAiOauth']['refreshToken'])])
        self.assertTrue(self.rig.holds_valid('uuid-b'))

    def test_one_accounts_trouble_never_stops_the_others_and_failures_back_off_with_a_cap(self):
        self.rig.keep('uuid-c', 'c@example.com', 'org-c')          # now C is live; A and B are idle
        for aid in ('uuid-a', 'uuid-b'):
            self.rig.set_saved_expiry(aid, time.time() - 60)
        real = tokens.refresh

        def flaky(ctx, aid, **kw):
            if aid == 'uuid-a':
                raise RuntimeError('boom')
            return real(ctx, aid, **kw)

        with mock.patch.object(tokens, 'refresh', flaky):
            notes = tokens.upkeep_pass(self.ctx, budget_s=120)
        self.assertEqual(notes, ['Renewed the saved login of b@example.com'])
        u = self.rig.doc()['upkeep']['uuid-a']
        self.assertEqual(u['fails'], 1)
        self.assertAlmostEqual(u['next_at'] - u['last_try'], tokens.BACKOFF_BASE_S, delta=1)
        self.assertFalse(tokens.due(self.rig.doc(), 'uuid-a', time.time()))
        with vault.changing(self.ctx) as doc:
            doc['upkeep']['uuid-a']['fails'] = 5000                # no overflow, and the wait is capped
        res = tokens.Result('transient', 'uuid-a')
        tokens._note_upkeep(self.ctx, 'uuid-a', res)
        u = self.rig.doc()['upkeep']['uuid-a']
        self.assertLessEqual(u['next_at'] - u['last_try'], tokens.BACKOFF_CAP_S + 1)

    def test_upkeep_waits_after_a_wake_until_the_network_has_answered_for_a_while(self):
        self.expire_a()
        tokens.upkeep_pass(self.ctx, budget_s=1)                   # a first look
        self.clock.wall_jump += 8 * 3600
        posts = len(self.posts())
        self.assertEqual(tokens.upkeep_pass(self.ctx, budget_s=120), [])
        self.assertEqual(len(self.posts()), posts)
        self.clock.mono_jump += 31
        self.assertEqual(tokens.upkeep_pass(self.ctx, budget_s=120), ['Renewed the saved login of a@example.com'])


if __name__ == '__main__':
    unittest.main()
