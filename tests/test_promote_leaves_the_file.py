import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# A promote (the login that sat only in Claude Code's plain login file, written back into the Keychain item) no longer
# removes that file at once. The Claude Code processes that were told "no such item" while the item was gone trust
# that answer for 30 s and read their login from the file meanwhile; with the file gone they have no login anywhere
# (seen in pile-ups of stand-in savers on the real Keychain tool: requests failed from 0.04 to 0.7 s after the
# promote until those 30 s were over). The file now stays exactly as it is, as one the sign-in hold takes away: by a
# look once the item has been back for HOLD_TIDY_S, at once when the settings name nobody, by a switch's write, and
# never when Claude Code wrote it again since. With the hold's setting off, or when its note can't be written, the
# file is removed at once, as before.
# The account engine on its stand-ins (r17_engine_fakes) with a fake clock; made-up *.example accounts.
import json
import os
import unittest
from unittest import mock

from hold_fakes import Base, stamp_of
from switchboard.accounts import api, putback, switcher


class Promoted(Base):
    def lost_to_the_file(self, text=None):
        """Claude Code's refused save moved its login to the plain login file and deleted the item; the first look
        saw it."""
        text = self.cc_writes(text)
        self.hold('appeared')
        self.cc_deletes_item()
        self.assertEqual(self.look(after=0.2, keychain=True).action, 'wait')
        return text

    def promoted(self, text=None):
        """... and the second look put the login back in the item. Returns (the file's text, its stamp before)."""
        text = self.lost_to_the_file(text)
        before = stamp_of(self.cred)
        res = self.look(after=putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, 'promote', res.reason)
        self.assertEqual(json.loads(self.rig.live_text())['claudeAiOauth'], json.loads(text)['claudeAiOauth'])
        return text, before


class TheFileStays(Promoted):
    def test_it_is_left_exactly_as_it_was_and_known_as_one_to_take_away(self):
        text, before = self.promoted()
        self.assertEqual(self.file_text(), text)
        self.assertEqual(stamp_of(self.cred), before)         # neither rewritten nor replaced: the very same file
        self.assertTrue(self.ours())
        note = self.note()
        self.assertEqual((note['state'], note['tmp'], note['path']), ('settled', None, self.cred))
        self.assertAlmostEqual(note['wrote'], self.clock.wall(), places=3)
        self.assertEqual(putback.load_record(self.ctx)['written']['file'], list(before))
        self.assert_no_secret_in(note)

    def test_nothing_removes_or_replaces_it_during_the_look_that_writes(self):
        self.lost_to_the_file()
        gone = []
        real_unlink, real_replace = os.unlink, os.replace

        def unlink(path, *a, **kw):
            gone.append(path) if path == self.cred else None
            return real_unlink(path, *a, **kw)

        def replace(src, dst, *a, **kw):
            gone.append(dst) if dst == self.cred else None
            return real_replace(src, dst, *a, **kw)
        with mock.patch.object(os, 'unlink', unlink), mock.patch.object(os, 'replace', replace):
            self.assertEqual(self.look(after=putback.PUT_BACK_AFTER_S).action, 'promote')
        self.assertEqual(gone, [])

    def test_once_the_item_has_been_back_long_enough_a_look_takes_it_away(self):
        self.promoted()
        self.assertEqual(self.look(after=putback.HOLD_TIDY_S - 0.5, keychain=True).action, 'leave')
        self.assertTrue(self.ours())
        self.assertEqual(self.look(after=0.6, keychain=True).action, 'leave')
        self.assertIsNone(self.file_text())
        self.assertIsNone(self.note())
        self.assertIsNotNone(self.rig.live_text())

    def test_the_watch_is_told_after_each_look_when_that_look_is_due(self):
        self.promoted()
        out = self.hold('looked')
        self.assertTrue(out['ours'])
        self.assertAlmostEqual(out['look_in'], putback.HOLD_TIDY_S + putback.HOLD_LOOK_S, places=3)
        self.clock.advance(4.0)
        self.assertAlmostEqual(self.hold('looked')['look_in'], putback.HOLD_TIDY_S - 4.0 + putback.HOLD_LOOK_S,
                               places=3)
        self.assertTrue(self.ours())                          # telling removes nothing

    def test_old_enough_and_still_there_a_look_is_asked_for_again_until_it_is_gone(self):
        # the look made at the time could not take it away (the Keychain did not answer): the file must not wait for
        # the next regular look, five minutes later
        self.promoted()
        self.clock.advance(putback.HOLD_TIDY_S + 0.3)
        self.rig.kc.rule('read', service=self.rig.service, why='timeout', times=3)
        self.look(tick=True)
        self.assertTrue(self.ours())
        self.assertEqual(self.hold('looked')['look_in'], putback.HOLD_AGAIN_S)
        del self.rig.kc.rules[:]
        self.look(after=putback.HOLD_AGAIN_S, tick=True)
        self.assertIsNone(self.file_text())
        self.assertNotIn('look_in', self.hold('looked'))      # gone: nothing more is asked for

    def test_a_file_that_is_claude_codes_sign_in_for_minutes_counts_its_time_from_the_last_look_that_read_no_item(self):
        text, _ = self.promoted()
        self.clock.advance(1.0)
        self.cc_deletes_item()
        self.rig.kc.rule('write', service=self.rig.service, why='locked', times=-1)   # nothing can be put back
        self.clock.advance(putback.HOLD_KEEP_S + 1.0)
        self.look(tick=True)
        self.assertEqual(self.file_text(), text)              # the only sign-in Claude Code has: it stays
        # the processes that asked at that look read the file for 30 s more: its time started again then
        self.assertAlmostEqual(self.note()['wrote'], self.clock.wall(), places=3)
        self.assertAlmostEqual(self.hold('looked')['look_in'], putback.HOLD_TIDY_S + putback.HOLD_LOOK_S, places=3)

    def test_old_enough_while_the_keychain_does_not_answer_it_is_looked_at_once_a_minute(self):
        text, _ = self.promoted()
        self.clock.advance(putback.HOLD_KEEP_S + 1.0)
        self.rig.kc.rule('read', service=self.rig.service, why='timeout', times=-1)
        self.look(tick=True)
        self.assertEqual(self.file_text(), text)              # no look can judge it: it stays
        self.assertEqual(self.hold('looked')['look_in'], putback.HOLD_AGAIN_LATER_S)

    def test_with_no_file_to_take_away_the_call_after_a_look_asks_for_nothing(self):
        out = self.hold('looked')
        self.assertIsNone(out['did'])
        self.assertNotIn('look_in', out)
        self.assert_no_keychain_or_network()

    def test_a_process_that_asked_while_the_item_was_gone_reads_the_file_for_its_thirty_seconds(self):
        # that process trusts "no such item" for 30 s and reads the plain login file at every request meanwhile
        text, _ = self.promoted()
        for _ in range(32):                                   # its requests, one a second, with the watch's looks
            self.look(after=1.0, keychain=True)
            self.assertEqual(self.file_text(), text)
        self.assertGreaterEqual(putback.HOLD_TIDY_S, 30.0 + 2.0)

    def test_the_item_lost_again_meanwhile_finds_the_file_still_there(self):
        text, _ = self.promoted()
        self.clock.advance(2.0)
        self.cc_deletes_item()                                # the pile-up goes on: the item is deleted again
        actions = []
        for _ in range(8):
            actions.append(self.look(after=5.0, keychain=True).action)
            if actions[-1] == 'promote':
                break
            self.assertEqual(self.file_text(), text)          # Claude Code reads its login from it all the while
        self.assertEqual(actions[-1], 'promote', actions)
        self.assertEqual(json.loads(self.rig.live_text())['claudeAiOauth'], json.loads(text)['claudeAiOauth'])


class WhatTakesItAwaySooner(Promoted):
    def test_a_sign_out_takes_the_file_away_at_once_and_the_write_back(self):
        self.promoted()
        self.clock.advance(1.0)
        self.settings_name(None)                              # the sign-out's settings step
        out = self.hold('settings')
        self.assertEqual((out['did'], out['why']), ('removed', 'nobody'))
        self.assertIsNone(self.file_text())
        self.assertIsNone(self.note())
        self.look(after=0.3, settings=True)
        self.assertIsNone(self.rig.live_text())               # the promote's write is taken back

    def test_a_look_of_another_process_does_the_same(self):
        self.promoted()
        putback.reset()                                       # the loop's process: nothing in memory
        self.settings_name(None)
        self.look(after=0.5, settings=True)
        self.assertIsNone(self.file_text())
        self.assertIsNone(self.rig.live_text())

    def test_a_switch_removes_it_instead_of_rewriting_it(self):
        self.promoted()
        switcher.switch(self.ctx, 'uuid-a', kind='manual', source='cli', reason='')
        self.assertIsNone(self.file_text())
        self.assertIsNone(self.note())
        self.assertIn('-uuid-a-s', json.loads(self.rig.live_text())['claudeAiOauth']['refreshToken'])

    def test_a_file_claude_code_wrote_since_is_left_to_claude_code(self):
        self.promoted()
        self.clock.advance(1.0)
        theirs = self.cc_writes(json.dumps(dict(json.loads(self.rig.live_text()), discoveryState={'p5': 2})))
        self.assertEqual(self.hold('changed')['did'], 'kept')  # a write of Claude Code's own again
        self.look(after=putback.HOLD_TIDY_S + 1.0, keychain=True)
        self.assertEqual(self.file_text(), theirs)
        self.assertIsNone(self.note())


class AFileTheHoldPutBack(Promoted):
    def test_promoted_its_time_counts_from_the_promote_not_from_claude_codes_write(self):
        text, out = self.kept_then_gone()
        self.assertEqual(out['did'], 'held')
        self.cc_deletes_item()
        self.assertEqual(self.look(after=0.2, keychain=True).action, 'wait')
        before = stamp_of(self.cred)
        self.assertEqual(self.look(after=putback.PUT_BACK_AFTER_S).action, 'promote')
        self.assertEqual(self.file_text(), text)
        self.assertEqual(stamp_of(self.cred), before)
        self.assertAlmostEqual(self.note()['wrote'], self.clock.wall(), places=3)
        self.look(after=putback.HOLD_TIDY_S - 0.5, keychain=True)      # Claude Code wrote the bytes longer ago
        self.assertTrue(self.ours())
        self.look(after=0.6, keychain=True)
        self.assertIsNone(self.file_text())
        self.assertEqual(self.leftovers(), [])


class AFileThatHeldAnOlderLogin(Promoted):
    def test_it_is_rewritten_with_the_login_now_in_the_item_and_then_taken_away(self):
        # a process wrote what it had read before a renewal: the file's refresh token is used up. The saved copy (the
        # newer login) goes into the item, and a process on its way to the file must not find the used-up one there
        old = self.b_text
        self.clock.advance(600)
        newer = self.rig.claude_renews()
        api.keep_pass()                                       # the saved copy is the newer one
        stale = json.dumps(dict(json.loads(old), discoveryState={'made-up': 4}), separators=(',', ':'))
        self.lost_to_the_file(stale)
        res = self.look(after=putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, 'promote', res.reason)
        item = json.loads(self.rig.live_text())
        self.assertEqual(item['claudeAiOauth'], json.loads(newer)['claudeAiOauth'])
        self.assertEqual(json.loads(self.file_text()), item)  # the same login as the item, never the used-up one
        self.assertTrue(self.ours())
        self.look(after=putback.HOLD_TIDY_S, keychain=True)
        self.assertIsNone(self.file_text())
        self.assertIsNone(self.note())


class WithTheSettingOff(Promoted):
    def setUp(self):
        super().setUp()
        with open(self.ctx.path('settings.json'), 'w') as f:
            json.dump({'auto_policy': {'hold_signin_file': False}}, f)

    def test_the_file_is_removed_at_once_as_before(self):
        self.promoted()
        self.assertIsNone(self.file_text())
        self.assertIsNone(self.note())
        self.assertIsNone(putback.load_record(self.ctx)['written']['file'])


class WhenTheNoteCannotBeWritten(Promoted):
    def test_the_file_is_removed_at_once(self):
        text = self.lost_to_the_file()
        with mock.patch.object(putback, '_hold_write_note', mock.Mock(side_effect=OSError('made-up'))):
            self.assertEqual(self.look(after=putback.PUT_BACK_AFTER_S).action, 'promote')
        self.assertIsNone(self.file_text())                   # nothing is left that nobody knows to take away
        self.assertIsNone(self.note())
        self.assertEqual(json.loads(self.rig.live_text())['claudeAiOauth'], json.loads(text)['claudeAiOauth'])


if __name__ == '__main__':
    unittest.main()
