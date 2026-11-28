import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# A sign-out the person chooses is never undone while the standing copy is on, and the copy goes. /logout and `claude
# auth logout` revoke the refresh token, delete Claude Code's item and its plain login file, and clear oauthAccount in
# its settings a moment later (2.1.329, read in its program text). An item gone with no file beside it, or with only
# the copy of ours, is what a sign-out passes through, so nothing is written back until SIGN_OUT_S have passed with the
# settings still naming the account; settings that name nobody take a copy of ours away at once.
# The account engine on its stand-ins (fakes_accounts) with a fake clock; made-up *.example accounts.
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

    def item(self):
        return self.rig.live_text()

    def signs_out(self, file_too=True, settings_after=1.0):
        """Claude Code's sign-out: the item deleted (and the plain login file), then the settings cleared
        settings_after seconds later; the sign-in watch tells the hold and looks at each change, as it does. Returns
        what each look answered."""
        answers = []
        self.cc_deletes_item()
        if file_too:
            self.cc_removes()
            self.hold('gone')
        answers.append(self.look(after=0.15, keychain=True, login_file='gone' if file_too else None))
        t = 0.15
        while t + 1.0 < settings_after:                 # the watch's quiet pace while the loss waits
            answers.append(self.look(after=1.0, keychain=True))
            t += 1.0
            self.assertIsNone(self.item())
        self.clock.advance(settings_after - t)
        self.settings_name(None)
        self.hold('settings')
        answers.append(self.look(after=0.15, settings=True))
        return answers

    def nothing_left_for(self, seconds):
        for _ in range(int(seconds / 5)):
            self.look(after=5.0, keychain=True)
            self.assertIsNone(self.item())
            self.assertIsNone(self.file_text())
        self.assertIsNone(self.note())


class ASignOut(Mirror):
    def test_item_and_file_gone_then_the_settings_a_second_later_is_never_undone(self):
        answers = self.signs_out(file_too=True, settings_after=1.0)
        self.assertNotIn('put_back', [a.action for a in answers])
        self.assertIsNone(self.item())
        self.assertIsNone(self.file_text())
        self.nothing_left_for(putback.TAKE_BACK_S + 10)

    def test_with_the_copy_left_behind_it_goes_when_the_settings_name_nobody(self):
        answers = self.signs_out(file_too=False, settings_after=1.0)
        self.assertNotIn('promote', [a.action for a in answers])
        self.assertIsNone(self.item())
        self.assertIsNone(self.file_text())
        self.assertIsNone(self.note())
        self.nothing_left_for(putback.TAKE_BACK_S + 10)

    def test_the_settings_cleared_nine_seconds_later_is_never_undone(self):
        answers = self.signs_out(file_too=True, settings_after=putback.SIGN_OUT_S - 1.0)
        self.assertNotIn('put_back', [a.action for a in answers])
        self.assertEqual({a.action for a in answers[:-1]}, {'wait'})
        self.nothing_left_for(putback.TAKE_BACK_S + 10)

    def test_with_the_copy_left_behind_the_settings_cleared_nine_seconds_later_is_never_undone(self):
        answers = self.signs_out(file_too=False, settings_after=putback.SIGN_OUT_S - 1.0)
        self.assertNotIn('promote', [a.action for a in answers])
        self.nothing_left_for(putback.TAKE_BACK_S + 10)

    def test_the_settings_alone_cleared_takes_the_copy_away_at_once(self):
        self.settings_name(None)                        # the settings step of a sign-out, the item not deleted yet
        out = self.hold('settings')
        self.assertEqual((out['did'], out['why']), ('removed', 'nobody'))
        self.assertIsNone(self.file_text())
        self.look(after=0.2, settings=True)
        self.assertIsNone(self.file_text())             # the item still there, the settings naming nobody: no copy


if __name__ == '__main__':
    unittest.main()
