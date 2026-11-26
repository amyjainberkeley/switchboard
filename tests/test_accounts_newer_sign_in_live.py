"""The account Claude Code is on gets its newer sign-in at once, through the switch transaction, and keeps it.

Signing in again to the account Claude Code is on (the page's Sign in, or Sign in to all) keeps a new login with a new
end date. When Claude Code's own login is an older sign-in of that same account, the engine puts the new one live
inside the switch: the live login's mark is taken under the locks, checked again just before the write, written with
read-back, undone on a mismatch. Every refusal leaves both logins as they were. Later, the login keeping pass puts the
newest kept sign-in back when a Claude Code session writes the older one back (at most a few times an hour), and when
the older one ends on its date, or Claude Code empties it, with nobody pressing anything.

Made-up accounts on the engine's own fakes (tests/fakes_accounts.py): a fake Keychain, a fake Anthropic, a clock that
moves.
"""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import os
import time
import unittest

from fakes_accounts import Rig, identity_block
from switchboard.accounts import api, capture, context, facts, live, roster, switcher, tokens, vault
from switchboard.accounts.api import ABSENT

D = 86400


class Clock(object):
    def __init__(self):
        self.jump = 0.0

    def wall(self):
        return time.time() + self.jump

    def mono(self):
        return time.monotonic() + self.jump


class Case(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.rig = Rig(wall=self.clock.wall, mono=self.clock.mono)
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        self.a_text = self.rig.keep('uuid-a', 'a@example.com', 'org-a')
        self.n = 0
        capture.reset()

    def tearDown(self):
        self.use.__exit__(None, None, None)
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        self.rig.close()

    def on_b(self, days=3):
        """Claude Code on B, kept, with a sign-in that ends `days` from now."""
        text = self.rig.server.add('uuid-b', 'b@example.com', 'org-b', rx_days=days)
        self.rig.set_live(text)
        self.rig.set_config(identity_block('uuid-b', 'b@example.com', 'org-b'))
        roster.add_current(self.ctx)
        roster.forget_live()
        self.rig.events[:] = []
        return text

    def folder(self, uuid='uuid-b', email='b@example.com', org='org-b'):
        """What the page's sign-in leaves: Claude Code's own new login in a throwaway folder's Keychain item."""
        self.n += 1
        d = os.path.join(self.rig.data, 'logins', 'f%d' % self.n)
        os.makedirs(d, mode=0o700)
        text = self.rig.server.add(uuid, email, org)
        self.rig.kc.put(facts.keychain_service({'CLAUDE_CONFIG_DIR': d}), self.rig.account, text)
        with open(os.path.join(d, '.claude.json'), 'w') as f:
            json.dump({'oauthAccount': identity_block(uuid, email, org)}, f)
        return d, text

    def refresh_token(self, text=None):
        return json.loads(self.rig.live_text() if text is None else text)['claudeAiOauth']['refreshToken']

    def said(self):
        return [e[0] for e in self.rig.events]

    def b(self):
        return next(a for a in api.accounts() if a.id == 'uuid-b')

    def assert_untouched(self, live_text, kept):
        self.assertEqual(self.rig.live_text(), live_text)
        self.assertEqual(self.rig.vault('uuid-b'), kept)
        self.assertEqual(switcher.read_journal(self.ctx)[0].kind, ABSENT)

    def session_writes_back(self, old):
        """A Claude Code session that held the older sign-in renews it and writes it back (its lineage still works)."""
        text = self.rig.server.refresh_for(old)
        self.rig.set_live(text)
        roster.forget_live()
        return text


class TheNewerSignInGoesLive(Case):
    def test_at_once_through_the_switch_and_it_is_said_as_the_same_account(self):
        old = self.on_b(3)
        d, new = self.folder()
        api.add_login(d)
        self.assertEqual(self.refresh_token(), self.refresh_token(new))
        self.assertEqual(self.rig.vault('uuid-b'), new)
        self.assertEqual(self.rig.config()['oauthAccount']['accountUuid'], 'uuid-b')
        self.assertEqual(switcher.read_journal(self.ctx)[0].kind, ABSENT)
        self.assertEqual(api.live().account_id, 'uuid-b')
        self.assertIn('Claude Code is on the new sign-in of b@example.com', self.said())
        self.assertFalse([s for s in self.said() if s.startswith('Switched Claude Code')])
        self.assertNotEqual(self.refresh_token(), self.refresh_token(old))
        self.assertAlmostEqual(self.b().refresh_expires_at, live.parse(new).refresh_expires_ms / 1000.0, delta=1)

    def test_the_mcp_logins_beside_it_stay_as_claude_code_has_them(self):
        old = self.on_b(3)
        blob = json.loads(old)
        blob['mcpOAuth'] = {'server': {'accessToken': 'mcp-made-up'}}
        self.rig.set_live(json.dumps(blob, separators=(',', ':')))
        roster.add_current(self.ctx)
        roster.forget_live()
        d, new = self.folder()
        api.add_login(d)
        now = json.loads(self.rig.live_text())
        self.assertEqual(now['claudeAiOauth']['refreshToken'], self.refresh_token(new))
        self.assertEqual(now.get('mcpOAuth'), {'server': {'accessToken': 'mcp-made-up'}})


class EveryRefusalLeavesBothAsTheyWere(Case):
    def test_the_live_login_changed_between_the_judging_and_the_write(self):
        old = self.on_b(3)
        d, new = self.folder()
        renewed = {}
        reads = []

        def meanwhile(verb, service, account):
            # the second read of B's kept login is the switch's own (the first is add_login's): Claude Code renews its
            # login just then, after the judging
            if verb == 'read' and service == vault.SERVICE_CURRENT and account == 'uuid-b':
                reads.append(1)
                if len(reads) == 2:
                    renewed['text'] = self.rig.server.refresh_for(old)
                    self.rig.kc.put(self.rig.service, self.rig.account, renewed['text'])
        self.rig.kc.on_call = meanwhile
        api.add_login(d)
        self.rig.kc.on_call = None
        self.assertIn('text', renewed)
        self.assert_untouched(renewed['text'], new)
        self.assertTrue([s for s in self.said() if s.startswith('Kept the new sign-in of b@example.com; Claude Code is '
                                                                'not on it yet')], self.said())

    def test_a_read_back_that_does_not_match_is_undone(self):
        old = self.on_b(3)
        d, new = self.folder()
        self.rig.kc.rule('write', service=self.rig.service, why='silent', times=1)    # "done", and nothing kept
        api.add_login(d)
        self.assert_untouched(old, new)
        self.assertTrue([s for s in self.said() if 'undone' in s], self.said())

    def test_an_unreadable_keychain_writes_nothing_and_says_so_plainly(self):
        old = self.on_b(3)
        d, new = self.folder()
        throwaway = facts.keychain_service({'CLAUDE_CONFIG_DIR': d})

        def lock_after_keeping(verb, service, account):
            if verb == 'delete' and service == throwaway:      # the new login is kept: the Keychain locks now
                self.rig.kc.rule('read', service=self.rig.service, why='locked', times=-1)
        self.rig.kc.on_call = lock_after_keeping
        api.add_login(d)
        self.rig.kc.on_call = None
        self.rig.kc.rules[:] = []
        self.assert_untouched(old, new)
        self.assertIn("Kept the new sign-in of b@example.com; Claude Code is not on it yet: Your Mac's login Keychain "
                      "is locked, so Switchboard can't read Claude Code's login. Unlock the Mac and try again; nothing "
                      "was changed.", self.said())

    def test_an_api_key_in_claude_codes_item_is_never_replaced(self):
        self.on_b(3)
        self.rig.set_live('sk-ant-api03-made-up-key')
        roster.forget_live()
        d, new = self.folder()
        api.add_login(d)
        self.assert_untouched('sk-ant-api03-made-up-key', new)
        self.assertIsNone(capture.keep_pass(self.ctx, force=True))
        self.assertEqual(self.rig.live_text(), 'sk-ant-api03-made-up-key')

    def test_a_token_login_in_claude_codes_item_is_never_replaced(self):
        old = self.on_b(3)
        blob = json.loads(old)
        del blob['claudeAiOauth']['refreshToken']                   # a long-lived token: no refresh token to renew
        token = json.dumps(blob, separators=(',', ':'))
        self.rig.set_live(token)
        roster.forget_live()
        d, new = self.folder()
        api.add_login(d)
        self.assert_untouched(token, new)
        capture.keep_pass(self.ctx, force=True)
        self.assertEqual(self.rig.live_text(), token)

    def test_another_accounts_login_live_is_never_touched(self):
        self.on_b(3)
        a_now = self.rig.sign_in('uuid-a', 'a@example.com', 'org-a')    # Claude Code moved to A
        roster.add_current(self.ctx)
        roster.forget_live()
        d, new = self.folder()
        api.add_login(d)
        self.assert_untouched(a_now, new)
        capture.keep_pass(self.ctx, force=True)
        self.assertEqual(self.rig.live_text(), a_now)
        self.assertFalse([s for s in self.said() if 'new sign-in of b@example.com' in s and 'is on' in s])


class TheKeepPassPutsTheNewestBack(Case):
    def put_live(self):
        """B's newer sign-in kept and put live by the page's sign-in."""
        old = self.on_b(3)
        d, new = self.folder()
        api.add_login(d)
        self.assertEqual(self.refresh_token(), self.refresh_token(new))
        self.rig.events[:] = []
        return old, new

    def test_an_older_sign_in_written_back_by_a_session_is_put_right_once_and_never_saved_over_the_newer(self):
        old, new = self.put_live()
        self.session_writes_back(old)
        self.assertEqual(capture.keep_pass(self.ctx, force=True), 'Claude Code is on the new sign-in of b@example.com')
        self.assertEqual(self.refresh_token(), self.refresh_token(new))
        self.assertEqual(self.rig.vault('uuid-b'), new)                 # the older one was never kept over it
        self.assertEqual(self.said().count('Claude Code is on the new sign-in of b@example.com'), 1)
        self.assertIsNone(capture.keep_pass(self.ctx, force=True))      # nothing more to do: nothing more said
        self.assertEqual(self.said().count('Claude Code is on the new sign-in of b@example.com'), 1)
        self.assertTrue([x for x in self.rig.logs if 'older sign-in' in x], self.rig.logs)

    def test_a_session_that_keeps_writing_it_back_wins_after_three_times_in_an_hour(self):
        old, new = self.put_live()
        for _ in range(capture.RENEW_MAX):
            old = self.session_writes_back(old)
            capture.keep_pass(self.ctx, force=True)
            self.assertEqual(self.refresh_token(), self.refresh_token(new))
        old = self.session_writes_back(old)
        first = capture.keep_pass(self.ctx, force=True)
        self.assertEqual(self.rig.live_text(), old)                     # left as it is now
        self.assertIn('keeps going back to an older sign-in of b@example.com', first)
        self.assertIsNone(capture.keep_pass(self.ctx, force=True))      # said once
        self.assertEqual(len([s for s in self.said() if 'keeps going back' in s]), 1)
        self.assertEqual(self.rig.vault('uuid-b'), new)
        self.clock.jump += 3601                                          # an hour later it is put right again
        capture.keep_pass(self.ctx, force=True)
        self.assertEqual(self.refresh_token(), self.refresh_token(self.rig.vault('uuid-b')))
        self.assertEqual(live.parse(self.rig.live_text()).refresh_expires_ms, live.parse(new).refresh_expires_ms)

    def test_the_cap_is_shared_by_every_process_that_runs_the_pass(self):
        old, new = self.put_live()
        for _ in range(capture.RENEW_MAX):
            old = self.session_writes_back(old)
            capture.keep_pass(self.ctx, force=True)
        capture.reset()                                                  # another process (the loop), or a restart
        old = self.session_writes_back(old)
        capture.keep_pass(self.ctx, force=True)
        self.assertEqual(self.rig.live_text(), old)


class WithNobodyPressingAnything(Case):
    def kept_but_not_live(self, days=2):
        """B's newer sign-in kept while Claude Code stays on its older one (the put could not go just then)."""
        old = self.on_b(days)
        d, new = self.folder()
        self.rig.kc.rule('write', service=self.rig.service, why='locked', times=1)
        api.add_login(d)
        self.assert_untouched(old, new)
        self.rig.events[:] = []
        return old, new

    def test_when_the_older_sign_in_ends_on_its_date_claude_code_carries_on_with_the_newer_one(self):
        old, new = self.kept_but_not_live(2)
        self.clock.jump += 3 * D                                         # its date passed; the newer one's has not
        capture.keep_pass(self.ctx, force=True)
        now = live.parse(self.rig.live_text())
        self.assertEqual(now.refresh_expires_ms, live.parse(new).refresh_expires_ms)
        self.assertEqual(api.live().account_id, 'uuid-b')               # the same account, not another one
        self.assertEqual(self.b().status, 'ok')
        self.assertIn('Claude Code is on the new sign-in of b@example.com', self.said())

    def test_when_claude_code_empties_the_older_one_it_carries_on_with_the_newer_one(self):
        old, new = self.kept_but_not_live(2)
        self.clock.jump += 3 * D
        blob = json.loads(old)
        blob['claudeAiOauth'].update({'accessToken': '', 'refreshToken': ''})   # Anthropic refused it: emptied
        self.rig.set_live(json.dumps(blob, separators=(',', ':')))
        roster.forget_live()
        capture.keep_pass(self.ctx, force=True)
        self.assertEqual(live.parse(self.rig.live_text()).refresh_expires_ms, live.parse(new).refresh_expires_ms)
        self.assertEqual(api.live().account_id, 'uuid-b')
        self.assertEqual(api.live().state, api.PRESENT)

    def test_an_emptied_login_with_no_newer_sign_in_kept_is_left_for_auto_switch(self):
        old = self.on_b(2)
        blob = json.loads(old)
        blob['claudeAiOauth'].update({'accessToken': '', 'refreshToken': ''})
        wiped = json.dumps(blob, separators=(',', ':'))
        self.rig.set_live(wiped)
        roster.forget_live()
        # nothing is written; the person is told, once, that the account needs a sign-in
        self.assertEqual(capture.keep_pass(self.ctx, force=True), capture.NEEDS_SIGN_IN % 'b@example.com')
        self.assertIsNone(capture.keep_pass(self.ctx, force=True))
        self.assertEqual(self.rig.live_text(), wiped)
        self.assertEqual(api.live().state, 'wiped')


class NeverTwoRenewersOfOneSignIn(Case):
    """A refresh token works once: two holders of one sign-in that both renew it end it. Once the newer sign-in is in
    Claude Code, Switchboard never renews its kept copy while that account is the one in use: right after the put,
    after Claude Code renewed it, while a session has written the older one back (a session may still hold the newer
    one), and after the keep pass put it back. The put-back itself spends nothing: an expired kept copy goes live as
    it is, and Claude Code renews it."""

    def put_live(self):
        old = self.on_b(3)
        d, new = self.folder()
        api.add_login(d)
        self.assertEqual(self.refresh_token(), self.refresh_token(new))
        return old, new

    def posts(self):
        return len(self.rig.server.token_posts())

    def renew_b(self):
        return tokens.refresh(self.ctx, 'uuid-b', budget_s=60.0, reason='upkeep', when_due=False)

    def assert_not_renewed(self, why):
        before = self.posts()
        res = self.renew_b()
        self.assertEqual((res.outcome, res.why), ('skipped', why))
        self.rig.set_saved_expiry('uuid-b', time.time() + 60)              # due for upkeep now
        self.assertEqual(tokens.upkeep_pass(self.ctx), [])
        self.assertEqual(self.posts(), before)                              # nothing went to Anthropic

    def test_right_after_the_put_and_after_claude_code_renewed_it(self):
        self.put_live()
        self.assert_not_renewed('live')
        self.rig.claude_renews()                                            # Claude Code renews its own
        capture.keep_pass(self.ctx, force=True)
        self.assert_not_renewed('live')

    def test_while_a_session_has_written_the_older_one_back(self):
        old, new = self.put_live()
        self.session_writes_back(old)
        # Anthropic's answer, kept from earlier, already names B for the live login: the check of whose the live login
        # is (tokens._held_by_live) holds it first, in its own word; held all the same
        self.assert_not_renewed('live_same_login')

    def test_after_the_keep_pass_put_it_back(self):
        old, new = self.put_live()
        self.session_writes_back(old)
        capture.keep_pass(self.ctx, force=True)
        self.assertEqual(live.parse(self.rig.live_text()).refresh_expires_ms, live.parse(new).refresh_expires_ms)
        self.assert_not_renewed('live')

    def test_the_put_back_of_a_kept_copy_that_has_run_out_spends_nothing(self):
        old, new = self.put_live()
        self.session_writes_back(old)
        ran_out = self.rig.set_saved_expiry('uuid-b', time.time() - 60)
        before = self.posts()
        capture.keep_pass(self.ctx, force=True)
        self.assertEqual(self.posts(), before)
        self.assertEqual(self.refresh_token(), self.refresh_token(ran_out))      # live as it is: Claude Code renews it

    def test_another_accounts_saved_login_is_still_renewed(self):
        self.put_live()
        self.rig.set_saved_expiry('uuid-a', time.time() + 60)
        before = self.posts()
        self.assertIn('Renewed the saved login of a@example.com', tokens.upkeep_pass(self.ctx))
        self.assertEqual(self.posts(), before + 1)


class TheCapNeverLeavesClaudeCodeWithNoWorkingLogin(Case):
    def test_once_capped_an_emptied_login_is_still_put_right(self):
        old = self.on_b(3)
        d, new = self.folder()
        api.add_login(d)
        for _ in range(capture.RENEW_MAX):
            old = self.session_writes_back(old)
            capture.keep_pass(self.ctx, force=True)
        old = self.session_writes_back(old)
        capture.keep_pass(self.ctx, force=True)
        self.assertEqual(self.rig.live_text(), old)                         # capped: the older one still works
        blob = json.loads(old)
        blob['claudeAiOauth'].update({'accessToken': '', 'refreshToken': ''})   # and now Claude Code empties it
        self.rig.set_live(json.dumps(blob, separators=(',', ':')))
        roster.forget_live()
        capture.keep_pass(self.ctx, force=True)
        self.assertEqual(live.parse(self.rig.live_text()).refresh_expires_ms, live.parse(new).refresh_expires_ms)
        self.assertEqual(api.live().state, api.PRESENT)


if __name__ == '__main__':
    unittest.main()
