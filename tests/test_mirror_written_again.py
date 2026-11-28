import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The standing copy is written again when a Claude Code process removes it while the item holds the login. Claude Code
# removes its plain login file after a save into the item landed for a process whose Keychain read had found nothing
# (2.1.329, read in its program text). Claude Code's own write of that file (its fallback after a refused save, which
# it follows by deleting the item) is never written over and never taken for a copy of ours: the hold and the promote
# decide what becomes of it. A file of Claude Code's that has stood HOLD_TIDY_S beside an item holding the login is
# brought up to the item and kept as the standing copy, unless it holds a newer login of the same account.
# The account engine on its stand-ins (fakes_accounts) with a fake clock; made-up *.example accounts.
import json
import os
import unittest

from hold_fakes import Base, stamp_of
from switchboard.accounts import putback


class Mirror(Base):
    MIRROR = True

    def setUp(self):
        super().setUp()
        self.look(keychain=True)
        self.assertTrue(self.ours())
        self.hold('looked')


class ARemovalByClaudeCode(Mirror):
    def test_is_written_again_at_the_next_look(self):
        self.cc_removes()
        self.hold('gone')
        self.look(after=0.2, login_file='gone')
        self.assertEqual(self.file_text(), self.rig.live_text())
        self.assertTrue(self.ours())
        self.assertEqual(os.stat(self.cred).st_mode & 0o777, 0o600)
        self.assertEqual(self.hold('looked')['did'], 'mirrored')

    def test_after_a_renewal_by_the_process_that_removed_it_the_new_login_is_written(self):
        renewed = self.rig.claude_renews()
        self.cc_removes()
        self.hold('gone')
        self.look(after=0.2, login_file='gone', keychain=True)
        self.assertEqual(self.file_text(), renewed)
        self.assertTrue(self.ours())

    def test_again_and_again(self):
        for _ in range(6):
            self.cc_removes()
            self.hold('gone')
            self.look(after=putback.REMAKE_GAP_S, login_file='gone')
            self.assertEqual(self.file_text(), self.rig.live_text())

    def test_a_second_removal_within_remake_gap_s_waits_for_its_end(self):
        # each new copy makes every running Claude Code read its item again: removed and made again in a loop on a
        # loaded Mac, that would feed the load. The first one is made again at once, the next ones REMAKE_GAP_S apart
        self.cc_removes()
        self.hold('gone')
        self.look(after=0.2, login_file='gone')
        self.assertTrue(self.ours())                          # the first: at once
        self.cc_removes()
        self.hold('gone')
        res = self.look(after=1.0, login_file='gone')
        self.assertIsNone(self.file_text())                   # the second: waits
        self.assertIsNotNone(res.next_s)
        self.assertLessEqual(res.next_s, putback.REMAKE_GAP_S)
        self.look(after=putback.REMAKE_GAP_S, keychain=True)
        self.assertTrue(self.ours())

    def test_a_new_login_is_copied_at_once_whatever_the_gap(self):
        self.cc_removes()
        self.hold('gone')
        self.look(after=0.2, login_file='gone')
        self.cc_removes()
        self.hold('gone')
        self.clock.advance(1.0)
        renewed = self.rig.claude_renews()
        self.look(after=0.2, login_file='gone', keychain=True)
        self.assertEqual(self.file_text(), renewed)

    def test_not_while_the_item_is_missing(self):
        self.cc_deletes_item()
        self.cc_removes()
        self.hold('gone')
        self.look(after=0.2, login_file='gone', keychain=True)
        self.assertIsNone(self.file_text())


class ClaudeCodesFallbackWrite(Mirror):
    def test_is_never_written_over_nor_taken_for_ours(self):
        renewed = self.rig.server.refresh_for(self.rig.live_text())     # its save of a renewal was refused
        text = self.cc_writes(renewed)
        out = self.hold('changed')
        self.assertEqual(out['did'], 'kept')                  # the hold keeps Claude Code's own bytes
        self.assertFalse(self.ours())
        self.look(after=0.1, login_file='changed')
        self.assertEqual(self.file_text(), text)              # not brought back to the item's older login
        self.assertFalse(self.ours())

    def test_then_the_item_deleted_promotes_claude_codes_login(self):
        renewed = self.rig.server.refresh_for(self.rig.live_text())
        self.cc_writes(renewed)
        self.hold('changed')
        self.cc_deletes_item()
        self.look(after=0.2, keychain=True, login_file='changed')
        res = self.look(after=putback.FAULT_AFTER_S + 0.1)
        self.assertEqual(res.action, 'promote', res.reason)
        self.assertEqual(json.loads(self.rig.live_text())['claudeAiOauth'], json.loads(renewed)['claudeAiOauth'])
        self.assertEqual(self.file_text(), renewed)
        self.assertTrue(self.ours())                          # kept after the promote, now the standing copy
        renewed2 = self.rig.claude_renews()
        self.look(after=1.0, keychain=True)
        self.assertEqual(self.file_text(), renewed2)


class ClaudeCodesOldFile(Base):
    MIRROR = True

    def test_beside_the_item_for_hold_tidy_s_it_is_brought_up_to_the_item_and_kept(self):
        old = self.rig.live_text()
        self.clock.advance(600.0)
        self.rig.claude_renews()                              # the item moved on; the file stayed
        self.cc_writes(old, age=putback.HOLD_TIDY_S + 1)
        self.look(keychain=True)
        self.assertEqual(self.file_text(), self.rig.live_text())
        self.assertTrue(self.ours())
        out = self.hold('looked')
        self.assertEqual((out['did'], out.get('why')), ('mirrored', 'adopted'))

    def test_one_with_the_items_login_and_other_parts_of_its_own_is_brought_up_and_kept(self):
        # a save Claude Code made into the file instead of the item: the very login, a connector mark of its own
        blob = json.loads(self.rig.live_text())
        blob.setdefault('mcpOAuth', {})['made-up-connector'] = {'discoveryState': 'made-up', 'at': 1}
        self.cc_writes(json.dumps(blob, separators=(',', ':')), age=putback.HOLD_TIDY_S + 1)
        self.look(keychain=True)
        self.assertEqual(self.file_text(), self.rig.live_text())
        self.assertTrue(self.ours())

    def test_a_temporary_name_an_earlier_note_named_goes(self):
        # a process stopped a moment ago between writing its temporary file and putting it in place: the pending note
        # still names that file, and is young, so the look's tidy leaves it; the adoption's note takes its place
        old = self.rig.live_text()
        self.clock.advance(600.0)
        self.rig.claude_renews()
        self.cc_writes(old, age=putback.HOLD_TIDY_S + 1)
        tmp = putback._hold_tmp_name(self.cred)
        with open(tmp, 'w') as f:
            f.write(self.rig.live_text())
        with open(self.ctx.path(putback.HOLD_FILE), 'w') as f:
            json.dump({'path': self.cred, 'tmp': tmp, 'stamp': None, 'at': self.clock.wall() - 1.0, 'wrote': None,
                       'state': 'pending'}, f)
        self.look(keychain=True)
        self.assertTrue(self.ours())
        self.assertFalse(os.path.exists(tmp))
        self.assertEqual(self.leftovers(), [])

    def test_holding_the_items_text_it_is_only_noted(self):
        text = self.cc_writes(self.rig.live_text(), age=putback.HOLD_TIDY_S + 1)
        before = stamp_of(self.cred)
        self.look(keychain=True)
        self.assertEqual((self.file_text(), stamp_of(self.cred)), (text, before))
        self.assertTrue(self.ours())

    def test_one_holding_another_accounts_login_stays_byte_for_byte(self):
        # a process that read A's login before a switch to B renewed it and its save was refused: Claude Code wrote A's
        # newest login to the file, and nowhere else holds it. However old, it is never replaced with B's text
        a = [k for k in self.rig.doc()['accounts'] if k.startswith('uuid-a')][0]
        a_newest = self.rig.server.refresh_for(self.rig.vault(a))
        self.clock.advance(600.0)
        self.rig.claude_renews()                              # B's item, renewed since: A's login reads as older
        self.assertEqual(putback.live_mod.compare(putback.live_mod.parse(a_newest),
                                                  putback.live_mod.parse(self.rig.live_text())), 'older')
        text = self.cc_writes(a_newest, age=putback.HOLD_TIDY_S + 1)
        self.look(keychain=True)
        self.look(after=putback.HOLD_TIDY_S, keychain=True)
        self.assertEqual(self.file_text(), text)
        self.assertFalse(self.ours())

    def test_a_young_one_is_left_to_claude_code(self):
        old = self.rig.live_text()
        self.clock.advance(600.0)
        self.rig.claude_renews()
        self.cc_writes(old, age=putback.HOLD_TIDY_S - 5)
        self.look(keychain=True)
        self.assertEqual(self.file_text(), old)
        self.assertFalse(self.ours())

    def test_one_holding_a_newer_login_of_the_same_account_is_left(self):
        self.clock.advance(600.0)
        newer = self.rig.server.refresh_for(self.rig.live_text())
        self.cc_writes(newer, age=putback.HOLD_TIDY_S + 1)
        self.look(keychain=True)
        self.assertEqual(self.file_text(), newer)
        self.assertFalse(self.ours())

    def test_one_whose_age_against_the_item_cant_be_told_is_left(self):
        other = self.rig.server.refresh_for(self.rig.live_text())     # renewed in the same second: same access end
        self.cc_writes(other, age=putback.HOLD_TIDY_S + 1)
        self.look(keychain=True)
        self.assertEqual(self.file_text(), other)
        self.assertFalse(self.ours())

    def test_one_that_is_not_json_is_left(self):
        self.cc_writes('not json at all', age=putback.HOLD_TIDY_S + 1)
        self.look(keychain=True)
        self.assertEqual(self.file_text(), 'not json at all')
        self.assertFalse(self.ours())


if __name__ == '__main__':
    unittest.main()
