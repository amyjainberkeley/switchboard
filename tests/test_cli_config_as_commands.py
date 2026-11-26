import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Your setup as commands: `switchboard config --changed` lists only what differs from the defaults, and
# `switchboard config --as-commands` prints those as the commands that set them again (a row's own command where it
# has one, else `switchboard config set KEY VALUE`), so a setup can live in a dotfile and be replayed on a new Mac.
# The lines are word for word what the page's Settings, Copy as commands gives for the same values (in Chrome).
import json
import os
import shlex
import sys
import unittest
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import config
from test_app import CHROME, ChromePage
from test_cli_commands import Cli, B


class Changed(Cli):
    def test_at_the_defaults_nothing_is_listed_and_no_command_printed(self):
        self.two()
        code, out, _ = self.run_cli('config', '--changed')
        self.assertEqual(code, 0)
        self.assertIn('Every setting is at its default.', out)
        self.assertEqual(self.run_cli('config', '--as-commands'), (0, '', ''))
        code, out, _ = self.run_cli('config', '--changed', '--json')
        self.assertEqual(json.loads(out)['settings'], [])

    def setup_of_mine(self):
        a, b = self.two()
        for args in (('auto', 'on'), ('auto_policy.weekly_at', '88.5'), ('auto_policy.model', 'Fable 5'),
                     ('auto_policy.strategy', 'most-room'), ('awake', 'off')):
            self.assertEqual(self.run_cli('config', 'set', *args)[0], 0, args)
        self.assertEqual(self.run_cli('auto', 'out', B)[0], 0)

    def test_only_what_differs_and_as_the_commands_that_set_it_again(self):
        self.setup_of_mine()
        code, out, _ = self.run_cli('config', '--changed', '--json')
        self.assertEqual([x['key'] for x in json.loads(out)['settings']],
                         ['auto', 'auto_policy.weekly_at', 'auto_policy.model', 'auto_policy.strategy',
                          'auto_policy.rules', 'awake'])
        code, out, _ = self.run_cli('config', '--as-commands')
        self.assertEqual(code, 0)
        self.assertEqual(out, 'switchboard auto on\n'
                              'switchboard config set auto_policy.weekly_at 88.5\n'
                              "switchboard config set auto_policy.model 'Fable 5'\n"
                              'switchboard config set auto_policy.strategy most-room\n'
                              'switchboard auto out %s\n'
                              'switchboard config set awake off\n' % B)

    def test_the_lines_replayed_on_a_fresh_setup_give_the_same_settings(self):
        self.setup_of_mine()
        _, lines, _ = self.run_cli('config', '--as-commands')
        _, mine, _ = self.run_cli('config', '--changed', '--json')
        for k in ('auto', 'auto_policy', 'awake'):              # a fresh setup: the same accounts, no settings
            self.run_cli('config', 'unset', k) if k != 'auto_policy' else None
        for key in ('auto_policy.weekly_at', 'auto_policy.model', 'auto_policy.strategy'):
            self.run_cli('config', 'unset', key)
        self.run_cli('auto', 'in', B)
        self.assertEqual(json.loads(self.run_cli('config', '--changed', '--json')[1])['settings'], [])
        for line in lines.splitlines():
            words = shlex.split(line)
            self.assertEqual(words[0], 'switchboard')
            self.assertEqual(self.run_cli(*words[1:])[0], 0, line)
        self.assertEqual(json.loads(self.run_cli('config', '--changed', '--json')[1]), json.loads(mine))


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class TheSameAsThePage(ChromePage):
    """The page's Settings, Copy as commands, pressed in Chrome: each of its lines is the line `config --as-commands`
    prints for the same value, and it has a line for every setting the page shows that a command sets."""

    COPY = """
      let copied = null;
      Object.defineProperty(navigator, 'clipboard', {configurable: true,
        value: {writeText: (t) => { copied = t; return Promise.resolve(); }}});
      document.execCommand = () => true;
      for (let i = 0; i < 60 && !$('[data-do="copyCommands"]'); i++) await wait(50);
      await wait(500);
      $('[data-do="copyCommands"]').click();
      for (let i = 0; i < 60 && copied === null; i++) await wait(50);
      done({text: copied});
    """

    def post(self, path, body):
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json', 'X-Switchboard': '1'})
        urllib.request.urlopen(req, timeout=10).close()

    def copied(self):
        text = self.run_page(self.COPY, path='#settings')['text']
        self.assertTrue(text and text.startswith('# Switchboard settings, as commands\n'), text)
        return [x for x in text.splitlines() if x and not x.startswith('#')]

    def same_lines(self, page):
        for line in page:
            words = shlex.split(line)
            with self.subTest(line=line):
                if words[1:3] == ['config', 'set']:
                    row, typed = config.BY_KEY[words[3]], words[4]
                    self.assertIsNotNone(row.post, line)
                elif words[1] == 'auto' and len(words) == 4:
                    row, typed = config.BY_KEY['auto_policy.rules'], None
                    mine = config.as_commands([(row, {words[3]: words[2]})])
                    self.assertEqual(mine, line + '\n')
                    continue
                else:
                    row, typed = config.BY_KEY[words[1]], words[2]
                value = config.parse(row, typed)
                if row.kind == 'text' and value == '':
                    value = None
                self.assertEqual(config.as_commands([(row, value)]), line + '\n')
        keys = set()
        for line in page:
            words = shlex.split(line)
            keys.add(words[3] if words[1:3] == ['config', 'set'] else
                     'auto_policy.rules' if words[1] == 'auto' and len(words) == 4 else words[1])
        shown = set(r.key for r in config.ROWS if r.page and (r.post or r.as_command)) - {'auto_policy.rules'}
        if 'switchboard config set auto_policy.model none' in page:
            shown.discard('auto_policy.model_at')               # its line counts for a model only
        self.assertEqual(keys - {'auto_policy.rules'}, shown)

    def test_copy_as_commands_gives_the_lines_config_prints(self):
        self.same_lines(self.copied())

    def test_a_model_with_a_space_is_quoted_the_same_way(self):
        self.post('api/auto', {'model': 'Fable 5', 'weekly_at': 88.5, 'strategy': 'most-room'})
        page = self.copied()
        self.assertIn("switchboard config set auto_policy.model 'Fable 5'", page)
        self.assertIn('switchboard config set auto_policy.weekly_at 88.5', page)
        self.same_lines(page)


if __name__ == '__main__':
    unittest.main()
