"""The sign-in watch's Changes, on made-up folders: what counts as a change, and what does not.

Real kqueue and real files under the test's temp folder. A hint that comes within a second or two of a change, from
a Changes made just before it, came from an event: the stamps are only compared every SWEEP_S (5 s) otherwise.
"""
import _isolate  # noqa: F401 - first: keeps every write in temp folders

import os
import select
import shutil
import sys
import tempfile
import time
import unittest

from switchboard.accounts import signin_watch as sw
from switchboard.accounts import usage

from fakes_autoswitch import FakeClock


def write(path, data=b'x' * 100):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(data)


def replace(path, data=b'y' * 120):
    tmp = path + '.tmp-new'
    write(tmp, data)
    os.replace(tmp, path)


class ShiftClock(usage.Clock):
    """The real clock, with awake time the test can move ahead."""

    def __init__(self):
        self.shift = 0.0

    def mono(self):
        return usage.Clock.mono(self) + self.shift


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='signin-watch-')
        self.home = os.path.join(self.tmp, 'home')
        self.kc = os.path.join(self.home, 'Library', 'Keychains')
        self.claude = os.path.join(self.home, '.claude')
        self.login = os.path.join(self.claude, '.credentials.json')
        self.settings = os.path.join(self.home, '.claude.json')
        write(os.path.join(self.kc, 'login.keychain-db'))
        write(self.settings, b'{}')
        os.makedirs(self.claude)
        self.logs = []
        self.made = []

    def tearDown(self):
        for ch in self.made:
            ch.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def changes(self, **kw):
        ch = sw.Changes(self.kc, self.login, self.settings, log=self.logs.append, **kw)
        self.made.append(ch)
        return ch

    def collect(self, ch, within, step=0.1):
        """The first hint within `within` real seconds (and how long it took), or (None, within)."""
        t0 = time.monotonic()
        while time.monotonic() - t0 < within:
            h = ch.wait(step)
            if h:
                return h, time.monotonic() - t0
        return None, within


@unittest.skipUnless(hasattr(select, 'kqueue'), 'kqueue is a macOS and BSD facility')
class EventsTest(Base):
    def test_a_keychain_file_replaced_by_rename_is_a_change_at_once(self):
        ch = self.changes()
        self.assertEqual(ch.mode, 'events')
        replace(os.path.join(self.kc, 'login.keychain-db'))
        h, took = self.collect(ch, 3.0)
        self.assertEqual(h, {'keychain': True, 'login_file': None, 'settings': False, 'tick': False,
                             'login_file_written': False})
        self.assertLess(took, 3.0)
        h, _ = self.collect(ch, 0.5)
        self.assertIsNone(h, 'one change is said once')

    def test_a_keychain_file_written_in_place_is_a_change_at_once(self):
        ch = self.changes()
        with open(os.path.join(self.kc, 'login.keychain-db'), 'ab') as f:
            f.write(b'more')
        h, took = self.collect(ch, 3.0)
        self.assertTrue(h and h['keychain'])
        self.assertLess(took, 3.0)

    def test_a_new_keychain_file_is_a_change(self):
        ch = self.changes()
        write(os.path.join(self.kc, 'other.keychain-db'))
        h, _ = self.collect(ch, 3.0)
        self.assertTrue(h and h['keychain'])

    def test_the_login_file_appears_changes_and_disappears(self):
        ch = self.changes()
        write(self.login, b'{"a":1}')
        h, took = self.collect(ch, 3.0)
        self.assertEqual(h['login_file'], 'appeared')
        self.assertFalse(h['keychain'])
        self.assertLess(took, 3.0)
        with open(self.login, 'ab') as f:
            f.write(b' ')
        h, _ = self.collect(ch, 3.0)
        self.assertEqual(h['login_file'], 'changed')
        os.unlink(self.login)
        h, _ = self.collect(ch, 3.0)
        self.assertEqual(h['login_file'], 'gone')

    def test_the_settings_file_replaced(self):
        ch = self.changes()
        replace(self.settings, b'{"oauthAccount":{}}')
        h, took = self.collect(ch, 3.0)
        self.assertEqual(h, {'keychain': False, 'login_file': None, 'settings': True, 'tick': False,
                             'login_file_written': False})
        self.assertLess(took, 3.0)

    def test_a_folder_that_does_not_exist_yet_is_watched_once_made(self):
        shutil.rmtree(self.claude)
        ch = self.changes()
        self.assertEqual(ch.mode, 'events')
        write(self.login, b'{}')
        h, _ = self.collect(ch, sw.SWEEP_S + 3.0)
        self.assertEqual(h['login_file'], 'appeared')
        self.assertIn(self.claude, ch.watched, 'the new folder is watched by events from now on')
        with open(self.login, 'ab') as f:
            f.write(b' ')
        h, took = self.collect(ch, 3.0)
        self.assertEqual(h['login_file'], 'changed')
        self.assertLess(took, 3.0)

    def test_a_watched_folder_removed_and_made_again(self):
        ch = self.changes()
        shutil.rmtree(self.kc)
        h, _ = self.collect(ch, 3.0)
        self.assertTrue(h and h['keychain'], 'its keychain file is gone')
        write(os.path.join(self.kc, 'login.keychain-db'), b'new')
        h, _ = self.collect(ch, sw.SWEEP_S + 3.0)
        self.assertTrue(h and h['keychain'])
        self.assertIn(self.kc, ch.watched)
        replace(os.path.join(self.kc, 'login.keychain-db'))
        h, took = self.collect(ch, 3.0)
        self.assertTrue(h and h['keychain'])
        self.assertLess(took, 3.0, 'the folder made again is watched by events')

    def test_an_unrelated_file_in_the_keychain_folder_is_no_change(self):
        ch = self.changes()
        write(os.path.join(self.kc, 'notes.txt'))
        replace(os.path.join(self.kc, 'notes.txt'))
        os.makedirs(os.path.join(self.kc, 'A1B2C3'))
        h, _ = self.collect(ch, 1.5)
        self.assertIsNone(h)

    def test_an_event_with_no_stamp_change_is_no_change(self):
        ch = self.changes()
        os.makedirs(os.path.join(self.claude, '.storage-write.lock'))       # Claude Code's save lock comes and goes
        os.rmdir(os.path.join(self.claude, '.storage-write.lock'))
        os.chmod(self.claude, 0o755)
        h, _ = self.collect(ch, 1.5)
        self.assertIsNone(h)

    def test_a_nudge_ends_a_wait_early(self):
        ch = self.changes()
        ch.nudge()
        t0 = time.monotonic()
        self.assertIsNone(ch.wait(4.0))
        self.assertLess(time.monotonic() - t0, 2.0)

    def test_a_missed_event_costs_one_sweep(self):
        ch = self.changes()
        ch._close_kqueue()                    # every registration gone, as if events stopped coming
        ch.kq = select.kqueue()
        ch.kq.control([select.kevent(ch.r, filter=select.KQ_FILTER_READ, flags=select.KQ_EV_ADD)], 0, 0)
        ch.next_sweep = ch.clock.mono() + 1.0
        write(self.login, b'{}')
        h, _ = self.collect(ch, 4.0)
        self.assertEqual(h['login_file'], 'appeared')


class PollingTest(Base):
    def test_kqueue_refused_gives_the_same_hints_within_a_second_and_is_tried_again(self):
        calls = []

        def refused():
            calls.append(1)
            if len(calls) == 1:
                raise OSError(24, 'Too many open files')
            return select.kqueue()
        clock = ShiftClock()
        ch = self.changes(kqueue=refused, clock=clock)
        self.assertEqual(ch.mode, 'polling')
        self.assertEqual(len([x for x in self.logs if 'kqueue failed (OSError)' in x]), 1)
        replace(os.path.join(self.kc, 'login.keychain-db'))
        h, took = self.collect(ch, 2.5)
        self.assertEqual(h, {'keychain': True, 'login_file': None, 'settings': False, 'tick': False,
                             'login_file_written': False})
        self.assertLessEqual(took, 1.0 + 1.0)
        write(self.login, b'{}')
        h, _ = self.collect(ch, 2.5)
        self.assertEqual(h['login_file'], 'appeared')
        if not hasattr(select, 'kqueue'):
            return
        clock.shift += sw.KQUEUE_RETRY_S + 1.0
        ch.wait(0.05)
        self.assertEqual(ch.mode, 'events')
        self.assertEqual(len(calls), 2)
        self.assertTrue(any('works again' in x for x in self.logs))

    def test_no_kqueue_at_all(self):
        ch = self.changes(kqueue=None)
        self.assertEqual(ch.mode, 'polling')
        replace(self.settings, b'{"x":1}')
        h, _ = self.collect(ch, 2.5)
        self.assertTrue(h and h['settings'])
        self.assertEqual(len([x for x in self.logs if 'not available' in x]), 1)


class UnreadableFolderTest(Base):
    """A Keychain folder that cannot be listed (a protection of macOS's, a volume that is not mounted) leaves the watch
    blind to every save in it: that is said once in the log, the heartbeat's problem says it while it lasts, and both
    clear when the folder can be listed again. A folder that is not there holds no keychain: nothing is said."""

    def tearDown(self):
        if os.path.isdir(self.kc):
            os.chmod(self.kc, 0o700)                        # so the temp folder can be removed whatever happened
        super().tearDown()

    def beat(self, ch):
        beats = []
        w = sw.Watch(ch, lambda hint: None, clock=FakeClock(), log=self.logs.append,
                     beat_path='/nonexistent/beat.json', write=lambda path, data: beats.append(dict(data)), pid=4242)
        w._beat(w.clock.mono())
        return beats[-1]

    def test_said_once_in_the_heartbeat_and_the_log_and_cleared_when_it_can_be_read(self):
        os.chmod(self.kc, 0)
        try:
            os.listdir(self.kc)
            self.skipTest('this user can list a folder with no permissions')
        except PermissionError:
            pass
        ch = self.changes()
        for _ in range(5):
            ch.sweep()
        self.assertEqual(len([x for x in self.logs if 'Keychain folder cannot be read' in x]), 1, self.logs)
        self.assertEqual(self.beat(ch)['problem'], 'the Keychain folder cannot be read')
        os.chmod(self.kc, 0o700)
        h = ch.sweep()
        self.assertTrue(h and h['keychain'], 'its keychain is seen again, as a change')
        ch.sweep()
        self.assertEqual(len([x for x in self.logs if 'Keychain folder can be read again' in x]), 1, self.logs)
        self.assertNotEqual(self.beat(ch)['problem'], 'the Keychain folder cannot be read')

    def test_a_folder_that_is_not_there_is_not_said(self):
        shutil.rmtree(self.kc)
        ch = self.changes()
        ch.sweep()
        self.assertEqual([x for x in self.logs if 'Keychain folder' in x], [])
        self.assertNotEqual(self.beat(ch)['problem'], 'the Keychain folder cannot be read')


class QuietTest(Base):
    def test_waiting_reads_no_file_and_starts_no_program(self):
        seen = []

        def hook(event, args):
            if not counting:
                return
            if event == 'open' and isinstance(args[0], str) and (args[1] in (None, 'r', 'rb') or
                                                                   isinstance(args[2], int) and
                                                                   not args[2] & (os.O_WRONLY | os.O_RDWR)):
                if args[0].startswith(self.tmp) and os.path.isfile(args[0]):
                    seen.append(('read', args[0]))
            if event in ('subprocess.Popen', 'os.posix_spawn', 'os.system') or event.startswith('os.exec'):
                seen.append(('run', event))
        counting = False
        sys.addaudithook(hook)
        for kqueue in (sw.Changes._DEFAULT, None):
            ch = self.changes(kqueue=kqueue)
            ch.next_sweep = ch.clock.mono()          # a sweep inside the window too
            counting = True
            t0 = time.monotonic()
            while time.monotonic() - t0 < 2.5:
                self.assertIsNone(ch.wait(0.5))
            counting = False
        self.assertEqual([x for x in seen if x[0] == 'run'], [])
        self.assertEqual([x for x in seen if x[0] == 'read'], [])

    def test_view_is_numbers_only(self):
        ch = self.changes()
        write(self.login, b'{"secret":"sk-ant-oat01-' + b'A' * 40 + b'"}')
        v = ch.view(fresh=True)
        self.assertEqual(set(v), {'kc_size', 'kc_mtime', 'login', 'login_size', 'login_mtime', 'settings_mtime'})
        self.assertTrue(v['login'])
        for k, x in v.items():
            self.assertTrue(x is None or isinstance(x, (int, float, bool)), k)
        self.assertFalse(ch.view()['login'], 'fresh does not move the stamps the next compare starts from')
        h, _ = self.collect(ch, 3.0)
        self.assertEqual(h['login_file'], 'appeared')


if __name__ == '__main__':
    unittest.main()
