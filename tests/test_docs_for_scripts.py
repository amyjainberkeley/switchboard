import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# docs/commands.md says what a script can rely on: rows for `current`, `switch NAME --json` and `config` in Accounts,
# right after `switchboard use NAME`; "Settings from the terminal" and "For scripts" right before "The accounts as
# JSON"; the switch document shown there has exactly the keys the command prints; ONE table of exit codes, with a row
# for each command scripts use. Reads files only.
import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)

from switchboard import config, report

DOC = os.path.join(ROOT, 'docs', 'commands.md')


def read(path=None):
    with open(path or DOC, encoding='utf-8') as f:
        return f.read()


def section(text, title):
    start = text.index('## %s\n' % title)
    nxt = text.find('\n## ', start + 3)
    return text[start:nxt if nxt != -1 else len(text)]


class ForScripts(unittest.TestCase):
    def setUp(self):
        self.text = read()

    def test_the_rows_follow_use_in_accounts(self):
        rows = [x for x in section(self.text, 'Accounts').splitlines() if x.startswith('| `')]
        names = [re.match(r'^\| `([^`]+)`', x).group(1) for x in rows]
        at = names.index('switchboard use NAME')
        self.assertEqual(names[at + 1:at + 4], ['switchboard current', 'switchboard switch NAME --json',
                                                'switchboard config'])

    def test_the_sections_come_right_before_the_accounts_as_json(self):
        heads = re.findall(r'^## (.+)$', self.text, re.M)
        at = heads.index('The accounts as JSON')
        self.assertEqual(heads[at - 2:at], ['Settings from the terminal', 'For scripts'])

    def test_the_switch_document_shown_has_the_commands_keys(self):
        block = re.search(r'```json\n(.+?)\n```', section(self.text, 'For scripts'), re.S).group(1)
        shown = json.loads(block)
        self.assertEqual(list(shown), list(report.switched('a@x.example', 'b@x.example', 'r', 0)))
        self.assertEqual(list(shown)[0], 'schema')

    def test_one_table_of_exit_codes_with_a_row_for_each_command_scripts_use(self):
        part = section(self.text, 'For scripts')
        tables = re.findall(r'^\| Command \| 0 \| 1 \| 2 \| Other codes \|$', self.text, re.M)
        self.assertEqual(len(tables), 1)
        rows = [x for x in part.splitlines() if x.startswith('| `switchboard')]
        for cmd in ('switchboard status --json', 'switchboard switch [NAME] --json', 'switchboard current [--json]',
                    'switchboard config ...', 'switchboard owner LINK --json'):
            self.assertTrue(any(x.startswith('| `%s`' % cmd) for x in rows), cmd)

    def test_every_config_key_a_person_sets_is_named(self):
        part = section(self.text, 'Settings from the terminal')
        for r in config.ROWS:
            if r.post or r.command in ('switchboard %s on|off' % r.key,):
                self.assertIn('`%s`' % r.key, part)


if __name__ == '__main__':
    unittest.main()
