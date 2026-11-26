"""The keep pass's puts of a kept sign-in into Claude Code's item are counted as attempts, not successes.

Each write of Claude Code's item can set many open chats saving at once, so a put that fails or is undone is not tried
again at every pass: every attempt is kept in the vault's record for the account (under the account lock, so every
process that runs keep passes sees it), the next attempt after a failed one waits longer each time
(capture.PUT_LIVE_WAITS), the hourly cap counts every attempt, and History says the trouble once for the whole spell.
Made-up *.example accounts on the engine's stand-ins (tests/fakes_accounts.py).
"""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import os
import time
import unittest
from unittest import mock

from fakes_accounts import Rig, identity_block
from switchboard.accounts import api, capture, context, facts, putback, roster, switcher, tokens

B = 'b@home.example'
FAILED = ('Putting the sign-in Switchboard keeps for %s back in Claude Code did not work. Switchboard tries again '
          'later, less often each time.' % B)


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


class Case(unittest.TestCase):
    """A and B kept; B signed in again on the page days later (the newer sign-in kept and put live); then a chat writes
    B's older sign-in back, so the keep pass puts the newer one back."""

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
        self.held_old = self.rig.keep('uuid-b', B, 'org-b')
        roster.forget_live()
        self.clock.advance(3 * 86400)
        d = os.path.join(self.rig.data, 'logins', 'f1')
        os.makedirs(d, mode=0o700)
        self.new = self.rig.server.add('uuid-b', B, 'org-b')
        self.rig.kc.put(facts.keychain_service({'CLAUDE_CONFIG_DIR': d}), self.rig.account, self.new)
        with open(os.path.join(d, '.claude.json'), 'w') as f:
            json.dump({'oauthAccount': identity_block('uuid-b', B, 'org-b')}, f)
        api.add_login(d)
        open(os.path.join(d, putback.SIGNIN_OVER), 'w').close()
        self.clock.advance(600)
        self.older = self.rig.server.refresh_for(self.held_old)   # a chat renews B's older sign-in and writes it back
        self.rig.set_live(self.older)
        roster.forget_live()
        self.rig.events[:] = []
        self.busy = {'on': False, 'after_write': False}
        self.rig.kc.on_call = self.busy_chat

    def tearDown(self):
        self.use.__exit__(None, None, None)
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        self.rig.close()

    def busy_chat(self, verb, service, account):
        """While on: a chat saves its older login again whenever the item is read back after our write, so every put
        is undone."""
        if not self.busy['on'] or service != self.rig.service:
            return
        if verb == 'write':
            self.busy['after_write'] = True
        elif verb == 'read' and self.busy['after_write']:
            self.busy['after_write'] = False
            self.rig.sec.put(self.rig.service, self.rig.account, self.older)

    def writes(self):
        return sum(1 for c in self.rig.kc.calls if c[0] == 'write' and c[1] == self.rig.service)

    def passes(self, n, gap=60.0, other_process=False):
        """n keep passes, gap seconds apart. Returns the writes of Claude Code's item they made."""
        before = self.writes()
        for _ in range(n):
            if other_process:
                capture.reset()
            capture.keep_pass(self.ctx, force=True)
            roster.forget_live()
            self.clock.advance(gap)
        return self.writes() - before

    def warnings(self):
        return [e[0] for e in self.rig.events if e[1] == 'warn']


class AnUndonePut(Case):
    def test_eight_passes_make_one_attempt_and_say_it_once(self):
        self.busy['on'] = True
        self.assertEqual(self.passes(8), 2)                         # one attempt: the write and its roll-back
        self.assertEqual(self.warnings(), [FAILED])
        self.assertEqual(token(self.rig.live_text()), token(self.older))

    def test_the_wait_grows_and_history_still_says_it_once(self):
        self.busy['on'] = True
        self.assertEqual(self.passes(1), 2)
        waits = capture.PUT_LIVE_WAITS
        self.clock.advance(waits[0] - 1)
        self.assertEqual(self.passes(1), 2)                         # the second attempt, after the first wait
        self.clock.advance(waits[0])
        self.assertEqual(self.passes(1), 0)                         # the second wait is longer
        self.clock.advance(waits[1] - waits[0])
        self.assertEqual(self.passes(1), 2)
        self.assertEqual(self.warnings(), [FAILED])

    def test_every_process_shares_the_wait(self):
        self.busy['on'] = True
        self.assertEqual(self.passes(8, other_process=True), 2)
        self.assertEqual(self.warnings(), [FAILED])

    def test_a_put_that_lands_ends_the_spell(self):
        self.busy['on'] = True
        self.passes(1)
        self.busy['on'] = False
        self.clock.advance(capture.PUT_LIVE_WAITS[0])
        self.assertEqual(self.passes(1), 1)
        self.assertEqual(token(self.rig.live_text()), token(self.new))
        self.rig.set_live(self.older)                               # later the chat writes the older one back again
        roster.forget_live()
        self.busy['on'] = True
        self.assertEqual(self.passes(1), 2)                         # tried at once: the spell before is over
        self.assertEqual(self.warnings(), [FAILED, FAILED])

    def test_claude_code_seen_on_the_newest_sign_in_ends_the_spell(self):
        self.busy['on'] = True
        self.assertEqual(self.passes(1), 2)
        self.busy['on'] = False
        self.rig.set_live(self.new)                                 # the chat is gone; Claude Code is on the newest
        roster.forget_live()
        self.assertEqual(self.passes(1), 0)
        self.clock.advance(1200)                                    # later the older one is written back again
        self.rig.set_live(self.older)
        roster.forget_live()
        self.busy['on'] = True
        self.assertEqual(self.passes(1), 2)
        self.assertEqual(self.warnings(), [FAILED, FAILED])         # a new spell, said again
        self.clock.advance(capture.PUT_LIVE_WAITS[0])
        self.assertEqual(self.passes(1), 2)                         # and its first wait is the first step

    def test_the_cap_counts_attempts_not_successes(self):
        for _ in range(capture.RENEW_MAX - 1):                      # two puts that land
            self.rig.set_live(self.older)
            roster.forget_live()
            self.assertEqual(self.passes(1), 1)
        self.rig.set_live(self.older)
        roster.forget_live()
        self.busy['on'] = True
        self.assertEqual(self.passes(1), 2)                         # a third that is undone
        self.busy['on'] = False
        self.clock.advance(capture.PUT_LIVE_WAITS[0])
        self.assertEqual(self.passes(1), 0)                         # three attempts this hour: capped
        self.assertTrue([w for w in self.warnings() if 'keeps going back to an older sign-in of %s' % B in w],
                        self.warnings())


class AnErrorFromTheSwitch(Case):
    """A put the switch refuses before writing anything (here: the disk is almost full) is an attempt too: retried
    after the same growing wait, and said once."""

    def test_it_is_tried_once_in_eight_passes_and_said_once(self):
        self.rig.free = 1024
        with mock.patch.object(switcher, 'switch', wraps=switcher.switch) as sw:
            self.assertEqual(self.passes(8), 0)
            self.assertEqual(sw.call_count, 1)
            self.assertEqual(self.passes(8, gap=10.0, other_process=True), 0)     # still within the first wait
            self.assertEqual(sw.call_count, 1)
            self.clock.advance(capture.PUT_LIVE_WAITS[0])
            self.passes(1)
            self.assertEqual(sw.call_count, 2)
        self.assertEqual(self.warnings(), [FAILED])


class AnEmptiedLoginStillGetsItsFirstTry(Case):
    def test_once_capped_an_emptied_login_is_put_right_but_not_during_a_wait(self):
        for _ in range(capture.RENEW_MAX):
            self.rig.set_live(self.older)
            roster.forget_live()
            self.passes(1)
        self.rig.set_live(self.older)
        roster.forget_live()
        self.assertEqual(self.passes(1), 0)                         # capped
        blob = json.loads(self.older)
        blob['claudeAiOauth'].update({'accessToken': '', 'refreshToken': ''})
        emptied = json.dumps(blob, separators=(',', ':'))
        self.rig.set_live(emptied)                                  # Claude Code empties the older sign-in
        roster.forget_live()
        self.busy['on'] = True
        self.older = emptied                                        # the busy chat saves the emptied login back
        self.assertEqual(self.passes(1), 2)                         # its first try is not capped; it is undone
        self.assertEqual(self.passes(5), 0)                         # and then it waits like any other
        self.busy['on'] = False
        self.clock.advance(capture.PUT_LIVE_WAITS[0])
        self.assertEqual(self.passes(1), 1)
        self.assertEqual(token(self.rig.live_text()), token(self.new))


if __name__ == '__main__':
    unittest.main()
