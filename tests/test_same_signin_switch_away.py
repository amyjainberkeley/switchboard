"""A switch away saves the login Claude Code is leaving, also when its renewal moved the sign-in's stamp a little.

Claude Code renews the login in use and stores a stamp up to some seconds lower. Until the keep pass saves it, the
account's saved copy is the older generation, which Anthropic no longer accepts. A switch away in that time must save
the live login into the account's slot, however its owner is known: by fingerprint, by stamp, by Anthropic's profile
answer or by the label. Otherwise the account's only working login is lost.
"""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import time
import unittest

from same_signin_fakes import DAY_MS, keep_at, new_rig, refresh_token, renew
from switchboard.accounts import live, net, switcher, tokens


class SwitchAway(unittest.TestCase):
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

    def accounts(self, gap_ms):
        self.a = keep_at(self.rig, 'uuid-a', self.R)
        self.b = keep_at(self.rig, 'uuid-b', self.R + gap_ms)          # Claude Code is on B

    def switch_to_a_and_check(self, out):
        self.assertNotEqual(self.rig.vault('uuid-b'), out)             # the saved copy is the older generation
        res = switcher.switch(self.ctx, 'uuid-a', source='cli')
        self.assertTrue(res.ok, res)
        self.assertEqual(self.rig.vault('uuid-b'), out)
        self.assertEqual(refresh_token(self.rig.vault('uuid-b')), self.server.valid_refresh('uuid-b'))
        self.assertEqual(refresh_token(self.rig.live_text()), refresh_token(self.a))
        self.assertTrue(self.rig.holds_valid('uuid-b'))

    def test_known_by_fingerprint(self):
        # a renewal that kept the refresh token: one fingerprint, a later access expiry, a lower stamp
        self.accounts(3 * DAY_MS)
        blob = json.loads(self.rig.live_text())
        blob['claudeAiOauth']['expiresAt'] += 3600 * 1000
        blob['claudeAiOauth']['accessToken'] += '-later'
        blob['claudeAiOauth']['refreshTokenExpiresAt'] -= 400
        out = json.dumps(blob, separators=(',', ':'))
        self.rig.set_live(out)
        self.switch_to_a_and_check(out)

    def test_known_by_its_stamp(self):
        self.accounts(3 * DAY_MS)
        _, out = renew(self.rig, -400, self.clock)
        self.switch_to_a_and_check(out)
        said = [x for x in self.rig.logs if 'older than the login in use of the same sign-in' in x]
        self.assertEqual(len(said), 1, self.rig.logs)
        self.assertIn(live.parse(out).fingerprint, said[0])
        self.assertNotIn(refresh_token(out), '\n'.join(self.rig.logs))

    def test_known_by_anthropics_profile_answer(self):
        self.accounts(5000)
        _, out = renew(self.rig, -9000, self.clock)                   # near both stamps
        self.rig.set_config(None)                                     # and no label to tell them apart
        self.switch_to_a_and_check(out)

    def test_known_by_the_label(self):
        # Anthropic can't be asked: a stamp near one account's only, the one the settings name, is that account's
        self.accounts(3 * DAY_MS)
        _, out = renew(self.rig, -400, self.clock)                    # the settings name B
        for _ in range(3):
            self.server.script.append(('profile', net.NetError('timeout', True, 'no answer')))
        self.switch_to_a_and_check(out)

    def test_near_two_accounts_with_no_answer_it_goes_to_the_safety_copy_and_no_slot_changes(self):
        self.accounts(5000)
        _, out = renew(self.rig, -9000, self.clock)                   # near both stamps; the settings name B
        for _ in range(3):
            self.server.script.append(('profile', net.NetError('timeout', True, 'no answer')))
        res = switcher.switch(self.ctx, 'uuid-a', source='cli')
        self.assertTrue(res.ok, res)
        self.assertEqual(self.rig.vault('uuid-b'), self.b)              # not proved: B's slot is not written
        self.assertEqual(refresh_token(self.rig.vault('uuid-a')), refresh_token(self.a))
        self.assertIn(out, list(self.rig.sec.items().values()))         # the safety copy holds it
        said = [x for x in self.rig.logs if 'Did not save the login in use for' in x and 'safety copy' in x]
        self.assertEqual(len(said), 1, self.rig.logs)
        self.assertNotIn(refresh_token(out), '\n'.join(self.rig.logs))

    def test_an_older_copy_in_use_never_goes_over_the_newer_saved_one(self):
        self.accounts(3 * DAY_MS)
        _, newer = renew(self.rig, -400, self.clock)
        self.rig.sec.put('Switchboard login', 'uuid-b', newer)         # saved: the newer generation
        older = self.b
        self.rig.set_live(older)                                       # in use: the older one (a stale copy)
        self.assertEqual(live.compare(live.parse(older), live.parse(newer)), 'older')
        res = switcher.switch(self.ctx, 'uuid-a', source='cli')
        self.assertTrue(res.ok, res)
        self.assertEqual(self.rig.vault('uuid-b'), newer)


if __name__ == '__main__':
    unittest.main()
