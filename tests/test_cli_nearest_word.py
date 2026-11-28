import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# An unknown command names the nearest real command in the same message: "Unknown command: stauts. Did you mean
# status?", then the short help, exit 2, as before. A word close to no command says only what it said before.
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from test_cli_help_and_no_page import nothing_runs
from test_cli_commands import Cli

from switchboard import firstrun


def short_help():
    return firstrun.short_help() + '\n'


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
                                                     + short_help()))

    def test_with_help_after_it_too(self):
        self.assertEqual(self.asked('stauts', '--help'), (2, 'Unknown command: stauts. Did you mean status?\n'
                                                          + short_help()))

    def test_a_word_near_nothing_is_as_before(self):
        for typed in ('bogus', '--colour', 'x', 'now', '--stauts'):
            with self.subTest(typed=typed):
                self.assertEqual(self.asked(typed), (2, 'Unknown command: %s.\n' % typed + short_help()))

    def test_a_two_letter_command_is_never_the_answer(self):
        # Almost any short word is close to a two-letter command (do): "doc" and "dot" say what they said before it
        # was in the help, and doctor is still found from a closer word.
        for typed in ('doc', 'dot', 'dos'):
            with self.subTest(typed=typed):
                self.assertEqual(self.asked(typed), (2, 'Unknown command: %s.\n' % typed + short_help()))
        self.assertEqual(self.asked('doctr'), (2, 'Unknown command: doctr. Did you mean doctor?\n' + short_help()))


if __name__ == '__main__':
    unittest.main()
