"""A one-year token or an API key kept as an account follows the rules every account follows on every surface:

- two accounts with one email: a token kept for one of them names it by its long name ("email (Organization)") in
  every sentence, as the plain switch line does, while History's facts keep the email, as they do for a sign-in;
- `switchboard switch NAME --json` and `use NAME --json` onto a token, or away from one: one document that says it
  moved, names the token as the terminal does, and gives no number (a token has none to type); a wrong command line
  is still one document;
- a token's label or short name edited by hand is cleaned (outside.said) before any surface prints it.

The engine is the real one on a made-up Mac (fakes_accounts.Rig: the suite's stand-in Keychain and Anthropic); the
page is not running. Made-up tokens and accounts only."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import contextlib
import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from fakes_accounts import Rig
from switchboard import __main__ as cli
from switchboard import engine
from switchboard.accounts import bearer, context, roster, vault
from switchboard.core import SwitchError

TOKEN = 'sk-ant-oat01-madeUpOneYearToken-0301-aaaaaaaaaaaa'
OWN = 'sk-ant-oat01-madeUpOneYearToken-0302-bbbbbbbbbbbb'
TWIN = 'maya@twin.example'


class Case(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        roster.forget_live()

    def tearDown(self):
        self.use.__exit__(None, None, None)
        self.rig.close()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        feed = io.StringIO('')
        feed.isatty = lambda: False
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), mock.patch.object(sys, 'stdin', feed):
            try:
                code = cli.main(list(argv))
            except SwitchError as e:
                code = 'refused: %s' % e
        roster.forget_live()
        return code, out.getvalue(), err.getvalue()


class TwoAccountsWithOneEmail(Case):
    def setUp(self):
        super().setUp()
        self.rig.sign_in('uuid-m', TWIN, 'org-n', name='Northwind')
        roster.add_current(self.ctx)
        self.rig.sign_in('uuid-m', TWIN, 'org-p', name="maya's Organization")
        roster.add_current(self.ctx)                    # Claude Code is on the second, Personal
        self.assertEqual(sorted(self.rig.doc()['accounts']), ['uuid-m', 'uuid-m/org-p'])
        bearer.add(self.ctx, TOKEN, for_id='uuid-m', alias='nw')

    def test_every_sentence_names_the_account_by_its_long_name(self):
        code, out, err = self.run_cli('switch', 'nw')
        self.assertEqual(code, 0, err)
        self.assertIn('Claude Code is on the one-year token for %s (Northwind).' % TWIN, out)
        said = [(text, facts) for text, kind, facts in self.rig.events if kind == 'switch']
        self.assertTrue(said[-1][0].startswith('Switched Claude Code to the one-year token for %s (Northwind).'
                                               % TWIN), said[-1][0])
        self.assertEqual(said[-1][1]['account'], TWIN)          # History's facts keep the email, as for a sign-in
        code, out, err = self.run_cli('status')
        self.assertIn('Claude Code is on the one-year token for %s (Northwind)' % TWIN, out)
        code, _, _ = self.run_cli('move', 'nw', '1')
        self.assertEqual(code, 'refused: nw is the one-year token for %s (Northwind); switchboard move is for '
                               'sign-ins.' % TWIN)
        code, out, err = self.run_cli('switch', '2')
        self.assertEqual(code, 0, err)
        said = [(text, facts) for text, kind, facts in self.rig.events if kind == 'switch']
        self.assertEqual(said[-1][1]['frm'], 'the one-year token for %s (Northwind)' % TWIN)


class SwitchJson(Case):
    def setUp(self):
        super().setUp()
        self.rig.two_accounts()                         # Claude Code is on b@example.com, number 2
        bearer.add(self.ctx, TOKEN, for_id='uuid-a', alias='a-tok')
        bearer.add(self.ctx, OWN, label='spare')

        def identity():                                 # what Claude Code's config names, on the made-up Mac
            b = self.rig.config().get('oauthAccount') or {}
            email = (b.get('emailAddress') or '').strip().lower()
            return (email, b.get('organizationUuid') or '') if email else None
        patch = mock.patch.object(engine, 'identity', identity)
        patch.start()
        self.addCleanup(patch.stop)

    def doc(self, *argv, code=0):
        got, out, err = self.run_cli(*argv)
        self.assertEqual(got, code, err)
        return json.loads(out)                          # one document, and nothing else on standard output

    def test_onto_a_token_and_back_each_is_one_document_that_says_it_moved(self):
        doc = self.doc('switch', 'spare', '--json')
        self.assertEqual((doc['moved'], doc['from'], doc['to'], doc['in_use'], doc['refused']),
                         (True, 'b@example.com', 'spare', None, None))
        self.assertEqual((doc['from_number'], doc['to_number']), (2, None))
        doc = self.doc('use', 'a-tok', '--json')
        self.assertEqual((doc['moved'], doc['from'], doc['to'], doc['in_use']),
                         (True, 'spare', 'a@example.com', None))
        self.assertEqual((doc['from_number'], doc['to_number']), (None, None))
        doc = self.doc('switch', 'b@example.com', '--json')
        self.assertEqual((doc['moved'], doc['from'], doc['to'], doc['in_use']),
                         (True, 'a@example.com', 'b@example.com', 'b@example.com'))
        self.assertEqual((doc['from_number'], doc['to_number']), (None, 2))
        doc = self.doc('switch', 'b@example.com', '--json')
        self.assertFalse(doc['moved'])

    def test_a_wrong_command_line_is_still_one_document(self):
        for argv in (('switch', 'spare', '--json', '--bogus'), ('use', 'spare', 'more', '--json')):
            with self.subTest(argv=argv):
                doc = self.doc(*argv, code=2)
                self.assertTrue(doc['refused'])
                self.assertFalse(doc['moved'])


class TextEditedByHand(Case):
    def test_a_label_and_a_short_name_are_cleaned_before_they_are_printed(self):
        self.rig.two_accounts()
        rec, _ = bearer.add(self.ctx, OWN, label='spare')
        with vault.changing(self.ctx) as doc:
            doc['bearers'][rec['id']].update(label='sp\x1b[2Jare‮', alias='x\x07y')
        outs = []
        for argv in (('status',), ('status', '--json'), ('auto', 'last', rec['id']), ('switch', rec['id']),
                     ('switch', 'b@example.com'), ('remove', rec['id'], '--yes')):
            code, out, err = self.run_cli(*argv)
            self.assertIn(code, (0, None), (argv, code, err))
            outs.append(out + err)
        said = ''.join(outs)
        self.assertIn('sp[2Jare', said)
        bad = sorted(set(c for c in said if c != '\n' and not c.isprintable()))
        self.assertEqual(bad, [])


if __name__ == '__main__':
    unittest.main()


class AnOrdinarySwitch(Case):
    def setUp(self):
        super().setUp()
        self.rig.two_accounts()                         # Claude Code is on b@example.com, number 2

    def test_history_says_which_account_claude_code_left(self):
        # Joined from two lines, the switch once used one name for the token it left and for Claude Code's save lock,
        # so every switch's History said it came from "the one-year token for " and named no account.
        code, out, err = self.run_cli('switch', '1')
        self.assertEqual(code, 0, err)
        said = [facts for text, kind, facts in self.rig.events if kind == 'switch']
        self.assertIn('b@example.com', said[-1].get('frm') or '')
        self.assertNotIn('one-year token', said[-1].get('frm') or '')
