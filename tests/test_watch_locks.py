import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The page's own locks (core.locked for state.json, core.file_lock for settings and choices) wait for a lock another
# process holds only so long, then say so and go on, as the engine's locks do: a process that hangs while holding one
# never hangs the page. Locks are held by this test from a second open file, which the kernel treats like another
# process; nothing outside the throwaway data folder is touched.
import fcntl
import os
import shutil
import sys
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import core

REAL_DEADLINE = getattr(core, 'LOCK_DEADLINE', None)    # before any test shortens it


class Held(object):
    """A lock on <data>/<name> held the way another process would hold it (its own open file)."""

    def __init__(self, name):
        os.makedirs(core.data_dir(), exist_ok=True)
        self.f = open(core.data_path(name), 'a')
        fcntl.flock(self.f, fcntl.LOCK_EX)

    def release(self):
        if not self.f.closed:
            self.f.close()


class Base(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(core.data_dir(), ignore_errors=True)
        os.makedirs(core.data_dir(), exist_ok=True)
        self.logged = []
        p = mock.patch.object(core, 'log', side_effect=lambda msg, *a, **k: self.logged.append(msg))
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(core, 'LOCK_DEADLINE', 0.5, create=True)
        p.start()
        self.addCleanup(p.stop)

    def attempt(self, enter):
        """Run `enter` on a thread; (finished within 5 s, what it raised or None)."""
        out = {}

        def run():
            try:
                enter()
                out['error'] = None
            except Exception as e:  # noqa: BLE001
                out['error'] = e
        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(5)
        return not t.is_alive(), out.get('error')


class TheStateLock(Base):
    def test_a_lock_held_by_a_process_that_hangs_is_given_up_after_its_deadline(self):
        held = Held('.lock')
        self.addCleanup(held.release)

        def enter():
            with core.locked() as st:
                st['touched'] = True
        started = time.monotonic()
        done, error = self.attempt(enter)
        self.assertTrue(done, 'core.locked() waited for ever on a lock another process held')
        self.assertLess(time.monotonic() - started, 4.0)
        self.assertIsInstance(error, core.LockTimeout)
        self.assertIsInstance(error, core.SwitchError)              # the page shows its words as they are
        self.assertIn('busy', str(error))
        self.assertTrue(any('.lock' in x and 'went on without it' in x for x in self.logged), self.logged)
        self.assertNotIn('touched', core.load())                    # nothing was written without the lock

    def test_a_lock_let_go_within_the_deadline_is_taken_and_the_change_saved(self):
        held = Held('.lock')
        threading.Timer(0.2, held.release).start()

        def enter():
            with core.locked() as st:
                st['touched'] = True
        done, error = self.attempt(enter)
        self.assertTrue(done)
        self.assertIsNone(error)
        self.assertTrue(core.load().get('touched'))
        self.assertEqual(self.logged, [])

    def test_the_deadline_is_long_enough_for_any_write_on_a_busy_mac(self):
        self.assertIsNotNone(REAL_DEADLINE)
        self.assertGreaterEqual(REAL_DEADLINE, 30.0)


class TheFileLock(Base):
    def test_settings_held_by_a_process_that_hangs_are_given_up_after_the_deadline(self):
        held = Held('settings.json.lock')
        self.addCleanup(held.release)
        done, error = self.attempt(lambda: core.set_setting('awake', False))
        self.assertTrue(done, 'core.file_lock() waited for ever on a lock another process held')
        self.assertIsInstance(error, core.LockTimeout)
        self.assertNotIn('awake', core.settings())
        self.assertTrue(any('settings.json.lock' in x for x in self.logged), self.logged)

    def test_a_free_file_lock_is_taken_at_once(self):
        done, error = self.attempt(lambda: core.set_setting('awake', False))
        self.assertTrue(done)
        self.assertIsNone(error)
        self.assertIs(core.settings().get('awake'), False)


if __name__ == '__main__':
    unittest.main()
