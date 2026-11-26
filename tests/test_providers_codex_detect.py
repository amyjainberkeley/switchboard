import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# Which Codex program Switchboard runs (DESIGN 7.1), on a made-up Mac with the four copies this Mac has, each a
# stand-in fake-codex that prints its version: one on PATH (0.155.1, linked as npm does), one in a made-up ChatGPT.app
# (0.169.1), one in an older made-up Codex.app (0.164.0-alpha.11.1), and the copy the app keeps for itself in the
# Codex folder's plugin place (0.167.0-alpha.4.1). The newest copy that may be chosen is chosen; the plugin copy is
# listed and never run; a copy any other user could change is listed and never run; each copy's --version runs once
# per size and time, in an empty Codex folder of Switchboard's own; the login file is looked at with os.stat only.
import dataclasses
import json
import os
import stat
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from fakes_codex import FakeCodex, Rig
from fakes_providers import stand_in
from switchboard.providers import codex_facts, contract

OPENS = []                      # (path) of every open while WATCH is on; the hook is added once for the whole file
WATCH = []


def _hook(event, args):
    if WATCH and event == 'open' and args and isinstance(args[0], str):
        OPENS.append(args[0])


sys.addaudithook(_hook)


class FourCopies(unittest.TestCase):

    def setUp(self):
        self.rig = Rig(self, env={'CODEX_API_KEY': 'made-up-key-0123456789'})
        self.path = self.rig.on_path('0.155.1')
        self.chatgpt = self.rig.app('ChatGPT.app', '0.169.1')
        self.old_app = self.rig.app('Codex.app', '0.164.0-alpha.11.1')
        self.plugin = self.rig.plugin('0.167.0-alpha.4.1')

    def test_the_newest_copy_that_may_be_chosen_is_chosen(self):
        found = self.rig.provider.detect()
        self.assertTrue(found.installed)
        self.assertEqual(found.program.path, self.chatgpt.path)
        self.assertEqual((found.program.version, found.program.source, found.program.where),
                         ('0.169.1', 'app', 'ChatGPT.app'))
        self.assertTrue(found.tested)
        self.assertEqual([p.version for p in found.programs], ['0.169.1', '0.164.0-alpha.11.1', '0.155.1', None])
        on_path = [p for p in found.programs if p.source == 'path'][0]
        self.assertEqual((on_path.path, on_path.where, on_path.refused),
                         (os.path.realpath(self.path.path), codex_facts.WHERE_PATH, None))     # the link's file

    def test_the_plugin_copy_is_listed_refused_and_never_run(self):
        found = self.rig.provider.detect()
        plugin = [p for p in found.programs if p.source == 'plugin']
        self.assertEqual(len(plugin), 1)
        self.assertEqual(plugin[0].refused, codex_facts.REFUSED_PLUGIN)
        self.assertNotIn(self.plugin.path, self.rig.probes())
        self.assertEqual(self.plugin.runs(), [])

    def test_the_plugin_copy_is_never_chosen_even_when_newest(self):
        self.plugin.spec(version='0.170.0')
        for p in (self.chatgpt, self.old_app):
            os.chmod(p.path, 0o644)             # only the PATH copy and the plugin copy can run now
        found = self.rig.provider.detect()
        self.assertEqual(found.program.path, os.path.realpath(self.path.path))
        self.assertEqual(found.program.version, '0.155.1')
        self.assertEqual(self.plugin.runs(), [])

    def test_only_the_plugin_copy_means_nothing_can_run(self):
        os.remove(os.path.join(self.rig.bin, 'fake-codex'))
        for p in (self.chatgpt, self.old_app):
            os.chmod(p.path, 0o644)
        found = self.rig.provider.detect()
        self.assertTrue(found.installed)
        self.assertIsNone(found.program)
        self.assertIn(codex_facts.REFUSED_PLUGIN, found.problems[-1])
        with self.assertRaises(contract.ProviderError) as e:
            self.rig.provider.job(self.rig.root, 'x', os.path.join(self.rig.root, 'out.txt'), 5.0)
        self.assertEqual(e.exception.kind, 'not_installed')
        self.assertEqual(self.plugin.runs(), [])

    def test_each_copy_is_probed_once_per_size_and_time(self):
        self.rig.provider.detect()
        first = self.rig.probes()
        self.assertEqual(sorted(first), sorted([os.path.realpath(self.path.path), self.chatgpt.path,
                                                self.old_app.path]))
        self.rig.make().detect()
        self.assertEqual(self.rig.probes(), first)              # a new adapter reads the cache: nothing ran
        with open(self.old_app.path, 'a') as f:
            f.write('\n# changed\n')
        self.rig.make().detect()
        self.assertEqual(self.rig.probes(), first + [self.old_app.path])

    def test_the_probe_runs_in_switchboards_own_empty_codex_folder(self):
        self.rig.provider.detect()
        probe_home = os.path.join(self.rig.data, contract.PROBE_HOME)
        self.assertEqual(stat.S_IMODE(os.stat(probe_home).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(probe_home)).st_mode), 0o700)
        self.assertEqual(os.listdir(probe_home), [])
        for argv, stdin, env, cwd in self.rig.calls.runs:
            self.assertEqual(env['CODEX_HOME'], probe_home)
            self.assertEqual(cwd, probe_home)
            self.assertNotIn('CODEX_API_KEY', env)
            self.assertEqual(list(argv[1:]), ['--version'])
        for fake in (self.path, self.chatgpt, self.old_app):
            starts = [r for r in fake.records() if 'argv' in r]
            self.assertEqual(len(starts), 1)
            self.assertNotIn('CODEX_API_KEY', starts[0]['env'])
            self.assertEqual(starts[0]['cwd'], os.path.realpath(probe_home))
        cache = os.path.join(self.rig.data, contract.PROGRAMS_FILE)
        self.assertEqual(stat.S_IMODE(os.stat(cache).st_mode), 0o600)

    def test_newest_seen_comes_from_the_chat_reader(self):
        self.rig.chatfiles.newest = '0.169.1'
        self.assertEqual(self.rig.provider.detect().newest_seen, '0.169.1')
        self.rig.chatfiles.newest = '0.170.0'
        found = self.rig.provider.detect()
        self.assertEqual(found.newest_seen, '0.170.0')
        with self.assertRaises(contract.ProviderError) as e:
            self.rig.provider.job(self.rig.root, 'x', os.path.join(self.rig.root, 'out.txt'), 5.0)
        self.assertEqual(e.exception.kind, 'too_old')
        self.assertEqual(str(e.exception), 'The newest Codex here (0.169.1) is older than the one that wrote your '
                                           'Codex chats (0.170.0). Update Codex, then run this again.')

    def test_a_chat_reader_that_cannot_load_is_said_not_raised(self):
        class Broken:
            def newest_seen(self, reach):
                raise ImportError('not here')
        self.rig.provider.chatfiles = Broken()
        found = self.rig.provider.detect()
        self.assertIsNone(found.newest_seen)
        self.assertEqual(found.program.path, self.chatgpt.path)
        self.assertTrue(found.problems)

    def test_no_copy_runs_or_is_launched_by_detect(self):
        self.rig.provider.detect()
        self.assertEqual(self.rig.calls.execs, [])
        self.assertEqual(self.rig.calls.links, [])
        self.assertEqual(self.rig.calls.pipes, [])
        for fake in (self.path, self.chatgpt, self.old_app, self.plugin):
            self.assertEqual(fake.forbidden(), [])


class Trust(unittest.TestCase):

    def setUp(self):
        self.rig = Rig(self)

    def test_a_world_writable_folder_is_refused_and_never_probed(self):
        shared = os.path.join(self.rig.root, 'shared')
        fake = FakeCodex(shared, '0.169.1')
        os.chmod(shared, 0o777)
        self.addCleanup(os.chmod, shared, 0o755)
        os.symlink(fake.path, os.path.join(self.rig.bin, 'fake-codex'))
        found = self.rig.provider.detect()
        self.assertIsNone(found.program)
        self.assertEqual(found.programs[0].refused, codex_facts.REFUSED_TRUST)
        self.assertEqual(self.rig.probes(), [])
        self.assertEqual(fake.runs(), [])

    def test_a_folder_another_group_can_write_is_refused(self):
        group = os.path.join(self.rig.root, 'group')
        fake = FakeCodex(group, '0.169.1')
        os.chmod(group, 0o775)          # the person's own group (staff on macOS), which is not admin or wheel
        self.addCleanup(os.chmod, group, 0o755)
        os.symlink(fake.path, os.path.join(self.rig.bin, 'fake-codex'))
        self.assertNotIn(os.stat(group).st_gid, (0, 80))
        found = self.rig.provider.detect()
        self.assertIsNone(found.program)
        self.assertEqual(found.programs[0].refused, codex_facts.REFUSED_TRUST)
        self.assertEqual(fake.runs(), [])

    def stat_as(self, changes):
        """A stand-in os.stat: the real answer, with a made-up (mode, uid, gid) for the paths in changes."""
        def fake_stat(path):
            st = os.stat(path)
            if path in changes:
                mode, uid, gid = changes[path]
                fields = list(st)
                fields[stat.ST_MODE], fields[stat.ST_UID], fields[stat.ST_GID] = mode, uid, gid
                return os.stat_result(fields)
            return st
        return fake_stat

    def test_an_admin_group_folder_like_applications_is_fine(self):
        app = self.rig.app('ChatGPT.app', '0.169.1')
        p = self.rig.provider
        p.stat = self.stat_as({self.rig.apps: (stat.S_IFDIR | 0o775, 0, 80)})     # drwxrwxr-x root admin
        found = p.detect()
        self.assertEqual(found.program.path, app.path)
        p.stat = self.stat_as({self.rig.apps: (stat.S_IFDIR | 0o775, 0, 20)})     # the same, group staff
        found = p.detect()
        self.assertIsNone(found.program)
        self.assertEqual(found.programs[0].refused, codex_facts.REFUSED_TRUST)

    def test_a_folder_owned_by_another_person_is_refused(self):
        self.rig.app('ChatGPT.app', '0.169.1')
        p = self.rig.provider
        p.stat = self.stat_as({self.rig.apps: (stat.S_IFDIR | 0o755, os.getuid() + 1, 20)})
        self.assertIsNone(p.detect().program)

    def test_a_copy_whose_version_cannot_be_read_is_never_chosen(self):
        stand_in(self.rig.bin, 'fake-codex', 'print("no version here")\n')
        found = self.rig.provider.detect()
        self.assertIsNone(found.program)
        self.assertEqual(found.programs[0].refused, codex_facts.REFUSED_NO_VERSION)


class Detect(unittest.TestCase):

    def setUp(self):
        self.rig = Rig(self)

    def test_no_copy(self):
        found = self.rig.provider.detect()
        self.assertEqual((found.installed, found.program, found.programs, found.signed_in),
                         (False, None, (), None))
        self.assertEqual(self.rig.calls.runs, [])

    def test_an_app_of_another_bundle_id_or_a_broken_plist_is_not_codex(self):
        from fakes_codex import make_app
        make_app(self.rig.apps, 'Other.app', '0.169.1', program='Contents/Resources/codex-cli/bin/fake-codex',
                 bundle_id='com.example.other')
        broken = os.path.join(self.rig.apps, 'Broken.app', 'Contents')
        os.makedirs(os.path.join(broken, 'Resources', 'codex-cli', 'bin'))
        with open(os.path.join(broken, 'Info.plist'), 'w') as f:
            f.write('not a plist')
        self.assertFalse(self.rig.provider.detect().installed)

    def test_a_version_outside_the_checked_ones_runs_but_is_not_tested(self):
        self.rig.app('ChatGPT.app', '0.170.0')
        found = self.rig.provider.detect()
        self.assertEqual(found.program.version, '0.170.0')
        self.assertFalse(found.tested)

    def test_signed_in_is_whether_the_login_file_exists_never_its_contents(self):
        self.rig.app('ChatGPT.app', '0.169.1')
        codex_home = os.path.join(self.rig.home, '.codex')
        os.makedirs(codex_home, exist_ok=True)
        self.assertIsNone(self.rig.provider.detect().signed_in)
        login = os.path.join(codex_home, 'auth.json')
        with open(login, 'w') as f:
            f.write('{"made-up": "canary-login-4711"}')
        del OPENS[:]
        WATCH.append(1)
        try:
            found = self.rig.provider.detect()
        finally:
            WATCH.pop()
        self.assertTrue(found.signed_in)
        self.assertEqual([p for p in OPENS if p.endswith('auth.json')], [])
        self.assertEqual([p for p in OPENS if p.startswith(codex_home)], [])
        self.assertNotIn('canary-login-4711', repr(found))

    def test_codex_home_from_the_environment(self):
        other = os.path.join(self.rig.root, 'other-codex')
        rig = self.rig
        rig.reach = dataclasses.replace(rig.reach, env=dict(rig.reach.env, CODEX_HOME=other))
        p = rig.make()
        self.assertEqual(p.codex_home(), other)
        rig.plugin('0.167.0-alpha.4.1')         # under ~/.codex: not the folder in use, so not listed
        self.assertFalse(p.detect().installed)

    def test_the_shared_programs_file_keeps_the_claude_adapters_entries(self):
        self.rig.app('ChatGPT.app', '0.169.1')
        cache = os.path.join(self.rig.data, contract.PROGRAMS_FILE)
        os.makedirs(os.path.dirname(cache), mode=0o700)
        claude = {'/Users/maya/.local/share/claude/versions/2.1.330': [123, 456, '2.1.330']}
        with open(cache, 'w') as f:
            json.dump({'v': 1, 'claude': claude, 'programs': {}}, f)
        self.rig.provider.detect()
        with open(cache) as f:
            doc = json.load(f)
        self.assertEqual(doc['claude'], claude)
        self.assertEqual(len(doc['programs']), 1)
        self.assertEqual(stat.S_IMODE(os.stat(cache).st_mode), 0o600)

    def test_a_programs_file_that_is_not_json_is_left_as_it_is(self):
        self.rig.app('ChatGPT.app', '0.169.1')
        cache = os.path.join(self.rig.data, contract.PROGRAMS_FILE)
        os.makedirs(os.path.dirname(cache), mode=0o700)
        with open(cache, 'w') as f:
            f.write('{not json')
        self.assertEqual(self.rig.provider.detect().program.version, '0.169.1')
        with open(cache) as f:
            self.assertEqual(f.read(), '{not json')

    def test_no_program_runs_while_the_shared_lock_is_held(self):
        import fcntl
        from switchboard import core
        self.rig.app('ChatGPT.app', '0.169.1')
        held = []
        run = self.rig.reach.run
        os.makedirs(core.data_dir(), exist_ok=True)

        def watching(argv, **kw):
            fd = os.open(core.data_path(contract.LOCK_NAME), os.O_WRONLY | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held.append(False)
            except OSError:
                held.append(True)
            finally:
                os.close(fd)
            return run(argv, **kw)
        self.rig.reach = dataclasses.replace(self.rig.reach, run=watching)
        self.rig.make().detect()
        self.assertEqual(held, [False])

    def test_a_data_folder_that_cannot_be_written_gives_no_traceback(self):
        self.rig.app('ChatGPT.app', '0.169.1')
        os.makedirs(self.rig.data)
        os.chmod(self.rig.data, 0o500)
        self.addCleanup(os.chmod, self.rig.data, 0o700)
        found = self.rig.provider.detect()             # the probe folder cannot be made: no version this time
        self.assertTrue(found.installed)
        os.chmod(self.rig.data, 0o700)
        os.makedirs(os.path.join(self.rig.data, contract.PROBE_HOME), mode=0o700)
        os.chmod(os.path.join(self.rig.data, contract.DATA_DIR), 0o500)    # the probe runs; the cache cannot be kept
        found = self.rig.make().detect()
        self.assertEqual(found.program.version, '0.169.1')
        os.chmod(os.path.join(self.rig.data, contract.DATA_DIR), 0o700)

    def test_a_linked_program_is_run_by_the_path_that_was_checked(self):
        real = self.rig.on_path('0.169.1')
        found = self.rig.provider.detect()
        self.assertEqual(found.program.path, os.path.realpath(real.path))
        self.assertEqual(self.rig.probes(), [os.path.realpath(real.path)])
        launch = self.rig.provider.start(self.rig.root, contract.Handoff(
            hop='h-0a0b0c0d', path=os.path.join(self.rig.root, 'n.md'), meta_path='', bytes=0, sha256=''),
            contract.PRINT)
        self.assertEqual(launch.argv[0], os.path.realpath(real.path))

    def test_it_says_what_it_can_do(self):
        p = self.rig.provider
        self.assertEqual((p.id, p.name, p.vendor), ('codex', 'Codex', 'OpenAI'))
        self.assertTrue(p.can <= contract.CAPABILITIES)
        self.assertNotIn(contract.NOTE, p.can)
        self.assertIsInstance(p.note(None), contract.Unsupported)


if __name__ == '__main__':
    unittest.main()
