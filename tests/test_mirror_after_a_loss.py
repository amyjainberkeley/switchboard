import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# A loss while the standing copy is on: Claude Code's item is gone and its settings still name the account. With no
# file beside it, or only the copy of ours, nothing proves a fault (Claude Code's fallback writes the file), and a
# chosen sign-out passes through the same state; so the sign-in is put back once it has been missing SIGN_OUT_S with
# the settings naming the account, and the copy then holds exactly what is in the item, and stays.
# The account engine on its stand-ins (fakes_accounts) with a fake clock; made-up *.example accounts.
import json
import unittest

from hold_fakes import Base
from switchboard.accounts import putback


class Mirror(Base):
    MIRROR = True

    def setUp(self):
        super().setUp()
        self.look(keychain=True)
        self.assertTrue(self.ours())
        self.hold('looked')
        self.login = json.loads(self.rig.live_text())['claudeAiOauth']

    def lost(self, file_too):
        """The item gone (and the file with it, when file_too); the sign-in watch looks at the change, then at its
        pace. Returns the time each look ran (since the loss) and what it answered, until one wrote."""
        self.cc_deletes_item()
        if file_too:
            self.cc_removes()
            self.hold('gone')
        t, seen = 0.15, []
        res = self.look(after=0.15, keychain=True, login_file='gone' if file_too else None)
        while res.action == 'wait' and t < 60:
            seen.append((t, res.action))
            if t < putback.SIGN_OUT_S:
                self.assertIsNone(self.rig.live_text())
            step = res.next_s or 1.0
            t += step
            res = self.look(after=step)
        seen.append((t, res.action))
        return seen


class ItemAndFileGone(Mirror):
    def test_put_back_after_sign_out_s_and_the_copy_follows(self):
        seen = self.lost(file_too=True)
        t, action = seen[-1]
        self.assertEqual(action, 'put_back')
        self.assertGreaterEqual(t, putback.SIGN_OUT_S)
        self.assertLess(t, putback.SIGN_OUT_S + putback.PUT_BACK_AFTER_S + 0.5)
        self.assertEqual(json.loads(self.rig.live_text())['claudeAiOauth'], self.login)
        self.assertEqual(self.file_text(), self.rig.live_text())
        self.assertTrue(self.ours())
        self.look(after=putback.HOLD_TIDY_S + 5, keychain=True)
        self.assertEqual(self.file_text(), self.rig.live_text())      # it stays: the standing copy

    def test_no_file_is_written_before_sign_out_s(self):
        seen = self.lost(file_too=True)
        self.assertEqual(seen[-1][1], 'put_back')
        for t, action in seen[:-1]:
            self.assertEqual(action, 'wait')
            self.assertLess(t, putback.SIGN_OUT_S)


class OnlyTheItemGone(Mirror):
    def test_the_copy_is_promoted_after_sign_out_s_and_stays(self):
        text = self.file_text()
        seen = self.lost(file_too=False)
        t, action = seen[-1]
        self.assertEqual(action, 'promote')
        self.assertGreaterEqual(t, putback.SIGN_OUT_S)
        self.assertEqual(self.rig.live_text(), text)
        self.assertEqual(self.file_text(), text)
        self.assertTrue(self.ours())
        self.look(after=putback.HOLD_TIDY_S + 5, keychain=True)
        self.assertEqual(self.file_text(), self.rig.live_text())
        renewed = self.rig.claude_renews()
        self.look(after=0.5, keychain=True)
        self.assertEqual(self.file_text(), renewed)


class WithTheSettingOff(Base):
    """4df2f46's rule stays with the setting off: the put-back after PUT_BACK_AFTER_S."""

    def test_put_back_after_put_back_after_s(self):
        self.look(keychain=True)
        self.assertIsNone(self.file_text())             # no copy (the stand-in Rig has the setting off)
        self.cc_deletes_item()
        self.look(after=0.15, keychain=True)
        res = self.look(after=putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, 'put_back', res.reason)


if __name__ == '__main__':
    unittest.main()
