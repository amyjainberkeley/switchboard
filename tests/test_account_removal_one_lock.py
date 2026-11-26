"""Removing an account checks for its token in use and removes it, with the token's record, under one lock.

Removing an account whose one-year token Claude Code is on is refused (switch away first). Were that check made
before the switch lock is taken, another process could put Claude Code on the token between the check and the
removal: the account would go and the token's record would stay, kept for an account that is gone. Here a switch onto
the token is started right after the check; it must wait until the removal is done, and then find nothing to switch
to. Either both stay (with the refusal) or both go. Made-up tokens and accounts only; the Keychain is the suite's
stand-in."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import threading
import unittest
from unittest import mock

from fakes_accounts import Rig
from switchboard.accounts import api, bearer, context, switcher
from switchboard.accounts.api import AccountsError

TOKEN = 'sk-ant-oat01-madeUpOneYearToken-0801-aaaaaaaaaaaa'


class OneLock(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        _, self.b_text = self.rig.two_accounts()                    # Claude Code is on B
        self.rec, _ = bearer.add(self.ctx, TOKEN, for_id='uuid-a')

    def tearDown(self):
        self.use.__exit__(None, None, None)
        self.rig.close()

    def state(self):
        doc = self.rig.doc()
        return ('uuid-a' in doc['accounts'], self.rec['id'] in bearer.records(doc),
                self.rig.sec.get(bearer.SERVICES[bearer.TOKEN], self.rec['id']) is not None)

    def test_a_switch_onto_the_token_right_after_the_check_waits_and_finds_nothing(self):
        real, said, threads = bearer.refuse_in_use, [], []

        def switch_onto_the_token():
            with context.use(self.ctx):
                try:
                    said.append(switcher.switch(self.ctx, self.rec['id'], source='cli').ok)
                except AccountsError as e:
                    said.append(str(e))

        def checked_then_a_switch(*args, **kwargs):
            real(*args, **kwargs)
            if not threads:
                t = threading.Thread(target=switch_onto_the_token)
                threads.append(t)
                t.start()
                t.join(2.0)                     # long enough for a switch that does not wait for the lock
        with mock.patch.object(bearer, 'refuse_in_use', checked_then_a_switch):
            api.remove('uuid-a')
        threads[0].join(60.0)
        self.assertFalse(threads[0].is_alive())
        self.assertEqual(self.state(), (False, False, False))
        self.assertEqual(said, ['No saved account matches that.'])
        self.assertEqual(self.rig.live_text(), self.b_text)

    def test_on_the_token_both_stay_with_the_refusal(self):
        self.assertTrue(switcher.switch(self.ctx, self.rec['id'], source='cli').ok)
        with self.assertRaises(AccountsError) as e:
            api.remove('uuid-a')
        self.assertEqual(str(e.exception), bearer.IN_USE % ('a@example.com', 'remove it', 'removed'))
        self.assertEqual(self.state(), (True, True, True))

    def test_away_from_the_token_both_go(self):
        api.remove('uuid-a')
        self.assertEqual(self.state(), (False, False, False))
        self.assertIn('Removed the one-year token for a@example.com from Switchboard',
                      [e[0] for e in self.rig.events])


if __name__ == '__main__':
    unittest.main()
