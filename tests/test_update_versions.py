import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# `switchboard update` ends with the version it had and the version it has now, for every way of installing: from a
# folder, from the web, and with Homebrew. Stand-in installers write a made-up copy into the data folder, as the real
# one does; nothing is downloaded and Homebrew is a stand-in script on PATH.
# An install that could not finish (offline, its own Python not downloaded) ends with the installer's own line: the
# way to finish. The update adds nothing after it.
import io
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)

from switchboard import __main__ as cli
from switchboard import core, newer


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


def installs(version, build):
    """A stand-in installer's shell lines: it puts a copy of that version and build where the real one does."""
    if version is None:
        return 'rm -rf "$SWITCHBOARD_HOME/app/switchboard"\n'
    return ('mkdir -p "$SWITCHBOARD_HOME/app/switchboard"\n'
            "printf \"VERSION = '%s'\\n\" > \"$SWITCHBOARD_HOME/app/switchboard/core.py\"\n"
            'printf "%s\\n" > "$SWITCHBOARD_HOME/app/switchboard/build.txt"\n' % (version, build))


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-update-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.data = os.path.join(self.tmp, 'data')
        self.home = os.path.join(self.tmp, 'home')
        os.makedirs(self.home)
        env = mock.patch.dict(os.environ, {'HOME': self.home, 'SWITCHBOARD_HOME': self.data,
                                           'SWITCHBOARD_PORT': str(free_port())})
        env.start()
        self.addCleanup(env.stop)
        os.makedirs(os.path.join(self.data, 'app', 'switchboard'))
        with open(os.path.join(self.data, 'app', 'switchboard', 'core.py'), 'w') as f:
            f.write("VERSION = '0.2.0'\n")
        with open(os.path.join(self.data, 'app', 'switchboard', 'build.txt'), 'w') as f:
            f.write('aaaaaaa\n')
        self.clone = os.path.join(self.tmp, 'clone')

    def source(self, text):
        with open(os.path.join(self.data, 'app', 'source'), 'w') as f:
            f.write(text + '\n')

    def run_cli(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(list(args))
        return code, out.getvalue()

    def from_folder(self, version, build, exit_code=0):
        write_exe(os.path.join(self.clone, 'install.sh'), '#!/bin/sh\n' + installs(version, build) +
                  'exit %d\n' % exit_code)
        self.source(self.clone)
        return self.run_cli('update')


class FromAFolder(Case):
    def test_a_new_version_says_from_which_to_which(self):
        code, out = self.from_folder('0.3.0', 'bbbbbbb')
        self.assertEqual(code, 0)
        self.assertEqual(out, 'Updating from %s\nUpdated Switchboard from 0.2.0 (aaaaaaa) to 0.3.0 (bbbbbbb).\n'
                         % self.clone)

    def test_a_new_build_of_the_same_number_is_an_update_too(self):
        self.assertTrue(self.from_folder('0.2.0', 'ccccccc')[1].endswith(
            'Updated Switchboard from 0.2.0 (aaaaaaa) to 0.2.0 (ccccccc).\n'))

    def test_already_the_newest(self):
        self.assertTrue(self.from_folder('0.2.0', 'aaaaaaa')[1].endswith(
            'Switchboard 0.2.0 (aaaaaaa) was already the newest.\n'))

    def test_a_failed_install_that_changed_nothing_says_it_is_still_the_same(self):
        code, out = self.from_folder('0.2.0', 'aaaaaaa', exit_code=1)
        self.assertEqual(code, 1)
        self.assertTrue(out.endswith('Switchboard is still 0.2.0 (aaaaaaa).\n'), out)

    def test_a_version_after_that_cannot_be_read_is_said_never_guessed(self):
        code, out = self.from_folder(None, None)
        self.assertEqual(out.splitlines()[-1], 'Could not read which version is installed now. Before the update it '
                                               'was 0.2.0 (aaaaaaa).')
        self.assertNotIn('0.3.0', out)

    def test_without_a_build_the_number_alone(self):
        code, out = self.from_folder('0.3.0', 'not-a-commit')
        self.assertEqual(out.splitlines()[-1], 'Updated Switchboard from 0.2.0 (aaaaaaa) to 0.3.0.')


class FromTheWeb(Case):
    def test_the_installer_from_its_address_and_the_line_after(self):
        script = ('#!/bin/sh\n' + installs('0.4.0', 'ddddddd')).encode()
        self.source('https://example.invalid/switchboard/main/install.sh')
        with mock.patch.object(cli, 'fetch', lambda url: script):
            code, out = self.run_cli('update')
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines(), ['Updating from https://example.invalid/switchboard/main/install.sh',
                                            'Updated Switchboard from 0.2.0 (aaaaaaa) to 0.4.0 (ddddddd).'])


class WithHomebrew(Case):
    def brew(self, version, build):
        bin_dir = os.path.join(self.tmp, 'bin')
        write_exe(os.path.join(bin_dir, 'brew'), '#!/bin/sh\n' + installs(version, build))
        p = mock.patch.dict(os.environ, {'PATH': bin_dir + os.pathsep + '/usr/bin:/bin'})
        p.start()
        self.addCleanup(p.stop)
        self.source('homebrew:' + self.clone)

    def test_homebrew_says_from_which_to_which(self):
        self.brew('0.3.0', 'eeeeeee')
        code, out = self.run_cli('update')
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines(), ['Updating with Homebrew: brew upgrade switchboard',
                                            'Updated Switchboard from 0.2.0 (aaaaaaa) to 0.3.0 (eeeeeee).'])

    def test_homebrew_already_the_newest(self):
        self.brew('0.2.0', 'aaaaaaa')
        self.assertEqual(self.run_cli('update')[1].splitlines()[-1],
                         'Switchboard 0.2.0 (aaaaaaa) was already the newest.')


FINISH = 'Run this installer again when you are online to finish.'
FINISH_BREW = 'Run brew reinstall switchboard when you are online to finish.'


def unfinished(last):
    """A stand-in installer's shell lines for an install that could not download Switchboard's own Python: the record
    the real one leaves (--own-python among its choices, the program on the Python found on this Mac) and its last
    line, the way to finish."""
    return ('mkdir -p "$SWITCHBOARD_HOME/app" "$SWITCHBOARD_HOME/runtime"\n'
            'printf "%s\\n" "--no-agent --own-python" > "$SWITCHBOARD_HOME/app/choices"\n'
            'printf "%s\\n" \'{"python_source": "found"}\' > "$SWITCHBOARD_HOME/runtime/python.json"\n'
            'echo "Switchboard runs on the Python found on this Mac until its own can be downloaded."\n'
            'echo "' + last + '"\n')


class AnInstallThatCouldNotFinish(Case):
    """Offline, the installer can't download Switchboard's own Python: it says how to finish, and that is the last
    thing read. The update adds no line after it, and never "was already the newest"."""

    def brew_that_finds_nothing_newer(self):
        WithHomebrew.brew(self, '0.2.0', 'aaaaaaa')

    def test_from_a_folder_the_update_adds_no_line_after_the_installers(self):
        write_exe(os.path.join(self.clone, 'install.sh'), '#!/bin/sh\n' + installs('0.2.0', 'aaaaaaa') +
                  unfinished(FINISH) + 'exit 1\n')
        self.source(self.clone)
        self.assertEqual(self.run_cli('update'), (1, 'Updating from %s\n' % self.clone))

    def test_with_homebrew_and_a_change_asked_it_adds_no_line_either(self):
        self.brew_that_finds_nothing_newer()
        write_exe(os.path.join(self.clone, 'install.sh'), '#!/bin/sh\n' + unfinished(FINISH_BREW) + 'exit 0\n')
        self.assertEqual(self.run_cli('update', '--port', '7844'),
                         (0, 'Updating with Homebrew: brew upgrade switchboard\n'))

    def test_the_last_line_a_person_reads_is_the_way_to_finish(self):
        # the whole command as a child of its own, so the installer's lines are in what is read; it gets this test's
        # throwaway data folder, home and port, and the stand-in brew first on its PATH
        self.brew_that_finds_nothing_newer()
        write_exe(os.path.join(self.clone, 'install.sh'), '#!/bin/sh\n' + unfinished(FINISH_BREW) + 'exit 0\n')
        r = subprocess.run([sys.executable, '-B', '-m', 'switchboard', 'update', '--port', '7844'], cwd=ROOT,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(r.stdout.strip().splitlines()[-1], FINISH_BREW, r.stdout)
        self.assertNotIn('already the newest', r.stdout)

    def test_an_install_left_unfinished_earlier_still_gets_its_line_when_no_installer_ran(self):
        os.makedirs(os.path.join(self.data, 'runtime'))
        with open(os.path.join(self.data, 'app', 'choices'), 'w') as f:
            f.write('--no-agent --own-python\n')
        with open(os.path.join(self.data, 'runtime', 'python.json'), 'w') as f:
            f.write('{"python_source": "found"}\n')
        self.brew_that_finds_nothing_newer()
        self.assertEqual(self.run_cli('update')[1].splitlines()[-1],
                         'Switchboard 0.2.0 (aaaaaaa) was already the newest.')

    def test_an_update_that_finished_says_its_versions_as_ever(self):
        write_exe(os.path.join(self.clone, 'install.sh'), '#!/bin/sh\n' + installs('0.3.0', 'bbbbbbb') +
                  'mkdir -p "$SWITCHBOARD_HOME/runtime"\n'
                  'printf "%s\\n" "--no-agent --own-python" > "$SWITCHBOARD_HOME/app/choices"\n'
                  'printf "%s\\n" \'{"python_source": "own"}\' > "$SWITCHBOARD_HOME/runtime/python.json"\n')
        self.source(self.clone)
        self.assertEqual(self.run_cli('update')[1].splitlines()[-1],
                         'Updated Switchboard from 0.2.0 (aaaaaaa) to 0.3.0 (bbbbbbb).')


class TheLine(unittest.TestCase):
    """newer.update_line on its own: every case it can be given."""

    def test_each_case(self):
        a, b = ('0.2.0', 'aaaaaaa'), ('0.3.0', 'bbbbbbb')
        self.assertEqual(newer.update_line(a, b, 0), 'Updated Switchboard from 0.2.0 (aaaaaaa) to 0.3.0 (bbbbbbb).')
        self.assertEqual(newer.update_line(a, a, 0), 'Switchboard 0.2.0 (aaaaaaa) was already the newest.')
        self.assertEqual(newer.update_line(a, a, 3), 'Switchboard is still 0.2.0 (aaaaaaa).')
        self.assertEqual(newer.update_line(a, None, 0),
                         'Could not read which version is installed now. Before the update it was 0.2.0 (aaaaaaa).')
        self.assertEqual(newer.update_line(None, None, 0), 'Could not read which version is installed now.')
        self.assertEqual(newer.update_line(None, b, 0), 'Switchboard is now 0.3.0 (bbbbbbb).')

    def test_the_installed_copy_is_read_as_the_installer_reads_it(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        self.assertIsNone(newer.version_in(folder))
        os.makedirs(os.path.join(folder, 'switchboard'))
        with open(os.path.join(folder, 'switchboard', 'core.py'), 'w') as f:
            f.write("import os\nVERSION = '1.10.2'    # the version\nOTHER = 'x'\n")
        self.assertEqual(newer.version_in(folder), ('1.10.2', None))
        self.assertEqual(core.VERSION, newer.version_in(ROOT)[0])


if __name__ == '__main__':
    unittest.main()
