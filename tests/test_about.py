import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# `switchboard about`: the facts of this install, each checked when it is asked for (switchboard/about.py).
#
# Every line from stand-in facts: a program folder made here (its version, commit, licence and uninstaller), the
# note of where it came from (app/source), made-up apps with made-up property lists, background jobs' property lists
# in a throwaway home, and stand-ins for the page (page_status, same_folder) and for codesign and spctl (run). Nothing
# here starts codesign, spctl, launchd or a page; nothing reaches the network. The installer is never run whole: its
# end block is cut out of install.sh and run alone in sh, with a stand-in for the command it calls.
import io
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)

from switchboard import __main__ as cli
from switchboard import about, core, linkapp, menubar, menubar_wrap

PORT = '7981'
HEX = 'ab' * 32
TEAM = 'A1B2C3D4E5'
DEV_ID = ('Executable=/x\nIdentifier=io.example.links\nFormat=app bundle with Mach-O thin (arm64)\n'
          'Authority=Developer ID Application: Example Maker (%s)\nAuthority=Developer ID Certification Authority\n'
          'Authority=Apple Root CA\nTeamIdentifier=%s\n' % (TEAM, TEAM))
ADHOC = 'Executable=/x\nIdentifier=SwitchboardLinks\nSignature=adhoc\nTeamIdentifier=not set\n'
UNSIGNED = '/x: code object is not signed at all\n'
NOTARIZED = '/x: accepted\nsource=Notarized Developer ID\norigin=Developer ID Application: Example Maker (%s)\n' % TEAM
DEV_ONLY = '/x: accepted\nsource=Developer ID\norigin=Developer ID Application: Example Maker (%s)\n' % TEAM
REJECTED = '/x: rejected\nsource=Unnotarized Developer ID\n'


def write(path, text='', mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(text)
    if mode is not None:
        os.chmod(path, mode)


def write_plist(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        plistlib.dump(doc, f)


class Runner:
    """A stand-in for running codesign and spctl: each program's answer by its path, (status, text), or an exception
    to raise. Every call is noted."""

    def __init__(self, codesign=(0, ADHOC), spctl=(0, NOTARIZED)):
        self.answers = {about.CODESIGN: codesign, about.SPCTL: spctl}
        self.calls = []

    def __call__(self, argv, timeout):
        self.calls.append((list(argv), timeout))
        got = self.answers[argv[0]]
        if isinstance(got, BaseException):
            raise got
        return got


def page(status='running', folder_ok=True):
    """A stand-in for the command line's page checks."""
    def same_folder():
        if folder_ok is not True:
            raise folder_ok
    return types.SimpleNamespace(page_status=lambda: status, same_folder=same_folder)


class Install(unittest.TestCase):
    """A made-up install in a throwaway home: the program in <data>/app with its SHA256SUMS, the command linked in
    ~/.local/bin with sb beside it, the two apps, the two background jobs."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix='switchboard-about-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, 'home')
        self.data = os.path.join(self.home, '.switchboard')
        self.apps_dir = os.path.join(self.home, 'Applications')     # where the menu bar's app goes without /Applications
        os.makedirs(self.apps_dir)
        os.makedirs(os.path.join(self.home, '.claude'))
        env = mock.patch.dict(os.environ, {'HOME': self.home, 'SWITCHBOARD_HOME': self.data, 'SWITCHBOARD_PORT': PORT,
                                           'CLAUDE_CONFIG_DIR': os.path.join(self.home, '.claude'),
                                           'SWITCHBOARD_APPLICATIONS': self.apps_dir})
        env.start()
        self.addCleanup(env.stop)
        os.makedirs(self.data, mode=0o700)
        os.chmod(self.data, 0o700)
        self.app = os.path.join(self.data, 'app')
        write(os.path.join(self.app, 'switchboard', 'core.py'), "VERSION = '0.2.0'\n")
        write(os.path.join(self.app, 'switchboard', 'build.txt'), 'd3da1a4\n')
        write(os.path.join(self.app, 'switchboard', 'web', 'index.html'), '<!doctype html>\n')
        write(os.path.join(self.app, 'router', 'build.sh'), '#!/bin/sh\n')
        write(os.path.join(self.app, 'LICENSE'), 'MIT License\n\nCopyright (c) Example\n')
        write(os.path.join(self.app, 'uninstall.sh'), '#!/bin/sh\n')
        self.source('https://raw.githubusercontent.com/example/switchboard/main/install.sh')
        about.write_manifest(self.app)
        self.sb = os.path.join(self.data, 'bin', 'switchboard')
        write(self.sb, '#!/bin/sh\n', 0o755)
        local = os.path.join(self.home, '.local', 'bin')
        os.makedirs(local)
        os.symlink(self.sb, os.path.join(local, 'switchboard'))
        os.symlink(self.sb, os.path.join(local, 'sb'))
        self.page_cmd = os.path.join(self.data, 'bin', 'switchboard-page')
        write(self.page_cmd, '#!/bin/sh\n', 0o755)
        self.links = os.path.join(self.home, 'Applications', 'Switchboard Links.app')
        self.menu_app = os.path.join(self.apps_dir, 'Switchboard.app')
        self.links_app()
        self.menubar_app()
        self.jobs()
        self.pid = mock.patch.object(menubar, 'running_pid', lambda: 4242)
        self.pid.start()
        self.addCleanup(self.pid.stop)

    def source(self, text):
        write(os.path.join(self.app, 'source'), text + '\n')

    def links_app(self):
        write_plist(os.path.join(self.links, 'Contents', 'Info.plist'),
                    {'CFBundleIdentifier': linkapp.BUNDLE_ID, 'CFBundleExecutable': 'SwitchboardLinks'})
        write(os.path.join(self.links, 'Contents', 'MacOS', 'SwitchboardLinks'), '#!/bin/sh\n', 0o755)

    def menubar_app(self):
        write_plist(os.path.join(self.menu_app, 'Contents', 'Info.plist'),
                    {'CFBundleIdentifier': menubar_wrap.BUNDLE_ID, menubar_wrap.DATA_KEY: os.path.realpath(self.data)})
        write(os.path.join(self.menu_app, 'Contents', 'MacOS', 'python'), '#!/bin/sh\n', 0o755)
        write(menubar.launcher(), '#!/bin/sh\n', 0o755)

    def jobs(self):
        agents = os.path.join(self.home, 'Library', 'LaunchAgents')
        write_plist(os.path.join(agents, menubar.PAGE_AGENT + '.plist'),
                    {'Label': menubar.PAGE_AGENT, 'ProgramArguments': [self.page_cmd], 'RunAtLoad': True})
        write_plist(os.path.join(agents, menubar.AGENT + '.plist'),
                    {'Label': menubar.AGENT, 'ProgramArguments': [menubar.launcher()], 'RunAtLoad': True})

    def accounts(self, *emails):
        with open(os.path.join(self.data, 'accounts.json'), 'w') as f:
            json.dump({'schema': 1, 'accounts': dict(('id%d' % i, {'email': e}) for i, e in enumerate(emails))}, f)

    def facts(self, runner=None, cli_=None, **kw):
        return about.gather(cli_ or page(), run=runner or Runner(), folder=self.app, **kw)

    def text(self, runner=None, cli_=None, short=False, done=None, cmd='switchboard', setup_now=False, colour=False):
        facts = self.facts(runner, cli_)
        return '\n'.join(about.block(facts, short, done, cmd, setup_now, colour)) + '\n'

    def rows(self, runner=None, cli_=None):
        return dict(about.rows(self.facts(runner, cli_)))


class EveryLine(Install):
    def test_a_homebrew_install_line_by_line(self):
        copy = os.path.join(self.home, 'brew', 'Caskroom', 'switchboard', '0.2.0', 'switchboard-0.2.0')
        write(os.path.join(copy, 'install.sh'), '#!/bin/sh\nREPO="${SWITCHBOARD_REPO:-example/switchboard}"\n')
        write(os.path.join(self.home, 'brew', 'Caskroom', 'switchboard', '.metadata', '0.2.0', '20261126153300.100',
                           'Casks', 'switchboard.rb'), 'cask "switchboard" do\n  version "0.2.0"\n  sha256 "%s"\nend\n' % HEX)
        self.source('homebrew:' + copy)
        brew_bin = os.path.join(self.home, 'brew', 'bin')
        os.makedirs(brew_bin)
        launcher = os.path.join(copy, 'packaging', 'homebrew', 'launcher')
        write(launcher, '#!/bin/sh\n', 0o755)
        os.symlink(launcher, os.path.join(brew_bin, 'switchboard'))
        os.symlink('switchboard', os.path.join(brew_bin, 'sb'))
        for name in ('switchboard', 'sb'):
            os.remove(os.path.join(self.home, '.local', 'bin', name))
        about.write_manifest(self.app)
        self.accounts('maya@northwind.example', 'maya@ivy.example')
        runner = Runner()
        runner.answers[about.CODESIGN] = (0, DEV_ID)

        def menu_codesign(argv, timeout):
            return (0, ADHOC) if argv[0] == about.CODESIGN and argv[-1] == self.menu_app else runner(argv, timeout)
        got = self.text(menu_codesign)
        self.assertEqual(got, (
            'Switchboard 0.2.0 (d3da1a4)  MIT  github.com/example/switchboard\n'
            '\n'
            '  Verified    Homebrew checked the release file against its SHA-256 before anything ran\n'
            '              sha256 %s\n'
            '  Signed      Switchboard Links: Developer ID (%s), notarized by Apple\n'
            '              Switchboard.app: made and signed on this Mac (ad-hoc)\n'
            '  Runs as     you: no administrator password was asked for or used; every file it added is yours\n'
            '  Installed   ~/.switchboard   ~/brew/bin/switchboard and sb   ~/Applications/Switchboard.app\n'
            '              ~/Applications/Switchboard Links.app\n'
            '  Running     the menu bar item, and the page on 127.0.0.1:%s (this Mac only); both start at login\n'
            '  Program     7 files, unchanged since install (checked against ~/.switchboard/app/SHA256SUMS)\n'
            '  Sign-ins    2 accounts, each login kept in your macOS Keychain\n'
            '  Privacy     no telemetry; sends nothing of yours off this Mac except to Anthropic\n'
            '              and to providers you add\n'
            '              the look for a newer version is off\n'
            '  Downloaded  the release from github.com (by Homebrew)\n'
            '  Remove      brew uninstall switchboard   (--zap also removes your accounts)\n'
            '\n'
            'Type switchboard to see your accounts.\n') % (HEX, TEAM, PORT))
        for line in got.splitlines():
            self.assertLessEqual(len(line), about.WIDTH, line)

    def test_the_one_line_install(self):
        got = self.text()
        self.assertTrue(got.startswith('Switchboard 0.2.0 (d3da1a4)  MIT  github.com/example/switchboard\n\n'), got)
        self.assertIn('\n  Source      the newest code on main, with no checksum (an install with Homebrew checks one)\n',
                      got)
        self.assertNotIn('Verified', got)
        self.assertIn('\n  Installed   ~/.switchboard   ~/.local/bin/switchboard and sb   ~/Applications/Switchboard.app\n'
                      '              ~/Applications/Switchboard Links.app\n', got)
        self.assertIn('\n  Remove      sh ~/.switchboard/app/uninstall.sh   (--all also removes your accounts)\n', got)
        self.assertTrue(got.endswith('\n\nNext: switchboard\n'), got)

    def test_where_it_came_from_and_never_a_placeholder(self):
        # The unpublished copy's placeholder, split so that publishing (which fills the owner into the tests) keeps it
        self.source('https://raw.githubusercontent.com/' + 'OWN' 'ER/switchboard/main/install.sh')
        self.assertTrue(self.text().startswith('Switchboard 0.2.0 (d3da1a4)  MIT\n'))
        folder = os.path.join(self.home, 'src', 'switchboard')
        self.source(folder)
        self.assertEqual(self.rows()['Source'], ['copied from ~/src/switchboard; nothing was downloaded'])
        os.remove(os.path.join(self.app, 'source'))
        self.assertEqual(self.rows()['Source'], ['not recorded: installed by a version before this was kept'])
        self.assertNotIn('Verified', self.rows())

    def test_a_cask_without_a_checksum_is_never_verified(self):
        copy = os.path.join(self.home, 'brew', 'Caskroom', 'switchboard', '0.2.0', 'switchboard-0.2.0')
        write(os.path.join(self.home, 'brew', 'Caskroom', 'switchboard', '.metadata', '0.2.0', '1', 'Casks',
                           'switchboard.rb'), 'cask "switchboard" do\n  sha256 :no_check\nend\n')
        self.source('homebrew:' + copy)
        rows = self.rows()
        self.assertNotIn('Verified', rows)
        self.assertEqual(rows['Source'], ['Homebrew, with no checksum: the cask it installed from says sha256 :no_check'])

    def test_homebrews_installer_step_run_from_outside_a_caskroom_checked_nothing(self):
        self.source('homebrew:' + os.path.join(self.home, 'src', 'switchboard'))
        rows = self.rows()
        self.assertNotIn('Verified', rows)
        self.assertEqual(rows['Source'], ['~/src/switchboard, run as Homebrew runs it but not from its Caskroom, so no '
                                          'checksum is known'])

    def test_homebrew_without_its_cask_record_says_the_check_without_the_number(self):
        self.source('homebrew:' + os.path.join(self.home, 'brew', 'Caskroom', 'switchboard', '0.2.0', 'x'))
        self.assertEqual(self.rows()['Verified'],
                         ['Homebrew checked the release file against its SHA-256 before anything ran'])

    def test_a_release_unpacked_straight_into_its_version_folder_is_in_the_caskroom(self):
        self.source('homebrew:' + os.path.join(self.home, 'brew', 'Caskroom', 'switchboard', '0.2.0'))
        self.assertIn('Verified', self.rows())
        self.source('homebrew:' + os.path.join(self.home, 'brew', 'Caskroom', 'switchboard'))
        self.assertNotIn('Verified', self.rows())

    def test_what_a_choice_left_out_is_left_out(self):
        shutil.rmtree(self.links)
        shutil.rmtree(self.menu_app)
        os.remove(menubar.launcher())
        os.remove(os.path.join(self.home, 'Library', 'LaunchAgents', menubar.AGENT + '.plist'))
        rows = self.rows()
        self.assertNotIn('Signed', rows)
        self.assertEqual(rows['Installed'], ['~/.switchboard', '~/.local/bin/switchboard and sb'])
        self.assertEqual(rows['Running'], ['the page on 127.0.0.1:%s (this Mac only); it starts at login' % PORT])

    def test_the_next_step(self):
        self.assertEqual(self.text().splitlines()[-1], 'Next: switchboard')
        self.accounts('maya@northwind.example')
        self.assertEqual(self.text(cmd='sb').splitlines()[-1], 'Type sb to see your accounts.')


class Signatures(Install):
    """Each answer macOS can give, said as macOS said it."""

    def said(self, codesign, spctl=(0, NOTARIZED)):
        r = Runner(codesign, spctl)
        return about.signature_words(about.signature('Switchboard Links', self.links, r)), r.calls

    def test_developer_id_notarized(self):
        said, calls = self.said((0, DEV_ID), (0, NOTARIZED))
        self.assertEqual(said, 'Switchboard Links: Developer ID (%s), notarized by Apple' % TEAM)
        self.assertEqual(calls, [([about.CODESIGN, '-dv', '--verbose=2', self.links], 3.0),
                                 ([about.SPCTL, '--assess', '--type', 'execute', '-vv', self.links], 3.0)])

    def test_developer_id_not_notarized(self):
        self.assertEqual(self.said((0, DEV_ID), (0, DEV_ONLY))[0],
                         'Switchboard Links: Developer ID (%s), not notarized' % TEAM)

    def test_developer_id_that_gatekeeper_rejects(self):
        self.assertEqual(self.said((0, DEV_ID), (3, REJECTED))[0],
                         'Switchboard Links: Developer ID (%s); macOS does not accept it (spctl: rejected)' % TEAM)

    def test_ad_hoc_asks_spctl_nothing(self):
        said, calls = self.said((0, ADHOC))
        self.assertEqual(said, 'Switchboard Links: made and signed on this Mac (ad-hoc)')
        self.assertEqual(len(calls), 1)

    def test_not_signed(self):
        self.assertEqual(self.said((1, UNSIGNED))[0], 'Switchboard Links: not signed')

    def test_a_tool_that_is_missing(self):
        self.assertEqual(self.said(FileNotFoundError(2, 'No such file'))[0],
                         'Switchboard Links: could not be checked: /usr/bin/codesign is not on this Mac')
        self.assertEqual(self.said((0, DEV_ID), FileNotFoundError(2, 'No such file'))[0],
                         'Switchboard Links: Developer ID (%s); whether Apple notarized it could not be checked: '
                         '/usr/sbin/spctl is not on this Mac' % TEAM)

    def test_a_time_out(self):
        late = subprocess.TimeoutExpired(about.CODESIGN, 3.0)
        self.assertEqual(self.said(late)[0], 'Switchboard Links: could not be checked: codesign did not answer in 3 s')
        self.assertEqual(self.said((0, DEV_ID), subprocess.TimeoutExpired(about.SPCTL, 3.0))[0],
                         'Switchboard Links: Developer ID (%s); whether Apple notarized it could not be checked in 3 s'
                         % TEAM)

    def test_an_app_that_is_missing(self):
        shutil.rmtree(self.links)
        r = Runner()
        self.assertEqual(about.signature_words(about.signature('Switchboard Links', self.links, r)),
                         'Switchboard Links: not found at ~/Applications/Switchboard Links.app')
        self.assertEqual(r.calls, [])

    def test_some_other_answer_is_quoted_short(self):
        self.assertEqual(self.said((1, '/x: invalid signature (code or signature have been modified)\n'))[0],
                         'Switchboard Links: could not be checked (codesign: /x: invalid signature (code or signature '
                         'have been modified))')

    def test_the_real_runner_has_the_time_limit(self):
        with mock.patch.object(about.subprocess, 'run', side_effect=subprocess.TimeoutExpired('x', 3.0)) as run:
            with self.assertRaises(subprocess.TimeoutExpired):
                about.run_tool([about.CODESIGN, '-dv', self.links], about.TOOL_S)
        self.assertEqual(run.call_args[1]['timeout'], 3.0)


class Program(Install):
    def program(self):
        return self.rows()['Program']

    def test_unchanged(self):
        self.assertEqual(self.program(), ['7 files, unchanged since install (checked against '
                                          '~/.switchboard/app/SHA256SUMS)'])

    def test_one_file_changed(self):
        write(os.path.join(self.app, 'switchboard', 'core.py'), "VERSION = '0.2.0'\n# changed\n")
        self.assertEqual(self.program(), ['1 file of 7 changed since install: switchboard/core.py',
                                          'Put them back with: switchboard update'])

    def test_one_missing_and_one_added(self):
        os.remove(os.path.join(self.app, 'router', 'build.sh'))
        write(os.path.join(self.app, 'switchboard', 'json.py'), '')
        self.assertEqual(self.program(), ['1 file missing: router/build.sh',
                                          '1 file added since install: switchboard/json.py',
                                          'Put them back with: switchboard update'])

    def test_bytecode_and_finder_notes_are_not_the_program(self):
        write(os.path.join(self.app, 'switchboard', '__pycache__', 'core.cpython-39.pyc'), '')
        write(os.path.join(self.app, '.DS_Store'), '')
        self.assertEqual(about.check_program(self.app)['state'], 'unchanged')

    def test_no_list(self):
        os.remove(os.path.join(self.app, about.MANIFEST))
        self.assertEqual(self.program(), ['no list of its files to check against: installed by a version before '
                                          'this one'])

    def test_a_list_that_is_not_one(self):
        write(os.path.join(self.app, about.MANIFEST), 'not a list\n')
        self.assertEqual(self.program(), ['~/.switchboard/app/SHA256SUMS is not a list of checksums, so nothing '
                                          'could be checked', 'Put them back with: switchboard update'])

    def test_the_list_is_what_shasum_checks(self):
        with open(os.path.join(self.app, about.MANIFEST)) as f:
            self.assertEqual(len(f.read().splitlines()), 7)
        r = subprocess.run(['/usr/bin/shasum', '-a', '256', '-c', about.MANIFEST], cwd=self.app, capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.count(': OK'), 7)

    def test_the_short_form_for_an_update(self):
        text = self.text(short=True, done=9)
        self.assertEqual(text, (
            '\n'
            'Switchboard 0.2.0 (d3da1a4)  MIT  github.com/example/switchboard\n'
            '\n'
            '  Program     7 files, unchanged since install (checked against ~/.switchboard/app/SHA256SUMS)\n'
            '  Running     the menu bar item, and the page on 127.0.0.1:%s (this Mac only); both start at login\n'
            '\n'
            'Done in 9 s. Next: switchboard\n') % PORT)

    def test_doctor_says_it_in_one_line(self):
        def lines():
            got = []
            about.doctor_line(lambda good, text, fix='': got.append((good, text, fix)), self.app)
            return got
        self.assertEqual(lines(), [(True, 'Program files unchanged since install (7, SHA256SUMS)', '')])
        write(os.path.join(self.app, 'switchboard', 'core.py'), '')
        self.assertEqual(lines(), [(False, 'Program files: 1 file of 7 changed since install: switchboard/core.py',
                                    'Put them back with: switchboard update')])
        os.remove(os.path.join(self.app, about.MANIFEST))
        self.assertEqual(lines(), [(None, 'Program files: no list of its files to check against: installed by a '
                                          'version before this one', 'Run switchboard update to add the list.')])
        with mock.patch.object(about, 'check_program', side_effect=RuntimeError('made up')):
            self.assertEqual(lines(), [])


class Running(Install):
    def running(self, cli_=None, **kw):
        return about.running_words(about.running(cli_ or page(), **kw))

    def test_nothing_runs_without_the_jobs(self):
        shutil.rmtree(os.path.join(self.home, 'Library', 'LaunchAgents'))
        with mock.patch.object(menubar, 'running_pid', lambda: None):
            self.assertEqual(self.running(page('down')), ['the page is not running: start it with switchboard serve',
                                                          'the menu bar item is not running: start it with '
                                                          'switchboard menubar'])

    def test_a_page_with_its_job_that_does_not_answer(self):
        self.assertEqual(self.running(page('down')),
                         ['the menu bar item; both start at login',
                          'the page is not running yet: switchboard doctor says why'])

    def test_another_installs_page_on_the_port(self):
        from switchboard import engine
        got = self.running(page('running', engine.Refused('another folder')))
        self.assertEqual(got[1], "another page answers on 127.0.0.1:%s, not this install's: switchboard doctor says "
                                 'more' % PORT)

    def test_a_busy_page_is_running(self):
        self.assertEqual(self.running(page('busy'))[0], 'the menu bar item, and the page on 127.0.0.1:%s (this Mac '
                                                        'only); both start at login' % PORT)

    def test_after_an_install_it_waits_for_the_menu_bar_it_just_started(self):
        clock = [0.0]
        seen = iter([None, None, 77])
        with mock.patch.object(menubar, 'running_pid', lambda: next(seen)):
            got = about.running(page(), wait=5.0, sleep=lambda s: clock.__setitem__(0, clock[0] + s),
                                clock=lambda: clock[0])
        self.assertTrue(got['menubar'])
        self.assertEqual(clock[0], 0.5)
        with mock.patch.object(menubar, 'running_pid', lambda: None):
            got = about.running(page(), wait=5.0, sleep=lambda s: clock.__setitem__(0, clock[0] + s),
                                clock=lambda: clock[0])
        self.assertFalse(got['menubar'])


class SignInsPrivacyAndWhoseFiles(Install):
    def test_recovery_copies_in_files_are_said(self):
        self.accounts('maya@northwind.example')
        write(os.path.join(self.data, 'recovery', 'manifest.json'), '{"entries": []}')
        write(os.path.join(self.data, 'recovery', 'successor-x.json'), '{}')
        self.assertEqual(self.rows()['Sign-ins'], [
            '1 account, each login kept in your macOS Keychain',
            '1 recovery copy in a file only you can read, since the Keychain refused it (switchboard leftovers)'])

    def test_what_this_install_has_turned_on_is_in_privacy(self):
        from switchboard.providers import choices
        choices.add('codex', 1.0)
        core.set_setting('update_check', 'daily')
        write(os.path.join(self.data, 'gateway-lanes.json'), json.dumps({'lanes': [
            {'name': 'a', 'base_url': 'https://api.anthropic.com'}, {'name': 'b', 'base_url': 'https://llm.example'}]}))
        self.assertEqual(self.rows()['Privacy'], [
            'no telemetry; sends nothing of yours off this Mac except to Anthropic',
            'and to providers you add',
            'and to OpenAI through Codex after your yes for a folder',
            'and to llm.example, for the key you added',
            'a look for a newer version once a day is on (switchboard config set update_check off)'])

    def test_a_file_of_another_user_and_a_folder_others_can_read(self):
        os.chmod(self.data, 0o755)
        real = os.getuid()
        with mock.patch.object(about.os, 'getuid', lambda: real + 1):
            r = about.runs_as(self.app, [])
        self.assertTrue(r['others'])
        words = about.runs_as_words(r)
        self.assertTrue(words[0].startswith('you, but files it added belong to another user: ~/.switchboard/app'),
                        words)
        self.assertNotIn('password', words[0])
        self.assertEqual(words[1], '~/.switchboard can be read by other users of this Mac: chmod 700 ~/.switchboard')


class Downloads(Install):
    """What this install downloaded and from where: install.sh's hosts, said only for a piece this install has."""

    def record(self, **rec):
        write(os.path.join(self.data, 'runtime', 'python.json'), json.dumps(rec))

    def own_python(self):
        write(os.path.join(self.data, 'runtime', 'python', 'bin', 'python3'), '#!/bin/sh\n', 0o755)

    def menu_packages(self):
        os.makedirs(os.path.join(self.data, 'runtime', 'env-0123456789abcdef', 'bin'))
        os.symlink('env-0123456789abcdef', os.path.join(self.data, 'runtime', 'env'))

    def test_the_one_line_install_with_its_own_python_and_the_menu_bars_packages(self):
        own = os.path.join(self.data, 'runtime', 'python', 'bin', 'python3')
        self.record(python='/usr/bin/python3', python_source='found', menu_python=own, menu_python_source='own')
        self.own_python()
        self.menu_packages()
        self.assertEqual(self.rows()['Downloaded'], [
            "the program from github.com, Switchboard's own Python from github.com, PyObjC for the menu bar from "
            "pypi.org"])
        self.assertEqual(self.facts()['downloaded'], [
            'the program from github.com', "Switchboard's own Python from github.com",
            'PyObjC for the menu bar from pypi.org'])

    def test_a_piece_recorded_but_not_there_is_not_said(self):
        self.record(python=os.path.join(self.data, 'runtime', 'python', 'bin', 'python3'), python_source='own',
                    menu_python='/opt/example/python3.13', menu_python_source='found')
        self.assertEqual(self.rows()['Downloaded'], ['the program from github.com'])

    def test_nothing_downloaded(self):
        self.source(os.path.join(self.home, 'src', 'switchboard'))
        self.record(python='/usr/bin/python3', python_source='found', menu_python='', menu_python_source='')
        self.menu_packages()                                    # left by an earlier install; not this one's
        self.assertEqual(self.rows()['Downloaded'], ['nothing: every piece was already on this Mac'])

    def test_homebrews_step_run_by_hand_downloaded_no_release(self):
        self.source('homebrew:' + os.path.join(self.home, 'src', 'switchboard'))
        self.record(python='/usr/bin/python3', python_source='found', menu_python='/opt/example/python3.13',
                    menu_python_source='found')
        self.menu_packages()
        self.assertEqual(self.rows()['Downloaded'], ['PyObjC for the menu bar from pypi.org'])

    def test_an_update_leaves_it_out(self):
        self.assertNotIn('Downloaded', dict(about.rows(self.facts(), short=True)))


class TheCommand(Install):
    def run_about(self, *args, tty=False):
        out = io.StringIO()
        out.isatty = lambda: tty
        with mock.patch.object(about, 'run_tool', Runner()), mock.patch.dict(os.environ, {'TERM': 'xterm'}), \
                mock.patch.object(about, 'program_folder', lambda: self.app):     # the installed copy runs it
            os.environ.pop('NO_COLOR', None)
            with mock.patch.object(cli, 'page_status', lambda: 'running'), \
                    mock.patch.object(cli, 'same_folder', lambda: None):
                code = about.main(list(args), cli, out=out, wait_s=0)
        return code, out.getvalue()

    def test_plain_text_without_a_terminal(self):
        code, out = self.run_about()
        self.assertEqual(code, 0)
        self.assertNotIn('\x1b', out)
        self.assertIn('  Program     7 files, unchanged since install', out)

    def test_colour_at_a_terminal_as_the_rest_of_the_terminal_output(self):
        code, out = self.run_about(tty=True)
        self.assertTrue(out.startswith('\x1b[1mSwitchboard 0.2.0 (d3da1a4)\x1b[0m\x1b[2m  MIT  '), out)
        self.assertIn('\x1b[2m  Program     \x1b[0m7 files', out)
        self.assertTrue(out.endswith('\n\nNext: \x1b[1mswitchboard\x1b[0m\n'), out)

    def test_json(self):
        code, out = self.run_about('--json')
        doc = json.loads(out)
        self.assertEqual((code, doc['schema'], doc['version'], doc['build']), (0, 1, '0.2.0', 'd3da1a4'))
        self.assertEqual(doc['repository'], 'github.com/example/switchboard')
        self.assertEqual(doc['source']['how'], 'web')
        self.assertFalse(doc['source']['checked'])
        self.assertEqual(doc['program']['state'], 'unchanged')
        self.assertEqual([s['kind'] for s in doc['signed']], ['adhoc', 'adhoc'])
        self.assertEqual(doc['running']['page'], 'running')
        self.assertEqual(doc['block'][0], 'Switchboard 0.2.0 (d3da1a4)  MIT  github.com/example/switchboard')

    def test_the_installers_options(self):
        code, out = self.run_about('--done', '12', '--as', 'sb')
        self.assertTrue(out.startswith('\nSwitchboard 0.2.0'))
        self.assertTrue(out.endswith('\n\nDone in 12 s. Next: sb\n'), out)
        code, out = self.run_about('--done', '12', '--setup-now')
        self.assertTrue(out.endswith('Remove      sh ~/.switchboard/app/uninstall.sh   (--all also removes your '
                                     'accounts)\n\nDone in 12 s.\n'), out)

    def test_words_it_does_not_take(self):
        for words in (['--jsn'], ['--done'], ['--done', 'x'], ['--as'], ['--json', '--json'], ['extra']):
            self.assertEqual(self.run_about(*words), (2, 'switchboard about [--json]\n'), words)

    def test_it_is_a_command_with_its_own_help(self):
        self.assertIn('switchboard about [--json]', cli.command_help('about'))
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(['about', '--help']), 0)
        self.assertTrue(out.getvalue().startswith('switchboard about [--json]  what is installed'))


# ---------- install.sh: the manifest after the copy, and the end block ----------

def install_sh():
    with open(os.path.join(ROOT, 'install.sh')) as f:
        return f.read()


class TheInstallersEnd(unittest.TestCase):
    """The end block of install.sh, cut out and run alone in sh: the variables it reads set as the steps before it
    leave them, and SB a stand-in that writes down how it was called and prints a made-up block."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-end-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.calls = os.path.join(self.tmp, 'calls')

    def end(self, sb_exit=0, was='', own_later=0, homebrew=0, on_path=1, accounts=None):
        text = install_sh()
        start = text.index('# ---- the end:')
        body = text[start:text.index('}\nswitchboard_install "$@"')]
        sb = os.path.join(self.tmp, 'sb-command')
        write(sb, '#!/bin/sh\nprintf "%%s\\n" "$*" >> "%s"\necho "(the block)"\nexit %d\n' % (self.calls, sb_exit), 0o755)
        data = os.path.join(self.tmp, 'data')
        os.makedirs(data, exist_ok=True)
        if accounts is not None:
            with open(os.path.join(data, 'accounts.json'), 'w') as f:
                json.dump({'accounts': [{'email': e} for e in accounts]}, f)
        head = ('say() { printf "%%s\\n" "$*"; }\nclose_line() { :; }\nT0=$(date +%%s)\nSB="%s"\nWAS="%s"\n'
                'OWN_LATER=%d\nHOMEBREW=%d\nON_PATH=%d\nSETUP_WANTED=0\nNEXT=sb\nPY="%s"\nDATA="%s"\nBOLD=""\nPLAIN=""\n'
                % (sb, was, own_later, homebrew, on_path, sys.executable, data))
        r = subprocess.run(['/bin/sh', '-c', 'f() {\n' + head + body + '}\nf'], capture_output=True, text=True,
                           timeout=60, stdin=subprocess.DEVNULL)
        calls = []
        if os.path.exists(self.calls):
            with open(self.calls) as f:
                calls = f.read().splitlines()
        return r.returncode, r.stdout, calls

    def test_a_first_install_ends_with_the_block(self):
        code, out, calls = self.end()
        self.assertEqual((code, out), (0, '(the block)\n'))
        self.assertRegex(calls[0], r'^about --done \d+ --as sb$')

    def test_an_update_ends_with_the_short_form(self):
        self.assertRegex(self.end(was='0.1.9')[2][0], r'^about --done \d+ --as sb --update$')

    def test_when_the_block_cannot_be_made_the_last_line_is_said_all_the_same(self):
        code, out, _ = self.end(sb_exit=1)
        self.assertEqual(code, 0)
        self.assertRegex(out, r'^\(the block\)\nDone in \d+ s\. Next: sb\n$')
        code, out, _ = self.end(sb_exit=1, accounts=['maya@northwind.example'])
        self.assertRegex(out.splitlines()[-1], r'^Done in \d+ s\. Type sb to see your accounts\.$')

    def test_the_path_advice_still_comes_last(self):
        code, out, _ = self.end(on_path=0)
        self.assertEqual(out.splitlines()[:2], ['(the block)', 'To type it, put ~/.local/bin on your PATH with this '
                                                               'line, then open a new terminal:'])

    def test_an_install_left_unfinished_is_as_it_was(self):
        code, out, calls = self.end(own_later=1)
        self.assertEqual((code, calls), (1, []))
        self.assertEqual(out.splitlines()[-1], 'Run this installer again when you are online to finish.')
        code, out, calls = self.end(own_later=1, homebrew=1)
        self.assertEqual((code, calls), (0, []))

    def test_the_list_is_made_from_the_copy_before_it_takes_the_old_ones_place(self):
        text = install_sh()
        at = text.index('about.write_manifest(sys.argv[1])')
        self.assertLess(text.index('chmod +x "$DATA/app.new/switchboard/shim/open"'), at)
        self.assertLess(at, text.index('mv "$DATA/app.new" "$DATA/app"'))
        self.assertIn('"$DATA/app.new" 2>/dev/null || true', text[at:at + 200])     # it never stops an install


if __name__ == '__main__':
    unittest.main()
