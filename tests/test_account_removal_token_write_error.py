"""Removing an account whose one-year token's record can't be written off the list says what stayed.

The account goes first (its own write), then the token kept for it (bearer.forget_for). When that second write of
the list fails with a disk error, the removal of the account is not undone, so nothing may escape as an error: the
account is removed, its readings go, and one History line says plainly that the token was not removed, or that its
record went and its Keychain item stayed. Made-up tokens and accounts only; the Keychain is the suite's stand-in."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import unittest
from unittest import mock

from fakes_accounts import Rig
from switchboard.accounts import api, bearer, context, vault

TOKEN = 'sk-ant-oat01-madeUpOneYearToken-0901-aaaaaaaaaaaa'


class TokenWriteError(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        self.rig.two_accounts()                                     # Claude Code is on B
        self.rec, _ = bearer.add(self.ctx, TOKEN, for_id='uuid-a')
        self.bid = self.rec['id']

    def tearDown(self):
        self.use.__exit__(None, None, None)
        self.rig.close()

    def state(self):
        doc = self.rig.doc()
        return ('uuid-a' in doc['accounts'], self.bid in bearer.records(doc),
                self.rig.sec.get(bearer.SERVICES[bearer.TOKEN], self.bid) is not None)

    def remove_with(self, fails):
        """api.remove('uuid-a') while vault.save raises a disk error for each save `fails(doc)` names."""
        real = vault.save

        def save(ctx, doc):
            if fails(doc):
                raise OSError(28, 'No space left on device')
            return real(ctx, doc)
        with mock.patch.object(vault, 'save', save):
            api.remove('uuid-a')
        return [(text, kind) for text, kind, _ in self.rig.events]

    def test_the_token_stays_whole_and_the_line_says_so(self):
        said = self.remove_with(lambda doc: self.bid not in bearer.records(doc))
        self.assertEqual(self.state(), (False, True, True))
        self.assertIn(('Removed a@example.com from Switchboard', 'info'), said)
        self.assertIn((bearer.FORGET_UNWRITTEN % ('a@example.com', 'OSError', self.bid, self.bid), 'warn'), said)
        self.assertNotIn('Removed the one-year token for a@example.com from Switchboard', [t for t, _ in said])

    def test_the_record_went_and_the_keychain_item_stayed_and_the_line_says_so(self):
        self.rig.sec.rule('delete', service=bearer.SERVICES[bearer.TOKEN], why='locked')
        seen = []

        def fails(doc):
            listed = self.bid in bearer.records(doc)
            if not listed:
                seen.append(True)                   # the save that takes the record off: it holds
            return listed and bool(seen)            # the save that would put it back: it fails
        said = self.remove_with(fails)
        self.assertEqual(self.state(), (False, False, True))
        self.assertIn(('Removed a@example.com from Switchboard', 'info'), said)
        self.assertIn((bearer.FORGET_UNLISTED % ('a@example.com', self.bid, 'OSError'), 'warn'), said)

    def test_no_part_of_the_token_is_said_or_logged(self):
        self.remove_with(lambda doc: self.bid not in bearer.records(doc))
        for line in self.rig.logs + [e[0] for e in self.rig.events]:
            self.assertNotIn(TOKEN[12:], line)


if __name__ == '__main__':
    unittest.main()
