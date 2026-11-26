import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# What `switchboard update` asks the installer for, doctor's menu bar and link app lines,
# router/build.sh, and pieces of install.sh read or run on their own.
#
# Made-up accounts and folders only. The installer and the uninstaller are never run whole here (tests/_isolate.py
# refuses them): tests/install-check.sh runs them in throwaway homes. The installer here is a stand-in that writes
# down what it was given. A link app is never built or registered: build.sh runs from a copy whose bundle identifier
# is made up, with a swiftc and a codesign that stand in, and --no-register.
import io
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
import urllib.request
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)

from switchboard import __main__ as cli
from switchboard import browser, connect, core, linkapp, menubar, newer
from fakes_accounts_api import install

REAL_PAGES = (7798, 7799)                            # a real page's and the demo's: never reached from here


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def write_exe(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(text)
    os.chmod(path, 0o755)


def guarded(real):
    def urlopen(url, *a, **k):
        port = urllib.parse.urlparse(getattr(url, 'full_url', url)).port
        if port in REAL_PAGES:
            raise AssertionError('a test reached for the page on port %d' % port)
        return real(url, *a, **k)
    return urlopen


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-install-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, 'home')
        self.data = os.path.join(self.tmp, 'data')
        os.makedirs(os.path.join(self.home, '.claude'))
        env = mock.patch.dict(os.environ, {'HOME': self.home, 'SWITCHBOARD_HOME': self.data,
                                           'CLAUDE_CONFIG_DIR': os.path.join(self.home, '.claude'),
                                           'SWITCHBOARD_PORT': str(free_port())})
        env.start()
        self.addCleanup(env.stop)
        for target, name in ((urllib.request, 'urlopen'), (cli, '_page_open')):
            p = mock.patch.object(target, name, guarded(getattr(target, name)))
            p.start()
            self.addCleanup(p.stop)

    def run_cli(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(list(args))
        return code, out.getvalue()


# ---------- switchboard update: the choices it passes on, and --menu-app, --no-menu-app, --port N ----------

class Update(Sandbox):
    """An install without app/choices gets the menu bar; update can add
    it or take it out, and move the page to another port, through the installer it came from."""

    def setUp(self):
        super().setUp()
        self.args_file = os.path.join(self.tmp, 'installer-args.txt')
        os.environ['FAKE_INSTALLER_ARGS'] = self.args_file
        self.clone = os.path.join(self.tmp, 'clone')
        write_exe(os.path.join(self.clone, 'install.sh'), '#!/bin/sh\nprintf "%s\\n" "$@" > "$FAKE_INSTALLER_ARGS"\n')
        os.makedirs(os.path.join(self.data, 'app'))
        with open(os.path.join(self.data, 'app', 'source'), 'w') as f:
            f.write(self.clone + '\n')

    def choices(self, text):
        with open(os.path.join(self.data, 'app', 'choices'), 'w') as f:
            f.write(text + '\n')

    def args(self):
        with open(self.args_file) as f:
            return f.read().split('\n')[:-1]

    def said(self):
        """What update prints with this stand-in installer, which installs no copy: the version after is not known."""
        return 'Updating from %s\n%s\n' % (self.clone, newer.update_line((core.VERSION, core.build()), None, 0))

    def test_an_install_without_a_choices_file_gets_the_menu_bar(self):
        self.assertFalse(os.path.exists(menubar.launcher()))         # and no launcher either: still wanted
        self.assertEqual(self.run_cli('update'), (0, self.said()))
        self.assertNotIn('--no-menu-app', self.args())
        self.assertNotIn('--no-menu-app', cli.install_choices())

    def test_only_a_choice_made_at_install_keeps_it_out(self):
        self.choices('--no-agent --no-menu-app')
        self.run_cli('update')
        self.assertEqual(self.args(), ['--no-open', '--no-setup', '--no-agent', '--no-menu-app'])
        self.choices('')                                             # an install that chose nothing: it is added
        self.run_cli('update')
        self.assertEqual(self.args(), ['--no-open', '--no-setup', '--no-agent'])

    def test_menu_app_adds_it_back_after_a_choice_to_leave_it_out(self):
        self.choices('--no-agent --no-menu-app')
        self.assertEqual(self.run_cli('update', '--menu-app'), (0, self.said()))
        self.assertEqual(self.args(), ['--no-open', '--no-setup', '--no-agent'])

    def test_no_menu_app_takes_it_out(self):
        self.assertEqual(self.run_cli('update', '--no-menu-app')[0], 0)
        self.assertEqual(self.args(), ['--no-open', '--no-setup', '--no-agent', '--no-menu-app'])
        self.choices('--no-agent --no-menu-app')                     # already out: said once
        self.run_cli('update', '--no-menu-app')
        self.assertEqual(self.args().count('--no-menu-app'), 1)

    def test_port_moves_the_page_with_the_exact_command_the_page_line_names(self):
        # the line for a port in use: "Run: switchboard update --port 7801"
        self.assertEqual(self.run_cli('update', '--port', '7801'), (0, self.said()))
        self.assertEqual(self.args(), ['--no-open', '--no-setup', '--no-agent', '--port', '7801'])
        core.set_setting('port', 7861)                               # a port set before: the new one replaces it
        self.run_cli('update', '--port', '7802')
        self.assertEqual(self.args(), ['--no-open', '--no-setup', '--no-agent', '--port', '7802'])

    def test_all_of_them_together(self):
        self.choices('--no-agent --no-menu-app')
        self.run_cli('update', '--port', '7801', '--menu-app', '--link-app')
        self.assertEqual(self.args(), ['--no-open', '--no-setup', '--no-agent', '--link-app', '--port', '7801'])

    def test_words_it_does_not_take(self):
        usage = ('switchboard update [--link-app] [--menu-app | --no-menu-app] [--port N]\n'
                 'switchboard update --check [--json]\n')
        for words in (['--port'], ['--menu-app', '--no-menu-app'], ['--own-python'], ['--link-app', '--link-app'],
                      ['7801']):
            self.assertEqual(self.run_cli('update', *words), (2, usage), words)
        for n in ('80', '70000', 'x'):
            with self.assertRaises(core.SwitchError) as e:
                self.run_cli('update', '--port', n)
            self.assertEqual(str(e.exception), '--port needs a number between 1024 and 65535, like --port 7801.')
        self.assertFalse(os.path.exists(self.args_file))             # the installer never ran

    def test_the_help_names_them(self):
        self.assertIn('switchboard update --menu-app', cli.__doc__)
        self.assertIn('--no-menu-app takes it out', cli.__doc__)
        self.assertIn('switchboard update --port N', cli.__doc__)


# ---------- the words that name it: switchboard menubar and doctor ----------

class Words(Sandbox):
    """Where the menu bar is missing, the exact command that adds it; the framework line."""

    def doctor_menubar(self):
        got = []
        cli._doctor_menubar(lambda good, text, fix='': got.append((good, text, fix)))
        return got

    def test_doctor_and_the_menu_bar_name_the_command(self):
        self.assertEqual(self.doctor_menubar(), [(None, 'Menu bar: not installed',
                                                  'Add it with: switchboard update --menu-app')])
        with mock.patch.object(menubar, 'appkit_here', lambda: False), mock.patch.object(menubar, 'terminal', lambda: True):
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(menubar.main([]), 1)
        self.assertEqual(out.getvalue(), 'The menu bar is not set up here. Add it with: switchboard update --menu-app\n')

    def test_doctor_names_the_menu_bars_log_when_it_is_not_running(self):
        write_exe(menubar.launcher(), '#!/bin/sh\nexit 0\n')
        with mock.patch.object(menubar, 'libraries', lambda: {'pyobjc': '13.0', 'python': '3.14.10', 'framework': False}):
            got = self.doctor_menubar()
        running = [g for g in got if g[1].startswith('Menu bar running')]
        self.assertEqual(running, [(None, 'Menu bar running',
                                    'Start it with: switchboard menubar. Its log: %s' % os.path.join(self.data, 'menubar.log'))])

    def test_what_it_runs_on_not_loading_names_update(self):
        write_exe(menubar.launcher(), '#!/bin/sh\nexit 0\n')
        with mock.patch.object(menubar, 'libraries', lambda: None):
            got = self.doctor_menubar()
        self.assertEqual(got[0][2], "What it runs on doesn't load on the menu bar's Python. Run: switchboard update")

    def test_the_framework_line_names_update(self):
        self.assertTrue(menubar.FRAMEWORK.endswith(' Run: switchboard update'))
        self.assertNotIn('--own-python', menubar.FRAMEWORK)


class DoctorLinkApp(Sandbox):
    """Doctor with the link app installed, not yet the default browser, says to turn it on with
    `switchboard links on` (the page has no switch for it); once on, the line is ok."""

    def setUp(self):
        super().setUp()
        install(self)               # the account engine's fake: doctor never reaches the real one (its Keychain read)

    def doctor(self, status):
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(linkapp, 'status', lambda fresh=False: status), \
                mock.patch.object(browser, 'present', lambda: False), mock.patch.object(cli, 'page_running', lambda: False), \
                mock.patch.object(connect, 'claude_bin', lambda: None):
            cli.doctor()
        return out.getvalue()

    def test_installed_and_off(self):
        text = self.doctor({'installed': True, 'on': False, 'browser': 'Safari'})
        self.assertIn(' ok  Link app (~/Applications/Switchboard Links.app)\n', text)
        self.assertIn(' --  Links find their account (Switchboard Links is the default browser)\n'
                      '       Turn it on with: switchboard links on (macOS asks). Until then, use `switchboard open LINK`.\n',
                      text)
        self.assertNotIn('It needs the link app, above.', text)

    def test_installed_and_on(self):
        text = self.doctor({'installed': True, 'on': True, 'browser': 'Switchboard Links'})
        self.assertIn(' ok  Links find their account (Switchboard Links is the default browser)\n', text)
        self.assertNotIn('Turn it on with: switchboard links on', text)

    def test_not_installed_names_the_command(self):
        text = self.doctor({'installed': False, 'on': False, 'browser': None})
        self.assertIn("Add it with: switchboard update --link-app (it needs Apple's command line tools).", text)
        self.assertNotIn('Optional.', text.split('Link app (')[1].split('\n ')[0])     # not called optional
        self.assertIn('It needs the link app, above. Until then, use `switchboard open LINK`.', text)


# ---------- router/build.sh: a failed build leaves nothing ----------

class BuildLinkApp(unittest.TestCase):
    """build.sh makes the app in a temporary folder and puts it in place only once it built. Run on a
    copy of build.sh whose Info.plist has a made-up bundle identifier, with stand-in swiftc and codesign first on the
    PATH, and --no-register: nothing is compiled, signed or told to macOS."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-build-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.router = os.path.join(self.tmp, 'router')
        os.makedirs(self.router)
        shutil.copy(os.path.join(ROOT, 'router', 'build.sh'), self.router)
        with open(os.path.join(self.router, 'Info.plist'), 'w') as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0"><dict><key>CFBundleIdentifier</key>'
                    '<string>com.example.links-test</string></dict></plist>\n')
        with open(os.path.join(self.router, 'SwitchboardLinks.swift'), 'w') as f:
            f.write('// made up\n')
        self.bin = os.path.join(self.tmp, 'bin')
        self.temp = os.path.join(self.tmp, 'temp')
        os.makedirs(self.temp)
        self.dest = os.path.join(self.tmp, 'home', 'Applications')
        self.app = os.path.join(self.dest, 'Switchboard Links.app')

    def tools(self, swiftc_works=True, codesign_works=True):
        # swiftc -O -o OUT FILE: the stand-in writes OUT (a made-up program) when it "works"
        write_exe(os.path.join(self.bin, 'swiftc'), '#!/bin/sh\n' + ('printf "#!/bin/sh\\n" > "$3"\nexit 0\n' if swiftc_works
                                                                  else 'echo "error: made up" >&2\nexit 1\n'))
        write_exe(os.path.join(self.bin, 'codesign'), '#!/bin/sh\nexit %d\n' % (0 if codesign_works else 1))

    def build(self):
        env = dict(os.environ, PATH=self.bin + ':/usr/bin:/bin:/usr/sbin:/sbin', TMPDIR=self.temp + '/')
        return subprocess.run(['/bin/sh', os.path.join(self.router, 'build.sh'), '--no-register', self.dest],
                              capture_output=True, text=True, env=env, timeout=60)

    def files(self, top):
        out = []
        for folder, dirs, names in os.walk(top):
            out += [os.path.relpath(os.path.join(folder, n), top) for n in dirs + names]
        return sorted(out)

    def test_a_failed_build_leaves_nothing(self):
        for swiftc, codesign in ((False, True), (True, False)):
            self.tools(swiftc, codesign)
            r = self.build()
            self.assertNotEqual(r.returncode, 0)
            self.assertFalse(os.path.exists(os.path.dirname(self.dest)), self.files(self.tmp))  # not even ~/Applications
            self.assertEqual(os.listdir(self.temp), [])

    def test_a_failed_build_leaves_the_app_already_there_as_it_was(self):
        self.tools()
        self.assertEqual(self.build().returncode, 0)
        with open(os.path.join(self.app, 'Contents', 'MacOS', 'mark'), 'w') as f:
            f.write('the one that works\n')
        before = dict((p, os.stat(os.path.join(self.app, p)).st_mtime_ns) for p in self.files(self.app))
        self.tools(swiftc_works=False)
        self.assertNotEqual(self.build().returncode, 0)
        self.assertEqual(dict((p, os.stat(os.path.join(self.app, p)).st_mtime_ns) for p in self.files(self.app)), before)
        self.assertEqual(sorted(os.listdir(self.dest)), ['Switchboard Links.app'])
        self.assertEqual(os.listdir(self.temp), [])

    def test_a_build_that_works_takes_the_place_of_the_old_one(self):
        self.tools()
        os.makedirs(os.path.join(self.app, 'Contents'))
        with open(os.path.join(self.app, 'Contents', 'old'), 'w') as f:
            f.write('older build\n')
        r = self.build()
        self.assertEqual((r.returncode, r.stdout), (0, 'built %s\n' % self.app), r.stderr)
        self.assertEqual(self.files(self.app), ['Contents', 'Contents/Info.plist', 'Contents/MacOS',
                                                'Contents/MacOS/SwitchboardLinks', 'Contents/Resources'])
        self.assertEqual(sorted(os.listdir(self.dest)), ['Switchboard Links.app'])     # no .previous left
        self.assertEqual(os.listdir(self.temp), [])


# ---------- install.sh, piece by piece (it is run whole only by tests/install-check.sh) ----------

def install_sh():
    with open(os.path.join(ROOT, 'install.sh')) as f:
        return f.read()


def shell_function(text, name):
    """A function as install.sh writes it: from `name() {` to the next line that is only `}`."""
    start = text.index('\n%s() {\n' % name) + 1
    return text[start:text.index('\n}\n', start) + 3]


STAND_IN = '''#!/bin/sh
# A stand-in Python %(v)s%(fw)s: the real one, with its version and its framework mark made up. menu_python_ok and
# program_python_ok run it as: python -I -B -c CODE
exec "%(real)s" -I -B -c "import sys; sys.version_info = %(info)r; sys._framework = %(mark)r; exec(compile(sys.argv.pop(1), '<c>', 'exec'))" "$4" "$5"
'''


class ThePythons(unittest.TestCase):
    """The program (the page, the engine and the terminal command, all on one Python)
    runs on any Python 3.9 or newer; the menu bar item draws only on 3.12 to 3.14 (the pinned PyObjC wheels), and not
    on a framework build. menu_python_ok and program_python_ok are lifted out of install.sh and run against stand-in
    Pythons that report another version."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-python-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        text = install_sh()
        self.functions = {'menu_python_ok': shell_function(text, 'menu_python_ok'),
                          'program_python_ok': next(line for line in text.splitlines()
                                                    if line.startswith('program_python_ok() {')) + '\n'}

    def python(self, version, framework=False):
        major, minor = (int(x) for x in version.split('.'))
        path = os.path.join(self.tmp, 'python%s%s' % (version, '-framework' if framework else ''))
        write_exe(path, STAND_IN % {'v': version, 'fw': ' (a framework build)' if framework else '',
                                    'real': os.path.realpath(sys.executable), 'info': (major, minor, 0, 'final', 0),
                                    'mark': 'Python' if framework else ''})
        return path

    def ok(self, name, python):
        r = subprocess.run(['/bin/sh', '-c', self.functions[name] + '%s "$1"' % name, 'sh', python],
                           capture_output=True, timeout=60)
        return r.returncode == 0

    def test_the_menu_bar_3_12_to_3_14_and_not_a_framework_build(self):
        self.assertEqual([self.ok('menu_python_ok', self.python(v)) for v in ('3.11', '3.12', '3.13', '3.14', '3.15',
                                                                             '3.16')],
                         [False, True, True, True, False, False])
        self.assertFalse(self.ok('menu_python_ok', self.python('3.14', framework=True)))

    def test_the_program_any_3_9_or_newer(self):
        self.assertEqual([self.ok('program_python_ok', self.python(v)) for v in ('3.8', '3.9', '3.12', '3.15')],
                         [False, True, True, True])
        self.assertTrue(self.ok('program_python_ok', self.python('3.14', framework=True)))

    def test_a_python_that_is_not_there_or_does_not_start(self):
        write_exe(os.path.join(self.tmp, 'broken'), '#!/bin/sh\nexit 3\n')
        for name in self.functions:
            with self.subTest(name=name):
                self.assertFalse(self.ok(name, os.path.join(self.tmp, 'nothing')))
                self.assertFalse(self.ok(name, os.path.join(self.tmp, 'broken')))


class InstallerWords(unittest.TestCase):
    """Every curl to this Mac skips proxies; and the lines it changed, as written."""

    def test_every_curl_to_this_mac_skips_proxies(self):
        lines = [l for l in install_sh().splitlines() if 'curl ' in l and not l.lstrip().startswith('#')]
        local = [l for l in lines if any(w in l for w in ('localhost', '127.0.0.1', '$URL'))]
        self.assertTrue(local)                                       # the page check after the job starts
        for l in local:
            self.assertIn("--noproxy '*'", l)

    def test_the_stop_line_without_python_or_internet(self):
        text = install_sh()
        self.assertIn('stop "needs the internet once, to download its own Python (27 MB). Run this again when you are '
                      'online."', text)
        self.assertNotIn('xcode-select --install   (then', text)


if __name__ == '__main__':
    unittest.main()
