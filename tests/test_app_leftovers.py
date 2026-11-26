"""Outside the account engine: what a throwaway sign-in leaves, uninstall.sh's message when the loop will not stop, and
the recovery folder's documentation. Each class's docstring names the failure its tests rule out."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import os
import re
import subprocess
import tempfile
import unittest
from unittest import mock

from switchboard import connect, core, engine

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')


class ASignInsItemThatCouldNotGoYetIsTriedAgain(unittest.TestCase):
    """engine.remove_sign_in says a False answer is tried again by the folder's sweep at the next start, but
    connect._clean removed the folder whatever the answer, and the sweep walks folders only: a throwaway sign-in's
    Keychain item (a working login of the new account) that the Keychain would not remove just then stayed for good."""

    def test_the_folder_stays_until_its_item_is_gone_and_the_next_sweep_tries_again(self):
        d = tempfile.mkdtemp(prefix='verify-', dir=self.logins())
        self.addCleanup(lambda: os.path.isdir(d) and __import__('shutil').rmtree(d, True))
        answers, asked = [False, True], []

        def remove(path):
            asked.append(path)
            return answers.pop(0) if path == d and answers else True
        with mock.patch.object(engine, 'remove_sign_in', remove):
            self.assertFalse(connect._clean(d))
            self.assertTrue(os.path.isdir(d))                  # the item may still be there: kept for the sweep
            connect.sweep()                                    # the next start
        self.assertEqual([p for p in asked if p == d], [d, d])
        self.assertFalse(os.path.isdir(d))

    def logins(self):
        folder = core.data_path('logins')
        os.makedirs(folder, mode=0o700, exist_ok=True)
        return folder


class TheUninstallerSaysWhatIsStillThere(unittest.TestCase):
    """When a lone loop would not stop, uninstall.sh said "so nothing else was removed", after it had already
    removed the menu bar item's job, the page's job and a page started by hand."""

    def test_a_loop_that_will_not_go_is_said_plainly(self):
        with open(os.path.join(HERE, 'uninstall.sh')) as f:
            text = f.read()
        block = re.search(r'^is_loop\(\) \{.*?^\}\n.*?^stop_loop\(\) \{.*?^\}\n', text, re.S | re.M).group(0)
        block = re.sub(r'-lt \d+ \] && is_loop', '-lt 1 ] && is_loop', block)       # short waits for the test
        data = tempfile.mkdtemp()
        with open(os.path.join(data, 'auto.pid'), 'w') as f:
            f.write('424242')
        stand_ins = 'is_loop() { return 0; }\nkill() { return 0; }\nsleep() { return 0; }\n'   # a loop that stays
        r = subprocess.run(['/bin/sh', '-c', 'set -eu\nDATA="$1"\n' + block + stand_ins + 'stop_loop', 'sh', data],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 1)
        self.assertNotIn('nothing else was removed', r.stderr)
        self.assertIn('did not stop, so the uninstall stopped here', r.stderr)
        self.assertIn('are still there', r.stderr)
        self.assertIn('Run this again in a minute', r.stderr)


class TheRecoveryFolderIsDescribedAsItIs(unittest.TestCase):
    """docs/commands.md said `recovery/` exists only when the Keychain refused a login, but the vault makes it
    (with its manifest.json, no login in it) for every login it is finishing."""

    def test_its_row_names_the_list_and_when_a_login_is_in_it(self):
        with open(os.path.join(HERE, 'docs', 'commands.md')) as f:
            row = next(line for line in f.read().splitlines() if line.startswith('| `recovery/` |'))
        self.assertIn('manifest.json', row)
        self.assertIn('no login', row)
        self.assertIn('only when the Keychain refused one', row)
        self.assertFalse(row.startswith('| `recovery/` | only when'), row)


if __name__ == '__main__':
    unittest.main()
