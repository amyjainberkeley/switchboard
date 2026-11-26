import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# An unknown command names the nearest real command in the same message: "Unknown command: stauts. Did you mean
# status?", then the help, exit 2, as before. A word close to no command says only what it said before.
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from test_cli_help_and_no_page import MAIN_HELP, nothing_runs
from test_cli_commands import Cli


class NearestWord(Cli):
    def asked(self, *args):
        with nothing_runs() as ran:
            code, out, err = self.run_cli(*args)
        self.assertEqual((err, ran), ('', []))
        return code, out

    def test_a_typing_mistake_names_the_command(self):
        for typed, meant in (('stauts', 'status'), ('swtich', 'switch'), ('confg', 'config'), ('curent', 'current'),
                             ('doctr', 'doctor'), ('upgarde', 'upgrade'), ('remvoe', 'remove'), ('lst', 'list')):
            with self.subTest(typed=typed):
                self.assertEqual(self.asked(typed), (2, 'Unknown command: %s. Did you mean %s?\n' % (typed, meant)
                                                     + MAIN_HELP))

    def test_with_help_after_it_too(self):
        self.assertEqual(self.asked('stauts', '--help'), (2, 'Unknown command: stauts. Did you mean status?\n'
                                                          + MAIN_HELP))

    def test_a_word_near_nothing_is_as_before(self):
        for typed in ('bogus', '--colour', 'x', 'now', '--stauts'):
            with self.subTest(typed=typed):
                self.assertEqual(self.asked(typed), (2, 'Unknown command: %s.\n' % typed + MAIN_HELP))


if __name__ == '__main__':
    unittest.main()
