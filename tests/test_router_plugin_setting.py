import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# The key that makes Claude Code load Switchboard's plugin (switchboard/router/plugin_setting.py):
# CLAUDE_CODE_PLUGIN_DIRS in the env block of Claude Code's user settings. A value the person set is kept and ours
# goes after it with ":"; undo takes out exactly our folder; every other byte of the file stays; an old or unknown
# Claude Code or a broken file gets nothing written.
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))
sys.path.insert(0, HERE)

from switchboard.router import plugin_setting as ps  # noqa: E402
from switchboard.router import wire_words as W  # noqa: E402
from test_providers_words import problems  # noqa: E402  (the one guard every package is held to)

NEW = [{'where': '/usr/local/bin/claude', 'version': '2.1.332', 'primary': True}]
KEY = 'CLAUDE_CODE_PLUGIN_DIRS'


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix='sb-plugin-setting-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cd = os.path.join(self.tmp, 'claude')
        self.data = os.path.join(self.tmp, 'data')
        self.plugin = os.path.join(self.tmp, 'app', 'switchboard', 'mods', 'switchboard-router')
        os.makedirs(self.cd)
        os.makedirs(os.path.join(self.plugin, '.claude-plugin'))
        with open(os.path.join(self.plugin, '.claude-plugin', 'plugin.json'), 'w') as f:
            f.write('{"name": "switchboard-router"}')
        self.path = os.path.join(self.cd, 'settings.json')

    def kw(self, **over):
        kw = dict(claude_dir=self.cd, data_dir=self.data, versions=NEW, environ={}, plugin=self.plugin)
        kw.update(over)
        return kw

    def put(self, text):
        with open(self.path, 'wb') as f:
            f.write(text.encode('utf-8') if isinstance(text, str) else text)

    def raw(self):
        with open(self.path, 'rb') as f:
            return f.read()

    def value(self):
        return json.loads(self.raw().decode('utf-8')).get('env', {}).get(KEY)

    def record(self):
        p = os.path.join(self.data, 'router', 'plugin-setting.json')
        if not os.path.exists(p):
            return None
        with open(p) as f:
            return json.load(f)

    def backups(self):
        d = os.path.join(self.data, 'router', 'plugin-backups')
        return sorted(os.listdir(d)) if os.path.isdir(d) else []


class OnAndBack(Base):
    FILES = ('{\n  "model": "sonnet"\n}\n',
             '{\n  "model": "sonnet",\n  "env": {\n    "SOME_VAR": "1"\n  }\n}\n',
             '{"model":"sonnet","env":{"A":"b"}}',
             '{\r\n\t"model": "sonnet",\r\n\t"env": {}\r\n}\r\n',
             '{\n  "x": "café ☃",\n  "y": [1, 2, {"z": null}]\n}',
             '{}', '{}\n', '  {   }  \n', '')

    def test_every_file_comes_back_byte_for_byte(self):
        for text in self.FILES:
            with self.subTest(text=text):
                self.put(text)
                got = ps.apply(**self.kw())
                self.assertTrue(got['written'], got)
                self.assertEqual(self.value(), self.plugin)
                self.assertEqual(ps.status(**self.kw())['state'], 'ours')
                back = ps.undo(**self.kw())
                self.assertTrue(back['written'], back)
                self.assertEqual(self.raw(), text.encode('utf-8'))
                self.assertEqual(self.record(), {'version': 1, 'dirs': {}})
                self.assertEqual(self.backups(), [])

    def test_the_splice_touches_only_our_key(self):
        self.put('{\n  "model": "sonnet",\n  "env": {\n    "SOME_VAR": "1"\n  }\n}\n')
        ps.apply(**self.kw())
        self.assertEqual(self.raw().decode('utf-8'),
                         '{\n  "model": "sonnet",\n  "env": {\n    "SOME_VAR": "1",\n    "%s": "%s"\n  }\n}\n'
                         % (KEY, self.plugin))

    def test_no_file_is_made_and_taken_away_again(self):
        got = ps.apply(**self.kw())
        self.assertTrue(got['made_file'])
        self.assertIn(W.MAKES_FILE, got['notes'])
        self.assertEqual(json.loads(self.raw()), {'env': {KEY: self.plugin}})
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        back = ps.undo(**self.kw())
        self.assertTrue(back['drops_file'])
        self.assertFalse(os.path.exists(self.path))

    def test_backup_and_record_live_in_switchboards_folder(self):
        self.put('{"a": 1}')
        got = ps.apply(**self.kw())
        self.assertEqual(os.listdir(self.cd), ['settings.json'])
        self.assertTrue(got['backup'].startswith(os.path.join(self.data, 'router', 'plugin-backups')))
        with open(got['backup'], 'rb') as f:
            self.assertEqual(f.read(), b'{"a": 1}')
        self.assertEqual(stat.S_IMODE(os.stat(got['backup']).st_mode), 0o600)
        rec = self.record()['dirs'][self.cd]
        self.assertEqual((rec['path'], rec['key_existed'], rec['made_env'], rec['made_file']),
                         (self.plugin, False, True, False))

    def test_plan_writes_nothing(self):
        self.put('{"a": 1}')
        got = ps.plan(**self.kw())
        self.assertEqual((got['op'], got['says'], got['made_env']), ('add', W.WILL_ADD.format(path=self.plugin), True))
        self.assertEqual(self.raw(), b'{"a": 1}')
        self.assertFalse(os.path.exists(self.data))


class ThePersonsValue(Base):
    def test_a_value_already_there_is_kept_and_ours_goes_after_it(self):
        text = '{"env": {"%s": "/opt/their-mod:/opt/another"}}' % KEY
        self.put(text)
        got = ps.apply(**self.kw())
        self.assertEqual((got['op'], got['says']), ('append', W.DID_APPEND))
        self.assertEqual(self.value(), '/opt/their-mod:/opt/another:' + self.plugin)
        self.assertEqual(ps.status(**self.kw())['state'], 'ours')
        back = ps.undo(**self.kw())
        self.assertEqual((back['op'], back['says']), ('take_out', W.DID_TAKE_OUT))
        self.assertEqual(self.raw(), text.encode('utf-8'))

    def test_an_empty_value_becomes_ours_and_comes_back_empty(self):
        text = '{"env": {"%s": ""}}' % KEY
        self.put(text)
        ps.apply(**self.kw())
        self.assertEqual(self.value(), self.plugin)
        ps.undo(**self.kw())
        self.assertEqual(self.raw(), text.encode('utf-8'))

    def test_ours_named_by_the_person_is_theirs(self):
        text = '{"env": {"%s": "%s"}}' % (KEY, '/opt/x:' + self.plugin)
        self.put(text)
        got = ps.apply(**self.kw())
        self.assertEqual((got['written'], got['says']), (False, W.YOURS_KEPT))
        self.assertEqual(ps.status(**self.kw())['state'], 'yours')
        back = ps.undo(**self.kw())
        self.assertEqual((back['written'], back['says']), (False, W.NOTHING_OURS))
        self.assertEqual(self.raw(), text.encode('utf-8'))
        self.assertIsNone(self.record())

    def test_a_folder_the_person_added_after_ours_stays(self):
        self.put('{"model": "x"}')
        ps.apply(**self.kw())
        doc = json.loads(self.raw())
        doc['env'][KEY] = self.plugin + ':/opt/later'
        self.put(json.dumps(doc))
        back = ps.undo(**self.kw())
        self.assertEqual(back['op'], 'take_out')
        self.assertEqual(json.loads(self.raw()), {'model': 'x', 'env': {KEY: '/opt/later'}})

    def test_a_later_edit_elsewhere_stays_when_ours_comes_out(self):
        self.put('{\n  "model": "x"\n}\n')
        ps.apply(**self.kw())
        text = self.raw().decode('utf-8').replace('"model": "x"', '"model": "y"')
        self.put(text)
        back = ps.undo(**self.kw())
        self.assertEqual((back['op'], back['drops_file']), ('remove', False))
        self.assertEqual(self.raw(), b'{\n  "model": "y"\n}\n')

    def test_an_env_block_with_more_in_it_stays(self):
        self.put('{"model": "x"}')
        ps.apply(**self.kw())
        doc = json.loads(self.raw())
        doc['env']['OTHER'] = '1'
        self.put(json.dumps(doc))
        ps.undo(**self.kw())
        self.assertEqual(json.loads(self.raw()), {'model': 'x', 'env': {'OTHER': '1'}})

    def test_the_person_took_ours_out(self):
        self.put('{}')
        ps.apply(**self.kw())
        self.put('{"env": {"%s": "/opt/theirs"}}' % KEY)
        back = ps.undo(**self.kw())
        self.assertEqual((back['written'], back['says'], back['forget']), (False, W.GONE_ALREADY, True))
        self.assertEqual(self.raw(), ('{"env": {"%s": "/opt/theirs"}}' % KEY).encode('utf-8'))
        self.assertFalse((self.record() or {}).get('dirs'))
        self.assertEqual(self.backups(), [])


class Moved(Base):
    def test_the_plugin_in_a_new_place_takes_the_old_ones_spot(self):
        text = '{"env": {"%s": "/opt/theirs"}}' % KEY
        self.put(text)
        ps.apply(**self.kw())
        moved = os.path.join(self.tmp, 'new-app', 'switchboard-router')
        os.makedirs(os.path.join(moved, '.claude-plugin'))
        with open(os.path.join(moved, '.claude-plugin', 'plugin.json'), 'w') as f:
            f.write('{}')
        got = ps.apply(**self.kw(plugin=moved))
        self.assertEqual((got['op'], got['says']), ('move', W.DID_MOVE))
        self.assertEqual(self.value(), '/opt/theirs:' + moved)
        self.assertEqual(ps.status(**self.kw(plugin=moved))['state'], 'ours')
        ps.undo(**self.kw(plugin=moved))
        self.assertEqual(self.value(), '/opt/theirs')

    def test_a_key_switchboard_made_goes_after_a_move(self):
        self.put('{"model": "x"}')
        ps.apply(**self.kw())
        moved = os.path.join(self.tmp, 'new-app', 'switchboard-router')
        os.makedirs(os.path.join(moved, '.claude-plugin'))
        with open(os.path.join(moved, '.claude-plugin', 'plugin.json'), 'w') as f:
            f.write('{}')
        ps.apply(**self.kw(plugin=moved))
        ps.undo(**self.kw(plugin=moved))
        self.assertEqual(self.raw(), b'{"model": "x"}')


class Twice(Base):
    def test_a_second_apply_changes_nothing(self):
        self.put('{"a": 1}')
        ps.apply(**self.kw())
        before = (self.raw(), os.stat(self.path).st_mtime_ns,
                  os.stat(os.path.join(self.data, 'router', 'plugin-setting.json')).st_mtime_ns, self.backups())
        got = ps.apply(**self.kw())
        self.assertEqual((got['written'], got['says']), (False, W.ALREADY))
        self.assertEqual((self.raw(), os.stat(self.path).st_mtime_ns,
                          os.stat(os.path.join(self.data, 'router', 'plugin-setting.json')).st_mtime_ns,
                          self.backups()), before)

    def test_a_second_undo_changes_nothing(self):
        self.put('{"a": 1}')
        ps.apply(**self.kw())
        ps.undo(**self.kw())
        mtime = os.stat(self.path).st_mtime_ns
        got = ps.undo(**self.kw())
        self.assertEqual((got['written'], got['says']), (False, W.NOTHING_OURS))
        self.assertEqual((self.raw(), os.stat(self.path).st_mtime_ns), (b'{"a": 1}', mtime))

    def test_undo_when_never_applied_writes_nothing(self):
        self.put('{"a": 1}')
        got = ps.undo(**self.kw())
        self.assertEqual((got['written'], got['says']), (False, W.NOTHING_OURS))
        self.assertFalse(os.path.exists(self.data))


class Broken(Base):
    def test_files_it_will_not_edit(self):
        cases = (('{"a": 1', W.BROKEN), ('{"a": 1} x', W.BROKEN), ('[1]', W.BROKEN), ('{"a": NaN}', W.BROKEN),
                 ('// c\n{}', W.BROKEN),
                 ('{"env": []}', W.CANT_EDIT.format(why=W.WHY['env'])),
                 ('{"env": {}, "env": {}}', W.CANT_EDIT.format(why=W.WHY['twice'])),
                 ('{"env": {"%s": "a", "%s": "b"}}' % (KEY, KEY), W.CANT_EDIT.format(why=W.WHY['twice'])),
                 ('{"env": {"%s": 3}}' % KEY, W.CANT_EDIT.format(why=W.WHY['value'])),
                 (b'\xef\xbb\xbf{}', W.CANT_EDIT.format(why=W.WHY['text'])),
                 (b'{"a": "\xff"}', W.CANT_EDIT.format(why=W.WHY['text'])))
        for text, line in cases:
            with self.subTest(text=text):
                self.put(text)
                before = self.raw()
                got = ps.apply(**self.kw())
                self.assertEqual((got['written'], got['blocked']), (False, line))
                self.assertEqual(self.raw(), before)
                self.assertFalse(os.path.exists(self.data))
                self.assertEqual(ps.status(**self.kw())['problem'], line)

    def test_a_link_or_a_read_only_file_is_left_alone(self):
        real = os.path.join(self.tmp, 'real.json')
        with open(real, 'w') as f:
            f.write('{}')
        os.symlink(real, self.path)
        got = ps.apply(**self.kw())
        self.assertEqual(got['blocked'], W.CANT_EDIT.format(why=W.WHY['link']))
        os.remove(self.path)
        self.put('{}')
        os.chmod(self.path, 0o400)
        self.addCleanup(os.chmod, self.path, 0o600)
        got = ps.apply(**self.kw())
        self.assertEqual(got['blocked'], W.CANT_EDIT.format(why=W.WHY['read_only']))
        with open(real) as f:
            self.assertEqual(f.read(), '{}')

    def test_a_record_that_cannot_be_read_changes_nothing(self):
        self.put('{}')
        os.makedirs(os.path.join(self.data, 'router'))
        with open(os.path.join(self.data, 'router', 'plugin-setting.json'), 'w') as f:
            f.write('not json')
        for call in (ps.apply, ps.undo):
            got = call(**self.kw())
            self.assertEqual((got['written'], got['blocked']), (False, W.RECORD_UNREADABLE))
        self.assertEqual(self.raw(), b'{}')

    def test_no_claude_folder(self):
        gone = os.path.join(self.tmp, 'nowhere')
        got = ps.apply(**self.kw(claude_dir=gone))
        self.assertEqual(got['blocked'], W.NO_FOLDER.format(path=gone))
        self.assertFalse(os.path.exists(gone))

    def test_whole_or_not_at_all(self):
        self.put('{"a": 1}')
        with mock.patch.object(ps, '_save_record', side_effect=OSError('disk')):
            with self.assertRaises(OSError):
                ps.apply(**self.kw())
        self.assertEqual(self.raw(), b'{"a": 1}')
        self.assertEqual(self.backups(), [])

    def test_the_record_is_saved_before_the_settings_file_is_written(self):
        # A stop between the two writes then leaves a record of a write that may not have happened (undo forgets
        # it), never a key with no record (undo would leave it in for good).
        self.put('{"a": 1}')
        order = []
        real = ps._m._atomic_write

        def watch(path, data, mode):
            order.append(os.path.basename(path))
            return real(path, data, mode)
        with mock.patch.object(ps._m, '_atomic_write', side_effect=watch):
            ps.apply(**self.kw())
        self.assertLess(order.index('plugin-setting.json'), order.index('settings.json'))

    def test_a_settings_write_that_fails_puts_the_old_record_back(self):
        self.put('{"a": 1}')
        real = ps._m._atomic_write

        def fail_on_settings(path, data, mode):
            if os.path.basename(path) == 'settings.json':
                raise OSError('disk')
            return real(path, data, mode)
        with mock.patch.object(ps._m, '_atomic_write', side_effect=fail_on_settings):
            with self.assertRaises(OSError):
                ps.apply(**self.kw())
        self.assertEqual(self.raw(), b'{"a": 1}')
        self.assertFalse((self.record() or {}).get('dirs'))
        self.assertEqual(self.backups(), [])

    def test_a_change_while_writing_writes_nothing(self):
        self.put('{"a": 1}')
        real = ps._backup

        def meddle(data, cd, raw):
            out = real(data, cd, raw)
            self.put('{"a": 2}')
            return out
        with mock.patch.object(ps, '_backup', side_effect=meddle):
            got = ps.apply(**self.kw())
        self.assertEqual((got['written'], got['says']), (False, W.CHANGED_WHILE))
        self.assertEqual(self.raw(), b'{"a": 2}')
        self.assertEqual(self.backups(), [])


class Versions(Base):
    def test_old_or_unknown_writes_nothing(self):
        cases = (([{'where': 'c', 'version': '2.1.286', 'primary': True}],
                  W.VERSION_OLD_NO_WRITE.format(version='2.1.286', need='2.1.287')),
                 ([], W.VERSION_UNKNOWN.format(need='2.1.287')),
                 ([{'where': 'c', 'version': 'not a version', 'primary': True}],
                  W.VERSION_UNKNOWN.format(need='2.1.287')))
        for versions, line in cases:
            with self.subTest(line=line):
                self.put('{}')
                got = ps.apply(**self.kw(versions=versions))
                self.assertEqual((got['written'], got['blocked']), (False, line))
                self.assertEqual(self.raw(), b'{}')
                self.assertFalse(os.path.exists(self.data))

    def test_the_first_version_that_runs_plugins_is_written(self):
        self.put('{}')
        got = ps.apply(**self.kw(versions=[{'where': 'c', 'version': '2.1.287', 'primary': True}]))
        self.assertTrue(got['written'])

    def test_another_older_install_is_said(self):
        self.put('{}')
        got = ps.apply(**self.kw(versions=NEW + [{'where': 'd', 'version': '2.1.200', 'primary': False}]))
        self.assertTrue(got['written'])
        self.assertIn(W.VERSION_OTHER_OLD.format(version='2.1.200'), got['notes'])

    def test_undo_works_on_any_version(self):
        self.put('{}')
        ps.apply(**self.kw())
        got = ps.undo(**self.kw(versions=[]))
        self.assertTrue(got['written'])
        self.assertEqual(self.raw(), b'{}')


class ThePlugin(Base):
    def test_the_plugin_that_ships_is_found_from_the_package(self):
        p = ps.plugin_dir()
        self.assertTrue(os.path.isabs(p))
        self.assertTrue(os.path.isfile(os.path.join(p, '.claude-plugin', 'plugin.json')))
        self.assertTrue(p.endswith(os.path.join('switchboard', 'mods', 'switchboard-router')))

    def test_no_plugin_there_writes_nothing(self):
        self.put('{}')
        gone = os.path.join(self.tmp, 'no-plugin')
        got = ps.apply(**self.kw(plugin=gone))
        self.assertEqual((got['written'], got['blocked']), (False, W.NO_PLUGIN.format(path=gone)))
        self.assertEqual(self.raw(), b'{}')

    def test_a_folder_with_a_colon_cannot_go_in_the_list(self):
        odd = os.path.join(self.tmp, 'a:b')
        os.makedirs(os.path.join(odd, '.claude-plugin'))
        with open(os.path.join(odd, '.claude-plugin', 'plugin.json'), 'w') as f:
            f.write('{}')
        self.put('{}')
        got = ps.apply(**self.kw(plugin=odd))
        self.assertEqual(got['blocked'], W.BAD_PLUGIN_PATH.format(path=odd))

    def test_the_key_in_the_environment_too_is_said(self):
        self.put('{}')
        got = ps.apply(**self.kw(environ={KEY: '/opt/x'}))
        self.assertTrue(got['written'])
        self.assertIn(W.ENV_TOO, got['notes'])
        self.assertIn(W.ENV_TOO, ps.status(**self.kw(environ={KEY: '/opt/x'}))['notes'])


class Status(Base):
    def test_each_state(self):
        self.assertEqual(ps.status(**self.kw())['lines'][0], W.STATE_UNSET)
        self.put('{"env": {"%s": "/opt/x"}}' % KEY)
        st = ps.status(**self.kw())
        self.assertEqual((st['state'], st['loaded'], st['lines'][0]), ('other', False, W.STATE_OTHER))
        ps.apply(**self.kw())
        st = ps.status(**self.kw())
        self.assertEqual((st['state'], st['set_by'], st['loaded'], st['lines'][0]),
                         ('ours', 'switchboard', True, W.STATE_OURS))
        self.assertEqual(st['lines'][1], W.VERSION_OK.format(version='2.1.332'))
        self.put('{"a": ')
        self.assertEqual(ps.status(**self.kw())['state'], 'unknown')

    def test_status_writes_nothing(self):
        self.put('{"a": 1}')
        ps.status(**self.kw())
        self.assertFalse(os.path.exists(self.data))
        self.assertEqual(self.raw(), b'{"a": 1}')


class TheDefaultFolder(Base):
    def test_the_installed_program_writes_the_default_folder(self):
        cd = os.environ['CLAUDE_CONFIG_DIR']
        os.makedirs(cd, exist_ok=True)
        path = os.path.join(cd, 'settings.json')
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        got = ps.apply(**self.kw(claude_dir=None))
        self.assertTrue(got['written'])
        with open(path) as f:
            self.assertEqual(json.load(f), {'env': {KEY: self.plugin}})
        self.assertTrue(ps.undo(**self.kw(claude_dir=None))['written'])
        self.assertFalse(os.path.exists(path))


class Words(unittest.TestCase):
    def test_every_sentence(self):
        found = []

        def take(name, v):
            if isinstance(v, str):
                found.append((name, v))
            elif isinstance(v, dict):
                for k, x in v.items():
                    take('%s[%r]' % (name, k), x)
        for name in dir(W):
            if name.isupper():
                take(name, getattr(W, name))
        self.assertGreater(len(found), 60)
        for name, text in found:
            with self.subTest(name=name):
                self.assertEqual(problems(text), [], text)
                self.assertNotRegex(text, r'(?i)best model|\bher\b|the owner|\bvendors?\b')
                self.assertNotIn('\n', text)

    def test_no_sentence_says_claude_code_loads_the_plugin_as_a_fact(self):
        # Only the docs say Claude Code reads this key; until a real Claude Code is seen doing it, the sentences say
        # what is set, not what Claude Code does with it.
        for name in dir(W):
            v = getattr(W, name)
            if name.isupper() and isinstance(v, str):
                with self.subTest(name=name):
                    self.assertNotRegex(v, r'(?i)\bwill load\b|Claude Code (already |no longer )?loads\b|'
                                           r'loads plugins from')


if __name__ == '__main__':
    unittest.main()
