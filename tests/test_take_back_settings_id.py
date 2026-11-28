import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The take-back's fifth bound, by the settings file's own id. A write of ours is taken back only when Claude Code's
# settings file is the lived-in one, not one Claude Code made anew. Claude Code gives a settings file an id (`userID`)
# the first time it needs one; its sign-out (read in the program text of 2.1.332) clears the account and about twenty
# caches kept for that account, kilobytes in a lived-in file, and keeps the id. So a sign-out can shrink the file by
# far more than a made-anew check by size allows, and the id tells the two apart: the same id is the lived-in file,
# another id or none is a file made anew; so is one that lost more keys than a sign-out can clear, whatever its id. A
# settings file that had no id at our write is judged by size as before.
# The account engine on its stand-ins with a fake clock (the helpers of test_take_back_bounds). Made-up accounts.
import json
import os
import unittest

import test_take_back_bounds as bounds
from fakes_accounts import identity_block
from switchboard.accounts import putback

USER_ID = 'made-up-settings-id-' + '5a' * 22            # as long as Claude Code's: 64 characters
OTHER_ID = 'made-up-settings-id-' + '7c' * 22
# What a sign-out clears besides the account (some of the names read in Claude Code's program text): caches kept for
# the signed-in account. Here they come to about 15 KB, more than putback.TAKE_BACK_SHRINK.
CACHES = {'clientDataCacheSlots': {'made-up': 'c' * 5000}, 'cachedUsageUtilization': {'made-up': 'u' * 4000},
          'additionalModelCostsCache': {'made-up': 'm' * 4000}, 'cachedArtifactRoster': {'made-up': 'r' * 2000}}
ANEW_ID = "Claude Code's settings file is not the one from a minute ago; Switchboard left its write as it is"


class Base(bounds.Base):
    ID = USER_ID

    def setUp(self):
        super().setUp()
        other = dict(bounds.LIVED_IN, **CACHES)
        other.update(('madeUpSetting%02d' % i, i) for i in range(40))       # a lived-in file holds about a hundred
        if self.ID is not None:
            other['userID'] = self.ID
        self.rig.set_config(identity_block('uuid-b', 'ana@home.example', 'org-b'), **other)
        self.size_before = os.path.getsize(self.rig.config_path())

    def sign_out_settings(self):
        """What Claude Code's sign-out does to its settings file: the account and the caches kept for it go, every
        other key stays (its own id too)."""
        obj = self.rig.config()
        for key in ('oauthAccount',) + tuple(CACHES):
            obj.pop(key, None)
        with open(self.rig.config_path(), 'w') as f:
            json.dump(obj, f, indent=2)
        self.assertLess(os.path.getsize(self.rig.config_path()), self.size_before - putback.TAKE_BACK_SHRINK)

    def made_anew(self, user_id=None, big=False):
        """Claude Code made its settings file anew: it names nobody, and holds no id or a new one. big: as large as
        the lived-in file was (caches a new file fills again), so its size says nothing."""
        obj = {'numStartups': 1, 'installMethod': 'made-up'}
        if user_id is not None:
            obj['userID'] = user_id
        if big:
            obj.update(bounds.LIVED_IN, **CACHES)
            obj.update(('madeUpSetting%02d' % i, i) for i in range(40))
        with open(self.rig.config_path(), 'w') as f:
            json.dump(obj, f, indent=2)

    def note(self):
        return putback.load_record(self.ctx)['written']


class ASignOutThatClearsKilobytes(Base):
    def test_a_write_into_no_item_is_taken_back(self):
        self.landed(created=True)
        self.assertEqual(self.note()['settings_user'], putback._sha(USER_ID)[:16])
        self.clock.advance(2.0)
        self.sign_out_settings()
        res = self.watch(0.3, settings=True)
        self.assertIn('took its write back', res.line or '')
        self.assertIsNone(self.rig.live_text())
        self.assertIsNone(self.note())

    def test_a_write_over_an_item_without_a_login_is_taken_back(self):
        self.landed(created=False)
        self.clock.advance(2.0)
        self.sign_out_settings()
        self.watch(0.3, settings=True)
        self.assertIsNone(self.oauth())
        self.assertEqual(self.removals(), [])                      # an ordinary write, never a delete
        self.assertIn('mcpOAuth', json.loads(self.rig.live_text()))

    def test_in_the_same_hold_as_the_write_it_is_taken_back(self):
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.watch(0.2, keychain=True)
        state = {'wrote': False}

        def at(verb, service, account):
            if service != self.rig.service:
                return
            if verb == 'write':
                state['wrote'] = True
            elif verb == 'read' and state['wrote']:
                state['wrote'] = False
                self.sign_out_settings()
        self.rig.kc.on_call = at
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.rig.kc.on_call = None
        self.assertEqual(res.action, 'leave', res.reason)
        self.assertIsNone(self.rig.live_text())

    def test_only_the_mark_of_the_id_is_kept_never_the_id(self):
        self.landed(created=True)
        with open(self.ctx.path('put-back.json'), encoding='utf-8') as f:
            text = f.read()
        self.assertNotIn(USER_ID, text)
        self.assertIn(putback._sha(USER_ID)[:16], text)


class ASettingsFileMadeAnewByItsId(Base):
    def nothing_is_taken_back(self, **how):
        ours = self.landed(created=True)
        self.clock.advance(1.0)
        self.made_anew(**how)
        looks = [self.watch(0.5, settings=True)] + [self.watch(2.0) for _ in range(35)]
        self.assertEqual(self.rig.live_text(), ours)
        self.assertEqual(self.removals(), [])
        self.assertEqual(set(r.action for r in looks), {'leave'})
        self.assertEqual(len([t for t in self.rig.logs if t == ANEW_ID]), 1, self.rig.logs[-5:])
        self.assertEqual([t for t in self.rig.logs if t == bounds.ANEW], [])

    def test_a_small_new_file_without_an_id(self):
        self.nothing_is_taken_back()

    def test_a_small_new_file_with_a_new_id(self):
        self.nothing_is_taken_back(user_id=OTHER_ID)

    def test_a_new_file_as_large_as_the_old_one_with_a_new_id(self):
        self.nothing_is_taken_back(user_id=OTHER_ID, big=True)

    def test_a_new_file_as_large_as_the_old_one_without_an_id(self):
        self.nothing_is_taken_back(big=True)

    def test_a_settings_file_that_cannot_be_read_takes_nothing_back(self):
        ours = self.landed(created=True)
        with open(self.rig.config_path(), 'w') as f:
            f.write('{not json' + 'x' * 30000)
        for _ in range(3):
            self.watch(1.0, settings=True)
        self.assertEqual(self.rig.live_text(), ours)

    def test_in_the_same_hold_as_the_write_it_is_left(self):
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.watch(0.2, keychain=True)
        state = {'wrote': False}

        def at(verb, service, account):
            if service != self.rig.service:
                return
            if verb == 'write':
                state['wrote'] = True
            elif verb == 'read' and state['wrote']:
                state['wrote'] = False
                self.made_anew(user_id=OTHER_ID, big=True)
        self.rig.kc.on_call = at
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.rig.kc.on_call = None
        self.assertEqual(res.action, 'put_back', res.reason)
        self.assertEqual(self.oauth(), json.loads(self.b_text)['claudeAiOauth'])


class TheSameIdInAFileOfAFewKeys(Base):
    """Claude Code may give a new settings file the id it had (the function that makes the id takes one). A new file
    starts with a handful of keys, so a lived-in file that lost more keys than a sign-out can clear was made anew,
    whatever id it holds."""

    def lose(self, count):
        """The settings file without its account and count - 1 other keys (never its id)."""
        obj = self.rig.config()
        obj.pop('oauthAccount')
        for name in sorted(k for k in obj if k != 'userID')[:count - 1]:
            del obj[name]
        with open(self.rig.config_path(), 'w') as f:
            json.dump(obj, f, indent=2)

    def test_a_new_file_with_the_same_id_and_a_handful_of_keys_takes_nothing_back(self):
        ours = self.landed(created=True)
        self.clock.advance(1.0)
        self.made_anew(user_id=USER_ID)
        for _ in range(5):
            self.watch(1.0, settings=True)
        self.assertEqual(self.rig.live_text(), ours)
        self.assertEqual(len([t for t in self.rig.logs if t == ANEW_ID]), 1, self.rig.logs[-5:])

    def test_as_many_keys_gone_as_a_sign_out_may_clear_is_the_lived_in_file(self):
        self.landed(created=True)
        self.assertEqual(self.note()['settings_keys'], len(self.rig.config()))
        self.assertGreater(len(self.rig.config()), putback.TAKE_BACK_KEYS + 5)
        self.lose(putback.TAKE_BACK_KEYS)
        self.watch(2.0, settings=True)
        self.assertIsNone(self.rig.live_text())                    # a sign-out: taken back

    def test_one_key_more_gone_is_a_file_made_anew(self):
        ours = self.landed(created=True)
        self.lose(putback.TAKE_BACK_KEYS + 1)
        for _ in range(3):
            self.watch(1.0, settings=True)
        self.assertEqual(self.rig.live_text(), ours)


class ASmallSettingsFile(bounds.Base):
    """A settings file of a dozen keys, as in the first days of use: a sign-out clears more than half of them, and
    is still a sign-out."""

    def setUp(self):
        super().setUp()
        obj = {'numStartups': 3, 'installMethod': 'made-up', 'userID': USER_ID, 'hasCompletedOnboarding': True,
               'oauthAccount': self.rig.config()['oauthAccount'], 'tipsHistory': {'made-up': 1}}
        obj.update((name, {'made-up': 1}) for name in ('clientDataCacheSlots', 'cachedUsageUtilization',
                   'additionalModelCostsCache', 'cachedArtifactRoster', 'modelAccessCache', 'orgModelDefaultCache'))
        with open(self.rig.config_path(), 'w') as f:
            json.dump(obj, f, indent=2)
        self.cleared = ('oauthAccount', 'clientDataCacheSlots', 'cachedUsageUtilization', 'additionalModelCostsCache',
                        'cachedArtifactRoster', 'modelAccessCache', 'orgModelDefaultCache')

    def test_a_sign_out_that_clears_seven_of_twelve_keys_is_taken_back(self):
        self.landed(created=True)
        self.assertEqual(putback.load_record(self.ctx)['written']['settings_keys'], 12)
        obj = self.rig.config()
        for name in self.cleared:
            del obj[name]
        with open(self.rig.config_path(), 'w') as f:
            json.dump(obj, f, indent=2)
        self.watch(2.0, settings=True)
        self.assertIsNone(self.rig.live_text())

    def test_a_new_file_with_another_id_is_not(self):
        ours = self.landed(created=True)
        with open(self.rig.config_path(), 'w') as f:
            json.dump({'numStartups': 1, 'userID': OTHER_ID}, f)
        for _ in range(3):
            self.watch(1.0, settings=True)
        self.assertEqual(self.rig.live_text(), ours)


class ASettingsFileWithoutAnId(Base):
    """No id at our write: the bound is judged by the file's size, as it was before the id was read."""
    ID = None

    def test_the_note_holds_no_mark(self):
        self.landed(created=True)
        self.assertIsNone(self.note()['settings_user'])
        self.assertEqual(self.note()['settings_size'], self.size_before)

    def test_a_sign_out_that_clears_kilobytes_is_read_as_made_anew(self):
        ours = self.landed(created=True)
        self.clock.advance(2.0)
        self.sign_out_settings()
        for _ in range(3):
            self.watch(1.0, settings=True)
        self.assertEqual(self.rig.live_text(), ours)
        self.assertEqual(len([t for t in self.rig.logs if t == bounds.ANEW]), 1, self.rig.logs[-5:])

    def test_a_sign_out_that_clears_a_few_hundred_bytes_is_taken_back(self):
        self.landed(created=True)
        self.clock.advance(2.0)
        self.rig.set_config(None)
        self.watch(0.3, settings=True)
        self.assertIsNone(self.rig.live_text())


class ANoteWithoutAMark(Base):
    def test_a_note_from_before_the_mark_was_kept_is_judged_by_size(self):
        ours = self.landed(created=True)
        path = self.ctx.path('put-back.json')
        with open(path, encoding='utf-8') as f:
            obj = json.load(f)
        self.assertEqual(len(obj['written'].pop('settings_user')), 16)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(obj, f)
        self.assertIsNone(self.note()['settings_user'])
        self.clock.advance(2.0)
        self.sign_out_settings()                                   # kilobytes fewer: by size, made anew
        for _ in range(3):
            self.watch(1.0, settings=True)
        self.assertEqual(self.rig.live_text(), ours)

    def test_a_mark_that_is_not_sixteen_characters_is_no_mark(self):
        for bad in (123, True, '', 'abc', 'x' * 17, ['a' * 16], None):
            w = {'at': 1.0, 'account': 'uuid-b', 'how': 'created', 'sha': 'a' * 64, 'settings_user': bad}
            self.assertIsNone(putback._load_written(w)['settings_user'], bad)
        w = {'at': 1.0, 'account': 'uuid-b', 'how': 'created', 'sha': 'a' * 64, 'settings_user': 'b' * 16}
        self.assertEqual(putback._load_written(w)['settings_user'], 'b' * 16)


if __name__ == '__main__':
    unittest.main()
