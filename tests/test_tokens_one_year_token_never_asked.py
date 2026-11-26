"""While Claude Code is on a one-year token, that token is never sent to Anthropic to ask whose login it is.

A login with no refresh token can't be renewed, so it holds no sign-in that a renewal of a kept copy could end: the
check before a renewal (tokens._held_by_live) holds nothing for it and asks nothing. So an idle account's kept sign-in
is renewed as usual and its numbers can be read. The account the token is kept for stays held, with nothing asked
either, for half an hour after the switch onto the token (a chat open during the switch may still hold its sign-in);
after that its kept sign-in is renewed and its numbers read too (tokens._in_use). A token put in Claude Code some other
way holds the account the settings name, as before. Made-up *.example accounts and a made-up token on the engine's
stand-ins (tests/fakes_accounts.py).
"""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import time
import unittest

from fakes_accounts import Rig
from switchboard.accounts import bearer, capture, context, facts, putback, roster, switcher, tokens
from switchboard.accounts.api import PRESENT

TOKEN = 'sk-ant-oat01-madeUpOneYearToken-0301-aaaa'


class Clock(object):
    def __init__(self):
        self.jump = 0.0

    def wall(self):
        return time.time() + self.jump

    def mono(self):
        return time.monotonic() + self.jump

    def advance(self, seconds):
        self.jump += seconds


class OnAOneYearToken(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.rig = Rig(wall=self.clock.wall, mono=self.clock.mono)
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        capture.reset()
        putback.reset()
        self.rig.keep('uuid-a', 'a@work.example', 'org-a')
        self.clock.advance(2 * 86400)
        self.rig.keep('uuid-b', 'b@home.example', 'org-b')
        bearer.add(self.ctx, TOKEN, for_id='uuid-a', alias='atok')
        roster.forget_live()
        bid = list(bearer.records(self.rig.doc()))[0]
        self.assertTrue(switcher.switch(self.ctx, bid, kind='manual', source='cli', interactive=True).ok)
        roster.forget_live()
        self.asked = []
        real = self.rig.server.request

        def spy(method, url, headers=None, body=None, timeout_s=10.0):
            if url == facts.PROFILE_URL:
                self.asked.append((headers or {}).get('Authorization', ''))
            return real(method, url, headers=headers, body=body, timeout_s=timeout_s)
        self.rig.server.request = spy

    def tearDown(self):
        self.use.__exit__(None, None, None)
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        self.rig.close()

    def token_sent(self):
        return [a for a in self.asked if TOKEN in a]

    def test_the_idle_accounts_renewal_goes_through_and_the_token_is_never_sent(self):
        self.rig.set_saved_expiry('uuid-b', self.clock.wall() + 60)
        res = tokens.refresh(self.ctx, 'uuid-b', reason='upkeep', when_due=False)
        self.assertEqual(res.outcome, 'refreshed', res.why)
        self.assertEqual(self.token_sent(), [])

    def test_the_idle_accounts_numbers_can_be_read(self):
        self.rig.set_saved_expiry('uuid-b', self.clock.wall() - 60)
        r = roster.access_token(self.ctx, 'uuid-b', fresh=True)
        self.assertEqual(r.kind, PRESENT, r.why)
        self.assertEqual(self.token_sent(), [])

    def test_the_account_the_token_is_for_is_held_with_nothing_asked(self):
        self.rig.set_saved_expiry('uuid-a', self.clock.wall() + 60)
        res = tokens.refresh(self.ctx, 'uuid-a', reason='upkeep', when_due=False)
        self.assertEqual((res.outcome, res.why), ('skipped', 'live_same_account'))
        self.assertEqual(self.asked, [])

    def test_half_an_hour_after_the_switch_the_account_the_token_is_for_is_renewed_and_read(self):
        self.rig.set_saved_expiry('uuid-a', self.clock.wall() - 60)
        self.assertEqual(roster.access_token(self.ctx, 'uuid-a', fresh=True).kind, 'unreadable')   # held before
        self.clock.advance(31 * 60)
        r = roster.access_token(self.ctx, 'uuid-a', fresh=True)
        self.assertEqual(r.kind, PRESENT, r.why)
        self.rig.set_saved_expiry('uuid-a', self.clock.wall() + 60)
        res = tokens.refresh(self.ctx, 'uuid-a', reason='upkeep', when_due=False)
        self.assertEqual(res.outcome, 'refreshed', res.why)
        self.assertEqual(self.token_sent(), [])

    def test_a_token_no_switch_put_there_holds_the_account_its_settings_name(self):
        self.rig.set_live(bearer.login_text(TOKEN + 'b'))         # put in Claude Code by hand: no switch recorded
        roster.forget_live()
        self.clock.advance(31 * 60)
        self.rig.set_saved_expiry('uuid-a', self.clock.wall() + 60)
        res = tokens.refresh(self.ctx, 'uuid-a', reason='upkeep', when_due=False)
        self.assertEqual((res.outcome, res.why), ('skipped', 'live_same_account'))

    def test_upkeep_sends_the_token_nowhere(self):
        for aid in ('uuid-a', 'uuid-b'):
            self.rig.set_saved_expiry(aid, self.clock.wall() + 60)
        tokens.upkeep_pass(self.ctx)
        self.assertEqual(self.token_sent(), [])


if __name__ == '__main__':
    unittest.main()
