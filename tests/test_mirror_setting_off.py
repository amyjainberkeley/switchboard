import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The setting `auto_policy.keep_signin_file` false: the standing copy goes at the first look that finds Claude Code's
# item, and what stays is the sign-in hold's own rule: a copy left after a put-back (or a promote) for the processes
# told "no such item", taken away HOLD_TIDY_S after the item was last known absent. Turned on again, the copy is made
# again at the next look.
# The account engine on its stand-ins (fakes_accounts) with a fake clock; made-up *.example accounts.
import json
import unittest

from hold_fakes import Base
from switchboard.accounts import putback


class Mirror(Base):
    MIRROR = True

    def setting(self, on):
        with open(self.ctx.path('settings.json'), 'w') as f:
            json.dump({'auto_policy': {'keep_signin_file': on}}, f)


class TurnedOff(Mirror):
    def test_the_copy_goes_at_the_next_look(self):
        self.look(keychain=True)
        self.assertTrue(self.ours())
        self.hold('looked')
        self.setting(False)
        self.look(after=1.0, keychain=True)
        self.assertIsNone(self.file_text())
        self.assertIsNone(self.note())
        self.assertEqual(self.hold('looked')['did'], None)

    def test_and_on_again_it_is_made_again(self):
        self.look(keychain=True)
        self.setting(False)
        self.look(after=1.0, keychain=True)
        self.assertIsNone(self.file_text())
        self.setting(True)
        self.look(after=1.0, keychain=True)
        self.assertEqual(self.file_text(), self.rig.live_text())
        self.assertTrue(self.ours())

    def test_a_copy_left_by_a_put_back_keeps_its_hold_tidy_s(self):
        self.look(keychain=True)
        self.cc_deletes_item()
        self.cc_removes()
        self.hold('gone')
        res = self.look(after=0.2, keychain=True, login_file='gone')
        while res.action == 'wait':
            res = self.look(after=res.next_s or 1.0)
        self.assertEqual(res.action, 'put_back', res.reason)
        put_at = self.clock.wall()
        self.assertTrue(self.ours())
        self.setting(False)
        self.look(after=5.0, keychain=True)
        self.assertTrue(self.ours())                    # processes told "no such item" may still read it
        out = self.hold('looked')
        self.assertIn('look_in', out)                   # a look is asked for when its time is over
        self.clock.advance(putback.HOLD_TIDY_S - (self.clock.wall() - put_at) + 0.5)
        self.look(keychain=True)
        self.assertIsNone(self.file_text())

    def test_with_it_off_a_change_of_the_items_other_parts_is_not_followed(self):
        # the hold's own rule (4df2f46): a file of ours follows the login, not the rest of the item
        self.look(keychain=True)
        self.cc_deletes_item()
        self.cc_removes()
        self.hold('gone')
        res = self.look(after=0.2, keychain=True, login_file='gone')
        while res.action == 'wait':
            res = self.look(after=res.next_s or 1.0)
        self.setting(False)
        text = self.file_text()
        blob = json.loads(self.rig.live_text())
        blob['mcpOAuth'] = {'docs-server': {'accessToken': 'mcp-made-up-3', 'expiresAt': 3}}
        self.rig.set_live(json.dumps(blob, separators=(',', ':')))
        self.look(after=1.0, keychain=True)
        self.assertEqual(self.file_text(), text)


if __name__ == '__main__':
    unittest.main()
