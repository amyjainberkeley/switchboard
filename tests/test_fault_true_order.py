import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The night Claude Code signed itself out, in its true order, on the stand-ins with a fake clock. What macOS and Claude
# Code did (measured and read in 2.1.329): two saves of the item at one moment, macOS refused one (exit 45); that
# process wrote its whole object to ~/.claude/.credentials.json and then deleted the Keychain item. With a look at each
# step, as the sign-in watch gives one, the login is back in the Keychain two looks after the delete. In the
# unlucky order, with no look in between, the file is deleted too and the next save writes an item without the login:
# then the saved login is put back after two looks 0.5 s apart when the watch saw the file appear, and after 4 s when it
# did not. Made-up *.example accounts.
import json
import os
import unittest

from test_fault_mode import MCP, Base
from switchboard.accounts import live, putback


class TheNight(Base):
    def claude_code_object(self):
        """The object a Claude Code process holds and saves: the item as it read it, with its own part changed."""
        blob = json.loads(self.rig.live_text())
        blob['discoveryState'] = {'made-up': 2}
        return json.dumps(blob, separators=(',', ':'))

    def test_with_a_look_at_each_step_the_login_is_back_at_the_second_look_after_the_delete(self):
        saved_before = self.rig.vault('uuid-b')
        mine = self.claude_code_object()
        # a save refused by macOS: nothing changed in the item
        self.assertEqual(self.watch(0.05).action, 'leave')
        # Claude Code's fallback, first half: its whole object to the plaintext file
        with open(self.cred, 'w') as f:
            f.write(mine)
        os.chmod(self.cred, 0o600)
        res = self.watch(0.01, keychain=False, login_file='appeared')
        self.assertEqual(res.action, 'leave')                              # the item still holds the login
        self.assertTrue(self.has_login())
        # second half, about 120 ms later: it deletes the Keychain item
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.assertEqual(self.watch(0.12).action, 'wait')                 # never at the first look
        res = self.watch(putback.FAULT_AFTER_S)
        self.assertEqual((res.action, res.account), ('promote', 'ana@home.example'), res.reason)
        self.assertEqual(self.rig.live_text(), mine)
        self.assertEqual(json.loads(self.rig.live_text())['mcpOAuth'], MCP['mcpOAuth'])     # connector logins intact
        self.assertTrue(os.path.exists(self.cred))              # left for now; a later look takes it away
        self.watch(putback.HOLD_TIDY_S)
        self.assertFalse(os.path.exists(self.cred))
        now_saved = live.parse(self.rig.vault('uuid-b'))
        self.assertIn(live.compare(now_saved, live.parse(saved_before)), ('same', 'newer'))
        self.assertEqual(len(self.repairs()), 1)
        self.assertEqual(self.told, [])
        for _ in range(10):                                                # the next looks change nothing
            self.assertEqual(self.watch(1.0).action, 'leave')
        self.assertEqual(len(self.repairs()), 1)

    def unlucky_order(self):
        """Item deleted, file deleted by another process's save that found the item absent, then a save that read
        neither and wrote an item without the account login."""
        mine = self.claude_code_object()
        with open(self.cred, 'w') as f:
            f.write(mine)
        self.rig.sec.drop(self.rig.service, self.rig.account)
        os.unlink(self.cred)
        self.rig.set_live(json.dumps(dict(MCP, discoveryState={'made-up': 3}), separators=(',', ':')))

    def test_the_unlucky_order_is_put_back_after_two_looks_half_a_second_apart_when_the_file_was_seen(self):
        self.unlucky_order()
        first = self.watch(0.2, login_file='appeared')
        self.assertEqual((first.action, first.next_s), ('wait', putback.FAULT_AFTER_S))
        res = self.watch(putback.FAULT_AFTER_S, keychain=False)
        self.assertEqual((res.action, res.account), ('put_back', 'ana@home.example'), res.reason)
        blob = json.loads(self.rig.live_text())
        self.assertEqual(blob['mcpOAuth'], MCP['mcpOAuth'])                 # the item's connector logins, kept
        self.assertEqual(live.parse(self.rig.live_text()).refresh_token,
                         live.parse(self.rig.vault('uuid-b')).refresh_token)
        self.assertEqual(len(self.told), 1)
        self.assertEqual(len(self.repairs()), 1)

    def test_the_unlucky_order_without_the_file_seen_waits_four_seconds(self):
        self.unlucky_order()
        first = self.watch(0.2)
        self.assertEqual((first.action, first.next_s), ('wait', putback.PUT_BACK_AFTER_S))
        self.assertEqual(self.watch(putback.FAULT_AFTER_S).action, 'wait')
        self.assertFalse(self.has_login())
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, 'put_back', res.reason)
        self.assertTrue(self.has_login())


if __name__ == '__main__':
    unittest.main()
