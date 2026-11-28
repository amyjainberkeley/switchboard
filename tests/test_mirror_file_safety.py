import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The standing copy is a secret on disk, so it is made owner-only from its first byte (0600, whatever the umask), never
# written through a link (Claude Code's folder or the file itself), never into a folder or over a file another user
# owns, and its text and tokens never reach a log line, the sign-in record or a History line.
# The account engine on its stand-ins (fakes_accounts) with a fake clock; made-up *.example accounts.
import os
import unittest
from unittest import mock

from hold_fakes import Base
from switchboard.accounts import putback, switcher


class Mirror(Base):
    MIRROR = True


class OwnerOnly(Mirror):
    def test_0600_whatever_the_umask(self):
        old = os.umask(0)
        try:
            self.look(keychain=True)
        finally:
            os.umask(old)
        self.assertEqual(os.stat(self.cred).st_mode & 0o777, 0o600)

    def test_0600_after_each_renewal(self):
        self.look(keychain=True)
        for _ in range(3):
            self.clock.advance(600.0)
            self.rig.claude_renews()
            old = os.umask(0)
            try:
                self.look(after=0.5, keychain=True)
            finally:
                os.umask(old)
            self.assertEqual(os.stat(self.cred).st_mode & 0o777, 0o600)

    def test_no_secret_in_the_log_the_record_or_history(self):
        self.look(keychain=True)
        out1 = self.hold('looked')
        self.clock.advance(600.0)
        self.rig.claude_renews()
        self.look(after=0.5, keychain=True)
        out2 = self.hold('looked')
        self.assert_no_secret_in(self.rig.logs, self.rig.events, out1, out2, self.note())


class AnAdoptedFile(Mirror):
    def test_of_claude_codes_readable_by_others_is_made_owner_only(self):
        # only noted as ours (it holds the item's very text): History and doctor then say "readable only by you"
        self.cc_writes(self.rig.live_text(), age=putback.HOLD_TIDY_S + 1)
        os.chmod(self.cred, 0o644)
        t = self.clock.wall() - putback.HOLD_TIDY_S - 1
        os.utime(self.cred, (t, t))
        self.look(keychain=True)
        self.assertTrue(self.ours())
        self.assertEqual(os.stat(self.cred).st_mode & 0o777, 0o600)

    def test_at_a_switch_too(self):
        self.cc_writes(self.rig.live_text(), age=5.0)
        os.chmod(self.cred, 0o644)
        a = [k for k in self.rig.doc()['accounts'] if k.startswith('uuid-a')][0]
        self.assertTrue(switcher.switch(self.ctx, a).ok)
        self.assertTrue(self.ours())
        self.assertEqual(os.stat(self.cred).st_mode & 0o777, 0o600)


class NeverThroughALink(Mirror):
    def test_a_folder_that_is_a_link_gets_no_copy(self):
        folder = os.path.dirname(self.cred)
        real = folder + '-real'
        os.rename(folder, real)
        os.symlink(real, folder)
        self.look(keychain=True)
        self.assertEqual(os.listdir(real), [])
        self.assertFalse(os.path.lexists(self.cred))

    def test_a_link_at_the_files_place_is_never_followed(self):
        target = os.path.join(self.rig.root, 'elsewhere.json')
        with open(target, 'w') as f:
            f.write('{}')
        os.symlink(target, self.cred)
        self.look(keychain=True)
        self.assertTrue(os.path.islink(self.cred))
        with open(target) as f:
            self.assertEqual(f.read(), '{}')

    def test_a_dangling_link_is_never_followed(self):
        target = os.path.join(self.rig.root, 'nothing-here.json')
        os.symlink(target, self.cred)
        self.look(keychain=True)
        self.assertTrue(os.path.islink(self.cred))
        self.assertFalse(os.path.exists(target))

    def test_a_switch_through_a_linked_folder_leaves_no_copy(self):
        folder = os.path.dirname(self.cred)
        real = folder + '-real'
        os.rename(folder, real)
        os.symlink(real, folder)
        a = [k for k in self.rig.doc()['accounts'] if k.startswith('uuid-a')][0]
        self.assertTrue(switcher.switch(self.ctx, a).ok)
        self.assertEqual([n for n in os.listdir(real) if 'credentials' in n], [])


class AnotherUsers(Mirror):
    def test_folder_gets_no_copy(self):
        uid = os.getuid() + 1
        with mock.patch.object(os, 'getuid', lambda: uid):
            self.look(keychain=True)
        self.assertIsNone(self.file_text())

    def test_file_is_never_written_over(self):
        self.look(keychain=True)
        self.clock.advance(600.0)
        self.rig.claude_renews()
        text = self.file_text()
        uid = os.getuid() + 1
        with mock.patch.object(os, 'getuid', lambda: uid):
            self.look(after=0.5, keychain=True)
        self.assertEqual(self.file_text(), text)
        self.assertTrue(putback._mirror_safe(self.cred))


if __name__ == '__main__':
    unittest.main()
