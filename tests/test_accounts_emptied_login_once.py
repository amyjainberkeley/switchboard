"""An emptied login (Claude Code's login after Anthropic refused it: the login object with its tokens blank) and the
sign-in Switchboard keeps for the account Claude Code's settings name.

The keep pass puts the kept sign-in live over an emptied login only when the end dates prove it a later sign-in. The
same sign-in, or an earlier one, is never written, and the person is told the account needs a sign-in. When the
emptied login has no end date left, nobody can tell, so the kept sign-in is tried once: what was tried is kept in the
engine's own record, so a second emptying writes nothing, in this process or another. The record of that try ends
when another sign-in is kept for the account, or when Claude Code is seen on another working login of it. A kept
sign-in past its own date, or one Anthropic refused to renew, is never written. Made-up *.example accounts on the
engine's stand-ins (tests/fakes_accounts.py): a fake Keychain, a fake Anthropic, a clock that moves.
"""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import os
import time
import unittest

from fakes_accounts import Rig, identity_block
from switchboard.accounts import api, capture, context, facts, putback, roster, tokens, vault

B = 'b@home.example'
KEPT_BACK = 'Switchboard put the sign-in it keeps for %s back in Claude Code' % B
NEW_SIGN_IN = 'Claude Code is on the new sign-in of %s' % B
NEEDS = '%s needs sign-in: ' % B
AGAIN = 'Sign in to it in Claude Code, and Switchboard keeps it again.'


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
    """What Claude Code leaves after Anthropic refused its login: the login object with its tokens blank, and (when
    keep_date is False) without its sign-in's end date."""
    blob = json.loads(text)
    blob['claudeAiOauth'].update({'accessToken': '', 'refreshToken': ''})
    if not keep_date:
        blob['claudeAiOauth'].pop('refreshTokenExpiresAt', None)
    return json.dumps(blob, separators=(',', ':'))


def token(text):
    return json.loads(text)['claudeAiOauth']['refreshToken']


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
        self.clock.advance(2 * 86400)                       # another account's sign-in, days later
        self.held = self.rig.keep('uuid-b', B, 'org-b')     # Claude Code is on B, and B's sign-in is kept
        roster.forget_live()
        self.rig.events[:] = []

    def tearDown(self):
        self.use.__exit__(None, None, None)
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        self.rig.close()

    def writes(self):
        """Writes of Claude Code's own login item so far."""
        return sum(1 for c in self.rig.kc.calls if c[0] == 'write' and c[1] == self.rig.service)

    def look(self, text=None):
        """Claude Code's item holds `text` (when given); one keep pass. (writes of Claude Code's item, its note)."""
        if text is not None:
            self.rig.set_live(text)
        roster.forget_live()
        before = self.writes()
        note = capture.keep_pass(self.ctx, force=True)
        roster.forget_live()
        self.clock.advance(180)
        return self.writes() - before, note

    def said(self, start=''):
        return [e[0] for e in self.rig.events if e[0].startswith(start)]

    def live_token(self):
        return token(self.rig.live_text())

    def refused_after_renewal(self):
        """Claude Code renews B by itself (the kept copy is now a spent, older generation of the same sign-in), then
        Anthropic refuses the new one. Returns Claude Code's renewed login, before it is emptied."""
        self.clock.advance(600)
        return self.rig.server.refresh_for(self.rig.live_text())


class NoEndDate(Case):
    """The emptied login lost its end date: the kept sign-in is tried once."""

    def test_six_looks_write_once_then_never_and_the_account_needs_a_sign_in(self):
        gen2 = self.refused_after_renewal()
        looks = [self.look(emptied(gen2, keep_date=False)) for _ in range(6)]
        self.assertEqual([n for n, _ in looks], [1, 0, 0, 0, 0, 0], looks)
        self.assertEqual(looks[0][1], KEPT_BACK)
        self.assertTrue((looks[1][1] or '').startswith(NEEDS) and looks[1][1].endswith(AGAIN), looks[1][1])
        self.assertEqual([note for _, note in looks[2:]], [None] * 4)        # said once
        self.assertEqual(self.said(KEPT_BACK), [KEPT_BACK])
        self.assertEqual(len(self.said(NEEDS)), 1, self.said())
        self.assertEqual(self.said(NEW_SIGN_IN), [])                         # never "new sign-in": nothing proves it
        self.assertEqual(self.rig.live_text(), emptied(gen2, keep_date=False))   # nothing written after the try

    def test_another_process_sees_what_was_tried(self):
        gen2 = self.refused_after_renewal()
        self.assertEqual(self.look(emptied(gen2, keep_date=False))[0], 1)
        for _ in range(3):
            capture.reset()                                 # the other process that runs keep passes, or a restart
            putback.reset()
            self.assertEqual(self.look(emptied(gen2, keep_date=False))[0], 0)
        self.assertEqual(len(self.said(NEEDS)), 1, self.said())

    def test_claude_code_on_the_sign_in_that_was_tried_does_not_end_the_record(self):
        gen2 = self.refused_after_renewal()
        self.assertEqual(self.look(emptied(gen2, keep_date=False))[0], 1)
        self.assertEqual(self.live_token(), token(self.held))
        self.assertEqual(self.look()[0], 0)                 # the keep pass sees the kept sign-in live: nothing to do
        n, note = self.look(emptied(gen2, keep_date=False))
        self.assertEqual(n, 0)
        self.assertTrue((note or '').startswith(NEEDS), note)

    def test_a_working_login_of_the_account_ends_the_record(self):
        self.assertEqual(self.look(emptied(self.held, keep_date=False))[0], 1)     # the kept one still works here
        self.rig.claude_renews()                            # and Claude Code works on with it: a new generation
        self.assertEqual(self.look()[1], 'Saved the newer login Claude Code made for %s' % B)
        renewed = self.rig.vault('uuid-b')
        n, note = self.look(emptied(renewed, keep_date=False))
        self.assertEqual((n, note), (1, KEPT_BACK))
        self.assertEqual(self.live_token(), token(renewed))
        n, note = self.look(emptied(renewed, keep_date=False))
        self.assertEqual(n, 0)
        self.assertTrue((note or '').startswith(NEEDS), note)

    def test_another_sign_in_kept_for_the_account_ends_the_record(self):
        gen2 = self.refused_after_renewal()
        for _ in range(2):
            self.look(emptied(gen2, keep_date=False))
        self.clock.advance(86400)
        d = os.path.join(self.rig.data, 'logins', 'f1')     # the person signs in to B again on the page
        os.makedirs(d, mode=0o700)
        new = self.rig.server.add('uuid-b', B, 'org-b')
        self.rig.kc.put(facts.keychain_service({'CLAUDE_CONFIG_DIR': d}), self.rig.account, new)
        with open(os.path.join(d, '.claude.json'), 'w') as f:
            json.dump({'oauthAccount': identity_block('uuid-b', B, 'org-b')}, f)
        api.add_login(d)
        open(os.path.join(d, putback.SIGNIN_OVER), 'w').close()
        self.assertEqual(token(self.rig.vault('uuid-b')), token(new))
        n, note = self.look(emptied(new, keep_date=False))
        self.assertEqual((n, note), (1, KEPT_BACK))
        self.assertEqual(self.look(emptied(new, keep_date=False))[0], 0)


class WithEndDate(Case):
    """The emptied login kept its end date: the kept sign-in goes live only when it is a later sign-in."""

    def test_a_later_sign_in_kept_is_put_live_as_the_new_sign_in(self):
        self.clock.advance(3 * 86400)
        d = os.path.join(self.rig.data, 'logins', 'f1')     # B signed in again on the page, days later
        os.makedirs(d, mode=0o700)
        new = self.rig.server.add('uuid-b', B, 'org-b')
        self.rig.kc.put(facts.keychain_service({'CLAUDE_CONFIG_DIR': d}), self.rig.account, new)
        with open(os.path.join(d, '.claude.json'), 'w') as f:
            json.dump({'oauthAccount': identity_block('uuid-b', B, 'org-b')}, f)
        api.add_login(d)
        open(os.path.join(d, putback.SIGNIN_OVER), 'w').close()
        self.rig.events[:] = []
        n, note = self.look(emptied(self.held))            # Claude Code's older sign-in, emptied
        self.assertEqual((n, note), (1, NEW_SIGN_IN))
        self.assertEqual(self.live_token(), token(new))
        self.assertEqual(self.said(NEEDS), [])

    def test_the_same_sign_in_is_never_written_and_the_account_needs_a_sign_in(self):
        gen2 = self.refused_after_renewal()
        n, note = self.look(emptied(gen2))
        self.assertEqual(n, 0)
        self.assertTrue((note or '').startswith(NEEDS) and note.endswith(AGAIN), note)
        self.assertEqual(self.look(emptied(gen2)), (0, None))           # said once
        capture.reset()                                                  # and once for every process
        self.assertEqual(self.look(emptied(gen2)), (0, None))
        self.assertEqual(self.rig.live_text(), emptied(gen2))
        self.assertEqual(len(self.said(NEEDS)), 1)

    def test_an_earlier_sign_in_is_never_written(self):
        self.clock.advance(86400)
        later = self.rig.server.add('uuid-b', B, 'org-b')   # B signed in again in Claude Code; not kept yet
        n, note = self.look(emptied(later))
        self.assertEqual(n, 0)
        self.assertTrue((note or '').startswith(NEEDS), note)
        self.assertEqual(self.rig.live_text(), emptied(later))


class NeverWritten(Case):
    """A kept sign-in past its own end date, or one Anthropic refused to renew, is never written, whatever the date of
    the emptied login."""

    def test_a_kept_sign_in_anthropic_refused(self):
        with vault.changing(self.ctx) as doc:
            a = doc['accounts']['uuid-b']
            a['strike'] = {'fp': a['fp'], 'at': self.clock.wall(), 'why': 'invalid_grant'}
        for keep_date in (True, False):
            with self.subTest(keep_date=keep_date):
                self.assertEqual(self.look(emptied(self.held, keep_date))[0], 0)

    def test_a_kept_sign_in_past_its_date(self):
        self.clock.advance(31 * 86400)
        for keep_date in (True, False):
            with self.subTest(keep_date=keep_date):
                self.assertEqual(self.look(emptied(self.held, keep_date))[0], 0)


if __name__ == '__main__':
    unittest.main()
