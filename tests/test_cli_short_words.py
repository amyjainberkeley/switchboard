import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Short words: `ls` and `list` are `status`, `rm` is `remove`, `upgrade` is `update`, `whoami` is `current`. One
# table (SHORT_WORDS in __main__.py), applied once at the top of main(), so a short word is the command itself:
# the same words after it, the same help, the same exit codes. `switchboard help` has one line that lists them.
import inspect
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import __main__ as cli
from test_cli_commands import Cli, B


class ShortWords(Cli):
    def test_the_table(self):
        self.assertEqual(cli.SHORT_WORDS, {'ls': 'status', 'list': 'status', 'rm': 'remove', 'upgrade': 'update',
                                           'whoami': 'current'})

    def test_each_runs_its_command_with_the_words_after_it(self):
        for short, full, more in (('ls', 'status', ['--json']), ('list', 'status', []), ('rm', 'remove_command', [B]),
                                  ('upgrade', 'update', ['--port', '7801']), ('whoami', 'current', [])):
            with self.subTest(short=short):
                seen = []
                if full == 'current':
                    from switchboard import scripted
                    target = mock.patch.object(scripted, 'current', lambda args: seen.append(args) or 0)
                elif full == 'status' and more:
                    target = mock.patch.object(cli, 'status_json', lambda: seen.append('json') or 0)
                elif full == 'status':
                    target = mock.patch.object(cli, 'status', lambda: seen.append('plain') or 0)
                else:
                    target = mock.patch.object(cli, full, lambda args: seen.append(args) or 0)
                with target:
                    self.assertEqual(self.run_cli(short, *more)[0], 0)
                self.assertEqual(len(seen), 1, seen)

    def test_help_after_a_short_word_is_its_commands_help(self):
        for short, full in (('ls', 'status'), ('rm', 'remove'), ('upgrade', 'update'), ('whoami', 'current')):
            with self.subTest(short=short):
                self.assertEqual(self.run_cli(short, '--help'), self.run_cli(full, '--help'))

    def test_a_short_word_only_counts_as_the_command_word(self):
        seen = []
        with mock.patch.object(cli, 'remove_command', lambda args: seen.append(args) or 0):
            self.run_cli('remove', 'ls', '--yes')
        self.assertEqual(seen, [['ls', '--yes']])                   # an account's short name may be "ls"

    def test_the_help_has_one_line_naming_them(self):
        lines = [x for x in inspect.cleandoc(cli.__doc__).splitlines() if 'whoami' in x]
        self.assertEqual(len(lines), 1, lines)
        for short, full in cli.SHORT_WORDS.items():
            self.assertIn(short, lines[0])
            self.assertIn(full, lines[0])


if __name__ == '__main__':
    unittest.main()
