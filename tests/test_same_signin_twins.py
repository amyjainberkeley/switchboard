"""Twins: two accounts signed in seconds apart, so their sign-ins' stamps are within the same-sign-in window.

The idle one is still renewed by upkeep, the one in use never is, and the keep pass saves what Claude Code renewed
into the right slot, whichever of the two is in use and however far the renewal moved the stamp.
"""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import time
import unittest

from fakes_accounts import identity_block
from same_signin_fakes import email_of, keep_at, new_rig, org_of, refresh_token, renew, sign_in_at
from switchboard.accounts import capture, live, roster, tokens
from switchboard.accounts.live import fingerprint


class Twins(unittest.TestCase):
    GAP_MS = 5000

    def setUp(self):
        self.rig, self.clock = new_rig()
        self.ctx = self.rig.ctx
        self.server = self.rig.server
        self.R = int((time.time() + 25 * 86400) * 1000)

    def tearDown(self):
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        self.rig.close()

    def setup_twins(self, in_use):
        """A, then B five seconds later (a sign-in's stamp follows the order of the sign-ins); then Claude Code is on
        in_use, as after a switch."""
        self.raw = []
        self.server.on_post = self.raw.append
        texts = {'uuid-a': keep_at(self.rig, 'uuid-a', self.R)}
        self.clock.advance(5)
        texts['uuid-b'] = keep_at(self.rig, 'uuid-b', self.R + self.GAP_MS)
        self.rig.set_live(texts[in_use])
        self.rig.set_config(identity_block(in_use, email_of(in_use), org_of(in_use)))
        return 'uuid-a' if in_use == 'uuid-b' else 'uuid-b'

    def run_case(self, in_use, move_ms):
        idle = self.setup_twins(in_use)
        stale = self.rig.vault(in_use)
        _, renewed = renew(self.rig, move_ms, self.clock)
        claude_code_posts = len(self.raw)
        self.rig.set_saved_expiry(idle, self.clock.wall() - 60)
        self.rig.set_saved_expiry(in_use, self.clock.wall() - 60)
        self.clock.advance(7 * 3600)
        tokens.upkeep_pass(self.ctx, budget_s=120)
        ours = self.raw[claude_code_posts:]
        self.assertEqual([rt for rt in ours if rt.startswith('sk-ant-ort01-' + in_use[:8])], [],
                         'the saved login of the account in use was spent')
        self.assertEqual(len(ours), 1, ours)
        self.assertEqual(refresh_token(self.rig.vault(idle)), self.server.valid_refresh(idle))
        self.assertNotIn('strike', self.rig.doc()['accounts'][in_use])
        self.assertEqual(capture.keep_pass(self.ctx, force=True),
                         'Saved the newer login Claude Code made for %s@mail.example' % in_use)
        self.assertEqual(self.rig.vault(in_use), renewed)
        self.assertNotEqual(self.rig.vault(in_use), stale)
        self.assertEqual(refresh_token(self.rig.vault(idle)), self.server.valid_refresh(idle))

    def test_b_in_use_with_a_small_move(self):
        self.run_case('uuid-b', -300)

    def test_b_in_use_moved_below_a(self):
        self.run_case('uuid-b', -9000)

    def test_a_in_use_with_a_small_move(self):
        self.run_case('uuid-a', -300)

    def test_a_in_use_with_a_large_move(self):
        self.run_case('uuid-a', -29000)

    def test_twins_signed_in_one_after_another_are_each_kept_as_their_own(self):
        self.raw = []
        a = sign_in_at(self.rig, 'uuid-a', self.R)
        roster.add_current(self.ctx)
        self.clock.advance(5)
        b = sign_in_at(self.rig, 'uuid-b', self.R + self.GAP_MS)
        self.assertEqual(capture.keep_pass(self.ctx, force=True), 'Kept uuid-b@mail.example · 2 accounts')
        self.assertEqual(self.rig.vault('uuid-a'), a)
        self.assertEqual(self.rig.vault('uuid-b'), b)
        self.assertEqual(live.parse(self.rig.vault('uuid-b')).fingerprint, fingerprint(refresh_token(b)))


if __name__ == '__main__':
    unittest.main()
