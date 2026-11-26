import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# `switchboard status` in a folder set to run as an account (`switchboard folder NAME`): at a terminal, one line under
# the list says so; `status --json` says it in `this_folder` (the account and the folder that set it, or null). Nothing
# is added where no folder applies. The engine is tests/fakes_accounts_api.py. Made-up accounts and folders.
import json
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import __main__ as cli
from switchboard.accounts import folders
from test_cli_commands import Cli, A, B


class ThisFolder(Cli):
    def setUp(self):
        super().setUp()
        self.addCleanup(os.chdir, os.getcwd())
        self.home = os.path.realpath(os.environ['HOME'])
        self.client = os.path.join(self.home, 'work', 'client')
        self.src = os.path.join(self.client, 'src')
        self.elsewhere = os.path.join(self.home, 'elsewhere')
        for p in (self.src, self.elsewhere):
            os.makedirs(p)
        self.two()
        os.chdir(self.client)
        self.assertEqual(self.run_cli('folder', B)[0], 0)

    def status(self, where, at_terminal):
        os.chdir(where)
        with mock.patch.object(cli, 'terminal', lambda: at_terminal):
            code, out, err = self.run_cli('status')
        self.assertEqual((code, err), (0, ''))
        return out

    def test_at_a_terminal_one_line_under_the_list(self):
        out = self.status(self.src, True)
        self.assertTrue(out.endswith('\nThis folder runs as %s (set for ~/work/client).\n' % B), out)
        self.assertEqual(out.count('This folder runs as'), 1)
        plain = self.status(self.elsewhere, True)
        self.assertNotIn('This folder', plain)
        self.assertEqual(out, plain + 'This folder runs as %s (set for ~/work/client).\n' % B)

    def test_not_at_a_terminal_nothing_is_added(self):
        self.assertEqual(self.status(self.src, False), self.status(self.elsewhere, False))

    def test_as_json_this_folder_names_the_account_and_the_folder_or_is_null(self):
        os.chdir(self.src)
        code, out, err = self.run_cli('status', '--json')
        self.assertEqual((code, err), (0, ''))
        doc = json.loads(out)
        self.assertEqual(doc['this_folder'], {'account': B, 'folder': self.client})
        self.assertEqual(doc['schema'], 1)
        os.chdir(self.elsewhere)
        self.assertIsNone(json.loads(self.run_cli('status', '--json')[1])['this_folder'])
        folders.clear(self.client)
        os.chdir(self.src)
        self.assertIsNone(json.loads(self.run_cli('status', '--json')[1])['this_folder'])
        self.assertNotIn(A, [doc['this_folder']['account']])


if __name__ == '__main__':
    unittest.main()
