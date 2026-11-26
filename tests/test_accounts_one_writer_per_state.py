"""Claude Code's own login item has one writer for each state it can be in, and a held copy of the same sign-in is
never put back over Claude Code's login because its end stamp reads a few milliseconds later.

Claude Code stores, after each renewal, refreshTokenExpiresAt = min(the stamp it had, now + the seconds the server
says are left), so two copies of ONE sign-in can carry stamps milliseconds apart, the newer generation with the LOWER
stamp. Different sign-ins of one account are made days apart here, as real ones are. Made-up *.example accounts on
the engine's own stand-ins (tests/fakes_accounts.py): a fake Keychain, a fake Anthropic, a clock that moves.
"""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import os
import time
import unittest

from fakes_accounts import Rig, identity_block
from switchboard.accounts import api, capture, context, facts, live, putback, roster, tokens, vault

MOVE_MS = 3                     # how much lower Claude Code's renewal left the stamp: a few milliseconds


class Clock(object):
    def __init__(self):
        self.jump = 0.0

    def wall(self):
        return time.time() + self.jump

    def mono(self):
        return time.monotonic() + self.jump

    def advance(self, seconds):
        self.jump += seconds


def lowered(text, ms):
    """The same login text with its sign-in's end stamp `ms` lower, as a Claude Code renewal can leave it."""
    blob = json.loads(text)
    blob['claudeAiOauth']['refreshTokenExpiresAt'] = int(blob['claudeAiOauth']['refreshTokenExpiresAt']) - ms
    return json.dumps(blob, separators=(',', ':'))


def emptied(text):
    """What Claude Code leaves after Anthropic refused its login: the login object with its tokens blank."""
    blob = json.loads(text)
    blob['claudeAiOauth'].update({'accessToken': '', 'refreshToken': ''})
    return json.dumps(blob, separators=(',', ':'))


class Case(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.rig = Rig(wall=self.clock.wall, mono=self.clock.mono)
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        capture.reset()
        putback.reset()
        self.rig.keep('uuid-a', 'a@work.example', 'org-a')
        self.clock.advance(2 * 86400)                       # another account's sign-in, days later
        self.held = self.rig.keep('uuid-b', 'b@home.example', 'org-b')     # Claude Code is on B
        roster.forget_live()
        self.rig.events[:] = []

    def tearDown(self):
        self.use.__exit__(None, None, None)
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        self.rig.close()

    def renew_lower(self):
        """Claude Code renews B by itself (new tokens, a later access expiry) and its stamp lands MOVE_MS lower: the
        held copy is now one generation older by its keys and a few milliseconds 'later' by its stamp."""
        self.clock.advance(600)
        text = lowered(self.rig.server.refresh_for(self.rig.live_text()), MOVE_MS)
        self.rig.set_live(text)
        roster.forget_live()
        return text

    def renews(self):
        return [s for s in vault.load(self.ctx).get('switches') or [] if s.get('source') == capture.RENEW]

    def writes(self):
        """Writes of Claude Code's own login item so far."""
        return sum(1 for c in self.rig.kc.calls if c[0] == 'write' and c[1] == self.rig.service)

    def newer_held(self):
        """Days later B is signed in again on the page (the new sign-in is kept and put live), then a Claude Code
        session writes B's older sign-in back. Returns (the older sign-in as written back, the newer one held)."""
        self.clock.advance(3 * 86400)
        d = os.path.join(self.rig.data, 'logins', 'f1')
        os.makedirs(d, mode=0o700)
        new = self.rig.server.add('uuid-b', 'b@home.example', 'org-b')
        self.rig.kc.put(facts.keychain_service({'CLAUDE_CONFIG_DIR': d}), self.rig.account, new)
        with open(os.path.join(d, '.claude.json'), 'w') as f:
            json.dump({'oauthAccount': identity_block('uuid-b', 'b@home.example', 'org-b')}, f)
        api.add_login(d)
        open(os.path.join(d, putback.SIGNIN_OVER), 'w').close()          # the page marks a finished sign-in over
        self.clock.advance(600)
        old = self.rig.server.refresh_for(self.held)                  # B's older sign-in still works: it renews
        self.rig.set_live(old)
        roster.forget_live()
        self.rig.events[:] = []
        return old, new

    def keep(self):
        """The keep pass (the sign-ins work): how many writes of Claude Code's item it made."""
        before = self.writes()
        capture.keep_pass(self.ctx, force=True)
        roster.forget_live()
        return self.writes() - before

    def put_back(self):
        """The put-back: a first look, then one past its wait. (writes it made, the last look's answer)."""
        before = self.writes()
        putback.look(self.ctx)
        self.clock.advance(putback.PUT_BACK_AFTER_S + 1)
        res = putback.look(self.ctx)
        roster.forget_live()
        return self.writes() - before, res

    def live_token(self):
        return json.loads(self.rig.live_text())['claudeAiOauth']['refreshToken']

    def said(self):
        return [e[0] for e in self.rig.events]


class OneSignInHeldAFewMillisecondsLater(Case):
    def test_claude_codes_newer_generation_is_never_written_over(self):
        text = self.renew_lower()
        capture.keep_pass(self.ctx, force=True)
        self.assertEqual(self.rig.live_text(), text)                # nothing written over Claude Code's login
        self.assertEqual(self.renews(), [])
        self.assertFalse([s for s in self.said() if 'new sign-in of' in s], self.said())

    def test_an_emptied_login_of_that_sign_in_gets_no_older_generation_put_back(self):
        text = emptied(self.renew_lower())          # then Anthropic refused it, and Claude Code emptied it
        self.rig.set_live(text)
        roster.forget_live()
        capture.keep_pass(self.ctx, force=True)
        self.assertEqual(self.rig.live_text(), text)                # the spent held copy is not put back
        self.assertEqual(self.renews(), [])


def token(text):
    return json.loads(text)['claudeAiOauth']['refreshToken']


class EachStateHasOneWriter(Case):
    """B is the account Claude Code is on, and a newer sign-in of B (made days later) is held. For each state of Claude
    Code's item: the one that writes it, what it writes (always the newest sign-in held for B), and nobody else."""

    def setUp(self):
        super().setUp()
        self.old, self.new = self.newer_held()

    def test_no_item_at_all_is_the_put_backs_alone(self):
        self.rig.sec.drop(self.rig.service, self.rig.account)
        roster.forget_live()
        self.assertEqual(self.keep(), 0)
        n, res = self.put_back()
        self.assertEqual(res.action, 'put_back', res.reason)
        self.assertGreaterEqual(n, 1)
        self.assertEqual(self.live_token(), token(self.new))
        self.assertEqual(self.keep(), 0)                            # and after it, nothing for the keep pass to do

    def test_an_item_without_a_login_is_the_put_backs_alone(self):
        self.rig.set_live(json.dumps({'mcpOAuth': {'docs': {'accessToken': 'mcp-made-up'}}}, separators=(',', ':')))
        roster.forget_live()
        self.assertEqual(self.keep(), 0)
        n, res = self.put_back()
        self.assertEqual(res.action, 'put_back', res.reason)
        self.assertGreaterEqual(n, 1)
        self.assertEqual(self.live_token(), token(self.new))
        self.assertEqual(self.keep(), 0)

    def test_a_login_emptied_after_a_refusal_is_the_keep_passs_alone(self):
        self.rig.set_live(emptied(self.old))
        roster.forget_live()
        n, res = self.put_back()
        self.assertEqual((n, res.action), (0, 'leave'), res.reason)   # never written over by the put-back
        self.assertGreaterEqual(self.keep(), 1)
        self.assertEqual(self.live_token(), token(self.new))
        self.assertEqual(self.put_back()[0], 0)

    def test_an_older_sign_in_that_still_works_is_the_keep_passs_alone(self):
        n, res = self.put_back()
        self.assertEqual((n, res.action), (0, 'leave'), res.reason)
        self.assertGreaterEqual(self.keep(), 1)
        self.assertEqual(self.live_token(), token(self.new))
        self.assertEqual(self.put_back()[0], 0)

    def test_a_login_nobody_may_write_over_is_left_by_both(self):
        renewed = self.rig.server.refresh_for(self.new)             # the newest sign-in, renewed by Claude Code
        other = self.rig.server.refresh_for(self.rig.vault('uuid-a'))
        stranger = self.rig.server.add('uuid-c', 'c@other.example', 'org-c')
        blob = json.loads(self.new)
        blob['claudeAiOauth'].pop('refreshToken')                   # a token login: it can't be renewed
        cases = (('the newest sign-in, newer than held', renewed, 'uuid-b'), ('another kept account', other, 'uuid-a'),
                 ('an account not kept', stranger, 'uuid-c'), ('an API key', 'sk-ant-api03-made-up', 'uuid-b'),
                 ('a token login', json.dumps(blob, separators=(',', ':')), 'uuid-b'))
        for what, text, uuid in cases:
            with self.subTest(what):
                email = {'uuid-a': 'a@work.example', 'uuid-b': 'b@home.example', 'uuid-c': 'c@other.example'}[uuid]
                self.rig.set_live(text)
                self.rig.set_config(identity_block(uuid, email, 'org-' + uuid[-1]))
                roster.forget_live()
                self.assertEqual(self.keep(), 0)
                self.assertEqual(self.put_back()[0], 0)
                self.assertEqual(self.rig.live_text(), text)


class NoRenewalAfterEitherPut(Case):
    """After the put-back and after the keep pass's put, Switchboard does not renew its copy of the login in use:
    Claude Code renews what it uses, and two renewers of one sign-in end it."""

    def setUp(self):
        super().setUp()
        self.old, self.new = self.newer_held()

    def assert_not_renewed(self):
        held = live.fingerprint(token(self.rig.vault('uuid-b')))
        res = tokens.refresh(self.ctx, 'uuid-b', budget_s=60.0, reason='upkeep', when_due=False)
        self.assertEqual((res.outcome, res.why), ('skipped', 'live'))
        self.rig.set_saved_expiry('uuid-b', time.time() + self.clock.jump + 60)       # due for upkeep now
        tokens.upkeep_pass(self.ctx)
        self.assertEqual([p for p in self.rig.server.token_posts() if p[2] == held], [])    # nothing for B

    def test_after_the_put_back(self):
        self.rig.sec.drop(self.rig.service, self.rig.account)
        roster.forget_live()
        self.assertEqual(self.put_back()[1].action, 'put_back')
        self.assert_not_renewed()

    def test_after_the_keep_pass_put_the_newest_back(self):
        self.rig.set_live(emptied(self.old))
        roster.forget_live()
        self.assertGreaterEqual(self.keep(), 1)
        self.assert_not_renewed()


if __name__ == '__main__':
    unittest.main()
