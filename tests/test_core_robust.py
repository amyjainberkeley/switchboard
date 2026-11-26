import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# core.py's writes, log and disk helpers hold up on a full disk, a flood of errors and a killed write.
# Every file lives in a throwaway folder; a full disk is a stand-in os.write / os.fsync / os.replace that fails.
import errno
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import core


class Folder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-core-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.data = os.path.join(self.tmp, 'data')
        env = mock.patch.dict(os.environ, {'SWITCHBOARD_HOME': self.data, 'HOME': os.path.join(self.tmp, 'home')})
        env.start()
        self.addCleanup(env.stop)

    def leftovers(self, folder):
        return [n for n in os.listdir(folder) if n.endswith('.tmp')]


class WholeOrNotAtAll(Folder):
    """A full disk at any point of a write leaves the old file whole and no half file beside it."""

    def setUp(self):
        super().setUp()
        self.path = os.path.join(self.data, 'state.json')
        core.write_json(self.path, {'accounts': {'a@b.example': {}}, 'n': 1})
        with open(self.path, 'rb') as f:
            self.before = f.read()

    def assert_untouched(self):
        with open(self.path, 'rb') as f:
            self.assertEqual(f.read(), self.before)
        self.assertEqual(self.leftovers(self.data), [])

    def test_a_full_disk_at_each_step_leaves_the_old_file_and_no_temporary_file(self):
        full = OSError(errno.ENOSPC, 'No space left on device')
        real_write = os.write
        steps = {
            'the first write': mock.patch.object(core.os, 'write', side_effect=full),
            'a later write': mock.patch.object(core.os, 'write', side_effect=[3, full]),
            'the flush to disk': mock.patch.object(core.os, 'fsync', side_effect=full),
            'the rename': mock.patch.object(core.os, 'replace', side_effect=full),
            'making the temporary file': mock.patch.object(core.os, 'open', side_effect=full),
        }
        for name, patch in steps.items():
            with self.subTest(step=name):
                with patch:
                    with self.assertRaises(core.DiskFull) as cm:
                        core.write_json(self.path, {'accounts': {}, 'n': 2, 'pad': 'x' * 5000})
                self.assertEqual(cm.exception.errno, errno.ENOSPC)
                self.assertIsInstance(cm.exception, OSError)            # callers that catch OSError still do
                self.assert_untouched()
        self.assertIs(os.write, real_write)

    def test_a_quota_is_a_full_disk_too_and_other_errors_stay_as_they_came(self):
        with mock.patch.object(core.os, 'fsync', side_effect=OSError(errno.EDQUOT, 'Disc quota exceeded')):
            with self.assertRaises(core.DiskFull):
                core.write_text(self.path, 'x')
        with mock.patch.object(core.os, 'replace', side_effect=PermissionError(errno.EACCES, 'denied')):
            with self.assertRaises(PermissionError):
                core.write_text(self.path, 'x')
        self.assert_untouched()

    def test_a_disk_that_cannot_flush_is_still_written(self):
        with mock.patch.object(core.os, 'fsync', side_effect=OSError(errno.ENOTSUP, 'not supported')):
            core.write_json(self.path, {'n': 3})
        self.assertEqual(core.read_json(self.path), {'n': 3})

    def test_data_that_is_not_json_never_touches_the_file(self):
        with self.assertRaises(TypeError):
            core.write_json(self.path, {'n': object()})
        self.assert_untouched()

    def test_a_kill_mid_write_leaves_only_a_hidden_temporary_file_that_is_swept_later(self):
        class Killed(BaseException):
            pass
        with mock.patch.object(core.os, 'replace', side_effect=Killed()):
            with self.assertRaises(Killed):
                core.write_json(self.path, {'n': 4})
        self.assert_untouched()                       # even a BaseException cleans up after itself
        stray = os.path.join(self.data, '.state.json.deadbeef.tmp')   # what a SIGKILL would leave
        with open(stray, 'w') as f:
            f.write('{"half": ')
        self.assertEqual(core.sweep_temporary(self.data, now=time.time()), 0)          # young: kept for now
        self.assertEqual(core.sweep_temporary(self.data, now=time.time() + 7200), 1)   # an hour later: gone
        self.assertFalse(os.path.exists(stray))
        self.assertTrue(os.path.exists(self.path))

    def test_private_from_the_first_byte_and_the_mode_asked_for(self):
        seen = []
        real_open = os.open

        def watch(path, flags, mode=0o777, *a, **k):
            seen.append((os.path.basename(path), mode, flags & os.O_EXCL))
            return real_open(path, flags, mode, *a, **k)
        with mock.patch.object(core.os, 'open', watch):
            core.write_text(os.path.join(self.data, 'port'), '7799', mode=0o644)
        self.assertTrue(seen[0][0].startswith('.port.') and seen[0][0].endswith('.tmp'))
        self.assertEqual((seen[0][1], bool(seen[0][2])), (0o600, True))
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.data, 'port')).st_mode), 0o644)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_two_writers_at_once_never_share_a_temporary_file(self):
        errors = []

        def writer(i):
            try:
                for n in range(40):
                    core.write_json(self.path, {'writer': i, 'n': n, 'pad': 'y' * 2000})
            except Exception as e:  # noqa: BLE001
                errors.append(e)
        threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        self.assertEqual(errors, [])
        got = core.read_json(self.path)
        self.assertEqual(sorted(got), ['n', 'pad', 'writer'])            # whole, from one writer
        self.assertEqual(self.leftovers(self.data), [])

    def test_a_symlinked_file_stays_a_link_and_its_target_is_written(self):
        real = os.path.join(self.tmp, 'elsewhere', 'settings.json')
        os.makedirs(os.path.dirname(real))
        link = os.path.join(self.data, 'settings.json')
        os.symlink(real, link)
        core.write_json(link, {'port': 7801})
        self.assertTrue(os.path.islink(link))
        self.assertEqual(core.read_json(real), {'port': 7801})

    def test_each_write_moves_the_counter_the_page_snapshot_watches(self):
        before = core.writes()
        core.write_text(os.path.join(self.data, 'x.txt'), 'a')
        core.write_json(os.path.join(self.data, 'y.json'), {})
        self.assertEqual(core.writes(), before + 2)
        with mock.patch.object(core.os, 'replace', side_effect=OSError(errno.ENOSPC, 'full')):
            with self.assertRaises(OSError):
                core.write_text(os.path.join(self.data, 'x.txt'), 'b')
        self.assertEqual(core.writes(), before + 2)                      # a write that did not happen does not count

    def test_a_state_save_on_a_full_disk_keeps_the_old_state(self):
        core.save(dict(core.blank_state(), active='a@b.example'))
        with mock.patch.object(core.os, 'write', side_effect=OSError(errno.ENOSPC, 'full')):
            with self.assertRaises(OSError):
                with core.locked() as st:
                    st['active'] = 'c@d.example'
        self.assertEqual(core.load()['active'], 'a@b.example')
        self.assertEqual(self.leftovers(self.data), [])


class TheLog(Folder):
    """The log never grows from one repeated line, and says the count; no token ever reaches it."""

    def setUp(self):
        super().setUp()
        p = mock.patch.dict(core._REPEAT, {'line': None, 'count': 0, 'said_at': 0.0})
        p.start()
        self.addCleanup(p.stop)
        self.path = os.path.join(self.data, 'switchboard.log')

    def lines(self):
        try:
            with open(self.path, encoding='utf-8') as f:
                return [ln.rstrip('\n').split(' ', 2)[2] for ln in f]
        except FileNotFoundError:
            return []

    def test_ten_thousand_identical_errors_are_a_few_lines_and_a_count(self):
        for _ in range(10000):
            core.log('GET /api/state failed: BrokenPipeError(32, Broken pipe)')
        core.log('something else')
        got = self.lines()
        self.assertLess(len(got), 50)
        self.assertEqual(got, ['GET /api/state failed: BrokenPipeError(32, Broken pipe)',
                               '(the line above 9999 more times)', 'something else'])

    def test_a_count_still_repeating_is_said_every_few_minutes_and_at_a_flush(self):
        clock = [1000.0]
        for i in range(30):
            core.log('the same', clock=lambda: clock[0])
            clock[0] += 20.0                         # 20 s apart: 600 s in all
        core.log_flush()
        got = self.lines()
        self.assertEqual(got[0], 'the same')
        counts = [int(x.split()[3]) for x in got[1:]]
        self.assertEqual(sum(counts), 29)            # every repeat counted, none lost
        self.assertTrue(2 <= len(counts) <= 4, got)  # said along the way, not only at the end

    def test_no_token_or_key_reaches_the_log_and_paths_stay_readable(self):
        secrets_ = ['sk-ant-oat01-' + 'A1b2C3d4' * 10, 'sk-ant-api03-zzzzzzzzzzzz',
                    'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U',
                    'Bearer abcdefghijklmnop123', '9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08']
        for s in secrets_:
            core.log('refresh said %s for /Users/maya/.switchboard/state.json' % s)
        text = '\n'.join(self.lines())
        for s in secrets_:
            self.assertNotIn(s.split()[-1], text)
        self.assertIn('/Users/maya/.switchboard/state.json', text)
        self.assertIn('[hidden]', text)

    def test_redact_keeps_emails_unless_the_text_leaves_the_mac(self):
        line = 'Switched Claude Code to maya@ivy.example (token=sk-ant-ort01-abcdefgh12345678)'
        self.assertEqual(core.redact(line), 'Switched Claude Code to maya@ivy.example (token=[hidden])')
        self.assertEqual(core.redact(line, emails=True), 'Switched Claude Code to [email] (token=[hidden])')
        for fine in ('/Users/maya/code/switchboard-work/v2-b5-robust/switchboard/server.py',
                     'artifact 0f8c2d4e-6a1b-4c3d-9e8f-7a6b5c4d3e2f', 'switchboard 0.2.0 (d635e47)'):
            self.assertEqual(core.redact(fine), fine)

    def test_a_line_is_one_line_and_logging_never_raises(self):
        core.log('first\nsecond')
        self.assertEqual(self.lines(), ['first second'])
        with mock.patch.object(core.os, 'open', side_effect=OSError(errno.ENOSPC, 'full')):
            core.log('on a full disk')                # lost, never raised
        core.log_flush()

    def test_the_log_rotates_at_its_size(self):
        os.makedirs(self.data)
        with open(self.path, 'w') as f:
            f.write('x' * (core.LOG_MAX + 1))
        core.log('after')
        self.assertEqual(self.lines(), ['after'])
        self.assertEqual(os.path.getsize(self.path + '.1'), core.LOG_MAX + 1)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)


AGENT_CHILD = r'''
import os, sys
sys.path.insert(0, sys.argv[1])
os.environ['SWITCHBOARD_HOME'] = sys.argv[2]
from switchboard import core
sys.stdout.write('old ' * 400 + '\n'); sys.stdout.flush()
sys.stderr.write('old error\n'); sys.stderr.flush()
print('rotated' if core.rotate_own_output(max_bytes=1000) else 'kept'); sys.stdout.flush()
sys.stderr.write('new error\n'); sys.stderr.flush()
print('after'); sys.stdout.flush()
print('again' if core.rotate_own_output(max_bytes=1000) else 'small'); sys.stdout.flush()
'''


class AgentLog(Folder):
    """agent.log (launchd's file for the page job's output) is rotated by the page itself past its size."""

    def test_its_own_output_moves_to_a_fresh_file(self):
        os.makedirs(self.data)
        path = os.path.join(self.data, 'agent.log')
        with open(path, 'ab') as out:
            r = subprocess.run([sys.executable, '-I', '-c', AGENT_CHILD, os.path.join(HERE, '..'), self.data],
                               stdout=out, stderr=out, timeout=120)
        self.assertEqual(r.returncode, 0)
        with open(path + '.1') as f:
            old = f.read()
        with open(path) as f:
            new = f.read()
        self.assertIn('old error', old)
        self.assertNotIn('after', old)
        self.assertEqual(new.split(), ['rotated', 'new', 'error', 'after', 'small'])
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_a_file_that_is_not_its_output_is_never_moved(self):
        os.makedirs(self.data)
        path = os.path.join(self.data, 'agent.log')
        with open(path, 'w') as f:
            f.write('z' * 5000)
        self.assertFalse(core.rotate_own_output(path, max_bytes=1000))   # this test's own output is elsewhere
        self.assertFalse(os.path.exists(path + '.1'))
        self.assertFalse(core.rotate_own_output(os.path.join(self.data, 'missing.log')))


class TheDisk(Folder):
    def test_free_space_is_looked_at_most_every_30_s_on_the_monotonic_clock(self):
        clock = [100.0]
        calls = []
        usage = mock.Mock(side_effect=lambda p: calls.append(p) or shutil._ntuple_diskusage(10, 5, 123))
        with mock.patch.object(core.shutil, 'disk_usage', usage), mock.patch.dict(core._DISK, {'at': None, 'free': None}):
            self.assertEqual(core.disk_free(clock=lambda: clock[0]), 123)
            clock[0] += 10
            self.assertEqual(core.disk_free(clock=lambda: clock[0]), 123)
            self.assertEqual(len(calls), 1)
            clock[0] -= 3600                        # the monotonic clock never goes back, but a fake one may: look again
            core.disk_free(clock=lambda: clock[0])
            self.assertEqual(len(calls), 2)
            self.assertTrue(os.path.isdir(calls[0]))  # the nearest folder that exists
        with mock.patch.object(core.shutil, 'disk_usage', side_effect=OSError('gone')), \
                mock.patch.dict(core._DISK, {'at': None, 'free': None}):
            self.assertIsNone(core.disk_free(fresh=True))
            self.assertFalse(core.disk_low())        # not known is not low

    def test_low_under_200_mb(self):
        for free, low in ((core.DISK_LOW - 1, True), (core.DISK_LOW, False), (50 << 30, False)):
            with mock.patch.dict(core._DISK, {'at': time.monotonic(), 'free': free}):
                self.assertIs(core.disk_low(), low)


if __name__ == '__main__':
    unittest.main()
