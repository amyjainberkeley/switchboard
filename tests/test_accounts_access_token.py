"""api.access_token, the join between the account engine and the usage reader.

The active account's token is Claude Code's and is never refreshed by us; another account's is refreshed only through
the engine's transaction, when it has lapsed, or once per half hour when the usage endpoint refused it."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import time
import unittest

from fakes_accounts import Rig
from switchboard.accounts import api, context, facts, live, roster, tokens, usage


class TokenCase(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        self.a_text, self.b_text = self.rig.two_accounts()          # Claude Code is on B; A is an idle saved login
        tokens.WAKE.reset()
        roster._FORCED.clear()

    def tearDown(self):
        self.use.__exit__(None, None, None)
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        self.rig.close()

    def posts(self):
        return [r for r in self.rig.server.requests if r[1] == facts.TOKEN_URL]

    def access(self, text):
        return json.loads(text)['claudeAiOauth']['accessToken']


class TheActiveAccount(TokenCase):
    def test_its_token_is_claude_codes_own_and_nothing_is_spent(self):
        got = api.access_token('uuid-b')
        self.assertEqual((got.kind, got.value), (api.PRESENT, self.access(self.b_text)))
        self.assertEqual(self.posts(), [])

    def test_a_lapsed_token_is_reported_expired_and_never_refreshed_by_us(self):
        blob = json.loads(self.rig.live_text())
        blob['claudeAiOauth']['expiresAt'] = int((time.time() - 60) * 1000)
        self.rig.set_live(json.dumps(blob, separators=(',', ':')))
        roster.forget_live()
        got = api.access_token('uuid-b', fresh=True)
        self.assertEqual((got.kind, got.why), (api.UNREADABLE, 'expired'))
        self.assertEqual(self.posts(), [])

    def test_a_label_that_disagrees_with_the_login_never_moves_a_token_to_another_account(self):
        cfg = self.rig.config()['oauthAccount']
        cfg.update({'accountUuid': 'uuid-a', 'emailAddress': 'a@example.com', 'organizationUuid': 'org-a'})
        self.rig.set_config(cfg)
        roster.forget_live()
        self.assertNotEqual(api.access_token('uuid-a').value, self.access(self.b_text))   # B's token never read as A's
        got = api.access_token('uuid-b')                                    # and B, whose login it is, is still read
        self.assertEqual((got.kind, got.value), (api.PRESENT, self.access(self.b_text)))


class AnotherAccount(TokenCase):
    def test_a_current_saved_token_is_handed_over_without_a_refresh(self):
        got = api.access_token('uuid-a')
        self.assertEqual((got.kind, got.value), (api.PRESENT, self.access(self.rig.vault('uuid-a'))))
        self.assertEqual(self.posts(), [])

    def test_a_lapsed_saved_token_is_refreshed_once_through_the_transaction(self):
        self.rig.set_saved_expiry('uuid-a', time.time() - 60)
        before = self.rig.vault('uuid-a')
        got = api.access_token('uuid-a')
        self.assertEqual(got.kind, api.PRESENT)
        self.assertEqual(len(self.posts()), 1)
        after = self.rig.vault('uuid-a')
        self.assertNotEqual(after, before)                               # the successor was saved first
        self.assertEqual(got.value, self.access(after))
        self.assertEqual(live.parse(self.rig.live_text()).fingerprint, live.parse(self.b_text).fingerprint)

    def test_a_refused_read_forces_one_refresh_per_half_hour_not_one_per_read(self):
        first = api.access_token('uuid-a', fresh=True)
        self.assertEqual(len(self.posts()), 1)
        second = api.access_token('uuid-a', fresh=True)
        self.assertEqual(len(self.posts()), 1)                           # no storm when the endpoint keeps saying 401
        self.assertEqual(first.value, second.value)
        self.assertEqual(first.value, self.access(self.rig.vault('uuid-a')))

    def test_an_account_whose_login_was_refused_gives_no_token_and_is_struck(self):
        self.rig.set_saved_expiry('uuid-a', time.time() - 60)
        self.rig.server.revoke('uuid-a')
        got = api.access_token('uuid-a')
        self.assertEqual((got.kind, got.why), (api.UNREADABLE, 'expired'))
        self.assertEqual(len(self.posts()), 1)
        self.assertEqual(api.find('uuid-a').status, 'needs_login')
        api.access_token('uuid-a')
        self.assertEqual(len(self.posts()), 1)                           # a struck login is never posted again

    def test_an_unknown_account_gives_no_token(self):
        got = api.access_token('uuid-nobody')
        self.assertEqual((got.kind, got.why), (api.UNREADABLE, 'not_found'))

    def test_when_claude_codes_login_cant_be_read_nothing_is_spent(self):
        self.rig.set_saved_expiry('uuid-a', time.time() - 60)
        self.rig.kc.rule('read', service=self.rig.service, why='locked', times=50)
        got = api.access_token('uuid-a')
        self.assertEqual(got.kind, api.UNREADABLE)
        self.assertEqual(self.posts(), [])


class TheUsageReader(TokenCase):
    def test_the_poller_gets_tokens_from_the_engine(self):
        p = usage.Poller()
        r = p._token('uuid-a', False)
        self.assertEqual((r.kind, r.value), (api.PRESENT, self.access(self.rig.vault('uuid-a'))))


if __name__ == '__main__':
    unittest.main()
