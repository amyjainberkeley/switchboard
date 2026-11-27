"""`uninstall.sh --all` takes the notes folder too, and no last line says "nothing is left" while it is there.

The notes of chats carried on in another assistant, and handed jobs' answers, live in ~/.switchboard-notes: beside
~/.switchboard, never in it (switchboard/providers/contract.py NOTES_DIR_NAME). The uninstaller knew one folder only,
so `--all` ended "Nothing of Switchboard is left." with a folder of the person's chat notes still on the Mac.

These tests run the whole script, in a throwaway home. What keeps that away from the real account:
- the home is a new temp folder, so the script takes it for not the account's own and leaves launchd and macOS's list
  of apps alone (it is run with --no-agent as well);
- SWITCHBOARD_APPLICATIONS points into the temp folder, so no app in /Applications is ever looked at;
- the script that runs is a copy whose Keychain tool is a stand-in that only writes down what it was asked.
Every process and command the script stops or removes is named by the home's own path, and this home has none.
"""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import os
import shutil
import stat
import subprocess
import tempfile
import unittest

from switchboard.providers import contract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, 'uninstall.sh')
KEYCHAIN_LINE = 'SECURITY=/usr/bin/security'
SERVICES = ('Switchboard login', 'Switchboard login (previous)', 'Switchboard recovery', 'switchboard-gateway',
            'Switchboard token', 'Switchboard API key')
UNTOUCHED = 'The login Claude Code is using was not touched.'
NOTHING_LEFT = 'Nothing of Switchboard is left.'


class Home:
    """A throwaway home with settings in ~/.switchboard, and the copy of uninstall.sh that is safe to run in it."""

    def __init__(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix='sb-uninstall-notes-'))
        self.home = os.path.join(self.root, 'home')
        self.data = os.path.join(self.home, '.switchboard')
        self.notes = os.path.join(self.home, contract.NOTES_DIR_NAME)
        self.calls = os.path.join(self.root, 'keychain-calls.log')
        os.makedirs(self.data)
        os.makedirs(os.path.join(self.root, 'Applications'))
        with open(os.path.join(self.data, 'settings.json'), 'w') as f:
            f.write('{"port": 7799}\n')
        tool = os.path.join(self.root, 'security')
        with open(tool, 'w') as f:      # says "no such item" (44) to everything, as an empty Keychain does
            f.write('#!/bin/sh\nprintf \'%%s\\n\' "$*" >> "%s"\nexit 44\n' % self.calls)
        os.chmod(tool, 0o755)
        with open(SCRIPT, encoding='utf-8') as f:
            text = f.read()
        if text.count(KEYCHAIN_LINE) != 1 or text.count('/usr/bin/security') != 1:     # (never an assert: -O drops those)
            raise RuntimeError('uninstall.sh names the Keychain tool in another way now: this test must not run it')
        self.script = os.path.join(self.root, 'uninstall.sh')
        with open(self.script, 'w', encoding='utf-8') as f:
            f.write(text.replace(KEYCHAIN_LINE, 'SECURITY="%s"' % tool))

    def a_note(self, folder=None):
        task = os.path.join(folder or self.notes, 't-0a0b0c0d')
        os.makedirs(task, mode=0o700)
        path = os.path.join(task, 'h-00000001.md')
        with open(path, 'w') as f:
            f.write('What we were doing: a made-up note.\n')
        return path

    def run(self, *options, script=None):
        if os.path.realpath(self.home) == os.path.realpath(os.path.expanduser('~')):
            raise RuntimeError('this would run the uninstaller in the real home folder')
        env = {'HOME': self.home, 'PATH': '/usr/bin:/bin:/usr/sbin:/sbin',
               'SWITCHBOARD_APPLICATIONS': os.path.join(self.root, 'Applications')}
        return subprocess.run(['/bin/sh', script or self.script] + list(options) + ['--no-agent'], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=180)

    def asked_of_the_keychain(self):
        if not os.path.exists(self.calls):
            return []
        with open(self.calls) as f:
            return [line.rstrip('\n') for line in f]

    def remove(self):
        for folder, names, _ in os.walk(self.root):
            for n in names:
                p = os.path.join(folder, n)
                if not os.path.islink(p):
                    os.chmod(p, stat.S_IRWXU)
        shutil.rmtree(self.root, ignore_errors=True)


class TheNotesFolderAtUninstall(unittest.TestCase):
    def setUp(self):
        self.h = Home()
        self.addCleanup(self.h.remove)

    def test_all_takes_the_notes_folder_and_says_so(self):
        note = self.h.a_note()
        done = self.h.run('--all')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse(os.path.exists(note))
        self.assertFalse(os.path.lexists(self.h.notes), 'the notes folder is still there after --all')
        self.assertFalse(os.path.exists(self.h.data))
        lines = done.stdout.splitlines()
        self.assertIn('Removed your notes of chats (~/%s).' % contract.NOTES_DIR_NAME, lines)
        self.assertEqual(lines[-2:], ['Removed ~/.switchboard with your settings. ' + NOTHING_LEFT, UNTOUCHED])
        self.assertEqual(os.listdir(self.h.home), [], 'something of Switchboard is left in the home folder')

    def test_all_with_no_notes_folder_says_what_it_always_said(self):
        done = self.h.run('--all')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn('notes', done.stdout)
        self.assertEqual(done.stdout.splitlines()[-2:],
                         ['Removed ~/.switchboard with your settings. ' + NOTHING_LEFT, UNTOUCHED])

    def test_a_link_in_the_notes_folders_place_is_unlinked_and_never_followed(self):
        elsewhere = os.path.join(self.h.root, 'elsewhere')
        kept = self.h.a_note(folder=elsewhere)
        os.symlink(elsewhere, self.h.notes)
        done = self.h.run('--all')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse(os.path.lexists(self.h.notes), 'the link is still there')
        self.assertTrue(os.path.isfile(kept), 'a file behind the link was removed')

    def test_notes_that_will_not_go_are_named_and_nothing_left_is_not_said(self):
        note = self.h.a_note()
        os.chmod(os.path.dirname(note), 0o500)          # a folder whose files can't be unlinked
        done = self.h.run('--all')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(os.path.exists(note))
        self.assertIn('Could not remove ~/%s (your notes of chats): move it to the Trash yourself.'
                      % contract.NOTES_DIR_NAME, done.stderr.splitlines())
        self.assertNotIn(NOTHING_LEFT, done.stdout)
        self.assertEqual(done.stdout.splitlines()[-2:], ['Removed ~/.switchboard with your settings.', UNTOUCHED])

    def test_a_plain_uninstall_keeps_the_notes_and_says_where_and_all_from_its_copy_takes_them(self):
        note = self.h.a_note()
        done = self.h.run()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(os.path.isfile(note), 'a plain uninstall removed the notes')
        self.assertEqual(done.stdout.splitlines()[-5:], [
            'Kept your settings in ~/.switchboard (a reinstall picks them up).',
            'Kept your notes of chats in ~/%s.' % contract.NOTES_DIR_NAME,
            UNTOUCHED,
            'To remove them too, run:',
            'sh ~/.switchboard/uninstall.sh --all'])
        again = self.h.run('--all', script=os.path.join(self.h.data, 'uninstall.sh'))
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(os.listdir(self.h.home), [])

    def test_a_plain_uninstall_with_only_notes_to_keep_never_says_nothing_is_left(self):
        os.remove(os.path.join(self.h.data, 'settings.json'))
        note = self.h.a_note()
        done = self.h.run()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(os.path.isfile(note))
        self.assertFalse(os.path.exists(self.h.data))
        self.assertNotIn(NOTHING_LEFT, done.stdout)
        self.assertEqual(done.stdout.splitlines()[-3:], [
            'Kept your notes of chats in ~/%s.' % contract.NOTES_DIR_NAME,
            UNTOUCHED,
            'To remove them too: rm -rf ~/%s' % contract.NOTES_DIR_NAME])

    def test_the_keychain_is_asked_only_for_switchboards_own_services(self):
        self.h.a_note()
        self.h.run('--all')
        asked = self.h.asked_of_the_keychain()
        self.assertEqual(sorted(set(asked)), sorted('delete-generic-password -s %s' % s for s in SERVICES))


class TheNameInTheScript(unittest.TestCase):
    def test_uninstall_sh_names_the_folder_the_program_writes(self):
        with open(SCRIPT, encoding='utf-8') as f:
            text = f.read()
        self.assertIn('NOTES="$HOME/%s"' % contract.NOTES_DIR_NAME, text,
                      'uninstall.sh does not name the notes folder the program makes (contract.NOTES_DIR_NAME)')


if __name__ == '__main__':
    unittest.main()
