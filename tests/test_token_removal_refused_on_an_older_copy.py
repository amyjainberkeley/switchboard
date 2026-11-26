"""Removing a kept token is refused while Claude Code's login item can only be read from its older file copy.

When the Keychain does not answer for Claude Code's login item, its plaintext file may stand in; that file can hold a
login Claude Code has since replaced, so it can't say whether Claude Code is on the token. A switch refuses then, and
so do the three ways a token's record goes: `switchboard remove` of the token, an `add --token` that replaces it, and
removing the account it is kept for. Each says that nothing was removed (or kept) and why. Made-up tokens and accounts
only; the Keychain is the suite's stand-in."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import os
import unittest

from fakes_accounts import Rig
from switchboard.accounts import api, bearer, context, facts, switcher
from switchboard.accounts.api import AccountsError

TOKEN = 'sk-ant-oat01-madeUpOneYearToken-0701-aaaaaaaaaaaa'
TOKEN2 = 'sk-ant-oat01-madeUpOneYearToken-0702-bbbbbbbbbbbb'
WHY = ("macOS didn't answer for Claude Code's login in the Keychain and only an older copy could be read, so "
       "Switchboard can't tell whether Claude Code is on that one-year token. Nothing was %s; try again in a minute.")


class OnlyAnOlderCopy(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        _, b_text = self.rig.two_accounts()                         # Claude Code is on B
        self.rec, _ = bearer.add(self.ctx, TOKEN, for_id='uuid-a')
        self.assertTrue(switcher.switch(self.ctx, self.rec['id'], source='cli').ok)
        path = facts.credentials_file(self.ctx.env, self.ctx.home)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            f.write(b_text)                                         # an older plaintext copy of another login
        self.ctx.keychain.rule('read', service=self.rig.service, why='timeout', times=-1)

    def tearDown(self):
        self.use.__exit__(None, None, None)
        self.rig.close()

    def assert_all_still_there(self):
        doc = self.rig.doc()
        self.assertIn(self.rec['id'], bearer.records(doc))
        self.assertEqual(bearer.records(doc)[self.rec['id']]['fp'], bearer.fingerprint(TOKEN))
        self.assertEqual(self.rig.sec.get(bearer.SERVICES[bearer.TOKEN], self.rec['id']), TOKEN)
        self.assertIn('uuid-a', doc['accounts'])

    def test_removing_the_token_is_refused(self):
        with self.assertRaises(AccountsError) as e:
            bearer.remove(self.ctx, self.rec['id'])
        self.assertEqual(str(e.exception), WHY % 'removed')
        self.assert_all_still_there()

    def test_a_new_token_in_its_place_is_refused(self):
        with self.assertRaises(AccountsError) as e:
            bearer.add(self.ctx, TOKEN2, for_id='uuid-a')
        self.assertEqual(str(e.exception), WHY % 'kept')
        self.assert_all_still_there()

    def test_removing_the_account_it_is_kept_for_is_refused(self):
        with self.assertRaises(AccountsError) as e:
            api.remove('uuid-a')
        self.assertEqual(str(e.exception), WHY % 'removed')
        self.assert_all_still_there()

    def test_once_the_keychain_answers_it_goes_as_before(self):
        self.ctx.keychain.rules[:] = []
        self.assertTrue(switcher.switch(self.ctx, 'uuid-b', source='cli').ok)
        self.assertEqual(bearer.remove(self.ctx, self.rec['id']), 'a@example.com')


if __name__ == '__main__':
    unittest.main()
