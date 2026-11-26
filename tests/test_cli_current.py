import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# `switchboard current`: the whole email of the account Claude Code is on, in one line, for a prompt or a script.
# Exit 0. Signed out, or on a login Switchboard does not keep: a sentence on standard error and exit 1 (the email is
# still printed when it is known). `--json` gives the same facts as one document. It is quick: it reads Claude Code's
# config and the list of accounts only (no Keychain, no network, no page). `whoami` is the same command.
import json
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import __main__ as cli
from switchboard import core
from test_cli_commands import Cli, A, B, C

CURRENT_KEYS = {'schema', 'email', 'signed_in', 'in_switchboard', 'number', 'short_name', 'kind', 'label'}


class Current(Cli):
    def test_the_account_in_use_one_line_exit_0(self):
        self.two()
        self.assertEqual(self.run_cli('current'), (0, A + '\n', ''))

    def test_signed_out_says_so_on_stderr_and_exits_1(self):
        self.fake.hold(A, 'org-w')
        code, out, err = self.run_cli('current')
        self.assertEqual((code, out), (1, ''))
        self.assertEqual(err, 'Claude Code is not signed in. Run /login in Claude Code.\n')

    def test_a_login_not_kept_prints_its_email_and_exits_1(self):
        self.fake.hold(A, 'org-w')
        self.fake.sign_in(C, 'org-ivy')
        code, out, err = self.run_cli('current')
        self.assertEqual((code, out), (1, C + '\n'))
        self.assertEqual(err, '%s is not in Switchboard yet. Add it with: switchboard add\n' % C)

    def test_json_pins_every_key(self):
        a, b = self.two()
        self.fake.by_id[a]['alias'] = 'work'
        code, out, err = self.run_cli('current', '--json')
        self.assertEqual((code, err), (0, ''))
        doc = json.loads(out)
        self.assertEqual(set(doc), CURRENT_KEYS)
        self.assertEqual(list(doc)[0], 'schema')
        self.assertEqual(doc, {'schema': 1, 'email': A, 'signed_in': True, 'in_switchboard': True, 'number': 1,
                               'short_name': 'work', 'kind': 'sign_in', 'label': None})

    def test_json_signed_out_and_not_kept(self):
        self.fake.hold(A, 'org-w')
        code, out, _ = self.run_cli('current', '--json')
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out), {'schema': 1, 'email': None, 'signed_in': False, 'in_switchboard': False,
                                           'number': None, 'short_name': None, 'kind': None, 'label': None})
        self.fake.sign_in(C, 'org-ivy')
        code, out, err = self.run_cli('current', '--json')
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out), {'schema': 1, 'email': C, 'signed_in': True, 'in_switchboard': False,
                                           'number': None, 'short_name': None, 'kind': 'sign_in', 'label': None})
        self.assertIn('not in Switchboard yet', err)

    def test_the_second_account_has_its_number(self):
        self.two()
        self.fake.sign_in(B)
        code, out, _ = self.run_cli('current', '--json')
        self.assertEqual((code, json.loads(out)['number']), (0, 2))

    def test_it_never_asks_the_keychain_the_network_or_the_page(self):
        self.two()
        with mock.patch.object(cli, 'page_state', lambda *a, **k: self.fail('the page was asked')), \
                mock.patch.object(cli, 'ask_page', lambda *a, **k: self.fail('the page was asked')), \
                mock.patch.object(cli, 'local_view', lambda *a, **k: self.fail('the accounts were read in full')):
            self.assertEqual(self.run_cli('current')[0], 0)
        self.assertEqual(sorted(set(c[0] for c in self.fake.calls)), ['accounts'])     # the list, nothing else

    def test_words_it_does_not_take(self):
        self.two()
        code, out, _ = self.run_cli('current', 'now')
        self.assertEqual((code, out), (2, 'switchboard current [--json]\n'))

    def test_whoami_is_current(self):
        self.two()
        self.assertEqual(self.run_cli('whoami'), self.run_cli('current'))

    def test_the_demo_names_its_made_up_account(self):
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = True
        code, out, _ = self.run_cli('current')
        self.assertEqual((code, out), (0, 'maya@northwind.example\n'))


if __name__ == '__main__':
    unittest.main()
