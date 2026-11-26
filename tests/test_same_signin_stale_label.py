"""A stamp a little under a kept account's is never enough to write that account's slot.

A renewal by Claude Code can lower a sign-in's stamp a little, so such a stamp may be the account's renewal. But a first
sign-in of another account, made a minute before, sits that close too, and Claude Code's settings can still name the
earlier account (an open session writes its name back after a switch). So the keep pass places such a login by
Anthropic's profile answer, and saves nothing while there is none; the page's Add does the same.
"""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import os
import time
import unittest

from fakes_accounts import identity_block
from same_signin_fakes import DAY_MS, email_of, keep_at, new_rig, org_of, refresh_token, renew, sign_in_at
from switchboard.accounts import capture, facts, identity, live, net, roster, tokens, vault
from switchboard.accounts.live import fingerprint


def no_answer():
    return ('profile', net.NetError('timeout', True, 'no answer'))


class Case(unittest.TestCase):
    def setUp(self):
        self.rig, self.clock = new_rig()
        self.ctx = self.rig.ctx
        self.server = self.rig.server
        self.R = int((time.time() + 25 * 86400) * 1000)
        self.spent = []
        self.n = 0

    def tearDown(self):
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        self.rig.close()

    def keep(self):
        return capture.keep_pass(self.ctx, force=True)

    def label(self, uuid):
        self.rig.set_config(identity_block(uuid, email_of(uuid), org_of(uuid)))

    def accounts(self):
        return sorted(self.rig.doc()['accounts'])

    def renew(self, move_ms):
        old, new = renew(self.rig, move_ms, self.clock)
        self.spent.append(fingerprint(refresh_token(old)))
        return old, new

    def posts_for(self, text):
        fp = fingerprint(refresh_token(text))
        posts = [p for p in self.server.token_posts() if p[2] == fp]
        return posts[1:] if fp in self.spent else posts


class WithoutNear(unittest.TestCase):
    def test_offline_takes_only_a_fingerprint_or_an_exact_stamp_unless_asked_for_near(self):
        r = 1800000000000
        doc = {'accounts': {'a': {'id': 'a', 'uuid': 'a', 'fp': 'fp-a', 'rx_ms': r}}}
        login = live.parse(json.dumps({'claudeAiOauth': {'accessToken': 'at-x', 'refreshToken': 'rt-x',
                                                         'expiresAt': r - DAY_MS, 'refreshTokenExpiresAt': r - 300}}))
        self.assertEqual(identity.offline(login, doc).kind, 'none')
        self.assertEqual(identity.offline(login, doc, identity_block('a', email_of('a'))).kind, 'none')
        self.assertEqual(identity.offline(login, doc, near=True).account_id, 'a')


class StaleLabel(Case):
    """A kept (stamp R); a first sign-in of B (not kept, stamp 20 s under R) is live; the settings still name A."""

    def setUp(self):
        super().setUp()
        self.a = keep_at(self.rig, 'uuid-a', self.R)
        self.b = sign_in_at(self.rig, 'uuid-b', self.R - 20000)
        self.label('uuid-a')

    def test_with_anthropic_answering_b_is_kept_as_b_and_as_slot_is_untouched(self):
        self.assertEqual(self.keep(), 'Kept uuid-b@mail.example · 2 accounts')
        self.assertEqual(self.rig.vault('uuid-a'), self.a)
        self.assertEqual(self.rig.vault('uuid-b'), self.b)

    def test_with_no_answer_nothing_is_saved_and_it_is_said_once(self):
        self.server.script.extend([no_answer(), no_answer()])
        first = self.keep()
        self.assertIn("can't match to an account yet", first or '')
        self.assertIsNone(self.keep())
        self.assertEqual(self.rig.vault('uuid-a'), self.a)
        self.assertEqual(self.accounts(), ['uuid-a'])

    def test_upkeep_meanwhile_never_posts_the_live_login_and_marks_nothing_refused(self):
        self.server.script.append(no_answer())
        self.keep()
        self.clock.advance(7 * 3600)
        tokens.upkeep_pass(self.ctx, budget_s=120)
        self.assertEqual(self.posts_for(self.b), [])
        self.assertEqual([aid for aid, a in self.rig.doc()['accounts'].items() if a.get('strike')], [])
        self.assertEqual(self.accounts(), ['uuid-a'])


class TwinsWithAStaleLabel(Case):
    """A and B kept 5 s apart, Claude Code on B; it renews B with the stamp moved under both; the settings name A."""

    def setUp(self):
        super().setUp()
        self.a = keep_at(self.rig, 'uuid-a', self.R)
        self.b = keep_at(self.rig, 'uuid-b', self.R + 5000)
        _, self.out = self.renew(-9000)
        self.label('uuid-a')

    def test_with_anthropic_answering_b_s_slot_gets_the_renewal_and_a_s_is_untouched(self):
        self.assertEqual(self.keep(), 'Saved the newer login Claude Code made for uuid-b@mail.example')
        self.assertEqual(self.rig.vault('uuid-b'), self.out)
        self.assertEqual(self.rig.vault('uuid-a'), self.a)

    def test_with_no_answer_nothing_is_saved(self):
        self.server.script.extend([no_answer(), no_answer()])
        self.keep()
        self.keep()
        self.assertEqual(self.rig.vault('uuid-b'), self.b)
        self.assertEqual(self.rig.vault('uuid-a'), self.a)


class TheRenewalReplay(Case):
    """Saved: token T1, stamp R. Claude Code renews B: token T2, stamp R minus 300 ms, a later access expiry."""

    def setUp(self):
        super().setUp()
        keep_at(self.rig, 'uuid-a', self.R - 3 * DAY_MS)
        self.t1 = keep_at(self.rig, 'uuid-b', self.R)
        _, self.t2 = self.renew(-300)

    def test_with_no_answer_at_first_the_first_pass_after_an_answer_saves_it(self):
        self.server.script.append(no_answer())
        self.assertNotIn('Saved', self.keep() or '')
        self.assertEqual(self.rig.vault('uuid-b'), self.t1)
        tokens.upkeep_pass(self.ctx, budget_s=120)
        self.assertEqual(self.posts_for(self.t1), [])
        self.assertEqual(self.keep(), 'Saved the newer login Claude Code made for uuid-b@mail.example')
        self.assertEqual(self.rig.vault('uuid-b'), self.t2)
        self.clock.advance(7 * 3600)
        tokens.upkeep_pass(self.ctx, budget_s=120)
        self.assertEqual(self.posts_for(self.t1), [])
        self.assertEqual(self.posts_for(self.t2), [])
        self.assertNotIn('needs sign-in', ' '.join(e[0] for e in self.rig.events))


class NewSignInWithAStaleLabel(Case):
    """B in use signs in again (a new sign-in, not kept yet); the settings name A. B's saved login is not spent."""

    def test_upkeep_asks_whose_the_live_login_is_and_never_posts_bs_saved_token(self):
        keep_at(self.rig, 'uuid-a', self.R - 3 * DAY_MS)
        b = keep_at(self.rig, 'uuid-b', self.R)
        self.clock.advance(3600)
        sign_in_at(self.rig, 'uuid-b', int(self.clock.wall() * 1000) + 30 * DAY_MS)
        self.label('uuid-a')
        self.clock.advance(7 * 3600)
        saved = live.parse(vault.read_current(self.ctx, 'uuid-b', fresh=True).value)
        self.assertEqual(tokens._held_elsewhere(self.ctx, saved, 'uuid-b'), 'live_same_login')
        tokens.upkeep_pass(self.ctx, budget_s=120)
        self.assertEqual(self.posts_for(b), [])
        self.assertEqual(self.rig.vault('uuid-b'), b)


class AddLogin(Case):
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

    def test_with_no_answer_a_new_sign_in_just_under_a_kept_stamp_is_kept_as_the_account_it_names(self):
        a = keep_at(self.rig, 'uuid-a', self.R)
        d, b = self.folder('uuid-b', self.R - 20000)
        self.server.script.append(no_answer())
        acc = roster.add_login(self.ctx, d)
        self.assertEqual(acc.id, 'uuid-b')
        self.assertEqual(self.rig.vault('uuid-a'), a)
        self.assertEqual(self.rig.vault('uuid-b'), b)

    def test_anthropics_answer_is_not_refused_for_a_stamp_just_under_a_kept_accounts(self):
        a = keep_at(self.rig, 'uuid-a', self.R)
        d, b = self.folder('uuid-b', self.R - 20000)
        acc = roster.add_login(self.ctx, d)
        self.assertEqual(acc.id, 'uuid-b')
        self.assertEqual(self.rig.vault('uuid-a'), a)
        self.assertEqual(self.rig.vault('uuid-b'), b)


if __name__ == '__main__':
    unittest.main()
