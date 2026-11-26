"""Claude Code's files and locks as the engine handles them: atomic writes, the global
config merged and never clobbered, Claude Code's own path and item rules, and its mkdir locks."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import errno
import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
import unicodedata
import unittest
from unittest import mock

from fakes_accounts import identity_block
from switchboard.accounts import ccfiles, facts


class Tmp(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix='sb-files-'))
        self.home = os.path.join(self.root, 'home')
        os.makedirs(os.path.join(self.home, '.claude'))
        self.env = {'USER': 'tester'}

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def leftovers(self, folder):
        return [n for n in os.listdir(folder) if n.endswith('.tmp') or '.stale.' in n or n.endswith('.takeover')]


class AtomicWrites(Tmp):
    def test_a_write_lands_whole_owner_only_from_its_first_byte_and_leaves_no_temp_file(self):
        path = os.path.join(self.root, 'f.json')
        modes = []
        real = os.write

        def watch(fd, data):
            modes.append(os.fstat(fd).st_mode & 0o777)
            return real(fd, data)

        with mock.patch.object(ccfiles, '_write', watch):
            ccfiles.atomic_write(path, b'{"a": 1}')
        self.assertEqual(modes[0], 0o600)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        with open(path, 'rb') as f:
            self.assertEqual(f.read(), b'{"a": 1}')
        self.assertEqual(self.leftovers(self.root), [])

    def test_a_full_disk_at_the_nth_write_leaves_the_old_file_whole_and_no_temp_file(self):
        path = os.path.join(self.root, 'state.json')
        with open(path, 'wb') as f:
            f.write(b'{"old": true}')
        for nth in (1, 2, 5):
            calls = []

            def dribble(fd, data):
                calls.append(1)
                if len(calls) == nth:
                    raise OSError(errno.ENOSPC, 'No space left on device')
                return os.write(fd, bytes(data[:7]))

            with mock.patch.object(ccfiles, '_write', dribble):
                with self.assertRaises(ccfiles.DiskFull):
                    ccfiles.atomic_write(path, b'{"new": "' + b'x' * 64 + b'"}')
            with open(path, 'rb') as f:
                self.assertEqual(f.read(), b'{"old": true}')
            self.assertEqual(self.leftovers(self.root), [])

    def test_a_symlinked_file_is_written_through_and_the_link_stays_a_link(self):
        real_dir = os.path.join(self.root, 'dotfiles')
        os.makedirs(real_dir)
        target = os.path.join(real_dir, 'claude.json')
        with open(target, 'w') as f:
            f.write('{}')
        mid = os.path.join(self.root, 'mid.json')
        os.symlink(os.path.join('dotfiles', 'claude.json'), mid)        # relative, two hops
        link = os.path.join(self.home, '.claude.json')
        os.symlink(os.path.join('..', 'mid.json'), link)
        ccfiles.atomic_write(link, b'{"b": 2}')
        self.assertTrue(os.path.islink(link) and os.path.islink(mid))
        with open(target) as f:
            self.assertEqual(f.read(), '{"b": 2}')

    def test_a_loop_of_links_is_refused_and_nothing_is_written(self):
        a, b = os.path.join(self.root, 'a'), os.path.join(self.root, 'b')
        os.symlink(b, a)
        os.symlink(a, b)
        with self.assertRaises(ccfiles.CCFileError):
            ccfiles.atomic_write(a, b'x')
        self.assertTrue(os.path.islink(a) and os.path.islink(b))

    def test_a_dangling_link_writes_where_it_points(self):
        link = os.path.join(self.root, 'link')
        os.symlink(os.path.join(self.root, 'not-yet'), link)
        ccfiles.atomic_write(link, b'made')
        with open(os.path.join(self.root, 'not-yet'), 'rb') as f:
            self.assertEqual(f.read(), b'made')


class TheGlobalConfig(Tmp):
    def write_config(self, text):
        path = facts.global_config(self.env, self.home)
        with open(path, 'w') as f:
            f.write(text)
        return path

    def test_only_the_identity_block_changes_and_every_other_key_survives_as_claude_code_writes_them(self):
        before = {'numStartups': 7, 'projects': {'/x': {'allowedTools': ['Bash'], 'history': [1, 2.5, None]}},
                  'mcpServers': {'docs': {'command': 'x', 'env': {'TOKEN': 'abc'}}}, 'userID': 'u-1',
                  'oauthAccount': identity_block('uuid-a', 'a@example.com'), 'someFutureKey': {'nested': [True]},
                  'text': 'café — ok'}
        path = self.write_config(json.dumps(before))
        ccfiles.write_identity(self.env, self.home, identity_block('uuid-b', 'b@example.com'))
        with open(path, encoding='utf-8') as f:
            raw = f.read()
        after = json.loads(raw)
        self.assertEqual(after['oauthAccount']['accountUuid'], 'uuid-b')
        for k in before:
            if k != 'oauthAccount':
                self.assertEqual(after[k], before[k], k)
        self.assertEqual(list(after), list(before))
        self.assertIn('\n  "numStartups": 7', raw)           # Claude Code's own format: 2-space JSON, text as is
        self.assertIn('café', raw)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_a_torn_or_unparseable_config_is_never_written_over(self):
        for text in ('{"projects": {"/x": ', '', '[1, 2]', '{"n": NaN}', '﻿{}'):
            path = self.write_config(text)
            with self.assertRaises(ccfiles.CCFileError):
                ccfiles.write_identity(self.env, self.home, identity_block('uuid-b', 'b@example.com'))
            with open(path, encoding='utf-8') as f:
                self.assertEqual(f.read(), text)

    def test_an_absent_config_is_a_genuine_empty_start(self):
        ccfiles.write_identity(self.env, self.home, identity_block('uuid-c', 'c@example.com'))
        with open(facts.global_config(self.env, self.home)) as f:
            self.assertEqual(json.load(f), {'oauthAccount': identity_block('uuid-c', 'c@example.com')})

    def test_the_global_config_path_follows_claude_codes_config_json_rule(self):
        main = os.path.join(self.home, '.claude.json')
        legacy = os.path.join(self.home, '.claude', '.config.json')
        self.assertEqual(facts.global_config(self.env, self.home), main)
        with open(legacy, 'w') as f:
            f.write('{"x": 1}')
        self.assertEqual(facts.global_config(self.env, self.home), legacy)        # non-empty legacy wins
        with open(main, 'w') as f:
            f.write('{}')
        self.assertEqual(facts.global_config(self.env, self.home), legacy)        # even beside a main file
        open(legacy, 'w').close()
        self.assertEqual(facts.global_config(self.env, self.home), main)          # empty, main exists: main
        os.unlink(main)
        self.assertEqual(facts.global_config(self.env, self.home), legacy)        # empty+writable, no main
        os.chmod(legacy, 0o444)
        self.assertEqual(facts.global_config(self.env, self.home), main)          # empty, no write bit: main
        os.chmod(legacy, 0o644)
        os.unlink(legacy)
        os.makedirs(legacy)
        self.assertEqual(facts.global_config(self.env, self.home), main)          # not a regular file
        custom = os.path.join(self.root, 'profile')
        os.makedirs(custom)
        self.assertEqual(facts.global_config({'CLAUDE_CONFIG_DIR': custom}, self.home),
                         os.path.join(custom, '.claude.json'))

    def test_claude_codes_plaintext_login_file_gets_the_new_login_when_it_exists_and_is_never_created(self):
        path = facts.credentials_file(self.env, self.home)
        self.assertFalse(ccfiles.bump_credentials_file(self.env, self.home, '{"new": 1}'))
        self.assertFalse(os.path.exists(path))
        with open(path, 'w') as f:
            f.write('{"old": 1}')
        os.utime(path, (time.time() - 3600, time.time() - 3600))
        before = os.stat(path).st_mtime
        self.assertTrue(ccfiles.bump_credentials_file(self.env, self.home, '{"new": 1}'))
        self.assertGreater(os.stat(path).st_mtime, before)
        with open(path) as f:
            self.assertEqual(f.read(), '{"new": 1}')


class ClaudeCodesNames(Tmp):
    def test_keychain_item_names_follow_claude_codes_rules(self):
        self.assertEqual(facts.keychain_service({}), 'Claude Code-credentials')
        self.assertEqual(facts.keychain_service({'CLAUDE_CONFIG_DIR': ''}), 'Claude Code-credentials')
        d = '/Users/t/Café/profile'
        want = 'Claude Code-credentials-' + hashlib.sha256(d.encode('utf-8')).hexdigest()[:8]
        self.assertEqual(facts.keychain_service({'CLAUDE_CONFIG_DIR': d}), want)
        self.assertEqual(facts.keychain_service({'CLAUDE_CONFIG_DIR': unicodedata.normalize('NFD', d)}), want)
        self.assertNotEqual(facts.keychain_service({'CLAUDE_CONFIG_DIR': d + '/'}), want)   # the string as given
        self.assertEqual(facts.keychain_service({'CLAUDE_CONFIG_DIR': d, 'CLAUDE_SECURESTORAGE_CONFIG_DIR': ''}),
                         'Claude Code-credentials')                                  # defined-empty: the default
        s = '/Users/t/secure'
        self.assertEqual(facts.keychain_service({'CLAUDE_CONFIG_DIR': d, 'CLAUDE_SECURESTORAGE_CONFIG_DIR': s}),
                         'Claude Code-credentials-' + hashlib.sha256(s.encode()).hexdigest()[:8])
        self.assertEqual(facts.keychain_account({'USER': 'kim'}), 'kim')
        self.assertEqual(facts.keychain_account({'USER': 'kim lee'}), 'claude-code-user')
        self.assertEqual(facts.keychain_account({'USER': 'kim\n'}), 'claude-code-user')

    def test_the_login_file_and_locks_live_in_the_secure_storage_folder(self):
        env = {'CLAUDE_SECURESTORAGE_CONFIG_DIR': ''}
        self.assertEqual(facts.credentials_file(env, self.home),
                         os.path.join(self.home, '.claude', '.credentials.json'))
        sec = os.path.join(self.root, 'sec')
        env = {'CLAUDE_CONFIG_DIR': os.path.join(self.root, 'cfg'), 'CLAUDE_SECURESTORAGE_CONFIG_DIR': sec}
        self.assertEqual(facts.credentials_file(env, self.home), os.path.join(sec, '.credentials.json'))
        self.assertEqual(facts.refresh_lock(env, self.home), os.path.join(sec, '.oauth_refresh.lock'))

    def test_the_legacy_lock_uses_the_folders_real_path(self):
        real = os.path.join(self.root, 'real-claude')
        os.makedirs(real)
        home2 = os.path.join(self.root, 'home2')
        os.makedirs(home2)
        os.symlink(real, os.path.join(home2, '.claude'))
        self.assertEqual(facts.legacy_lock({}, home2), real + '.lock')
        self.assertEqual(facts.legacy_lock({}, os.path.join(self.root, 'nobody')),
                         os.path.join(self.root, 'nobody', '.claude') + '.lock')

    def test_check_warns_when_the_claude_code_on_this_mac_is_newer_than_the_checked_facts(self):
        self.assertTrue(facts.check(self.home, versions=[facts.CHECKED])['ok'])
        newer = facts.check(self.home, versions=['2.1.9', '2.2.0'])
        self.assertFalse(newer['ok'])
        self.assertIn('2.2.0', newer['warnings'][0])
        os.makedirs(os.path.join(self.home, '.vscode', 'extensions', 'anthropic.claude-code-2.1.400-darwin-arm64'))
        self.assertEqual(facts.check(self.home)['installed'], '2.1.400')


class Locks(Tmp):
    def lock(self, name='.oauth_refresh.lock', stale=60.0, **kw):
        return ccfiles.DirLock(os.path.join(self.home, '.claude', name), stale_s=stale, **kw)

    def test_a_held_lock_is_waited_for_and_never_stolen_while_younger_than_its_window(self):
        path = os.path.join(self.home, '.claude', '.oauth_refresh.lock')
        os.mkdir(path)
        os.utime(path, (time.time() - 50, time.time() - 50))       # 50 s: younger than 60
        with self.assertRaises(ccfiles.LockBusy):
            self.lock().acquire(budget_s=0.6)
        self.assertTrue(os.path.isdir(path))
        self.assertEqual(self.leftovers(os.path.join(self.home, '.claude')), [])

    def test_a_stale_lock_is_taken_over_and_nothing_is_left_beside_it(self):
        path = os.path.join(self.home, '.claude', '.oauth_refresh.lock')
        os.mkdir(path)
        os.utime(path, (time.time() - 70, time.time() - 70))
        lk = self.lock().acquire(budget_s=2.0)
        self.assertTrue(lk.still_held())
        lk.release()
        self.assertFalse(os.path.exists(path))
        self.assertEqual(self.leftovers(os.path.join(self.home, '.claude')), [])

    def test_two_takers_of_one_stale_lock_never_both_hold_it(self):
        path = os.path.join(self.home, '.claude', '.oauth_refresh.lock')
        os.mkdir(path)
        os.utime(path, (time.time() - 120, time.time() - 120))
        inside, worst, got = [0], [0], []
        guard = threading.Lock()

        def taker():
            lk = self.lock()
            try:
                lk.acquire(budget_s=30.0)          # generous: a loaded Mac schedules threads late
            except ccfiles.LockBusy:
                return
            with guard:
                inside[0] += 1
                worst[0] = max(worst[0], inside[0])
            time.sleep(0.05)
            with guard:
                inside[0] -= 1
                got.append(1)
            lk.release()

        threads = [threading.Thread(target=taker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(worst[0], 1)
        self.assertEqual(len(got), 6)

    def test_letting_go_of_a_lock_taken_over_never_removes_the_new_holders(self):
        lk = self.lock().acquire(budget_s=1.0)
        os.rmdir(lk.path)                                    # someone judged it stale and took it
        os.mkdir(lk.path)
        self.assertFalse(lk.still_held())
        lk.release()
        self.assertTrue(os.path.isdir(lk.path))

    def test_our_toucher_keeps_a_held_lock_fresh(self):
        lk = self.lock(touch_s=0.1).acquire(budget_s=1.0)
        try:
            os.utime(lk.path, (time.time() - 40, time.time() - 40))
            end = time.time() + 15                           # a loaded Mac runs the toucher thread late
            while time.time() - os.stat(lk.path).st_mtime > 5 and time.time() < end:
                time.sleep(0.1)
            self.assertLess(time.time() - os.stat(lk.path).st_mtime, 5)
        finally:
            lk.release()

    def test_a_dangling_link_at_the_lock_path_backs_off_instead_of_spinning(self):
        path = os.path.join(self.home, '.claude', '.oauth_refresh.lock')
        os.symlink(os.path.join(self.root, 'gone'), path)
        sleeps = []
        lk = self.lock(sleep=lambda s: (sleeps.append(s), time.sleep(s)))
        with self.assertRaises(ccfiles.LockBusy):
            lk.acquire(budget_s=0.4)
        self.assertTrue(sleeps and all(s <= 0.05 for s in sleeps))
        self.assertLess(len(sleeps), 40)

    def test_the_config_lock_is_stale_after_ten_seconds_and_the_credential_locks_after_sixty(self):
        cfg = facts.config_lock(self.env, self.home)
        os.mkdir(cfg)
        os.utime(cfg, (time.time() - 5, time.time() - 5))
        with self.assertRaises(ccfiles.LockBusy):
            with ccfiles.config_lock(self.env, self.home, budget_s=0.5):
                pass
        os.utime(cfg, (time.time() - 15, time.time() - 15))
        with ccfiles.config_lock(self.env, self.home, budget_s=2.0) as lk:
            self.assertTrue(lk.still_held())
        cred = facts.refresh_lock(self.env, self.home)
        os.mkdir(cred)
        os.utime(cred, (time.time() - 15, time.time() - 15))       # 15 s is young for a credential lock
        with self.assertRaises(ccfiles.LockBusy):
            with ccfiles.credential_locks(self.env, self.home, budget_s=1.0):
                pass

    def test_credential_locks_go_in_claude_codes_order_and_a_held_legacy_lock_lets_go_of_the_first(self):
        order = []
        real = ccfiles.DirLock.acquire

        def note(lk, budget_s=None):
            order.append(os.path.basename(lk.path))
            return real(lk, budget_s)

        with mock.patch.object(ccfiles.DirLock, 'acquire', note):
            with ccfiles.credential_locks(self.env, self.home, budget_s=5.0) as held:
                self.assertTrue(held.still_held())
        self.assertEqual(order[:2], ['.oauth_refresh.lock', '.claude.lock'])
        legacy = facts.legacy_lock(self.env, self.home)
        os.mkdir(legacy)                                     # Claude Code holds the second one
        first = facts.refresh_lock(self.env, self.home)
        seen_first_free = []

        def watch_sleep(s):
            if s >= 0.9:                                     # the pause between tries of the pair
                seen_first_free.append(not os.path.exists(first))
            time.sleep(min(s, 0.05))

        with self.assertRaises(ccfiles.LockBusy):
            with ccfiles.credential_locks(self.env, self.home, budget_s=3.0, sleep=watch_sleep):
                pass
        self.assertTrue(seen_first_free and all(seen_first_free))
        self.assertFalse(os.path.exists(first))
        self.assertTrue(os.path.isdir(legacy))


if __name__ == '__main__':
    unittest.main()
