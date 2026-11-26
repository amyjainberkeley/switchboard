import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Notifications never double, a burst is at most three and one summary, a cut-off osascript never
# shows a note twice, and Script Editor's switch is said plainly.
# No notification is shown: every send gets a stand-in runner. The memory file lives in a throwaway folder.
import errno
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import notify as nt


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-notify-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = mock.patch.dict(os.environ, {'HOME': os.path.join(self.tmp, 'home'),
                                           'SWITCHBOARD_HOME': os.path.join(self.tmp, 'data')})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop('SWITCHBOARD_NOTIFY_STATE', None)
        p = mock.patch.dict(nt._HERE, {})
        p.start()
        self.addCleanup(p.stop)
        self.shown = []
        self.now = 1.8e9

    def ok(self, cmd):
        self.shown.append(cmd)
        return 0

    def titles(self):
        return [c[-2] for c in self.shown]

    def store(self):
        with open(nt.state_path()) as f:
            return json.load(f)


def done(i):
    return nt.from_hook({'hook_event_name': 'Stop', 'session_id': 'session-%d' % i, 'prompt_id': 'p%d' % i,
                         'cwd': '/Users/maya/project-%d' % i})


class ABurst(Base):
    """20 events in 60 s are at most 3 notifications and one summary, and a restart in the middle doubles none."""

    def test_twenty_in_a_minute_are_three_then_one_summary(self):
        for i in range(20):
            nt.send(done(i), run=self.ok, now=self.now + 3 * i)
        self.assertEqual(len(self.shown), 3)
        self.assertEqual(self.store()['held']['n'], 17)
        self.assertFalse(nt.flush_held(run=self.ok, now=self.now + 120))         # no room yet: still held
        self.assertTrue(nt.flush_held(run=self.ok, now=self.now + nt.RATE_WINDOW + 1))
        self.assertEqual(len(self.shown), 4)
        self.assertEqual(self.shown[-1][-2:], ['Switchboard', '17 more things happened. The last: Claude Code is done.'])
        self.assertFalse(nt.flush_held(run=self.ok, now=self.now + nt.RATE_WINDOW + 2))   # said once
        for i in range(20):                                                       # and none of them comes back alone
            nt.send(done(i), run=self.ok, now=self.now + nt.RATE_WINDOW + 10 + i)
        self.assertEqual(len(self.shown), 4)

    def test_a_restart_in_the_middle_doubles_nothing(self):
        for i in range(10):
            nt.send(done(i), run=self.ok, now=self.now + i)
        # A new process (the page restarted, or a hook in another window) reads the same memory.
        code = textwrap.dedent('''
            import json, sys
            sys.path.insert(0, sys.argv[1])
            from switchboard import notify as nt
            shown = []
            run = lambda cmd: shown.append(cmd) or 0
            now = float(sys.argv[2])
            def done(i):
                return nt.from_hook({'hook_event_name': 'Stop', 'session_id': 'session-%d' % i, 'prompt_id': 'p%d' % i,
                                     'cwd': '/Users/maya/project-%d' % i})
            for i in range(20):
                nt.send(done(i), run=run, now=now + 10 + i)
            nt.flush_held(run=run, now=now + 30)
            nt.flush_held(run=run, now=now + 400)
            print(json.dumps([c[-2:] for c in shown]))
        ''')
        r = subprocess.run([sys.executable, '-I', '-c', code, os.path.join(HERE, '..'), repr(self.now)],
                           capture_output=True, text=True, timeout=120, env=dict(os.environ))
        self.assertEqual(r.returncode, 0, r.stderr)
        later = json.loads(r.stdout)
        self.assertEqual(len(self.shown), 3)
        self.assertEqual(later, [['Switchboard', '17 more things happened. The last: Claude Code is done.']])

    def test_a_note_already_shown_is_not_held_or_counted(self):
        note = nt.plug_in('Battery at 18%.')
        self.assertTrue(nt.send(note, run=self.ok, now=self.now))
        for t in range(1, 10):
            self.assertFalse(nt.send(note, run=self.ok, now=self.now + t))
        self.assertEqual(self.store()['held']['n'], 0)

    def test_a_clock_set_back_never_holds_notes_back_for_an_hour(self):
        for i in range(3):
            nt.send(done(i), run=self.ok, now=self.now)
        self.assertFalse(nt.send(done(9), run=self.ok, now=self.now + 10))      # held
        # an hour back: the three are dated ahead now, so they count for nothing: room again, not a wait of an hour
        self.assertTrue(nt.send(done(10), run=self.ok, now=self.now - 3600))
        self.assertEqual(len(self.shown), 4)


class NeverTwice(Base):
    """a note is remembered before osascript runs, so a cut-off run (a timeout, the hook's own alarm) never shows
    it again; a run that said no is tried again."""

    def test_a_timed_out_osascript_is_not_tried_again(self):
        note = nt.switched('a', 'b', at=5)
        self.assertFalse(nt.send(note, run=lambda cmd: nt.TIMEOUT, now=self.now))
        self.assertFalse(nt.send(note, run=self.ok, now=self.now + 60))
        self.assertEqual(self.shown, [])

    def test_a_process_killed_while_osascript_runs_does_not_show_it_again(self):
        class Killed(BaseException):
            pass
        note = nt.switched('a', 'b', at=6)

        def killed(cmd):
            raise Killed()                                    # SIGALRM in the hook, a kill of the page
        with self.assertRaises(Killed):
            nt.send(note, run=killed, now=self.now)
        self.assertFalse(nt.send(note, run=self.ok, now=self.now + 1))

    def test_a_refusal_is_forgotten_with_the_earlier_note_of_that_kind_kept(self):
        first = nt.nearing('atlas', 'seven_day', {'used_percentage': 92, 'resets_at': self.now + 86400}, now=self.now)
        self.assertTrue(nt.send(first, run=self.ok, now=self.now))
        nxt = nt.nearing('atlas', 'seven_day', {'used_percentage': 91, 'resets_at': self.now + 8 * 86400},
                         now=self.now + 7 * 86400)
        self.assertFalse(nt.send(nxt, run=lambda cmd: 1, now=self.now + 7 * 86400))
        self.assertEqual(self.store()['seen']['near:atlas:seven_day']['instance'], self.now + 86400)
        self.assertTrue(nt.send(nxt, run=self.ok, now=self.now + 7 * 86400 + 1))

    def test_a_memory_that_cannot_be_written_shows_a_note_once_per_process(self):
        data = os.path.join(self.tmp, 'data')
        os.makedirs(data)
        os.chmod(data, 0o500)                                 # nothing can be made there (a full disk does the same)
        self.addCleanup(os.chmod, data, 0o700)
        note = nt.disk_full()
        self.assertTrue(nt.send(note, run=self.ok, now=self.now))
        self.assertFalse(nt.send(note, run=self.ok, now=self.now + 60))
        self.assertEqual(self.titles(), ['Your disk is almost full'])

    def test_a_memory_file_broken_in_odd_ways_is_started_again(self):
        path = nt.state_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        for junk in ({'seen': {'plug-in': ['x']}}, {'seen': {'plug-in': {'at': 'yesterday'}}},
                     {'seen': {}, 'recent': ['x', True, None], 'held': {'n': 'many', 'titles': 'a'}}, {'held': []}):
            with self.subTest(junk=junk):
                with open(path, 'w') as f:
                    json.dump(junk, f)
                self.shown = []
                self.assertTrue(nt.send(nt.plug_in('x'), run=self.ok, now=self.now))
                self.assertEqual(len(self.shown), 1)

    def test_a_full_disk_while_saving_leaves_no_half_file(self):
        nt.send(nt.plug_in('x'), run=self.ok, now=self.now)
        before = self.store()
        with mock.patch.object(nt.json, 'dump', side_effect=OSError(errno.ENOSPC, 'full')):
            nt.send(nt.switched('a', 'b', at=7), run=self.ok, now=self.now + 10)
        self.assertEqual(self.store(), before)
        folder = os.path.dirname(nt.state_path())
        self.assertEqual([n for n in os.listdir(folder) if n.endswith('.tmp')], [])


class ScriptEditor(Base):
    """osascript says it worked even when Script Editor's notifications are off: the page sends a test and asks."""

    def test_the_test_is_never_held_or_remembered_and_says_each_outcome_plainly(self):
        for i in range(5):                                    # a burst first: the test still goes
            nt.send(done(i), run=self.ok, now=time.time())
        self.assertEqual(nt.send_test(run=self.ok), (True, 'Sent. Did a notification appear?'))
        self.assertEqual(self.shown[-1][-2:], [nt.TEST_TITLE, nt.TEST_BODY])
        self.assertEqual(nt.send_test(run=self.ok)[0], True)  # and again: a test is never "already shown"
        for code, words in ((1, 'ended with 1'), (None, "couldn't start osascript"), (nt.TIMEOUT, 'took too long')):
            shown, line = nt.send_test(run=lambda cmd, c=code: c)
            self.assertFalse(shown)
            self.assertIn(words, line)
        self.assertIn('System Settings › Notifications', nt.SCRIPT_EDITOR_OFF)
        self.assertIn('Script Editor', nt.SCRIPT_EDITOR_OFF)

    def test_the_runner_tells_a_timeout_from_a_failure(self):
        with mock.patch.object(nt.subprocess, 'run', side_effect=subprocess.TimeoutExpired('osascript', 10)):
            self.assertEqual(nt._run(['osascript']), nt.TIMEOUT)
        with mock.patch.object(nt.subprocess, 'run', side_effect=FileNotFoundError('osascript')):
            self.assertIsNone(nt._run(['osascript']))

    def test_the_disk_note_comes_at_most_every_six_hours(self):
        note = nt.disk_full()
        self.assertTrue(nt.send(note, run=self.ok, now=self.now))
        self.assertFalse(nt.send(note, run=self.ok, now=self.now + 5 * 3600))
        self.assertTrue(nt.send(note, run=self.ok, now=self.now + 6 * 3600 + 1))


if __name__ == '__main__':
    unittest.main()
