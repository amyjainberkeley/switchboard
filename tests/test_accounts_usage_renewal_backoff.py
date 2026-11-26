"""A kept account's lapsed access token, asked for by the usage reader every five minutes while Anthropic's token
endpoint keeps failing: the renewal rests as long as the upkeep pass would rest, a 429's Retry-After included, and a
later success ends the rest."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import time
import unittest

from fakes_accounts import Rig
from switchboard.accounts import api, facts, roster, tokens, vault


class Clock(object):
    def __init__(self):
        self.w, self.m = time.time(), 50000.0

    def wall(self):
        return self.w

    def mono(self):
        return self.m

    def sleep(self, s):
        self.advance(s)

    def advance(self, s):
        self.w += s
        self.m += s


class UsageRenewalRests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.rig = Rig(wall=self.clock.wall, mono=self.clock.mono, sleep=self.clock.sleep)
        self.addCleanup(self.rig.close)
        self.rig.keep('uuid-a', 'a@example.com', 'org-a')
        self.clock.advance(10)                      # (a sign-in is told apart by its refresh expiry, set from the clock)
        self.rig.keep('uuid-b', 'b@example.com', 'org-b')   # Claude Code is on B; A is an idle saved login
        roster._FORCED.clear()
        self.rig.set_saved_expiry('uuid-a', self.clock.wall() - 60)
        self.failing = True
        self.headers = {}
        self.posts = 0
        server = self.rig.server
        real = server.request

        def answer(method, url, headers=None, body=None, timeout_s=10.0):
            if url == facts.TOKEN_URL:
                self.posts += 1
                if self.failing:
                    return 429, dict(self.headers), b'{"error": {"type": "rate_limit_error"}}'
            return real(method, url, headers, body, timeout_s)
        server.request = answer

    def tearDown(self):
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)

    def passes(self, n, every=300.0):
        got = None
        for _ in range(n):
            got = roster.access_token(self.rig.ctx, 'uuid-a')
            self.clock.advance(every)
        return got

    def upkeep(self):
        return vault.load(self.rig.ctx)['upkeep'].get('uuid-a') or {}

    def test_two_hours_of_429s_send_a_handful_of_renewals_not_one_per_read(self):
        got = self.passes(24)
        self.assertEqual((got.kind, got.why), (api.UNREADABLE, 'expired'))
        self.assertGreaterEqual(self.posts, 2)
        self.assertLessEqual(self.posts, 5)                 # 15, 30, 60 minutes apart: 4, where it was 24
        self.assertGreater(self.upkeep().get('next_at'), self.clock.wall())

    def test_the_upkeep_pass_sees_the_same_rest(self):
        self.passes(1)
        doc = vault.load(self.rig.ctx)
        self.assertFalse(tokens.due(doc, 'uuid-a', self.clock.wall()))
        self.assertEqual(self.upkeep().get('fails'), 1)

    def test_a_retry_after_is_never_undercut(self):
        self.headers = {'retry-after': '7200'}
        self.passes(1)
        self.assertEqual(self.posts, 1)
        self.assertGreaterEqual(self.upkeep()['next_at'], self.clock.wall() - 300 + 7200)
        self.passes(23)                                     # 115 minutes more, all inside the asked-for wait
        self.assertEqual(self.posts, 1)

    def test_a_later_success_ends_the_rest(self):
        self.passes(3)
        self.assertEqual(self.posts, 1)
        self.failing = False
        self.clock.advance(self.upkeep()['next_at'] - self.clock.wall() + 1)
        got = roster.access_token(self.rig.ctx, 'uuid-a')
        self.assertEqual(got.kind, api.PRESENT)
        self.assertEqual(self.posts, 2)
        u = self.upkeep()
        self.assertNotIn('next_at', u)
        self.assertEqual(u.get('fails'), 0)
        self.passes(3)
        self.assertEqual(self.posts, 2)                     # renewed: nothing more is sent

    def test_no_name_lookup_rests_too(self):
        self.rig.server.dns_ok = False
        self.passes(1)
        self.assertGreater(self.upkeep().get('next_at'), self.clock.wall())
        self.assertEqual(self.posts, 0)


if __name__ == '__main__':
    unittest.main()
