import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# A sign-out chosen in the seconds after one of Claude Code's refused saves removes the plain login file while the
# settings still name the account, and the sign-in hold puts the file back until the settings change a moment later.
# A Claude Code process that saves in that moment reads the login from the file and saves it into the Keychain item:
# a login behind a sign-out, there because of a file of ours. The next look of the sign-in watch takes it out again
# (putback._maybe_take_out), within every one of these bounds: the hold put that file in place less than a minute ago;
# the settings name nobody; the settings file is the lived-in one; the item still holds that very account login. The
# item keeps every other part and loses only its account login, by an ordinary write, never a delete.
# The account engine on its stand-ins with a fake clock (the helpers of test_hold_sign_out). Made-up accounts.
import json
import unittest

import test_hold_sign_out as episode
from hold_fakes import B, MiniWatch
from switchboard.accounts import putback

USER_ID = 'made-up-settings-id-' + '3e' * 22


class Base(episode.EpisodeBase):
    def setUp(self):
        super().setUp()
        cfg = self.rig.config()
        cfg['userID'] = USER_ID                                # a lived-in settings file has an id of its own
        cfg.update(('madeUpSetting%02d' % i, i) for i in range(40))
        with open(self.rig.config_path(), 'w') as f:
            json.dump(cfg, f, indent=2)
        self.w = MiniWatch(self.rig, self.clock)

    def a_save_in_the_gap(self):
        """A Claude Code process saves now: its fresh read found no item and took the login from the plain login file;
        its save lands (the whole object, with its own change), and it removes the plain login file as a landed save
        does. Returns what it saved."""
        self.assertEqual(self.gap(), ('file', 'uuid-b'))
        blob = json.loads(self.file_text())
        blob['discoveryState'] = {'a-saver-in-the-gap': 2}
        saved = json.dumps(blob, separators=(',', ':'))
        self.rig.set_live(saved)
        self.cc_removes()
        self.w.notice()
        return saved

    def signs_out_with_a_save_in_the_gap(self, delay=0.3):
        """The fallback write, then within the hold's seconds the sign-out's first steps (the item deleted, the file
        removed, the settings still naming the account), a save in that gap, and the settings' change after delay."""
        self.fallback()
        self.clock.advance(0.5)
        self.rig.server.revoke('uuid-b')
        self.cc_deletes_item()
        self.w.notice()
        self.cc_removes()
        self.w.notice()                                        # the hold puts the file back: the settings name B
        saved = self.a_save_in_the_gap()
        self.clock.advance(delay)
        self.settings_name(None)
        self.w.notice()
        return saved

    def deletes(self):
        return [c for c in self.rig.kc.calls if c[0] == 'delete' and c[1] == self.rig.service]

    def oauth(self):
        text = self.rig.live_text()
        return json.loads(text).get('claudeAiOauth') if text else None


class TheLoginIsTakenOutAgain(Base):
    def the_item_loses_only_its_account_login(self, delay):
        saved = self.signs_out_with_a_save_in_the_gap(delay)
        self.assertEqual(self.oauth(), json.loads(saved)['claudeAiOauth'])      # what the saver left
        self.w.look(0.3)
        now = json.loads(self.rig.live_text())
        self.assertNotIn('claudeAiOauth', now)
        self.assertEqual(now, dict((k, v) for k, v in json.loads(saved).items() if k != 'claudeAiOauth'))
        self.assertEqual(self.deletes(), [])                                    # an ordinary write, never a delete
        self.assertIsNone(self.file_text())
        said = [t for t in self.rig.logs if t == putback.HOLD_TAKEN_OUT_LINE]
        self.assertEqual(len(said), 1, self.rig.logs[-4:])
        self.assert_no_secret_in(said)
        for _ in range(5):                                                      # and nothing more after it
            self.w.look(1.0)
        self.assertEqual(json.loads(self.rig.live_text()), now)

    def test_the_settings_change_5_ms_after_the_save(self):
        self.the_item_loses_only_its_account_login(0.005)

    def test_the_settings_change_300_ms_after_the_save(self):
        self.the_item_loses_only_its_account_login(0.3)

    def test_the_settings_change_2_s_after_the_save(self):
        self.the_item_loses_only_its_account_login(2.0)

    def test_a_save_that_changed_nothing_else_is_taken_out_too(self):
        self.fallback()
        self.clock.advance(0.5)
        self.cc_deletes_item()
        self.w.notice()
        self.cc_removes()
        self.w.notice()
        self.rig.set_live(self.file_text())                    # byte for byte what the file held
        self.cc_removes()
        self.w.notice()
        self.settings_name(None)
        self.w.notice()
        self.w.look(0.3)
        self.assertIsNone(self.oauth())
        self.assertEqual(self.deletes(), [])

    def test_a_write_that_does_not_land_is_tried_again_at_the_next_look(self):
        self.signs_out_with_a_save_in_the_gap()
        self.rig.kc.rule('write', service=self.rig.service, why='locked', times=1)
        res = self.w.look(0.3)
        self.assertEqual(res.action, 'wait', res.reason)
        self.assertIsNotNone(self.oauth())
        self.w.look(1.0)
        self.assertIsNone(self.oauth())


class EachBoundAloneLeavesTheItem(Base):
    def looks(self, n=6, step=1.0):
        for _ in range(n):
            self.w.look(step)

    def test_while_the_settings_name_the_account(self):
        # the fault itself, with no sign-out: a save reads the login from the hold's file, which is what the hold is for
        self.fallback()
        self.clock.advance(0.2)
        self.cc_deletes_item()
        self.w.notice()
        self.cc_removes()
        self.w.notice()
        saved = self.a_save_in_the_gap()
        self.looks()
        self.assertEqual(self.oauth(), json.loads(saved)['claudeAiOauth'])
        self.assertEqual([t for t in self.rig.logs if t == putback.HOLD_TAKEN_OUT_LINE], [])

    def test_a_login_that_is_not_the_one_the_file_held(self):
        self.signs_out_with_a_save_in_the_gap()
        fresh = self.rig.server.add(*B)                        # she signed in again at once: another login
        self.rig.set_live(fresh)
        self.looks()
        self.assertEqual(self.rig.live_text(), fresh)

    def test_a_settings_file_made_anew(self):
        saved = self.signs_out_with_a_save_in_the_gap()
        with open(self.rig.config_path(), 'w') as f:           # not a sign-out: Claude Code made its settings anew
            json.dump({'numStartups': 1, 'userID': 'made-up-settings-id-' + '9d' * 22}, f)
        self.w.notice()
        self.looks()
        self.assertEqual(self.oauth(), json.loads(saved)['claudeAiOauth'])

    def test_only_within_a_minute_of_the_file(self):
        self.fallback()
        self.clock.advance(0.5)
        self.cc_deletes_item()
        self.w.notice()
        self.cc_removes()
        self.w.notice()
        saved = self.a_save_in_the_gap()
        self.clock.advance(putback.TAKE_BACK_S)                # the settings change a minute later: not this episode
        self.settings_name(None)
        self.w.notice()
        self.looks(3)
        self.assertEqual(self.oauth(), json.loads(saved)['claudeAiOauth'])

    def test_only_the_process_that_put_the_file_back_knows(self):
        saved = self.signs_out_with_a_save_in_the_gap()
        putback.reset()                                        # another process, or the watch started again
        self.looks(3)
        self.assertEqual(self.oauth(), json.loads(saved)['claudeAiOauth'])

    def test_what_is_kept_of_the_file_is_hashes_and_numbers(self):
        self.signs_out_with_a_save_in_the_gap()
        gave = putback._HOLD_GAVE[self.ctx.data_dir]
        self.assertEqual(sorted(gave), ['at', 'login_sha', 'settings_keys', 'settings_size', 'settings_user', 'sha'])
        self.assert_no_secret_in(gave)
        self.assertNotIn(USER_ID, json.dumps(gave))


if __name__ == '__main__':
    unittest.main()
