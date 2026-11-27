import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The menu bar item stays on. Its menu has no Quit; turning it off is `switchboard menubar off`, which stops it and
# keeps it off across logins and updates (the installer's --no-menu-app choice), and `switchboard menubar on` brings it
# back. Its job's KeepAlive is always, the page's watch starts an item that stopped for any other reason, and doctor
# says which of the three it is. launchd is a stand-in here: nothing is started, stopped or asked for real.
import io
import os
import plistlib
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import __main__ as cli
from switchboard import core, menubar, menubar_keep, menubar_wrap, server, watch
from test_menubar import Sandbox, write_exe
from test_watch_launchd_jobs import install_sh, job

OFF = 'The menu bar item is off. Turn it on with: switchboard menubar on'
OFF_LINE = (None, 'Menu bar item: off, by your choice', 'Turn it on with: switchboard menubar on')


class Launchd(object):
    """launchd, as far as the menu bar's job goes: a job is loaded from its plist and runs the item until booted out."""

    def __init__(self, test):
        self.test = test
        self.calls = []
        self.loaded = False

    def __call__(self, *args):
        self.calls.append(list(args))
        what = args[0]
        if what == 'bootout':
            self.loaded = False
            self.test.running = None
            return 0
        if what == 'bootstrap':
            if not os.path.exists(args[2]):
                return 5
            self.loaded = True
            self.test.running = 4242
            return 0
        if what == 'kickstart':
            if not self.loaded:
                return 113
            self.test.running = 4242
            return 0
        if what == 'print':
            return 0 if self.loaded else 113
        return 1


class Base(Sandbox):
    def setUp(self):
        Sandbox.setUp(self)
        self.running = None
        self.launchd = Launchd(self)
        self.killed = []
        self.updates = []
        os.makedirs(os.path.join(self.data, 'app'))
        with open(os.path.join(self.data, 'app', 'choices'), 'w') as f:
            f.write(' --own-python\n')
        write_exe(menubar.launcher(), '#!/bin/sh\nexit 0\n')
        page = menubar.agent_plist(menubar.PAGE_AGENT)          # the page's job: the installer added both
        os.makedirs(os.path.dirname(page))
        with open(page, 'wb') as f:
            plistlib.dump({'Label': menubar.PAGE_AGENT}, f)
        for p in (mock.patch.object(menubar, 'launchctl', self.launchd),
                  mock.patch.object(menubar, 'running_pid', lambda: self.running),
                  mock.patch.object(menubar_keep, 'pause', lambda s: None),
                  mock.patch.object(menubar_keep.os, 'kill', lambda pid, sig: self.killed.append((pid, sig)))):
            p.start()
            self.addCleanup(p.stop)
        self.install_job()

    def install_job(self):
        """The menu bar's job as the installer leaves it: written, loaded, the item running."""
        with open(menubar.agent_plist(menubar.AGENT), 'wb') as f:
            plistlib.dump(menubar_keep.job_description(), f)
        self.launchd.loaded = True
        self.running = 4242

    def cli(self, *args):
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(cli, 'update', lambda a: self.updates.append(a) or 0):
            code = cli.main(['menubar'] + list(args))
        return code, out.getvalue()

    def choices(self):
        with open(os.path.join(self.data, 'app', 'choices')) as f:
            return f.read().split()


class TurningItOff(Base):
    def test_off_stops_it_removes_its_job_and_says_so_in_one_line(self):
        self.assertEqual(self.cli('off'), (0, OFF + '\n'))
        self.assertIn(['bootout', '%s/%s' % (menubar.domain(), menubar.AGENT)], self.launchd.calls)
        self.assertFalse(os.path.exists(menubar.agent_plist(menubar.AGENT)))     # nothing starts it at the next login
        self.assertIsNone(self.running)
        self.assertTrue(menubar_keep.is_off())
        self.assertEqual(self.choices(), ['--own-python', '--no-menu-app'])

    def test_an_update_after_off_keeps_it_off(self):
        self.cli('off')
        self.assertIn('--no-menu-app', cli.install_choices())
        self.assertIn('--no-menu-app', cli.update_flags({'link': False, 'menu': None, 'port': None}))
        text = install_sh()                     # Homebrew's upgrade gives only --homebrew: the saved choice is read
        self.assertIn('case "$saved" in *" --no-menu-app "*) MENUBAR=0 ;; esac', text)
        self.assertIn('[ "$MENUBAR" = 1 ] || CHOICES="$CHOICES --no-menu-app"', text)

    def test_a_new_login_after_off_leaves_it_off(self):
        self.cli('off')
        self.assertIsNone(menubar.our_job())    # launchd starts at login only the jobs in LaunchAgents
        starts = []
        clock = [1000.0]
        keeper = watch.MenuBarKeeper(running=lambda: False, job=lambda: '/the/job.plist',
                                     start=lambda p: starts.append(p) or True, mono=lambda: clock[0],
                                     wall=lambda: clock[0])
        for _ in range(30):                     # even a job file left behind is not started while it is off
            keeper.look()
            clock[0] += 60
        self.assertEqual(starts, [])

    def test_a_menu_bar_run_by_hand_is_stopped_too(self):
        os.remove(menubar.agent_plist(menubar.AGENT))
        self.launchd.loaded = False
        self.running = 5151                     # `switchboard menubar` in a terminal, with no job

        def gone(pid, sig):
            self.killed.append((pid, sig))
            self.running = None
        with mock.patch.object(menubar_keep.os, 'kill', gone):
            self.assertEqual(self.cli('off'), (0, OFF + '\n'))
        self.assertEqual(self.killed, [(5151, menubar_keep.signal.SIGTERM)])

    def test_one_that_will_not_stop_is_said_and_the_choice_is_still_kept(self):
        self.launchd.loaded = False
        os.remove(menubar.agent_plist(menubar.AGENT))
        self.running = 0                        # running, its pid not known: nothing to send a signal to
        code, out = self.cli('off')
        self.assertEqual(code, 1)
        self.assertEqual(out, menubar_keep.STILL_RUNNING + '\n')
        self.assertTrue(menubar_keep.is_off())

    def test_off_twice_is_the_same_line(self):
        self.cli('off')
        self.assertEqual(self.cli('off'), (0, OFF + '\n'))
        self.assertEqual(self.choices().count('--no-menu-app'), 1)

    def test_opening_the_app_while_it_is_off_opens_the_page_and_starts_no_item(self):
        self.cli('off')
        opened, ran = [], []
        with mock.patch.object(menubar, 'open_page', lambda: opened.append(1) or True), \
                mock.patch.object(menubar, 'run', lambda: ran.append(1) or 0):
            self.assertEqual(menubar.opened(), 0)
        self.assertEqual((opened, ran), ([1], []))


class TurningItOn(Base):
    def test_on_writes_its_job_starts_it_and_says_so(self):
        self.cli('off')
        self.launchd.calls[:] = []
        code, out = self.cli('on')
        self.assertEqual((code, out), (0, menubar_keep.ON + '\n'))
        self.assertFalse(menubar_keep.is_off())
        self.assertEqual(self.choices(), ['--own-python'])
        with open(menubar.agent_plist(menubar.AGENT), 'rb') as f:
            self.assertEqual(plistlib.load(f), menubar_keep.job_description())
        self.assertIn(['bootstrap', menubar.domain(), menubar.agent_plist(menubar.AGENT)], self.launchd.calls)
        self.assertEqual(self.running, 4242)

    def test_on_when_an_update_took_its_files_out_runs_the_update_that_adds_them(self):
        self.cli('off')
        os.remove(menubar.launcher())           # the next update, with --no-menu-app, took the item out
        self.assertEqual(self.cli('on')[0], 0)
        self.assertEqual(self.updates, [['--menu-app']])
        self.assertFalse(menubar_keep.is_off())

    def test_on_while_it_runs_says_it_is_on(self):
        self.assertEqual(self.cli('on'), (0, menubar_keep.ON + '\n'))

    def test_on_without_the_background_job_says_how_to_start_it(self):
        self.cli('off')
        os.remove(menubar.agent_plist(menubar.PAGE_AGENT))      # installed with --no-agent
        self.assertEqual(self.cli('on'), (0, menubar_keep.NO_JOB + '\n'))
        self.assertFalse(menubar_keep.is_off())
        self.assertFalse(os.path.exists(menubar.agent_plist(menubar.AGENT)))

    def test_a_job_that_macos_will_not_start_is_said_not_claimed(self):
        self.cli('off')
        with mock.patch.object(menubar, 'launchctl', lambda *a: 5):
            code, out = self.cli('on')
        self.assertEqual((code, out), (1, menubar_keep.NOT_STARTED + '\n'))

    def test_on_leaves_a_job_that_runs_another_folder_as_it_is(self):
        other = {'Label': menubar.AGENT, 'ProgramArguments': ['/another/folder/bin/switchboard-menubar']}
        with open(menubar.agent_plist(menubar.AGENT), 'wb') as f:
            plistlib.dump(other, f)
        self.launchd.calls[:] = []
        self.assertEqual(self.cli('on'), (1, menubar_keep.OTHER_JOB + '\n'))
        with open(menubar.agent_plist(menubar.AGENT), 'rb') as f:
            self.assertEqual(plistlib.load(f), other)
        self.assertEqual(self.launchd.calls, [])

    def test_menubar_alone_still_does_what_it_did(self):
        ran = []
        with mock.patch.object(menubar, 'appkit_here', lambda: True), \
                mock.patch.object(menubar, 'terminal', lambda: False), \
                mock.patch.object(menubar, 'run', lambda: ran.append(1) or 0):
            self.assertEqual(self.cli(), (0, ''))
        self.assertEqual(ran, [1])
        self.assertEqual(menubar.USAGE, 'switchboard menubar [on | off | --applications]')
        self.assertEqual(self.cli('sideways'), (2, menubar.USAGE + '\n'))
        self.assertEqual(self.cli('off', 'now'), (2, menubar.USAGE + '\n'))


class TheJob(unittest.TestCase):
    def test_its_keepalive_is_always(self):
        bar = job(install_sh(), 'MENUBAR_PLIST')
        self.assertIs(bar.get('KeepAlive'), True)
        self.assertEqual(bar.get('ThrottleInterval'), 20)       # launchd starts it again at most every 20 s

    def test_menubar_on_writes_the_job_the_installer_writes(self):
        bar = job(install_sh(), 'MENUBAR_PLIST')
        mine = menubar_keep.job_description()
        self.assertEqual(sorted(mine), sorted(bar))
        for key in ('RunAtLoad', 'KeepAlive', 'ProcessType', 'ThrottleInterval'):
            self.assertEqual(mine[key], bar[key], key)
        self.assertEqual(mine['Label'], menubar.AGENT)
        self.assertEqual(mine['ProgramArguments'], [menubar.launcher()])
        self.assertEqual(mine['StandardOutPath'], core.data_path('menubar.log'))
        self.assertEqual(mine['EnvironmentVariables']['PATH'],
                         ':'.join(p.format(home=core.home()) for p in menubar.LAUNCHD_PATH))
        self.assertIn('JOB_PATH="$HOME/.local/bin:$HOME/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:'
                      '/bin:/usr/sbin:/sbin"', install_sh())


class ThePageStartsItAgain(Base):
    def test_a_quit_mark_from_an_older_version_is_not_honoured(self):
        core.write_json(core.data_path('menubar-quit.json'), {'at': 1.0, 'pid': 1})
        starts = []
        clock = [1000.0]
        keeper = watch.MenuBarKeeper(running=lambda: False, job=lambda: '/the/job.plist',
                                     start=lambda p: starts.append(p) or True, mono=lambda: clock[0],
                                     wall=lambda: clock[0])
        for _ in range(3):
            keeper.look()
            clock[0] += 60
        self.assertEqual(starts, ['/the/job.plist'])
        self.assertFalse(hasattr(watch, 'note_quit'))
        self.assertFalse(hasattr(watch, 'quit_chosen'))

    def test_the_pages_tick_reads_the_off_choice(self):
        self.running = None
        self.launchd.loaded = True
        with mock.patch.object(server, 'BAR_KEEPER', []), mock.patch.object(watch, 'background', lambda: True):
            menubar_keep.set_off(True)
            self.assertIsNone(server.menubar_tick())
            self.assertIsNone(server.BAR_KEEPER[0].gone_since)
            menubar_keep.set_off(False)
            server.menubar_tick()                                   # gone since now
            server.BAR_KEEPER[0].gone_since -= watch.BAR_DOWN_FOR + 1
            self.assertEqual(server.menubar_tick(), 'started')
        self.assertIn(['kickstart', '%s/%s' % (menubar.domain(), menubar.AGENT)], self.launchd.calls)


class WhenItCannotRun(Base):
    def test_under_its_job_a_menu_bar_that_cannot_draw_waits_before_it_exits(self):
        # KeepAlive always: launchd would start it again every 20 s. It waits, so a Mac where it can't draw starts it
        # at most every CANT_DRAW_WAIT seconds.
        waited = []
        with mock.patch.object(menubar, 'appkit_here', lambda: False), \
                mock.patch.object(menubar, 'terminal', lambda: False), \
                mock.patch.object(menubar_keep, 'under_launchd', lambda: True), \
                mock.patch.object(menubar_keep, 'pause', lambda s: waited.append(s)):
            os.environ[menubar.HOPPED] = '1'
            self.addCleanup(os.environ.pop, menubar.HOPPED, None)
            self.assertEqual(self.cli(), (0, menubar.NOT_HERE + '\n'))
        self.assertEqual(waited, [menubar_keep.CANT_DRAW_WAIT])
        self.assertGreaterEqual(menubar_keep.CANT_DRAW_WAIT, 600)

    def test_under_its_job_a_second_menu_bar_waits_for_the_lock_instead_of_exiting(self):
        waits = []
        with mock.patch.object(menubar_keep, 'under_launchd', lambda: True), \
                mock.patch.object(menubar, 'take_lock', lambda wait=None: waits.append(wait) or False), \
                mock.patch.object(menubar_wrap, 'relaunch_inside', lambda a: None), redirect_stdout(io.StringIO()):
            self.assertEqual(menubar.run(), 0)
        self.assertEqual(waits, [menubar.FOREVER])

    def test_in_a_terminal_it_does_not_wait(self):
        waited = []
        with mock.patch.object(menubar, 'appkit_here', lambda: False), \
                mock.patch.object(menubar, 'terminal', lambda: True), \
                mock.patch.object(menubar_keep, 'under_launchd', lambda: False), \
                mock.patch.object(menubar_keep, 'pause', lambda s: waited.append(s)):
            os.environ[menubar.HOPPED] = '1'
            self.addCleanup(os.environ.pop, menubar.HOPPED, None)
            self.assertEqual(self.cli()[0], 1)
        self.assertEqual(waited, [])


class Doctor(Base):
    def lines(self):
        got = []
        libs = {'python': '3.12.13', 'pyobjc': '13.0', 'framework': False}
        with mock.patch.object(menubar, 'libraries', lambda: libs), \
                mock.patch.object(cli.subprocess, 'run',
                                  lambda cmd, **k: mock.Mock(returncode=0 if self.launchd.loaded else 113)):
            cli._doctor_menubar(lambda good, text, fix='': got.append((good, text, fix)))
        return got

    def test_on(self):
        self.assertIn((True, 'Menu bar item: on (pid 4242)', mock.ANY), self.lines())

    def test_off_by_choice_is_one_line_with_the_way_back(self):
        self.cli('off')
        self.assertEqual(self.lines(), [OFF_LINE])
        os.remove(menubar.launcher())           # after the next update took its files out: still off by choice
        self.assertEqual(self.lines(), [OFF_LINE])

    def test_installed_but_not_running(self):
        self.running = None
        got = [g for g in self.lines() if g[1].startswith('Menu bar item')]
        self.assertEqual(got, [(None, 'Menu bar item: installed, not running',
                                'Start it with: switchboard menubar on. Its log: %s' % cli._short_path(
                                    core.data_path('menubar.log')))])


if __name__ == '__main__':
    unittest.main()
