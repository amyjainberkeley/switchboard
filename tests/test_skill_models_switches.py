import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The skill names both Models switches by the commands the help gives them: `switchboard models on|off` turns Models
# by the job, `switchboard models sonnet on|off` turns Sonnet for helpers. It says the uninstaller puts Models by the
# job back, and never calls Sonnet for helpers the one model setting.
#
# Reads text only: nothing runs, nothing is written.
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)

from switchboard import __main__ as cli
from switchboard import skillfile


def read(name):
    with open(os.path.join(skillfile.SOURCE, name), encoding='utf-8') as f:
        return f.read()


def every_file():
    out = {}
    for top, _, files in os.walk(skillfile.SOURCE):
        for name in files:
            p = os.path.join(top, name)
            with open(p, encoding='utf-8') as f:
                out[os.path.relpath(p, skillfile.SOURCE)] = f.read()
    return out


class TheModelsSwitches(unittest.TestCase):
    def test_the_help_gives_each_switch_its_own_command(self):
        own = cli.command_help('models') or ''
        self.assertIn('switchboard models [on|off] Models by the job', own)
        self.assertIn('switchboard models sonnet on|off', own)

    def test_the_commands_reference_names_each_switch_by_its_command(self):
        text = read(os.path.join('reference', 'commands.md'))
        self.assertIn('`switchboard models sonnet on`', text)
        on = [line for line in text.splitlines() if '`switchboard models on`' in line]
        self.assertTrue(on, 'no line names `switchboard models on`')
        for line in on:
            self.assertIn('Models by the job', line)

    def test_no_line_turns_sonnet_for_helpers_with_the_plain_command(self):
        for name, text in every_file().items():
            for line in text.splitlines():
                if 'Sonnet for helpers' in line and ('`on` or `off`' in line or '`switchboard models on`' in line):
                    self.assertIn('switchboard models sonnet', line, name)

    def test_what_it_changes_and_puts_back_names_models_by_the_job(self):
        for name in ('claude-code.md', 'privacy.md', 'update-and-remove.md', 'what-it-does-not-do.md'):
            self.assertIn('Models by the job', read(os.path.join('reference', name)), name)

    def test_sonnet_for_helpers_is_not_called_the_one_model_setting(self):
        for name, text in every_file().items():
            self.assertNotIn('one model setting', text, name)


if __name__ == '__main__':
    unittest.main()
