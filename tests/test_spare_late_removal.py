import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# A removal of Claude Code's plain login file later than the hold's HOLD_FOR_S is answered from the Keychain item. A
# Claude Code process whose save landed after it had read "no such item" removes that file, at any moment (read in
# Claude Code's program text), and the processes still holding "no such item" then have no login. The hold puts back
# bytes kept in memory only within HOLD_FOR_S of Claude Code's write; past that, while the item was known absent less
# than HOLD_TIDY_S ago (a fallback write the hold saw, a put-back, a promote, a look that read "no such item"), the
# sign-in watch makes a look at once, and that look, holding Claude Code's save lock, makes a spare copy of the
# sign-in from the item's exact text: only when the item holds a full login of the account the settings name, no
# switch is part-way, no sign-in is under way on the page and no file is there. The hold's own call stays free of
# Keychain calls and lock waits; a look that finds the save lock held asks for another look instead of waiting.
# The account engine on its stand-ins (r17_engine_fakes) with a fake clock, and the sign-in watch on a made-up world
# (signin_watch_fakes); made-up *.example accounts.
import json
import os
import unittest
from unittest import mock

from hold_fakes import MCP, Base
from signin_watch_fakes import T0_MONO, make
from switchboard.accounts import ccfiles, facts, live, putback, switcher, vault
from switchboard.accounts import signin_watch as sw


class LateRemoval(Base):
    def written(self, text=None):
        """Claude Code's fallback write after a refused save, seen by the hold: the item is known absent from now."""
        text = self.cc_writes(text)
        self.assertEqual(self.hold('appeared')['did'], 'kept')
        return text

    def removed(self, after):
        """... removed `after` seconds later by another Claude Code process. The hold's answer."""
        self.clock.advance(after)
        self.cc_removes()
        return self.hold('gone')

    def answered(self, after):
        """A removal `after` seconds after the write, the hold's answer, and the look the sign-in watch makes for it."""
        self.written()
        written_at = self.clock.wall()
        out = self.removed(after)
        res = self.look(after=0.1, login_file='gone')
        return out, res, written_at

    def set_hold(self, on):
        with open(self.ctx.path('settings.json'), 'w') as f:
            json.dump({'auto_policy': {'hold_signin_file': on}}, f)

    def account_of(self, uuid):
        return [k for k in self.rig.doc()['accounts'] if k.startswith(uuid)][0]


class AnsweredFromMemoryAsBefore(LateRemoval):
    def test_a_removal_two_seconds_after_the_write_is_answered_from_memory(self):
        text = self.written()
        out = self.removed(2.0)
        self.assertEqual(out['did'], 'held')
        self.assertNotIn('late', out)
        self.assertEqual(self.file_text(), text)              # Claude Code's own bytes, as before


class AnsweredFromTheItem(LateRemoval):
    def test_at_five_seconds(self):
        out, res, written_at = self.answered(5.0)
        self.assertEqual((out['did'], out.get('late'), out.get('look_in')), (None, True, 0.0))
        self.assertEqual(self.file_text(), self.rig.live_text())
        self.assertTrue(self.ours())
        self.assertEqual(os.stat(self.cred).st_mode & 0o777, 0o600)
        self.assertAlmostEqual(self.note()['wrote'], written_at, places=3)    # its time counts from the loss
        self.assertEqual(self.leftovers(), [])
        out = self.hold('looked')
        self.assertEqual((out['did'], out['why']), ('spare', 'gone'))
        self.assert_no_secret_in(out, self.note())

    def test_at_thirty_seconds_and_it_goes_when_the_loss_is_hold_tidy_s_old(self):
        out, res, written_at = self.answered(30.0)
        self.assertTrue(out.get('late'))
        self.assertEqual(self.file_text(), self.rig.live_text())
        self.look(after=putback.HOLD_TIDY_S - 30.0 - 0.1 - 0.5, keychain=True)
        self.assertTrue(self.ours())
        self.look(after=0.6, keychain=True)
        self.assertIsNone(self.file_text())
        self.assertIsNone(self.note())

    def test_not_at_forty_seconds(self):
        out, res, _ = self.answered(40.0)
        self.assertNotIn('late', out)
        self.assertIsNone(self.file_text())

    def test_the_spare_holds_the_items_text_when_its_login_was_renewed_since(self):
        written = self.written()
        self.clock.advance(2.0)
        renewed = self.rig.claude_renews()                    # one process renewed; its fresh read found the item
        self.assertTrue(self.removed(3.0).get('late'))
        self.look(after=0.1, login_file='gone')
        self.assertEqual(self.file_text(), renewed)           # the item's login, not the bytes Claude Code wrote
        self.assertNotEqual(json.loads(renewed)['claudeAiOauth'], json.loads(written)['claudeAiOauth'])

    def test_the_spare_a_put_back_left_is_made_again_when_a_process_removes_it(self):
        self.cc_deletes_item()
        self.assertEqual(self.look(after=0.2, keychain=True).action, 'wait')
        self.assertEqual(self.look(after=putback.PUT_BACK_AFTER_S).action, 'put_back')
        put_at = self.clock.wall()
        self.assertTrue(self.removed(10.0).get('late'))
        self.look(after=0.1, login_file='gone')
        self.assertEqual(self.file_text(), self.rig.live_text())
        self.assertAlmostEqual(self.note()['wrote'], put_at, places=3)

    def test_with_the_item_absent_the_put_back_decides_and_leaves_a_spare(self):
        self.written()
        self.cc_deletes_item()
        self.assertTrue(self.removed(5.0).get('late'))
        self.assertEqual(self.look(after=0.1, login_file='gone').action, 'wait')
        self.assertIsNone(self.file_text())                   # nothing to copy: the item is gone
        self.assertEqual(self.look(after=putback.PUT_BACK_AFTER_S).action, 'put_back')
        self.assertEqual(self.file_text(), self.rig.live_text())


class Never(LateRemoval):
    def test_when_the_settings_name_nobody(self):
        self.written()
        self.clock.advance(5.0)
        self.settings_name(None)
        self.hold('settings')
        self.cc_removes()
        self.assertTrue(self.hold('gone').get('late'))       # the look is owed; it finds the settings naming nobody
        self.look(after=0.1, login_file='gone', settings=True)
        self.assertIsNone(self.file_text())

    def test_when_the_item_holds_another_accounts_login(self):
        self.written()
        self.rig.set_live(self.rig.vault(self.account_of('uuid-a')))
        self.assertTrue(self.removed(5.0).get('late'))
        self.look(after=0.1, login_file='gone', keychain=True)
        self.assertIsNone(self.file_text())

    def test_while_a_switch_is_part_way(self):
        self.written()
        self.assertTrue(self.removed(5.0).get('late'))
        with open(switcher.journal_path(self.ctx), 'w') as f:
            f.write('{"step": "writing"}')
        self.look(after=0.1, login_file='gone')
        self.assertIsNone(self.file_text())

    def test_while_a_sign_in_is_under_way_on_the_page(self):
        self.written()
        self.assertTrue(self.removed(5.0).get('late'))
        os.makedirs(self.ctx.path(putback.SIGNINS, 'made-up-sign-in'))
        self.look(after=0.1, login_file='gone')
        self.assertIsNone(self.file_text())

    def test_with_the_setting_off(self):
        self.written()
        self.set_hold(False)
        self.assertNotIn('late', self.removed(5.0))
        self.look(after=0.1, login_file='gone')
        self.assertIsNone(self.file_text())

    def test_over_a_file_that_is_there(self):
        self.written()
        self.assertTrue(self.removed(5.0).get('late'))
        theirs = self.cc_writes(json.dumps(dict(MCP, discoveryState={'p6': 2})))
        self.look(after=0.1, login_file='gone')
        self.assertEqual(self.file_text(), theirs)


class NeverTakenOut(LateRemoval):
    def test_the_items_own_login_copied_into_the_file_is_never_taken_out_of_the_item(self):
        self.written()
        self.clock.advance(2.0)
        renewed = self.rig.claude_renews()
        self.assertTrue(self.removed(3.0).get('late'))
        self.look(after=0.1, login_file='gone')
        self.assertEqual(self.file_text(), renewed)
        self.settings_name(None)                              # settings naming nobody, the item kept
        self.hold('settings')
        self.look(after=0.5, settings=True)
        self.assertEqual(self.rig.live_text(), renewed)


class TheSettingsAreReadBeforeTheLock(LateRemoval):
    def test_while_the_save_lock_is_held_only_the_check_after_the_link_reads_the_settings(self):
        self.written()
        self.assertTrue(self.removed(5.0).get('late'))
        under = []
        real_settings, real_accounts = live.config_identity, vault.load

        def settings(*a, **kw):
            under.append(('settings', ccfiles.held_save_lock() is not None))
            return real_settings(*a, **kw)

        def accounts(*a, **kw):
            under.append(('accounts', ccfiles.held_save_lock() is not None))
            return real_accounts(*a, **kw)
        with mock.patch.object(live, 'config_identity', settings), mock.patch.object(vault, 'load', accounts):
            self.look(after=0.1, login_file='gone')
        self.assertEqual(self.file_text(), self.rig.live_text())
        self.assertEqual([what for what, held in under if held], ['settings'])     # the guard after the link


class NothingWaits(LateRemoval):
    def test_the_holds_call_makes_no_keychain_call(self):
        self.written()
        self.assertTrue(self.removed(5.0).get('late'))
        self.assert_no_keychain_or_network()

    def test_with_claude_codes_save_lock_held_the_look_asks_for_another_and_never_waits(self):
        self.written()
        self.assertTrue(self.removed(5.0).get('late'))
        lock = facts.storage_lock(self.rig.env, self.rig.home)
        os.mkdir(lock)
        try:
            self.clock.advance(0.1)
            before = self.clock.mono()
            res = self.look(login_file='gone')
            self.assertEqual(self.clock.mono(), before)       # not one fake second waited for the lock
        finally:
            os.rmdir(lock)
        self.assertIsNone(self.file_text())
        self.assertLessEqual(res.next_s, putback.SPARE_AGAIN_S)
        self.look(after=putback.SPARE_AGAIN_S)
        self.assertEqual(self.file_text(), self.rig.live_text())


class TheWatchLooksAtOnce(unittest.TestCase):
    def test_a_late_removal_is_looked_at_with_no_settle(self):
        # an earlier answer asked for a look much later (a file of ours still young): the removal's look still comes
        # as soon as the pace allows, as for a login file that appeared
        T = T0_MONO
        w, sim, looks, beats, lines, logs = make()
        w.hold = lambda event: {'did': None, 'look_in': 30.0} if event == 'looked' else \
            {'did': None, 'late': True, 'look_in': 0.0} if event == 'gone' else {'did': None}
        sim.change(T + 0.5, login_file='gone')
        w.run(until=T + 5.0)
        starts = [t for t, _ in looks.calls]
        self.assertAlmostEqual(starts[1], T + 0.5)            # not after a settle and MIN_GAP_S (T + 1.0)
        self.assertEqual(looks.calls[1][1]['login_file'], 'gone')
        self.assertLess(starts[1] - starts[0], sw.MIN_GAP_S)


if __name__ == '__main__':
    unittest.main()
