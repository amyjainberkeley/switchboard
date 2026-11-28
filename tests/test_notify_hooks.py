import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# "done" and "needs you" through Claude Code's hooks.
#
# Fakes only. Every settings file is a throwaway one under a temp HOME and CLAUDE_CONFIG_DIR; the managed settings
# folder is a temp one too. No real notification is shown: the hook program runs with SWITCHBOARD_OSASCRIPT pointing
# at a stand-in that writes its arguments to a file, and in-process sends get a fake runner. Nothing runs claude.
import contextlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import __main__ as cli
from switchboard import core, demo, notify as nt, notify_hooks as nh, server, statusline as sl
import test_app  # its sandbox and its headless-Chrome helper

FOLDER = os.path.dirname(os.path.abspath(nt.__file__))
MATCHER = 'permission_prompt|elicitation_dialog|elicitation_url_dialog|agent_needs_input'


def ups(sid='s1', pid='p1', **more):
    return dict({'session_id': sid, 'prompt_id': pid, 'cwd': '/work/my-app', 'hook_event_name': 'UserPromptSubmit',
                 'permission_mode': 'default', 'prompt': 'PROMPT-TEXT: rewrite the parser'}, **more)


def stop(sid='s1', pid='p1', **more):
    return dict({'session_id': sid, 'prompt_id': pid, 'cwd': '/work/my-app', 'hook_event_name': 'Stop',
                 'stop_hook_active': False, 'last_assistant_message': 'ANSWER-TEXT: done, here is the summary',
                 'background_tasks': [], 'session_crons': []}, **more)


def needs(kind='permission_prompt', sid='s1', **more):
    return dict({'session_id': sid, 'cwd': '/work/my-app', 'hook_event_name': 'Notification',
                 'message': 'Claude needs your permission to use Bash', 'title': 'Permission needed',
                 'notification_type': kind}, **more)


class Base(unittest.TestCase):
    """A throwaway home, data folder, Claude Code folder and managed-settings folder; the clock at Nov 14 2026 10:00."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-hooks-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, 'home')
        self.data = os.path.join(self.tmp, 'data')
        self.claude = os.path.join(self.home, '.claude')
        os.makedirs(self.claude)
        env = mock.patch.dict(os.environ, {'HOME': self.home, 'SWITCHBOARD_HOME': self.data,
                                           'CLAUDE_CONFIG_DIR': self.claude, 'TZ': 'America/Los_Angeles'})
        env.start()
        self.addCleanup(env.stop)
        for k in ('SWITCHBOARD_NOTIFY_STATE', 'SWITCHBOARD_OSASCRIPT', 'SWITCHBOARD_NOTIFY_NOW', 'SWITCHBOARD_STATE'):
            os.environ.pop(k, None)
        time.tzset()
        self.addCleanup(time.tzset)
        self.managed = os.path.join(self.tmp, 'managed')            # stands in for /Library/Application Support/ClaudeCode
        os.makedirs(self.managed)
        self.patch(nh, 'MANAGED', self.managed)
        self.compiled = []
        self.patch(sl, 'precompile', lambda *a, **k: self.compiled.append(k.get('script')) or None)
        self.path = os.path.join(self.claude, 'settings.json')
        self.now = time.mktime((2026, 11, 14, 10, 0, 0, 0, 0, -1))
        self.shown = []

    def patch(self, target, name, value):
        p = mock.patch.object(target, name, value)
        p.start()
        self.addCleanup(p.stop)

    def run_ok(self, cmd):
        self.shown.append(cmd)
        return 0

    def write(self, text, path=None, mode=0o644):
        path = path or self.path
        with open(path, 'w', newline='') as f:
            f.write(text)
        os.chmod(path, mode)

    def read(self, path=None):
        with open(path or self.path, 'rb') as f:
            return f.read().decode('utf-8')

    def turn_files(self):
        folder = os.path.join(self.data, 'turns')
        return sorted(n for n in os.listdir(folder) if not n.startswith('.')) if os.path.isdir(folder) else []


# ---------- 1. what counts as "done" and "needs you" ----------

class FromHook(Base):
    def test_a_turn_of_a_minute_or_more_is_done_and_a_shorter_one_is_not(self):
        n = nt.from_hook(stop(), started=self.now - 90, now=self.now)
        self.assertEqual((n.title, n.body, n.key, n.instance), ('Claude Code is done', 'In my-app.', 'done:s1', 'p1'))
        self.assertIsNotNone(nt.from_hook(stop(), started=self.now - 60, now=self.now))
        self.assertIsNone(nt.from_hook(stop(), started=self.now - 59.9, now=self.now))
        self.assertIsNone(nt.from_hook(stop(), started=self.now - 5, now=self.now))
        self.assertEqual(nt.LONG_TURN, 60)

    def test_with_no_start_known_it_is_done_anyway(self):
        self.assertIsNotNone(nt.from_hook(stop(), now=self.now))
        for odd in (None, 'yesterday', float('nan'), True):
            self.assertIsNotNone(nt.from_hook(stop(), started=odd, now=self.now), odd)

    def test_idle_prompt_is_not_a_done(self):
        self.assertIsNone(nt.from_hook(needs('idle_prompt', prompt_id='p1')))

    def test_nothing_for_helpers_teammates_or_workflow_agents(self):
        for ev in (stop(agent_id='agent-1', agent_type='Explore'), needs(agent_id='agent-1'),
                   needs('agent_needs_input', agent_id=''), stop(agent_id='wf-3', agent_type='')):
            self.assertIsNone(nt.from_hook(ev, started=self.now - 600, now=self.now), ev)
        # a main session started with --agent carries agent_type only: it is no helper
        self.assertIsNotNone(nt.from_hook(stop(agent_type='security-reviewer'), started=self.now - 600, now=self.now))

    def test_needs_you_is_the_four_kinds_that_wait_on_you_and_nothing_else(self):
        for kind in nt.NEEDS_YOU:
            n = nt.from_hook(needs(kind))
            said = nt.OUTSIDE.get(kind, 'Claude needs your permission to use Bash.')     # an outside text never shows
            self.assertEqual((n.title, n.body), ('Claude Code needs you', said + ' In my-app.'))
        for msg, body in (('Allow the fetch? ', 'Allow the fetch? In my-app.'), ('Open the link.', 'Open the link. In my-app.'),
                          ('', 'In my-app.')):
            self.assertEqual(nt.from_hook(needs(message=msg)).body, body)
        self.assertEqual(nt.from_hook(needs(cwd='')).body, 'Claude needs your permission to use Bash')
        self.assertEqual(set(nt.NEEDS_YOU), set(MATCHER.split('|')))
        for kind in ('idle_prompt', 'auth_success', 'auth_storage_failure', 'elicitation_complete', 'elicitation_response',
                     'agent_completed', 'quota_auto_resume_fired', 'plugin_notification', None, 5):
            self.assertIsNone(nt.from_hook(needs(kind)), kind)

    def test_paused_or_continuing_is_not_done(self):
        self.assertIsNone(nt.from_hook(stop(stop_hook_active=True), started=self.now - 600, now=self.now))
        self.assertIsNone(nt.from_hook(stop(background_tasks=[{'id': 't', 'type': 'workflow', 'status': 'running'}]),
                                       started=self.now - 600, now=self.now))

    def test_no_conversation_text_ever_and_long_text_is_cut(self):
        n = nt.from_hook(stop(), started=self.now - 90, now=self.now)
        self.assertNotIn('ANSWER-TEXT', ''.join(map(str, n)))
        long_msg = 'x\n\x07' + 'y' * 400
        nt.send(nt.from_hook(needs(message=long_msg)), run=self.run_ok, now=self.now)
        title, body = self.shown[0][-2:]
        self.assertLessEqual(len(body), 240)
        self.assertFalse(any(ord(c) < 32 for c in title + body))

    def test_the_same_turn_is_told_once(self):
        n = nt.from_hook(stop(), started=self.now - 90, now=self.now)
        self.assertTrue(nt.send(n, run=self.run_ok, now=self.now))
        self.assertFalse(nt.send(nt.from_hook(stop(), started=self.now - 95, now=self.now + 5), run=self.run_ok,
                                 now=self.now + 5))
        self.assertTrue(nt.send(nt.from_hook(stop(pid='p2'), started=self.now + 10, now=self.now + 100),
                                run=self.run_ok, now=self.now + 100))
        self.assertEqual(len(self.shown), 2)


# ---------- 2. when each turn started ----------

class Turns(Base):
    def test_a_stop_takes_its_own_prompts_start(self):
        self.assertTrue(nh.mark(ups(pid='p1'), now=self.now))
        self.assertEqual(nh.take(stop(pid='p1'), now=self.now + 70), self.now)
        self.assertIsNone(nh.take(stop(pid='p1'), now=self.now + 71))          # taken once

    def test_a_late_stop_never_takes_the_next_prompts_start(self):
        nh.mark(ups(pid='p1'), now=self.now)
        nh.mark(ups(pid='p2'), now=self.now + 100)                               # typed before p1's Stop hook ran
        self.assertEqual(nh.take(stop(pid='p1'), now=self.now + 101), self.now)
        self.assertEqual(nh.take(stop(pid='p2'), now=self.now + 104), self.now + 100)

    def test_without_a_prompt_id_the_latest_start_counts(self):
        nh.mark(ups(pid=None), now=self.now)
        self.assertEqual(nh.take(stop(pid='p9'), now=self.now + 5), self.now)    # the prompt came without its id
        nh.mark(ups(pid='p3'), now=self.now + 10)
        nh.mark(ups(pid='p4'), now=self.now + 20)
        self.assertEqual(nh.take(stop(pid=None), now=self.now + 30), self.now + 20)

    def test_background_work_keeps_the_first_start_for_the_stop_that_ends_it(self):
        work = [{'id': 'w1', 'type': 'workflow', 'status': 'running', 'name': 'build'}]
        nh.mark(ups(pid='p1'), now=self.now)
        self.assertIsNone(nh.take(stop(pid='p1', background_tasks=work), now=self.now + 20))   # paused, not done
        nh.mark(ups(pid='p2'), now=self.now + 1800)                              # the session wakes when it ends
        self.assertEqual(nh.take(stop(pid='p2'), now=self.now + 1810), self.now)  # the whole job: 30 minutes
        nh.mark(ups(pid='p3'), now=self.now + 1900)
        self.assertEqual(nh.take(stop(pid='p3'), now=self.now + 1905), self.now + 1900)

    def test_a_stop_that_continues_changes_nothing(self):
        nh.mark(ups(pid='p1'), now=self.now)
        self.assertIsNone(nh.take(stop(pid='p1', stop_hook_active=True), now=self.now + 30))
        self.assertEqual(nh.take(stop(pid='p1'), now=self.now + 90), self.now)

    def test_records_from_another_session_or_over_a_day_old_do_not_count(self):
        nh.mark(ups(sid='s1', pid='p1'), now=self.now)
        self.assertIsNone(nh.take(stop(sid='s2', pid='p1'), now=self.now + 90))
        self.assertIsNone(nh.take(stop(sid='s1', pid='p1'), now=self.now + 86400 + 1))
        nh.mark(ups(sid='s3', pid='p1'), now=self.now + 120)                      # a start dated ahead of the clock
        self.assertIsNone(nh.take(stop(sid='s3', pid='p1'), now=self.now + 60))

    def test_only_ids_and_times_are_kept_and_unsafe_ids_write_nothing(self):
        nh.mark(ups(), now=self.now)
        files = self.turn_files()
        self.assertEqual(files, ['s1.json'])
        with open(os.path.join(self.data, 'turns', 's1.json')) as f:
            text = f.read()
        self.assertNotIn('PROMPT-TEXT', text)
        self.assertEqual(json.loads(text), {'v': 1, 'prompts': {'p1': self.now}, 'paused': None})
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.data, 'turns', 's1.json')).st_mode), 0o600)
        for sid in ('../escape', '.hidden', '', None, 5, 'a/b', 'x' * 200):
            self.assertFalse(nh.mark(ups(sid=sid), now=self.now), sid)
        self.assertEqual(self.turn_files(), ['s1.json'])

    def test_old_files_are_pruned_at_most_once_an_hour(self):
        nh.mark(ups(sid='old'), now=self.now)
        os.utime(os.path.join(self.data, 'turns', 'old.json'), (self.now, self.now))
        nh.mark(ups(sid='new'), now=self.now + 86400 + 10)
        self.assertEqual(self.turn_files(), ['new.json'])
        nh.mark(ups(sid='old2'), now=self.now + 86400 + 20)
        os.utime(os.path.join(self.data, 'turns', 'old2.json'), (self.now, self.now))
        nh.mark(ups(sid='new2'), now=self.now + 86400 + 30)                       # within the hour: not looked at
        self.assertEqual(self.turn_files(), ['new.json', 'new2.json', 'old2.json'])

    def test_a_lock_held_elsewhere_never_holds_up_a_prompt(self):
        folder = os.path.join(self.data, 'turns')
        os.makedirs(folder)
        import fcntl
        with open(os.path.join(folder, '.lock'), 'a') as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            t0 = time.time()
            self.assertFalse(nh.mark(ups(), now=self.now))
            self.assertLess(time.time() - t0, 3)


# ---------- 3. the program Claude Code runs ----------

class HookProgram(Base):
    """The command exactly as it sits in settings.json, run by a shell as Claude Code runs it."""

    def setUp(self):
        super().setUp()
        bin_dir = os.path.join(self.tmp, 'bin')
        os.makedirs(bin_dir)
        self.log = os.path.join(self.tmp, 'shown.jsonl')
        self.osa = os.path.join(bin_dir, 'osascript')
        with open(self.osa, 'w') as f:
            f.write('#!%s -I\nimport json, sys\nwith open(%r, "a") as f:\n    f.write(json.dumps(sys.argv[1:]) + "\\n")\n'
                    % (sys.executable, self.log))
        os.chmod(self.osa, 0o755)
        self.cmd = nh.command(python=sys.executable)
        # "Done" turned on, as `switchboard notify done on` does: a data folder with no setting at all is a new
        # install, where a turn's end is quiet (test_notify_done_choice.py).
        os.makedirs(self.data, exist_ok=True)
        with open(os.path.join(self.data, 'settings.json'), 'w') as f:
            json.dump({'notify_done': True}, f)

    def hook(self, event, at, raw=None, cmd=None, cwd=None, **env):
        e = dict(os.environ, SWITCHBOARD_OSASCRIPT=self.osa, SWITCHBOARD_NOTIFY_NOW=repr(at), **env)
        data = raw if raw is not None else json.dumps(event).encode()
        r = subprocess.run(['/bin/sh', '-c', cmd or self.cmd], input=data, capture_output=True, env=e,
                           cwd=cwd or self.tmp, timeout=90)
        self.assertEqual((r.returncode, r.stdout), (0, b''), r.stderr)
        return r

    def notes(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return [json.loads(line) for line in f]

    def test_the_command_is_ours_runs_isolated_and_says_nothing(self):
        self.assertTrue(self.cmd.startswith(sys.executable + ' -I -S -c '), self.cmd)
        self.assertTrue(self.cmd.endswith(' || true # switchboard notify'), self.cmd)
        self.assertTrue(nh.is_ours({'type': 'command', 'command': self.cmd}))
        self.assertIn('D = "%s";' % FOLDER, self.cmd)
        r = self.hook(ups(), self.now)
        self.assertEqual(r.stderr, b'')

    def test_a_turn_under_a_minute_is_quiet_and_one_over_is_done(self):
        self.hook(ups(pid='p1'), self.now)
        self.hook(stop(pid='p1'), self.now + 5)
        self.assertEqual(self.notes(), [])
        self.hook(ups(pid='p2'), self.now + 10)
        self.hook(stop(pid='p2'), self.now + 100)
        shown = self.notes()
        self.assertEqual(len(shown), 1)
        self.assertEqual(shown[0], ['-e', 'on run argv', '-e', 'display notification (item 2 of argv) with title (item 1 of argv)',
                                    '-e', 'end run', '--', 'Claude Code is done', 'In my-app.'])

    def test_background_work_is_a_pause_and_the_stop_that_ends_it_is_done(self):
        self.hook(ups(pid='p1'), self.now)
        self.hook(stop(pid='p1', background_tasks=[{'id': 'w', 'type': 'workflow', 'status': 'running'}]), self.now + 30)
        self.assertEqual(self.notes(), [])
        self.hook(stop(pid='p1'), self.now + 900)
        self.assertEqual([n[-2] for n in self.notes()], ['Claude Code is done'])

    def test_needs_you_is_told_and_a_helpers_is_not(self):
        self.hook(needs(agent_id='agent-7'), self.now)
        self.assertEqual(self.notes(), [])
        self.hook(needs('elicitation_dialog', message='An MCP server asks for a choice'), self.now)
        self.assertEqual([n[-2:] for n in self.notes()], [['Claude Code needs you', 'An MCP server is asking you something. In my-app.']])

    def test_garbage_in_is_exit_0_with_nothing_said(self):
        for raw in (b'', b'   ', b'\x00garbage', b'{"hook_event_name": "Stop",', b'[1, 2]', b'"Stop"', b'null',
                    b'{"hook_event_name": "Stop"} extra', json.dumps({'hook_event_name': 'Stop', 'session_id': {'x': 1}}).encode(),
                    json.dumps({'hook_event_name': 'Stop', 'session_id': '../../etc'}).encode()):
            r = self.hook(None, self.now, raw=raw)
            self.assertEqual(r.stderr, b'', raw)
        self.assertEqual(self.notes(), [])

    def test_notifications_turned_off_in_switchboard_keep_it_quiet(self):
        os.makedirs(self.data, exist_ok=True)
        with open(os.path.join(self.data, 'settings.json'), 'w') as f:
            json.dump({'notify': False}, f)
        self.hook(ups(pid='p1'), self.now)
        self.hook(stop(pid='p1'), self.now + 600)
        self.hook(needs(), self.now + 601)
        self.assertEqual((self.notes(), self.turn_files()), ([], []))

    def test_files_planted_where_it_runs_are_never_imported(self):
        cwd = os.path.join(self.tmp, 'repo')
        os.makedirs(cwd)
        marker = os.path.join(self.tmp, 'planted-ran')
        for name in ('json', 'notify', 'notify_hooks', 'statusline', 'os', 'time', 'fcntl', 'subprocess'):
            with open(os.path.join(cwd, name + '.py'), 'w') as f:
                f.write('open(%r, "a").write(%r)\n' % (marker, name))
        self.hook(stop(pid='px'), self.now, cwd=cwd)
        self.assertFalse(os.path.exists(marker))
        self.assertEqual([n[-2] for n in self.notes()], ['Claude Code is done'])      # no start known: done

    def test_a_moved_install_or_a_missing_python_still_exits_0_and_says_nothing(self):
        gone = nh.command(python=sys.executable, folder=os.path.join(self.tmp, 'gone'))
        r = self.hook(stop(), self.now, cmd=gone)
        self.assertEqual(r.stderr, b'')
        self.hook(stop(), self.now, cmd=nh.command(python=os.path.join(self.tmp, 'no-python'), folder=FOLDER))
        self.assertEqual(self.notes(), [])

    def test_the_prompt_path_loads_neither_json_nor_subprocess_nor_the_rest_of_switchboard(self):
        probe = ('import sys; sys.path.append(%r); import io, notify_hooks; '
                 'notify_hooks.hook(stdin=io.BytesIO(%r), env={}, now=%r); '
                 'print(sorted(m for m in sys.modules if m in ("json", "re", "subprocess", "notify", "core") '
                 'or "switchboard" in m))' % (FOLDER, json.dumps(ups()).encode(), self.now))
        r = subprocess.run([sys.executable, '-I', '-S', '-c', probe], capture_output=True, timeout=60, env=dict(os.environ))
        self.assertEqual(r.stdout.decode().strip(), '[]', r.stderr)
        self.assertEqual(self.turn_files(), ['s1.json'])

    def test_the_check_input_proves_it_runs(self):
        self.assertIsNone(nh.verify(self.cmd))
        self.assertIn('Python', nh.verify(nh.command(python=os.path.join(self.tmp, 'no-python'), folder=FOLDER)))
        self.assertIsNotNone(nh.verify(nh.command(python=sys.executable, folder=os.path.join(self.tmp, 'gone'))))

    def test_a_folder_with_quotes_and_a_dollar_in_its_name(self):
        folder = os.path.join(self.tmp, "Ana's \"stuff\" $(touch x) `id`")
        os.makedirs(folder)
        for name in ('notify_hooks.py', 'notify.py', 'statusline.py'):
            shutil.copy(os.path.join(FOLDER, name), folder)
        cmd = nh.command(python=sys.executable, folder=folder)
        self.assertIsNone(nh.verify(cmd))
        self.assertTrue(nh._runs(cmd))
        self.hook(stop(pid='q1'), self.now, cmd=cmd)
        self.assertEqual([n[-2] for n in self.notes()], ['Claude Code is done'])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'x')))


# ---------- 4. Claude Code's settings: in and out, byte for byte ----------

ORIGINAL = ('{\n'
            '  "$schema": "https://json.schemastore.org/claude-code-settings.json",\n'
            '  "model": "claude-opus-5-5",\n'
            '  "statusLine": {"type": "command", "command": "~/.claude/mine.sh"},\n'
            '  "hooks": {\n'
            '    "PreToolUse": [\n'
            '      {"matcher": "Bash",  "hooks": [{"type": "command", "command": "~/.claude/check.sh"}]}\n'
            '    ],\n'
            '    "Stop": [{ "hooks": [ { "type": "command", "command": "say done" } ] }]\n'
            '  },\n'
            '  "env": {"A": "caf\\u00e9 é", "N": "1e3"}\n'
            '}\n')
THEIRS_PRE = '{"matcher": "Bash",  "hooks": [{"type": "command", "command": "~/.claude/check.sh"}]}'
THEIRS_STOP = '{ "hooks": [ { "type": "command", "command": "say done" } ] }'


class Settings(Base):
    def setUp(self):
        super().setUp()
        self.cmd = nh.command(python='/usr/bin/python3', folder='/opt/sb/switchboard')

    def ours(self, text):
        """{event: [our handlers]} in a file's text."""
        out = {}
        for event, groups in (json.loads(text).get('hooks') or {}).items():
            for g in groups:
                for h in g.get('hooks') or []:
                    if nh.is_ours(h):
                        out.setdefault(event, []).append(dict(h, matcher=g.get('matcher')))
        return out

    def install(self, cmd=None):
        plan = nh.install_plan(self.path, command_line=cmd or self.cmd)
        self.assertTrue(plan['ok'], plan.get('message'))
        return nh.apply(plan)

    def uninstall(self):
        plan = nh.uninstall_plan(self.path)
        self.assertTrue(plan['ok'], plan.get('message'))
        return nh.apply(plan)

    def test_the_entries_are_the_researched_ones(self):
        self.write('{"model": "x"}')
        self.install()
        self.assertEqual(json.loads(self.read())['hooks'], {
            'UserPromptSubmit': [{'hooks': [{'type': 'command', 'command': self.cmd, 'timeout': 15}]}],
            'Stop': [{'hooks': [{'type': 'command', 'command': self.cmd, 'timeout': 15, 'async': True}]}],
            'Notification': [{'matcher': MATCHER,
                              'hooks': [{'type': 'command', 'command': self.cmd, 'timeout': 15, 'async': True}]}]})

    def test_plans_change_nothing(self):
        self.write(ORIGINAL)
        before = sorted(os.listdir(self.claude)), os.path.exists(self.data), self.read()
        nh.install_plan(self.path, command_line=self.cmd)
        nh.uninstall_plan(self.path)
        nh.status(self.path)
        self.assertEqual((sorted(os.listdir(self.claude)), os.path.exists(self.data), self.read()), before)

    def test_in_and_out_gives_back_the_same_bytes_and_theirs_never_move(self):
        self.write(ORIGINAL)
        plan = nh.install_plan(self.path, command_line=self.cmd)
        self.assertTrue(plan['ok'] and plan['change'])
        self.assertEqual(plan['message'], "Switchboard's notification hooks go on.")
        self.assertTrue(all(l[0] in ' +@' or l.startswith(('---', '+++')) or l == '-    "Stop": [%s]' % THEIRS_STOP
                            for l in plan['diff'].splitlines()), plan['diff'])
        done = nh.apply(plan)
        after = self.read()
        self.assertIn(THEIRS_PRE, after)
        self.assertIn('"Stop": [%s,' % THEIRS_STOP, after)
        self.assertIn('"statusLine": {"type": "command", "command": "~/.claude/mine.sh"}', after)
        self.assertEqual(set(self.ours(after)), {'UserPromptSubmit', 'Stop', 'Notification'})
        mine = json.loads(after)
        mine['hooks'] = {k: [g for g in v if not any(nh.is_ours(h) for h in g['hooks'])] for k, v in mine['hooks'].items()}
        mine['hooks'] = {k: v for k, v in mine['hooks'].items() if v}
        self.assertEqual(mine, json.loads(ORIGINAL))
        self.assertEqual(self.read(done['backup']), ORIGINAL)
        self.assertEqual(stat.S_IMODE(os.stat(done['backup']).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o644)

        undo = nh.uninstall_plan(self.path)
        self.assertEqual(undo['message'], "Switchboard's notification hooks go off.")
        nh.apply(undo)
        self.assertEqual(self.read(), ORIGINAL)
        self.assertEqual(nh._records(), {})

    def test_installing_twice_changes_nothing_the_second_time(self):
        self.write(ORIGINAL)
        self.install()
        first = self.read()
        plan = nh.install_plan(self.path, command_line=self.cmd)
        self.assertFalse(plan['change'])
        self.assertEqual(plan['message'], "Switchboard's notification hooks are already on.")
        self.assertFalse(nh.apply(plan)['changed'])
        self.assertEqual(self.read(), first)

    def test_a_moved_install_is_updated_in_place_and_still_comes_out_exactly(self):
        self.write(ORIGINAL)
        self.install()
        moved = nh.command(python='/usr/local/bin/python3.14', folder='/elsewhere/switchboard')
        plan = nh.install_plan(self.path, command_line=moved)
        self.assertEqual((plan['change'], plan['message']), (True, "Switchboard's notification hooks are updated to run from here."))
        nh.apply(plan)
        self.assertEqual({h['command'] for hs in self.ours(self.read()).values() for h in hs}, {moved})
        self.uninstall()
        self.assertEqual(self.read(), ORIGINAL)

    def test_other_layouts_keep_their_style_and_come_back_the_same(self):
        for text in ('{"model": "x", "theme": "dark"}', '{\n\t"model": "x"\n}\n', '{}', '{}\n', ORIGINAL.replace('\n', '\r\n'),
                     '{\n  "statusLine": {"type": "command", "command": "~/.claude/mine.sh", "padding": 0}\n}\n',
                     '{\n  "hooks": {}\n}\n', '{\n  "hooks": {\n    "Stop": []\n  }\n}\n', '  {"hooks": {"Stop": []}}  ',
                     '{\n    "model": "x",\n    "hooks": {\n        "Notification": [\n            {\n                "hooks": []\n'
                     '            }\n        ]\n    }\n}'):
            with self.subTest(text=text):
                self.write(text)
                self.install()
                after = self.read()
                self.assertEqual(set(self.ours(after)), {'UserPromptSubmit', 'Stop', 'Notification'})
                if '\r\n' in text:
                    self.assertNotIn('\n', after.replace('\r\n', ''))
                if '\t' in text:
                    self.assertIn('\n\t"hooks": {\n\t\t"UserPromptSubmit": [', after)
                if text.startswith('{\n    '):
                    self.assertIn('\n        "UserPromptSubmit": [\n', after)
                self.uninstall()
                self.assertEqual(self.read(), text)

    def test_no_file_is_made_and_then_removed_and_an_empty_one_comes_back_empty(self):
        self.install()
        self.assertEqual(set(self.ours(self.read())), {'UserPromptSubmit', 'Stop', 'Notification'})
        undo = nh.uninstall_plan(self.path)
        self.assertEqual(undo['message'], "Switchboard's notification hooks go off, and the settings file it made goes too.")
        nh.apply(undo)
        self.assertFalse(os.path.exists(self.path))
        self.write('')
        self.install()
        self.uninstall()
        self.assertEqual(self.read(), '')

    def test_what_you_add_after_is_kept_when_it_comes_out(self):
        self.write(ORIGINAL)
        self.install()
        # Your editor saves the file its own way, with a hook of yours in our Notification group, and a Stop group
        # after ours.
        g = json.loads(self.read())
        g['hooks']['Stop'].append({'hooks': [{'type': 'command', 'command': 'afplay /System/Library/Sounds/Glass.aiff'}]})
        g['hooks']['Notification'][0]['hooks'].append({'type': 'command', 'command': 'say waiting'})
        self.write(json.dumps(g, indent=2) + '\n')
        self.uninstall()
        left = json.loads(self.read())
        self.assertEqual(self.ours(self.read()), {})
        self.assertEqual(left['hooks']['Stop'], json.loads(ORIGINAL)['hooks']['Stop'] +
                         [{'hooks': [{'type': 'command', 'command': 'afplay /System/Library/Sounds/Glass.aiff'}]}])
        self.assertEqual(left['hooks']['Notification'], [{'matcher': MATCHER, 'hooks': [{'type': 'command', 'command': 'say waiting'}]}])
        self.assertEqual(left['hooks']['PreToolUse'], json.loads(ORIGINAL)['hooks']['PreToolUse'])
        self.assertNotIn('UserPromptSubmit', left['hooks'])                  # made by Switchboard, empty: gone
        self.assertEqual({k: v for k, v in left.items() if k != 'hooks'},
                         {k: v for k, v in json.loads(ORIGINAL).items() if k != 'hooks'})

    def test_the_status_line_and_the_hooks_never_touch_each_other(self):
        self.write('{\n  "model": "x"\n}\n')
        line = sl.command(python='/usr/bin/python3', script='/opt/sb/switchboard/statusline.py')
        sl.apply(sl.install_plan(self.path, command_line=line))
        with_line = self.read()
        self.install()
        self.assertEqual(json.loads(self.read())['statusLine'], {'type': 'command', 'command': line, 'refreshInterval': 60})
        sl.apply(sl.uninstall_plan(self.path))                     # the status line goes first this time
        self.assertEqual(set(self.ours(self.read())), {'UserPromptSubmit', 'Stop', 'Notification'})
        self.uninstall()
        self.assertEqual(self.read(), '{\n  "model": "x"\n}\n')
        self.assertNotEqual(with_line, self.read())

    def test_a_file_the_status_line_made_goes_when_both_are_off_in_either_order(self):
        line = sl.command(python='/usr/bin/python3', script='/opt/sb/switchboard/statusline.py')
        for hooks_last in (True, False):
            with self.subTest(hooks_last=hooks_last):
                self.assertFalse(os.path.exists(self.path))
                sl.apply(sl.install_plan(self.path, command_line=line))      # there was no file: the status line made it
                self.install()
                first, then = (lambda: sl.apply(sl.uninstall_plan(self.path)), self.uninstall)
                for step in ((first, then) if hooks_last else (then, first)):
                    step()
                self.assertFalse(os.path.exists(self.path))
                self.assertEqual((nh._records(), sl._records()), ({}, {}))

    def test_broken_files_are_refused_and_left_alone(self):
        for text, why in (('{"a": 1,}', "isn't valid JSON"), ('{"a": NaN}', "isn't valid JSON"), ('[1]', "doesn't hold a JSON object"),
                          ('{"hooks": [1]}', '"hooks" is not an object'), ('{"hooks": {"Stop": {}}}', '"Stop" is not a list'),
                          ('{"hooks": {}, "hooks": {}}', '"hooks" twice'), ('{"hooks": {"Stop": [], "Stop": []}}', '"Stop" twice')):
            with self.subTest(text=text):
                self.write(text)
                for plan in (nh.install_plan(self.path, command_line=self.cmd), nh.uninstall_plan(self.path)):
                    self.assertFalse(plan['ok'])
                    self.assertIn(why, plan['message'])
                    with self.assertRaises(sl.PlanError):
                        nh.apply(plan)
                self.assertEqual(self.read(), text)
                self.assertIsNone(nh.status(self.path)['in'])
        with open(self.path, 'wb') as f:
            f.write(b'\xef\xbb\xbf{}')
        self.assertFalse(nh.install_plan(self.path, command_line=self.cmd)['ok'])

    def test_a_file_changed_after_the_plan_is_not_overwritten(self):
        self.write(ORIGINAL)
        plan = nh.install_plan(self.path, command_line=self.cmd)
        changed = ORIGINAL.replace('claude-opus-5-5', 'claude-sonnet-5')
        self.write(changed)
        with self.assertRaises(sl.PlanError) as e:
            nh.apply(plan)
        self.assertIn('changed just now', str(e.exception))
        self.assertEqual((self.read(), nh._records()), (changed, {}))

    def test_a_read_only_file_is_left_alone(self):
        self.write(ORIGINAL, mode=0o444)
        plan = nh.install_plan(self.path, command_line=self.cmd)
        with self.assertRaises(sl.PlanError) as e:
            nh.apply(plan)
        self.assertIn('read-only', str(e.exception))
        self.assertEqual((self.read(), nh._records()), (ORIGINAL, {}))

    def test_a_symlinked_file_stays_a_symlink_and_a_link_to_nowhere_is_refused(self):
        real = os.path.join(self.tmp, 'dotfiles', 'claude-settings.json')
        os.makedirs(os.path.dirname(real))
        self.write(ORIGINAL, path=real)
        os.symlink(real, self.path)
        self.install()
        self.assertTrue(os.path.islink(self.path))
        self.assertIn('switchboard notify', self.read(real))
        self.uninstall()
        self.assertEqual((os.path.islink(self.path), self.read(real)), (True, ORIGINAL))
        os.remove(self.path)
        os.symlink(os.path.join(self.tmp, 'nowhere', 'settings.json'), self.path)
        plan = nh.install_plan(self.path, command_line=self.cmd)
        self.assertFalse(plan['ok'])
        self.assertIn("isn't there", plan['message'])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'nowhere')))

    def test_only_handlers_that_end_with_the_mark_are_ours(self):
        for c in (self.cmd, nh.command(), 'anything at all # switchboard notify', 'x # switchboard notify  '):
            self.assertTrue(nh.is_ours({'type': 'command', 'command': c}), c)
        for c in ('say done', 'echo "# switchboard notify" > /dev/null', '# switchboard statusline'):
            self.assertFalse(nh.is_ours({'type': 'command', 'command': c}), c)
        for v in (None, 'x', {'type': 'command'}, {'command': 5}, []):
            self.assertFalse(nh.is_ours(v))

    def test_status_says_in_out_working_and_whether_hooks_run_at_all(self):
        self.assertEqual(nh.status(self.path), {'in': False, 'works': False, 'off_by': None, 'path': self.path})
        here = nh.command(python=sys.executable)
        self.install(here)
        self.assertEqual(nh.status(self.path)['in'], True)
        self.assertEqual(nh.status(self.path)['works'], True)
        self.install(nh.command(python=os.path.join(self.tmp, 'gone', 'python3')))
        self.assertEqual((nh.status(self.path)['in'], nh.status(self.path)['works']), (True, False))
        self.install(nh.command(python=sys.executable, folder=os.path.join(self.tmp, 'gone')))
        self.assertFalse(nh.status(self.path)['works'])
        self.install(here)
        d = json.loads(self.read())
        del d['hooks']['UserPromptSubmit']                          # one taken out by hand: not on
        self.write(json.dumps(d))
        self.assertFalse(nh.status(self.path)['in'])
        d['disableAllHooks'] = True
        self.write(json.dumps(d))
        self.assertEqual(nh.status(self.path)['off_by'], 'disableAllHooks')

    def test_managed_settings_can_stop_them_too(self):
        self.write('{}')
        for name, body, want in (('managed-settings.json', {'disableAllHooks': True}, 'disableAllHooks'),
                                 ('managed-settings.json', {'allowManagedHooksOnly': True}, 'allowManagedHooksOnly'),
                                 ('managed-settings.json', {'disableAllHooks': False}, None),
                                 ('managed-settings.json', '{not json', None)):
            with self.subTest(body=body):
                with open(os.path.join(self.managed, name), 'w') as f:
                    f.write(body if isinstance(body, str) else json.dumps(body))
                self.assertEqual(nh.status(self.path)['off_by'], want)
        os.remove(os.path.join(self.managed, 'managed-settings.json'))
        os.makedirs(os.path.join(self.managed, 'managed-settings.d'))
        with open(os.path.join(self.managed, 'managed-settings.d', '10-hooks.json'), 'w') as f:
            json.dump({'disableAllHooks': True}, f)
        self.assertEqual(nh.status(self.path)['off_by'], 'disableAllHooks')
        self.assertEqual(nh.off_line('disableAllHooks'), "Done and needs you are off: Claude Code's disableAllHooks is on.")

    def test_the_real_settings_file_is_never_the_target(self):
        real = os.path.join(_isolate.REAL_HOME, '.claude', 'settings.json')
        self.assertNotEqual(os.path.realpath(nh.settings_path()), os.path.realpath(real))
        self.assertTrue(nh.record_path().startswith(self.data))


# ---------- 5. the page's Notifications switch, the terminal and the uninstaller ----------

class SwitchBase(test_app.Sandbox):
    """The server with a fake engine (test_app's sandbox), a throwaway managed-settings folder, and no bytecode
    written (precompile only noted)."""

    def setUp(self):
        super().setUp()
        self.managed = os.path.join(self.tmp, 'managed')
        os.makedirs(self.managed)
        self.patch(nh, 'MANAGED', self.managed)
        self.path = os.path.join(self.claude, 'settings.json')
        self.compiled = []
        self.patch(sl, 'precompile', lambda *a, **k: self.compiled.append(k.get('script')) or None)

    def read(self):
        with open(self.path, 'rb') as f:
            return f.read()

    def mac(self):
        return server.mac_facts()


class Switch(SwitchBase):
    """The page's Notifications switch: POST /api/notify, Switchboard's setting, and the facts the page draws."""

    def test_off_until_turned_on_and_switchboards_own_notes_stay_on(self):
        self.assertEqual(server.helper_settings()['notify'], True)       # kept, switched, near a limit, plug in
        m = self.mac()
        self.assertEqual((m['notify'], m['notify_hooks'], m['hooks_off']), (False, False, None))

    def test_on_puts_them_in_and_off_takes_exactly_them_out(self):
        with open(self.path, 'w') as f:
            f.write(ORIGINAL)
        before = self.read()
        r = server.act('/api/notify', {'on': True})
        self.assertTrue(r['changed'])
        self.assertTrue(core.settings()['notify'])
        self.assertEqual((self.mac()['notify'], self.mac()['notify_hooks']), (True, True))
        self.assertEqual(nh.status()['works'], True)
        self.assertIn('Notifications: on', [e['text'] for e in core.load()['events']])
        self.assertEqual(sorted(os.path.basename(p) for p in self.compiled), ['notify.py', 'notify_hooks.py', 'statusline.py'])
        server.act('/api/notify', {'on': False})
        self.assertEqual(self.read(), before)
        self.assertEqual((core.settings()['notify'], self.mac()['notify'], self.mac()['notify_hooks']), (False, False, False))

    def test_a_settings_file_that_is_not_json_refuses_on_and_changes_nothing(self):
        with open(self.path, 'w') as f:
            f.write('{"model": "x",}')
        with self.assertRaises(core.SwitchError) as e:
            server.act('/api/notify', {'on': True})
        self.assertIn("isn't valid JSON", str(e.exception))
        self.assertNotIn('notify', core.settings())
        self.assertEqual(self.read(), b'{"model": "x",}')
        self.assertEqual(self.mac()['notify_hooks'], None)
        self.assertEqual(self.mac()['notify'], True)                # unknown hooks: the switch shows your setting

    def test_off_always_works_even_when_the_file_is_read_only_and_the_hooks_go_quiet(self):
        server.act('/api/notify', {'on': True})
        os.chmod(self.path, 0o444)
        self.addCleanup(os.chmod, self.path, 0o644)
        r = server.act('/api/notify', {'on': False})
        self.assertFalse(r['changed'])
        self.assertIn('read-only', r['left'])
        self.assertFalse(core.settings()['notify'])
        self.assertTrue(nh.status()['in'])
        shown = []
        with mock.patch.object(nt, '_run', lambda cmd: shown.append(cmd) or 0):
            nh.hook(stdin=io.BytesIO(json.dumps(stop()).encode()), env={}, now=time.time())
        self.assertEqual(shown, [])

    def test_a_command_that_does_not_run_is_never_written(self):
        broken = nh.command(python=os.path.join(self.tmp, 'no-python'))
        with mock.patch.object(nh, 'command', lambda python=None, folder=None: broken):
            with self.assertRaises(core.SwitchError) as e:
                server.act('/api/notify', {'on': True})
        self.assertIn("didn't run", str(e.exception))
        self.assertFalse(os.path.exists(self.path))
        self.assertNotIn('notify', core.settings())

    def test_disable_all_hooks_is_said_in_one_plain_line(self):
        with open(self.path, 'w') as f:
            f.write('{"disableAllHooks": true}')
        r = server.act('/api/notify', {'on': True})
        self.assertEqual(r['off_by'], 'disableAllHooks')
        self.assertEqual(self.mac()['hooks_off'], 'disableAllHooks')
        self.assertTrue(json.loads(self.read())['disableAllHooks'])

    def test_the_page_state_carries_the_switch(self):
        self.three()
        server.tick()
        s = server.view()
        self.assertEqual({k: s['mac'][k] for k in ('notify', 'notify_hooks', 'hooks_off')},
                         {'notify': False, 'notify_hooks': False, 'hooks_off': None})


class Terminal(SwitchBase):
    def say(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cli.main(['notify'] + list(args))
        return rc, out.getvalue()

    def test_show_on_and_off(self):
        rc, text = self.say()
        self.assertEqual(rc, 0)
        self.assertEqual(text.splitlines()[:2], ['Notifications: off', 'Notification hooks in Claude Code: off'])
        self.assertIn('Settings file: %s' % self.path, text)
        rc, text = self.say('on')
        self.assertEqual(rc, 0)
        lines = text.splitlines()
        self.assertEqual(lines[0], "Switchboard's notification hooks go on.")
        self.assertTrue(any(l.startswith('+') and '"UserPromptSubmit"' in l for l in lines), text)
        self.assertEqual(lines[-1], 'Notifications: on')
        self.assertTrue(nh.status()['in'])
        rc, text = self.say()
        self.assertEqual(text.splitlines()[:2], ['Notifications: on', 'Notification hooks in Claude Code: on'])
        rc, text = self.say('off')
        self.assertEqual((rc, text.splitlines()[0], text.splitlines()[-1]),
                         (0, "Switchboard's notification hooks go off, and the settings file it made goes too.", 'Notifications: off'))
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(self.say('sideways'), (2, 'switchboard notify [on|off]\nswitchboard notify done on|off\n'))

    def test_show_says_when_claude_code_runs_no_hooks(self):
        with open(self.path, 'w') as f:
            f.write('{"disableAllHooks": true}')
        rc, text = self.say()
        self.assertIn("Done and needs you are off: Claude Code's disableAllHooks is on.", text.splitlines())

    def test_a_refusal_prints_no_change_that_did_not_happen(self):
        broken = nh.command(python=os.path.join(self.tmp, 'no-python'))
        out = io.StringIO()
        with mock.patch.object(nh, 'command', lambda python=None, folder=None: broken), contextlib.redirect_stdout(out):
            with self.assertRaises(core.SwitchError) as e:
                cli.main(['notify', 'on'])
        self.assertIn("didn't run", str(e.exception))
        self.assertEqual(out.getvalue(), '')
        self.assertFalse(os.path.exists(self.path))
        rc, text = self.say('on')                               # and once it can run, it says what it did
        self.assertEqual(text.splitlines()[0], "Switchboard's notification hooks go on.")
        rc, text = self.say('on')
        self.assertEqual(text.splitlines(), ["Switchboard's notification hooks are already on.", 'Notifications: on'])


class Uninstaller(SwitchBase):
    def test_tidy_takes_them_out_and_says_so(self):
        with open(self.path, 'w') as f:
            f.write(ORIGINAL)
        before = self.read()
        server.act('/api/notify', {'on': True})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.tidy()
        self.assertIn('Notification hooks: taken out.', out.getvalue().splitlines())
        self.assertEqual(self.read(), before)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.tidy()                                                    # nothing left: nothing said
        self.assertNotIn('Notification hooks', out.getvalue())

    def test_the_uninstaller_script_says_what_tidy_puts_back(self):
        with open(os.path.join(HERE, '..', 'uninstall.sh')) as f:
            text = f.read()
        self.assertTrue('its notification hooks' in text, 'uninstall.sh does not name the notification hooks')


# ---------- 6. the page's row ----------

@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class NotificationsRowInThePage(test_app.ChromePage):
    ROW = """
      location.hash = '#settings'; await wait(900);               // Settings › While Claude works
      const row = $('#row-notify');
      const sw = row.querySelector('.sw');
      done({checked: sw ? sw.getAttribute('aria-checked') : null, aside: (row.querySelector('.r-extra .hint') || {}).textContent || null});
"""

    def mac(self, **m):
        demo._ready()
        demo.FACTS['mac'] = dict(demo.FACTS['mac'], **m)

    def test_the_row_shows_the_switch_and_says_when_claude_code_runs_no_hooks(self):
        self.mac(notify=False, notify_hooks=False, hooks_off=None)
        self.assertEqual(self.run_page(self.ROW), {'checked': 'false', 'aside': None})
        self.mac(notify=True, notify_hooks=True, hooks_off='disableAllHooks')
        self.assertEqual(self.run_page(self.ROW), {'checked': 'true',
                                                   'aside': 'Done and needs you are off: Claude Code’s disableAllHooks is on.'})

    def test_the_switch_posts_on(self):
        self.mac(notify=False, notify_hooks=False, hooks_off=None)
        sent = self.run_page("""
      location.hash = '#settings'; await wait(900);
      $('#row-notify .sw').click(); await wait(500);
      done(sent);
""")
        self.assertEqual(sent, [['/api/notify', {'on': True}]])


if __name__ == '__main__':
    unittest.main()
