"""The put-back takes Claude Code's own two credential locks around its last look and its write, as a switch does.

A Claude Code chat that renews its login takes .oauth_refresh.lock, renews, and saves the item. Without the lock, a
chat could save its renewed login between the put-back's last look ("no login") and its write, and the put-back would
write the older kept copy over it (a spent key). With the lock held, that save comes after the put-back's write, and
the newer login is what stays. When Claude Code holds the lock, the put-back waits and writes nothing. Made-up
*.example accounts on the engine's stand-ins (tests/fakes_accounts.py).
"""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import os
import time
import unittest

from fakes_accounts import Rig
from switchboard.accounts import capture, context, facts, putback, roster, tokens, vault


class Clock(object):
    def __init__(self):
        self.jump = 0.0

    def wall(self):
        return time.time() + self.jump

    def mono(self):
        return time.monotonic() + self.jump

    def advance(self, seconds):
        self.jump += seconds


def token(text):
    return json.loads(text)['claudeAiOauth']['refreshToken']


class ALostLoginWhileAChatRenews(unittest.TestCase):
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
        self.held = self.rig.keep('uuid-b', 'b@home.example', 'org-b')
        roster.forget_live()
        self.clock.advance(600)
        self.renewed = self.rig.server.refresh_for(self.rig.live_text())     # a chat renews B: only it holds this
        self.rig.sec.drop(self.rig.service, self.rig.account)                 # then the item is lost
        roster.forget_live()
        self.assertEqual(putback.look(self.ctx).action, 'wait')              # first seen missing
        self.clock.advance(putback.PUT_BACK_AFTER_S + 1)
        self.lock = facts.refresh_lock(self.rig.env, self.rig.home)
        self.second = facts.legacy_lock(self.rig.env, self.rig.home)

    def tearDown(self):
        self.use.__exit__(None, None, None)
        for path in (self.lock, self.second):
            try:
                os.rmdir(path)
            except OSError:
                pass
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        self.rig.close()

    def writes(self):
        return sum(1 for c in self.rig.kc.calls if c[0] == 'write' and c[1] == self.rig.service)

    def test_a_chat_that_saves_its_renewed_login_meanwhile_is_never_written_over(self):
        chat = {'waits': False, 'at_write': None}

        def renewing_chat(verb, service, account):
            """Just before the put-back's write lands, the chat wants to save its renewed login: under Claude Code's
            lock, as Claude Code saves a renewal. When the lock is held, it saves once the lock is free."""
            if verb != 'write' or service != self.rig.service or chat['at_write'] is not None:
                return
            chat['at_write'] = (os.path.isdir(self.lock), os.path.isdir(self.second), vault.holds('switch_lock'),
                                vault.holds('account_lock'))
            try:
                os.mkdir(self.lock)
            except FileExistsError:
                chat['waits'] = True
                return
            self.rig.sec.put(self.rig.service, self.rig.account, self.renewed)
            os.rmdir(self.lock)
        self.rig.kc.on_call = renewing_chat
        res = putback.look(self.ctx)
        self.rig.kc.on_call = None
        self.assertEqual(res.action, 'put_back', res.reason)
        self.assertEqual(chat['at_write'], (True, True, True, True))         # both of Claude Code's locks, after ours
        self.assertTrue(chat['waits'])
        os.mkdir(self.lock)                                                   # the chat saves now
        self.rig.sec.put(self.rig.service, self.rig.account, self.renewed)
        os.rmdir(self.lock)
        self.assertEqual(token(self.rig.live_text()), token(self.renewed))   # the newer login stays

    def test_while_claude_code_holds_its_lock_nothing_is_written(self):
        os.mkdir(self.lock)                                                   # Claude Code is renewing right now
        before = self.writes()
        res = putback.look(self.ctx)
        self.assertEqual(res.action, 'wait', res.reason)
        self.assertEqual(self.writes(), before)
        self.assertIsNone(self.rig.live_text())
        os.rmdir(self.lock)
        res = putback.look(self.ctx)
        self.assertEqual(res.action, 'put_back', res.reason)
        self.assertEqual(token(self.rig.live_text()), token(self.held))


if __name__ == '__main__':
    unittest.main()
