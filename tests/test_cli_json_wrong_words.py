import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# With --json, standard output holds one JSON document and nothing else, also when the command line is wrong: the
# document's `refused` holds the sentence, the sentence goes to standard error too, and the exit code is 2. Every
# command that takes --json, one wrong command line each.
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import config
from test_cli_commands import Cli

WRONG = (('current', '--json', 'x'),
         ('leftovers', '--json', 'x'),
         ('leftovers', 'clear', '--json'),
         ('folders', '--json', '--bogus'),
         ('folders', '--check', '--prune', '--json'),
         ('config', '--json', '--bogus'),
         ('config', 'set', 'awake', 'on', '--json'),
         ('config', 'path', '--json'),
         ('log', '--json', '--bogus'),
         ('chats', '--json', '--bogus'),
         ('owner', 'https://claude.ai/public/artifacts/x', '--json', '--bogus'),
         ('switch', '--json', '--bogus'),
         ('use', '--json'),
         ('auto', 'check', '--json', '--bogus'),
         ('auto', 'why', '--json', '--bogus'))


class WrongWordsWithJson(Cli):
    def test_each_gives_one_document_with_the_refusal_and_exit_2(self):
        self.two()
        for args in WRONG:
            with self.subTest(args=args):
                code, out, err = self.run_cli(*args)
                self.assertEqual(code, 2, out)
                doc = json.loads(out)                               # the whole of standard output is the document
                self.assertEqual(doc['schema'], 1)
                said = doc['problem'] if args[0] == 'auto' else doc['refused']     # the check's own document
                if args[0] == 'auto':
                    self.assertEqual(doc['outcome'], 'refused')
                self.assertIsInstance(said, str)
                self.assertTrue(said.strip())
                self.assertTrue(err.strip(), 'the sentence goes to standard error too')

    def test_without_json_the_usage_is_said_as_before(self):
        code, out, _ = self.run_cli('current', 'x')
        self.assertEqual((code, out), (2, 'switchboard current [--json]\n'))
        code, out, _ = self.run_cli('config', '--bogus')
        self.assertEqual((code, out), (2, config.USAGE + '\n'))


class ConfigCommandsAsJson(Cli):
    def test_as_commands_with_json_is_one_document_with_the_commands_in_a_list(self):
        self.two()
        self.run_cli('config', 'set', 'awake', 'off')
        self.run_cli('config', 'set', 'auto_policy.weekly_at', '88')
        for args in (('--json', '--as-commands'), ('--json', '--changed', '--as-commands')):
            with self.subTest(args=args):
                code, out, err = self.run_cli('config', *args)
                self.assertEqual((code, err), (0, ''))
                doc = json.loads(out)
                self.assertEqual(list(doc)[0], 'schema')
                self.assertEqual(doc['commands'], ['switchboard config set auto_policy.weekly_at 88',
                                                   'switchboard config set awake off'])
        code, plain, _ = self.run_cli('config', '--as-commands')
        self.assertEqual(plain.splitlines(), doc['commands'])


if __name__ == '__main__':
    unittest.main()
