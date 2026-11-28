import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# While Claude Code has lost its login and its settings still name an account, that account's saved login is not spent
# by upkeep: Switchboard is about to put it back, and Claude Code's chats still hold its access token for up to 30 s
# (2.1.329 trusts its cached read that long); a renewal would end that token. Stand-ins only (fakes_accounts), with
# a clock that can be moved; made-up *.example accounts.
import json
import os
import time
import unittest

from fakes_accounts import Rig
from switchboard.accounts import live, tokens, vault

MCP = {'mcpOAuth': {'docs-server': {'accessToken': 'mcp-made-up-1', 'expiresAt': 1}}}


class Clock:
    def __init__(self):
        self.jump = 0.0

    def wall(self):
        return time.time() + self.jump

    def mono(self):
        return time.monotonic() + self.jump


class HeldWhileLost(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.rig = Rig(wall=self.clock.wall, mono=self.clock.mono)
        self.ctx = self.rig.ctx
        self.rig.keep('uuid-a', 'ana@work.example', 'org-a')
        self.b_text = self.rig.keep('uuid-b', 'ana@home.example', 'org-b')       # Claude Code is on B
        self.server = self.rig.server

    def tearDown(self):
        deadline = time.time() + 20
        while tokens._INFLIGHT and time.time() < deadline:
            time.sleep(0.05)
        self.rig.close()

    def due(self, aid):
        self.rig.set_saved_expiry(aid, self.clock.wall() - 60)

    def saved(self, aid):
        return live.parse(self.rig.vault(aid))

    def test_each_shape_of_a_loss_holds_the_named_accounts_saved_login(self):
        saved_b = self.saved('uuid-b')
        cred = os.path.join(self.rig.home, '.claude', '.credentials.json')
        shapes = {
            'no item': lambda: self.rig.sec.drop(self.rig.service, self.rig.account),
            'an item without a login': lambda: self.rig.set_live(json.dumps(MCP)),
            'an empty item': lambda: self.rig.set_live(' '),
        }
        for name, lose in shapes.items():
            self.rig.set_live(self.b_text)
            lose()
            self.assertEqual(tokens._held_elsewhere(self.ctx, saved_b, 'uuid-b'), 'live_missing', name)
        self.rig.sec.drop(self.rig.service, self.rig.account)
        with open(cred, 'w') as f:                                   # the login only in Claude Code's plaintext file
            f.write(self.server.refresh_for(self.b_text))
        # Claude Code works from that file: it holds B's renewed login, which is held as any live login of B's is
        self.assertIn(tokens._held_elsewhere(self.ctx, saved_b, 'uuid-b'), tokens.HELD_ELSEWHERE)
        os.unlink(cred)
        self.assertIn('live_missing', tokens.HELD_ELSEWHERE)

    def test_upkeep_sends_nothing_for_that_account_during_a_loss(self):
        self.rig.set_live(json.dumps(MCP))                            # the shape the fault leaves; the settings name B
        self.due('uuid-b')
        before = len(self.server.token_posts())
        self.assertEqual(tokens.refresh(self.ctx, 'uuid-b', budget_s=60).why, 'live_missing')
        tokens.upkeep_pass(self.ctx, budget_s=120)
        b_fp = self.saved('uuid-b').fingerprint
        self.assertEqual([p for p in self.server.token_posts()[before:] if p[2] == b_fp], [])
        self.assertTrue(self.rig.holds_valid('uuid-b'))

    def test_an_account_the_settings_do_not_name_is_renewed_as_before(self):
        self.rig.set_live(json.dumps(MCP))
        self.due('uuid-a')
        a_fp = self.saved('uuid-a').fingerprint
        before = len(self.server.token_posts())
        notes = tokens.upkeep_pass(self.ctx, budget_s=120)
        self.assertEqual(notes, ['Renewed the saved login of ana@work.example'])
        self.assertEqual([p[2] for p in self.server.token_posts()[before:]], [a_fp])

    def test_once_the_loss_is_over_the_account_is_renewed_again(self):
        self.rig.set_live(json.dumps(MCP))
        self.due('uuid-b')
        self.assertEqual(tokens.refresh(self.ctx, 'uuid-b', budget_s=60).why, 'live_missing')
        # Claude Code is back on a login, now A's (B is an idle saved login again)
        self.rig.set_live(self.rig.vault('uuid-a'))
        self.rig.set_config({'accountUuid': 'uuid-a', 'emailAddress': 'ana@work.example', 'organizationUuid': 'org-a',
                             'organizationName': 'Personal', 'displayName': 'ana'})
        self.clock.jump += tokens.SKIP_WAIT_S + 1                     # the wait after a skip, as for any held login
        self.due('uuid-b')
        with vault.changing(self.ctx) as doc:
            doc['upkeep'].get('uuid-b', {}).pop('next_at', None)
        b_fp = self.saved('uuid-b').fingerprint
        before = len(self.server.token_posts())
        res = tokens.refresh(self.ctx, 'uuid-b', budget_s=60)
        self.assertEqual(res.outcome, 'refreshed', res)
        self.assertEqual([p[2] for p in self.server.token_posts()[before:]], [b_fp])

    def test_a_login_present_for_the_account_is_held_as_before(self):
        self.assertEqual(tokens._held_elsewhere(self.ctx, self.saved('uuid-b'), 'uuid-b'), 'live')
        self.assertIsNone(tokens._held_elsewhere(self.ctx, self.saved('uuid-a'), 'uuid-a'))


if __name__ == '__main__':
    unittest.main()
