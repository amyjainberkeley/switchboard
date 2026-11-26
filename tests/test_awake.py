import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# Tests for keeping the Mac awake.
# Nothing here starts the real caffeinate or runs pmset or ioreg: those come from
# recorded outputs and a fake process. Transcripts are made in a throwaway folder.
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import awake

# Recorded on a MacBook (`pmset -g batt`, `ioreg -r -k AppleClamshellState -d 1`), ids changed.
PMSET_AC = "Now drawing from 'AC Power'\n -InternalBattery-0 (id=1234567)\t70%; charging; 2:05 remaining present: true\n"
PMSET_LOW = ("Now drawing from 'Battery Power'\n"
             " -InternalBattery-0 (id=1234567)\t18%; discharging; 1:02 remaining present: true\n")
PMSET_HALF = ("Now drawing from 'Battery Power'\n"
              " -InternalBattery-0 (id=1234567)\t52%; discharging; 4:40 remaining present: true\n")
PMSET_DESKTOP = "Now drawing from 'AC Power'\n"
IOREG = ('+-o IOPMrootDomain  <class IOPMrootDomain, id 0x100000101, registered, matched, active, busy 0 (0 ms), '
         'retain 40>\n    {\n      "AppleClamshellCausesSleep" = Yes\n      "AppleClamshellState" = %s\n    }\n')
IOREG_OPEN, IOREG_SHUT = IOREG % 'No', IOREG % 'Yes'


def entry(kind, **more):
    return json.dumps(dict({'type': kind, 'sessionId': 's1'}, **more), separators=(',', ':')) + '\n'


def assistant(stop, blocks=('tool_use',)):
    return entry('assistant', message={'role': 'assistant', 'stop_reason': stop,
                                       'content': [{'type': b} for b in blocks]})


def user(content):
    return entry('user', message={'role': 'user', 'content': content})


TOOL_RUNNING = [user('fix the bug'), entry('api-request', querySource='sdk'), assistant('tool_use', ('thinking',)),
                assistant('tool_use')]
WAITING_FOR_REPLY = TOOL_RUNNING + [user([{'type': 'tool_result', 'content': 'ok'}]), entry('api-request')]
FINISHED = WAITING_FOR_REPLY + [assistant('end_turn', ('text',)), entry('system', subtype='stop_hook_summary'),
                                entry('last-prompt'), entry('ai-title', aiTitle='x')]


class FakeProc(object):
    started = []

    def __init__(self, args, **kw):
        self.args, self.kw, self.returncode = args, kw, None
        self.terminated = False
        FakeProc.started.append(self)

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated, self.returncode = True, -15

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-awake-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, 'projects')
        os.makedirs(self.root)
        env = mock.patch.dict(os.environ, {'HOME': os.path.join(self.tmp, 'home'),
                                           'CLAUDE_CONFIG_DIR': os.path.join(self.tmp, 'claude')})
        env.start()
        self.addCleanup(env.stop)
        self.now = time.time()
        self.outputs = {'pmset': PMSET_AC, 'ioreg': IOREG_OPEN}
        self.calls = []
        FakeProc.started = []

    def fake_run(self, cmd, timeout=5):
        self.calls.append(cmd)
        if cmd == awake.PMSET:
            return self.outputs['pmset']
        if cmd == awake.IOREG:
            return self.outputs['ioreg']
        raise AssertionError('unexpected command %r' % (cmd,))

    def helper(self):
        return awake.Awake(run=self.fake_run, popen=FakeProc, clock=lambda: self.now, root=self.root, pid=4242)

    def transcript(self, rel, lines, age):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            f.writelines(lines)
        self.age(path, age)
        return path

    def age(self, path, seconds):
        t = self.now - seconds
        os.utime(path, (t, t))


class Power(Base):
    def test_battery_and_power_source(self):
        self.assertEqual(awake.parse_batt(PMSET_AC), (True, 70))
        self.assertEqual(awake.parse_batt(PMSET_LOW), (False, 18))
        self.assertEqual(awake.parse_batt(PMSET_DESKTOP), (True, None))
        self.assertEqual(awake.parse_batt("Now drawing from 'UPS Power'\n"), (False, None))
        self.assertEqual(awake.parse_batt(''), (None, None))

    def test_lid(self):
        self.assertIs(awake.parse_clamshell(IOREG_OPEN), False)
        self.assertIs(awake.parse_clamshell(IOREG_SHUT), True)
        self.assertIsNone(awake.parse_clamshell(''))           # a Mac with no lid

    def test_commands_are_absolute_and_never_a_shell(self):
        self.assertEqual(awake.PMSET[0], '/usr/bin/pmset')
        self.assertEqual(awake.IOREG[0], '/usr/sbin/ioreg')
        self.assertEqual(awake.CAFFEINATE, '/usr/bin/caffeinate')
        with mock.patch.object(awake.subprocess, 'run', side_effect=OSError('nope')):
            self.assertEqual(awake.run_cmd(['x']), '')

    def test_status_has_the_promised_fields_and_says_the_limits(self):
        a = self.helper()
        st = a.status()
        self.assertEqual(set(st), {'working', 'holding', 'on_ac', 'lid_closed', 'battery_pct', 'reason', 'limits'})
        self.assertEqual((st['on_ac'], st['battery_pct'], st['lid_closed'], st['limits']), (True, 70, False, []))
        self.outputs.update(pmset=PMSET_HALF, ioreg=IOREG_SHUT)
        self.assertEqual(a.status()['limits'], ['On battery with the lid shut, the Mac sleeps no matter what.'])
        self.outputs.update(ioreg=IOREG_OPEN)
        self.assertIn('-s counts only on AC power', a.status()['limits'][0])


class TurnState(Base):
    def test_open_while_a_tool_runs_or_a_reply_is_awaited(self):
        self.assertEqual(awake.turn_state(self.transcript('p/a.jsonl', TOOL_RUNNING, 0)), awake.OPEN)
        self.assertEqual(awake.turn_state(self.transcript('p/b.jsonl', WAITING_FOR_REPLY, 0)), awake.OPEN)
        self.assertEqual(awake.turn_state(self.transcript('p/c.jsonl', [assistant('pause_turn', ('text',))], 0)),
                         awake.OPEN)
        self.assertEqual(awake.turn_state(self.transcript('p/d.jsonl', [assistant(None)], 0)), awake.OPEN)

    def test_closed_once_the_turn_ends(self):
        self.assertEqual(awake.turn_state(self.transcript('p/a.jsonl', FINISHED, 0)), awake.CLOSED)
        for last in (user([{'type': 'tool_result', 'content': 'done'}]),            # e.g. a subagent's last call
                     user([{'type': 'text', 'text': '[Request interrupted by user]'}]),
                     user('<command-name>/model</command-name>'),
                     entry('system', subtype='api_error'),
                     assistant('stop_sequence', ('text',)), assistant('max_tokens', ('text',)),
                     assistant(None, ('text',))):
            self.assertEqual(awake.turn_state(self.transcript('p/b.jsonl', TOOL_RUNNING + [last], 0)), awake.CLOSED,
                             last)

    def test_a_half_written_last_line_is_skipped(self):
        path = self.transcript('p/a.jsonl', WAITING_FOR_REPLY + ['{"type":"assistant","message":{"stop_re'], 0)
        self.assertEqual(awake.turn_state(path), awake.OPEN)

    def test_text_that_looks_like_an_entry_does_not_fool_it(self):
        sneaky = user('please print {"type":"api-request"} and {"type":"assistant"}')
        self.assertEqual(awake.turn_state(self.transcript('p/a.jsonl', TOOL_RUNNING + [sneaky], 0)), awake.CLOSED)

    def test_only_the_end_of_a_big_file_is_read(self):
        filler = entry('attachment', blob='x' * 5000)
        path = self.transcript('p/a.jsonl', TOOL_RUNNING + [filler] * 100, 0)
        self.assertEqual(awake.turn_state(path, tail=64 * 1024), awake.UNKNOWN)
        self.assertEqual(awake.turn_state(path, tail=1024 * 1024), awake.OPEN)
        # The cut lands inside an entry that looks like an open request: that partial line is not used.
        cut = [assistant('end_turn', ('text',)), entry('api-request', pad='y' * 3000)]
        path = self.transcript('p/b.jsonl', cut, 0)
        self.assertEqual(awake.turn_state(path, tail=1000), awake.UNKNOWN)

    def test_missing_or_empty(self):
        self.assertEqual(awake.turn_state(os.path.join(self.tmp, 'none.jsonl')), awake.UNKNOWN)
        self.assertEqual(awake.turn_state(self.transcript('p/e.jsonl', [], 0)), awake.UNKNOWN)


class Finding(Base):
    def layout(self):
        self.main = self.transcript('proj/s1.jsonl', FINISHED, 30)
        self.sub = self.transcript('proj/s1/subagents/agent-a.jsonl', FINISHED, 40)
        self.wf = self.transcript('proj/s1/subagents/workflows/wf_1/agent-b.jsonl', FINISHED, 50)
        self.cold_main = self.transcript('other/s2.jsonl', FINISHED, 86400)
        self.cold_wf = self.transcript('other/s2/subagents/workflows/wf_9/agent-c.jsonl', FINISHED, 86400)
        self.junk = self.transcript('proj/s1/tool-results/x.txt', ['x'], 0)

    def test_finds_main_subagent_and_workflow_transcripts(self):
        self.layout()
        t = awake.Transcripts(self.root)
        got = t.recent(self.now, 3600)
        self.assertEqual([p for _, p in got], [self.main, self.sub, self.wf])
        self.assertAlmostEqual(got[0][0], self.now - 30, places=3)

    def test_between_full_scans_it_still_sees_new_and_recent_writes(self):
        self.layout()
        t = awake.Transcripts(self.root)
        t.recent(self.now, 60)                                   # first look: everything
        self.age(self.wf, 1)                                     # a recent file written again
        new = self.transcript('proj/s1/subagents/workflows/wf_2/agent-d.jsonl', TOOL_RUNNING, 2)
        self.age(self.cold_main, 3)                              # an old chat picked up again
        self.age(self.cold_wf, 4)                                # an old agent file written again
        got = [p for _, p in t.recent(self.now + 10, 15)]
        self.assertEqual(got, [self.wf, new, self.cold_main])   # the cold agent file waits for the full scan
        got = [p for _, p in t.recent(self.now + awake.FULL_SCAN_EVERY + 1, 700)]
        self.assertIn(self.cold_wf, got)

    def test_deleted_files_and_folders_are_forgotten(self):
        self.layout()
        t = awake.Transcripts(self.root)
        t.recent(self.now, 3600)
        os.remove(self.sub)
        shutil.rmtree(os.path.join(self.root, 'proj', 's1', 'subagents', 'workflows'))
        self.assertEqual([p for _, p in t.recent(self.now + 5, 3600)], [self.main])
        self.assertNotIn(self.wf, t.times)

    def test_no_projects_folder(self):
        self.assertEqual(awake.Transcripts(os.path.join(self.tmp, 'missing')).recent(self.now, 60), [])

    def test_default_root_follows_claude_config_dir(self):
        self.assertEqual(awake.projects_root(), os.path.join(self.tmp, 'claude', 'projects'))


class Working(Base):
    def test_nothing_written(self):
        working, reason = self.helper().working()
        self.assertFalse(working)
        self.assertIn('No Claude Code chat', reason)

    def test_fresh_write_means_working(self):
        self.transcript('proj/s1.jsonl', FINISHED, 60)
        self.assertEqual(self.helper().working()[0], True)

    def test_finished_and_quiet_for_two_minutes_means_idle(self):
        self.transcript('proj/s1.jsonl', FINISHED, awake.FRESH + 1)
        working, reason = self.helper().working()
        self.assertFalse(working)
        self.assertIn('finished', reason)

    def test_a_long_tool_or_a_slow_reply_keeps_it_working_up_to_the_cap(self):
        for lines in (TOOL_RUNNING, WAITING_FOR_REPLY):
            path = self.transcript('proj/s1.jsonl', lines, 5 * 60)
            self.assertTrue(self.helper().working()[0])
            self.age(path, awake.CAP + 1)
            working, reason = self.helper().working()
            self.assertFalse(working, reason)

    def test_an_open_subagent_counts_too(self):
        self.transcript('proj/s1.jsonl', FINISHED, 20 * 60)
        self.transcript('proj/s1/subagents/agent-a.jsonl', TOOL_RUNNING, 4 * 60)
        self.assertTrue(self.helper().working()[0])

    def test_reasons_never_name_a_file_or_project(self):
        self.transcript('secret-project/s1.jsonl', TOOL_RUNNING, 5 * 60)
        a = self.helper()
        for age in (10, 5 * 60, awake.CAP + 5):
            self.age(os.path.join(self.root, 'secret-project', 's1.jsonl'), age)
            a.transcripts.full_at = None
            reason = a.working()[1]
            self.assertNotIn('secret', reason)
            self.assertNotIn(self.tmp, reason)

    def test_an_unchanged_transcript_is_read_once(self):
        self.transcript('proj/s1.jsonl', TOOL_RUNNING, 5 * 60)
        a = self.helper()
        with mock.patch.object(awake, 'turn_state', wraps=awake.turn_state) as reads:
            a.working()
            a.working()
            self.assertEqual(reads.call_count, 1)


class Holding(Base):
    def test_holds_caffeinate_for_our_own_pid_while_working_and_lets_go_two_minutes_after(self):
        path = self.transcript('proj/s1.jsonl', FINISHED, 0)       # the turn just ended
        a = self.helper()
        self.assertTrue(a.tick()['holding'])
        self.assertEqual(len(FakeProc.started), 1)
        proc = FakeProc.started[0]
        self.assertEqual(proc.args, ['/usr/bin/caffeinate', '-i', '-s', '-w', '4242'])
        self.assertEqual(proc.kw.get('stdin'), subprocess.DEVNULL)
        self.assertNotIn('shell', proc.kw)
        for later in (60, awake.FRESH - 1):
            self.now = os.stat(path).st_mtime + later
            self.assertTrue(a.tick()['holding'])
        self.assertEqual(len(FakeProc.started), 1)                 # one hold, not one per check
        self.now = os.stat(path).st_mtime + awake.FRESH + 1
        st = a.tick()
        self.assertFalse(st['holding'])
        self.assertTrue(proc.terminated)

    def test_a_long_tool_run_is_held_until_it_counts_as_stuck(self):
        path = self.transcript('proj/s1.jsonl', TOOL_RUNNING, 0)
        a = self.helper()
        for later in (5 * 60, 10 * 60, awake.CAP - 1):
            self.now = os.stat(path).st_mtime + later
            self.assertTrue(a.tick()['holding'], later)
        self.now = os.stat(path).st_mtime + awake.CAP + 1
        self.assertFalse(a.tick()['holding'])

    def test_a_hold_that_died_is_started_again(self):
        self.transcript('proj/s1.jsonl', FINISHED, 0)
        a = self.helper()
        a.tick()
        FakeProc.started[0].returncode = 1                          # someone killed it
        self.assertTrue(a.tick()['holding'])
        self.assertEqual(len(FakeProc.started), 2)

    def test_no_caffeinate_is_no_crash(self):
        self.transcript('proj/s1.jsonl', FINISHED, 0)
        a = awake.Awake(run=self.fake_run, popen=mock.Mock(side_effect=OSError('missing')),
                        clock=lambda: self.now, root=self.root)
        self.assertFalse(a.tick()['holding'])

    def test_the_loop_checks_until_told_to_stop_and_always_lets_go(self):
        self.transcript('proj/s1.jsonl', FINISHED, 0)
        a = self.helper()
        stop, seen = threading.Event(), []
        boom = {'n': 0}
        real_tick = a.tick

        def tick(now=None):
            boom['n'] += 1
            if boom['n'] == 2:
                raise RuntimeError('one bad check')
            return real_tick(now)
        a.tick = tick

        def on_tick(st):
            seen.append(st)
            if len(seen) == 3:
                stop.set()
        a.run_loop(poll_seconds=0, stop=stop, on_tick=on_tick)
        self.assertEqual(len(seen), 3)
        self.assertEqual(seen[1], {'error': 'one bad check'})
        self.assertTrue(FakeProc.started[0].terminated)
        self.assertFalse(a.holding())


class Warnings(Base):
    def st(self, **kw):
        return dict({'working': True, 'holding': True, 'on_ac': False, 'lid_closed': False, 'battery_pct': 50}, **kw)

    def test_low_battery_while_working_warns_once_per_half_hour(self):
        a = self.helper()
        self.assertTrue(a.should_warn(self.st(battery_pct=18), now=1000))
        self.assertEqual(a.warning, 'Battery at 18% while Claude Code is working.')
        self.assertFalse(a.should_warn(self.st(battery_pct=17), now=1000 + 29 * 60))
        self.assertTrue(a.should_warn(self.st(battery_pct=12), now=1000 + 30 * 60))

    def test_lid_shut_on_battery_while_working(self):
        a = self.helper()
        self.assertTrue(a.should_warn(self.st(lid_closed=True, battery_pct=90), now=1))
        self.assertIn('lid is shut', a.warning)

    def test_no_warning_when_there_is_nothing_to_say(self):
        a = self.helper()
        for st in (self.st(on_ac=True, battery_pct=5), self.st(battery_pct=25), self.st(on_ac=None, battery_pct=5),
                   self.st(working=False, holding=False, battery_pct=5),
                   self.st(working=False, holding=False, lid_closed=True), self.st(battery_pct=None)):
            self.assertFalse(a.should_warn(st, now=1), st)

    def test_holding_alone_counts_for_low_battery(self):
        self.assertTrue(self.helper().should_warn(self.st(working=False, battery_pct=10), now=1))

    def test_it_reads_the_power_itself_when_not_given_a_status(self):
        self.outputs['pmset'] = PMSET_LOW
        self.transcript('proj/s1.jsonl', FINISHED, 0)
        a = self.helper()
        self.assertTrue(a.should_warn())
        self.assertIn(awake.PMSET, self.calls)


class Once(Base):
    def test_once_prints_the_status_and_holds_nothing(self):
        self.transcript('proj/s1.jsonl', TOOL_RUNNING, 5 * 60)
        made = self.helper()
        out = io.StringIO()
        with mock.patch.object(awake, 'Awake', lambda: made), redirect_stdout(out):
            self.assertEqual(awake.main(['--once']), 0)
        text = out.getvalue()
        self.assertIn('working:  yes. A turn is still open', text)
        self.assertIn('holding:  no', text)
        self.assertIn('power:    AC power, battery 70%, lid open', text)
        self.assertEqual(FakeProc.started, [])
        out = io.StringIO()
        with mock.patch.object(awake, 'Awake', lambda: made), redirect_stdout(out):
            awake.main(['--once', '--json'])
        self.assertEqual(json.loads(out.getvalue())['battery_pct'], 70)

    def test_without_once_it_only_explains(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(awake.main([]), 2)
        self.assertIn('--once', out.getvalue())


if __name__ == '__main__':
    unittest.main()
