import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Upkeep leaves the saved login of the account Claude Code is on alone while Claude Code's own login of it is blanked.
# Claude Code empties both tokens of its login after Anthropic refused a renewal (2.1.329 to 2.1.332, read in its
# program text). The saved copy of that account is then the loop's to deal with: it moves Claude Code to another
# signed-in account, or a new sign-in replaces the login. Upkeep renewing that saved copy behind the loop's back would
# spend it unseen. Stand-ins only (fakes_accounts), with a clock that can be moved; made-up *.example accounts.
import json
import time
import unittest

from fakes_accounts import Rig
from switchboard.accounts import live, tokens


class Clock:
    def __init__(self):
        self.jump = 0.0

    def wall(self):
        return time.time() + self.jump

    def mono(self):
        return time.monotonic() + self.jump


def blanked(text):
    """Claude Code's login after a refused renewal: both tokens emptied, the rest of the item as it was."""
    blob = json.loads(text)
    blob['claudeAiOauth'].update(accessToken='', refreshToken='', expiresAt=0)
    return json.dumps(blob, separators=(',', ':'))


class ABlankedLiveLogin(unittest.TestCase):
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

    def saved(self, aid):
        return live.parse(self.rig.vault(aid))

    def test_of_the_account_in_use_holds_its_saved_login(self):
        self.rig.set_live(blanked(self.b_text))
        self.assertEqual(live.parse(self.rig.live_text()).kind, 'wiped')
        held = tokens._held_elsewhere(self.ctx, self.saved('uuid-b'), 'uuid-b')
        self.assertEqual(held, 'live_dead')
        self.assertIn('live_dead', tokens.HELD_ELSEWHERE)

    def test_upkeep_sends_nothing_for_that_account(self):
        self.rig.set_live(blanked(self.b_text))
        self.rig.set_saved_expiry('uuid-b', self.clock.wall() - 60)        # due for upkeep
        b_fp = self.saved('uuid-b').fingerprint
        before = len(self.server.token_posts())
        self.assertEqual(tokens.refresh(self.ctx, 'uuid-b', budget_s=60).why, 'live_dead')
        tokens.upkeep_pass(self.ctx, budget_s=120)
        self.assertEqual([p for p in self.server.token_posts()[before:] if p[2] == b_fp], [])
        self.assertEqual(self.rig.doc()['upkeep']['uuid-b'].get('next_at') is not None, True)    # rests, as held

    def test_another_account_is_renewed_as_before(self):
        self.rig.set_live(blanked(self.b_text))
        self.rig.set_saved_expiry('uuid-a', self.clock.wall() - 60)
        a_fp = self.saved('uuid-a').fingerprint
        before = len(self.server.token_posts())
        notes = tokens.upkeep_pass(self.ctx, budget_s=120)
        self.assertEqual(notes, ['Renewed the saved login of ana@work.example'])
        self.assertEqual([p[2] for p in self.server.token_posts()[before:]], [a_fp])

    def test_a_blanked_login_while_the_settings_name_another_account_holds_nothing(self):
        self.rig.set_live(blanked(self.b_text))
        self.rig.set_config({'accountUuid': 'uuid-a', 'emailAddress': 'ana@work.example', 'organizationUuid': 'org-a',
                             'organizationName': 'Personal', 'displayName': 'ana'})
        self.assertIsNone(tokens._held_elsewhere(self.ctx, self.saved('uuid-b'), 'uuid-b'))


if __name__ == '__main__':
    unittest.main()
