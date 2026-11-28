import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The uninstaller's tidy (signin_watch --tidy -> putback.hold_uninstall) and the standing copy. The copy goes, unless
# Claude Code's Keychain says its item is not there: then the copy is the only sign-in Claude Code has, and it stays.
# A Keychain that does not answer is not "not there": the read is tried again, and when it still gives no answer the
# copy and its note stay, and a line says to run the uninstaller again (a copy left without its note would be a login
# in plain text nobody knows is Switchboard's).
# The account engine on its stand-ins (r17_engine_fakes) with a fake clock; made-up *.example accounts.
import io
import unittest
from contextlib import redirect_stdout

from hold_fakes import Base
from switchboard.accounts import putback
from switchboard.accounts import signin_watch as sw


class Mirror(Base):
    MIRROR = True

    def setUp(self):
        super().setUp()
        self.look(keychain=True)
        self.assertTrue(self.ours())
        self.text = self.file_text()


class TheUninstallersTidy(Mirror):
    def test_removes_the_copy_beside_the_item(self):
        lines = putback.hold_uninstall(self.ctx)
        self.assertIsNone(self.file_text())
        self.assertIsNone(self.note())
        self.assertTrue(any('Removed' in x for x in lines), lines)

    def test_leaves_the_copy_when_the_item_is_not_there(self):
        self.cc_deletes_item()
        lines = putback.hold_uninstall(self.ctx)
        self.assertEqual(self.file_text(), self.text)
        self.assertTrue(any('missing' in x for x in lines), lines)

    def test_settings_that_cant_be_read_are_no_proof_the_copy_is_no_sign_in(self):
        # the item is gone: the copy is the only sign-in; a moment of a half-written settings file must not remove it
        self.cc_deletes_item()
        with open(self.rig.config_path(), 'w') as f:
            f.write('{"oauthAccount": ')
        putback.hold_uninstall(self.ctx)
        self.assertEqual(self.file_text(), self.text)

    def test_saved_accounts_that_cant_be_read_are_no_proof_either(self):
        self.cc_deletes_item()
        from switchboard.accounts import vault
        with open(vault.store_path(self.ctx), 'w') as f:
            f.write('not json')
        putback.hold_uninstall(self.ctx)
        self.assertEqual(self.file_text(), self.text)

    def test_settings_that_name_nobody_remove_it_without_asking(self):
        self.settings_name(None)
        self.rig.kc.rule('read', self.rig.service, why='timeout', times=-1)
        lines = putback.hold_uninstall(self.ctx)
        self.assertIsNone(self.file_text())
        self.assertTrue(any('Removed' in x for x in lines), lines)

    def test_a_keychain_that_does_not_answer_keeps_the_copy_and_its_note(self):
        self.rig.kc.rule('read', self.rig.service, why='timeout', times=-1)
        lines = putback.hold_uninstall(self.ctx)
        self.assertEqual(self.file_text(), self.text)
        self.assertTrue(self.ours())                          # the note still knows it: a second run can finish
        self.assertTrue(any('run' in x.lower() and 'again' in x for x in lines), lines)
        self.assertFalse(any('missing' in x for x in lines), lines)

    def test_a_read_that_answers_on_its_second_try_removes_the_copy(self):
        self.rig.kc.rule('read', self.rig.service, why='timeout', times=1)
        lines = putback.hold_uninstall(self.ctx)
        self.assertIsNone(self.file_text())
        self.assertIsNone(self.note())
        self.assertTrue(any('Removed' in x for x in lines), lines)

    def test_a_locked_keychain_is_not_taken_for_a_missing_item_either(self):
        self.rig.kc.rule('read', self.rig.service, why='locked', times=-1)
        lines = putback.hold_uninstall(self.ctx)
        self.assertEqual(self.file_text(), self.text)
        self.assertTrue(self.ours())
        self.assertFalse(any('missing' in x for x in lines), lines)


class TheTidyCommand(Mirror):
    # `python -m switchboard.accounts.signin_watch --tidy`, as the uninstaller runs it: its exit code says when the
    # copy was kept because the Keychain gave no answer, so the uninstaller can stop before it removes the program
    def tidy(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = sw.main(['--tidy'])
        return code, out.getvalue()

    def test_exits_again_when_the_keychain_gives_no_answer(self):
        self.rig.kc.rule('read', self.rig.service, why='timeout', times=-1)
        code, said = self.tidy()
        self.assertEqual(code, sw.EXIT_AGAIN)
        self.assertEqual(sw.EXIT_AGAIN, 3)
        self.assertIn('Run the uninstaller again to finish.', said)
        self.assertTrue(self.ours())

    def test_exits_0_when_it_removed_the_copy(self):
        code, said = self.tidy()
        self.assertEqual(code, 0)
        self.assertIn('Removed the plain login file', said)

    def test_exits_0_when_it_left_the_only_sign_in(self):
        self.cc_deletes_item()
        code, said = self.tidy()
        self.assertEqual(code, 0)
        self.assertIn('missing', said)


if __name__ == '__main__':
    unittest.main()
