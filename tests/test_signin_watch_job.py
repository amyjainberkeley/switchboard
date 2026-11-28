import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The sign-in watch's background job, as install.sh adds it and uninstall.sh removes it. Neither script is run whole
# here (tests/install-check.sh does that, with --no-agent): the watch's launcher block and job block are lifted out of
# install.sh, and the watch's block and the page's job block out of uninstall.sh, and each runs in sh with stand-ins
# for launchctl, ps, kill, sleep and the installer's say and note. Nothing reaches launchd.
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
sys.path.insert(0, ROOT)

from switchboard import engine  # noqa: E402

LABEL = 'io.github.amyjainberkeley.switchboard'
WATCH = LABEL + '.watch'
JOB_PATH = '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'


def read(name):
    with open(os.path.join(ROOT, name), encoding='utf-8') as f:
        return f.read()


def between(text, start, end):
    """From the line that starts with `start` up to (not with) the next one that starts with `end`."""
    i = text.index(start)
    return text[i:text.index(end, i + len(start))]


# The stand-ins: launchctl keeps whether the job is loaded in a file, and `print` keeps saying "loaded" for LETGO
# more looks after a bootout (macOS lets go of a job a moment after bootout returns).
STAND_INS = r'''
launchctl() {
  printf 'launchctl %s\n' "$*" >> "$CALLS"
  case "$1" in
    bootout) rm -f "$STATE/loaded"; printf '%s' "${LETGO:-0}" > "$STATE/letgo"; return 0 ;;
    print)
      n="$(cat "$STATE/letgo" 2>/dev/null || echo 0)"
      if [ "$n" -gt 0 ]; then printf '%s' $((n - 1)) > "$STATE/letgo"; return 0; fi
      [ -f "$STATE/loaded" ] ;;
    bootstrap) [ "${BOOT_RC:-0}" = 0 ] || return "$BOOT_RC"; : > "$STATE/loaded" ;;
    *) return 0 ;;
  esac
}
sleep() { printf 'sleep %s\n' "$*" >> "$CALLS"; }
say() { printf 'say %s\n' "$*"; }
note() { printf 'note %s\n' "$*"; }
'''


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-watch-job-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, 'home')
        self.data = os.path.join(self.home, '.switchboard')
        self.state = os.path.join(self.tmp, 'state')
        self.calls = os.path.join(self.tmp, 'calls')
        self.agents = os.path.join(self.home, 'Library', 'LaunchAgents')
        for d in (self.home, os.path.join(self.data, 'bin'), self.state, self.agents):
            os.makedirs(d, exist_ok=True)
        open(self.calls, 'w').close()
        self.watch_plist = os.path.join(self.agents, WATCH + '.plist')
        self.page_plist = os.path.join(self.agents, LABEL + '.plist')
        self.launcher = os.path.join(self.data, 'bin', 'switchboard-watch')

    def run_sh(self, script, **env):
        path = os.path.join(self.tmp, 'piece.sh')
        with open(path, 'w') as f:
            f.write(script)
        base = {'PATH': '/usr/bin:/bin', 'HOME': self.home, 'CALLS': self.calls, 'STATE': self.state}
        base.update(env)
        r = subprocess.run(['/bin/sh', path], env=base, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def launchctl_calls(self):
        with open(self.calls) as f:
            return [line.strip() for line in f if line.startswith('launchctl ')]


class TheInstaller(Sandbox):
    def module(self, there=True):
        """The installed copy's watch module: a made-up one that writes down that it ran, with what."""
        pkg = os.path.join(self.data, 'app', 'switchboard', 'accounts')
        os.makedirs(pkg, exist_ok=True)
        for p in (os.path.join(self.data, 'app', 'switchboard', '__init__.py'), os.path.join(pkg, '__init__.py')):
            open(p, 'w').close()
        mod = os.path.join(pkg, 'signin_watch.py')
        if there:
            with open(mod, 'w') as f:
                f.write('import os, sys\nif __name__ == "__main__":\n'
                        '    open(os.environ["RAN"], "w").write(" ".join([__name__] + sys.argv[1:]))\n')
        elif os.path.exists(mod):
            os.remove(mod)

    def install(self, agent='1', **env):
        text = read('install.sh')
        xml = next(line for line in text.splitlines() if line.startswith('xml() {'))
        launcher = between(text, "# ---- the sign-in watch's launcher", '# ---- the background jobs')
        job = between(text, "# The sign-in watch's job, right after the page's", "# The menu bar item's job runs")
        script = 'set -eu\n%s\n%s\nLABEL="%s"\nDATA="%s"\nME="gui/501"\nAGENT="%s"\nPY="%s"\nAPP_PY=""\n' \
                 'PAGE_CHECK=""\nJOB_PATH="%s"\n%s\n%s' % (STAND_INS, xml, LABEL, self.data, agent, sys.executable,
                                                         JOB_PATH, launcher, job)
        return self.run_sh(script, **env)

    def plist(self):
        with open(self.watch_plist, 'rb') as f:
            return plistlib.load(f)

    def test_the_job_has_its_label_program_keys_and_paths(self):
        self.module()
        out = self.install()
        self.assertEqual(self.plist(), {
            'Label': WATCH, 'ProgramArguments': [self.launcher], 'EnvironmentVariables': {'PATH': JOB_PATH},
            'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 10,          # no ProcessType: never throttled
            'StandardOutPath': os.path.join(self.data, 'signin-watch.out'),        # not the watch's own log, which
            'StandardErrorPath': os.path.join(self.data, 'signin-watch.out')})     # it rotates by renaming
        self.assertEqual(self.launchctl_calls(), ['launchctl bootout gui/501/%s' % WATCH,
                                                  'launchctl print gui/501/%s' % WATCH,
                                                  'launchctl bootstrap gui/501 %s' % self.watch_plist,
                                                  'launchctl print gui/501/%s' % WATCH])
        self.assertEqual(out, 'note The sign-in watch is running, and starts at every login.\n')

    def test_its_launcher_runs_the_watch_on_the_pages_python_from_the_installed_copy(self):
        self.module()
        self.install()
        ran = os.path.join(self.tmp, 'ran')
        r = subprocess.run([self.launcher], env={'PATH': '/usr/bin:/bin', 'HOME': self.home, 'RAN': ran},
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(ran) as f:
            self.assertEqual(f.read(), '__main__')          # run as the watch's own main, with no arguments
        with open(self.launcher) as f:
            text = f.read()
        self.assertIn('run_module("switchboard.accounts.signin_watch", run_name="__main__", alter_sys=True)', text)
        self.assertIn('exec "$PY" -I -B -c', text)
        self.assertIn('exec "$APP_PY" -I -B -c', text)      # Switchboard.app's Python first, as the page's launcher

    def test_its_launcher_empties_the_jobs_output_file_once_it_passes_1_mb(self):
        # launchd's output file for the job is never rotated: a start that fails at once, every 10 s, must not fill
        # the disk. launchd holds it open for appending, so emptying it under the job is safe.
        self.module()
        self.install()
        out = os.path.join(self.data, 'signin-watch.out')
        env = {'PATH': '/usr/bin:/bin', 'HOME': self.home, 'RAN': os.path.join(self.tmp, 'ran')}
        for size, after in ((2 * 1024 * 1024, 0), (1024 * 1024, 1024 * 1024), (None, None)):
            if size is None:
                os.remove(out)
            else:
                with open(out, 'wb') as f:
                    f.write(b'x' * size)
            with open(out if size is not None else os.devnull, 'ab') as f:
                r = subprocess.run([self.launcher], env=env, stdout=f, stderr=subprocess.PIPE, timeout=60)
            self.assertEqual((r.returncode, r.stderr), (0, b''), size)
            self.assertEqual(os.path.getsize(out) if os.path.exists(out) else None, after, size)

    def test_a_launcher_whose_module_went_waits_before_it_exits(self):
        self.module()
        self.install()
        self.module(there=False)
        with open(self.launcher) as f:
            text = f.read()
        guard = text[text.index('if [ ! -f'):text.index('fi\n') + 3]
        out = self.run_sh('set -eu\n' + STAND_INS + '\n(\n' + guard + ') || echo "exit $?"\n')
        self.assertEqual(out, 'exit 1\n')
        with open(self.calls) as f:
            self.assertIn('sleep 300\n', f.read())

    def test_run_by_launchd_as_its_job_with_the_module_still_gone_a_minute_later_the_job_takes_itself_out(self):
        # After an older build was put back over this one (its installer knows nothing of the watch), the job would
        # otherwise be started again every few minutes for ever.
        self.module()
        self.install()
        self.module(there=False)
        open(self.calls, 'w').close()
        with open(self.launcher) as f:
            text = f.read()
        guard = text[text.index('if [ ! -f'):text.index('fi\n') + 3]
        out = self.run_sh('set -eu\n' + STAND_INS + '\n(\n' + guard + ') || echo "exit $?"\n', XPC_SERVICE_NAME=WATCH)
        self.assertEqual(out, 'exit 1\n')
        self.assertFalse(os.path.exists(self.watch_plist))
        self.assertEqual(self.launchctl_calls(), ['launchctl bootout gui/%d/%s' % (os.getuid(), WATCH)])
        with open(self.calls) as f:
            self.assertEqual([x for x in f.read().splitlines() if x.startswith('sleep')], ['sleep 60', 'sleep 300'])

    def test_with_the_module_back_within_the_minute_or_not_run_by_launchd_the_job_stays(self):
        self.module()
        self.install()
        with open(self.launcher) as f:
            text = f.read()
        guard = text[text.index('if [ ! -f'):text.index('fi\n') + 3]
        # Not launchd's job (a copy started by hand, a test): exactly as before, the job file is never touched.
        self.module(there=False)
        open(self.calls, 'w').close()
        self.run_sh('set -eu\n' + STAND_INS + '\n(\n' + guard + ') || echo "exit $?"\n', XPC_SERVICE_NAME='other')
        self.assertTrue(os.path.exists(self.watch_plist))
        self.assertEqual(self.launchctl_calls(), [])
        # Launchd's job, and an install puts the module back during the minute: the job stays.
        stand_in_sleep = 'sleep() { printf "sleep %s\\n" "$*" >> "$CALLS"; [ "$1" != 60 ] || : > "' + \
            os.path.join(self.home, '.switchboard/app/switchboard/accounts/signin_watch.py') + '"; }\n'
        open(self.calls, 'w').close()
        self.run_sh('set -eu\n' + STAND_INS + stand_in_sleep + '\n(\n' + guard + ') || echo "exit $?"\n',
                    XPC_SERVICE_NAME=WATCH)
        self.assertTrue(os.path.exists(self.watch_plist))
        self.assertEqual(self.launchctl_calls(), [])

    def test_a_second_install_stops_the_old_watch_waits_for_macos_then_starts_the_new_one(self):
        self.module()
        self.install()
        open(self.calls, 'w').close()
        with open(self.watch_plist, 'w') as f:
            f.write('an older job file')
        out = self.install(LETGO='2')
        self.assertEqual(self.plist()['Label'], WATCH)      # written again
        self.assertEqual(self.launchctl_calls(), ['launchctl bootout gui/501/%s' % WATCH] +
                         ['launchctl print gui/501/%s' % WATCH] * 3 +       # loaded, loaded, let go
                         ['launchctl bootstrap gui/501 %s' % self.watch_plist, 'launchctl print gui/501/%s' % WATCH])
        self.assertIn('note The sign-in watch is running', out)

    def test_it_waits_as_long_as_the_old_watch_may_take_to_stop(self):
        # The watch gives a look under way up to 10 s at a stop: the installer waits up to 15 s (60 looks, 0.25 s
        # apart) for macOS to let go of the old job before it starts the new one.
        self.module()
        self.install()
        open(self.calls, 'w').close()
        self.install(LETGO='50')
        with open(self.calls) as f:
            sleeps = [line for line in f if line == 'sleep 0.25\n']
        self.assertEqual(self.launchctl_calls(), ['launchctl bootout gui/501/%s' % WATCH] +
                         ['launchctl print gui/501/%s' % WATCH] * 51 +
                         ['launchctl bootstrap gui/501 %s' % self.watch_plist, 'launchctl print gui/501/%s' % WATCH])
        self.assertEqual(len(sleeps), 50)
        open(self.calls, 'w').close()
        self.install(LETGO='1000')                          # never let go: 15 s, then it tries anyway
        self.assertEqual(self.launchctl_calls()[:62], ['launchctl bootout gui/501/%s' % WATCH] +
                         ['launchctl print gui/501/%s' % WATCH] * 60 +
                         ['launchctl bootstrap gui/501 %s' % self.watch_plist])

    def test_macos_refusing_it_is_one_line_and_the_install_goes_on(self):
        self.module()
        out = self.install(BOOT_RC='5')
        self.assertEqual(self.launchctl_calls().count('launchctl bootstrap gui/501 %s' % self.watch_plist), 5)
        self.assertEqual(out, "say Added the sign-in watch's background job, but macOS would not start it. "
                              'The page starts it again.\n')

    def test_no_agent_adds_no_job(self):
        self.module()
        out = self.install(agent='0')
        self.assertFalse(os.path.exists(self.watch_plist))
        self.assertEqual(self.launchctl_calls(), [])
        self.assertEqual(out, '')
        self.assertTrue(os.access(self.launcher, os.X_OK))  # the launcher is there to run by hand

    def test_a_copy_without_the_watch_adds_no_job_with_one_line(self):
        self.module(there=False)
        out = self.install()
        self.assertFalse(os.path.exists(self.watch_plist))
        self.assertFalse(os.path.exists(self.launcher))
        self.assertEqual(self.launchctl_calls(), [])
        self.assertEqual(out, 'note No sign-in watch: this copy of Switchboard does not have it.\n')
        self.assertEqual(self.install(agent='0'), '')       # --no-agent: nothing to say

    def test_a_copy_without_the_watch_removes_the_one_an_earlier_install_added(self):
        self.module()
        self.install()
        open(self.calls, 'w').close()
        self.module(there=False)
        out = self.install()
        self.assertFalse(os.path.exists(self.watch_plist))
        self.assertFalse(os.path.exists(self.launcher))
        self.assertEqual(self.launchctl_calls(), ['launchctl bootout gui/501/%s' % WATCH])
        self.assertEqual(out, 'say Removed the sign-in watch: this copy of Switchboard does not have it.\n')

    def test_with_no_agent_its_file_goes_and_launchd_is_not_asked(self):
        self.module()
        self.install()
        open(self.calls, 'w').close()
        self.module(there=False)
        out = self.install(agent='0')
        self.assertFalse(os.path.exists(self.watch_plist))
        self.assertEqual(self.launchctl_calls(), [])
        self.assertEqual(out, 'say Removed the sign-in watch: this copy of Switchboard does not have it.\n')

    def test_the_watch_is_added_right_after_the_page_and_before_the_menu_bar(self):
        text = read('install.sh')
        page = text.index('launchctl bootstrap "$ME" "$PLIST"')
        watch = text.index('launchctl bootstrap "$ME" "$WATCH_PLIST"')
        menu = text.index('launchctl bootstrap "$ME" "$MENUBAR_PLIST"')
        self.assertLess(page, watch)
        self.assertLess(watch, menu)


class TheUninstaller(Sandbox):
    PS = r'''
ps() {
  run='/usr/bin/python3 -I -B -c import runpy; run_module("switchboard.accounts.signin_watch")'
  printf '%s\n' "  401 $run $DATA/app" "  402 $run /elsewhere/app"
  printf '%s\n' "  403 /usr/bin/python3 -I -B -c something else $DATA/app"
}
kill() { printf 'kill %s\n' "$*" >> "$CALLS"; : > "$STATE/killed"; }
'''

    def uninstall(self, agent='1', hand_started=False):
        text = read('uninstall.sh')
        head = between(text, 'DATA="$HOME/.switchboard"', 'LINKS_ID=')
        names = between(text, 'WATCH_ID="$LABEL.watch"', 'MENUBAR_APP=')
        piece = between(text, '# The sign-in watch, before the page', '# A page started by hand')
        # with a hand-started watch, ps keeps listing it: it is asked to stop, then told
        ps = self.PS if hand_started else 'ps() { :; }\nkill() { printf "kill %s\\n" "$*" >> "$CALLS"; }\n'
        script = 'set -eu\n%s\n%s\nLABEL="%s"\n%s\nAGENT="%s"\n%s\n' % (STAND_INS, ps, LABEL, head + names, agent,
                                                                        piece)
        return self.run_sh(script)

    def jobs(self):
        for p in (self.watch_plist, self.page_plist):
            with open(p, 'w') as f:
                f.write('a job file')

    def test_the_watch_goes_before_the_page_job(self):
        self.jobs()
        out = self.uninstall()
        uid = os.getuid()
        self.assertEqual(self.launchctl_calls(), ['launchctl bootout gui/%d/%s' % (uid, WATCH),
                                                  'launchctl bootout gui/%d/%s' % (uid, LABEL)])
        self.assertFalse(os.path.exists(self.watch_plist))
        self.assertFalse(os.path.exists(self.page_plist))
        self.assertEqual(out, "Removed the sign-in watch's background job (%s).\nRemoved the background job (%s).\n"
                         % (WATCH, LABEL))

    def test_a_hand_started_watch_of_this_install_is_stopped_and_no_other(self):
        self.jobs()
        out = self.uninstall(hand_started=True)
        with open(self.calls) as f:
            kills = [line.strip() for line in f if line.startswith('kill ')]
        self.assertEqual(kills[0], 'kill -TERM 401')        # not 402 (another data folder), not 403 (not the watch)
        self.assertTrue(all(k.endswith(' 401') for k in kills), kills)
        self.assertIn('Stopped the sign-in watch.\n', out)
        self.assertLess(out.index('Stopped the sign-in watch.'), out.index('Removed the background job'))

    def test_a_hand_started_watch_has_12_s_to_stop_before_it_is_told(self):
        # It may be finishing a look for up to 10 s: TERM, then 48 looks 0.25 s apart, then KILL.
        self.jobs()
        self.uninstall(hand_started=True)
        with open(self.calls) as f:
            calls = [line.strip() for line in f if line.startswith(('kill ', 'sleep '))]
        self.assertEqual(calls, ['kill -TERM 401'] + ['sleep 0.25'] * 48 + ['kill -KILL 401'])

    def test_no_agent_leaves_launchd_alone(self):
        self.jobs()
        out = self.uninstall(agent='0')
        self.assertEqual(self.launchctl_calls(), [])
        self.assertTrue(os.path.exists(self.watch_plist))
        self.assertEqual(out, '')

    def test_in_the_whole_script_the_watch_goes_first_and_its_files_go_with_the_program(self):
        text = read('uninstall.sh')
        watch = text.index('launchctl bootout "gui/$(id -u)/$WATCH_ID"')
        self.assertLess(watch, text.index('launchctl bootout "gui/$(id -u)/$LABEL"'))
        self.assertLess(watch, text.index('page_pids() {'))
        self.assertLess(watch, text.index('stop_loop || exit 1'))
        removed = between(text, 'rm -rf "$DATA/app" "$DATA/app.new"', 'echo "Removed the program')
        for name in ('signin-watch.json', 'signin-watch.lease', 'signin-watch.out', 'signin-watch.log',
                     'signin-watch.log.1', 'signin-watch.log.2', 'signin-watch.log.3', 'signin-record.jsonl',
                     'signin-record.jsonl.1', 'signin-record.jsonl.2', 'signin-record.jsonl.3'):
            self.assertIn('"$DATA/%s"' % name, removed)


class BothScriptsAndTheLabels(unittest.TestCase):
    def test_sh_reads_both_scripts(self):
        for name in ('install.sh', 'uninstall.sh'):
            r = subprocess.run(['/bin/sh', '-n', os.path.join(ROOT, name)], capture_output=True, text=True, timeout=30)
            self.assertEqual((r.returncode, r.stderr), (0, ''), name)

    def test_the_watch_is_one_of_ours(self):
        self.assertIn(WATCH, engine.OUR_LABELS)
        tmp = tempfile.mkdtemp(prefix='switchboard-watch-labels-')
        self.addCleanup(shutil.rmtree, tmp, True)
        with open(os.path.join(tmp, WATCH + '.plist'), 'wb') as f:
            plistlib.dump({'Label': WATCH, 'ProgramArguments': ['/x/bin/claude-account-switch-watch']}, f)
        self.assertIsNone(engine.foreign_switcher(tmp))


if __name__ == '__main__':
    unittest.main()
