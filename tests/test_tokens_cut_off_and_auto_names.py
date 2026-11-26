"""Two things about one-year tokens that a sentence or a lookup could get wrong.

- A switch away from a token, cut off, then settled when Claude Code holds a login Switchboard did not write: what it
  says of the login that was in use is true. A kept token is in its own Keychain item (no safety copy is made of it);
  a token no record owns was not copied at all; a sign-in is in a safety copy, as before.
- `switchboard auto rank` and `auto line` find the account with the engine's own finder only: a token's name never
  gets a rank or a line, and a sign-in that answers to the same word is the one that gets it.

The engine is the real one on a made-up Mac (fakes_accounts.Rig). Made-up tokens and accounts only."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import contextlib
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from fakes_accounts import Rig
from switchboard import __main__ as cli
from switchboard.accounts import api, bearer, context, roster, switcher, vault
from switchboard.core import SwitchError

TOKEN = 'sk-ant-oat01-madeUpOneYearToken-0401-aaaaaaaaaaaa'
OWN = 'sk-ant-oat01-madeUpOneYearToken-0402-bbbbbbbbbbbb'
PUT_AWAY = ('Put away the record of a switch that was cut off: Claude Code now holds a login Switchboard did not '
            'write, so nothing was changed')


class CutOff(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.ctx = self.rig.ctx
        self.rig.two_accounts()                                     # Claude Code is on B

    def tearDown(self):
        switcher._HOOK = None
        self.rig.close()

    def cut_off_then_another_login(self, target):
        def stop(name):
            if name == 'credential_written':
                raise KeyboardInterrupt()
        switcher._HOOK = stop
        with self.assertRaises(KeyboardInterrupt):
            switcher.switch(self.ctx, target, source='cli')
        switcher._HOOK = None
        self.rig.set_live(self.rig.server.add('uuid-z', 'z@example.com'))   # a /login of its own meanwhile
        return switcher.resolve_journal(self.ctx)

    def test_away_from_a_kept_token_it_says_the_token_is_in_its_own_item(self):
        rec, _ = bearer.add(self.ctx, TOKEN, for_id='uuid-a')
        self.assertTrue(switcher.switch(self.ctx, rec['id'], source='cli').ok)
        self.assertEqual(self.cut_off_then_another_login('uuid-b'),
                         PUT_AWAY + ' (the one-year token that was in use is still kept in its own Keychain item).')
        self.assertEqual(vault.stashed(self.ctx), [])

    def test_away_from_a_token_nothing_owns_it_says_no_copy_was_made(self):
        rec, _ = bearer.add(self.ctx, TOKEN, for_id='uuid-a')
        self.assertTrue(switcher.switch(self.ctx, rec['id'], source='cli').ok)
        with vault.changing(self.ctx) as doc:
            doc['bearers'] = {}
        self.assertEqual(self.cut_off_then_another_login('uuid-b'),
                         PUT_AWAY + ' (the login that was in use had no refresh token, so no copy of it was made).')
        self.assertEqual(vault.stashed(self.ctx), [])

    def test_away_from_a_sign_in_it_still_says_the_safety_copy(self):
        self.assertEqual(self.cut_off_then_another_login('uuid-a'),
                         PUT_AWAY + ' (the login that was in use is in a safety copy).')


class AutoFindsSignInsOnly(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.rig.two_accounts()
        self.use = context.use(self.rig.ctx)
        self.use.__enter__()
        roster.forget_live()
        self.tok, _ = bearer.add(self.rig.ctx, OWN, label='spare')

    def tearDown(self):
        self.use.__exit__(None, None, None)
        self.rig.close()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.main(list(argv))
            except SwitchError as e:
                code = 'refused: %s' % e
        return code, out.getvalue(), err.getvalue()

    def test_a_sign_in_that_answers_to_a_tokens_name_is_the_one_that_gets_it(self):
        with vault.changing(self.rig.ctx) as doc:             # a list edited by hand: both answer to "spare"
            doc['accounts']['uuid-a']['alias'] = 'spare'
        for argv in (('auto', 'rank', 'spare', '1'), ('auto', 'line', 'spare', '--five-hour', '80')):
            code, _, err = self.run_cli(*argv)
            self.assertEqual(code, 0, err)
        auto = api.auto_settings()
        self.assertEqual(dict(auto.get('ranks') or {}), {'uuid-a': 1})
        self.assertEqual(list(auto.get('own_lines') or {}), ['uuid-a'])

    def test_a_tokens_name_alone_never_reaches_the_tokens_record(self):
        seen = []
        real = api.find_token
        with mock.patch.object(api, 'find_token', lambda name: seen.append(name) or real(name)), \
                mock.patch.object(bearer, 'set_rule', side_effect=AssertionError('a token was given a rule')):
            for argv in (('auto', 'rank', 'spare', '2'), ('auto', 'line', 'spare', '--week', '90')):
                code, out, _ = self.run_cli(*argv)
                self.assertTrue(str(code).startswith('refused: '), argv)
                self.assertEqual(out, '')
        auto = api.auto_settings()
        self.assertNotIn(self.tok['id'], dict(auto.get('ranks') or {}))
        self.assertNotIn(self.tok['id'], dict(auto.get('own_lines') or {}))


if __name__ == '__main__':
    unittest.main()
