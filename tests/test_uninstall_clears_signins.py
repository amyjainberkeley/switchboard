import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The uninstaller's tidy clears every sign-in it finds, also one the browser approved a moment ago.
#
# When the page's job starts, a sign-in whose process has ended and that nobody has handed to the engine yet is taken
# up again, and the loop that reads the accounts keeps its login (tests/test_signin_pressed_twice.py, ARestart). The
# uninstaller has no such loop: its tidy runs in a process that ends at once. A sign-in taken up there would be kept
# by nobody. Its folder goes with ~/.switchboard a moment later, and the temporary Keychain item that holds the new
# login would stay behind with nothing left on the Mac that knows of it. So tidy clears: the Keychain item first (the
# engine's removal), then the folder, as it did before a start could take a sign-in up.
#
# - A sign-in the browser approved, found by tidy: its Keychain item is removed and its folder goes. It is not taken
#   up, and nobody is woken.
# - One still waiting in the browser: stopped and cleared, as ever.
# - `switchboard tidy` itself (the command uninstall.sh runs) clears it, and says nothing is left.
# - A start of the page's job still takes such a sign-in up: only tidy clears.
# - A login the Keychain will not give up now (locked, busy): the sweep says which folders it could not clear, tidy
#   names "A sign-in that never finished" as left and exits 1, and the folder stays. The uninstaller then stops before
#   it removes ~/.switchboard/logins, so the same command, run again once the Keychain opens, still finds the folder
#   and takes the login out. (Before, tidy said nothing, the folder went, and the login stayed in the Keychain.)
# - A link under the sign-ins' folder is not a sign-in's own folder. A start and tidy both leave it alone: nothing is
#   written through it, it is never taken up, the engine is never asked about it, and it never stops an uninstall
#   (the engine refuses such a folder at every run, so naming it as left would make the uninstaller fail for ever).
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import connect
import test_settings_edits
import test_signin_pressed_twice as signins

B = signins.B


class Leftovers(signins.SignIns):
    """Sign-ins as the page's job left them when it was stopped: nothing in memory, only the folders. The Keychain
    clean-up is the real one, over a stand-in for the engine's removal of the item."""

    def setUp(self):
        super().setUp()
        self.removed, self.removes, self.going = [], True, ''
        self.patch(connect, '_clean', signins.CLEAN)
        self.patch(connect.engine, 'remove_sign_in', lambda d: self.removed.append(d) or self.removes)
        # what `ps` says of a folder's process number: nothing (it has ended), unless a test says it is still going
        self.patch(connect, 'run', lambda args, **kw: type('Ran', (), {'stdout': self.going})())

    def approved_and_left(self):
        """The browser approved a sign-in, and the page's job stopped before the loop kept it. Its folder."""
        connect.start(B, 'P1')
        d = self.made[0].dir
        self.made[0].approve(B)
        self.woken_soon()
        connect.RUNNING.clear()                 # the uninstaller stops the page's job before it runs tidy
        return d


class TheUninstallersSweep(Leftovers):
    def test_one_the_browser_approved_is_cleared_and_not_taken_up(self):
        d = self.approved_and_left()
        woken = len(self.woken)
        connect.sweep(take_up=False)
        self.assertEqual(self.removed, [d])                     # its Keychain item: the engine was asked to remove it
        self.assertFalse(os.path.isdir(d))
        self.assertEqual(connect.RUNNING, {})                   # not taken up: nobody here would ever keep it
        self.assertEqual(len(self.woken), woken)                # and no loop is woken

    def test_one_still_waiting_in_the_browser_is_stopped_and_cleared(self):
        connect.start(B, 'P1')
        d = self.made[0].dir
        connect.RUNNING.clear()
        self.going = '/made-up/claude auth login --claudeai'
        connect.sweep(take_up=False)
        self.assertTrue(self.killed)
        self.assertEqual(self.removed, [d])
        self.assertFalse(os.path.isdir(d))

    def test_a_start_of_the_pages_job_still_takes_it_up(self):
        d = self.approved_and_left()
        connect.sweep()
        self.assertTrue(os.path.isdir(d))
        self.assertEqual(self.removed, [])
        self.assertEqual([c['dir'] for c in connect.RUNNING.values()], [d])

    def test_it_says_which_folders_it_could_not_clear(self):
        d = self.approved_and_left()
        self.removes = False                                    # the Keychain will not give the login up now
        self.assertEqual(connect.sweep(take_up=False), [d])
        self.assertTrue(os.path.isdir(d))
        self.removes = True
        self.assertEqual(connect.sweep(take_up=False), [])
        self.assertFalse(os.path.isdir(d))


class TheCommandTheUninstallerRuns(Leftovers):
    def test_switchboard_tidy_clears_it_and_leaves_nothing(self):
        d = self.approved_and_left()
        from switchboard import __main__ as cli
        rc, said = test_settings_edits.quiet(cli.main, ['tidy'])
        self.assertEqual(self.removed, [d], said)
        self.assertFalse(os.path.isdir(d), said)
        self.assertEqual(connect.RUNNING, {})
        self.assertEqual(rc, 0, said)
        self.assertNotIn('left as', said)

    def test_a_login_the_keychain_will_not_give_up_is_named_and_its_folder_stays_for_the_next_run(self):
        d = self.approved_and_left()
        from switchboard import __main__ as cli
        self.removes = False                                    # a locked or busy Keychain
        rc, said = test_settings_edits.quiet(cli.main, ['tidy'])
        self.assertEqual(rc, 1, said)                           # so uninstall.sh stops before the folder goes
        self.assertIn("A sign-in that never finished: left as it is (Couldn't take its login out of the Keychain yet: "
                      "it may be locked, or macOS may be waiting for your answer)", said)
        self.assertTrue(os.path.isdir(d))
        self.assertTrue(os.path.exists(os.path.join(d, connect.OVER)))      # never to be taken up as a sign-in again
        self.removes = True                                     # the Keychain opens: the same command again
        rc, said = test_settings_edits.quiet(cli.main, ['tidy'])
        self.assertEqual(rc, 0, said)
        self.assertNotIn('left as', said)
        self.assertFalse(os.path.isdir(d))


class ALinkUnderTheSignInsFolder(Leftovers):
    """Not a sign-in's own folder: a link under logins/ to a folder somewhere else, which holds what a fresh sign-in
    would. The engine refuses such a folder every time it is asked."""

    def setUp(self):
        super().setUp()
        self.removes = False
        self.elsewhere = tempfile.mkdtemp(prefix='switchboard-elsewhere-')
        self.addCleanup(shutil.rmtree, self.elsewhere, True)
        with open(os.path.join(self.elsewhere, '.credentials.json'), 'w') as f:
            f.write('{}')
        logins = connect.data_path('logins')
        os.makedirs(logins, exist_ok=True)
        self.link = os.path.join(logins, 'abcdef0123')
        os.symlink(self.elsewhere, self.link)
        self.addCleanup(lambda: os.path.islink(self.link) and os.unlink(self.link))

    def test_a_start_and_the_uninstaller_both_leave_it_alone(self):
        for take_up in (True, False):
            self.assertEqual(connect.sweep(take_up=take_up), [])
            self.assertEqual(connect.RUNNING, {})                           # never taken up as a sign-in
            self.assertEqual(self.removed, [])                              # the engine is never asked about it
            self.assertEqual(os.listdir(self.elsewhere), ['.credentials.json'])     # nothing written through it
        self.assertTrue(os.path.islink(self.link))

    def test_it_never_stops_the_uninstaller(self):
        from switchboard import __main__ as cli
        rc, said = test_settings_edits.quiet(cli.main, ['tidy'])
        self.assertEqual(rc, 0, said)
        self.assertNotIn('left as', said)


if __name__ == '__main__':
    unittest.main()
