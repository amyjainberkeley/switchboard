"""A forced switch away from an emptied login records the account it left, so the put-back never writes that
account's sign-in back when a chat writes its name into the settings and the item is lost.

An emptied login (its tokens blank after Anthropic refused it) proves no account by itself. Claude Code's settings
named the account, and that is the account the switch left: the switch's record says so, for the put-back's rule
"never put back the account a switch just left". What the switch keeps of the outgoing login does not change (an
emptied login is never saved), History says what it said before, and every other switch's record is as it was (each
record now also keeps the sign-in end of the login it left, 'left_rx', when that is known).
Made-up *.example accounts on the engine's stand-ins (tests/fakes_accounts.py).
"""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import time
import unittest

from fakes_accounts import Rig, identity_block
from switchboard.accounts import capture, context, putback, roster, switcher, tokens, vault


class Clock(object):
    def __init__(self):
        self.jump = 0.0

    def wall(self):
        return time.time() + self.jump

    def mono(self):
        return time.monotonic() + self.jump

    def advance(self, seconds):
        self.jump += seconds


def emptied(text, keep_date=True):
    blob = json.loads(text)
    blob['claudeAiOauth'].update({'accessToken': '', 'refreshToken': ''})
    if not keep_date:
        blob['claudeAiOauth'].pop('refreshTokenExpiresAt', None)
    return json.dumps(blob, separators=(',', ':'))


class Case(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.rig = Rig(wall=self.clock.wall, mono=self.clock.mono)
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        capture.reset()
        putback.reset()
        self.rig.keep('uuid-a', 'a@work.example', 'org-a')
        self.clock.advance(2 * 86400)
        self.held_b = self.rig.keep('uuid-b', 'b@home.example', 'org-b')      # Claude Code is on B
        roster.forget_live()
        self.rig.events[:] = []

    def tearDown(self):
        self.use.__exit__(None, None, None)
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        self.rig.close()

    def writes(self):
        return sum(1 for c in self.rig.kc.calls if c[0] == 'write' and c[1] == self.rig.service)

    def last_switch(self):
        rec = dict(vault.load(self.ctx)['switches'][-1])
        rec.pop('at')
        return rec

    @staticmethod
    def as_before(rec, left=None):
        """The record as before, plus the one key a switch's record carries now: 'left_rx', the sign-in end of the
        login it left, so the put-back can tell a new sign-in of that account from a renewal (no key when that end is
        not known)."""
        rx = json.loads(left)['claudeAiOauth'].get('refreshTokenExpiresAt') if left else None
        return dict(rec, left_rx=rx) if rx else dict(rec)


class AForcedSwitchFromAnEmptiedLogin(Case):
    def test_the_put_back_never_writes_the_account_it_left(self):
        for keep_date in (True, False):
            with self.subTest(keep_date=keep_date):
                self.tearDown()
                self.setUp()
                self.rig.set_live(emptied(self.held_b, keep_date))
                roster.forget_live()
                res = switcher.switch(self.ctx, 'uuid-a', kind='forced', source='auto', interactive=True)
                self.assertTrue(res.ok)
                self.assertEqual(self.last_switch(), self.as_before({'from': 'uuid-b', 'to': 'uuid-a', 'kind': 'forced',
                                                                     'source': 'auto'},
                                                                    emptied(self.held_b, keep_date)))
                self.assertIsNone(res.from_id)                          # what the switch returns is as before
                self.assertEqual([e[2].get('from') for e in self.rig.events if e[2].get('h') == 'switch'], [None])
                self.clock.advance(60)
                self.rig.set_config(identity_block('uuid-b', 'b@home.example', 'org-b'))  # a chat writes B back
                self.rig.sec.drop(self.rig.service, self.rig.account)                    # and the item is lost
                roster.forget_live()
                before = self.writes()
                looks = []
                for _ in range(12):                                     # half an hour of looks
                    looks.append(putback.look(self.ctx).action)
                    self.clock.advance(150)
                self.assertEqual(self.writes(), before, looks)
                self.assertNotIn('put_back', looks)
                self.assertIsNone(self.rig.live_text())


class EveryOtherSwitchIsRecordedAsBefore(Case):
    def test_a_switch_from_a_working_login(self):
        self.assertTrue(switcher.switch(self.ctx, 'uuid-a', kind='manual', source='cli', interactive=True).ok)
        self.assertEqual(self.last_switch(), self.as_before({'from': 'uuid-b', 'to': 'uuid-a', 'kind': 'manual',
                                                             'source': 'cli'}, self.held_b))

    def test_a_switch_from_a_login_of_no_kept_account(self):
        c = self.rig.sign_in('uuid-c', 'c@other.example', 'org-c')   # an account Switchboard does not keep
        roster.forget_live()
        self.assertTrue(switcher.switch(self.ctx, 'uuid-a', kind='manual', source='cli', interactive=True).ok)
        self.assertEqual(self.last_switch(), self.as_before({'from': None, 'to': 'uuid-a', 'kind': 'manual',
                                                             'source': 'cli'}, c))


if __name__ == '__main__':
    unittest.main()
