import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# A chosen sign-out, in Claude Code's order (2.1.329: the refresh token revoked with Anthropic, the Keychain item
# deleted, the plain login file removed, then the settings' account cleared), at each moment of an episode of the
# fault: before Claude Code's fallback write; 0.1 s, 1 s, 2.9 s and 3.1 s after it; while a file the hold put back is
# there; while a promote of that file waits for its second look. The settings change comes 5 ms, 300 ms or 2 s after
# the file's removal, with the sign-in watch's calls (MiniWatch) after every step and its looks in the gap. In every
# case at the end: no plain login file, no account login written by Switchboard in the item, the holder empty, and
# nothing written by Switchboard after the settings named nobody (a take-back may delete its own write).
# Inside the hold's three seconds the file the sign-out removed is put back until the settings change; what a Claude
# Code process that saves in that gap would read is asserted as it is: the revoked login, from the hold's file. Outside
# them it reads nothing.
# Besides: a sign-in inside Claude Code during an episode ends with the new login in the item and nothing of ours left.
# The account engine on its stand-ins (r17_engine_fakes) with a fake clock; made-up *.example accounts.
import itertools
import json
import os
import unittest

from hold_fakes import A, B, Base, MiniWatch, identity_block
from switchboard.accounts import live, putback

DELAYS = {'5ms': 0.005, '300ms': 0.3, '2s': 2.0}


def owner(text):
    """The made-up account a login text belongs to (the stand-in Anthropic names it in the refresh token)."""
    login = live.parse(text) if text else None
    rt = login.refresh_token if login is not None and login.kind == 'oauth' else None
    for uuid in ('uuid-a', 'uuid-b'):
        if rt and '-%s-s' % uuid in rt:
            return uuid
    return None


class EpisodeBase(Base):
    def setUp(self):
        super().setUp()
        self.w = MiniWatch(self.rig, self.clock)
        self.written = []                       # (step, text) of each write of Switchboard's into the live item
        self.made = []                          # the steps at which the hold put a file in place
        self.steps = itertools.count()          # one order for both, and for the settings' change
        self.nobody_at = None
        real_write = self.rig.kc.write

        def write(service, account, value, interactive=False, create_only=False):
            done = real_write(service, account, value, interactive=interactive, create_only=create_only)
            if service == self.rig.service and done.ok:
                self.written.append((next(self.steps), value))
            return done
        self.rig.kc.write = write
        real_link = putback._link

        def link(src, dst):
            real_link(src, dst)
            self.made.append(next(self.steps))
        putback._link = link
        self.addCleanup(setattr, putback, '_link', real_link)

    # Claude Code's moves, each followed by what the watch does at once
    def fallback(self):
        """A save macOS refused: the whole object into the plain login file (the item is deleted a moment later, by
        that process, in fault())."""
        blob = json.loads(self.rig.live_text())
        blob['discoveryState'] = {'refused-saver': 1}          # its own change, beside the login it read
        self.cc_writes(json.dumps(blob, separators=(',', ':')))
        self.w.notice()
        self.w.look()                           # the watch looks at once at a file that appeared

    def fault(self):
        """The fault's own order: the fallback write, another process's removal of the file, the item's delete."""
        self.fallback()
        self.clock.advance(0.18)
        self.cc_removes()
        self.w.notice()
        self.clock.advance(0.05)
        self.cc_deletes_item()
        self.w.notice()
        self.w.look(0.15)

    def gap(self):
        """What a Claude Code process that saves now would read: the item, else the plain login file, else nothing
        (its fresh read, 2.1.329): (where, whose login)."""
        item = self.rig.live_text()
        if item is not None:
            return 'item', owner(item)
        text = self.file_text()
        if text is not None:
            return 'file', owner(text)
        return None, None

    def sign_out(self, delay):
        self.rig.server.revoke('uuid-b')
        self.cc_deletes_item()
        self.w.notice()
        if os.path.exists(self.cred):
            self.cc_removes()
        self.w.notice()
        read = self.gap()
        left = delay
        while left > 1e-9:                      # the watch's looks in the gap
            step = min(0.25, left)
            self.w.look(step)
            left -= step
        self.settings_name(None)
        self.nobody_at = next(self.steps)
        self.w.notice()
        for _ in range(10):
            self.w.look(0.5)
        return read

    def check_end(self):
        self.assertIsNone(self.file_text(), 'a plain login file is left')
        item = self.rig.live_text()
        ours = [text for _, text in self.written]
        login = live.parse(item) if item else None
        self.assertFalse(login is not None and login.kind == 'oauth' and
                         any(json.loads(t).get('claudeAiOauth') == json.loads(item)['claudeAiOauth'] for t in ours),
                         'the item holds an account login Switchboard wrote')
        h = putback._HOLDER.get(self.ctx.data_dir) or {}
        self.assertFalse(h.get('text') or h.get('ours'), 'the holder is not empty')
        self.assertEqual([n for n, _ in self.written if n > self.nobody_at], [], 'a write after the sign-out')
        self.assertEqual([n for n in self.made if n > self.nobody_at], [], 'a file put back after the sign-out')
        self.assertIsNone(self.note())

    # the moments
    def before_the_fallback_write(self, delay):
        read = self.sign_out(delay)
        self.assertEqual(read, (None, None))
        self.check_end()

    def after_the_fallback_write(self, offset, delay):
        self.fallback()
        self.clock.advance(offset)
        read = self.sign_out(delay)
        if offset < putback.HOLD_FOR_S:
            self.assertEqual(read, ('file', 'uuid-b'))          # the leftover: the hold's file, the revoked login
        else:
            self.assertEqual(read, (None, None))
        self.check_end()

    def while_a_file_of_ours_is_there(self, delay):
        self.fault()
        self.assertTrue(self.ours())
        self.clock.advance(0.3)
        read = self.sign_out(delay)
        self.assertEqual(read, ('file', 'uuid-b'))
        self.check_end()

    def while_a_promote_of_ours_waits(self, delay):
        self.fault()
        self.assertTrue(self.ours())
        rec = putback.load_record(self.ctx)
        self.assertEqual((rec['missing_how'], rec['missing'][0]), ('file', 'uuid-b'))   # seen once, waiting
        self.assertIsNone(self.rig.live_text())
        self.clock.advance(0.1)
        read = self.sign_out(delay)
        self.assertEqual(read, ('file', 'uuid-b'))
        self.check_end()


class AChosenSignOut(EpisodeBase):
    """One test per moment of the episode and delay of the settings' change (made below)."""


def _add(name, fn, *args):
    for word, delay in DELAYS.items():
        def test(self, delay=delay):
            getattr(self, fn)(*(args + (delay,)))
        test.__name__ = 'test_%s_settings_%s_after' % (name, word)
        setattr(AChosenSignOut, test.__name__, test)


_add('before_the_fallback_write', 'before_the_fallback_write')
_add('0_1_s_after_the_fallback_write', 'after_the_fallback_write', 0.1)
_add('1_s_after_the_fallback_write', 'after_the_fallback_write', 1.0)
_add('2_9_s_after_the_fallback_write', 'after_the_fallback_write', 2.9)
_add('3_1_s_after_the_fallback_write', 'after_the_fallback_write', 3.1)
_add('while_a_file_of_ours_is_there', 'while_a_file_of_ours_is_there')
_add('while_a_promote_of_ours_waits', 'while_a_promote_of_ours_waits')


class ASignInInsideClaudeCode(EpisodeBase):
    def sign_in(self, who, gap=0.3):
        """Claude Code's sign-in: the same routine first (the login taken out of the item, the plain login file
        removed, the settings' account cleared), then the new login and the new settings."""
        cur = self.rig.live_text()
        blob = json.loads(cur) if cur else {}
        blob.pop('claudeAiOauth', None)
        self.rig.set_live(json.dumps(blob, separators=(',', ':')))
        self.w.notice()
        if os.path.exists(self.cred):
            self.cc_removes()
        self.w.notice()
        self.settings_name(None)
        self.w.notice()
        self.w.look(gap)
        self.clock.advance(1.0)
        new = json.loads(self.rig.server.add(*who))
        blob.update(new)
        text = json.dumps(blob, separators=(',', ':'))
        self.rig.set_live(text)
        self.rig.set_config(identity_block(*who))
        self.w.notice()
        for _ in range(8):
            self.w.look(0.5)
        return text

    def check_signed_in(self, text):
        self.assertEqual(self.rig.live_text(), text)
        self.assertIsNone(self.file_text())
        self.assertIsNone(self.note())

    def test_to_another_account_during_an_episode_with_a_file_of_ours(self):
        self.fault()
        self.assertTrue(self.ours())
        self.check_signed_in(self.sign_in(A))

    def test_to_the_same_account_during_an_episode_with_a_file_of_ours(self):
        self.fault()
        self.check_signed_in(self.sign_in(B))

    def test_right_after_a_fallback_write(self):
        self.fallback()
        self.clock.advance(0.2)
        self.check_signed_in(self.sign_in(A))

    def test_a_fallback_write_of_the_new_login_right_after_it_stays_claude_codes(self):
        self.fallback()
        text = self.sign_in(A)
        self.cc_writes(text)                     # a refused save of the new login: Claude Code's own file
        self.w.notice()
        self.w.look(0.5)
        self.assertEqual(self.file_text(), text)
        self.assertFalse(self.ours())


if __name__ == '__main__':
    unittest.main()
