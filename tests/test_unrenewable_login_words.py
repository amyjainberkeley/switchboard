"""What a switch says of a login that can't be renewed and that no record owns is true whatever that login is.

Such a login may be a one-year token whose record went, an API key, a sign-in stripped of its refresh token, or an
item written by hand. Its note after a switch, and the note History keeps when a switch away from it was cut off,
say one plain thing that holds for all of them: it had no refresh token, and no copy of it was made. They never call
it a token Switchboard does not keep. Made-up tokens and accounts only; the Keychain is the suite's stand-in."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import unittest

from fakes_accounts import Rig
from switchboard.accounts import context, switcher, vault

TOKEN = 'sk-ant-oat01-madeUpOneYearToken-0901-aaaaaaaaaaaa'
KEY = 'sk-ant-api03-madeUpConsoleKey-0902-bbbbbbbbbbbb'
NOTE = 'Claude Code was on a login with no refresh token; no copy of it was made.'
PUT_AWAY = ('Put away the record of a switch that was cut off: Claude Code now holds a login Switchboard did not '
            'write, so nothing was changed (the login that was in use had no refresh token, so no copy of it was '
            'made).')
LOGINS = {
    'a one-year token': json.dumps({'claudeAiOauth': {'accessToken': TOKEN, 'refreshToken': None,
                                                      'expiresAt': None, 'scopes': ['user:inference']}}),
    'a sign-in stripped of its refresh token': json.dumps({'claudeAiOauth': {
        'accessToken': TOKEN, 'refreshToken': '', 'expiresAt': 99999999999999,
        'scopes': ['user:inference', 'user:profile'], 'subscriptionType': 'max'}}),
    'a bare API key': KEY,
    'a token in an object written by hand': json.dumps({'accessToken': TOKEN}),
}


class Case(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        self.rig.two_accounts()                                     # Claude Code is on B

    def tearDown(self):
        switcher._HOOK = None
        self.use.__exit__(None, None, None)
        self.rig.close()

    def assert_never_said(self, *texts):
        for t in list(texts) + [e[0] for e in self.rig.events]:
            self.assertNotIn('does not keep', t or '')


class TheNote(Case):
    def test_each_says_it_had_no_refresh_token_and_no_copy_was_made(self):
        for what, text in LOGINS.items():
            with self.subTest(what):
                self.rig.set_live(text)
                res = switcher.switch(self.ctx, 'uuid-a', source='cli')
                self.assertTrue(res.ok)
                self.assertEqual(res.note, NOTE)
                self.assert_never_said(res.note)
                self.assertEqual(vault.stashed(self.ctx), [])
                self.assertTrue(switcher.switch(self.ctx, 'uuid-b', source='cli').ok)


class CutOff(Case):
    def test_each_cut_off_switch_away_says_it_had_no_refresh_token(self):
        for what, text in LOGINS.items():
            with self.subTest(what):
                self.rig.set_live(text)

                def stop(name):
                    if name == 'credential_written':
                        raise KeyboardInterrupt()
                switcher._HOOK = stop
                with self.assertRaises(KeyboardInterrupt):
                    switcher.switch(self.ctx, 'uuid-a', source='cli')
                switcher._HOOK = None
                self.rig.set_live(self.rig.server.add('uuid-z', 'z@example.com'))   # a /login of its own meanwhile
                said = switcher.resolve_journal(self.ctx)
                self.assertEqual(said, PUT_AWAY)
                self.assert_never_said(said)
                self.assertEqual(vault.stashed(self.ctx), [])


if __name__ == '__main__':
    unittest.main()
