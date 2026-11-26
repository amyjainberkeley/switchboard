"""While Claude Code's item holds no login and its settings name a kept account, the renewals that wait are those of
the accounts Claude Code may be on; every other kept account is renewed as on any other day.

After a switch from B to A, a chat open during the switch can write B's name back into Claude Code's settings, and
then the item can be lost (many chats saving at once). The open chats may hold A's sign-in or B's, whatever the
settings say: nobody knows which. So the accounts that wait are the one the settings name, the one Switchboard's own
record says is in use, and the two of the last recorded switch (the one it left and the one it went to), with no time
limit while the login stays lost: an idle chat can bring its sign-in back by itself hours later, and renewing that
sign-in meanwhile would spend its key. Any other kept account no chat can be on: it is renewed, and its numbers can
be read, however long the loss lasts. Settings that name nobody are a sign-out the person chose (the put-back's own
test): nothing waits. Made-up *.example accounts on the engine's stand-ins (tests/fakes_accounts.py).
"""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import time
import unittest

from fakes_accounts import Rig, identity_block
from switchboard.accounts import capture, context, live, putback, roster, switcher, tokens
from switchboard.accounts.api import PRESENT


class Clock(object):
    def __init__(self):
        self.jump = 0.0

    def wall(self):
        return time.time() + self.jump

    def mono(self):
        return time.monotonic() + self.jump

    def advance(self, seconds):
        self.jump += seconds


class ALostLoginRightAfterASwitch(unittest.TestCase):
    """C, A and B kept; Claude Code was on B and was switched to A; a chat wrote B's name back; the item is lost."""

    def setUp(self):
        self.clock = Clock()
        self.rig = Rig(wall=self.clock.wall, mono=self.clock.mono)
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        capture.reset()
        putback.reset()
        self.rig.keep('uuid-c', 'c@third.example', 'org-c')
        self.clock.advance(86400)
        self.rig.keep('uuid-a', 'a@work.example', 'org-a')
        self.clock.advance(2 * 86400)
        self.rig.keep('uuid-b', 'b@home.example', 'org-b')                 # on B
        roster.forget_live()
        self.assertTrue(switcher.switch(self.ctx, 'uuid-a', kind='auto', source='auto', interactive=True).ok)
        self.rig.set_config(identity_block('uuid-b', 'b@home.example', 'org-b'))   # a chat writes B's name back
        self.rig.sec.drop(self.rig.service, self.rig.account)                 # and the item is lost
        roster.forget_live()

    def tearDown(self):
        self.use.__exit__(None, None, None)
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        self.rig.close()

    def hold(self, aid):
        return tokens._held_elsewhere(self.ctx, live.parse(self.rig.vault(aid)), aid)

    def renew(self, aid):
        before = len(self.rig.server.token_posts())
        res = tokens.refresh(self.ctx, aid, budget_s=60.0, reason='upkeep', when_due=False)
        return res, len(self.rig.server.token_posts()) - before

    def test_the_account_in_use_and_the_one_the_settings_name_wait(self):
        for aid in ('uuid-a', 'uuid-b'):
            with self.subTest(aid):
                self.assertEqual(self.hold(aid), 'live_missing')
                res, posts = self.renew(aid)
                self.assertEqual((res.outcome, res.why, posts), ('skipped', 'live_missing', 0))

    def test_an_account_no_chat_can_be_on_is_renewed_and_read(self):
        self.assertIsNone(self.hold('uuid-c'))
        res, posts = self.renew('uuid-c')
        self.assertEqual((res.outcome, posts), ('refreshed', 1), res.why)
        self.rig.set_saved_expiry('uuid-c', self.clock.wall() - 60)
        r = roster.access_token(self.ctx, 'uuid-c', fresh=True)
        self.assertEqual(r.kind, PRESENT, r.why)

    def test_nine_hours_on_the_same(self):
        for _ in range(3):                                     # the put-back leaves this loss alone (a switch just
            putback.look(self.ctx)                             # left B), so it lasts
            self.clock.advance(3 * 3600)
        self.assertIsNone(self.rig.live_text())
        self.assertEqual((self.hold('uuid-a'), self.hold('uuid-b')), ('live_missing', 'live_missing'))
        self.rig.set_saved_expiry('uuid-c', self.clock.wall() - 60)
        r = roster.access_token(self.ctx, 'uuid-c', fresh=True)
        self.assertEqual(r.kind, PRESENT, r.why)
        upkeep = tokens.upkeep_pass(self.ctx)
        self.assertEqual([n for n in upkeep if 'a@work.example' in n or 'b@home.example' in n], [])

    def test_an_item_with_connector_logins_only_is_the_same(self):
        self.rig.set_live(json.dumps({'mcpOAuth': {'docs': {'accessToken': 'mcp-made-up'}}}, separators=(',', ':')))
        roster.forget_live()
        self.assertEqual((self.hold('uuid-a'), self.hold('uuid-b'), self.hold('uuid-c')),
                         ('live_missing', 'live_missing', None))

    def test_a_sign_out_the_person_chose_holds_nothing(self):
        self.rig.set_config(None)
        roster.forget_live()
        for aid in ('uuid-a', 'uuid-b', 'uuid-c'):
            with self.subTest(aid):
                self.assertIsNone(self.hold(aid))
        res, posts = self.renew('uuid-a')
        self.assertEqual((res.outcome, posts), ('refreshed', 1), res.why)

    def test_settings_naming_an_account_not_kept_hold_nothing(self):
        self.rig.set_config(identity_block('uuid-x', 'x@other.example', 'org-x'))
        roster.forget_live()
        self.assertIsNone(self.hold('uuid-a'))


if __name__ == '__main__':
    unittest.main()
