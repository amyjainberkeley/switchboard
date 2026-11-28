import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# docs/how-it-works.md, "One account per editor window": the terminal profile it shows is valid JSON, and the words it
# hands `switchboard` (each "args" it names) start a run as that account, as the command itself takes them.
import json
import os
import re
import unittest
from unittest import mock

import test_cli_commands as cli_case          # by the module: its own tests are not run again from here

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))


def section():
    with open(os.path.join(ROOT, 'docs', 'how-it-works.md'), encoding='utf-8') as f:
        text = f.read()
    part = text[text.index('\n### One account per editor window\n'):]
    return part[:part.index('\n## ', 1)]


class TheRecipe(cli_case.Cli):
    def test_the_profile_is_json_and_its_words_start_a_run_as_the_account(self):
        text = section()
        profile = json.loads(re.search(r'```json\n(.*?)```', text, re.S).group(1))
        shown = profile['terminal.integrated.profiles.osx'][profile['terminal.integrated.defaultProfile.osx']]
        self.assertEqual(shown['path'], 'switchboard')
        a, b = self.two()
        self.fake.by_id[b]['alias'] = 'work'
        with mock.patch.object(cli_case.cli.connect, 'claude_bin', lambda: '/usr/local/bin/claude'):
            code, _, _ = self.run_cli(*shown['args'])
        self.assertEqual(code, 0)
        self.assertEqual([c for c in self.fake.calls if c[0] == 'run_as'], [('run_as', 'work', [])])
        for other in re.findall(r'`"args": (\[[^\]]*\])`', text):              # the other profiles it names
            with self.subTest(args=other):
                self.assertEqual(json.loads(other)[0], 'run')
                self.assertEqual(json.loads(other)[-1], '--')


if __name__ == '__main__':
    unittest.main()
