import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# What the sign-in hold keeps, and for how long: only what Claude Code's own fallback wrote. A rewrite of the plain
# login file by a write of Switchboard's own into Claude Code's item (a switch, a put-back, a promote: each rewrites a
# plain login file that exists) is never kept, whichever process made it, and does not end what Claude Code wrote
# before it. Bytes are let go three seconds after Claude Code wrote them, by either clock, and a look is asked for
# then. A file of ours counts as young (HOLD_TIDY_S) from the moment Claude Code wrote its bytes, not from the
# put-back, and a time a little past the clock counts as now. The notes
# written before the file is in place do not wait for the disk, and a note without a stamp claims no file.
# The account engine on its stand-ins (r17_engine_fakes) with a fake clock; made-up *.example accounts.
import json
import os
import unittest
from unittest import mock

from hold_fakes import Base, Killed, stamp_of
from switchboard.accounts import ccfiles, live, putback, switcher


class AWriteOfOurOwn(Base):
    def old_file_beside_a_healthy_item(self):
        """A plain login file Claude Code wrote long ago is still there beside its item (no save of its landed
        since)."""
        return self.cc_writes(age=600.0)

    def test_a_switchs_rewrite_is_not_kept_and_a_sign_out_right_after_brings_nothing_back(self):
        self.old_file_beside_a_healthy_item()
        switcher.switch(self.ctx, 'uuid-a', kind='manual', source='cli', reason='')
        self.assertEqual(self.file_text(), self.rig.live_text())             # the switch rewrote the file
        self.assertIsNone(self.hold('changed')['did'])
        self.assertIsNone(self.holder_text())
        self.cc_deletes_item()                                               # she signs out within three seconds
        self.cc_removes()
        self.assertIsNone(self.hold('gone')['did'])
        self.assertIsNone(self.file_text())

    def test_a_rewrite_by_this_process_is_known_before_it_can_be_seen(self):
        self.old_file_beside_a_healthy_item()
        seen = {}
        real = os.replace

        def replace(src, dst):
            if dst == self.cred:
                seen['own'] = putback._HOLD_OWN.get(self.ctx.data_dir)       # told before the rename
                seen['stamp'] = stamp_of(src)
            real(src, dst)
        with mock.patch.object(ccfiles.os, 'replace', replace):
            done = live.write_live(self.ctx, self.rig.live_text())
        self.assertTrue(done.ok)
        self.assertEqual(seen['own'], seen['stamp'])
        self.assertEqual(stamp_of(self.cred), seen['stamp'])
        self.assertIsNone(self.hold('changed')['did'])
        self.assertIsNone(self.holder_text())

    def test_what_claude_code_wrote_before_our_rewrite_keeps_its_seconds(self):
        text = self.cc_writes()
        self.assertEqual(self.hold('appeared')['did'], 'kept')
        self.clock.advance(0.5)
        self.assertTrue(live.write_live(self.ctx, self.rig.live_text()).ok)  # a write of ours rewrites the file
        self.assertIsNone(self.hold('changed')['did'])
        self.assertEqual(self.holder_text(), text)                           # neither kept anew nor let go
        self.cc_removes()
        self.cc_deletes_item()                                               # a refused process's delete follows
        self.assertEqual(self.hold('gone')['did'], 'held')
        self.assertEqual(self.file_text(), text)

    def test_a_put_back_by_another_process_is_known_by_the_record(self):
        # the page's or the loop's put-back rewrote the plain login file and noted its stamp; the watch's process
        # made no such write itself
        text = self.cc_writes()                               # stands for the file that write left: fresh, a full login
        note = {'at': self.clock.wall(), 'account': 'uuid-b', 'how': 'over', 'sha': 'a' * 64,
                'file': list(stamp_of(self.cred))}
        with open(self.ctx.path('put-back.json'), 'w') as f:
            json.dump({'written': note}, f)
        self.assertEqual(tuple(putback.load_record(self.ctx)['written']['file']), stamp_of(self.cred))
        self.assertIsNone(self.hold('changed')['did'])
        self.assertIsNone(self.holder_text())
        os.unlink(self.ctx.path('put-back.json'))             # without the note the same file is Claude Code's write
        self.assertEqual(self.hold('changed')['did'], 'kept')
        self.assertEqual(self.holder_text(), text)

    def test_a_switch_by_another_process_is_known_by_its_record(self):
        self.old_file_beside_a_healthy_item()
        switcher.switch(self.ctx, 'uuid-a', kind='manual', source='cli', reason='')
        self.assertEqual(tuple(self.rig.doc()['switches'][-1]['file']), stamp_of(self.cred))
        putback.reset()                                       # the watch's process did not make that write
        self.assertIsNone(self.hold('changed')['did'])
        self.assertIsNone(self.holder_text())

    def test_nothing_is_kept_while_a_switch_is_part_way(self):
        with open(switcher.journal_path(self.ctx), 'w') as f:
            f.write('{}')
        self.cc_writes()
        self.assertIsNone(self.hold('appeared')['did'])
        self.assertIsNone(self.holder_text())

    def test_a_write_of_claude_codes_after_ours_is_kept(self):
        self.old_file_beside_a_healthy_item()
        self.assertTrue(live.write_live(self.ctx, self.rig.live_text()).ok)
        self.hold('changed')
        self.clock.advance(0.3)
        text = self.cc_writes(json.dumps(dict(json.loads(self.rig.live_text()), discoveryState={'p4': 1})))
        self.assertEqual(self.hold('changed')['did'], 'kept')
        self.assertEqual(self.holder_text(), text)


class ThreeSeconds(Base):
    def test_the_bytes_are_let_go_three_seconds_after_claude_code_wrote_them(self):
        self.cc_writes()
        out = self.hold('appeared')
        self.assertEqual(out['did'], 'kept')
        self.assertAlmostEqual(out['look_in'], putback.HOLD_FOR_S + putback.HOLD_LOOK_S)   # a look is asked for then
        self.clock.advance(putback.HOLD_FOR_S - 0.1)
        self.hold('settings')
        self.assertIsNotNone(self.holder_text())
        self.clock.advance(0.2)
        self.hold('settings')                                 # any call after their time lets them go
        self.assertIsNone(self.holder_text())

    def test_a_late_word_of_the_watch_about_the_file_lets_them_go(self):
        text, _ = self.kept_then_gone()
        self.assertEqual(self.holder_text(), text)
        self.clock.advance(putback.HOLD_FOR_S)
        self.assertTrue(self.hold('appeared')['ours'])        # the watch tells of the hold's own file, late
        self.assertIsNone(self.holder_text())

    def test_a_look_lets_them_go_too(self):
        self.cc_writes()
        self.hold('appeared')
        self.look(after=putback.HOLD_FOR_S + putback.HOLD_LOOK_S, tick=True)
        self.assertIsNone(self.holder_text())

    def test_not_once_three_seconds_have_passed_by_the_awake_clock_alone(self):
        self.cc_writes()
        self.hold('appeared')
        self.clock.advance(putback.HOLD_FOR_S + 1.0)
        self.clock.step(-(putback.HOLD_FOR_S + 1.0))          # the wall clock was set back: it says no time passed
        self.cc_removes()
        self.assertIsNone(self.hold('gone')['did'])
        self.assertIsNone(self.file_text())

    def test_a_file_of_ours_is_young_from_claude_codes_write_not_from_the_put_back(self):
        self.cc_writes()
        self.hold('appeared')
        self.clock.advance(2.5)
        self.cc_removes()
        out = self.hold('gone')
        self.assertEqual(out['did'], 'held')
        self.assertAlmostEqual(out['look_in'], putback.HOLD_TIDY_S - 2.5 + putback.HOLD_LOOK_S, places=3)
        self.assertAlmostEqual(self.note()['wrote'], self.clock.wall() - 2.5, places=3)
        self.look(after=putback.HOLD_TIDY_S - 2.5 - 0.2, keychain=True)       # 0.2 s before the bytes are old enough
        self.assertTrue(self.ours())
        self.look(after=0.3, keychain=True)                   # 0.1 s after: it goes, sooner after the put-back
        self.assertIsNone(self.file_text())

    def test_a_time_a_little_past_the_clock_counts_as_now(self):
        self.kept_then_gone()
        self.clock.step(-2.0)                                 # the wall clock was set back two seconds
        self.look(after=0.1, keychain=True)
        self.assertTrue(self.ours())                          # not "old already": the item may still be deleted
        self.look(after=putback.HOLD_TIDY_S + 2.0, keychain=True)
        self.assertIsNone(self.file_text())

    def test_a_note_without_the_time_of_the_write_counts_from_the_put_back(self):
        self.kept_then_gone()
        note = self.note()
        del note['wrote']
        with open(self.ctx.path(putback.HOLD_FILE), 'w') as f:
            json.dump(note, f)
        self.look(after=putback.HOLD_TIDY_S - 0.5, keychain=True)
        self.assertTrue(self.ours())
        self.look(after=0.6, keychain=True)
        self.assertIsNone(self.file_text())


class TheNotes(Base):
    def test_the_notes_before_the_link_do_not_wait_for_the_disk(self):
        self.cc_writes()
        self.hold('appeared')
        self.cc_removes()
        order = []
        real_write, real_link = ccfiles.atomic_write, putback._link

        def write(path, data, mode=0o600, follow=True, sync=True):
            if os.path.basename(path) == putback.HOLD_FILE:
                order.append((json.loads(data)['state'], json.loads(data)['stamp'] is not None, sync))
            return real_write(path, data, mode=mode, follow=follow, sync=sync)

        def link(src, dst):
            order.append('link')
            real_link(src, dst)
        with mock.patch.object(ccfiles, 'atomic_write', write), mock.patch.object(putback, '_link', link):
            self.assertEqual(self.hold('gone')['did'], 'held')
        self.assertEqual(order, [('pending', False, False), ('pending', True, False), 'link', ('settled', True, True)])

    def test_a_pending_note_without_a_stamp_claims_no_file(self):
        # a process stopped after its first note and before it made the temporary file: Claude Code's file is its own
        self.cc_writes()
        self.hold('appeared')
        self.cc_removes()
        real = os.open

        def opened(path, flags, mode=0o777, **kw):
            if str(path).endswith('.hold'):
                raise Killed()
            return real(path, flags, mode, **kw)
        with mock.patch.object(putback.os, 'open', opened):
            with self.assertRaises(Killed):
                self.hold('gone')
        self.assertEqual((self.note()['state'], self.note()['stamp']), ('pending', None))
        theirs = self.cc_writes()
        putback.reset()
        self.assertIsNone(self.hold('start')['did'])
        self.look(after=putback.HOLD_PENDING_S, keychain=True)
        self.assertEqual(self.file_text(), theirs)
        self.assertIsNone(self.note())

    def test_a_file_claude_code_rewrote_in_place_after_a_stop_is_its_own(self):
        # stopped between the link and the settled note: the temporary name is still another name of the file. Claude
        # Code then writes the file in place (the same inode); its bytes are not ours, whatever that name says
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
        self.assertTrue(self.ours())
        ino = os.stat(self.cred).st_ino
        newer = json.dumps(dict(json.loads(self.rig.live_text()), discoveryState={'p9': 7}))
        with open(self.cred, 'r+') as f:                      # in place: the inode stays, the size and the time move
            f.truncate(0)
            f.write(newer)
        self.assertEqual(os.stat(self.cred).st_ino, ino)
        putback.reset()
        self.hold('start')
        self.look(after=putback.HOLD_TIDY_S + 1.0, keychain=True)
        self.assertEqual(self.file_text(), newer)
        self.assertEqual(self.leftovers(), [])


if __name__ == '__main__':
    unittest.main()
