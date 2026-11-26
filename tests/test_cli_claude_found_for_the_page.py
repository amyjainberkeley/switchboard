"""Where Switchboard finds Claude Code's command, with a throwaway home and made-up executables: the PATH first; then
the one a command run at a terminal found on the shell's PATH (kept in settings, written only when it changes), while
it is still an executable file; then the usual places, version managers' folders among them. The page's job runs with
a short PATH of its own, so without this it said Claude Code was not installed when `claude` worked in the terminal."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import io
import os
import sys
import tempfile
import unittest
from unittest import mock

from switchboard import connect, core


class _Tty(io.StringIO):
    def __init__(self, tty):
        super().__init__()
        self.tty = tty

    def isatty(self):
        return self.tty


def executable(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write('#!/bin/sh\nexit 0\n')
    os.chmod(path, 0o755)
    return path


class ClaudeFound(unittest.TestCase):
    def setUp(self):
        root = tempfile.mkdtemp(prefix='switchboard-claude-')
        self.home = os.path.join(root, 'home')
        self.empty = os.path.join(root, 'empty')                   # the page job's PATH, without claude in it
        os.makedirs(self.home)
        os.makedirs(self.empty)
        env = mock.patch.dict(os.environ, {'HOME': self.home, 'SWITCHBOARD_HOME': os.path.join(root, 'data'),
                                           'PATH': self.empty})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = False
        self.writes = 0
        real = core.update_setting

        def counted(key, change):
            self.writes += 1
            return real(key, change)
        p = mock.patch.object(core, 'update_setting', counted)
        p.start()
        self.addCleanup(p.stop)
        # The usual places outside the home folder are this Mac's own: none of them counts here.
        q = mock.patch.object(os.path, 'exists', lambda p, real=os.path.exists: False if p in (
            '/opt/homebrew/bin/claude', '/usr/local/bin/claude') else real(p))
        q.start()
        self.addCleanup(q.stop)

    def at(self, tty):
        return mock.patch.object(sys, 'stdin', _Tty(tty))

    def test_a_terminal_remembers_its_claude_and_the_page_finds_it(self):
        exe = executable(os.path.join(self.home, 'tools', 'node-22', 'bin', 'claude'))
        with self.at(False):
            self.assertIsNone(connect.claude_bin())                  # the page's job, before any terminal ran
        with self.at(True), mock.patch.dict(os.environ, {'PATH': os.path.dirname(exe)}):
            self.assertEqual(connect.claude_bin(), exe)              # a command at a terminal
            self.assertEqual(connect.claude_bin(), exe)
        self.assertEqual(core.settings().get(connect.CLAUDE_PATH), exe)
        self.assertEqual(self.writes, 1)                             # written only when it changed
        with self.at(False):
            self.assertEqual(connect.claude_bin(), exe)              # the page's job, afterwards
        os.chmod(exe, 0o644)                                         # no longer runnable: not offered
        with self.at(False):
            self.assertIsNone(connect.claude_bin())

    def test_the_page_never_remembers_its_own_path(self):
        exe = executable(os.path.join(self.home, 'job', 'claude'))
        with self.at(False), mock.patch.dict(os.environ, {'PATH': os.path.dirname(exe)}):
            self.assertEqual(connect.claude_bin(), exe)
        self.assertEqual(self.writes, 0)
        self.assertNotIn(connect.CLAUDE_PATH, core.settings())

    def test_a_claude_found_through_a_path_entry_that_is_not_a_whole_path_is_not_remembered(self):
        here = os.path.join(self.home, 'project')
        executable(os.path.join(here, 'claude'))                    # a file named claude in the folder one stands in
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        os.chdir(here)
        with mock.patch.dict(os.environ, {'PATH': '.'}), mock.patch.object(sys, 'stdin', _Tty(True)):
            self.assertTrue(connect.claude_bin())                   # the terminal's own PATH finds it, as ever
        self.assertEqual((self.writes, core.settings().get(connect.CLAUDE_PATH)), (0, None))

    def test_a_claude_others_can_replace_is_neither_remembered_nor_used(self):
        shared = os.path.join(self.home, 'shared', 'bin')
        claude = executable(os.path.join(shared, 'claude'))
        os.chmod(shared, 0o777)                                     # anyone on this Mac may put another file there
        with mock.patch.dict(os.environ, {'PATH': shared}), mock.patch.object(sys, 'stdin', _Tty(True)):
            self.assertEqual(connect.claude_bin(), claude)
        self.assertEqual((self.writes, core.settings().get(connect.CLAUDE_PATH)), (0, None))
        os.chmod(shared, 0o755)
        with mock.patch.dict(os.environ, {'PATH': shared}), mock.patch.object(sys, 'stdin', _Tty(True)):
            connect.claude_bin()
        self.assertEqual(core.settings().get(connect.CLAUDE_PATH), claude)
        with mock.patch.object(sys, 'stdin', _Tty(False)):
            self.assertEqual(connect.claude_bin(), claude)          # the page finds it
            os.chmod(shared, 0o777)
            self.assertIsNone(connect.claude_bin())                 # and no longer, once the folder is open to all

    def test_the_known_places(self):
        with self.at(False):
            for rel in ('.claude/local/claude', '.bun/bin/claude', '.volta/bin/claude', '.asdf/shims/claude',
                        '.local/share/mise/shims/claude'):
                exe = executable(os.path.join(self.home, rel))
                self.assertEqual(connect.claude_bin(), exe, rel)
                os.remove(exe)
            executable(os.path.join(self.home, '.nvm/versions/node/v9.11.2/bin/claude'))
            newest = executable(os.path.join(self.home, '.nvm/versions/node/v22.11.0/bin/claude'))
            executable(os.path.join(self.home, '.nvm/versions/node/v18.20.4/bin/claude'))
            self.assertEqual(connect.claude_bin(), newest)

    def test_a_sign_in_runs_claude_with_its_own_folder_on_the_path(self):
        exe = executable(os.path.join(self.home, '.nvm/versions/node/v22.11.0/bin/claude'))
        seen = {}

        class Stop(Exception):
            pass

        def popen(argv, **kw):
            seen.update(argv=argv, env=kw.get('env'))
            raise Stop()
        with self.at(False), mock.patch.object(connect.subprocess, 'Popen', popen), self.assertRaises(Stop):
            connect.start('', None)
        self.assertEqual(seen['argv'][0], exe)
        self.assertEqual(seen['env']['PATH'].split(':')[-1], os.path.dirname(exe))
        with self.at(False), mock.patch.object(connect.subprocess, 'Popen', popen), self.assertRaises(Stop), \
                mock.patch.object(connect, 'claude_bin', lambda: 'claude'):     # a claude with no folder to its name
            connect.start('', None)
        self.assertNotIn('', seen['env']['PATH'].split(':'))                    # (an empty entry means "here")
        with self.at(False), mock.patch.object(connect.subprocess, 'Popen', popen), self.assertRaises(Stop), \
                mock.patch.object(connect, 'claude_bin', lambda: './claude'):   # found through a "." on a PATH
            connect.start('', None)
        self.assertNotIn('.', seen['env']['PATH'].split(':'))                   # (and so does ".")


if __name__ == '__main__':
    unittest.main()
