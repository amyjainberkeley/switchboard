import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# `switchboard switch [NAME] --json` and `switchboard use NAME --json`: the same switch, then one JSON document a
# script can read: whether Claude Code moved, from which account, to which, why (the reason History records) and
# when. When it refuses, the document says why and the exit code is the plain command's own. Standard output holds
# the document and nothing else; what the plain command says goes to standard error.
# The engine is tests/fakes_accounts_api.py; the page, when it runs, is test_cli_with_page's stand-in.
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import __main__ as cli
from switchboard.accounts import api
from test_cli_commands import Cli, A, B, C
from test_cli_with_page import FakePage

# The shape, pinned. A change that removes or renames one of these keys must raise "schema" in switchboard/report.py
# (and say so in docs/commands.md); adding a key does not, and adds it here.
SWITCH_KEYS = {'schema', 'moved', 'from', 'to', 'in_use', 'reason', 'at', 'refused', 'from_number', 'to_number'}
ISO_Z = r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$'


class SwitchJson(Cli):
    def doc(self, *args):
        """(exit code, the document, stderr). Standard output must be exactly one JSON document."""
        code, out, err = self.run_cli(*args)
        self.assertNotIsInstance(code, str, 'it raised instead of printing the document: %s' % code)
        doc = json.loads(out)
        self.assertEqual(set(doc), SWITCH_KEYS)
        self.assertEqual(list(doc)[0], 'schema')
        self.assertEqual(doc['schema'], 1)
        return code, doc, err


class ItMoved(SwitchJson):
    def test_switch_name_moves_and_says_from_to_why_and_when(self):
        self.two()
        code, doc, err = self.doc('switch', B, '--json')
        self.assertEqual(code, 0)
        self.assertEqual((doc['moved'], doc['from'], doc['to'], doc['in_use']), (True, A, B, B))
        self.assertEqual(doc['reason'], 'Picked in a terminal.')
        self.assertRegex(doc['at'], ISO_Z)
        self.assertIsNone(doc['refused'])
        self.assertEqual(self.fake.block()['emailAddress'], B)
        self.assertIn('Claude Code is on %s.' % B, err)             # the plain words go to standard error

    def test_json_may_come_before_the_name(self):
        self.two()
        code, doc, _ = self.doc('switch', '--json', '2')
        self.assertEqual((code, doc['moved'], doc['to']), (0, True, B))

    def test_use_name_json_is_the_same(self):
        self.two()
        code, doc, _ = self.doc('use', B, '--json')
        self.assertEqual((code, doc['moved'], doc['from'], doc['to']), (0, True, A, B))

    def test_switch_alone_goes_to_the_next_one(self):
        self.two()
        self.fake.hold(C)
        code, doc, _ = self.doc('switch', '--json')
        self.assertEqual((code, doc['moved'], doc['to']), (0, True, B))

    def test_already_on_it_did_not_move_and_is_not_a_refusal(self):
        self.two()
        code, doc, _ = self.doc('switch', A, '--json')
        self.assertEqual(code, 0)
        self.assertEqual((doc['moved'], doc['from'], doc['to'], doc['in_use']), (False, A, None, A))
        self.assertEqual((doc['reason'], doc['at'], doc['refused']), (None, None, None))

    def test_through_the_running_page_the_reason_is_the_pages(self):
        self.two()
        view = cli.local_view()

        def switch_there():
            self.fake.sign_in(B)
            return 200, {'note': None}
        page = FakePage(self, {('GET', '/api/state'): (200, view), ('POST', '/api/switch'): switch_there})
        code, doc, _ = self.doc('switch', B, '--json')
        self.assertEqual(page.posted, [('/api/switch', {'email': B})])
        self.assertEqual((code, doc['moved'], doc['to'], doc['reason']), (0, True, B, 'Picked on the page.'))
        self.assertFalse([c for c in self.fake.calls if c[0] == 'switch'])      # the page did it, not this command


class ItRefused(SwitchJson):
    def test_no_such_account_exit_1_and_the_sentence(self):
        self.two()
        code, doc, _ = self.doc('switch', 'nobody', '--json')
        self.assertEqual(code, 1)
        self.assertEqual((doc['moved'], doc['from'], doc['to'], doc['in_use']), (False, A, None, A))
        self.assertEqual(doc['refused'], 'No account called "nobody". Run `switchboard` to see them.')
        self.assertEqual(self.run_cli('switch', 'nobody')[0],               # the plain command refuses the same way
                         'refused: No account called "nobody". Run `switchboard` to see them.')

    def test_the_engine_says_no(self):
        self.two()
        self.fake.fail['switch'] = api.AccountsError('keychain_locked', "macOS's Keychain is locked: unlock the Mac.")
        code, doc, _ = self.doc('use', B, '--json')
        self.assertEqual(code, 1)
        self.assertEqual((doc['moved'], doc['in_use'], doc['refused']),
                         (False, A, "macOS's Keychain is locked: unlock the Mac."))

    def test_nowhere_to_go(self):
        self.fake.hold(A, 'org-w')
        self.fake.sign_in(A, 'org-w')
        code, doc, _ = self.doc('switch', '--json')
        self.assertEqual(code, 1)
        self.assertEqual(doc['refused'], 'There is no other account to switch to. Add one with: switchboard add')

    def test_signed_out_from_is_null(self):
        self.fake.hold(A, 'org-w')
        self.fake.hold(B)
        code, doc, _ = self.doc('switch', B, '--json')
        self.assertEqual((code, doc['from'], doc['moved'], doc['in_use']), (0, None, True, B))

    def test_wrong_words_exit_2_with_the_usage_as_the_refusal(self):
        self.two()
        code, doc, err = self.doc('switch', A, B, '--json')
        self.assertEqual(code, 2)
        self.assertEqual(doc['refused'], 'switchboard switch [NAME|--best] [--json]')
        self.assertEqual(self.run_cli('switch', A, B)[0], 2)                # the plain command's own code
        code, doc, _ = self.doc('use', '--json')
        self.assertEqual((code, doc['refused']), (2, 'switchboard use NAME [--json]'))


class PlainIsUnchanged(SwitchJson):
    def test_without_json_nothing_is_json(self):
        self.two()
        code, out, err = self.run_cli('switch', B)
        self.assertEqual((code, err), (0, ''))
        self.assertTrue(out.startswith('Claude Code is on'), out)


if __name__ == '__main__':
    unittest.main()
