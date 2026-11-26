import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The suite's last test (the name sorts last, and discover runs files in name order).
#
# Fails when anything in this run wrote outside SWITCHBOARD_HOME and the temp folder, ran a command no
# test may run, or changed this Mac's real ~/.claude/settings.json or anything in the real
# ~/.switchboard. Only sizes and change times are compared; no file is read.
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import core


class RealFoldersUntouched(unittest.TestCase):
    def test_the_data_folder_is_a_throwaway(self):
        data = os.path.realpath(core.data_dir())
        self.assertFalse(data == os.path.realpath(_isolate.REAL_DATA) or data.startswith(_isolate.REAL_DATA + os.sep))
        self.assertTrue(data.startswith(os.path.realpath(tempfile.gettempdir())), data)

    def test_core_refuses_the_real_folder_inside_a_test_run(self):
        saved = os.environ.pop('SWITCHBOARD_HOME')
        try:
            with self.assertRaises(RuntimeError):
                core.data_dir()
        finally:
            os.environ['SWITCHBOARD_HOME'] = saved

    def test_a_running_switchboards_own_files_are_not_counted(self):
        # this Mac's own Switchboard rewrites its account store, its loops' files and their temporary names while a
        # run goes on; anything else that moves in ~/.switchboard still counts
        live = ['accounts.json', 'accounts.json.3', '.accounts.json.ab12cd34.tmp', 'accounts.lock', 'auto-heartbeat.json',
                'auto.lease', 'auto-state.json', 'auto.log.1', 'auto-restarted.json', 'switch-journal.json',
                'usage.json.123.456.tmp', 'keychain-argv.json', 'keychain-argv.json.4242.tmp', 'keychain.gate',
                'consume/0a08.lock', 'recovery/manifest.json', 'owners.json', 'serve.out', 'serve.out.1', 'logins/3f2a',
                'week.json', '.week.json.ab12cd34.tmp', '.state.json.ab12cd34.tmp', '.notify.json.4242.123.tmp']
        kept = ['settings.json', '.settings.json.ab12cd34.tmp', 'bin/switchboard', 'app/source', 'app/switchboard/core.py']
        before = {'settings': None, 'data': dict((p, (1, 1)) for p in live + kept)}
        after = {'settings': None, 'data': dict((p, (2, 2)) for p in live + kept)}
        self.assertEqual(_isolate.changes(before, after), ['~/.switchboard/%s: (1, 1) -> (2, 2)' % p for p in sorted(kept)])

    def test_the_watch_blocks_a_write_outside_the_temp_folders(self):
        target = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'must-not-exist')   # the repo, not temp
        before = list(_isolate.VIOLATIONS)
        with self.assertRaises(PermissionError):
            open(target, 'w').close()
        with self.assertRaises(PermissionError):
            os.mkdir(target)
        self.assertFalse(os.path.exists(target))
        del _isolate.VIOLATIONS[len(before):]            # this test's own two, on purpose

    def test_a_temp_symlink_to_a_place_outside_is_not_a_way_round(self):
        outside = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'must-not-exist-2')   # the repo
        link = os.path.join(tempfile.mkdtemp(prefix='switchboard-link-'), 'looks-temp.json')
        os.symlink(outside, link)
        before = list(_isolate.VIOLATIONS)
        with self.assertRaises(PermissionError):
            open(link, 'w').close()
        self.assertFalse(os.path.exists(outside))
        del _isolate.VIOLATIONS[len(before):]

    def test_a_shell_command_naming_the_real_folders_is_refused(self):
        import subprocess
        before = list(_isolate.VIOLATIONS)
        for cmd in ('touch ~/.switchboard/x', 'ls $HOME/.claude', 'ls %s' % _isolate.REAL_DATA,
                    'cat %s' % os.path.join(_isolate.REAL_HOME, '.claude', 'settings.json')):
            with self.subTest(cmd=cmd), self.assertRaises(PermissionError):
                subprocess.run(['/bin/sh', '-c', cmd], capture_output=True)
        del _isolate.VIOLATIONS[len(before):]

    def test_a_child_given_the_real_home_is_refused(self):
        import subprocess
        before = list(_isolate.VIOLATIONS)
        claude = os.path.join(_isolate.REAL_HOME, '.claude')
        for env in ({'HOME': _isolate.REAL_HOME}, {'CLAUDE_CONFIG_DIR': claude},
                    {'SWITCHBOARD_HOME': os.path.join(_isolate.REAL_DATA, 'x')}):
            with self.subTest(env=env), self.assertRaises(PermissionError):
                subprocess.run(['/usr/bin/true'], env=env)
        subprocess.run(['/usr/bin/true'], env={'HOME': tempfile.mkdtemp(prefix='switchboard-ok-')})   # a temp one is fine
        del _isolate.VIOLATIONS[len(before):]

    def test_settings_moved_by_claude_code_is_a_note_not_a_failure(self):
        # A Claude Code window may write ~/.claude/settings.json mid-run.
        before = {'settings': (100, 1), 'data': {'bin/switchboard': (5, 1)}}
        after = {'settings': (120, 2), 'data': {'bin/switchboard': (5, 1)}}
        self.assertEqual(_isolate.changes(before, after), [])
        self.assertEqual(len(_isolate.outside(before, after)), 1)
        after['data']['bin/switchboard'] = (6, 2)                # the real data folder still counts
        self.assertEqual(len(_isolate.changes(before, after)), 1)

    def test_a_live_switchboards_own_writes_are_not_the_runs(self):
        # A Switchboard running for real rewrites these besides the files already listed (its auto-switch loop,
        # its page's agent log, the engine's own store).
        live = ('engine-auto.json', 'engine-auto.err', 'engine-auto.pid', 'agent.log', 'menubar.log',
                'engine/cache/.usage.lock', 'engine/cache/usage.json', 'engine/sequence.json', 'menubar/Switchboard.app',
                'engine', 'menubar')     # a folder's own time moves when a file in it is replaced (sequence.json)
        before = {'settings': (100, 1), 'data': dict({k: (5, 1) for k in live}, **{'app/switchboard/core.py': (9, 1)})}
        after = {'settings': (100, 1), 'data': dict({k: (6, 2) for k in live}, **{'app/switchboard/core.py': (9, 1)})}
        self.assertEqual(_isolate.changes(before, after), [])
        after['data']['app/switchboard/core.py'] = (10, 2)        # the program itself still counts
        self.assertEqual(_isolate.changes(before, after), ['~/.switchboard/app/switchboard/core.py: (9, 1) -> (10, 2)'])

    def test_nothing_in_this_run_touched_the_real_folders(self):
        self.assertEqual(_isolate.problems(), [])


if __name__ == '__main__':
    unittest.main()
