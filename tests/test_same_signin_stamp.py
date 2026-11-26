"""One sign-in stays one sign-in when its end stamp moves a little at a renewal.

Claude Code stores refreshTokenExpiresAt = min(the stamp it had, now + the seconds left) after each renewal, so the
stamp of one sign-in can go down by milliseconds to seconds. Switchboard must still see the renewed login as the same
sign-in: save it over the older saved copy, never call it older for its lower stamp, and never spend the saved login
of the account Claude Code is on.
"""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import time
import unittest

from fakes_accounts import identity_block, login_text
from same_signin_fakes import DAY_MS, email_of, keep_at, moved, new_rig, refresh_token, renew, sign_in_at, \
    count_profile_asks
from switchboard.accounts import capture, facts, identity, live, net, runas, switcher, tokens, vault
from switchboard.accounts.live import fingerprint


def _login(access, refresh, exp_ms, rx_ms):
    return live.parse(login_text(access, refresh, exp_ms, rx_ms))


class TheHelper(unittest.TestCase):
    def test_two_stamps_are_one_sign_in_within_the_named_window_only(self):
        self.assertEqual(facts.SAME_SIGN_IN_MS, 120000)
        r = 1800000000000
        self.assertTrue(facts.same_sign_in(r, r))
        self.assertTrue(facts.same_sign_in(r, r - 300))
        self.assertTrue(facts.same_sign_in(r - 120000, r))
        self.assertFalse(facts.same_sign_in(r - 120001, r))
        self.assertFalse(facts.same_sign_in(r, r + 120001))
        self.assertFalse(facts.same_sign_in(None, r))
        self.assertFalse(facts.same_sign_in(r, None))
        self.assertFalse(facts.same_sign_in(None, None))


class Compare(unittest.TestCase):
    R = 1800000000000
    E = 1790000000000

    def test_one_refresh_token_is_told_apart_by_its_access_expiry_as_before(self):
        a = _login('at1', 'rt1', self.E + 5000, self.R - 900)
        b = _login('at0', 'rt1', self.E, self.R)
        self.assertEqual(live.compare(a, b), 'newer')
        self.assertEqual(live.compare(b, a), 'older')

    def test_a_lower_stamp_within_the_window_is_the_newer_generation_when_its_access_expiry_is_later(self):
        renewed = _login('at2', 'rt2', self.E + 8 * 3600 * 1000, self.R - 300)
        saved = _login('at1', 'rt1', self.E, self.R)
        self.assertEqual(live.compare(renewed, saved), 'newer')
        self.assertEqual(live.compare(saved, renewed), 'older')

    def test_stamps_further_apart_than_the_window_are_two_sign_ins_and_the_later_one_is_newer(self):
        later = _login('at2', 'rt2', self.E, self.R + 120001)
        earlier = _login('at1', 'rt1', self.E + 3600 * 1000, self.R)
        self.assertEqual(live.compare(later, earlier), 'newer')
        self.assertEqual(live.compare(earlier, later), 'older')

    def test_a_stamp_not_known_leaves_the_access_expiry_to_decide(self):
        a = _login('at2', 'rt2', self.E + 1000, None)
        b = _login('at1', 'rt1', self.E, self.R)
        self.assertEqual(live.compare(a, b), 'newer')
        self.assertIsNone(live.compare(_login('x', 'rt3', self.E, None), _login('y', 'rt4', self.E, self.R)))


class Offline(unittest.TestCase):
    R = 1800000000000

    def doc(self, **stamps):
        return {'accounts': dict((aid, {'id': aid, 'uuid': aid, 'fp': 'fp-' + aid, 'rx_ms': rx})
                                 for aid, rx in stamps.items())}

    def login(self, rx):
        return _login('at-x', 'rt-x', 1790000000000, rx)

    def test_a_stamp_a_little_lower_than_one_saved_accounts_is_that_account(self):
        p = identity.offline(self.login(self.R - 300), self.doc(b=self.R, a=self.R - 3 * DAY_MS), near=True)
        self.assertEqual((p.account_id, p.kind), ('b', 'refresh_expiry'))

    def test_an_exact_stamp_still_names_its_account_beside_a_near_one(self):
        p = identity.offline(self.login(self.R), self.doc(b=self.R, a=self.R - 5000), near=True)
        self.assertEqual((p.account_id, p.kind), ('b', 'refresh_expiry'))

    def test_several_near_accounts_are_told_apart_only_by_the_label_the_caller_passed(self):
        doc = self.doc(a=self.R, b=self.R + 5000)
        login = self.login(self.R - 400)
        self.assertIsNone(identity.offline(login, doc, near=True).account_id)
        self.assertEqual(identity.offline(login, doc, identity_block('b', email_of('b')), near=True).account_id, 'b')
        self.assertEqual(identity.offline(login, doc, identity_block('a', email_of('a')), near=True).account_id, 'a')
        self.assertIsNone(identity.offline(login, doc, identity_block('c', email_of('c')), near=True).account_id)

    def test_a_stamp_later_than_a_saved_accounts_is_never_a_renewal_of_it(self):
        # Claude Code only ever lowers a sign-in's stamp: a later one is another sign-in (one sitting, a minute on)
        p = identity.offline(self.login(self.R + 60000), self.doc(a=self.R), near=True)
        self.assertIsNone(p.account_id)

    def test_a_near_stamp_is_not_proof_when_the_label_names_another_account(self):
        p = identity.offline(self.login(self.R - 20000), self.doc(a=self.R), identity_block('b', email_of('b')),
                             near=True)
        self.assertIsNone(p.account_id)

    def test_reprove_keeps_the_owner_when_the_moved_bytes_are_a_renewal_with_a_lower_stamp(self):
        doc = self.doc(b=self.R, a=self.R - 3 * DAY_MS)
        doc['accounts']['b']['rx_ms'] = self.R - 3 * DAY_MS + 1        # no offline proof for the new bytes
        before = identity.Proof('b', 'profile')
        pre = self.login(self.R)
        cur = _login('at-y', 'rt-y', 1790000500000, self.R - 700)
        p = identity.reprove(cur, doc, None, before, pre)
        self.assertEqual((p.account_id, p.kind), ('b', 'refresh_expiry'))


class RigCase(unittest.TestCase):
    def setUp(self):
        self.rig, self.clock = new_rig()
        self.ctx = self.rig.ctx
        self.server = self.rig.server
        self.R = int((time.time() + 25 * 86400) * 1000)
        self.claude_code_spent = []

    def tearDown(self):
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        self.rig.close()

    def renew(self, move_ms):
        old, new = renew(self.rig, move_ms, self.clock)
        self.claude_code_spent.append(fingerprint(refresh_token(old)))
        return old, new

    def posts_for(self, text):
        """Switchboard's own token POSTs with this login's refresh token (Claude Code's own renewal left out)."""
        fp = fingerprint(refresh_token(text))
        posts = [p for p in self.server.token_posts() if p[2] == fp]
        return posts[1:] if fp in self.claude_code_spent else posts

    def keep(self):
        return capture.keep_pass(self.ctx, force=True)

    def strikes(self):
        return dict((aid, a['strike']) for aid, a in self.rig.doc()['accounts'].items() if a.get('strike'))


class ARenewalThatLoweredTheStamp(RigCase):
    """Saved: token T1, stamp R. Claude Code renews: token T2, stamp R minus a few hundred ms, later access expiry."""

    def setUp(self):
        super().setUp()
        keep_at(self.rig, 'uuid-a', self.R - 3 * DAY_MS)
        self.t1 = keep_at(self.rig, 'uuid-b', self.R)                  # Claude Code is on B
        _, self.t2 = self.renew(-300)
        self.assertLess(live.parse(self.t2).refresh_expires_ms, self.R)

    def test_one_keep_pass_saves_what_claude_code_renewed(self):
        self.assertEqual(self.keep(), 'Saved the newer login Claude Code made for uuid-b@mail.example')
        self.assertEqual(self.rig.vault('uuid-b'), self.t2)
        self.assertNotIn('Kept the new sign-in', ' '.join(e[0] for e in self.rig.events))

    def test_upkeep_after_it_sends_nothing_for_the_account_in_use(self):
        self.keep()
        self.clock.advance(7 * 3600)
        self.renew(-250)                                               # Claude Code renews again while in use
        tokens.upkeep_pass(self.ctx, budget_s=120)
        self.assertEqual(self.posts_for(self.t1), [])
        self.assertEqual(self.posts_for(self.t2), [])
        self.assertEqual(self.strikes(), {})

    def test_upkeep_before_the_keep_pass_never_spends_the_stale_saved_copy(self):
        self.clock.advance(7 * 3600)
        res = tokens.refresh(self.ctx, 'uuid-b', budget_s=60, when_due=False)
        self.assertEqual((res.outcome, res.why), ('skipped', 'live_same_login'))
        tokens.upkeep_pass(self.ctx, budget_s=120)
        self.assertEqual(self.posts_for(self.t1), [])
        self.assertEqual(self.strikes(), {})
        self.assertNotIn('needs sign-in', ' '.join(e[0] for e in self.rig.events))

    def test_a_real_new_sign_in_is_still_kept_as_a_new_sign_in(self):
        self.keep()
        self.clock.advance(3600)
        new = sign_in_at(self.rig, 'uuid-b', self.R + 3600 * 1000)
        self.assertEqual(self.keep(), 'Kept the new sign-in of uuid-b@mail.example')
        self.assertEqual(self.rig.vault('uuid-b'), new)


class WhatIsSaidAndShown(RigCase):
    def setUp(self):
        super().setUp()
        keep_at(self.rig, 'uuid-a', self.R - 3 * DAY_MS)
        self.t1 = keep_at(self.rig, 'uuid-b', self.R)                  # Claude Code is on B
        _, self.t2 = self.renew(-300)

    def test_replacing_an_older_saved_copy_of_the_same_sign_in_is_logged_once_with_fingerprints(self):
        self.keep()
        lines = [x for x in self.rig.logs if 'older than Claude Code\'s login of the same sign-in' in x]
        self.assertEqual(len(lines), 1, self.rig.logs)
        self.assertIn(live.parse(self.t1).fingerprint, lines[0])
        self.assertIn(live.parse(self.t2).fingerprint, lines[0])
        self.assertIn('0.3 s earlier', lines[0])
        for t in (self.t1, self.t2):
            self.assertNotIn(refresh_token(t), '\n'.join(self.rig.logs))
            self.assertNotIn(json.loads(t)['claudeAiOauth']['accessToken'], '\n'.join(self.rig.logs))
        self.assertIsNone(self.keep())
        self.assertEqual(len([x for x in self.rig.logs if 'of the same sign-in' in x]), 1)

    def test_the_saved_date_is_the_one_claude_code_holds_and_its_words_stay_within_a_minute(self):
        from switchboard import term
        self.keep()
        held = live.parse(self.rig.live_text()).refresh_expires_ms
        doc = self.rig.doc()
        self.assertEqual(doc['accounts']['uuid-b']['rx_ms'], held)
        shown = vault.to_account(self.ctx, doc, 'uuid-b').refresh_expires_at
        self.assertEqual(shown, held / 1000.0)
        now = self.R / 1000.0 - 2 * 86400
        self.assertEqual(term.signin_words(shown, now), term.signin_words(held / 1000.0, now))
        self.assertLess(abs(shown - self.R / 1000.0), 60)
        self.assertTrue(term.signin_words(shown, now).startswith('Sign-in ends by '))


class WhoseItIsWithoutTheStamp(RigCase):
    def test_a_renewal_proved_by_anthropic_is_saved_as_claude_codes_newer_login(self):
        keep_at(self.rig, 'uuid-a', self.R)
        keep_at(self.rig, 'uuid-b', self.R + 5000)
        _, t2 = self.renew(-9000)                     # now near both stamps
        self.rig.set_config(None)                                      # and no label to tell them apart
        self.assertEqual(self.keep(), 'Saved the newer login Claude Code made for uuid-b@mail.example')
        self.assertEqual(self.rig.vault('uuid-b'), t2)
        self.assertEqual(json.loads(self.rig.vault('uuid-a'))['claudeAiOauth']['refreshToken'],
                         self.server.valid_refresh('uuid-a'))


class OneSitting(RigCase):
    def test_an_account_signed_in_a_minute_after_another_is_kept_as_its_own(self):
        a = keep_at(self.rig, 'uuid-a', self.R)
        self.clock.advance(60)
        b = sign_in_at(self.rig, 'uuid-b', self.R + 60000)
        self.assertEqual(self.keep(), 'Kept uuid-b@mail.example · 2 accounts')
        self.assertEqual(self.rig.vault('uuid-a'), a)
        self.assertEqual(self.rig.vault('uuid-b'), b)

    def test_a_login_seen_late_with_a_stamp_just_under_another_accounts_is_not_given_to_it(self):
        a = keep_at(self.rig, 'uuid-a', self.R)
        b = sign_in_at(self.rig, 'uuid-b', self.R - 20000)              # the settings name B
        self.assertEqual(self.keep(), 'Kept uuid-b@mail.example · 2 accounts')
        self.assertEqual(self.rig.vault('uuid-a'), a)
        self.assertEqual(self.rig.vault('uuid-b'), b)


class NeverSpendTheAccountInUse(RigCase):
    """tokens._held_elsewhere, row by row, counting what reached the stand-in for Anthropic."""

    def setUp(self):
        super().setUp()
        self.asks = count_profile_asks(self.server)

    def twins(self, gap_ms=5000):
        self.a = keep_at(self.rig, 'uuid-a', self.R)
        self.b = keep_at(self.rig, 'uuid-b', self.R + gap_ms)          # Claude Code is on B
        del self.asks[:]                                               # (the page's Add asked whose each was)

    def held(self, aid):
        lr = vault.read_current(self.ctx, aid, fresh=True)
        return tokens._held_elsewhere(self.ctx, live.parse(lr.value), aid)

    def test_an_unreadable_live_login_is_held_as_before(self):
        self.twins()
        self.rig.kc.rule('read', service=self.rig.service, why='timeout', times=-1)
        self.assertEqual(self.held('uuid-b'), 'live_unreadable')
        self.assertEqual(self.posts_for(self.b), [])

    def test_the_same_refresh_token_is_held_as_before(self):
        self.twins()
        self.assertEqual(self.held('uuid-b'), 'live')

    def test_a_renewal_proved_offline_to_be_this_accounts_is_held(self):
        self.twins()
        self.renew(-400)
        self.assertEqual(self.held('uuid-b'), 'live_same_login')
        self.assertEqual(self.asks, [])
        res = tokens.refresh(self.ctx, 'uuid-b', budget_s=60, when_due=False)
        self.assertEqual((res.outcome, res.why), ('skipped', 'live_same_login'))
        self.assertEqual(self.posts_for(self.b), [])

    def test_a_cached_profile_answer_for_the_live_login_is_enough(self):
        self.twins()
        _, t2 = self.renew(-9000)                     # near both stamps
        self.rig.set_config(None)
        self.assertEqual(identity.lookup_profile(self.ctx, json.loads(t2)['claudeAiOauth']['accessToken']).status,
                         'resolved')
        asked = len(self.asks)
        self.assertEqual(self.held('uuid-b'), 'live_same_login')
        self.assertEqual(len(self.asks), asked)

    def test_a_live_login_proved_by_fingerprint_to_be_another_accounts_lets_this_one_be_renewed(self):
        self.twins()
        self.assertIsNone(self.held('uuid-a'))
        self.rig.set_saved_expiry('uuid-a', time.time() - 60)
        res = tokens.refresh(self.ctx, 'uuid-a', budget_s=60)
        self.assertEqual(res.outcome, 'refreshed', res)
        self.assertEqual(len(self.posts_for(self.a)), 1)
        self.assertEqual(self.posts_for(self.b), [])

    def test_an_owner_not_proved_with_near_stamps_is_asked_of_anthropic_once(self):
        self.twins()
        self.renew(-9000)
        self.rig.set_config(None)
        self.assertEqual(self.held('uuid-b'), 'live_same_login')
        self.assertEqual(len(self.asks), 1)
        identity._PROFILE_CACHE.clear()
        self.assertIsNone(self.held('uuid-a'))                         # Anthropic says it is B's: A may be renewed
        self.assertEqual(len(self.asks), 2)

    def test_an_owner_that_cannot_be_proved_is_held_and_waits(self):
        self.twins()
        self.renew(-9000)
        self.rig.set_config(None)
        self.server.script.append(('profile', net.NetError('timeout', True, 'no answer')))
        self.server.script.append(('profile', net.NetError('timeout', True, 'no answer')))
        self.assertEqual(self.held('uuid-b'), 'live_unsure')
        self.assertIn('live_unsure', tokens.HELD_ELSEWHERE)
        self.rig.set_saved_expiry('uuid-b', time.time() - 60)
        self.clock.advance(7 * 3600)
        tokens.upkeep_pass(self.ctx, budget_s=120)
        self.assertEqual(self.posts_for(self.b), [])
        u = self.rig.doc()['upkeep']['uuid-b']
        self.assertEqual(u['last_outcome'], 'skipped')
        self.assertAlmostEqual(u['next_at'] - self.clock.wall(), tokens.SKIP_WAIT_S, delta=60)

    def test_far_stamps_and_settings_naming_this_account_are_asked_of_anthropic(self):
        self.a = keep_at(self.rig, 'uuid-a', self.R - 3 * DAY_MS)
        self.b = keep_at(self.rig, 'uuid-b', self.R)
        self.clock.advance(60)
        sign_in_at(self.rig, 'uuid-b', self.R + 2 * DAY_MS)             # Claude Code now on a newer sign-in of B
        del self.asks[:]
        self.assertEqual(self.held('uuid-b'), 'live_same_login')
        self.assertEqual(len(self.asks), 1)

    def lose_login(self, connectors=True):
        """Claude Code lost its login: the item gone, or saved again holding connector state only."""
        self.rig.sec.drop(self.rig.service, self.rig.account)
        if connectors:
            self.rig.set_live(json.dumps({'mcpOAuth': {'made-up': {'expiresAt': 1}}}, separators=(',', ':')))

    def test_a_lost_login_whose_settings_name_this_account_is_held_and_never_spent(self):
        for connectors in (True, False):
            with self.subTest(connectors=connectors):
                self.tearDown()
                self.setUp()
                self.a = keep_at(self.rig, 'uuid-a', self.R - 3 * DAY_MS)
                self.b = keep_at(self.rig, 'uuid-b', self.R)           # Claude Code is on B
                self.lose_login(connectors)
                self.assertEqual(self.held('uuid-b'), 'live_missing')
                self.assertIn('live_missing', tokens.HELD_ELSEWHERE)
                self.assertIsNone(self.held('uuid-a'))                 # the settings name B: A may be renewed
                self.rig.set_saved_expiry('uuid-b', time.time() + 60)
                tokens.upkeep_pass(self.ctx, budget_s=120)
                self.assertEqual(self.posts_for(self.b), [])

    def test_a_chosen_sign_out_leaves_the_saved_login_free_to_renew(self):
        self.a = keep_at(self.rig, 'uuid-a', self.R - 3 * DAY_MS)
        self.b = keep_at(self.rig, 'uuid-b', self.R)
        self.lose_login(connectors=False)
        self.rig.set_config(None)                                      # /logout clears the settings' account
        self.assertIsNone(self.held('uuid-b'))

    def test_far_stamps_and_settings_naming_another_account_go_on_without_asking(self):
        self.a = keep_at(self.rig, 'uuid-a', self.R - 3 * DAY_MS)
        self.b = keep_at(self.rig, 'uuid-b', self.R)
        sign_in_at(self.rig, 'uuid-c', self.R + DAY_MS)                 # a login not kept yet, of another account
        del self.asks[:]
        self.assertIsNone(self.held('uuid-a'))
        self.assertEqual(self.asks, [])


class ASuccessorLeftBehind(RigCase):
    def test_a_successor_whose_stamp_moved_is_saved_instead_of_spending_anything(self):
        keep_at(self.rig, 'uuid-a', self.R)
        keep_at(self.rig, 'uuid-b', self.R + 3 * DAY_MS)               # Claude Code is on B; A is idle
        saved = self.rig.vault('uuid-a')
        self.clock.advance(60)
        nxt = self.server.refresh_for(saved)                           # the answer to a refresh that was cut off
        nxt = moved(nxt, -600)
        self.rig.kc.put(vault.SERVICE_RECOVERY, 'successor:uuid-a', nxt)
        with vault.changing(self.ctx) as doc:
            doc['refreshing']['uuid-a'] = {'fp': live.parse(saved).fingerprint, 'at': time.time(), 'reason': 'x'}
        posts = len(self.server.token_posts())
        res = tokens.refresh(self.ctx, 'uuid-a', budget_s=60, when_due=False)
        self.assertEqual((res.outcome, res.why), ('adopted', 'successor saved'))
        self.assertEqual(self.rig.vault('uuid-a'), nxt)
        self.assertEqual(len(self.server.token_posts()), posts)


class ACutOffSwitch(RigCase):
    def journal(self, orig, target, to_id, from_id):
        o, t = live.parse(orig), live.parse(target)
        switcher._write_journal(self.ctx, {
            'id': 'x', 'pid': -1, 'at': time.time(), 'from_id': from_id, 'to_id': to_id, 'kind': 'manual',
            'source': 'cli', 'step': 'credential_written', 'orig_fp': o.fingerprint, 'orig_rx': o.refresh_expires_ms,
            'orig_text_fp': o.text_fp, 'orig_label': None, 'target_fp': t.fingerprint,
            'target_rx': t.refresh_expires_ms, 'written_fp': t.fingerprint, 'locks': []})

    def test_the_target_renewed_with_a_moved_stamp_finishes_the_switch(self):
        a = keep_at(self.rig, 'uuid-a', self.R - 3 * DAY_MS)
        b = keep_at(self.rig, 'uuid-b', self.R)
        self.rig.set_live(a)                                           # the switch to A wrote A
        self.journal(b, a, 'uuid-a', 'uuid-b')
        self.renew(-400)                               # and Claude Code renewed it
        note = switcher.resolve_journal(self.ctx)
        self.assertEqual(note, 'Repaired the cut-off switch: Claude Code is on uuid-a@mail.example')

    def test_with_near_stamps_a_moved_stamp_alone_never_decides_which_side_it_is(self):
        a = keep_at(self.rig, 'uuid-a', self.R)
        b = keep_at(self.rig, 'uuid-b', self.R + 5000)
        self.journal(b, a, 'uuid-a', 'uuid-b')
        self.renew(-400)                               # B renewed: near A's stamp and B's
        note = switcher.resolve_journal(self.ctx)
        self.assertTrue(note.startswith('Put away the record of a switch'), note)
        self.assertNotEqual(self.rig.doc().get('active'), 'uuid-a')


class ARunAs(unittest.TestCase):
    def test_a_renewal_inside_a_run_with_a_moved_stamp_is_the_seeded_lineage(self):
        r = 1800000000000
        claim = {'seeded_fp': fingerprint('rt-1'), 'seeded_rx': r}
        self.assertTrue(runas._lineage(_login('at2', 'rt-2', 1790000000000, r - 500), claim))
        self.assertFalse(runas._lineage(_login('at3', 'rt-3', 1790000000000, r + 3 * DAY_MS), claim))

    def test_a_sign_in_inside_a_run_whose_stamp_is_a_little_later_is_not_the_seeded_lineage(self):
        r = 1800000000000
        claim = {'seeded_fp': fingerprint('rt-1'), 'seeded_rx': r}
        self.assertFalse(runas._lineage(_login('at4', 'rt-4', 1790000000000, r + 30000), claim))   # a renewal never
        self.assertTrue(runas._lineage(_login('at5', 'rt-5', 1790000000000, r), claim))            # raises the stamp


if __name__ == '__main__':
    unittest.main()
