import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# A put-back leaves a spare copy of the sign-in in Claude Code's plain login file. A Claude Code process trusts each
# read of its Keychain item for 30 s, a "no such item" answer too, and while it holds that answer it reads its login
# from the plain login file at every request (read in Claude Code's program text, 2.1.329 to 2.1.332). So once the
# sign-in is back in the item, the processes that asked while it was gone still need it in the file. With the sign-in
# hold's setting on, the put-back makes that file just before its write when none is there (holding exactly the text
# it writes, owner-only, create-only, known as the hold's), keeps the file that is there (Claude Code's, or one of
# ours) instead of removing it, and hands it to the hold, which takes it away by its rules. A write that does not land,
# or is taken back, takes away the file it made before Claude Code's save lock is let go. With the setting off, the
# put-back does what it did before.
# The account engine on its stand-ins (r17_engine_fakes) with a fake clock; made-up *.example accounts.
import json
import os
import unittest
from unittest import mock

from hold_fakes import MCP, Base, stamp_of
from switchboard.accounts import ccfiles, live, putback, switcher


class Lost(Base):
    def lose(self, item=None):
        """Claude Code lost its sign-in and no plain login file is there: the item deleted (item None) or written
        without the login (item: its text); the first look saw it."""
        if item is None:
            self.cc_deletes_item()
        else:
            self.rig.set_live(item)
        self.assertEqual(self.look(after=0.2, keychain=True).action, 'wait')

    def put_back(self, item=None):
        """... and the next look put the saved login back."""
        self.lose(item)
        res = self.look(after=putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, 'put_back', res.reason)
        return res

    def set_hold(self, on):
        with open(self.ctx.path('settings.json'), 'w') as f:
            json.dump({'auto_policy': {'hold_signin_file': on}}, f)

    def at_each_write(self, also=None):
        """A list that gets, at each write of Claude Code's item, whether a file of ours was in place; also(verb): more
        for the stand-in Keychain to do at each call."""
        seen = []

        def on_call(verb, service, account):
            if service != self.rig.service:
                return
            if verb == 'write':
                seen.append(self.ours())
            if also is not None:
                also(verb)
        self.rig.kc.on_call = on_call
        self.addCleanup(setattr, self.rig.kc, 'on_call', None)
        return seen

    def assert_nothing_left(self):
        self.assertIsNone(self.file_text())
        self.assertIsNone(self.note())
        self.assertEqual(self.leftovers(), [])


class APutBackLeavesASpare(Lost):
    def test_into_an_absent_item_it_leaves_the_text_it_wrote_known_as_the_holds(self):
        self.put_back()
        item = self.rig.live_text()
        self.assertEqual(self.file_text(), item)              # byte for byte what the item holds
        st = os.stat(self.cred)
        self.assertEqual(st.st_mode & 0o777, 0o600)
        self.assertTrue(self.ours())
        note = self.note()
        self.assertEqual((note['state'], note['path']), ('settled', self.cred))
        self.assertAlmostEqual(note['wrote'], self.clock.wall(), places=3)
        self.assertEqual(putback.load_record(self.ctx)['written']['file'], list(stamp_of(self.cred)))
        self.assertEqual(self.leftovers(), [])
        self.assert_no_secret_in(note)

    def test_it_is_in_place_before_the_item_is_written(self):
        # a process that asks for its login while the write runs finds it in the file
        self.lose()
        seen = []

        def on_call(verb, service, account):
            if verb == 'write' and service == self.rig.service:
                st = os.stat(self.cred) if os.path.exists(self.cred) else None
                seen.append((self.file_text(), st.st_mode & 0o777 if st else None, self.ours()))
        self.rig.kc.on_call = on_call
        try:
            self.assertEqual(self.look(after=putback.PUT_BACK_AFTER_S).action, 'put_back')
        finally:
            self.rig.kc.on_call = None
        self.assertEqual(seen, [(self.rig.live_text(), 0o600, True)])

    def test_over_an_item_without_a_login_it_leaves_one_too(self):
        self.put_back(item=json.dumps(MCP))
        self.assertEqual(json.loads(self.rig.live_text())['claudeAiOauth'],
                         json.loads(self.b_text)['claudeAiOauth'])
        self.assertEqual(self.file_text(), self.rig.live_text())
        self.assertTrue(self.ours())

    def test_the_sign_in_watch_records_it_after_the_look(self):
        self.put_back()
        out = self.hold('looked')
        self.assertEqual((out['did'], out['why']), ('spare', 'put_back'))
        self.assertIsNone(self.hold('looked')['did'])        # said once
        self.assert_no_secret_in(out)


class AFileThatIsThere(Lost):
    def test_claude_codes_file_beside_an_item_without_a_login_is_kept_not_removed(self):
        filed = self.cc_writes(self.b_text)                   # Claude Code's fallback, with the login
        self.rig.set_live(json.dumps(MCP))                    # and an item that holds none
        self.assertEqual(self.look(after=0.2, keychain=True).action, 'wait')
        res = self.look(after=putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, 'put_back', res.reason)
        item = self.rig.live_text()
        self.assertEqual(json.loads(item)['claudeAiOauth'], json.loads(filed)['claudeAiOauth'])
        self.assertEqual(self.file_text(), item)              # left as it is, or brought to the item's text
        self.assertTrue(self.ours())                          # handed to the hold: it takes it away later
        self.assertAlmostEqual(self.note()['wrote'], self.clock.wall(), places=3)
        self.look(after=putback.HOLD_TIDY_S + 0.1, keychain=True)
        self.assert_nothing_left()

    def test_a_file_of_ours_beside_an_item_without_a_login_is_kept(self):
        text, out = self.kept_then_gone(self.b_text)
        self.assertEqual(out['did'], 'held')                  # the hold put Claude Code's bytes back: a file of ours
        self.rig.set_live(json.dumps(MCP))
        self.assertEqual(self.look(after=0.2, keychain=True).action, 'wait')
        self.assertEqual(self.look(after=putback.PUT_BACK_AFTER_S).action, 'put_back')
        self.assertEqual(self.file_text(), self.rig.live_text())
        self.assertTrue(self.ours())
        self.assertAlmostEqual(self.note()['wrote'], self.clock.wall(), places=3)     # its time starts again

    def test_a_file_claude_code_writes_just_before_the_link_is_its_own_and_is_kept_as_one(self):
        self.lose()
        theirs = json.dumps(dict(MCP, discoveryState={'p1': 7}))
        real = putback._link

        def link(src, dst):
            with open(dst, 'w') as f:                         # Claude Code's own write lands first
                f.write(theirs)
            real(src, dst)
        with mock.patch.object(putback, '_link', link):
            self.assertEqual(self.look(after=putback.PUT_BACK_AFTER_S).action, 'put_back')
        self.assertEqual(self.file_text(), self.rig.live_text())   # brought to the item's text, as a promote does
        self.assertTrue(self.ours())
        self.assertEqual(self.leftovers(), [])


class TheHoldsRules(Lost):
    def test_it_is_taken_away_once_the_item_has_been_back_hold_tidy_s_and_not_before(self):
        self.put_back()
        self.look(after=putback.HOLD_TIDY_S - 0.5, keychain=True)
        self.assertTrue(self.ours())
        self.look(after=0.6, keychain=True)
        self.assert_nothing_left()
        self.assertIsNotNone(self.rig.live_text())

    def test_at_once_on_a_sign_outs_settings_step(self):
        self.put_back()
        self.clock.advance(1.0)
        self.settings_name(None)
        out = self.hold('settings')
        self.assertEqual((out['did'], out['why']), ('removed', 'nobody'))
        self.assert_nothing_left()

    def test_by_a_switch(self):
        self.put_back()
        self.assertTrue(self.ours())
        switcher.switch(self.ctx, 'uuid-a', kind='manual', source='cli', reason='')
        self.assert_nothing_left()
        self.assertIn('-uuid-a-s', json.loads(self.rig.live_text())['claudeAiOauth']['refreshToken'])

    def test_a_file_claude_code_wrote_since_is_left_to_it(self):
        self.put_back()
        self.assertTrue(self.ours())
        self.clock.advance(1.0)
        theirs = self.cc_writes(json.dumps(dict(json.loads(self.rig.live_text()), discoveryState={'p2': 3})))
        self.look(after=putback.HOLD_TIDY_S + 1.0, keychain=True)
        self.assertEqual(self.file_text(), theirs)
        self.assertIsNone(self.note())


class WithTheSettingOff(Lost):
    def setUp(self):
        super().setUp()
        self.set_hold(False)

    def test_nothing_is_created(self):
        self.put_back()
        self.assert_nothing_left()
        self.assertIsNone(putback.load_record(self.ctx)['written']['file'])

    def test_claude_codes_file_beside_an_item_without_a_login_is_removed_as_before(self):
        self.cc_writes(self.b_text)
        self.rig.set_live(json.dumps(MCP))
        self.assertEqual(self.look(after=0.2, keychain=True).action, 'wait')
        self.assertEqual(self.look(after=putback.PUT_BACK_AFTER_S).action, 'put_back')
        self.assert_nothing_left()


class NothingIsLeft(Lost):
    def test_when_the_note_cannot_be_written(self):
        self.lose()
        note = mock.Mock(side_effect=OSError('made-up'))
        with mock.patch.object(putback, '_hold_write_note', note):
            self.assertEqual(self.look(after=putback.PUT_BACK_AFTER_S).action, 'put_back')
        self.assertTrue(note.called)                          # a spare was asked for, and could not be noted
        self.assert_nothing_left()
        self.assertIsNotNone(self.rig.live_text())

    def test_when_every_write_is_refused(self):
        self.lose()
        seen = self.at_each_write()
        self.rig.kc.rule('write', service=self.rig.service, why='locked', times=-1)
        self.assertEqual(self.look(after=putback.PUT_BACK_AFTER_S).action, 'failed')
        self.assertGreater(len(seen), 1)                      # tried again, each time with a file made and taken away
        self.assertEqual(set(seen), {True})
        self.assert_nothing_left()

    def test_when_another_process_made_the_item_first(self):
        self.lose()
        b = self.b_text

        def also(verb):
            if verb == 'write' and self.rig.live_text() is None:
                self.rig.set_live(json.dumps(dict(json.loads(b), discoveryState={'p3': 1})))
        seen = self.at_each_write(also)
        self.look(after=putback.PUT_BACK_AFTER_S)
        self.assertEqual(seen, [True])
        self.assert_nothing_left()

    def test_when_the_write_does_not_read_back(self):
        self.lose()
        seen = self.at_each_write()
        self.rig.kc.rule('write', service=self.rig.service, why='silent', times=-1)
        self.assertEqual(self.look(after=putback.PUT_BACK_AFTER_S).action, 'failed')
        self.assertEqual(set(seen), {True})
        self.assert_nothing_left()

    def test_when_the_write_is_taken_back(self):
        # Claude Code was signing out: its settings name nobody by the time the write has read back
        self.lose()
        state = {'wrote': False, 'cleared': False}

        def also(verb):
            if verb == 'write':
                state['wrote'] = True
            elif verb == 'read' and state['wrote'] and not state['cleared']:
                state['cleared'] = True
                self.settings_name(None)
        seen = self.at_each_write(also)
        res = self.look(after=putback.PUT_BACK_AFTER_S)
        self.assertEqual(seen, [True])
        self.assertTrue(state['cleared'])
        self.assertEqual(res.reason, 'Claude Code was signing out')
        self.assertIsNone(self.rig.live_text())
        self.assert_nothing_left()

    def test_when_the_save_lock_is_no_longer_held_at_the_write(self):
        self.lose()
        seen = []

        def write_live(*a, **kw):
            seen.append(self.ours())
            raise ccfiles.LockBusy('made-up')
        with mock.patch.object(live, 'write_live', write_live):
            self.assertEqual(self.look(after=putback.PUT_BACK_AFTER_S).action, 'wait')
        self.assertEqual(seen, [True])
        self.assert_nothing_left()


if __name__ == '__main__':
    unittest.main()
