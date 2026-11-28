import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# A plain login file the sign-in hold put back is taken away again (putback.hold_tidy and live.write_live): by the
# first look that finds the Keychain item there once the file's bytes are HOLD_TIDY_S old. Not sooner: the process
# whose save was refused deletes the item a moment after it wrote the file, so a look that read the item just before
# would take away the one login left; and the processes that asked while the item was gone read the file for 30 s.
# Beside an item without a login it stays while the look will put that item back from it. It goes at once when the
# settings name nobody or another account (the holder emptied), by a switch's write, at the sign-in watch's start,
# and by the uninstaller. A file Claude Code wrote is never taken away, and a file of ours is never taken away while
# it is the only login Claude Code has. Besides: a file of ours is promoted only at a second look, also on the
# straight path, and what is written is decided again under Claude Code's save lock; its fresh age is never proof of
# the fault, while a write the watch reported before it was put back still is; nothing is put back while the
# settings name nobody or after a take-back's delete; one that keeps Claude Code signed in past two minutes stays,
# said once.
# The account engine on its stand-ins (r17_engine_fakes) with a fake clock; made-up *.example accounts.
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from hold_fakes import A, B, MCP, Base, Killed
from switchboard.accounts import api, putback, switcher
from switchboard.accounts import signin_watch as sw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TheTidy(Base):
    def signed_in_again(self):
        """B signed in again inside Claude Code, days after the sign-in the saved accounts know: another sign-in of
        the same account (its end date is its own), and nothing saved describes it yet."""
        self.clock.advance(3 * 86400.0)
        return self.rig.server.add(*B)

    def test_a_look_that_finds_the_item_leaves_ours_while_it_is_young(self):
        text, out = self.kept_then_gone()
        self.assertEqual(out['did'], 'held')
        # one look is asked for once the bytes are old enough (they were written 0.18 s before they went)
        self.assertAlmostEqual(out['look_in'], putback.HOLD_TIDY_S - 0.18 + putback.HOLD_LOOK_S, places=3)
        for after in (0.0, 1.0, 3.0, 5.5, 20.0):              # the refused process's delete of the item may still come
            res = self.look(after=after, keychain=True)
            self.assertEqual(res.action, 'leave')
            self.assertTrue(self.ours())
        self.assertEqual(self.file_text(), text)

    def test_a_look_that_finds_the_item_takes_ours_away_once_its_bytes_are_old_enough(self):
        self.kept_then_gone()
        res = self.look(after=putback.HOLD_TIDY_S, keychain=True)
        self.assertEqual(res.action, 'leave')
        self.assertIsNone(self.file_text())
        self.assertIsNone(self.note())

    def test_the_item_deleted_just_after_a_look_read_it_leaves_claude_code_its_login(self):
        # seen in pile-ups on the real Keychain tool: a look reads the item, the process whose save was refused deletes
        # the item a moment later, and the look goes on with what it read
        text, _ = self.kept_then_gone()
        real, state = putback.hold_tidy, {'done': False}

        def tidy(ctx, lr=None, **kw):
            if lr is not None and not state['done']:
                state['done'] = True
                self.cc_deletes_item()                        # after the look's read, before the look acts on it
            return real(ctx, lr=lr, **kw)
        with mock.patch.object(putback, 'hold_tidy', tidy):
            self.assertEqual(self.look(after=0.3, keychain=True).action, 'leave')
        self.assertTrue(state['done'])
        self.assertIsNone(self.rig.live_text())
        self.assertEqual(self.file_text(), text)              # the one login left: Claude Code reads it
        self.assertTrue(self.ours())
        self.assertEqual(self.look(after=0.2, keychain=True).action, 'wait')       # never promoted at first sight
        res = self.look(after=putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, 'promote', res.reason)
        self.assertEqual(json.loads(self.rig.live_text())['claudeAiOauth'], json.loads(text)['claudeAiOauth'])
        self.assertEqual(self.file_text(), text)              # it stays through the promote, for a reader on its way
        self.look(after=putback.HOLD_TIDY_S, keychain=True)
        self.assertIsNone(self.file_text())

    def test_beside_an_item_without_a_login_ours_stays_and_the_item_is_put_back_from_it(self):
        # a process that read nothing in the moment before the file was back saved its object without the login; the
        # file holds the newest login there is (here: a renewal the saved copy does not have yet)
        newer = self.rig.claude_renews()
        text, _ = self.kept_then_gone(text=newer)
        self.rig.set_live(json.dumps(MCP, separators=(',', ':')))
        self.assertEqual(self.look(after=0.1, keychain=True).action, 'wait')
        self.assertTrue(self.ours())
        self.clock.advance(putback.HOLD_TIDY_S)               # old enough to go beside an item: it stays for this
        res = self.look(after=putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, 'put_back', res.reason)
        self.assertEqual(json.loads(self.rig.live_text())['claudeAiOauth'], json.loads(text)['claudeAiOauth'])
        # kept, holding the item's text, for the processes still reading it; its time starts again at the write
        self.assertEqual(self.file_text(), self.rig.live_text())
        self.assertTrue(self.ours())
        self.look(after=putback.HOLD_TIDY_S + 0.1, keychain=True)
        self.assertIsNone(self.file_text())
        self.assertIsNone(self.note())

    def test_beside_an_item_without_a_login_ours_that_cannot_repair_it_goes_when_it_is_old_enough(self):
        # the file's login is a sign-in the saved accounts do not know: the rule leaves the item alone because of the
        # file, and nobody reads a file beside an item, so ours goes and the saved login is put back
        self.kept_then_gone(text=self.signed_in_again())
        self.rig.set_live(json.dumps(MCP, separators=(',', ':')))
        self.assertEqual(self.look(after=0.1, keychain=True).key, 'file')
        self.assertTrue(self.ours())
        res = self.look(after=putback.HOLD_TIDY_S)
        self.assertIsNone(self.file_text())
        self.assertNotEqual(res.key, 'file')
        self.assertEqual(res.action, 'wait')                  # the lost login's own wait, then the saved copy

    def test_beside_an_item_without_a_login_ours_does_not_stay_past_two_minutes(self):
        newer = self.rig.claude_renews()
        self.kept_then_gone(text=newer)
        self.rig.set_live(json.dumps(MCP, separators=(',', ':')))
        self.rig.kc.rule('write', service=self.rig.service, why='locked', times=-1)   # every put-back is refused
        for _ in range(11):
            self.look(after=10.0)
            self.assertTrue(self.ours())
        self.look(after=putback.HOLD_KEEP_S - 110.0 + 1.0)
        self.assertIsNone(self.file_text())
        self.assertIsNone(self.note())

    def test_settings_naming_nobody_take_ours_away_and_empty_the_holder(self):
        self.kept_then_gone()
        self.settings_name(None)
        out = self.hold('settings')
        self.assertEqual((out['did'], out['why']), ('removed', 'nobody'))
        self.assertIsNone(self.file_text())
        self.assertIsNone(self.note())
        self.assertIsNone(self.holder_text())
        self.assertIsNone(self.hold('gone')['did'])
        self.assertIsNone(self.file_text())

    def test_settings_naming_nobody_empty_the_holder_with_no_file_of_ours_there(self):
        self.cc_writes()
        self.assertEqual(self.hold('appeared')['did'], 'kept')
        self.settings_name(None)                              # a sign-out ...
        self.assertEqual(self.hold('settings')['why'], 'nobody')
        self.assertIsNone(self.holder_text())
        self.settings_name(B)                                 # ... and a sign-in to the same account within the 3 s
        self.cc_removes()
        self.assertIsNone(self.hold('gone')['did'])           # the bytes from before the sign-out never come back
        self.assertIsNone(self.file_text())

    def test_settings_naming_another_account_and_back_put_nothing_back(self):
        self.cc_writes()
        self.hold('appeared')
        self.settings_name(A)
        self.assertEqual(self.hold('settings')['why'], 'another')
        self.settings_name(B)
        self.cc_removes()
        self.assertIsNone(self.hold('gone')['did'])
        self.assertIsNone(self.file_text())

    def test_settings_naming_another_account_take_ours_away_and_empty_the_holder(self):
        self.kept_then_gone()
        self.settings_name(A)
        out = self.hold('settings')
        self.assertEqual((out['did'], out['why']), ('removed', 'another'))
        self.assertIsNone(self.file_text())
        self.assertIsNone(self.holder_text())

    def test_a_look_of_another_process_takes_ours_away_once_the_settings_name_nobody(self):
        self.kept_then_gone()
        self.cc_deletes_item()
        putback.reset()                                       # the loop's process: no holder of its own
        self.settings_name(None)
        writes = self.rig.kc.count('write', self.rig.service)
        api.put_back_pass()
        self.assertIsNone(self.file_text())
        self.assertIsNone(self.note())
        self.assertIsNone(self.rig.live_text())
        self.assertEqual(self.rig.kc.count('write', self.rig.service), writes)

    def test_a_file_claude_code_wrote_is_never_taken_away(self):
        theirs = self.cc_writes()
        ino = os.stat(self.cred).st_ino
        self.look(keychain=True)
        self.hold('start')
        self.settings_name(None)
        self.hold('settings')
        self.look(after=1.0, settings=True)
        api.put_back_pass()
        self.assertEqual(self.file_text(), theirs)
        self.assertEqual(os.stat(self.cred).st_ino, ino)

    def test_a_note_whose_file_was_replaced_goes_and_claude_codes_file_stays(self):
        self.kept_then_gone()
        self.clock.advance(0.2)
        theirs = self.cc_writes(json.dumps(dict(MCP, discoveryState={'p6': 4})))     # Claude Code wrote it again
        self.assertIsNone(self.hold('start')['did'])
        self.assertIsNone(self.note())
        self.assertEqual(self.file_text(), theirs)

    def test_ours_that_is_the_only_login_is_never_taken_away_because_it_is_not_proven(self):
        # a sign-in inside Claude Code a moment ago, which the saved accounts do not know yet: the file is the only
        # copy of it. The rule for a plain login file leaves it (not proven the account's newest), and so does the
        # hold; Claude Code works from it, the keep pass saves it, and then it is promoted like any such file
        text, out = self.kept_then_gone(text=self.signed_in_again())
        self.assertEqual(out['did'], 'held')
        self.cc_deletes_item()
        res = self.look(after=0.1, keychain=True)
        self.assertEqual(res.key, 'file_owner')
        self.assertEqual(self.file_text(), text)
        self.assertTrue(self.ours())
        self.assertIsNone(self.rig.live_text())               # nothing was written into the item for it
        actions = []
        for after in (1.0, putback.PUT_BACK_AFTER_S, putback.PUT_BACK_AFTER_S):
            actions.append(self.look(after=after, keychain=True).action)
            if actions[-1] == 'promote':
                break
            self.assertEqual(self.file_text(), text)          # Claude Code is never without its login on the way
        self.assertEqual(actions[-1], 'promote', actions)
        self.assertEqual(json.loads(self.rig.live_text())['claudeAiOauth'], json.loads(text)['claudeAiOauth'])
        self.assertEqual(self.file_text(), text)              # it stays through the promote, for a reader on its way
        self.look(after=putback.HOLD_TIDY_S, keychain=True)
        self.assertIsNone(self.file_text())

    def test_a_file_of_ours_that_keeps_claude_code_signed_in_stays_and_is_said_once(self):
        self.kept_then_gone()
        self.cc_deletes_item()
        self.rig.kc.rule('write', service=self.rig.service, why='locked', times=-1)   # every promote is refused
        for _ in range(30):
            self.look(after=5.0)
        self.assertTrue(self.ours())
        said = [x for x in self.rig.logs if x == putback.HOLD_STAYS_LINE]
        self.assertEqual(len(said), 1, self.rig.logs[-5:])
        with open(self.ctx.path(sw.RECORD)) as f:
            lines = [json.loads(x) for x in f]
        self.assertEqual([dict(x, t=0) for x in lines], [{'kind': 'hold', 'did': 'stays', 't': 0}])
        self.assert_no_secret_in(lines)


class AtTheWatchsStart(Base):
    def test_ours_whose_bytes_are_not_the_named_accounts_login_is_removed(self):
        self.settings_name(A)                                 # kept and put back while the settings named A ...
        self.kept_then_gone(text=self.rig.vault('uuid-a'))
        putback.reset()
        self.settings_name(B)                                 # ... and they changed while no watch ran
        out = self.hold('start')
        self.assertEqual((out['did'], out['why']), ('removed', 'start'))
        self.assertIsNone(self.file_text())
        self.assertIsNone(self.note())

    def test_ours_whose_bytes_are_the_named_accounts_own_stays_until_a_look_finds_the_item(self):
        self.kept_then_gone()
        putback.reset()
        out = self.hold('start')
        self.assertIsNone(out['did'])
        self.assertTrue(self.ours())
        self.assertTrue(0 < out['look_in'] <= putback.HOLD_TIDY_S + putback.HOLD_LOOK_S)  # the watch looks then
        self.look(keychain=True, tick=True)
        self.assertTrue(self.ours())                          # young: the item may still be deleted
        self.look(after=out['look_in'], tick=True)
        self.assertIsNone(self.file_text())

    def test_ours_is_removed_when_the_settings_name_nobody(self):
        self.kept_then_gone()
        putback.reset()
        self.settings_name(None)
        self.assertEqual(self.hold('start')['did'], 'removed')
        self.assertIsNone(self.file_text())

    def test_a_note_that_matches_no_file_is_removed(self):
        with open(self.ctx.path(putback.HOLD_FILE), 'w') as f:
            json.dump({'path': self.cred, 'tmp': None, 'stamp': [1, 2, 3, 4], 'at': 1.0, 'state': 'settled'}, f)
        self.hold('start')
        self.assertIsNone(self.note())

    def test_a_note_that_cannot_be_read_is_removed(self):
        with open(self.ctx.path(putback.HOLD_FILE), 'w') as f:
            f.write('{not json')
        self.hold('start')
        self.assertIsNone(self.note())

    def test_a_temporary_name_left_after_the_settled_note_is_removed(self):
        self.cc_writes()
        self.hold('appeared')
        self.cc_removes()
        real = putback._hold_unlink

        def unlink(path):
            if path and path.endswith('.hold'):
                raise Killed()
            real(path)
        with mock.patch.object(putback, '_hold_unlink', unlink):
            with self.assertRaises(Killed):
                self.hold('gone')
        self.assertEqual(self.note()['state'], 'settled')
        self.assertEqual(len(self.leftovers()), 1)
        putback.reset()
        self.hold('start')
        self.assertEqual(self.leftovers(), [])
        self.assertTrue(self.ours())                          # the named account's own login: known, and it stays


class NeverPromotedAtFirstSight(Base):
    def lost_beside_ours(self):
        text, out = self.kept_then_gone()
        self.assertEqual(out['did'], 'held')
        self.cc_deletes_item()                                # the refused process's own delete came after
        return text

    def test_through_a_reading_look_only_the_second_look_promotes(self):
        text = self.lost_beside_ours()
        first = self.look(after=0.05, keychain=True, login_file='gone', login_file_written=True)
        self.assertEqual(first.action, 'wait')
        self.assertIsNone(self.rig.live_text())               # nothing written at the first look
        self.assertTrue(self.ours())
        self.assertIsNotNone(putback.load_record(self.ctx)['fault_at'])   # the write the watch reported is proof
        second = self.look(after=putback.FAULT_AFTER_S)
        self.assertEqual(second.action, putback.PROMOTE, second.reason)
        self.assertEqual(self.rig.live_text(), text)          # Claude Code's own bytes, created only
        self.assertIn((self.rig.service, self.rig.account), self.rig.kc.created)
        self.assertEqual(self.file_text(), text)              # left as it is for now: a reader may be on its way to it
        self.assertTrue(self.ours())
        self.look(after=putback.HOLD_TIDY_S, keychain=True)   # and taken away once the item has been back a while
        self.assertIsNone(self.file_text())
        self.assertIsNone(self.note())

    def test_through_the_straight_path_what_is_written_is_decided_under_the_save_lock(self):
        self.lost_beside_ours()
        self.assertEqual(self.look(after=0.05, keychain=True, login_file='gone',
                                   login_file_written=True).action, 'wait')
        self.assertEqual(putback.load_record(self.ctx)['missing_how'], 'file')
        renewed = json.loads(self.rig.server.refresh_for(self.b_text))
        newer = json.dumps(dict(renewed, discoveryState={'p7': 5}), separators=(',', ':'))
        real = putback._presumed
        presumed = []

        def spy(ctx, how):
            lr = real(ctx, how)
            presumed.append((how, lr.read.value if lr is not None else None))
            self.cc_writes(newer)                             # Claude Code writes the file again meanwhile
            return lr
        with mock.patch.object(putback, '_presumed', spy):
            res = self.look(after=putback.FAULT_AFTER_S)
        self.assertEqual([h for h, _ in presumed], ['file'])  # the straight path presumed the hold's own bytes
        self.assertNotEqual(presumed[0][1], newer)
        self.assertEqual(res.action, putback.PROMOTE, res.reason)
        self.assertEqual(self.rig.live_text(), newer)         # what the read under the save lock found went in

    def test_the_holds_file_is_never_proof_by_its_age(self):
        self.lost_beside_ours()
        first = self.look(after=0.05)                         # no hint: only the file's age could be proof
        self.assertEqual(first.action, 'wait')
        self.assertIsNone(putback.load_record(self.ctx)['fault_at'])
        self.assertEqual(first.next_s, putback.PUT_BACK_AFTER_S)
        self.assertEqual(self.look(after=putback.FAULT_AFTER_S).action, 'wait')   # no quick promote without proof


class TheProofRuleForTheHoldsFile(unittest.TestCase):
    """putback._sightings with the stamp of the file the hold put back (hold_file)."""
    NOW = 1794837600.0
    STAMP = (1, 2, 1794837599000000000, 3900)
    OURS = (1, 9, 1794837590000000000, 3900)                 # the stamp a put-back of ours left (rec['written'])

    def proven(self, hint, wrote_at=None, fresh=False, hold_file=STAMP):
        seen = putback.Seen(live=api.PRESENT, login='oauth', from_file=True, full=True, owner=True, file_there=True,
                            file_fresh=fresh, named='uuid-b', saved='ok')
        rec = {'missing': None, 'done': [], 'failed': [], 'fault_at': None, 'wrote_at': wrote_at, 'episode': None,
               'episodes': [], 'written': {'file': list(self.OURS)} if wrote_at else None}
        putback._sightings(seen, hint, self.NOW, file_now=self.STAMP, hold_file=hold_file)(rec)
        return putback.fault(rec, self.NOW)

    def test_its_fresh_age_is_never_proof(self):
        self.assertFalse(self.proven(None, fresh=True))
        self.assertTrue(self.proven(None, fresh=True, hold_file=None))      # the same file, not the hold's: proof

    def test_a_write_the_watch_reported_before_it_was_put_back_is_still_proof(self):
        self.assertTrue(self.proven({'login_file': 'gone', 'login_file_written': True}))

    def test_after_a_write_of_ours_it_counts_as_no_file(self):
        # a write of ours less than FAULT_SEEN_S ago, its file gone since and the hold's in its place: the write the
        # watch reported may be ours, as for a file that went away after a write of ours
        self.assertFalse(self.proven({'login_file': 'gone', 'login_file_written': True}, wrote_at=self.NOW - 2.0))
        self.assertTrue(self.proven({'login_file': 'gone', 'login_file_written': True},
                                    wrote_at=self.NOW - putback.FAULT_SEEN_S - 1.0))


class ASwitch(Base):
    def test_ours_is_taken_away_not_rewritten_and_never_comes_back(self):
        self.kept_then_gone()
        self.assertTrue(self.ours())
        during = {}
        real = putback.hold_drop

        def drop(ctx, env=None):
            removed = real(ctx, env)
            during['removed'] = removed
            during['hold'] = putback.hold_seen(ctx, 'gone')   # the watch sees the removal while the switch runs
            return removed
        with mock.patch.object(putback, 'hold_drop', drop):
            switcher.switch(self.ctx, 'uuid-a', kind='manual', source='cli', reason='')
        self.assertTrue(during['removed'])
        self.assertIsNone(during['hold']['did'])              # the switch's journal was there
        self.assertIn('-uuid-a-s', json.loads(self.rig.live_text())['claudeAiOauth']['refreshToken'])
        self.assertIsNone(self.file_text())
        self.assertIsNone(self.note())
        self.assertIsNone(self.hold('gone')['did'])           # the settings name A now
        self.assertIsNone(self.file_text())
        self.assertNotIn('file', self.rig.doc()['switches'][-1])

    def test_claude_codes_own_file_is_still_rewritten_by_a_switch(self):
        self.cc_writes()
        switcher.switch(self.ctx, 'uuid-a', kind='manual', source='cli', reason='')
        self.assertEqual(self.file_text(), self.rig.live_text())


class NeverWhileNobodyNorAfterATakeBack(Base):
    def put_back_of_ours(self):
        """Claude Code lost its login with no file; Switchboard put the saved login back (created the item)."""
        self.cc_deletes_item()
        self.look(after=0.2, keychain=True)
        res = self.look(after=putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, 'put_back', res.reason)
        return self.rig.live_text()

    def test_nothing_is_put_back_while_the_settings_name_nobody(self):
        self.settings_name(None)
        self.cc_writes()
        self.assertIsNone(self.hold('appeared')['did'])
        self.cc_removes()
        self.assertIsNone(self.hold('gone')['did'])
        self.assertIsNone(self.file_text())

    def test_after_a_take_backs_delete_nothing_is_put_back(self):
        ours = self.put_back_of_ours()
        self.cc_writes(ours)                                  # a refused save of a chat writes the file
        self.assertEqual(self.hold('appeared')['did'], 'kept')
        self.settings_name(None)                              # a chosen sign-out
        self.hold('settings')
        res = self.look(after=0.3, settings=True)
        self.assertIn('took its write back', res.line or '')
        self.assertIsNone(self.rig.live_text())               # the take-back deleted the item it created
        self.assertTrue(os.path.exists(self.cred))            # ... and left the file to the sign-out
        self.cc_removes()
        self.assertIsNone(self.hold('gone')['did'])
        self.assertIsNone(self.file_text())

    def test_a_sign_out_that_removes_the_file_first_leaves_nothing_after_its_settings_change(self):
        ours = self.put_back_of_ours()
        self.cc_writes(ours)
        self.hold('appeared')
        self.cc_deletes_item()
        self.cc_removes()
        self.assertEqual(self.hold('gone')['did'], 'held')    # the leftover: the settings still name B
        self.settings_name(None)
        self.assertEqual(self.hold('settings')['did'], 'removed')
        self.look(after=0.3, settings=True)
        self.assertIsNone(self.file_text())
        self.assertIsNone(self.rig.live_text())
        self.assertIsNone(self.holder_text())
        self.assertIsNone(self.hold('gone')['did'])


class TheUninstaller(Base):
    def tidy(self, code=0):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(sw.main(['--tidy']), code)
        return out.getvalue()

    def test_tidy_removes_ours_and_its_note(self):
        self.kept_then_gone()
        said = self.tidy()
        self.assertIsNone(self.file_text())
        self.assertIsNone(self.note())
        self.assertEqual(said, "Removed the plain login file Switchboard had kept for Claude Code "
                               "(~/.claude/.credentials.json).\n")
        self.assert_no_secret_in(said)

    def test_tidy_leaves_the_only_sign_in_claude_code_has(self):
        text, _ = self.kept_then_gone()
        self.cc_deletes_item()                                # Claude Code reads its login from the file now
        said = self.tidy()
        self.assertEqual(self.file_text(), text)
        self.assertIsNone(self.note())
        self.assertEqual(self.leftovers(), [])
        self.assertEqual(said, "Left Claude Code's plain login file where it is (~/.claude/.credentials.json): Claude "
                               "Code's Keychain item is missing, so this file is the sign-in it is using. Claude Code "
                               "removes it itself the next time it saves its login.\n")
        self.assert_no_secret_in(said)

    def test_tidy_leaves_it_when_the_item_cannot_be_read(self):
        # no answer is not "not there": the file stays, and so does the note, so a second run can finish
        text, _ = self.kept_then_gone()
        self.rig.kc.rule('read', service=self.rig.service, why='locked', times=-1)
        self.assertIn("Could not read Claude Code's Keychain item just now", self.tidy(code=sw.EXIT_AGAIN))
        self.assertEqual(self.file_text(), text)
        self.assertIsNotNone(self.note())

    def test_tidy_removes_ours_with_no_item_once_the_settings_name_nobody(self):
        self.kept_then_gone()
        self.cc_deletes_item()
        self.settings_name(None)                              # a sign-out while no watch ran
        self.assertIn('Removed the plain login file', self.tidy())
        self.assertIsNone(self.file_text())

    def test_tidy_removes_ours_with_no_item_when_it_is_not_the_named_accounts_login(self):
        self.kept_then_gone()
        self.cc_deletes_item()
        self.settings_name(A)                                 # a switch while no watch ran
        self.assertIn('Removed the plain login file', self.tidy())
        self.assertIsNone(self.file_text())

    def test_a_note_that_names_other_files_removes_none_of_them(self):
        # a damaged or edited note: the path of another file with that file's own stamp, another file as the temporary
        # name. Neither the uninstaller nor a look removes anything but Claude Code's plain login file
        other = os.path.join(self.tmp, 'another-file')
        temp = os.path.join(self.tmp, 'a-third-file')
        for name in (other, temp):
            with open(name, 'w') as f:
                f.write('made-up')
        st = os.stat(other)
        cases = [{'path': other, 'tmp': None, 'stamp': [st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size],
                  'at': self.clock.wall(), 'state': 'settled'},
                 {'path': self.cred, 'tmp': temp, 'stamp': None, 'at': self.clock.wall() - 60.0, 'state': 'pending'},
                 {'path': self.cred, 'tmp': os.path.join(os.path.dirname(self.cred), 'not-ours.hold'),
                  'stamp': [1, 2, 3, 4], 'at': 1.0, 'state': 'settled'}]
        with open(cases[2]['tmp'], 'w') as f:
            f.write('made-up')
        for note in cases:
            for act in (self.tidy, lambda: self.look(keychain=True), lambda: self.hold('start')):
                with open(self.ctx.path(putback.HOLD_FILE), 'w') as f:
                    json.dump(note, f)
                act()
                self.assertTrue(os.path.exists(other) and os.path.exists(temp) and os.path.exists(cases[2]['tmp']),
                                (note, act))
        with open(self.ctx.path(putback.HOLD_FILE), 'w') as f:
            json.dump(cases[0], f)                            # a note that names a file of another name can't be used:
        self.look(keychain=True)                              # it goes at the next look, and that file stays
        self.assertIsNone(self.note())
        self.assertTrue(os.path.exists(other))

    def test_a_note_for_another_claude_code_folder_is_left_alone_by_looks(self):
        elsewhere = os.path.join(self.tmp, 'another-folder', '.credentials.json')
        note = {'path': elsewhere, 'tmp': None, 'stamp': [1, 2, 3, 4], 'at': self.clock.wall(), 'state': 'settled'}
        with open(self.ctx.path(putback.HOLD_FILE), 'w') as f:
            json.dump(note, f)
        self.look(keychain=True)
        self.hold('start')
        self.assertEqual(self.note(), note)                   # another process's note: neither used nor removed

    def test_tidy_never_removes_claude_codes_file(self):
        self.kept_then_gone()
        theirs = self.cc_writes(json.dumps(dict(MCP, discoveryState={'p8': 6})))
        self.assertEqual(self.tidy(), '')
        self.assertEqual(self.file_text(), theirs)
        self.assertIsNone(self.note())

    def test_tidy_removes_one_put_in_place_by_a_process_stopped_before_its_settled_note(self):
        self.cc_writes()
        self.hold('appeared')
        self.cc_removes()
        real = putback._hold_write_note

        def write(ctx, note, sync=True):
            if note['state'] == 'settled':
                raise Killed()
            real(ctx, note, sync=sync)
        with mock.patch.object(putback, '_hold_write_note', write):
            with self.assertRaises(Killed):
                self.hold('gone')
        self.tidy()
        self.assertIsNone(self.file_text())
        self.assertEqual(self.leftovers(), [])
        self.assertIsNone(self.note())

    def uninstall_lines(self):
        with open(os.path.join(ROOT, 'uninstall.sh')) as f:
            text = f.read()
        start = text.index('HOLD_PY=')
        end = text.index('\nfi\n', start) + 4
        return text[start:end]

    def run_uninstall_lines(self, rc=0):
        data = self.ctx.data_dir
        os.makedirs(os.path.join(data, 'bin'), exist_ok=True)
        program = os.path.join(data, 'bin', 'switchboard')
        with open(program, 'w') as f:
            f.write('#!/bin/sh\nPY="%s"\nexit 0\n' % sys.executable)     # the launcher the installer writes
        os.chmod(program, 0o700)
        shutil.copytree(os.path.join(ROOT, 'switchboard'), os.path.join(data, 'app', 'switchboard'),
                        ignore=shutil.ignore_patterns('__pycache__'))         # the installed program's copy
        env = {'PATH': '/usr/bin:/bin', 'HOME': self.rig.home, 'SWITCHBOARD_HOME': data,
               'SWITCHBOARD_TEST_RUN': os.environ.get('SWITCHBOARD_TEST_RUN', '1')}
        script = 'set -eu\nDATA="%s"\nPROGRAM="%s"\n%s' % (data, program, self.uninstall_lines())
        r = subprocess.run(['/bin/sh', '-c', script], env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, rc, r.stderr)
        self.stderr = r.stderr
        return r.stdout

    def test_the_uninstallers_lines_run_the_tidy_with_the_programs_python(self):
        self.kept_then_gone()
        self.settings_name(None)                              # nobody is signed in: ours serves nobody
        self.assertIn('Removed the plain login file', self.run_uninstall_lines())
        self.assertIsNone(self.file_text())
        self.assertIsNone(self.note())

    def test_the_uninstallers_lines_leave_the_file_when_the_keychain_cannot_be_asked(self):
        # that process cannot read the Keychain here (the test mode); the file may be the only sign-in: it stays,
        # with the note, the line asks for another run, and the uninstaller stops there (exit 1), before the program
        # and ~/.switchboard go, so that run can be made
        text, _ = self.kept_then_gone()
        self.assertIn("Run the uninstaller again to finish", self.run_uninstall_lines(rc=1))
        self.assertIn('The program and its data folder were kept, so this can run again', self.stderr)
        self.assertEqual(self.file_text(), text)
        self.assertIsNotNone(self.note())

    def test_the_uninstallers_lines_say_so_when_the_program_is_not_there_to_look(self):
        self.kept_then_gone()
        data = tempfile.mkdtemp(prefix='hold-uninstall-')
        self.addCleanup(shutil.rmtree, data, True)
        with open(os.path.join(data, putback.HOLD_FILE), 'w') as f:
            f.write('{}')
        env = {'PATH': '/usr/bin:/bin', 'HOME': self.rig.home}
        script = 'set -eu\nDATA="%s"\nPROGRAM="%s/bin/switchboard"\n%s' % (data, data, self.uninstall_lines())
        r = subprocess.run(['/bin/sh', '-c', script], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual((r.returncode, r.stderr), (0, ''))
        self.assertIn('the program was not there to look at it, so it was left as it is', r.stdout)
        self.assertTrue(self.ours())

    def test_the_uninstallers_lines_say_nothing_without_a_note(self):
        data = tempfile.mkdtemp(prefix='hold-uninstall-')
        self.addCleanup(shutil.rmtree, data, True)
        env = {'PATH': '/usr/bin:/bin', 'HOME': self.rig.home}
        script = 'set -eu\nDATA="%s"\nPROGRAM="%s/bin/switchboard"\n%s' % (data, data, self.uninstall_lines())
        r = subprocess.run(['/bin/sh', '-c', script], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, '', ''))


if __name__ == '__main__':
    unittest.main()
