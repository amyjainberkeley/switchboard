import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The menu bar's app in Applications, where Spotlight and Launchpad find it. Where it goes
# (/Applications, else ~/Applications, never over an app that is not this install's), what opening it does (the menu
# bar when it is not running, the page when it is), the copies it leaves elsewhere, and what doctor says.
#
# Every Applications folder is a temp one: /Applications is stood in for by menubar_wrap.APPLICATIONS (the
# Sandbox's), ~/Applications is the throwaway home's. Nothing here opens an app, asks Launch Services or launchd, or
# starts the real menu bar: a Python started the way macOS starts an opened app runs a stand-in program that only says
# how it was started. The installer's side is in tests/install-check.sh (cases 16 and 17).
import io
import os
import plistlib
import queue
import subprocess
import sys
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from switchboard import __main__ as cli
from switchboard import core, menubar, pagekey
from switchboard import menubar_wrap as wrap

import test_menubar as tm  # its sandbox, write_exe, hold and stand-in PyObjC; no tests run from here

MENUBAR_ID = 'io.github.amyjainberkeley.switchboard.menubar'

# A stand-in for the program: `switchboard ...` says how it was started, what it was given and what it was not.
STAND_IN = '''import os, sys
print('|'.join(['switchboard ' + ' '.join(sys.argv[1:]),
                'data=%s' % os.environ.get('SWITCHBOARD_HOME'),
                'pythonpath=%s' % ('PYTHONPATH' in os.environ),
                'opened=%s' % ('SWITCHBOARD_APP_OPENED' in os.environ),
                'isolated=%d' % sys.flags.isolated,
                'bytecode=%d' % (not sys.flags.dont_write_bytecode)]))
'''


def fake_python(tmp, version='3.12'):
    """A made-up install: bin/python3.12 (a few bytes), its lib/libpython3.12.dylib, and an environment."""
    base = os.path.join(tmp, 'python')
    exe = os.path.join(base, 'bin', 'python' + version)
    tm.write_exe(exe, '#!/bin/sh\necho made-up\n')
    os.utime(exe, (1700000000, 1700000000))
    os.makedirs(os.path.join(base, 'lib'), exist_ok=True)
    with open(os.path.join(base, 'lib', 'libpython%s.dylib' % version), 'w') as f:
        f.write('lib')
    env = os.path.join(tmp, 'env')
    os.makedirs(os.path.join(env, 'lib', 'python' + version, 'site-packages'), exist_ok=True)
    return exe, env


def foreign_app(path, bundle_id='com.example.someone-else', more=None):
    """Someone else's app at path: its Info.plist and a program file. Returns what it holds, to compare later."""
    os.makedirs(os.path.join(path, 'Contents', 'MacOS'), exist_ok=True)
    with open(os.path.join(path, 'Contents', 'Info.plist'), 'wb') as f:
        plistlib.dump(dict({'CFBundleIdentifier': bundle_id, 'CFBundleExecutable': 'Switchboard'}, **(more or {})), f)
    tm.write_exe(os.path.join(path, 'Contents', 'MacOS', 'Switchboard'), '#!/bin/sh\necho theirs\n')
    return snapshot(path)


def snapshot(top):
    out = {}
    for folder, dirs, names in os.walk(top):
        for n in names:
            p = os.path.join(folder, n)
            with open(p, 'rb') as f:
                out[os.path.relpath(p, top)] = f.read()
    return out


class Base(tm.Sandbox):
    def setUp(self):
        super().setUp()
        self.exe, self.env = fake_python(self.tmp)
        self.user = os.path.join(self.home, 'Applications')         # ~/Applications in the throwaway home

    def app_in(self, folder):
        return os.path.join(folder, 'Switchboard.app')

    def place(self):
        return wrap.place(executable=self.exe, prefix=self.env)

    def lock_held_from(self, app):
        """Another menu bar of this data folder runs, from inside app (its lock held, its stamp naming that Python)."""
        child = tm.hold(self)
        core.write_json(menubar.stamp_path(), {'pid': child.pid, 'started': 0,
                                               'python': os.path.join(app, 'Contents', 'MacOS', 'python')})
        return child


# ---------- what opening the app runs ----------

class OpeningTheApp(Base):
    """Opened from Finder, Spotlight or Launchpad, macOS starts the app's program, a copy of Python, with nothing to run.
    Info.plist's LSEnvironment (set only when macOS opens it) points that Python at a few lines in the app that hand it
    to `switchboard menubar --opened`, on the program and the data folder the app was made for."""

    def build_with_stand_in(self, where):
        program = os.path.join(self.tmp, 'program')
        tm.write_exe(os.path.join(program, 'switchboard', '__init__.py'), '')
        tm.write_exe(os.path.join(program, 'switchboard', '__main__.py'), STAND_IN)
        with mock.patch.object(core, 'PACKAGE', os.path.join(program, 'switchboard')):
            wrap.make(where, executable=self.exe, prefix=self.env)
        return self.app_in(where)

    def started_as_macos_does(self, app, *args, opened=True):
        """This Python (any will do: the lines in the app are plain Python), started the way macOS starts an opened
        app: no arguments, nothing on stdin, Info.plist's LSEnvironment in its environment. Its output."""
        env = {'HOME': self.home, 'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'TMPDIR': self.tmp + '/'}
        env.update(wrap.info(app)['LSEnvironment'] if opened else
                   {'PYTHONPATH': os.path.join(app, 'Contents', 'Resources')})
        r = subprocess.run([sys.executable] + list(args), stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_info_plist_says_what_macos_sets_when_it_opens_the_app(self):
        app = self.app_in(self.apps)
        wrap.make(self.apps, executable=self.exe, prefix=self.env)
        info = wrap.info(app)
        self.assertEqual(info['LSEnvironment'], {'PYTHONPATH': os.path.join(app, 'Contents', 'Resources'),
                                                 'PYTHONDONTWRITEBYTECODE': '1', 'SWITCHBOARD_APP_OPENED': '1'})
        self.assertEqual(info['SwitchboardData'], os.path.realpath(self.data))
        # the program is still the copy of Python itself (menu bar managers match a relaunched app by it)
        self.assertEqual(info['CFBundleExecutable'], 'python')
        self.assertTrue(os.path.isfile(os.path.join(app, 'Contents', 'Resources', 'sitecustomize.py')))

    def test_opened_it_hands_over_to_switchboard_menubar_opened(self):
        app = self.build_with_stand_in(self.apps)
        out = self.started_as_macos_does(app)
        self.assertEqual(out, 'switchboard menubar --opened|data=%s|pythonpath=False|opened=False|isolated=1|'
                              'bytecode=0\n' % os.path.realpath(self.data))
        self.assertFalse(os.path.exists(os.path.join(app, 'Contents', 'Resources', '__pycache__')))   # nothing written

    def test_started_any_other_way_it_does_nothing_of_the_kind(self):
        app = self.build_with_stand_in(self.apps)
        # with something to run (as the menu bar starts it, though that start also passes -I): just that
        self.assertEqual(self.started_as_macos_does(app, '-c', 'print("its own")'), 'its own\n')
        # without the mark macOS sets for an opened app: nothing
        self.assertEqual(self.started_as_macos_does(app, opened=False), '')

    def test_a_second_build_writes_none_of_it_again(self):
        wrap.make(self.apps, executable=self.exe, prefix=self.env)
        contents = os.path.join(self.app_in(self.apps), 'Contents')
        files = [os.path.join(contents, 'Info.plist'), os.path.join(contents, 'Resources', 'sitecustomize.py')]
        before = [(os.stat(p).st_ino, os.stat(p).st_mtime_ns) for p in files]
        wrap.make(self.apps, executable=self.exe, prefix=self.env)
        self.assertEqual([(os.stat(p).st_ino, os.stat(p).st_mtime_ns) for p in files], before)


# ---------- where it goes ----------

class WhereItGoes(Base):
    """/Applications when it can be written to without a password, else ~/Applications. A Switchboard.app there that is
    not this install's is never touched: one line says so, and the other folder is used."""

    def test_applications_when_it_can_be_written_to(self):
        app, said = self.place()
        self.assertEqual((app, said), (self.app_in(self.apps), []))
        self.assertTrue(wrap.mine(app))
        self.assertTrue(os.path.isfile(os.path.join(app, 'Contents', 'MacOS', 'python')))
        self.assertFalse(os.path.exists(self.user))             # ~/Applications is not made when it is not needed
        self.assertEqual((wrap.home(), wrap.present()), (self.apps, app))

    def test_else_the_home_folders_applications(self):
        os.chmod(self.apps, 0o555)                              # /Applications that asks for a password
        self.addCleanup(os.chmod, self.apps, 0o755)
        app, said = self.place()
        self.assertEqual((app, said), (self.app_in(self.user), []))
        self.assertEqual((wrap.home(), wrap.present()), (self.user, app))     # the menu bar's start finds it there

    def test_another_apps_switchboard_is_left_alone(self):
        theirs = self.app_in(self.apps)
        held = foreign_app(theirs)
        app, said = self.place()
        self.assertEqual((app, said), (self.app_in(self.user), ['%s is another app, so it was left alone.' % theirs]))
        self.assertEqual(snapshot(theirs), held)
        self.assertEqual(wrap.home(), self.user)

    def test_one_in_each_folder_and_it_stays_in_the_data_folder(self):
        held = [foreign_app(self.app_in(self.apps)), foreign_app(self.app_in(self.user))]
        app, said = self.place()
        self.assertIsNone(app)
        self.assertEqual(said, ['%s is another app, so it was left alone.' % self.app_in(self.apps),
                                '~/Applications/Switchboard.app is another app, so it was left alone.'])
        self.assertEqual([snapshot(self.app_in(self.apps)), snapshot(self.app_in(self.user))], held)
        self.assertEqual(wrap.home(), os.path.join(self.data, 'menubar'))

    def test_an_older_one_of_ours_is_replaced(self):
        os.environ['SWITCHBOARD_HOME'] = self.data = os.path.join(self.home, '.switchboard')    # the usual folder
        old = self.app_in(self.apps)
        foreign_app(old, MENUBAR_ID, {'CFBundleExecutable': 'python', 'CFBundleShortVersionString': '0.1.0'})
        tm.write_exe(os.path.join(old, 'Contents', 'MacOS', 'python'), '#!/bin/sh\necho an older Python\n')
        app, said = self.place()
        self.assertEqual((app, said), (old, []))
        info = wrap.info(app)
        self.assertEqual((info['CFBundleShortVersionString'], info['SwitchboardData']),
                         (core.VERSION, os.path.realpath(self.data)))
        with open(os.path.join(app, 'Contents', 'MacOS', 'python'), 'rb') as a, open(self.exe, 'rb') as b:
            self.assertEqual(a.read(), b.read())

    def test_another_installs_app_is_not_this_ones(self):
        # Switchboard's, made for another data folder (another account on this Mac, or a try with SWITCHBOARD_HOME)
        theirs = self.app_in(self.apps)
        held = foreign_app(theirs, MENUBAR_ID, {'CFBundleExecutable': 'python',
                                                'SwitchboardData': '/Users/someone/.switchboard'})
        self.assertFalse(wrap.mine(theirs))
        self.assertEqual(wrap.home(), os.path.join(self.data, 'menubar'))     # a menu bar start never rebuilds it
        app, said = self.place()
        self.assertEqual((app, said), (self.app_in(self.user), ['%s is another app, so it was left alone.' % theirs]))
        self.assertEqual(snapshot(theirs), held)

    def test_an_older_app_counts_for_the_usual_data_folder_only(self):
        # `switchboard menubar --applications` made it without naming a data folder: it was ~/.switchboard's
        old = self.app_in(self.apps)
        held = foreign_app(old, MENUBAR_ID, {'CFBundleExecutable': 'python'})
        self.assertFalse(wrap.mine(old))                        # a try with another SWITCHBOARD_HOME never takes it
        self.assertEqual(self.place()[0], self.app_in(self.user))
        self.assertEqual(snapshot(old), held)
        os.environ['SWITCHBOARD_HOME'] = os.path.join(self.home, '.switchboard')
        self.assertTrue(wrap.mine(old))


class TheCopiesLeftElsewhere(Base):
    """Once the app is in its place, this install's copies elsewhere go (the hidden one in the data folder, one in the
    other Applications folder), unless a menu bar runs from one of them: that one goes once it has stopped."""

    def test_they_go(self):
        hidden, older = self.app_in(os.path.join(self.data, 'menubar')), self.app_in(self.user)
        wrap.make(os.path.join(self.data, 'menubar'), executable=self.exe, prefix=self.env)
        wrap.make(self.user, executable=self.exe, prefix=self.env)
        app, said = self.place()
        self.assertEqual((app, said), (self.app_in(self.apps), []))
        self.assertFalse(os.path.exists(hidden))
        self.assertFalse(os.path.exists(older))
        self.assertTrue(os.path.isdir(os.path.join(self.data, 'menubar')))      # its lock and stamp stay

    def test_the_one_a_menu_bar_runs_from_stays(self):
        hidden = self.app_in(os.path.join(self.data, 'menubar'))
        wrap.make(os.path.join(self.data, 'menubar'), executable=self.exe, prefix=self.env)
        child = self.lock_held_from(hidden)
        app, said = self.place()
        self.assertEqual(app, self.app_in(self.apps))
        self.assertTrue(os.path.isdir(hidden))
        child.stdin.close()
        child.wait(10)
        self.assertEqual(wrap.tidy(app), [hidden])               # stopped: it goes the next time

    def test_never_an_app_that_is_not_this_installs(self):
        theirs = self.app_in(self.user)
        held = foreign_app(theirs, MENUBAR_ID, {'CFBundleExecutable': 'python',
                                                'SwitchboardData': '/Users/someone/.switchboard'})
        app, said = self.place()
        self.assertEqual(app, self.app_in(self.apps))
        self.assertEqual(snapshot(theirs), held)

    def test_the_menu_bar_tidies_once_it_runs_from_applications(self):
        hidden = self.app_in(os.path.join(self.data, 'menubar'))
        wrap.make(os.path.join(self.data, 'menubar'), executable=self.exe, prefix=self.env)
        python = wrap.make(self.apps, executable=self.exe, prefix=self.env)
        took = []
        with mock.patch.object(wrap, 'relaunch_inside', lambda args: 'already in the app'), \
                mock.patch.object(wrap, 'framework', lambda build=None: False), \
                mock.patch.object(wrap, 'running_app', lambda: os.path.dirname(os.path.dirname(os.path.dirname(python)))), \
                mock.patch.object(menubar, '_ui', lambda: {'run': lambda: took.append(os.path.exists(hidden)) or 0}), \
                mock.patch.object(menubar, 'terminal', lambda: False):
            self.assertEqual(menubar.run(), 0)
        self.assertEqual(took, [False])                         # gone before the item showed
        self.assertTrue(os.path.isdir(self.app_in(self.apps)))


class TheInstallersLines(Base):
    """install.sh runs menubar_wrap.installer() on the engine's Python and says its lines: one line for where the app
    went, and one for each app it left alone. A line starting "app " is the app's path, for Launch Services."""

    def lines(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = wrap.installer(executable=self.exe, prefix=self.env)
        return code, out.getvalue()

    def test_in_applications(self):
        self.assertEqual(self.lines(), (0, 'app %s\nnote Added Switchboard to %s.\n' % (self.app_in(self.apps), self.apps)))

    def test_in_the_home_folders_applications_after_another_app(self):
        foreign_app(self.app_in(self.apps))
        self.assertEqual(self.lines(), (0, 'say %s is another app, so it was left alone.\napp %s\n'
                                           'note Added Switchboard to ~/Applications.\n'
                                        % (self.app_in(self.apps), self.app_in(self.user))))

    def test_a_home_folder_written_with_a_double_slash_is_still_said_with_a_tilde(self):
        # $HOME as a temporary folder can hold one ($TMPDIR ends with a slash): the line still says ~/Applications
        os.environ['HOME'] = self.home.replace(os.sep + 'home', os.sep + os.sep + 'home')
        os.chmod(self.apps, 0o555)
        self.addCleanup(os.chmod, self.apps, 0o755)
        self.assertEqual(self.lines()[1].splitlines()[-1], 'note Added Switchboard to ~/Applications.')

    def test_nowhere(self):
        foreign_app(self.app_in(self.apps))
        foreign_app(self.app_in(self.user))
        code, out = self.lines()
        self.assertEqual(code, 1)
        self.assertEqual(out.splitlines()[-1], "say The menu bar's app stays in %s." % os.path.join(self.data, 'menubar'))


# ---------- `switchboard menubar --opened` ----------

class MenubarOpened(Base):
    """What an opened app runs: the page when the menu bar runs (never a second item); else the menu bar, through its
    job when there is one (so it runs as it does after a login), else here."""

    def run_cli(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(['menubar'] + list(args))
        return code, out.getvalue()

    def watch(self, kickstart=0, bootstrap=0):
        self.opened, self.launchd, self.ran = [], [], []
        codes = {'kickstart': kickstart, 'bootstrap': bootstrap}
        return [mock.patch.object(menubar, 'appkit_here', lambda: True),
                mock.patch.object(menubar, 'terminal', lambda: False),
                mock.patch.object(menubar.subprocess, 'call',
                                  lambda cmd, **k: self.opened.append(cmd) or 0),
                mock.patch.object(menubar, 'launchctl', lambda *a: self.launchd.append(list(a)) or codes[a[0]]),
                mock.patch.object(menubar, 'run', lambda: self.ran.append(1) or 0)]

    def go(self, **codes):
        patches = self.watch(**codes)
        for p in patches:
            p.start()
        try:
            return self.run_cli('--opened')
        finally:
            for p in reversed(patches):
                p.stop()

    def job(self):
        path = menubar.agent_plist(menubar.AGENT)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            plistlib.dump({'Label': menubar.AGENT, 'ProgramArguments': [menubar.launcher()]}, f)
        return path

    def test_running_it_opens_the_page(self):
        tm.hold(self)
        self.job()
        self.assertEqual(self.go(), (0, ''))
        # `open` is handed a private file that carries a one-use ticket (pagekey), never the page key
        self.assertEqual([argv[0] for argv in self.opened], ['/usr/bin/open'])
        self.assertEqual(os.path.dirname(self.opened[0][1]), core.data_path('page-open'))
        self.assertNotIn(pagekey.key(), ' '.join(self.opened[0]))
        self.assertEqual((self.launchd, self.ran), ([], []))

    def test_not_running_its_job_starts_it(self):
        self.job()
        self.assertEqual(self.go(), (0, ''))
        self.assertEqual(self.launchd, [['kickstart', 'gui/%d/%s' % (os.getuid(), MENUBAR_ID)]])
        self.assertEqual((self.opened, self.ran), ([], []))

    def test_a_job_that_was_taken_down_is_loaded_again(self):
        plist = self.job()
        self.assertEqual(self.go(kickstart=113), (0, ''))
        self.assertEqual(self.launchd, [['kickstart', 'gui/%d/%s' % (os.getuid(), MENUBAR_ID)],
                                        ['bootstrap', 'gui/%d' % os.getuid(), plist]])
        self.assertEqual(self.ran, [])

    def test_without_a_job_it_runs_here(self):
        self.assertEqual(self.go(), (0, ''))
        self.assertEqual((self.ran, self.launchd, self.opened), ([1], [], []))
        self.job()
        self.assertEqual(self.go(kickstart=5, bootstrap=5), (0, ''))       # launchd would not: here all the same
        self.assertEqual(self.ran, [1])

    def test_it_is_not_in_the_usage_line_and_other_words_still_are_refused(self):
        self.assertEqual(menubar.USAGE, 'switchboard menubar [on | off | --applications]')
        self.assertEqual(self.run_cli('--nope'), (2, menubar.USAGE + '\n'))


class TheRunningItemOpenedAgain(tm.Sandbox):
    """Opened again while it runs, macOS hands the running menu bar the open (no second copy starts): it opens the
    page. The real menu bar class, on stand-in AppKit modules (nothing is drawn)."""

    def setUp(self):
        super().setUp()
        mods = tm.stand_in_pyobjc(queue.Queue())
        self.workspace = mock.MagicMock(name='NSWorkspace')
        mods['AppKit'].NSWorkspace = self.workspace
        mods['Foundation'].NSURL = mock.MagicMock(URLWithString_=lambda s: 'NSURL(%s)' % s,
                                                  fileURLWithPath_=lambda s: 'FILE(%s)' % s)
        self.waits = []
        p = mock.patch.object(pagekey, 'watch_taken', lambda path, say, port, patience, part='': self.waits.append(path))
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.dict(sys.modules, mods)
        p.start()
        self.addCleanup(p.stop)
        menubar._UI.clear()
        self.addCleanup(menubar._UI.clear)
        self.bar = menubar._ui()['MenuBar'].alloc().init()

    def test_it_opens_the_page(self):
        self.assertIs(self.bar.applicationShouldHandleReopen_hasVisibleWindows_(None, False), False)
        # NSWorkspace is handed the private file with a one-use ticket; the wait for its ticket runs on a thread
        opened = self.workspace.sharedWorkspace.return_value.openURL_
        opened.assert_called_once()
        path = opened.call_args[0][0][len('FILE('):-1]
        self.assertEqual(os.path.dirname(path), core.data_path('page-open'))
        for _ in range(100):
            if self.waits:
                break
            time.sleep(0.05)
        self.assertEqual(self.waits, [path])

    def test_add_account_opens_the_add_form_through_a_ticket_too(self):
        self.bar.add_(None)
        opened = self.workspace.sharedWorkspace.return_value.openURL_
        opened.assert_called_once()
        path = opened.call_args[0][0][len('FILE('):-1]
        self.assertEqual(os.path.dirname(path), core.data_path('page-open'))
        with open(path, encoding='utf-8') as f:
            page = f.read()
        self.assertRegex(page, r'http://127\.0\.0\.1:%d/\?t=[^"#]+#accounts/add"' % core.port())


# ---------- `switchboard menubar --applications` ----------

class MenubarApplications(Base):
    def run_cli(self):
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(menubar, 'appkit_here', lambda: True), \
                mock.patch.object(wrap, 'make', lambda where, executable=None, prefix=None: os.makedirs(
                    os.path.join(where, 'Switchboard.app', 'Contents'), exist_ok=True)):
            code = cli.main(['menubar', '--applications'])
        return code, out.getvalue()

    def test_it_goes_where_the_installer_puts_it(self):
        theirs = self.app_in(self.apps)
        foreign_app(theirs)
        self.assertEqual(self.run_cli(), (0, "%s is another app, so it was left alone.\nThe menu bar's app is in "
                                             "~/Applications/Switchboard.app.\n" % theirs))

    def test_nowhere_it_says_so(self):
        foreign_app(self.app_in(self.apps))
        foreign_app(self.app_in(self.user))
        code, out = self.run_cli()
        self.assertEqual(code, 1)
        self.assertEqual(out.splitlines()[-1], "The menu bar's app stays in %s." % os.path.join(self.data, 'menubar'))


# ---------- doctor ----------

class DoctorSaysWhere(Base):
    def lines(self):
        tm.write_exe(menubar.launcher(), '#!/bin/sh\nexit 0\n')
        tm.write_exe(os.path.join(self.data, 'runtime', 'env', 'bin', 'python'), '#!/bin/sh\necho "13.0 3.12.13 0"\n')
        got = []
        cli._doctor_menubar(lambda good, text, fix='': got.append((good, text, fix)))
        return got[3]

    def test_in_applications(self):
        app, _ = self.place()
        self.assertEqual(self.lines()[:2], (True, 'Menu bar app (%s)' % app))

    def test_in_the_home_folders_applications(self):
        os.chmod(self.apps, 0o555)
        self.addCleanup(os.chmod, self.apps, 0o755)
        self.place()
        self.assertEqual(self.lines()[:2], (True, 'Menu bar app (~/Applications/Switchboard.app)'))

    def test_only_the_hidden_copy(self):
        wrap.make(os.path.join(self.data, 'menubar'), executable=self.exe, prefix=self.env)
        self.assertEqual(self.lines(), (None, 'Menu bar app (%s), not in Applications' % self.app_in(
            os.path.join(self.data, 'menubar')), 'Put it there with: switchboard menubar --applications'))

    def test_none_yet(self):
        self.assertEqual(self.lines(), (None, 'Menu bar app: not made yet',
                                        'Put it in Applications with: switchboard menubar --applications'))


if __name__ == '__main__':
    unittest.main()
