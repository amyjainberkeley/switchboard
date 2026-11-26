import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# The short name, `sb` (core.SHORT_NAME), the same command as `switchboard`.
#
# The installers' side (the link, another `sb` left alone, the last line's words, uninstall, Homebrew, update) is
# tests/install-check.sh case 22. Here: the name is defined once, the help says it once near the top, the docs say it
# once each, and doctor knows it (ours, another program's, not there) without calling another or a missing one a
# problem. Every `sb` here is a made-up
# file in a throwaway folder.
import inspect
import io
import os
import re
import sys
import tempfile
import shutil
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
sys.path.insert(0, ROOT)

from switchboard import __main__ as cli
from switchboard import connect, core, linkapp
import test_cli_commands  # its Cli: a throwaway home and the fake accounts, for the doctor


def read(name):
    with open(os.path.join(ROOT, name), encoding='utf-8') as f:
        return f.read()


class TheName(unittest.TestCase):
    def test_defined_once_in_python(self):
        self.assertEqual(core.SHORT_NAME, 'sb')

    def test_one_shell_variable_in_each_script(self):
        for name in ('install.sh', 'uninstall.sh', 'packaging/homebrew/launcher', 'packaging/homebrew/uninstall.sh'):
            with self.subTest(script=name):
                text = read(name)
                self.assertEqual(re.findall(r'^SHORT="([^"]*)"', text, re.M), ['sb'])
                code = [l for l in text.splitlines() if not l.lstrip().startswith('#')]
                # the name itself is written nowhere else in the script's code: every use goes through $SHORT
                self.assertEqual([l for l in code if re.search(r'\bsb\b', l) and not l.startswith('SHORT=')], [])


class TheHelp(unittest.TestCase):
    def test_the_help_says_once_near_the_top_that_sb_is_the_same_command(self):
        lines = inspect.cleandoc(cli.__doc__).splitlines()
        said = [n for n, l in enumerate(lines) if re.search(r'\bsb\b', l)]
        self.assertEqual(len(said), 1, said)
        self.assertLess(said[0], 3)
        self.assertIn('the same command', lines[said[0]])

    def test_help_prints_it(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(['help']), 0)
        self.assertEqual(len(re.findall(r'\bsb\b', out.getvalue())), 1)

    def test_sb_has_no_command_help_of_its_own(self):
        self.assertIsNone(cli.command_help('sb'))


class TheDocs(unittest.TestCase):
    def test_each_says_it_once(self):
        readme = read('README.md')
        use = readme[readme.index('## Usage'):]
        use = use[:use.index('\n## ', 4)]
        commands = read('docs/commands.md')
        top = commands[:commands.index('\n## ')]
        log = read('CHANGELOG.md')
        added = log[log.index('### Added'):]
        for where, text in (('README Usage', use), ('docs/commands.md top', top), ('CHANGELOG Added', added)):
            with self.subTest(where=where):
                self.assertEqual(len(re.findall(r'`sb\b', text)), 1, text)

    def test_packaging_says_why_sb_is_no_binary_line(self):
        text = read('packaging/README.md')
        self.assertIn('`sb`', text)
        self.assertIn('binary', text)


class DoctorKnowsTheShortName(test_cli_commands.Cli):
    def setUp(self):
        super().setUp()
        self.bin = tempfile.mkdtemp(prefix='switchboard-bin-')
        self.addCleanup(shutil.rmtree, self.bin, True)
        self.ours = os.path.join(self.bin, 'ours')
        self.other = os.path.join(self.bin, 'other')
        os.makedirs(self.ours)
        os.makedirs(self.other)
        program = core.data_path('bin/switchboard')
        os.makedirs(os.path.dirname(program), exist_ok=True)
        with open(program, 'w') as f:
            f.write('#!/bin/sh\n')
        os.chmod(program, 0o755)
        os.symlink(program, os.path.join(self.ours, 'switchboard'))

    def doctor(self, path):
        if not self.fake.saved:
            self.two()
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {'PATH': path}), \
                mock.patch.object(cli, 'page_running', lambda: False), \
                mock.patch.object(linkapp, 'status', lambda fresh=False: {'installed': False, 'on': False}), \
                mock.patch.object(cli.browser, 'present', lambda: False), \
                mock.patch.object(connect, 'claude_bin', lambda: None), \
                redirect_stdout(out), redirect_stderr(err):
            cli.main(['doctor'])
        return [l for l in out.getvalue().splitlines() if re.search(r'\bsb\b', l)]

    def test_ours(self):
        os.symlink(core.data_path('bin/switchboard'), os.path.join(self.ours, 'sb'))
        lines = self.doctor(self.ours + os.pathsep + '/usr/bin')
        self.assertEqual(lines, [' ok  The short command sb (%s)' % cli._short_path(self.ours)])

    def test_ours_beside_homebrews_switchboard(self):
        # Homebrew's bin folder: switchboard is Homebrew's link to the launcher, and sb a link to switchboard beside it.
        brew = os.path.join(self.bin, 'brew')
        os.makedirs(brew)
        launcher = os.path.join(self.bin, 'launcher')
        with open(launcher, 'w') as f:
            f.write('#!/bin/sh\n')
        os.chmod(launcher, 0o755)
        os.symlink(launcher, os.path.join(brew, 'switchboard'))
        os.symlink('switchboard', os.path.join(brew, 'sb'))
        lines = self.doctor(brew + os.pathsep + '/usr/bin')
        self.assertEqual(lines, [' ok  The short command sb (%s)' % cli._short_path(brew)])

    def test_another_program_is_no_problem(self):
        theirs = os.path.join(self.other, 'sb')
        with open(theirs, 'w') as f:
            f.write('#!/bin/sh\n')
        os.chmod(theirs, 0o755)
        lines = self.doctor(self.ours + os.pathsep + self.other + os.pathsep + '/usr/bin')
        self.assertEqual(lines, [' ok  sb already means something else on this Mac (%s), so the command is switchboard'
                                 % cli._short_path(theirs)])

    def test_not_there_is_no_problem(self):
        lines = self.doctor(self.ours + os.pathsep + '/usr/bin')
        self.assertEqual(lines, [' --  No short command sb'])
        out = io.StringIO()
        # its one hint, and not a NO
        with mock.patch.dict(os.environ, {'PATH': self.ours + os.pathsep + '/usr/bin'}), \
                mock.patch.object(cli, 'page_running', lambda: False), \
                mock.patch.object(linkapp, 'status', lambda fresh=False: {'installed': False, 'on': False}), \
                mock.patch.object(cli.browser, 'present', lambda: False), \
                mock.patch.object(connect, 'claude_bin', lambda: None), redirect_stdout(out):
            cli.main(['doctor'])
        text = out.getvalue()
        at = text.index(' --  No short command sb\n')
        self.assertTrue(text[at:].splitlines()[1].startswith('       switchboard update adds it'), text[at:])


if __name__ == '__main__':
    unittest.main()
