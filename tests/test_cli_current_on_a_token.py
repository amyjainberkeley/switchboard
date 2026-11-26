"""`switchboard current` names what Claude Code is on when that is a one-year token kept in Switchboard.

On a token kept as an account of its own, `current` prints that account's name (its label, as `status --json` gives
it) and exits 0; `--json` says the same, never "not signed in". On a token kept for an account, it names that
account. `kind` says which it is ('sign_in' or 'one_year', as in `status --json`), and `label` gives a token's own
name. The engine is the real one on a made-up Mac (fakes_accounts.Rig); the page is not running."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import unittest
from unittest import mock

from switchboard import engine
from test_cli_tokens import TOKEN, Case

OWN = TOKEN.replace('0101', '0303')


class Current(Case):
    def setUp(self):
        super().setUp()
        patch = mock.patch.object(engine, 'claude_json', self.rig.config_path)    # the made-up Mac's settings
        patch.start()
        self.addCleanup(patch.stop)
        self.run_cli('add', '--token', '-', '--for', 'a@example.com', '--as', 'a-tok', stdin=TOKEN)
        self.run_cli('add', '--token', '-', '--label', 'spare', stdin=OWN)

    def current(self):
        code, out, err = self.run_cli('current')
        jcode, jout, _ = self.run_cli('current', '--json')
        self.assertEqual(code, jcode)
        return code, out, err, json.loads(jout)

    def in_use_in_status(self):
        doc = json.loads(self.run_cli('status', '--json')[1])
        return doc['in_use'], [(a['email'], a['label'], a['kind']) for a in doc['accounts'] if a['in_use']]

    def test_on_a_token_of_its_own_it_names_that_token(self):
        code, _, err = self.run_cli('switch', 'spare')
        self.assertEqual(code, 0, err)
        code, out, err, doc = self.current()
        self.assertEqual((code, out, err), (0, 'spare\n', ''))
        self.assertEqual(doc, {'schema': 1, 'email': None, 'signed_in': True, 'in_switchboard': True, 'number': None,
                               'short_name': None, 'kind': 'one_year', 'label': 'spare'})
        self.assertEqual(self.in_use_in_status(), (None, [(None, 'spare', 'one_year')]))

    def test_on_a_token_kept_for_an_account_it_names_the_account(self):
        code, _, err = self.run_cli('switch', 'a-tok')
        self.assertEqual(code, 0, err)
        code, out, err, doc = self.current()
        self.assertEqual((code, out, err), (0, 'a@example.com\n', ''))
        self.assertEqual(doc, {'schema': 1, 'email': 'a@example.com', 'signed_in': True, 'in_switchboard': True,
                               'number': 1, 'short_name': None, 'kind': 'one_year', 'label': None})

    def test_on_a_sign_in_it_is_as_before_with_its_kind(self):
        code, out, err, doc = self.current()
        self.assertEqual((code, out, err), (0, 'b@example.com\n', ''))
        self.assertEqual(doc, {'schema': 1, 'email': 'b@example.com', 'signed_in': True, 'in_switchboard': True,
                               'number': 2, 'short_name': None, 'kind': 'sign_in', 'label': None})
        self.assertEqual(self.in_use_in_status()[0], 'b@example.com')


if __name__ == '__main__':
    unittest.main()
