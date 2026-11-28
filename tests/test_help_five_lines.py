import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The short help: `switchboard help` (and -h, --help) prints what a new person needs on one screen (firstrun's
# HELP_LINES, the first run's own words), with one line for the list and a key press on any of its rows, Codex's row
# included (within 80 columns), each line within 120. `switchboard help all` prints the full help, unchanged; do,
# route, routes and the provider commands are there only.
import io
import os
import re
import sys
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import __main__ as cli  # noqa: E402
from switchboard import firstrun  # noqa: E402
from test_providers_words import HELP_NEVER, problems  # noqa: E402

KEY = '  switchboard          your accounts and limits; press a number to move there'
FULL = cli.__doc__.strip() + '\n'


def short():
    return firstrun.short_help() + '\n'


def run(*args):
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(list(args))
    return code, out.getvalue()


class ShortHelp(unittest.TestCase):
    def test_help_is_the_short_help_on_one_screen(self):
        for args in (('help',), ('-h',), ('--help',)):
            with self.subTest(args=args):
                self.assertEqual(run(*args), (0, short()))
        lines = short().splitlines()
        self.assertLessEqual(len(lines), 10)
        self.assertLessEqual(max(len(x) for x in lines), 120)      # the onboarding test's own rule for its lines
        self.assertLessEqual(len(KEY), 80)

    def test_one_line_for_the_key_press(self):
        self.assertEqual([x for x in short().splitlines() if 'press a number' in x], [KEY])

    def test_the_commands_it_names(self):
        commands = [re.split(r'\s{2,}', x.strip())[0] for x in short().splitlines() if x.startswith('  switchboard')]
        self.assertEqual(commands, ['switchboard', 'switchboard add', 'switchboard page', 'switchboard doctor',
                                    'switchboard help all'])

    def test_help_all_is_the_full_help_unchanged(self):
        self.assertEqual(run('help', 'all'), (0, FULL))
        self.assertEqual(run('--help', 'all'), (0, FULL))
        for cmd in ('do', 'route', 'routes', 'providers', 'continue', 'back', 'hand', 'allow', 'stay', 'order',
                    'add PROVIDER'):
            with self.subTest(cmd=cmd):
                self.assertIn('switchboard %s' % cmd, FULL)
                self.assertIsNone(re.search(r'switchboard %s\b' % cmd, short()))     # do: not doctor

    def test_plain_words(self):
        self.assertEqual(problems(short()), [])
        self.assertIsNone(HELP_NEVER.search(short()))


if __name__ == '__main__':
    unittest.main()
