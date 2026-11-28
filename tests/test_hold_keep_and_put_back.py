import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The sign-in hold keeps what Claude Code's fallback wrote in its plain login file, for a few seconds and in memory
# only, and puts exactly those bytes back the moment the file goes (putback.hold_seen, reached through
# api.watch_hold as the sign-in watch calls it). Each bound on putting a file back has a test that fails for its own
# reason when that bound is taken out; so do the race with a file that appears before the link, the file's mode from
# its first byte, the 256 KB limit on reading, a file that is not a full login, the setting that turns the hold off,
# and a process stopped between the pending note and the link or between the link and the settled note.
# The account engine on its stand-ins (r17_engine_fakes) with a fake clock; made-up *.example accounts; every plain
# login file lives in the test's throwaway home.
import builtins
import json
import os
import unittest
from unittest import mock

from hold_fakes import B, MCP, Base, Killed, identity_block
from switchboard.accounts import api, putback, switcher
from switchboard.accounts import signin_watch as sw


class TheHoldKeeps(Base):
    def test_a_full_login_claude_code_wrote_is_kept_in_memory_only(self):
        text = self.cc_writes()
        out = self.hold('appeared')
        self.assertEqual(out['did'], 'kept')
        self.assertEqual(putback._HOLDER[self.ctx.data_dir]['text'], text)
        self.assertIsNone(self.note())                       # nothing on disk until a file is put back
        for name in os.listdir(self.ctx.data_dir):
            if os.path.isfile(self.ctx.path(name)):
                with open(self.ctx.path(name), 'rb') as f:
                    self.assertNotIn(b'sk-ant-', f.read(), name)
        self.assert_no_secret_in(out, self.rig.logs, self.rig.events)
        self.assert_no_keychain_or_network()

    def test_a_file_that_is_not_a_full_login_empties_the_holder(self):
        self.cc_writes()
        self.assertEqual(self.hold('appeared')['did'], 'kept')
        self.clock.advance(0.1)
        # another process, whose save was refused after it read "no login", writes its object without the login
        self.cc_writes(json.dumps(dict(MCP, discoveryState={'p3': 1})))
        self.assertIsNone(self.hold('changed')['did'])
        self.assertNotIn('text', putback._HOLDER[self.ctx.data_dir])
        self.cc_removes()
        self.assertIsNone(self.hold('gone')['did'])
        self.assertIsNone(self.file_text())

    def test_a_login_the_saved_accounts_prove_to_be_another_accounts_is_never_kept(self):
        # a process that read A's login before a switch to B wrote it after: put back, it would run Claude Code on A
        self.cc_writes(self.rig.vault('uuid-a'))
        self.assertIsNone(self.hold('appeared')['did'])
        self.assertNotIn('text', putback._HOLDER[self.ctx.data_dir])
        self.cc_removes()
        self.assertIsNone(self.hold('gone')['did'])
        self.assertIsNone(self.file_text())
        self.assert_no_keychain_or_network()

    def test_a_sign_in_the_saved_accounts_do_not_know_is_kept(self):
        fresh = self.rig.server.add(*B)                       # a sign-in inside Claude Code a moment ago
        self.cc_writes(fresh)
        self.assertEqual(self.hold('appeared')['did'], 'kept')
        self.cc_removes()
        self.assertEqual(self.hold('gone')['did'], 'held')
        self.assertEqual(self.file_text(), fresh)

    def test_a_login_with_one_token_only_is_never_kept(self):
        blob = json.loads(self.rig.live_text())
        del blob['claudeAiOauth']['refreshToken']
        self.cc_writes(json.dumps(blob))
        self.assertIsNone(self.hold('appeared')['did'])
        self.cc_removes()
        self.assertIsNone(self.hold('gone')['did'])
        self.assertIsNone(self.file_text())

    def test_nothing_over_256_kb_is_read(self):
        blob = json.loads(self.rig.live_text())
        blob['pad'] = 'x' * putback.HOLD_READ_MAX
        self.cc_writes(json.dumps(blob))
        self.assertGreater(os.path.getsize(self.cred), putback.HOLD_READ_MAX)
        opened = []
        real = builtins.open

        def spy(path, *a, **k):
            opened.append(os.fspath(path) if not isinstance(path, int) else path)
            return real(path, *a, **k)
        with mock.patch('builtins.open', spy):
            out = self.hold('appeared')
        self.assertIsNone(out['did'])
        self.assertNotIn(self.cred, opened)
        # the reader itself never takes more than the limit and one byte, even from a file that grew
        reads = []

        class Small(object):
            def __init__(self, f):
                self.f = f

            def __enter__(self):
                return self

            def __exit__(self, *a):
                self.f.close()

            def read(self, n=-1):
                reads.append(n)
                return self.f.read(n)
        with mock.patch('builtins.open', lambda p, *a, **k: Small(real(p, *a, **k))):
            self.assertIsNone(putback._hold_read(self.cred))
        self.assertEqual(reads, [putback.HOLD_READ_MAX + 1])

    def test_a_login_just_under_256_kb_is_kept(self):
        blob = json.loads(self.rig.live_text())
        blob['pad'] = 'x' * (putback.HOLD_READ_MAX - len(self.rig.live_text()) - 20)
        self.cc_writes(json.dumps(blob, separators=(',', ':')))
        self.assertLessEqual(os.path.getsize(self.cred), putback.HOLD_READ_MAX)
        self.assertEqual(self.hold('appeared')['did'], 'kept')

    def test_a_write_three_seconds_old_is_not_kept(self):
        self.cc_writes(age=putback.HOLD_FOR_S + 0.01)
        self.assertIsNone(self.hold('appeared')['did'])
        self.cc_removes()
        self.assertIsNone(self.hold('gone')['did'])
        self.assertIsNone(self.file_text())

    def test_a_write_seen_late_counts_its_seconds_from_the_write_itself(self):
        text = self.cc_writes(age=2.5)                       # the watch learned of it late (its 5 s sweep)
        self.assertEqual(self.hold('appeared')['did'], 'kept')
        self.clock.advance(0.4)
        self.cc_removes()
        self.assertEqual(self.hold('gone')['did'], 'held')   # 2.9 s after the write
        self.assertEqual(self.file_text(), text)
        os.unlink(self.cred)
        self.clock.advance(0.2)                              # 3.1 s after it
        self.assertIsNone(self.hold('gone')['did'])
        self.assertIsNone(self.file_text())

    def test_nothing_is_kept_while_the_settings_name_nobody(self):
        self.rig.set_config(None)
        self.cc_writes()
        self.assertIsNone(self.hold('appeared')['did'])
        self.cc_removes()
        self.assertIsNone(self.hold('gone')['did'])
        self.assertIsNone(self.file_text())

    def test_the_file_the_hold_put_back_is_not_kept_again_and_the_count_goes_on(self):
        text, out = self.kept_then_gone()
        self.assertEqual(out['did'], 'held')
        self.assertEqual(out['made'], 1)
        again = self.hold('appeared')                       # the watch sees the hold's own link
        self.assertTrue(again['ours'])
        self.assertIsNone(again['did'])
        self.assertEqual(again['made'], 1)                  # not a new write of Claude Code's: no new count


class APutBack(Base):
    def test_it_puts_back_exactly_what_claude_code_wrote(self):
        text, out = self.kept_then_gone()
        self.assertEqual(out['did'], 'held')
        self.assertEqual(self.file_text(), text)
        st = os.stat(self.cred)
        self.assertEqual(st.st_mode & 0o777, 0o600)
        note = self.note()
        self.assertEqual(note['state'], 'settled')
        self.assertEqual(note['stamp'], [st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size])
        self.assertEqual(note['path'], self.cred)
        self.assertEqual(sorted(note), ['at', 'path', 'stamp', 'state', 'tmp', 'wrote'])
        self.assertAlmostEqual(note['wrote'], self.clock.wall() - 0.18, places=3)        # when Claude Code wrote it
        self.assertEqual(self.leftovers(), [])
        self.assert_no_secret_in(out, note, self.rig.logs, self.rig.events)
        self.assert_no_keychain_or_network()

    def test_1_not_once_three_seconds_have_passed(self):
        self.cc_writes()
        self.hold('appeared')
        self.clock.advance(putback.HOLD_FOR_S + 0.01)
        self.cc_removes()
        self.assertIsNone(self.hold('gone')['did'])
        self.assertIsNone(self.file_text())

    def test_1_just_under_three_seconds_it_is_put_back(self):
        text = self.cc_writes()
        self.hold('appeared')
        self.clock.advance(putback.HOLD_FOR_S - 0.1)
        self.cc_removes()
        self.assertEqual(self.hold('gone')['did'], 'held')
        self.assertEqual(self.file_text(), text)

    def test_1_not_after_a_sleep_of_the_mac(self):
        self.cc_writes()
        self.hold('appeared')
        self.clock.slept(600.0)                              # the awake clock did not move
        self.cc_removes()
        self.assertIsNone(self.hold('gone')['did'])
        self.assertIsNone(self.file_text())

    def test_2_at_most_five_times_for_one_write_of_claude_codes(self):
        self.cc_writes()
        self.hold('appeared')
        for n in range(putback.HOLD_MAX):
            self.clock.advance(0.1)
            if os.path.exists(self.cred):
                self.cc_removes()
            self.assertEqual(self.hold('gone')['did'], 'held', n)
        self.cc_removes()
        self.assertIsNone(self.hold('gone')['did'])
        self.assertIsNone(self.file_text())
        self.cc_writes()                                     # a new write of Claude Code's starts the count again
        self.assertEqual(self.hold('appeared')['did'], 'kept')
        self.cc_removes()
        self.assertEqual(self.hold('gone')['did'], 'held')

    def test_3_not_once_the_settings_name_nobody(self):
        self.cc_writes()
        self.hold('appeared')
        self.cc_removes()
        self.rig.set_config(None)
        self.assertIsNone(self.hold('gone')['did'])
        self.assertIsNone(self.file_text())

    def test_3_not_once_the_settings_name_another_account(self):
        self.cc_writes()
        self.hold('appeared')
        self.cc_removes()
        self.rig.set_config(identity_block('uuid-a', 'ana@work.example', 'org-a'))
        self.assertIsNone(self.hold('gone')['did'])
        self.assertIsNone(self.file_text())

    def test_3_not_once_the_settings_name_another_organization(self):
        self.cc_writes()
        self.hold('appeared')
        self.cc_removes()
        self.rig.set_config(identity_block('uuid-b', 'ana@home.example', 'org-other'))
        self.assertIsNone(self.hold('gone')['did'])
        self.assertIsNone(self.file_text())

    def test_3_settings_changed_while_the_file_was_put_in_place_get_no_file(self):
        self.cc_writes()
        self.hold('appeared')
        self.cc_removes()
        real = os.link

        def link(src, dst):
            real(src, dst)
            self.rig.set_config(None)                        # the sign-out cleared its settings in that moment
        with mock.patch.object(putback, '_link', link):
            out = self.hold('gone')
        self.assertEqual(out['did'], 'removed')
        self.assertIsNone(self.file_text())
        self.assertIsNone(self.note())
        self.assertEqual(self.leftovers(), [])

    def test_4_not_while_a_switch_is_part_way(self):
        self.cc_writes()
        self.hold('appeared')
        self.cc_removes()
        with open(switcher.journal_path(self.ctx), 'w') as f:
            f.write('{"step": "writing"}')
        self.assertIsNone(self.hold('gone')['did'])
        self.assertIsNone(self.file_text())

    def test_4_not_while_a_sign_in_is_under_way_on_the_page(self):
        self.cc_writes()
        self.hold('appeared')
        self.cc_removes()
        os.makedirs(self.ctx.path(putback.SIGNINS, 'made-up-sign-in'))
        self.assertIsNone(self.hold('gone')['did'])
        self.assertIsNone(self.file_text())

    def test_4_not_with_the_setting_off(self):
        self.cc_writes()
        self.hold('appeared')
        self.cc_removes()
        with open(self.ctx.path('settings.json'), 'w') as f:
            json.dump({'auto_policy': {'hold_signin_file': False}}, f)
        self.assertIsNone(self.hold('gone')['did'])
        self.assertIsNone(self.file_text())

    def test_5_never_over_a_file_that_is_there(self):
        self.cc_writes()
        self.hold('appeared')
        self.cc_removes()
        theirs = self.cc_writes(json.dumps(dict(MCP, discoveryState={'p4': 2})))   # written again meanwhile
        before = os.stat(self.cred)
        self.assertIsNone(self.hold('gone')['did'])
        self.assertEqual(self.file_text(), theirs)
        self.assertEqual(os.stat(self.cred).st_ino, before.st_ino)

    def test_5_a_file_that_appears_between_the_check_and_the_link_is_left_untouched(self):
        self.cc_writes()
        self.hold('appeared')
        self.cc_removes()
        real = os.link
        theirs = json.dumps(dict(MCP, discoveryState={'p5': 3}))

        def link(src, dst):
            with open(dst, 'w') as f:                        # Claude Code's own write lands first
                f.write(theirs)
            real(src, dst)
        with mock.patch.object(putback, '_link', link):
            out = self.hold('gone')
        self.assertIsNone(out['did'])
        self.assertEqual(out['failed'], 0)                   # not a failure: the file is Claude Code's
        self.assertEqual(self.file_text(), theirs)
        self.assertIsNone(self.note())
        self.assertEqual(self.leftovers(), [])

    def test_the_temporary_file_is_owner_only_from_its_first_byte(self):
        self.cc_writes()
        text = self.file_text()
        self.hold('appeared')
        self.cc_removes()
        real, real_open = os.link, os.open
        seen = {}

        def link(src, dst):
            st = os.stat(src)
            seen['mode'] = st.st_mode & 0o777
            with open(src) as f:
                seen['text'] = f.read()
            seen['note'] = self.note()
            real(src, dst)

        def opened(path, flags, mode=0o777, **kw):
            fd = real_open(path, flags, mode, **kw)
            if str(path).endswith('.hold'):
                st = os.fstat(fd)
                seen['created'] = (st.st_mode & 0o777, st.st_size)      # before its first byte
            return fd
        old = os.umask(0)                                    # the mode comes from the file's creation, not the umask
        try:
            with mock.patch.object(putback, '_link', link), mock.patch.object(putback.os, 'open', opened):
                self.assertEqual(self.hold('gone')['did'], 'held')
        finally:
            os.umask(old)
        self.assertEqual(seen['created'], (0o600, 0))
        self.assertEqual(seen['mode'], 0o600)
        self.assertEqual(seen['text'], text)
        self.assertEqual(seen['note']['state'], 'pending')   # the note was there before the link, and already
        st = os.stat(self.cred)                              # holds the stamp the file put in place has
        self.assertEqual(seen['note']['stamp'], [st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size])
        self.assertTrue(seen['note']['tmp'].endswith('.hold'))


class TheSetting(Base):
    def set_hold(self, on):
        with open(self.ctx.path('settings.json'), 'w') as f:
            json.dump({'auto_policy': {'hold_signin_file': on, 'stay_signed_in': True}}, f)

    def test_off_nothing_is_read_kept_or_put_back(self):
        self.set_hold(False)
        self.cc_writes()
        real = putback._hold_read
        reads = []
        with mock.patch.object(putback, '_hold_read', lambda p: reads.append(p) or real(p)):
            self.assertIsNone(self.hold('appeared')['did'])
        self.assertEqual(reads, [])
        self.cc_removes()
        self.assertIsNone(self.hold('gone')['did'])
        self.assertIsNone(self.file_text())
        self.assertIsNone(self.note())

    def test_missing_means_on_and_it_is_read_again_only_when_the_file_changes(self):
        self.assertTrue(putback.hold_on(self.ctx))           # no settings file at all
        with open(self.ctx.path('settings.json'), 'w') as f:
            json.dump({'auto': True, 'auto_policy': {'stay_signed_in': False}}, f)
        self.assertTrue(putback.hold_on(self.ctx))           # the key missing
        real = putback.ccfiles.read_json
        reads = []
        with mock.patch.object(putback.ccfiles, 'read_json',
                               lambda p: (reads.append(p) if p.endswith('settings.json') else None) or real(p)):
            for _ in range(5):
                self.assertTrue(putback.hold_on(self.ctx))
            self.assertEqual(reads, [])                      # unchanged: answered from the stamp
            self.set_hold(False)
            self.assertFalse(putback.hold_on(self.ctx))
            self.assertEqual(len(reads), 1)

    def test_turned_off_after_a_write_was_kept_nothing_is_put_back(self):
        self.cc_writes()
        self.assertEqual(self.hold('appeared')['did'], 'kept')
        self.set_hold(False)
        self.cc_removes()
        self.assertIsNone(self.hold('gone')['did'])
        self.assertIsNone(self.file_text())

    def test_off_a_file_of_ours_from_before_is_still_taken_away(self):
        self.kept_then_gone()
        self.assertIsNotNone(self.file_text())
        self.set_hold(False)
        self.rig.set_config(None)                            # a sign-out
        self.assertEqual(self.hold('settings')['did'], 'removed')
        self.assertIsNone(self.file_text())


class AProcessStoppedPartWay(Base):
    def restart(self):
        """A new watch process: nothing in memory; its start, then its first look."""
        putback.reset()
        out = self.hold('start')
        api.watch_look(dict(keychain=False, login_file=None, settings=False, tick=True, login_file_written=False))
        return out

    def test_stopped_between_the_pending_note_and_the_link_leaves_nothing_after_the_next_start(self):
        self.cc_writes()
        self.hold('appeared')
        self.cc_removes()

        def link(src, dst):
            raise Killed()
        with mock.patch.object(putback, '_link', link):
            with self.assertRaises(Killed):
                self.hold('gone')
        self.assertEqual(self.note()['state'], 'pending')
        self.assertEqual(len(self.leftovers()), 1)           # the temporary file, holding the login
        self.assertIsNone(self.file_text())
        self.restart()
        self.assertEqual(self.leftovers(), [])
        self.assertIsNone(self.note())
        self.assertIsNone(self.file_text())

    def stop_after_the_link(self):
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
        self.assertEqual(self.note()['state'], 'pending')
        self.assertEqual(len(self.leftovers()), 1)
        self.assertIsNotNone(self.file_text())               # in place, known only through the temporary file's inode
        tmp = os.path.join(os.path.dirname(self.cred), self.leftovers()[0])
        self.assertEqual(os.stat(tmp).st_ino, os.stat(self.cred).st_ino)

    def test_stopped_between_the_link_and_the_settled_note_leaves_nothing_after_the_next_start(self):
        self.stop_after_the_link()
        self.restart()
        self.clock.advance(putback.HOLD_TIDY_S)              # the item holds the login: once the file's bytes are old
        api.watch_look(dict(sw.no_hint(), tick=True))        # enough, a look takes it away
        self.assertIsNone(self.file_text())
        self.assertEqual(self.leftovers(), [])
        self.assertIsNone(self.note())

    def test_stopped_after_the_link_while_the_item_is_gone_the_file_is_known_as_ours(self):
        self.stop_after_the_link()
        self.rig.sec.drop(self.rig.service, self.rig.account)  # Claude Code's own delete came
        putback.reset()
        self.assertIsNone(self.hold('start')['did'])         # the named account's own full login: it stays
        st = os.stat(self.cred)
        note = self.note()
        self.assertEqual(note['state'], 'settled')
        self.assertEqual(note['stamp'], [st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size])
        self.assertEqual(self.leftovers(), [])

    def test_a_pending_note_of_a_stopped_process_is_left_alone_by_other_processes_only_for_a_while(self):
        self.cc_writes()
        self.hold('appeared')
        self.cc_removes()
        with mock.patch.object(putback, '_link', mock.Mock(side_effect=Killed())):
            with self.assertRaises(Killed):
                self.hold('gone')
        putback.reset()
        putback.hold_tidy(self.ctx)                          # another process: it may be putting a file in place now
        self.assertIsNotNone(self.note())
        self.clock.advance(putback.HOLD_PENDING_S)
        putback.hold_tidy(self.ctx)
        self.assertIsNone(self.note())
        self.assertEqual(self.leftovers(), [])


if __name__ == '__main__':
    unittest.main()
