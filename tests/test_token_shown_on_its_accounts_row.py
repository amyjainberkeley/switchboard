"""A one-year token kept for an account is said on that account's own row whatever the row is called: matched by the
account's id, never by its email. So it shows for one of two accounts with one email (whose rows are named "email
(Organization)") and for an email kept with capitals, in the terminal and in `status --json` alike (the page:
tests/test_page_tokens.py). The engine is the real one on a made-up Mac (fakes_accounts.Rig). Made-up tokens and
accounts only."""
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
from switchboard.accounts import bearer, context, roster, vault
from switchboard.core import SwitchError

TOKEN = 'sk-ant-oat01-madeUpOneYearToken-0601-aaaaaaaaaaaa'
TWIN = 'maya@twin.example'
ALSO = 'also a one-year token'


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
        self.assertEqual(code, 0, err)
        return out.getvalue()

    def said_on(self, out, row_name):
        """The lines of `switchboard status` that belong to the row named row_name (its line, and those under it)."""
        lines, take = [], False
        for line in out.splitlines():
            if line[:1].isdigit():
                take = row_name in line
            elif line and not line.startswith(' '):
                take = False
            if take:
                lines.append(line)
        return '\n'.join(lines)


class TwoAccountsWithOneEmail(Case):
    def test_the_token_is_said_on_its_own_accounts_row_only(self):
        self.rig.sign_in('uuid-m', TWIN, 'org-n', name='Northwind')
        roster.add_current(self.ctx)
        self.rig.sign_in('uuid-m', TWIN, 'org-p', name='Fernhill')
        roster.add_current(self.ctx)
        bearer.add(self.ctx, TOKEN, for_id='uuid-m')
        out = self.run_cli('status')
        self.assertEqual(out.count(ALSO), 1, out)
        self.assertIn(ALSO, self.said_on(out, '%s (Northwind)' % TWIN))
        doc = json.loads(self.run_cli('status', '--json'))
        got = dict((a['organization'], a['one_year_added_at']) for a in doc['accounts'] if a['email'] == TWIN)
        self.assertTrue(got['Northwind'] and got['Northwind'].endswith('Z'), got)
        self.assertIsNone(got['Fernhill'])


class AnEmailKeptWithCapitals(Case):
    def test_the_token_is_said_on_its_row(self):
        self.rig.two_accounts()
        with vault.changing(self.ctx) as doc:
            doc['accounts']['uuid-a']['email'] = 'A@Example.com'
        bearer.add(self.ctx, TOKEN, for_id='uuid-a')
        out = self.run_cli('status')
        self.assertEqual(out.count(ALSO), 1, out)
        self.assertIn(ALSO, self.said_on(out, 'a@example.com'))
        doc = json.loads(self.run_cli('status', '--json'))
        got = dict((a['email'], a['one_year_added_at']) for a in doc['accounts'])
        self.assertTrue(got['a@example.com'] and got['a@example.com'].endswith('Z'), got)
        self.assertIsNone(got['b@example.com'])


class MatchedByIdNotByName(unittest.TestCase):
    """The page's state as the terminal and `status --json` read it: a token whose account's name is said another
    way than the row's is still that row's, and a token for an account that is not listed is no row's."""

    def view(self, for_id):
        now = 1764000000.0
        return {'now': now, 'auto': {},
                'accounts': [{'id': 'uuid-a', 'email': 'a@example.com', 'slot': 1},
                             {'id': 'uuid-b', 'email': 'b@example.com', 'slot': 2, 'active': True}],
                'tokens': [{'id': 'token-0001', 'kind': 'token', 'for': 'A@Example.com (as said elsewhere)',
                            'for_id': for_id, 'name': 'A@Example.com', 'label': None, 'alias': None,
                            'added_at': now - 3600, 'last4': 'aaaa', 'rule': 'out', 'active': False}]}

    def test_the_terminal_and_the_json_match_by_id(self):
        from switchboard import report, term
        rows = term.read(self.view('uuid-a'))['accounts']
        self.assertEqual([bool(r['token']) for r in rows], [True, False])
        doc = report.document(self.view('uuid-a'), '0.0.0')
        self.assertEqual([bool(a['one_year_added_at']) for a in doc['accounts']], [True, False])
        rows = term.read(self.view('uuid-gone'))['accounts']
        self.assertEqual([bool(r['token']) for r in rows], [False, False])
        doc = report.document(self.view('uuid-gone'), '0.0.0')
        self.assertEqual([bool(a['one_year_added_at']) for a in doc['accounts']], [False, False])


if __name__ == '__main__':
    unittest.main()
