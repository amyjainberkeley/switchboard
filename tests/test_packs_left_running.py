import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# claude-mem's installer left running, and two edges of claude-mem's undo.
#
# The fixtures are test_packs_install's own (a throwaway home, fake node, npx, bun, uv and claude-mem, a fake worker,
# claude-mem's made-up package on 127.0.0.1). A run left going is stood in for by a process this test starts and ends
# itself. Nothing real is installed, and nothing leaves the Mac.
import os
import subprocess
import sys
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import claudecode, demo
from switchboard.core import SwitchError
from switchboard.packs import claudemem_install as CMI
from switchboard.packs import common as C
from switchboard.packs import install as I
from switchboard.packs import runner as R
import test_app  # its headless Chrome page; its tests run from its own file
import test_packs_install as TP  # its fixtures; its tests run from its own file
from test_packs_install_safety import Died, changed

PACKAGE = os.path.join('packs', 'claude-mem', 'claude-mem-15.18.3.tgz')     # under the data folder


def stand_in(test, argv):
    """A stand-in for claude-mem's installer (or its repair) left running when Switchboard stopped: a process in a
    session of its own, as npx is, whose command line holds the run's own arguments, the checked package file among
    them. It only waits; the test ends it."""
    p = subprocess.Popen(['/usr/bin/python3', '-IB', '-c', 'import time; time.sleep(300)'] + list(argv[1:]),
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    test.addCleanup(end, p)
    return p


def end(p):
    """End a stand-in this test started (its own Popen, so its own pid), and wait for it."""
    if p.poll() is None:
        p.terminate()
    p.wait()


def stops_while_it_runs(test, got, what):
    """runner.run as it goes when Switchboard stops during claude-mem's `what` (install or repair): that command
    starts (a stand-in), the runner's hook is given its pid, and then nothing more happens in this process. Every
    other command (the package's download) runs as usual."""
    real = R.run

    def run(argv, env, *a, **kw):
        if os.path.basename(argv[0]) != 'npx' or argv[3:4] != [what]:
            return real(argv, env, *a, **kw)
        got.update(p=stand_in(test, argv), argv=list(argv), env=dict(env))
        if kw.get('on_start') is not None:
            kw['on_start'](got['p'].pid)
        raise Died('stopped')
    return run


def waits(what, pid):
    return "claude-mem's %s is still running (pid %d). Try again once it has finished." % (what, pid)


def worker_down(mac, wait=10):
    """Stop the fake worker and wait until it no longer answers."""
    mac.stop_worker()
    t0 = time.time()
    while CMI.health(mac.port, 0.5)['ok'] and time.time() - t0 < wait:
        time.sleep(0.1)


# ---------------------------------------------------------------- the runner gives the pid as soon as it starts

class TheRunnerSaysWhenItStarted(unittest.TestCase):
    """runner.run's on_start: told the command's pid once it has started. When it fails, the command is ended, so no
    run goes on that its caller could not note down."""

    def setUp(self):
        self.mac = TP.Mac(self, tools=('node',))
        self.node = os.path.join(self.mac.bin, 'node')
        self.env = R.environment([self.mac.bin, '/usr/bin', '/bin'], {}, self.mac.environ, self.mac.home)

    def test_it_is_given_the_pid_of_the_command_while_it_runs(self):
        seen = []
        r = R.run([self.node, '--version'], self.env,
                  on_start=lambda pid: seen.append((pid, os.getpgid(pid), pid in R.RUNNING)))
        self.assertTrue(r.ok, r.out)
        self.assertEqual(len(seen), 1)
        pid, group, listed = seen[0]
        self.assertEqual(group, pid)                    # its own group: the run and what it starts, by one number
        self.assertTrue(listed)

    def test_a_hook_that_fails_ends_the_command_and_the_error_goes_on(self):
        self.mac.cfg['sleep'] = 30
        self.mac.save_cfg()
        started = []

        def cannot_note(pid):
            started.append(pid)
            raise C.Refused("Switchboard's record of its pack changes can't be written.")
        t0 = time.time()
        with self.assertRaises(C.Refused):
            R.run([self.node, '--version'], self.env, on_start=cannot_note)
        self.assertLess(time.time() - t0, 15)
        with self.assertRaises(ProcessLookupError):
            os.kill(started[0], 0)                      # ended, and waited for
        self.assertNotIn(started[0], R.RUNNING)


# ---------------------------------------------------------------- an installer left running

class TellingTheRunApart(unittest.TestCase):
    """installer_running: the pid in the record is alive and is still that run (its start time is the one noted as it
    started, or its command line names the package file it was given). A bare pid is never trusted: macOS gives
    numbers out again."""

    def setUp(self):
        self.mac = TP.Mac(self)
        self.pkg = os.path.join(self.mac.data, PACKAGE)

    def test_by_its_command_line(self):
        p = stand_in(self, ['npx', '--yes', 'file:' + self.pkg, 'install'])     # (npx is given a file: spec)
        gone = subprocess.Popen(['/usr/bin/true'])
        gone.wait()

        def rec(pid, **kw):
            return {'run': dict({'pid': pid, 'what': 'install', 'package': self.pkg}, **kw)}
        self.assertTrue(CMI.installer_running(rec(p.pid)))
        self.assertFalse(CMI.installer_running(rec(os.getpid())))     # alive, but another program: a number reused
        self.assertFalse(CMI.installer_running(rec(gone.pid)))        # ended
        for bad in (None, {}, {'run': None}, {'run': 'x'}, rec(True), rec('%d' % p.pid), rec(1), rec(0), rec(-1),
                    rec(p.pid, package=None), rec(p.pid, package='')):
            with self.subTest(rec=bad):
                self.assertFalse(CMI.installer_running(bad))
        end(p)
        self.assertFalse(CMI.installer_running(rec(p.pid)))

    def test_by_the_start_time_noted_as_it_started(self):
        p = stand_in(self, ['npx', 'npm', 'exec', '***', 'install'])  # as npm's own title can read: no file named
        ctx = {'rec': {}, 'state_path': C.default_state_path()}
        CMI._noting(ctx, 'install', self.pkg)(p.pid)                    # the runner's hook
        run = ctx['rec']['run']
        self.assertEqual((run['pid'], run['what'], run['package']), (p.pid, 'install', self.pkg))
        self.assertTrue(run['started'])
        self.assertEqual(CMI.record()['run'], run)                     # in the record as it starts
        self.assertTrue(CMI.installer_running(ctx['rec']))
        self.assertFalse(CMI.installer_running({'run': dict(run, pid=os.getpid())}))     # another process
        end(p)
        self.assertFalse(CMI.installer_running(ctx['rec']))


class AnInstallerLeftRunning(unittest.TestCase):
    """Switchboard stops while claude-mem's installer runs on in its own session. Off, Repair and On wait for it,
    with one line naming its pid, and the record stays; once it has ended, Off takes out everything it wrote."""

    def setUp(self):
        self.mac = TP.Mac(self, settings=TP.USER_SETTINGS, cfg={'rc': True})
        claudecode.KW.update(claude_dir=self.mac.cd, home=self.mac.home, environ=self.mac.environ,
                             path=self.mac.environ['PATH'], battery=(True, 100), free=10 ** 12)
        self.addCleanup(claudecode.KW.clear)
        self.start = TP.tree(self.mac.home)

    def left_running(self):
        """Steps 1 and 2, then its installer starts, and Switchboard stops while it runs."""
        ctx = I._ctx('claude-mem', R.new_log('claude-mem'), {'cap': None, 'consent': 'plan'})
        CMI.step_runtimes(ctx)
        CMI.step_settings(ctx)
        got = {}
        with mock.patch.object(R, 'run', stops_while_it_runs(self, got, 'install')):
            with self.assertRaises(Died):
                CMI.step_install(ctx)
        self.assertEqual(got['argv'][2:4], ['file:' + os.path.join(self.mac.data, PACKAGE), 'install'])
        return got

    def test_off_repair_and_on_wait_while_it_runs_then_off_takes_out_all_it_wrote(self):
        got = self.left_running()
        line = waits('installer', got['p'].pid)
        rec = CMI.record()
        before = TP.tree(self.mac.home)
        with self.assertRaises(C.Refused) as e:                       # Off (plan_remove, then remove)
            self.mac.off()
        self.assertEqual(str(e.exception), line)
        self.assertEqual(CMI.record(), rec)                           # the record stays
        with self.assertRaises(C.Refused) as e:
            I.remove('claude-mem')
        self.assertEqual(str(e.exception), line)
        with self.assertRaises(SwitchError) as e:                     # the switch
            claudecode.turn('claudemem', on=False)
        self.assertEqual(str(e.exception), line)
        with self.assertRaises(SwitchError) as e:                     # Repair
            claudecode.act('claudemem', {'do': 'repair'})
        self.assertEqual(str(e.exception), line)
        with self.assertRaises(SwitchError) as e:                     # turning it on again
            claudecode.turn('claudemem', on=True)
        self.assertEqual(str(e.exception), line)
        left = []                                                     # the uninstaller's tidy
        said = claudecode.undo_all(left)
        self.assertIn('claude-mem: left as it is (%s)' % line, said)
        self.assertIn('claude-mem', left)
        self.assertEqual(TP.tree(self.mac.home), before)              # nothing was taken out or started
        self.assertEqual(CMI.record(), rec)
        self.assertEqual(I.JOBS, {})
        # It ends, having written all it writes (the same command, run to its end), and Off takes all of it out.
        end(got['p'])
        r = R.run(got['argv'], got['env'])
        self.assertTrue(r.ok, r.out)
        self.assertTrue(os.path.isfile(os.path.join(self.mac.home, '.local', 'bin', 'claude-mem')))
        self.assertIn('enabledPlugins', TP.json.loads(self.mac.read(os.path.join(self.mac.cd, 'settings.json'))))
        lines = self.mac.off()
        self.assertIn("claude-mem's installer wrote ~/.claude/settings.json whole while Switchboard was stopped, so "
                      "its spacing is the installer's.", lines)
        self.assertEqual(changed(self.start, TP.tree(self.mac.home)), ['.claude/settings.json'])
        self.assertEqual(TP.json.loads(self.mac.read(os.path.join(self.mac.cd, 'settings.json'))),
                         TP.json.loads(TP.USER_SETTINGS))
        self.assertIsNone(CMI.record())
        self.assertEqual(self.mac.off(), [])

    def test_off_waits_for_claude_mems_own_repair_too(self):
        self.assertEqual(self.mac.install()['state'], 'done')
        ctx = I._ctx('claude-mem', R.new_log('claude-mem'), {'rec': CMI.record()})
        got = {}
        with mock.patch.object(R, 'run', stops_while_it_runs(self, got, 'repair')):
            with self.assertRaises(Died):
                CMI.step_repair(ctx)
        self.assertEqual(got['argv'][2:], ['file:' + os.path.join(self.mac.data, PACKAGE), 'repair'])
        line = waits('repair', got['p'].pid)
        for name, attempt in (('off', self.mac.off), ('repair', lambda: I.repair('claude-mem')),
                              ('on', lambda: I.begin('claude-mem'))):
            with self.subTest(attempt=name):
                with self.assertRaises(C.Refused) as e:
                    attempt()
                self.assertEqual(str(e.exception), line)
        self.assertEqual(CMI.record()['state'], 'installed')
        end(got['p'])
        self.assertEqual(self.mac.off(), ['claude-mem: taken out; your notes stay in ~/.claude-mem.'])
        self.assertEqual(changed(self.start, TP.tree(self.mac.home)), [])
        self.assertIsNone(CMI.record())

    def test_each_long_run_is_noted_before_it_runs_and_cleared_when_it_returns(self):
        guard = R.run                              # the Mac's guard over the real runner
        seen = []

        def watching(argv, env, *a, **kw):
            hook = kw.get('on_start')
            if hook is not None:
                def noted(pid):
                    hook(pid)
                    seen.append((pid, dict((CMI.record() or {}).get('run') or {})))
                kw['on_start'] = noted
            return guard(argv, env, *a, **kw)
        with mock.patch.object(R, 'run', watching):
            self.assertEqual(self.mac.install()['state'], 'done')
            self.assertNotIn('run', CMI.record())
            self.assertEqual(I.repair('claude-mem', wait=True)['state'], 'installing')
            self.assertEqual(I.job_view('claude-mem')['state'], 'done', I.job_view('claude-mem'))
        pkg = os.path.join(self.mac.data, PACKAGE)
        self.assertEqual([(run.get('pid') == pid, run.get('what'), run.get('package')) for pid, run in seen],
                         [(True, 'install', pkg), (True, 'repair', pkg)])  # telemetry and start are short: not noted
        self.assertNotIn('run', CMI.record())
        self.mac.off()

    def test_the_page_says_installing_from_before_while_it_runs(self):
        got = self.left_running()
        v = I.view('claude-mem', True)
        self.assertEqual((v['stopped'], v.get('before'), v.get('line')), (False, True, 'Installing (from before).'))
        self.assertEqual(claudecode.claudemem_view()['setup'], v)
        end(got['p'])
        v = I.view('claude-mem', True)
        self.assertEqual((v['stopped'], v.get('before'), v.get('line')), (True, None, 'Stopped part-way.'))


SETUP_FROM_BEFORE = {'by_switchboard': True, 'version': '15.18.3', 'stopped': False, 'before': True,
                     'line': 'Installing (from before).', 'notes': [], 'job': None}


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class FromBeforeOnThePage(test_app.ChromePage):
    """The page: an installer from before is shown as installing, with nothing to click, and read again each
    second until it ends."""

    def test_it_says_installing_from_before(self):
        base, reads = demo.claudecode, []

        def fake():
            reads.append(1)
            v = base()
            v['claudemem'].update(on=True, installed=False, setup=SETUP_FROM_BEFORE)
            return v
        p = mock.patch.object(demo, 'claudecode', fake)
        p.start()
        self.addCleanup(p.stop)
        v = self.run_page("""
      location.hash = '#settings'; await wait(2600);
      done({line: ($('#setup-claudemem .hint') || {}).textContent || null,
            repair: !!$('[data-do="packRepair"][data-pack="claudemem"]')});
""")
        self.assertEqual(v, {'line': 'Installing (from before).', 'repair': False})
        self.assertGreaterEqual(len(reads), 3)


# ---------------------------------------------------------------- only claude-mem's own cache folder

class OnlyClaudeMemsOwnCacheFolder(unittest.TestCase):
    """Another plugin of the same marketplace, in plugins/cache/thedotmack/, stays when claude-mem is turned off,
    also when claude-mem's install made that folder. The folder goes only when nothing else is in it."""

    OTHER = ['.claude/plugins', '.claude/plugins/cache', '.claude/plugins/cache/thedotmack',
             '.claude/plugins/cache/thedotmack/other-plugin', '.claude/plugins/cache/thedotmack/other-plugin/1.0.0',
             '.claude/plugins/cache/thedotmack/other-plugin/1.0.0/plugin.json']

    def check(self, mac, start):
        self.assertEqual(mac.install()['state'], 'done')
        cache = os.path.join(mac.cd, 'plugins', 'cache', 'thedotmack')
        other = os.path.join(cache, 'other-plugin', '1.0.0', 'plugin.json')
        os.makedirs(os.path.dirname(other), exist_ok=True)
        with open(other, 'w') as f:
            f.write('{}\n')
        self.assertEqual(mac.off(), ['claude-mem: taken out; your notes stay in ~/.claude-mem.'])
        self.assertTrue(os.path.isfile(other))
        self.assertEqual(os.listdir(cache), ['other-plugin'])        # claude-mem's own folder in it went
        self.assertEqual(changed(start, TP.tree(mac.home)), self.OTHER)

    def test_one_installed_after_it_stays(self):
        mac = TP.Mac(self, settings=TP.USER_SETTINGS)
        self.check(mac, TP.tree(mac.home))

    def test_one_that_came_while_it_installed_stays(self):
        mac = TP.Mac(self, settings=TP.USER_SETTINGS, cfg={'also_make': ['.claude/plugins/cache/thedotmack/other-plugin/']})
        self.check(mac, TP.tree(mac.home))

    def test_the_folder_goes_when_only_claude_mem_was_in_it(self):
        mac = TP.Mac(self, settings=TP.USER_SETTINGS)
        start = TP.tree(mac.home)
        self.assertEqual(mac.install()['state'], 'done')
        mac.off()
        self.assertEqual(changed(start, TP.tree(mac.home)), [])


# ---------------------------------------------------------------- only the launcher its install left

class OnlyItsOwnLauncher(unittest.TestCase):
    """Off takes out ~/.local/bin/claude-mem only while it is still the file claude-mem's install (or its own
    repair) left there; a file the person put there under the same name stays, and Off says so in one line."""

    def setUp(self):
        self.mac = TP.Mac(self, settings=TP.USER_SETTINGS)
        self.start = TP.tree(self.mac.home)
        self.launcher = os.path.join(self.mac.home, '.local', 'bin', 'claude-mem')

    def test_a_launcher_the_person_replaced_stays_and_off_says_so(self):
        self.assertEqual(self.mac.install()['state'], 'done')
        worker_down(self.mac)
        os.unlink(self.launcher)
        with open(self.launcher, 'w') as f:
            f.write('#!/bin/sh\necho mine\n')
        lines = self.mac.off()
        with open(self.launcher) as f:
            self.assertEqual(f.read(), '#!/bin/sh\necho mine\n')
        self.assertEqual(lines, ['claude-mem: taken out; your notes stay in ~/.claude-mem.',
                                 '~/.local/bin/claude-mem was changed after claude-mem installed it, so it stays.'])
        self.assertEqual(changed(self.start, TP.tree(self.mac.home)), ['.local', '.local/bin', '.local/bin/claude-mem'])

    def test_one_claude_mems_own_repair_placed_again_still_goes(self):
        self.mac.cfg['repair_launcher'] = True
        self.mac.save_cfg()
        self.assertEqual(self.mac.install()['state'], 'done')
        first = TP.tree(self.mac.home)['.local/bin/claude-mem']
        self.assertEqual(I.repair('claude-mem', wait=True)['state'], 'installing')
        self.assertEqual(I.job_view('claude-mem')['state'], 'done', I.job_view('claude-mem'))
        self.assertNotEqual(TP.tree(self.mac.home)['.local/bin/claude-mem'], first)      # its repair placed it again
        self.assertEqual(self.mac.off(), ['claude-mem: taken out; your notes stay in ~/.claude-mem.'])
        self.assertEqual(changed(self.start, TP.tree(self.mac.home)), [])

    def test_one_its_repair_placed_after_switchboard_stopped_still_goes(self):
        self.assertEqual(self.mac.install()['state'], 'done')
        ctx = I._ctx('claude-mem', R.new_log('claude-mem'), {'rec': CMI.record()})
        got = {}
        with mock.patch.object(R, 'run', stops_while_it_runs(self, got, 'repair')):
            with self.assertRaises(Died):
                CMI.step_repair(ctx)
        with open(self.launcher, 'a') as f:            # the repair, run on by itself, places the launcher again
            f.write('# placed again by its repair\n')
        end(got['p'])
        self.assertEqual(self.mac.off(), ['claude-mem: taken out; your notes stay in ~/.claude-mem.'])
        self.assertEqual(changed(self.start, TP.tree(self.mac.home)), [])


if __name__ == '__main__':
    unittest.main()
