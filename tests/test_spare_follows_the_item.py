import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# A plain login file of ours follows Claude Code's Keychain item. A file the sign-in hold keeps for Claude Code (put
# back, kept after a promote, or a spare copy of the sign-in) holds the login as it was. A process that renews while
# its Keychain read answers "no such item", "empty", "unparsable" or "locked" takes its refresh token from that file
# (read in Claude Code's program text, 2.1.329 to 2.1.332); one that a renewal by another process has spent is refused,
# and Claude Code then blanks its login. So any look that finds in the item a full login of the same account other than
# the file's (its tokens and their end) brings the file to the item's exact text: holding Claude Code's save lock, a
# fresh read of the item, one rename while the file is still ours (never a moment without a file), the note's stamp
# updated and its 'wrote' kept. Beside another account's login the file goes. A look that finds the save lock held
# asks for another look instead of waiting.
# The account engine on its stand-ins (r17_engine_fakes) with a fake clock; made-up *.example accounts.
import json
import os
import unittest
from unittest import mock

from hold_fakes import MCP, Base, Killed, stamp_of
from switchboard.accounts import ccfiles, facts, live, putback, vault


class Promoted(Base):
    def promoted(self):
        """Claude Code's refused save moved its login to the plain login file and deleted the item; a look put that
        login back in the item and kept the file for the processes still reading it. Returns the file's text."""
        text = self.cc_writes()
        self.hold('appeared')
        self.cc_deletes_item()
        self.assertEqual(self.look(after=0.2, keychain=True).action, 'wait')
        res = self.look(after=putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, 'promote', res.reason)
        self.assertTrue(self.ours())
        return text

    def renewed(self, after=2.0):
        """One Claude Code process renews the login; its fresh read found the item, so it leaves the file alone."""
        self.clock.advance(after)
        return self.rig.claude_renews()

    def account_of(self, uuid):
        return [k for k in self.rig.doc()['accounts'] if k.startswith(uuid)][0]

    @property
    def lock(self):
        return facts.storage_lock(self.rig.env, self.rig.home)


class ARenewal(Promoted):
    def test_brings_the_file_to_the_items_text_at_the_next_look(self):
        text = self.promoted()
        before = self.note()
        renewed = self.renewed()
        self.assertNotEqual(json.loads(renewed)['claudeAiOauth'], json.loads(text)['claudeAiOauth'])
        self.look(after=0.1, keychain=True)
        self.assertEqual(self.file_text(), renewed)
        self.assertTrue(self.ours())
        note = self.note()
        self.assertEqual(note['wrote'], before['wrote'])      # its time is not started again
        self.assertEqual((note['state'], note.get('prev'), note.get('tmp')), ('settled', None, None))
        self.assertEqual(os.stat(self.cred).st_mode & 0o777, 0o600)
        self.assertEqual(self.leftovers(), [])
        out = self.hold('looked')
        self.assertEqual(out['did'], 'followed')
        self.assert_no_secret_in(out, note)

    def test_there_is_never_a_moment_without_a_file(self):
        self.promoted()
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

    def test_the_sign_in_watch_knows_the_new_file_as_its_own(self):
        self.promoted()
        old = stamp_of(self.cred)
        self.renewed()
        self.look(after=0.1, keychain=True)
        self.assertNotEqual(stamp_of(self.cred), old)
        out = self.hold('changed')                            # the watch's call for that change
        self.assertEqual((out['did'], out.get('ours')), (None, True))

    def test_the_file_still_goes_hold_tidy_s_after_the_promote(self):
        self.promoted()
        renewed = self.renewed(after=5.0)
        self.look(after=0.1, keychain=True)
        self.assertEqual(self.file_text(), renewed)
        self.look(after=putback.HOLD_TIDY_S - 5.1 - 0.5, keychain=True)
        self.assertTrue(self.ours())
        self.look(after=0.6, keychain=True)
        self.assertIsNone(self.file_text())
        self.assertIsNone(self.note())


class NeverTakenOut(Promoted):
    def test_the_items_own_login_copied_into_the_file_is_never_taken_out_of_the_item(self):
        # the take-out of a login behind a sign-out is for bytes the hold put back from the file; a copy of the item's
        # own login proves nothing about how a login got into the item
        self.promoted()
        renewed = self.renewed()
        self.look(after=0.1, keychain=True)
        self.assertEqual(self.file_text(), renewed)
        self.settings_name(None)                              # settings naming nobody, the item kept
        self.hold('settings')
        self.look(after=0.5, settings=True)
        self.assertEqual(self.rig.live_text(), renewed)


class AnotherAccount(Promoted):
    def test_beside_another_accounts_login_the_file_goes(self):
        self.promoted()
        self.clock.advance(2.0)
        self.rig.set_live(self.rig.vault(self.account_of('uuid-a')))     # another program put A's login there
        self.look(after=0.1, keychain=True)
        self.assertIsNone(self.file_text())
        self.assertIsNone(self.note())
        out = self.hold('looked')
        self.assertEqual((out['did'], out['why']), ('removed', 'another'))

    def test_a_file_whose_login_no_saved_account_proves_goes_beside_another_accounts_login(self):
        # a fresh sign-in of the other account, not kept yet, made a minute from the one in the item: their end stamps
        # sit as close as one sign-in renewed, and that proves nothing
        fresh = self.rig.server.add('uuid-a', 'ana@work.example', 'org-a')
        text, out = self.kept_then_gone(fresh)
        self.assertEqual(out['did'], 'held')                  # put back from memory: a file of ours
        self.look(after=0.1, keychain=True)
        self.assertIsNone(self.file_text())                   # never brought to the item's login of another account
        self.assertIsNone(self.note())


class ClaudeCodesOwnFile(Promoted):
    def test_a_file_claude_code_writes_while_the_step_runs_is_left_to_it(self):
        self.promoted()
        self.renewed()
        theirs = json.dumps(dict(json.loads(self.rig.live_text()), discoveryState={'p7': 1}))
        state = {}

        def on_call(verb, service, account):
            # a process that does not take the save lock writes its fallback while the step holds it
            if verb == 'read' and service == self.rig.service and os.path.isdir(self.lock) and not state:
                state['wrote'] = self.cc_writes(theirs)
        self.rig.kc.on_call = on_call
        try:
            self.look(after=0.1, keychain=True)
        finally:
            self.rig.kc.on_call = None
        self.assertIn('wrote', state)                         # the step ran, holding the save lock
        self.assertEqual(self.file_text(), theirs)
        self.assertFalse(self.ours())

    def test_a_file_claude_code_writes_while_the_new_file_is_named_is_never_replaced_by_the_rename(self):
        self.promoted()
        self.renewed()
        theirs = json.dumps(dict(json.loads(self.rig.live_text()), discoveryState={'p10': 3}))
        real = putback._hold_write_note
        state = {}

        def write(ctx, note, sync=True):
            out = real(ctx, note, sync)
            if note.get('state') == 'pending' and note.get('prev') is not None and not state:
                state['wrote'] = self.cc_writes(theirs)     # Claude Code's fallback lands as the new file is named
            return out
        with mock.patch.object(putback, '_hold_write_note', write):
            self.look(after=0.1, keychain=True)
        self.assertIn('wrote', state)                       # the step got as far as the rename
        self.assertEqual(self.file_text(), theirs)
        self.assertFalse(self.ours())
        self.assertEqual(self.leftovers(), [])

    def test_a_file_claude_code_wrote_before_is_never_replaced(self):
        self.promoted()
        self.clock.advance(1.0)
        theirs = self.cc_writes(json.dumps(dict(json.loads(self.rig.live_text()), discoveryState={'p8': 2})))
        self.hold('changed')
        self.renewed()
        self.look(after=0.1, keychain=True)
        self.assertEqual(self.file_text(), theirs)


class NothingWaits(Promoted):
    def test_with_the_save_lock_held_the_look_asks_for_another_and_never_waits(self):
        text = self.promoted()
        renewed = self.renewed()
        os.mkdir(self.lock)
        try:
            self.clock.advance(0.1)
            before = self.clock.mono()
            res = self.look(keychain=True)
            self.assertEqual(self.clock.mono(), before)       # not one fake second waited for the lock
        finally:
            os.rmdir(self.lock)
        self.assertEqual(self.file_text(), text)
        self.assertLessEqual(res.next_s, putback.SPARE_AGAIN_S)
        self.look(after=putback.SPARE_AGAIN_S)
        self.assertEqual(self.file_text(), renewed)


class TheSettingsAreReadBeforeTheLock(Promoted):
    def test_while_it_holds_the_save_lock_the_step_reads_no_settings_and_no_saved_accounts(self):
        self.promoted()
        renewed = self.renewed()
        under = []
        real_settings, real_accounts = live.config_identity, vault.load

        def settings(*a, **kw):
            under.append(('settings', ccfiles.held_save_lock() is not None))
            return real_settings(*a, **kw)

        def accounts(*a, **kw):
            under.append(('accounts', ccfiles.held_save_lock() is not None))
            return real_accounts(*a, **kw)
        with mock.patch.object(live, 'config_identity', settings), mock.patch.object(vault, 'load', accounts):
            self.look(after=0.1, keychain=True)
        self.assertEqual(self.file_text(), renewed)
        self.assertEqual([what for what, held in under if held], [])


class LeftAsItIs(Promoted):
    def test_the_same_login_with_other_parts_of_the_item_changed(self):
        self.promoted()
        stamp = stamp_of(self.cred)
        self.clock.advance(2.0)
        self.rig.set_live(json.dumps(dict(json.loads(self.rig.live_text()), discoveryState={'p9': 4})))
        self.look(after=0.1, keychain=True)
        self.assertEqual(stamp_of(self.cred), stamp)

    def test_an_item_without_a_login_leaves_the_file_to_the_holds_other_rules(self):
        text = self.promoted()
        self.clock.advance(2.0)
        self.rig.set_live(json.dumps(MCP))
        self.look(after=0.1, keychain=True)
        self.assertEqual(self.file_text(), text)

    def test_with_the_setting_off(self):
        text = self.promoted()
        with open(self.ctx.path('settings.json'), 'w') as f:
            json.dump({'auto_policy': {'hold_signin_file': False}}, f)
        self.renewed()
        self.look(after=0.1, keychain=True)
        self.assertEqual(self.file_text(), text)


class StoppedPartWay(Promoted):
    def restart(self):
        putback.reset()                                       # a new watch: nothing in memory
        putback._KEPT.clear()
        self.hold('start')

    def test_between_the_note_and_the_rename_the_next_start_still_knows_the_file(self):
        text = self.promoted()
        renewed = self.renewed()
        real = os.replace

        def replace(src, dst, *a, **kw):
            if dst == self.cred:
                raise Killed()
            return real(src, dst, *a, **kw)
        with mock.patch.object(os, 'replace', replace):
            with self.assertRaises(Killed):
                self.look(after=0.1, keychain=True)
        self.assertEqual(self.file_text(), text)
        self.restart()
        self.assertTrue(self.ours())                          # the old file, named in the pending note
        self.look(after=0.2, keychain=True)
        self.assertEqual(self.file_text(), renewed)
        self.assertTrue(self.ours())
        self.assertEqual(self.leftovers(), [])

    def test_right_after_the_temporary_file_is_written_the_next_start_leaves_no_login_but_the_file_of_ours(self):
        self.promoted()
        self.renewed()
        real = os.stat

        def stat(path, *a, **kw):
            if str(path).endswith('.hold'):
                raise Killed()                              # stopped once the temporary file holds the new login
            return real(path, *a, **kw)
        with mock.patch.object(os, 'stat', stat):
            with self.assertRaises(Killed):
                self.look(after=0.1, keychain=True)
        self.assertNotEqual(self.leftovers(), [])           # the temporary file is there, the login in it
        self.restart()
        self.assertEqual(self.leftovers(), [])
        self.assertTrue(self.ours())
        folder = os.path.dirname(self.cred)
        holding = []
        for name in os.listdir(folder):
            p = os.path.join(folder, name)
            if os.path.isfile(p):
                with open(p) as f:
                    holding += [name] if '"refreshToken"' in f.read() else []
        self.assertEqual(holding, [os.path.basename(self.cred)])

    def test_between_the_rename_and_the_settled_note_the_next_start_knows_the_new_file(self):
        self.promoted()
        renewed = self.renewed()
        real = putback._hold_write_note

        def write(ctx, note, sync=True):
            if note.get('state') == 'settled' and note.get('prev') is None and note.get('tmp') is None and \
                    os.path.exists(self.cred) and self.file_text() == renewed:
                raise Killed()
            return real(ctx, note, sync)
        with mock.patch.object(putback, '_hold_write_note', write):
            with self.assertRaises(Killed):
                self.look(after=0.1, keychain=True)
        self.assertEqual(self.file_text(), renewed)
        self.restart()
        self.assertTrue(self.ours())
        self.assertEqual(self.note()['state'], 'settled')
        self.assertEqual(self.leftovers(), [])


if __name__ == '__main__':
    unittest.main()
