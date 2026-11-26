import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Starting other programs for the other assistants (switchboard/providers/run.py): a run that ends on its time limit
# and takes its children with it, a missing program, output cut at 1 MiB, the environment a child gets, a pipe whose
# program fills standard error, exec's own checks, the one line to type, a lock two processes share, how versions
# sort, and the notes folder beside the data folder.
#
# Every program here is a stand-in a test writes (fakes_providers.stand_in), never a vendor's. No real pid is asked
# about: a child is known to be gone when the file it writes to every 50 ms stops growing.
import dataclasses
import json
import os
import signal
import stat
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import core
from switchboard.providers import contract, run
import fakes_providers as fp

MIB = 1 << 20

# A grandchild that appends one byte to argv[1] every 50 ms for as long as it lives.
HEARTBEAT = 'import sys, time\nwhile True:\n    open(sys.argv[1], "a").write("x")\n    time.sleep(0.05)\n'


def _sleeper_with_child(heartbeat):
    """A stand-in that starts a grandchild (same process group, same output pipes), waits until it has written once
    (a Python started at background priority can take a while), and then sleeps."""
    return ('import os, subprocess, sys, time\n'
            'subprocess.Popen([sys.executable, "-I", "-c", %r, %r])\n'
            'end = time.time() + 30\n'
            'while time.time() < end and not os.path.exists(%r):\n'
            '    time.sleep(0.05)\n'
            'time.sleep(60)\n' % (HEARTBEAT, heartbeat, heartbeat))


def _size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


class Root(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='sb-run-')
        self.bin = os.path.join(self.root, 'bin')

    def standin(self, body, name='fake-tool'):
        return fp.stand_in(self.bin, name, body)

    def assertStops(self, heartbeat, msg):
        """The heartbeat grew once (the grandchild ran) and no longer grows (it is gone)."""
        deadline = time.monotonic() + 10
        while _size(heartbeat) == 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertGreater(_size(heartbeat), 0, 'the grandchild never ran')
        time.sleep(0.4)
        before = _size(heartbeat)
        time.sleep(0.6)
        self.assertEqual(_size(heartbeat), before, msg)


class RunProgram(Root):
    def test_a_program_past_its_limit_is_ended_and_says_timed_out(self):
        prog = self.standin('import time\ntime.sleep(60)\n')
        t0 = time.monotonic()
        ran = run.run_program([prog], timeout=1.0)
        took = time.monotonic() - t0
        self.assertTrue(ran.timed_out)
        self.assertIsNone(ran.code)
        self.assertFalse(ran.missing)
        self.assertLess(took, 6.0)

    def test_its_children_end_with_it(self):
        hb = os.path.join(self.root, 'heartbeat')
        prog = self.standin(_sleeper_with_child(hb))
        ran = run.run_program([prog], timeout=8.0)
        self.assertTrue(ran.timed_out)
        self.assertStops(hb, 'a child of the program outlived the time limit')

    def test_a_missing_program_says_missing(self):
        ran = run.run_program([os.path.join(self.root, 'no-such-program')], timeout=5.0)
        self.assertTrue(ran.missing)
        self.assertIsNone(ran.code)
        self.assertFalse(ran.timed_out)

    def test_a_missing_folder_is_not_called_a_missing_program(self):
        prog = self.standin('print("hi")\n')
        ran = run.run_program([prog], timeout=5.0, cwd=os.path.join(self.root, 'gone'))
        self.assertIsNone(ran.code)
        self.assertFalse(ran.missing)

    def test_each_output_is_cut_at_one_mebibyte_and_the_program_is_not_stalled(self):
        prog = self.standin('import os\nos.write(2, b"e" * (2 << 20))\n'
                            'import sys\nsys.stdout.write("o" * (3 << 20))\nsys.stdout.flush()\n')
        ran = run.run_program([prog], timeout=20.0)
        self.assertEqual(ran.code, 0)
        self.assertEqual(len(ran.out), MIB)
        self.assertEqual(len(ran.err), MIB)
        self.assertEqual(set(ran.out), {'o'})

    def test_input_goes_in_on_standard_input_and_words_are_never_a_shell(self):
        marker = os.path.join(self.root, 'made-by-a-shell')
        prog = self.standin('import json, sys\nprint(json.dumps({"argv": sys.argv[1:], "in": sys.stdin.read()}))\n')
        word = 'a; touch %s' % marker
        ran = run.run_program([prog, word, '$HOME'], stdin='the job\nline two', timeout=10.0)
        self.assertEqual(ran.code, 0)
        got = json.loads(ran.out)
        self.assertEqual(got['argv'], [word, '$HOME'])
        self.assertEqual(got['in'], 'the job\nline two')
        self.assertFalse(os.path.exists(marker))

    def test_no_input_reads_as_an_empty_one(self):
        prog = self.standin('import sys\nprint(len(sys.stdin.read()))\n')
        ran = run.run_program([prog], timeout=10.0)
        self.assertEqual((ran.code, ran.out.strip()), (0, '0'))

    def test_the_exit_code_comes_back(self):
        prog = self.standin('import sys\nsys.stderr.write("no")\nsys.exit(3)\n')
        ran = run.run_program([prog], timeout=10.0)
        self.assertEqual((ran.code, ran.err, ran.timed_out, ran.missing), (3, 'no', False, False))
        self.assertGreater(ran.took_s, 0)

    def test_no_environment_given_means_this_one_without_the_billing_names(self):
        prog = self.standin('import json, os\nprint(json.dumps(sorted(os.environ)))\n')
        made_up = {'CODEX_API_KEY': 'made-up-codex-key-0000', 'ANTHROPIC_API_KEY': 'made-up-anthropic-0000',
                   'SB_TEST_KEEP': 'kept'}
        with mock.patch.dict(os.environ, made_up):
            ran = run.run_program([prog], timeout=10.0)
        names = json.loads(ran.out)
        self.assertIn('SB_TEST_KEEP', names)
        self.assertNotIn('CODEX_API_KEY', names)
        self.assertNotIn('ANTHROPIC_API_KEY', names)

    def test_an_environment_given_is_the_whole_environment(self):
        prog = self.standin('import json, os\nprint(json.dumps(dict(os.environ)))\n')
        ran = run.run_program([prog], env={'PATH': '/usr/bin:/bin', 'ONLY': 'this'}, timeout=10.0)
        got = json.loads(ran.out)
        self.assertEqual(got.get('ONLY'), 'this')
        self.assertNotIn('SWITCHBOARD_HOME', got)

    def test_ctrl_c_while_it_waits_ends_the_program_and_its_children_first(self):
        hb = os.path.join(self.root, 'heartbeat')
        prog = self.standin(_sleeper_with_child(hb))

        def interrupt(signum, frame):
            raise KeyboardInterrupt

        def press_once_it_runs():
            deadline = time.monotonic() + 30
            while _size(hb) == 0 and time.monotonic() < deadline:
                time.sleep(0.05)
            os.kill(os.getpid(), signal.SIGALRM)        # this process only: the handler raises KeyboardInterrupt

        old = signal.signal(signal.SIGALRM, interrupt)
        presser = threading.Thread(target=press_once_it_runs)
        try:
            presser.start()
            with self.assertRaises(KeyboardInterrupt):
                run.run_program([prog], timeout=60.0)
        finally:
            presser.join(40)
            signal.signal(signal.SIGALRM, old)
        self.assertStops(hb, 'Ctrl-C left the program or its child running')


class OpenPipe(Root):
    def test_a_program_that_fills_standard_error_still_answers(self):
        prog = self.standin('import os, sys\nos.write(2, b"e" * (1 << 20))\n'
                            'for line in sys.stdin:\n    sys.stdout.write("pong:" + line)\n    sys.stdout.flush()\n')
        pipe = run.open_pipe([prog], env={'PATH': '/usr/bin:/bin'})
        try:
            t0 = time.monotonic()
            pipe.send('ping')
            self.assertEqual(pipe.receive(5.0), 'pong:ping')
            self.assertLess(time.monotonic() - t0, 5.0)
            pipe.send('again')
            self.assertEqual(pipe.receive(5.0), 'pong:again')
        finally:
            code = pipe.close(3.0)
        self.assertEqual(code, 0)

    def test_nothing_within_the_time_gives_none_and_close_ends_a_stuck_program(self):
        prog = self.standin('import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(60)\n')
        pipe = run.open_pipe([prog], env={'PATH': '/usr/bin:/bin'})
        t0 = time.monotonic()
        self.assertIsNone(pipe.receive(0.5))
        self.assertGreaterEqual(time.monotonic() - t0, 0.4)
        t1 = time.monotonic()
        pipe.close(0.5)
        self.assertLess(time.monotonic() - t1, 8.0)

    def test_a_line_over_the_cap_is_read_past(self):
        prog = self.standin('import sys\nsys.stdout.write("x" * 5000 + "\\n" + "short\\n")\nsys.stdout.flush()\n'
                            'import time\ntime.sleep(5)\n')
        with mock.patch.object(run, 'PIPE_LINE_MAX', 1024):
            pipe = run.open_pipe([prog], env={'PATH': '/usr/bin:/bin'})
            try:
                self.assertIsNone(pipe.receive(5.0))
                self.assertEqual(pipe.receive(5.0), 'short')
            finally:
                pipe.close(0.5)

    def test_the_end_of_the_program_gives_none_and_sending_then_is_refused(self):
        prog = self.standin('print("bye")\n')
        pipe = run.open_pipe([prog], env={'PATH': '/usr/bin:/bin'})
        self.assertEqual(pipe.receive(5.0), 'bye')
        self.assertIsNone(pipe.receive(5.0))
        time.sleep(0.2)
        with self.assertRaises(contract.ProviderError):
            for _ in range(50):         # a pipe's buffer may take a few lines before the reader's end is seen
                pipe.send('x' * 4096)
        pipe.close(1.0)

    def test_a_missing_program_raises_a_provider_error(self):
        with self.assertRaises(contract.ProviderError) as cm:
            run.open_pipe([os.path.join(self.root, 'nothing-here')])
        self.assertEqual(cm.exception.kind, 'not_installed')


class ExecProgram(Root):
    def test_a_missing_program_raises_oserror_before_anything_happens(self):
        here = os.getcwd()
        with self.assertRaises(OSError):
            run.exec_program(os.path.join(self.root, 'gone'), ['gone'], {}, self.root)
        self.assertEqual(os.getcwd(), here)

    def test_a_missing_folder_raises_oserror_and_the_folder_stays(self):
        prog = self.standin('print(1)\n')
        here = os.getcwd()
        with self.assertRaises(OSError):
            run.exec_program(prog, [prog], {}, os.path.join(self.root, 'no-folder'))
        self.assertEqual(os.getcwd(), here)

    def test_a_file_that_cannot_run_raises_oserror(self):
        path = os.path.join(self.root, 'not-a-program')
        with open(path, 'w') as f:
            f.write('nothing')
        os.chmod(path, 0o644)
        with self.assertRaises(OSError):
            run.exec_program(path, [path], {}, self.root)

    def test_in_a_child_process_it_becomes_the_program_with_its_words_folder_and_environment(self):
        out = os.path.join(self.root, 'seen.json')
        folder = os.path.join(self.root, 'my site')
        os.makedirs(folder)
        prog = self.standin('import json, os, sys\n'
                            'json.dump({"argv": sys.argv, "cwd": os.getcwd(), "env": dict(os.environ)}, '
                            'open(%r, "w"))\n' % out)
        code = ('from switchboard.providers import run\n'
                'run.exec_program(%r, [%r, "one", "two words"], {"PATH": "/usr/bin:/bin", "SB_MARK": "yes"}, %r)\n'
                % (prog, prog, folder))
        ran = fp.child_python(code, self.root, timeout=30.0)
        self.assertEqual(ran.code, 0, ran.err)
        with open(out) as f:
            seen = json.load(f)
        self.assertEqual(seen['argv'][1:], ['one', 'two words'])
        self.assertEqual(os.path.realpath(seen['cwd']), os.path.realpath(folder))
        self.assertEqual(seen['env'].get('SB_MARK'), 'yes')
        self.assertNotIn('SWITCHBOARD_HOME', seen['env'])


class OpenLink(unittest.TestCase):
    def test_a_link_goes_to_macos_open_as_a_list_with_a_short_limit(self):
        seen = []

        def fake_run(argv, stdin=None, timeout=30.0, env=None, cwd=None):
            seen.append((list(argv), timeout))
            return contract.Ran(code=0)

        url = 'codex://new?prompt=Switchboard%20note&path=%2FUsers%2Fmaya%2Fsite'
        with mock.patch.object(run, 'run_program', fake_run):
            run.open_link(url)
            run.open_link(url, app='/Applications/ChatGPT.app')
        self.assertEqual(seen[0], (['/usr/bin/open', url], 10.0))
        self.assertEqual(seen[1], (['/usr/bin/open', '-a', '/Applications/ChatGPT.app', url], 10.0))


class ChildEnv(unittest.TestCase):
    def test_every_billing_name_is_dropped_and_nothing_is_added(self):
        env = {n: 'made-up-%d' % i for i, n in enumerate(contract.BILLING_ENV)}
        env.update({'PATH': '/usr/bin', 'HOME': '/Users/maya', 'EDITOR': 'vi'})
        got = run.child_env(env)
        self.assertEqual(got, {'PATH': '/usr/bin', 'HOME': '/Users/maya', 'EDITOR': 'vi'})
        self.assertIn('CODEX_API_KEY', env)                     # the given one is not changed

    def test_a_job_keeps_only_the_allow_list(self):
        env = {'PATH': '/usr/bin', 'HOME': '/Users/maya', 'LANG': 'en_US.UTF-8', 'LC_ALL': 'en_US.UTF-8',
               'XDG_CONFIG_HOME': '/Users/maya/.config', 'CODEX_HOME': '/Users/maya/.codex-made-up',
               'DATABASE_URL': 'postgres://u:made-up@db.example/x', 'GH_PAT': 'made-up-pat',
               'SENTRY_DSN': 'https://made-up@sentry.example/1', 'CODEX_API_KEY': 'made-up-key',
               'OPENAI_API_KEY': 'made-up-key-2', 'EDITOR': 'vi'}
        got = run.child_env(env, job=True)
        self.assertEqual(sorted(got), ['CODEX_HOME', 'HOME', 'LANG', 'LC_ALL', 'PATH', 'XDG_CONFIG_HOME'])
        for gone in ('DATABASE_URL', 'GH_PAT', 'SENTRY_DSN', 'CODEX_API_KEY', 'OPENAI_API_KEY', 'EDITOR'):
            self.assertNotIn(gone, got)

    def test_none_is_an_empty_environment(self):
        self.assertEqual(run.child_env(None), {})
        self.assertEqual(run.child_env(None, job=True), {})

    def test_a_terminal_opened_from_a_claude_code_chat(self):
        # Such a terminal carries that chat's marks into every window. Claude Code started with them takes itself for
        # the chat's helper: it saves no chat (so nothing could carry it on later) and runs at that chat's effort.
        marks = {n: 'made-up' for n in contract.SESSION_ENV}
        own = {'PATH': '/usr/bin', 'CLAUDE_CONFIG_DIR': '/Users/maya/.claude-work',
               'CLAUDE_CODE_MAX_OUTPUT_TOKENS': '8000', 'EDITOR': 'vi'}
        for mark in contract.NESTED_MARKS:
            self.assertIn(mark, contract.SESSION_ENV)
        self.assertEqual(run.child_env(dict(own, **marks)), own)        # the person's own settings all go on
        for mark in contract.NESTED_MARKS:                              # either mark alone says it
            with self.subTest(mark=mark):
                self.assertEqual(run.child_env(dict(own, CLAUDE_EFFORT='xhigh', **{mark: '1'})), own)
        by_hand = dict(own, CLAUDE_EFFORT='low')                       # no mark: a name the person set stays
        self.assertEqual(run.child_env(by_hand), by_hand)
        self.assertEqual(run.child_env(dict(own, **marks), job=True), {'PATH': '/usr/bin'})

    def test_the_marks_named_are_claude_codes_own_and_no_setting(self):
        self.assertEqual(len(set(contract.SESSION_ENV)), len(contract.SESSION_ENV))
        for name in contract.SESSION_ENV:
            with self.subTest(name=name):
                self.assertTrue(name.startswith('CLAUDE'))
                self.assertNotIn(name, contract.BILLING_ENV)
        for kept in ('CLAUDE_CONFIG_DIR', 'CLAUDE_CODE_MAX_OUTPUT_TOKENS', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC'):
            self.assertNotIn(kept, contract.SESSION_ENV)


class ShellLine(unittest.TestCase):
    def test_spaces_quotes_and_dollars_are_quoted(self):
        line = run.shell_line(['/Applications/ChatGPT.app/Contents/codex', '-C', "/Users/maya/my site",
                               "it's $HOME"], '/Users/maya/my site')
        self.assertEqual(line, "cd '/Users/maya/my site' && /Applications/ChatGPT.app/Contents/codex -C "
                               "'/Users/maya/my site' 'it'\"'\"'s $HOME'")

    def test_env_u_for_each_billing_name_that_is_set(self):
        env = {'CODEX_API_KEY': 'made-up', 'OPENAI_BASE_URL': 'https://proxy.example', 'ANTHROPIC_API_KEY': '',
               'PATH': '/usr/bin'}
        line = run.shell_line(['/usr/local/bin/fake-codex', 'hi'], '/Users/maya/site', env)
        self.assertEqual(line, 'cd /Users/maya/site && env -u CODEX_API_KEY -u OPENAI_BASE_URL '
                               '/usr/local/bin/fake-codex hi')
        self.assertNotIn('made-up', line)

    def test_a_control_character_in_any_word_gives_nothing(self):
        for argv, cwd in ((['/bin/fake', 'a\x1b[31mred'], '/x'), (['/bin/fake', 'a\nb'], '/x'),
                          (['/bin/fake'], '/x\x07'), (['/bin/fa\x00ke'], '/x'), (['/bin/fake', 'a\u2028b'], '/x'),
                          (['/bin/fake', 'a\x85b'], '/x')):
            self.assertEqual(run.shell_line(argv, cwd), '', repr(argv))

    def test_it_unsets_another_chats_marks_too(self):
        env = {'CLAUDECODE': '1', 'CLAUDE_CODE_CHILD_SESSION': '1', 'CLAUDE_EFFORT': 'xhigh',
               'OPENAI_API_KEY': 'made-up', 'CLAUDE_CONFIG_DIR': '/Users/maya/.claude-work'}
        line = run.shell_line(['/usr/local/bin/claude', 'hello'], '/Users/maya/site', env)
        self.assertEqual(line, 'cd /Users/maya/site && env -u OPENAI_API_KEY -u CLAUDECODE '
                               '-u CLAUDE_CODE_CHILD_SESSION -u CLAUDE_EFFORT /usr/local/bin/claude hello')
        line = run.shell_line(['/usr/local/bin/claude', 'hello'], '/Users/maya/site', {'CLAUDE_EFFORT': 'low'})
        self.assertEqual(line, 'cd /Users/maya/site && /usr/local/bin/claude hello')


class VersionKey(unittest.TestCase):
    def test_versions_sort_with_pre_releases_first_and_unknown_first_of_all(self):
        given = ['0.169.1', '0.164.0-alpha.11.1', '0.169.0', None, 'not a version', '0.155.1',
                 '0.169.0-alpha.11.1', '0.167.0-alpha.4.1', '0.153.4']
        self.assertEqual(sorted(given, key=lambda v: run.version_key(v))[2:],
                         ['0.153.4', '0.155.1', '0.164.0-alpha.11.1', '0.167.0-alpha.4.1', '0.169.0-alpha.11.1',
                          '0.169.0', '0.169.1'])
        self.assertEqual(run.version_key(None), ())
        self.assertEqual(run.version_key('not a version'), ())

    def test_exact_keys(self):
        self.assertEqual(run.version_key('0.169.1'), (0, 169, 1, 1, 0, 0))
        self.assertEqual(run.version_key('0.164.0-alpha.11.1'), (0, 164, 0, 0, 11, 1))
        self.assertEqual(run.version_key('2.1.330 (Claude Code)'), (2, 1, 330, 1, 0, 0))
        self.assertEqual(run.version_key('codex-cli 0.169.1'), ())       # the probe's prefix is the adapter's to cut
        self.assertLess(run.version_key('2.1.309'), run.version_key('2.1.330 (Claude Code)'))


class TryLock(Root):
    HOLD = ('import os, sys, time\n'
            'from switchboard.providers import run\n'
            'with run.try_lock(%r) as held:\n'
            '    open(%r, "w").write(str(held))\n'
            '    end = time.time() + 20\n'
            '    while not os.path.exists(%r) and time.time() < end:\n'
            '        time.sleep(0.05)\n')

    def test_two_processes_one_holds_the_other_is_told_at_once(self):
        name = contract.HAND_LOCK_NAME
        mark, release = os.path.join(self.root, 'held'), os.path.join(self.root, 'release')
        result = {}
        th = threading.Thread(target=lambda: result.update(
            ran=fp.child_python(self.HOLD % (name, mark, release), self.root, timeout=40.0)))
        th.start()
        try:
            deadline = time.monotonic() + 20
            while not (os.path.exists(mark) and _size(mark)) and time.monotonic() < deadline:
                time.sleep(0.05)
            with open(mark) as f:
                self.assertEqual(f.read(), 'True')
            t0 = time.monotonic()
            with run.try_lock(name) as held:
                self.assertFalse(held)
            self.assertLess(time.monotonic() - t0, 1.0)              # it does not wait
        finally:
            open(release, 'w').close()
            th.join(30)
        self.assertEqual(result['ran'].code, 0, result['ran'].err)
        with run.try_lock(name) as held:
            self.assertTrue(held)

    def test_inside_one_process_a_second_hold_is_refused_and_the_file_is_in_the_data_folder(self):
        name = contract.ASK_LOCK_NAME
        with run.try_lock(name) as a:
            with run.try_lock(name) as b:
                self.assertEqual((a, b), (True, False))
        self.assertTrue(os.path.isfile(os.path.join(core.data_dir(), name)))
        with run.try_lock(name) as c:
            self.assertTrue(c)

    def test_a_name_that_is_a_path_is_refused(self):
        for bad in ('../x.lock', 'a/b.lock', '', '.'):
            with self.assertRaises(ValueError):
                with run.try_lock(bad):
                    pass


class NotesRoot(Root):
    def reach(self):
        reach, _ = fp.make_reach(self.root)
        return dataclasses.replace(reach, data_dir=os.path.join(self.root, 'data'))

    def test_beside_the_data_folder_and_owner_only(self):
        reach = self.reach()
        got = run.notes_root(reach)
        self.assertEqual(got, os.path.join(self.root, contract.NOTES_DIR_NAME))
        self.assertTrue(os.path.isdir(got))
        self.assertEqual(stat.S_IMODE(os.stat(got).st_mode), 0o700)
        self.assertFalse(got.startswith(reach.data_dir + os.sep))

    def test_a_wider_folder_that_was_there_is_made_owner_only(self):
        reach = self.reach()
        os.makedirs(os.path.join(self.root, contract.NOTES_DIR_NAME), mode=0o755)
        os.chmod(os.path.join(self.root, contract.NOTES_DIR_NAME), 0o755)
        got = run.notes_root(reach)
        self.assertEqual(stat.S_IMODE(os.stat(got).st_mode), 0o700)

    def test_a_data_folder_written_with_a_trailing_slash_gives_the_same_place(self):
        reach = dataclasses.replace(self.reach(), data_dir=os.path.join(self.root, 'data') + os.sep)
        self.assertEqual(run.notes_root(reach), os.path.join(self.root, contract.NOTES_DIR_NAME))


class DefaultReach(Root):
    def made_up(self):
        # Never used with its real home, environment, PATH or apps: all four are replaced at once.
        return dataclasses.replace(run.default_reach(), home=os.path.join(self.root, 'home'), env={'PATH': '/x'},
                                   path_dirs=(), app_dirs=())

    def test_it_is_a_reach_of_real_functions(self):
        reach = self.made_up()
        self.assertIsInstance(reach, contract.Reach)
        self.assertEqual(reach.data_dir, core.data_dir())
        self.assertIs(reach.run, run.run_program)
        self.assertIs(reach.pipe, run.open_pipe)
        self.assertIs(reach.execve, run.exec_program)
        self.assertIs(reach.open_url, run.open_link)
        self.assertIsInstance(reach.tty(), bool)
        self.assertNotIn('PATH', repr(reach))

    def test_path_dirs_are_absolute_in_order_without_repeats(self):
        self.assertEqual(run._path_dirs('/usr/local/bin::bin:/usr/bin:/usr/local/bin:./x:/opt/y/'),
                         ('/usr/local/bin', '/usr/bin', '/opt/y'))
        self.assertEqual(run._path_dirs(''), ())
        self.assertEqual(run._path_dirs(None), ())

    def test_alive_never_signals_a_group_and_knows_this_process(self):
        self.assertFalse(run._alive(0))
        self.assertFalse(run._alive(-1))
        self.assertFalse(run._alive('12'))
        self.assertTrue(run._alive(os.getpid()))

    def test_app_dirs_are_the_two_application_folders(self):
        home = os.path.join(self.root, 'home')
        self.assertEqual(run._app_dirs(home), ('/Applications', os.path.join(home, 'Applications')))



class ShellLineIsCheckedAsEveryPrintedLine(unittest.TestCase):
    def test_a_mark_that_reverses_or_hides_text_gives_nothing(self):
        for mark in ('\u202e', '\u200b', '\u2066', '\ufeff', '\u2061', '\U000e0041', '\ufe0f'):
            with self.subTest(mark=hex(ord(mark))):
                self.assertEqual(run.shell_line(['/bin/fake', 'go'], '/Users/maya/site' + mark + 'cod.txt'), '')
                self.assertEqual(run.shell_line(['/bin/fake', 'a' + mark], '/Users/maya/site'), '')
        self.assertEqual(run.shell_line(['/bin/fake', 'caf\u00e9'], '/Users/maya/caf\u00e9'),
                         "cd '/Users/maya/caf\u00e9' && /bin/fake 'caf\u00e9'")


if __name__ == '__main__':
    unittest.main()
