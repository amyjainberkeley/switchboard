import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# npx and the uninstaller's tidy, in three parts:
#
# - npx is given claude-mem's checked package as a `file:` spec. Real npx (npm 10.9.9 and 11.19.0, 2026-11, run
#   offline) runs a bare path that names a file as a command, so with the bare path the
#   install, telemetry disable, claude-mem's repair, and start and stop through npx all stop: "Permission denied",
#   exit 126.
#
# - The Models setting's key can't be taken out because a file can't be read or understood (Claude Code's settings
#   file, or Switchboard's own record of the setting): the uninstaller's tidy names it as left, naming that file,
#   exits 1 and keeps the backup copy; the page's own Off never says it turned off.
#
# - The uninstaller's tidy takes house rules and the code map out of every CLAUDE.md Switchboard's record names,
#   whatever CLAUDE_CONFIG_DIR its shell has.
#
# Fakes only: test_packs_install's fixtures (a throwaway home, fake node, npx, bun, uv and claude-mem, a fake worker,
# claude-mem's made-up package on 127.0.0.1). The fake npx takes what it is given as real npx does.
# Nothing real is installed, and nothing leaves the Mac.
import json
import os
import subprocess
import sys
import time
import unittest
import urllib.parse
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import models, server
from switchboard.core import SwitchError
from switchboard.packs import claudemem_install as CMI
from switchboard.packs import codemap as CM
from switchboard.packs import common as C
from switchboard.packs import install as I
from switchboard.packs import runner as R
import test_packs_install as TP  # its fixtures; its tests run from its own file
import test_uninstall_tidy_left as tidy_left  # its Fresh: a throwaway home, data folder and Claude Code folder
from test_packs_install_safety import Died, changed
from test_packs_left_running import end, stand_in, stops_while_it_runs, worker_down

PACKAGE = os.path.join('packs', 'claude-mem', 'claude-mem-15.18.3.tgz')     # under the data folder
ODD = ('Jane Doe', 'a#b', 'a%b', 'a?b')                                     # a home's name can hold any of these
INSTALL = ['install'] + R.CM_INSTALL_ARGS


def settled(pid, pkg, wait=10):
    """True once ps shows a stand-in's command line naming pkg. macOS's /usr/bin/python3 hands over to Xcode's
    python3, which hands over again, and for a moment ps shows only "(python3.9)" (macOS, 2026-11): then only the start
    time tells the run apart."""
    t0 = time.time()
    while time.time() - t0 < wait:
        seen = CMI._process(pid)
        if seen and pkg in seen[2]:
            return True
        time.sleep(0.05)
    return False


def fake_npx(mac, spec, *sub):
    """The fake npx itself, given `spec` in the package's place: (exit code, stdout, stderr)."""
    p = subprocess.run([os.path.join(mac.bin, 'npx'), '--yes', spec] + list(sub), cwd=mac.root,
                       env={'HOME': mac.home, 'PATH': '/usr/bin:/bin'}, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=60)
    return p.returncode, p.stdout, p.stderr


# ---------------------------------------------------------------- the fake npx acts as real npx does

class TheFakeNpxActsLikeRealNpx(unittest.TestCase):
    """The fixture first. Real npx 10.9.9 and 11.19.0 (2026-11): a bare path to a file
    runs as a command, so a .tgz gives "Permission denied" and exit 126; `file:` and the plain path installs, also
    from a home named with a space, #, % or ?; an encoded path or a file:// URL names another file (ENOENT, exit 254).
    The fake therefore takes only what real npx takes: a bare path to the .tgz fails here too."""

    def setUp(self):
        self.mac = TP.Mac(self)
        self.launcher = os.path.join(self.mac.home, '.local', 'bin', 'claude-mem')

    def package_under(self, name):
        p = os.path.join(self.mac.root, name, '.switchboard', PACKAGE)
        self.mac.write(p, TP.cm_package())
        return p

    def test_a_bare_path_to_the_package_is_run_as_a_command(self):
        p = self.package_under('home')
        code, out, err = fake_npx(self.mac, p, *INSTALL)
        self.assertEqual(code, 126)
        self.assertIn('sh: %s: Permission denied' % p, err)
        self.assertFalse(os.path.lexists(self.launcher))                # nothing installed

    def test_a_bare_path_to_a_program_runs_it(self):
        prog = os.path.join(self.mac.root, 'a-program')
        self.mac.write(prog, b'#!/bin/sh\necho "ran $*"\n')
        os.chmod(prog, 0o755)
        self.assertEqual(fake_npx(self.mac, prog, 'start'), (0, 'ran start\n', ''))

    def test_file_and_the_plain_path_installs_from_a_home_with_odd_characters(self):
        for name in ('home',) + ODD:
            with self.subTest(home=name):
                code, out, err = fake_npx(self.mac, 'file:' + self.package_under(name), *INSTALL)
                self.assertEqual(code, 0, err)
                self.assertIn('Installation Complete', out)
                self.assertTrue(os.path.isfile(self.launcher))
                os.unlink(self.launcher)

    def test_an_encoded_path_or_a_file_url_names_another_file(self):
        for name in ODD:
            p = self.package_under(name)
            for spec in ('file:' + urllib.parse.quote(p), 'file://' + urllib.parse.quote(p)):
                with self.subTest(spec=spec):
                    code, out, err = fake_npx(self.mac, spec, *INSTALL)
                    self.assertEqual(code, 254)
                    self.assertIn("npm error enoent ENOENT: no such file or directory, open '%s'"
                                  % urllib.parse.quote(p), err)            # the encoded path, taken as it is
        self.assertFalse(os.path.lexists(self.launcher))

    def test_dot_dot_is_read_by_name_as_npm_reads_it(self):
        os.makedirs(os.path.join(self.mac.root, 'elsewhere', 'deep'))
        os.symlink(os.path.join(self.mac.root, 'elsewhere', 'deep'), os.path.join(self.mac.root, 'link'))
        code, out, err = fake_npx(self.mac, 'file:' + os.path.join(self.mac.root, 'link', '..', 'x.tgz'), 'start')
        self.assertEqual(code, 254)                                     # root/x.tgz, not root/elsewhere/x.tgz
        self.assertIn("open '%s'" % os.path.join(self.mac.root, 'x.tgz'), err)

    def test_a_name_at_a_version_installs_too(self):
        code, out, err = fake_npx(self.mac, 'claude-mem@15.18.3', *INSTALL)
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isfile(self.launcher))


# ---------------------------------------------------------------- Switchboard gives npx a file: spec

class NpxIsGivenTheCheckedFileAsAFileSpec(unittest.TestCase):
    """Every npx run gives npx the checked package as `file:` and its plain path: the install, telemetry disable,
    claude-mem's repair, and start and stop through npx when there is no launcher. A bare path would be run by npx as a
    command: the install would stop at step 3 (exit 126)."""

    def check(self, under=None):
        mac = TP.Mac(self, settings=TP.USER_SETTINGS, under=under)
        start = TP.tree(mac.home)
        pkg = os.path.join(mac.data, PACKAGE)
        j = mac.install()
        self.assertEqual(j['state'], 'done', j)
        self.assertEqual(I.repair('claude-mem', wait=True)['state'], 'installing')
        self.assertEqual(I.job_view('claude-mem')['state'], 'done', I.job_view('claude-mem'))
        worker_down(mac)
        os.unlink(os.path.join(mac.home, '.local', 'bin', 'claude-mem'))      # no launcher: start and stop use npx
        w, rec = CMI.where(mac.cd, mac.home), CMI.record()
        self.assertTrue(CMI.start(w, mac.environ, rec, wait=10)['ok'])
        self.assertTrue(CMI.stop(w, mac.environ, rec))
        calls = mac.calls('npx')
        self.assertEqual([c['args'][2:] for c in calls], [INSTALL, ['telemetry', 'disable'], ['repair'], ['start'],
                                                          ['stop']])
        self.assertEqual({c['args'][1] for c in calls}, {'file:' + pkg})
        self.assertEqual(mac.off(), ['claude-mem: taken out; your notes stay in ~/.claude-mem.'])
        self.assertEqual(changed(start, TP.tree(mac.home)), [])

    def test_install_telemetry_repair_start_and_stop(self):
        self.check()

    def test_a_home_with_a_space(self):
        self.check('Jane Doe')

    def test_a_home_with_a_hash(self):
        self.check('a#b')

    def test_a_home_with_a_percent_sign(self):
        self.check('a%b')

    def test_a_home_with_a_question_mark(self):
        self.check('a?b')

    def test_the_plan_names_the_file_spec(self):
        TP.Mac(self)
        p = I.plan('claude-mem')
        self.assertIn('npx --yes file:<that file> install %s' % ' '.join(R.CM_INSTALL_ARGS), p['commands'])
        self.assertIn('npx --yes file:<that file> telemetry disable', p['commands'])


class TheRunnerTakesOnlyTheFileSpec(unittest.TestCase):
    """The runner's allow-list takes exactly the form built in one place (runner.package_spec) for the checked
    package file, and not the bare path, so a later change can't bring the broken form back."""

    def setUp(self):
        self.mac = TP.Mac(self, tools=('npx',), under='Jane Doe')
        self.npx = os.path.join(self.mac.bin, 'npx')
        self.pkg = os.path.join(C.packs_dir(), 'claude-mem', 'claude-mem-15.18.3.tgz')

    def test_it_is_built_in_one_place(self):
        self.assertEqual(R.package_spec(self.pkg), 'file:' + self.pkg)
        self.assertEqual(CMI.npx_argv(self.npx, self.pkg, 'telemetry', 'disable'),
                         [self.npx, '--yes', 'file:' + self.pkg, 'telemetry', 'disable'])

    def test_only_that_form_is_allowed(self):
        spec = 'file:' + self.pkg
        for sub in [[s] for s in R.CM_SUBS] + [INSTALL, ['telemetry', 'disable']]:
            self.assertEqual(R.check([self.npx, '--yes', spec] + sub), 'npx')
        folder = os.path.dirname(self.pkg)
        for bad in (self.pkg,                                          # bare: npx would run it as a command
                    'file:' + urllib.parse.quote(self.pkg),            # encoded: npm would read another file
                    'file://' + urllib.parse.quote(self.pkg), 'file://' + self.pkg, 'FILE:' + self.pkg,
                    'file:' + self.pkg + ' ', ' file:' + self.pkg, 'file:/tmp/claude-mem-15.18.3.tgz',
                    'file:' + os.path.join(folder, 'other-1.0.0.tgz'), 'file:claude-mem-15.18.3.tgz',
                    'file:' + os.path.join(folder, '..', 'claude-mem', 'claude-mem-15.18.3.tgz')):
            with self.subTest(spec=bad):
                with self.assertRaises(R.NotAllowed):
                    R.check([self.npx, '--yes', bad, 'start'])
        self.assertEqual(self.mac.calls(), [])


class NeverAnotherFileThanTheCheckedOne(unittest.TestCase):
    """npm reads a '..' in a file: path by name, as a URL, never through a link (real npm 10.9.9 and 11.19.0, 2026-11;
    the fake does the same). With a link and then '..' in the data folder's path, the
    spec would name another file than the one Switchboard checked: refused before npx runs, in one plain line. A '..'
    with no link before it names the same file, so it is not refused. (Switchboard's record is kept outside that
    folder here: on Python 3.9 its own writes there fail first, on 3.14 they work, and the install reaches npx.)"""

    def setUp(self):
        self.mac = TP.Mac(self, settings=TP.USER_SETTINGS)
        os.makedirs(os.path.join(self.mac.root, 'elsewhere', 'deep'))
        os.symlink(os.path.join(self.mac.root, 'elsewhere', 'deep'), os.path.join(self.mac.root, 'link'))
        self.data = os.path.join(self.mac.root, 'link', '..', 'data')      # the Mac reads root/elsewhere/data

    def test_a_link_then_dot_dot_is_refused_before_npx_runs(self):
        start = TP.tree(self.mac.home)
        decoy = os.path.join(self.mac.data, PACKAGE)                     # root/data: what npm would read
        self.mac.write(decoy, TP.cm_package(extra=b'not the file Switchboard checked'))
        I.KW['state_path'] = os.path.join(self.mac.root, 'state.json')  # (restored by the Mac's own cleanup)
        with mock.patch.dict(os.environ, {'SWITCHBOARD_HOME': self.data}):
            j = self.mac.install()
        self.assertEqual(j['state'], 'failed', j)
        self.assertEqual(j['line'], "Stopped at step 3 of 5 (Install): claude-mem's package was not run: its path goes "
                                    "through a link and then '..', which npm reads by name, so npm would read another "
                                    "file (%s)." % os.path.join(self.data, PACKAGE))
        self.assertEqual(self.mac.calls('npx'), [])                      # nothing of claude-mem's ran
        self.assertEqual(changed(start, TP.tree(self.mac.home)), [])

    def test_dot_dot_with_no_link_before_it_is_not_refused(self):
        plain = os.path.join(self.mac.root, 'elsewhere', '..', 'data', PACKAGE)
        self.mac.write(plain, TP.cm_package())
        self.assertEqual(R.package_spec(plain), 'file:' + plain)
        with self.assertRaises(R.NotAllowed):
            R.package_spec(os.path.join(self.data, PACKAGE))


class InstallerRunningStillToldApart(unittest.TestCase):
    """installer_running still tells an installer left running apart with the file: form, by its command
    line (the spec holds the package file's path) and by its start time."""

    def test_by_its_command_line_and_by_its_start_time(self):
        mac = TP.Mac(self, settings=TP.USER_SETTINGS, cfg={'rc': True})
        pkg = os.path.join(mac.data, PACKAGE)
        ctx = I._ctx('claude-mem', R.new_log('claude-mem'), {'cap': None, 'consent': 'plan'})
        CMI.step_runtimes(ctx)
        CMI.step_settings(ctx)
        got = {}
        with mock.patch.object(R, 'run', stops_while_it_runs(self, got, 'install')):
            with self.assertRaises(Died):
                CMI.step_install(ctx)
        self.assertEqual(got['argv'][2:4], ['file:' + pkg, 'install'])
        run = CMI.record()['run']
        self.assertEqual((run['pid'], run['what'], run['package']), (got['p'].pid, 'install', pkg))
        self.assertTrue(CMI.installer_running({'run': run}))                       # its start time, as noted
        self.assertTrue(settled(got['p'].pid, pkg))
        self.assertTrue(CMI.installer_running({'run': dict(run, started=None)}))   # its command line alone
        self.assertFalse(CMI.installer_running({'run': dict(run, started=None, package=pkg + '.other')}))
        title = stand_in(self, ['npx', 'npm', 'exec', '***', 'install'])          # npm's own title: no file named
        ctx2 = {'rec': {}, 'state_path': os.path.join(mac.root, 'other-state.json')}
        CMI._noting(ctx2, 'install', pkg)(title.pid)
        self.assertTrue(CMI.installer_running(ctx2['rec']))                        # its start time alone
        end(title)
        end(got['p'])
        self.assertFalse(CMI.installer_running({'run': run}))
        self.assertFalse(CMI.installer_running(ctx2['rec']))


# ---------------------------------------------------------------- the Models setting, when a file can't be read

class ModelsKeyThatCantBeTakenOut(tidy_left.Fresh):
    """Switchboard put CLAUDE_CODE_SUBAGENT_MODEL in Claude Code's settings (the Models setting on), then a file
    can't be read or understood. tidy names the setting as left with a true line naming that file, exits 1 and keeps
    the backup copy; the page's own Off refuses with the same line, and the page never shows the setting as off. The
    cases: settings.json not JSON, models.json not JSON, and models.json that can't be opened; the line names that
    file, not Claude Code's settings file."""

    def setUp(self):
        super().setUp()
        server.act('/api/models', {'enabled': True})
        self.backup = self.path + '.switchboard-backup'
        self.record = models.default_state_path()
        self.assertEqual(self.key(), 'sonnet')
        self.assertTrue(os.path.isfile(self.backup))

    def key(self):
        return (json.loads(self.get()).get('env') or {}).get(models.SUBAGENT_VAR)

    def record_line(self):
        home = os.path.expanduser('~')
        short = '~' + self.record[len(home):] if self.record.startswith(home + os.sep) else self.record
        return "Switchboard's record of this setting (%s) can't be read." % short

    def tidy_leaves_it(self, line):
        rc, said = self.tidy()
        self.assertIn('Models setting: left as it is (%s)' % line, said)
        self.assertNotIn('Models setting: taken out', said)
        self.assertEqual(rc, 1, said)
        self.assertTrue(os.path.isfile(self.backup))                 # the copy from before stays
        self.assertNotIn('Removed', said)

    def page_off_refuses(self, line):
        with self.assertRaises(SwitchError) as e:
            server.act('/api/models', {'enabled': False})
        self.assertEqual(str(e.exception), line)
        self.assertIsNone(server.models_view()['enabled'])           # never shown as off while the key may be there

    def settings_not_json(self):
        self.put(self.get() + b'oops')
        return "Claude Code's settings file isn't valid JSON."

    def record_not_json(self):
        self.put(b'{"version": ', self.record)
        return self.record_line()

    def record_cant_be_opened(self):
        self.unreadable(self.record)
        return self.record_line()

    def test_settings_json_that_is_not_json_tidy(self):
        before = self.get()
        self.tidy_leaves_it(self.settings_not_json())
        self.assertEqual(self.get(), before + b'oops')               # nothing was written

    def test_settings_json_that_is_not_json_page(self):
        self.page_off_refuses(self.settings_not_json())

    def test_its_record_that_is_not_json_tidy(self):
        self.tidy_leaves_it(self.record_not_json())
        self.assertEqual(self.key(), 'sonnet')                       # Switchboard can't tell it from the person's

    def test_its_record_that_is_not_json_page(self):
        self.page_off_refuses(self.record_not_json())
        self.assertEqual(self.key(), 'sonnet')

    def test_its_record_that_cant_be_opened_tidy(self):
        self.tidy_leaves_it(self.record_cant_be_opened())
        self.assertEqual(self.key(), 'sonnet')

    def test_its_record_that_cant_be_opened_page(self):
        self.page_off_refuses(self.record_cant_be_opened())

    def test_with_both_readable_it_is_taken_out(self):
        rc, said = self.tidy()
        self.assertIn('Models setting: taken out.', said)
        self.assertEqual((rc, self.key()), (0, None), said)
        self.assertFalse(os.path.exists(self.backup))


class ModelsNothingToTakeOut(tidy_left.Fresh):
    """No line and nothing left when there is no key of Switchboard's to take out, in the cases of
    test_uninstall_tidy_left too (Claude Code's settings file can't be read or understood, with no record of a
    Models change)."""

    def test_a_record_that_cant_be_read_and_no_key_in_the_settings(self):
        server.act('/api/models', {'enabled': True})
        server.act('/api/models', {'enabled': False})
        self.put(b'{"version": ', models.default_state_path())
        rc, said = self.tidy()
        self.assertNotIn('Models setting', said)
        self.assertEqual(rc, 0, said)
        self.assertEqual(server.models_view()['enabled'], False)

    def test_settings_that_are_not_json_after_the_setting_was_turned_off(self):
        server.act('/api/models', {'enabled': True})
        server.act('/api/models', {'enabled': False})
        self.put(b'not json{')
        self.assertEqual(server.act('/api/models', {'enabled': False})['changed'], False)
        rc, said = self.tidy()
        self.assertNotIn('Models setting', said)
        self.assertEqual(rc, 0, said)


# ---------------------------------------------------------------- blocks taken out where the record says

class BlocksOutOfTheFileTheRecordNames(tidy_left.Fresh):
    """House rules and the code map put in ~/.claude/CLAUDE.md by the page, then the uninstaller run from a shell
    that exports another CLAUDE_CONFIG_DIR. tidy takes each block out of the file Switchboard's record names, through
    the same careful undo as Off (the record's sha, the link and folder checks, a person's own edits)."""

    def setUp(self):
        super().setUp()
        self.other = os.path.join(self.tmp, 'work-claude')
        os.makedirs(self.other)
        os.makedirs(os.path.join(self.claude, 'skills', 'graphify'))
        self.put(tidy_left.SKILL, os.path.join(self.claude, 'skills', 'graphify', 'SKILL.md'))

    def from_another_shell(self):
        with mock.patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': self.other}):
            return self.tidy()

    def where(self, path):
        home, p = os.path.realpath(os.path.expanduser('~')), os.path.realpath(path)
        return '~' + p[len(home):] if p.startswith(home + os.sep) else p

    def blocks(self):
        return C.load_state()[0]['blocks']

    def test_house_rules(self):
        self.act({'pack': 'house', 'on': True})
        self.assertNotEqual(self.get(self.md), b'# mine\n')
        rc, said = self.from_another_shell()
        self.assertEqual(self.get(self.md), b'# mine\n')               # out, every other byte as it was
        self.assertIn('House rules: put back as it was (%s).' % self.where(self.md), said)
        self.assertEqual((rc, self.blocks()), (0, {}), said)

    def test_the_code_map(self):
        CM.apply(CM.plan(True, path=''))
        self.assertNotEqual(self.get(self.md), b'# mine\n')
        rc, said = self.from_another_shell()
        self.assertEqual(self.get(self.md), b'# mine\n')
        self.assertIn('Code map: put back as it was (%s).' % self.where(self.md), said)
        self.assertEqual((rc, self.blocks()), (0, {}), said)

    def test_a_block_in_each_folder_both_go(self):
        self.act({'pack': 'house', 'on': True})
        with mock.patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': self.other}):
            self.act({'pack': 'house', 'on': True})                   # a CLAUDE.md made for the block
        rc, said = self.from_another_shell()
        self.assertEqual(self.get(self.md), b'# mine\n')
        self.assertFalse(os.path.exists(os.path.join(self.other, 'CLAUDE.md')))
        self.assertIn('House rules: put back as it was.', said)          # this shell's folder: the line with no path
        self.assertIn('House rules: put back as it was (%s).' % self.where(self.md), said)
        self.assertEqual((rc, self.blocks()), (0, {}), said)

    def test_a_block_the_person_edited_stays_and_is_named(self):     # the edits stay
        self.act({'pack': 'house', 'on': True})
        edited = self.get(self.md).replace(b'\n', b'\nmy own line\n', 2)
        self.put(edited, self.md)
        rc, said = self.from_another_shell()
        self.assertEqual(self.get(self.md), edited)
        self.assertIn('House rules: left as it is (You edited inside the House rules block', said)
        self.assertEqual(rc, 1, said)

    def test_a_block_whose_file_cant_be_read_stays_left_with_its_line(self):
        self.act({'pack': 'house', 'on': True})
        self.unreadable(self.md)
        rc, said = self.from_another_shell()
        self.assertIn('House rules: left as it is (%s: Permission denied)' % os.path.realpath(self.md), said)
        self.assertEqual(rc, 1, said)

    def test_a_folder_that_is_gone_is_forgotten(self):                # as Off does: nothing to take out, nothing said
        with mock.patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': self.other}):
            self.act({'pack': 'house', 'on': True})
        import shutil
        shutil.rmtree(self.other)
        rc, said = self.tidy()
        self.assertNotIn('House rules', said)
        self.assertEqual((rc, self.blocks()), (0, {}), said)


if __name__ == '__main__':
    unittest.main()
