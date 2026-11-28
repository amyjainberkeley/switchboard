import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The standing copy of the sign-in: Claude Code's plain login file kept as an exact copy of its Keychain item. A Claude
# Code process whose Keychain read gives nothing (a timeout on a loaded Mac, "no such item", locked with nothing
# cached) reads that file instead (2.1.329 to 2.1.332, read in its program text), so a file that always holds the
# item's text turns such a miss into a sign-in. A look that finds the item holding a full login of the account the
# settings name and no file makes it (exact text, owner-only, known as ours); the look after any change of the item
# brings it up to the item in one rename. The first one after an install is said once in History.
# The account engine on its stand-ins (fakes_accounts) with a fake clock; made-up *.example accounts.
import json
import os
import unittest
from unittest import mock

from hold_fakes import Base, stamp_of
from switchboard.accounts import facts, putback


class Mirror(Base):
    MIRROR = True

    def renewed(self, after=2.0):
        """One Claude Code process renews the login; its fresh read found the item, so it leaves the file alone."""
        self.clock.advance(after)
        return self.rig.claude_renews()

    def said(self):
        return [e for e in self.rig.events if e[0] == putback.MIRROR_LINE]


class MadeAtALook(Mirror):
    def test_the_first_look_makes_an_exact_owner_only_copy_known_as_ours(self):
        self.assertIsNone(self.file_text())
        self.look(keychain=True)
        self.assertEqual(self.file_text(), self.rig.live_text())
        self.assertEqual(os.stat(self.cred).st_mode & 0o777, 0o600)
        self.assertTrue(self.ours())
        self.assertEqual(self.leftovers(), [])
        out = self.hold('looked')
        self.assertEqual(out['did'], 'mirrored')
        self.assert_no_secret_in(out, self.note(), self.rig.logs)

    def test_it_is_said_once_in_history(self):
        self.look(keychain=True)
        self.assertEqual(len(self.said()), 1)
        self.assertEqual(self.said()[0][2].get('h'), 'setting')
        os.unlink(self.cred)                                  # a Claude Code process removed it
        self.hold('gone')
        self.look(after=1.0, login_file='gone')
        self.assertEqual(self.file_text(), self.rig.live_text())
        self.assertEqual(len(self.said()), 1)                 # not again for a copy made again

    def test_said_once_too_when_a_switch_made_the_first_copy(self):
        from switchboard.accounts import switcher
        a = [k for k in self.rig.doc()['accounts'] if k.startswith('uuid-a')][0]
        self.assertTrue(switcher.switch(self.ctx, a).ok)
        self.assertTrue(self.ours())                          # made by the switch, before the settings changed
        self.look(after=1.0, keychain=True, settings=True)
        self.look(after=1.0, keychain=True)
        self.assertEqual(len(self.said()), 1)

    def test_said_again_after_the_uninstallers_tidy(self):
        self.look(keychain=True)
        putback.hold_uninstall(self.ctx)                      # the item is there: the copy goes
        self.assertIsNone(self.file_text())
        self.look(after=1.0, keychain=True)
        self.assertEqual(len(self.said()), 2)

    def test_no_look_is_asked_for_to_take_it_away(self):
        self.look(keychain=True)
        self.clock.advance(putback.HOLD_TIDY_S + 1)
        out = self.hold('looked')
        self.assertNotIn('look_in', out)
        self.look(keychain=True)
        self.assertTrue(self.ours())                          # old, and still there: the item holds the login


class FollowsTheItem(Mirror):
    def test_a_renewal_is_in_the_file_at_the_next_look(self):
        self.look(keychain=True)
        before = self.note()
        renewed = self.renewed()
        self.assertNotEqual(self.file_text(), renewed)
        self.look(after=0.1, keychain=True)
        self.assertEqual(self.file_text(), renewed)
        self.assertTrue(self.ours())
        note = self.note()
        self.assertEqual(note['wrote'], before['wrote'])
        self.assertEqual((note['state'], note.get('prev'), note.get('tmp')), ('settled', None, None))
        self.assertEqual(os.stat(self.cred).st_mode & 0o777, 0o600)
        self.assertEqual(self.leftovers(), [])
        self.assertEqual(self.hold('looked')['did'], 'followed')
        self.assertIn(putback.FOLLOW_LINE, self.rig.logs)

    def test_a_change_of_the_items_other_parts_is_followed_within_mirror_parts_s_and_not_said(self):
        # each new copy makes every running Claude Code read its item again at its next request (it compares the
        # file's time): a connector's login or a mark in the item waits until the copy is MIRROR_PARTS_S old
        self.look(keychain=True)
        self.assertEqual(self.hold('looked')['did'], 'mirrored')
        text = self.file_text()
        blob = json.loads(self.rig.live_text())
        blob['mcpOAuth'] = {'docs-server': {'accessToken': 'mcp-made-up-2', 'expiresAt': 2}}
        self.rig.set_live(json.dumps(blob, separators=(',', ':')))
        logs = len(self.rig.logs)
        self.look(after=0.5, keychain=True)
        self.assertEqual(self.file_text(), text)
        # the copy's age is its real modification time against the fake clock, which started at the real time and
        # moves only when told: the margin covers the real seconds a loaded Mac takes to run the test so far
        self.look(after=putback.MIRROR_PARTS_S + 600.0, keychain=True)
        self.assertEqual(self.file_text(), self.rig.live_text())
        self.assertEqual(self.rig.logs[logs:], [])
        self.assertIsNone(self.hold('looked')['did'])

    def test_a_renewal_right_after_a_copy_is_followed_at_once(self):
        self.look(keychain=True)
        renewed = self.renewed(after=1.0)
        self.look(after=0.2, keychain=True)
        self.assertEqual(self.file_text(), renewed)

    def test_there_is_never_a_moment_without_a_file(self):
        self.look(keychain=True)
        self.renewed()
        unlinked, replaced = [], []
        real_unlink, real_replace = os.unlink, os.replace

        def unlink(path, *a, **kw):
            unlinked.append(path) if path == self.cred else None
            return real_unlink(path, *a, **kw)

        def replace(src, dst, *a, **kw):
            replaced.append(dst) if dst == self.cred else None
            return real_replace(src, dst, *a, **kw)
        with mock.patch.object(os, 'unlink', unlink), mock.patch.object(os, 'replace', replace):
            self.look(after=0.1, keychain=True)
        self.assertEqual((unlinked, replaced), ([], [self.cred]))

    def test_the_sign_in_watch_knows_each_new_file_as_ours(self):
        self.look(keychain=True)
        self.assertEqual(self.hold('appeared').get('ours'), True)
        old = stamp_of(self.cred)
        self.renewed()
        self.look(after=0.1, keychain=True)
        self.assertNotEqual(stamp_of(self.cred), old)
        out = self.hold('changed')
        self.assertEqual((out['did'], out.get('ours')), (None, True))

    def test_many_renewals_hours_apart_each_followed(self):
        self.look(keychain=True)
        for _ in range(5):
            renewed = self.renewed(after=8 * 3600.0)
            self.look(after=0.2, keychain=True)
            self.assertEqual(self.file_text(), renewed)
            self.assertTrue(self.ours())

    def test_the_save_lock_held_asks_for_another_look_and_waits_for_nothing(self):
        self.look(keychain=True)
        renewed = self.renewed()
        lock = facts.storage_lock(self.rig.env, self.rig.home)
        os.mkdir(lock)
        try:
            t0 = self.clock.mono()
            res = self.look(after=0.1, keychain=True)
            self.assertAlmostEqual(self.clock.mono() - t0, 0.1)
            self.assertEqual(res.next_s, putback.SPARE_AGAIN_S)
            self.assertNotEqual(self.file_text(), renewed)
        finally:
            os.rmdir(lock)
        self.look(after=putback.SPARE_AGAIN_S, keychain=True)
        self.assertEqual(self.file_text(), renewed)


class AMomentThatCantBeRead(Mirror):
    # Claude Code rewrites its settings all the time and Switchboard's accounts file is written under a lock: one look
    # that can't read either is no reason to take the copy away (the next look would make it again, and each new copy
    # makes every running Claude Code read its item again)
    def test_settings_that_cant_be_read_keep_the_copy(self):
        self.look(keychain=True)
        text = self.file_text()
        with open(self.rig.config_path(), 'w') as f:
            f.write('{"oauthAccount": ')                    # a moment of a half-written file
        self.look(after=putback.HOLD_TIDY_S + 1, keychain=True)
        self.assertEqual(self.file_text(), text)
        self.assertTrue(self.ours())

    def test_saved_accounts_that_cant_be_read_keep_the_copy(self):
        self.look(keychain=True)
        text = self.file_text()
        from switchboard.accounts import vault
        with open(vault.store_path(self.ctx), 'w') as f:
            f.write('not json')
        self.look(after=putback.HOLD_TIDY_S + 1, keychain=True)
        self.assertEqual(self.file_text(), text)
        self.assertTrue(self.ours())


class NotWithTheSettingOff(Mirror):
    def write_settings(self, policy):
        with open(self.ctx.path('settings.json'), 'w') as f:
            json.dump({'auto_policy': policy}, f)

    def test_keep_signin_file_false_makes_none(self):
        self.write_settings({'keep_signin_file': False})
        self.look(keychain=True)
        self.assertIsNone(self.file_text())

    def test_hold_signin_file_false_makes_none(self):
        self.write_settings({'hold_signin_file': False})
        self.look(keychain=True)
        self.assertIsNone(self.file_text())

    def test_missing_settings_mean_on(self):
        self.write_settings({'auto_switch': True})
        self.look(keychain=True)
        self.assertEqual(self.file_text(), self.rig.live_text())



if __name__ == '__main__':
    unittest.main()
