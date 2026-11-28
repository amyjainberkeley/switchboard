import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# The helpers' cache setting (switchboard/router/cache_setting.py): one key of Claude Code's user settings,
# subagentPromptCacheTtl. It is written only on a call, spliced in so every other byte of the file stays, recorded in
# Switchboard's own folder, and taken out again exactly. A value the person set is never changed. Every folder here is
# a throwaway one; the Claude Code version is given, never looked up.
import json
import os
import re
import shutil
import string
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from switchboard.router import cache_setting as cs  # noqa: E402
from switchboard.router import cache_words as W  # noqa: E402
from test_providers_words import problems  # noqa: E402  (the one guard every package is held to)

NEW = [{'where': '/stand-in/bin/claude', 'version': '2.1.332', 'primary': True}]
OLD = [{'where': '/stand-in/bin/claude', 'version': '2.1.241', 'primary': True}]
KEY = 'subagentPromptCacheTtl'


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='sb-cache-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cd = os.path.join(self.tmp, 'claude')
        os.makedirs(self.cd)
        self.data = os.path.join(self.tmp, 'data')
        self.path = os.path.join(self.cd, 'settings.json')

    def kw(self, **over):
        k = {'claude_dir': self.cd, 'data_dir': self.data, 'versions': NEW, 'environ': {}, 'managed_dir': ''}
        k.update(over)
        return k

    def put(self, raw):
        with open(self.path, 'wb') as f:
            f.write(raw if isinstance(raw, bytes) else raw.encode('utf-8'))
        os.utime(self.path, (1000000000, 1000000000))       # an old time, so any write shows

    def raw(self):
        with open(self.path, 'rb') as f:
            return f.read()

    def obj(self):
        return json.loads(self.raw().decode('utf-8'))

    def mtime(self):
        return os.stat(self.path).st_mtime_ns

    def record(self):
        return os.path.join(self.data, 'router', 'cache-setting.json')


class OnAndBack(Base):
    def test_unset_to_1h_and_back_byte_for_byte(self):
        before = '{\n  "model": "opus",\n  "env": {\n    "A": "1"\n  }\n}\n'
        self.put(before)
        got = cs.apply('1h', **self.kw())
        self.assertTrue(got['written'])
        self.assertEqual(got['says'], W.DID_ADD.format(value='1h'))
        self.assertEqual(self.obj(), {'model': 'opus', 'env': {'A': '1'}, KEY: '1h'})
        self.assertEqual(self.raw().decode(), '{\n  "model": "opus",\n  "env": {\n    "A": "1"\n  },\n  "%s": "1h"\n}\n'
                         % KEY)
        st = cs.status(**self.kw())
        self.assertEqual((st['value'], st['state'], st['set_by']), ('1h', '1h', 'switchboard'))
        self.assertIn(W.STATE_OURS.format(value='1h'), st['lines'])
        back = cs.undo(**self.kw())
        self.assertTrue(back['written'])
        self.assertEqual(back['says'], W.DID_REMOVE)
        self.assertEqual(self.raw(), before.encode())
        st = cs.status(**self.kw())
        self.assertEqual((st['value'], st['state'], st['set_by']), (None, 'unset', None))

    def test_apply_none_is_the_same_as_undo(self):
        before = '{"a": 1}'
        self.put(before)
        cs.apply('1h', **self.kw())
        self.assertTrue(cs.apply(None, **self.kw())['written'])
        self.assertEqual(self.raw(), before.encode())

    def test_ours_from_1h_to_5m_then_back_to_nothing(self):
        before = '{\n\t"a": [1, 2]\n}'
        self.put(before)
        cs.apply('1h', **self.kw())
        got = cs.apply('5m', **self.kw())
        self.assertEqual((got['op'], got['old'], got['new'], got['written']), ('change', '1h', '5m', True))
        self.assertEqual(got['says'], W.DID_CHANGE.format(old='1h', value='5m'))
        self.assertEqual(cs.status(**self.kw())['set_by'], 'switchboard')
        cs.undo(**self.kw())
        self.assertEqual(self.raw(), before.encode())

    def test_one_change_at_a_time(self):
        # Two changes at once (the menu bar and a terminal) take turns: while another holds the lock beside the
        # record, apply waits, and the record keeps both Claude folders' entries.
        import fcntl
        import threading
        self.put('{"a": 1}\n')
        lock = os.path.join(self.data, 'router', 'cache-setting.lock')
        os.makedirs(os.path.dirname(lock), exist_ok=True)
        fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            t = threading.Thread(target=cs.apply, args=('1h',), kwargs=self.kw())
            t.start()
            t.join(2.0)
            self.assertTrue(t.is_alive(), 'apply did not wait for the lock')
            self.assertEqual(self.raw(), b'{"a": 1}\n')
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        t.join(30)
        self.assertFalse(t.is_alive())
        self.assertEqual(self.obj(), {'a': 1, KEY: '1h'})

    def test_only_the_first_copy_is_kept_and_the_person_is_told(self):
        # The settings file can hold keys, so Switchboard keeps one copy, the file from before its first write (the
        # one an exact undo needs), and says where.
        self.put('{"a": 1}\n')
        folder = os.path.join(self.data, 'router', 'cache-backups')
        self.assertIn(W.COPY_KEPT.format(path=folder), cs.plan('1h', **self.kw())['notes'])
        first = cs.apply('1h', **self.kw())
        self.assertIn(W.COPY_KEPT.format(path=folder), first['notes'])
        got = cs.apply('5m', **self.kw())
        self.assertIsNone(got['backup'])
        self.assertNotIn(W.COPY_KEPT.format(path=folder), got['notes'])
        self.assertEqual(os.listdir(folder), [os.path.basename(first['backup'])])
        cs.undo(**self.kw())
        self.assertEqual(self.raw(), b'{"a": 1}\n')
        self.assertEqual(os.listdir(folder), [])

    def test_backup_and_record_in_switchboards_own_folder(self):
        before = '{"a": 1}\n'
        self.put(before)
        got = cs.apply('1h', **self.kw())
        self.assertTrue(got['backup'].startswith(os.path.join(self.data, 'router') + os.sep))
        with open(got['backup'], 'rb') as f:
            self.assertEqual(f.read(), before.encode())
        self.assertEqual(os.stat(got['backup']).st_mode & 0o777, 0o600)
        with open(self.record(), encoding='utf-8') as f:
            rec = json.load(f)
        mine = rec['dirs'][os.path.realpath(self.cd)]
        self.assertEqual((mine['wrote'], mine['made_file']), ('1h', False))
        self.assertEqual(sorted(os.listdir(self.cd)), ['settings.json'])      # nothing left beside the file

    def test_plan_writes_nothing(self):
        self.put('{"a": 1}')
        t = self.mtime()
        p = cs.plan('1h', **self.kw())
        self.assertEqual((p['op'], p['old'], p['new'], p['blocked']), ('add', None, '1h', None))
        self.assertEqual(p['says'], W.WILL_ADD.format(value='1h'))
        self.assertIn(W.NEW_SESSIONS, p['notes'])
        self.assertEqual((self.raw(), self.mtime()), (b'{"a": 1}', t))
        self.assertFalse(os.path.exists(self.data))

    def test_only_1h_5m_or_none(self):
        self.put('{}')
        for bad in ('2h', '', 'on', 1):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                cs.plan(bad, **self.kw())


class OddFiles(Base):
    FILES = (
        '{"a" :1,   "b":[1, 2] ,"c":{"x":"y"}}',
        '{"a" :1,   "b":[1, 2] ,"c":{"x":"y"}}\n',
        '{\r\n  "a": "été 名前 \U0001f600",\r\n  "b": null\r\n}\r\n',
        '{\n\t"z": true,\n\t"y": {"k": [ ]}\n}',
        '  {   }  \n',
        '{}',
        '',
        '\n',
        '{"名": "\\u00e9", "n": 1.50e3}',
    )

    def test_every_other_byte_stays(self):
        for text in self.FILES:
            with self.subTest(text=text):
                self.put(text)
                before_obj = json.loads(text) if text.strip() else {}
                self.assertTrue(cs.apply('1h', **self.kw())['written'])
                want = dict(before_obj)
                want[KEY] = '1h'
                self.assertEqual(self.obj(), want)
                after = self.raw().decode('utf-8')
                piece = after.replace('"%s"' % KEY, '', 1)
                self.assertEqual(len(after) - len(piece), len(KEY) + 2)
                self.assertTrue(cs.undo(**self.kw())['written'])
                self.assertEqual(self.raw(), text.encode('utf-8'))

    def test_a_later_edit_of_the_person_stays_when_the_key_comes_out(self):
        # The file changed since Switchboard wrote it, so the old copy cannot come back: the key alone is spliced out.
        # (A file that was blank or an empty object with spaces inside comes back as "{}" with its outer spacing.)
        for text in self.FILES:
            if not text.strip() or not json.loads(text):
                continue
            with self.subTest(text=text):
                self.put(text)
                cs.apply('1h', **self.kw())
                self.put(self.raw() + b' ')
                self.assertTrue(cs.undo(**self.kw())['written'])
                self.assertEqual(self.raw(), text.encode('utf-8') + b' ')

    def test_a_later_edit_inside_the_file_stays(self):
        self.put('{\n  "a": 1,\n  "b": 2\n}\n')
        cs.apply('1h', **self.kw())
        self.put(self.raw().replace(b'"a": 1', b'"a": 3'))
        cs.undo(**self.kw())
        self.assertEqual(self.raw(), b'{\n  "a": 3,\n  "b": 2\n}\n')


class ThePersonsValue(Base):
    def test_a_value_the_person_set_is_never_changed(self):
        for theirs in ('5m', '1h', '2h'):
            for want in ('1h', '5m', None):
                with self.subTest(theirs=theirs, want=want):
                    text = '{"a": 1, "%s": "%s"}' % (KEY, theirs)
                    self.put(text)
                    t = self.mtime()
                    got = cs.apply(want, **self.kw())
                    self.assertFalse(got['written'])
                    if want is None:
                        self.assertEqual(got['says'], W.NOTHING_OURS)
                    else:
                        self.assertEqual(got['says'], W.YOURS_KEPT.format(value=theirs))
                    self.assertEqual((self.raw(), self.mtime()), (text.encode(), t))
                    self.assertFalse(os.path.exists(self.record()))
                    st = cs.status(**self.kw())
                    self.assertEqual((st['value'], st['set_by']), (theirs, 'you'))
                    self.assertEqual(st['state'], theirs if theirs in ('5m', '1h') else 'other')
                    line = W.STATE_YOURS if theirs in ('5m', '1h') else W.STATE_OTHER
                    self.assertIn(line.format(value=theirs), st['lines'])

    def test_set_by_switchboard_then_changed_by_the_person_then_undo(self):
        self.put('{"a": 1}\n')
        cs.apply('1h', **self.kw())
        changed = self.raw().replace(b'"1h"', b'"5m"')
        self.put(changed)
        t = self.mtime()
        st = cs.status(**self.kw())
        self.assertEqual((st['value'], st['set_by']), ('5m', 'you'))
        got = cs.undo(**self.kw())
        self.assertFalse(got['written'])
        self.assertEqual(got['says'], W.CHANGED_SINCE.format(value='5m'))
        self.assertEqual((self.raw(), self.mtime()), (changed, t))
        # It is the person's now: a later switch leaves it too, even back at the value Switchboard first wrote.
        self.put(changed.replace(b'"5m"', b'"1h"'))
        self.assertEqual(cs.status(**self.kw())['set_by'], 'you')
        self.assertEqual(cs.apply('5m', **self.kw())['says'], W.YOURS_KEPT.format(value='1h'))

    def test_the_person_took_it_out(self):
        self.put('{"a": 1}')
        cs.apply('1h', **self.kw())
        self.put('{"a": 1}')
        got = cs.undo(**self.kw())
        self.assertEqual((got['written'], got['says']), (False, W.GONE_ALREADY))
        self.assertEqual(cs.undo(**self.kw())['says'], W.NOTHING_OURS)


class Twice(Base):
    def test_a_second_apply_changes_nothing(self):
        self.put('{"a": 1}')
        cs.apply('1h', **self.kw())
        os.utime(self.path, (1000000000, 1000000000))
        os.utime(self.record(), (1000000000, 1000000000))
        raw, t, rt = self.raw(), self.mtime(), os.stat(self.record()).st_mtime_ns
        got = cs.apply('1h', **self.kw())
        self.assertEqual((got['written'], got['op'], got['says']), (False, None, W.ALREADY.format(value='1h')))
        self.assertEqual((self.raw(), self.mtime(), os.stat(self.record()).st_mtime_ns), (raw, t, rt))


class Broken(Base):
    def test_a_file_that_is_not_json_is_left_alone(self):
        for text in ('{"a": 1,', '{"a": 1} x', '[1, 2]', '{"a": NaN}', "{'a': 1}", '{"a": 1, // note\n}'):
            with self.subTest(text=text):
                self.put(text)
                t = self.mtime()
                got = cs.apply('1h', **self.kw())
                self.assertEqual((got['written'], got['blocked'], got['says']), (False, W.BROKEN, W.BROKEN))
                self.assertEqual((self.raw(), self.mtime()), (text.encode(), t))
                self.assertEqual(cs.status(**self.kw())['problem'], W.BROKEN)

    def test_other_files_it_will_not_edit(self):
        cases = ((b'\xef\xbb\xbf{}', 'text'), (b'{"a": "\xff"}', 'text'),
                 (('{"%s": "1h", "%s": "5m"}' % (KEY, KEY)).encode(), 'twice'))
        for raw, why in cases:
            with self.subTest(why=why):
                self.put(raw)
                got = cs.apply('1h', **self.kw())
                self.assertEqual((got['written'], got['says']), (False, W.CANT_EDIT.format(why=W.WHY[why])))
                self.assertEqual(self.raw(), raw)

    def test_a_linked_or_read_only_file_is_left_alone(self):
        target = os.path.join(self.tmp, 'elsewhere.json')
        with open(target, 'w') as f:
            f.write('{}')
        os.symlink(target, self.path)
        self.assertEqual(cs.apply('1h', **self.kw())['says'], W.CANT_EDIT.format(why=W.WHY['link']))
        os.unlink(self.path)
        self.put('{}')
        os.chmod(self.path, 0o444)
        self.addCleanup(os.chmod, self.path, 0o644)
        self.assertEqual(cs.apply('1h', **self.kw())['says'], W.CANT_EDIT.format(why=W.WHY['read_only']))
        self.assertEqual(self.raw(), b'{}')
        with open(target, 'rb') as f:
            self.assertEqual(f.read(), b'{}')

    def test_a_record_that_cannot_be_read_changes_nothing(self):
        self.put('{}')
        os.makedirs(os.path.dirname(self.record()))
        with open(self.record(), 'w') as f:
            f.write('{not json')
        got = cs.apply('1h', **self.kw())
        self.assertEqual((got['written'], got['says']), (False, W.RECORD_UNREADABLE))
        self.assertEqual(self.raw(), b'{}')

    def test_whole_or_not_at_all(self):
        before = '{"a": 1}\n'
        self.put(before)
        with mock.patch.object(cs, '_save_record', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                cs.apply('1h', **self.kw())
        self.assertEqual(self.raw(), before.encode())
        self.assertEqual(cs.status(**self.kw())['set_by'], None)

    def test_a_change_while_writing_writes_nothing(self):
        before = '{"a": 1}'
        self.put(before)
        real, real_held = cs._read, cs._held

        def moved(path):            # the file changes just after Switchboard first reads it under its lock
            got = real(path)
            if os.path.realpath(path) == os.path.realpath(self.path) and moved.locked and not moved.done:
                with open(self.path, 'w') as f:
                    f.write('{"a": 2}')
                moved.done = True
            return got
        moved.locked = moved.done = False

        def held(data):
            moved.locked = True
            return real_held(data)
        with mock.patch.object(cs, '_read', side_effect=moved), mock.patch.object(cs, '_held', side_effect=held):
            got = cs.apply('1h', **self.kw())
        self.assertEqual((got['written'], got['says']), (False, W.CHANGED_WHILE))
        self.assertEqual(self.raw(), b'{"a": 2}')
        self.assertFalse(os.path.exists(self.record()))


class NoFile(Base):
    def test_no_settings_file_is_made_and_taken_away_again(self):
        p = cs.plan('1h', **self.kw())
        self.assertEqual((p['op'], p['made_file']), ('add', True))
        self.assertIn(W.MAKES_FILE, p['notes'])
        self.assertFalse(os.path.exists(self.path))
        got = cs.apply('1h', **self.kw())
        self.assertTrue(got['written'])
        self.assertIsNone(got['backup'])
        self.assertEqual(self.raw(), ('{\n  "%s": "1h"\n}\n' % KEY).encode())
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        p = cs.plan(None, **self.kw())
        self.assertIn(W.DROPS_FILE, p['notes'])
        self.assertTrue(cs.undo(**self.kw())['written'])
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(os.listdir(self.cd), [])

    def test_a_file_made_for_the_key_keeps_what_the_person_added(self):
        cs.apply('1h', **self.kw())
        self.put(self.raw().replace(b'\n}', b',\n  "theme": "dark"\n}'))
        cs.undo(**self.kw())
        self.assertEqual(self.obj(), {'theme': 'dark'})

    def test_no_claude_folder(self):
        gone = os.path.join(self.tmp, 'none')
        got = cs.apply('1h', **self.kw(claude_dir=gone))
        self.assertEqual((got['written'], got['says']), (False, W.NO_FOLDER.format(path=os.path.realpath(gone))))
        self.assertFalse(os.path.exists(gone))
        self.assertFalse(os.path.exists(self.data))


class Versions(Base):
    def test_too_old_says_so_and_writes_nothing(self):
        self.put('{}')
        st = cs.status(**self.kw(versions=OLD))
        self.assertEqual((st['version'], st['new_enough']), ('2.1.241', False))
        self.assertIn(W.VERSION_OLD.format(version='2.1.241', need='2.1.242'), st['lines'])
        got = cs.apply('1h', **self.kw(versions=OLD))
        self.assertEqual((got['written'], got['says']),
                         (False, W.VERSION_OLD_NO_WRITE.format(version='2.1.241', need='2.1.242')))
        self.assertEqual(self.raw(), b'{}')

    def test_taking_ours_out_works_on_any_version(self):
        self.put('{}')
        cs.apply('1h', **self.kw())
        self.assertTrue(cs.undo(**self.kw(versions=OLD))['written'])
        self.assertEqual(self.raw(), b'{}')

    def test_new_enough(self):
        st = cs.status(**self.kw())
        self.assertEqual((st['version'], st['new_enough']), ('2.1.332', True))
        self.assertIn(W.VERSION_OK.format(version='2.1.332'), st['lines'])
        exact = [{'where': '/x/claude', 'version': '2.1.242', 'primary': True}]
        self.assertTrue(cs.status(**self.kw(versions=exact))['new_enough'])

    def test_unknown_is_said_not_guessed(self):
        self.put('{}')
        st = cs.status(**self.kw(versions=[]))
        self.assertEqual((st['version'], st['new_enough']), (None, None))
        self.assertIn(W.VERSION_UNKNOWN.format(need='2.1.242'), st['lines'])
        got = cs.apply('1h', **self.kw(versions=[]))
        self.assertTrue(got['written'])
        self.assertIn(W.VERSION_UNKNOWN.format(need='2.1.242'), got['notes'])

    def test_another_older_install_is_said(self):
        both = NEW + [{'where': '/other/claude', 'version': '2.1.200', 'primary': False}]
        p = cs.plan('1h', **self.kw(versions=both))
        self.assertIsNone(p['blocked'])
        self.assertIn(W.VERSION_OTHER_OLD.format(version='2.1.200'), p['notes'])


class WhatWins(Base):
    def test_what_beats_the_key_is_said(self):
        self.put('{"env": {"CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL": "5m"}}')
        st = cs.status(**self.kw(environ={'FORCE_PROMPT_CACHING_5M': '1'}))
        self.assertIn(W.FORCE_WINS.format(where=W.WHERE['environ']), st['notes'])
        self.assertIn(W.ENV_WINS.format(name='CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL', where=W.WHERE['settings']),
                      st['notes'])
        md = os.path.join(self.tmp, 'managed')
        os.makedirs(md)
        with open(os.path.join(md, 'managed-settings.json'), 'w') as f:
            json.dump({KEY: '5m'}, f)
        p = cs.plan('1h', **self.kw(managed_dir=md))
        self.assertEqual(p['op'], 'add')            # said, not blocked: the person may still want it in their file
        self.assertIn(W.MANAGED_WINS.format(value='5m'), p['notes'])

    def test_the_main_chats_key_and_the_variable_are_never_written(self):
        self.put('{"promptCacheTtl": "5m"}')
        cs.apply('1h', **self.kw())
        self.assertEqual(self.obj(), {'promptCacheTtl': '5m', KEY: '1h'})


class TheDefaultFolder(Base):
    def test_the_installed_program_writes_the_default_folder(self):
        cd = os.environ['CLAUDE_CONFIG_DIR']
        os.makedirs(cd, exist_ok=True)
        path = os.path.join(cd, 'settings.json')
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        got = cs.apply('1h', **self.kw(claude_dir=None))
        self.assertTrue(got['written'])
        self.assertTrue(cs.undo(**self.kw(claude_dir=None))['written'])
        self.assertFalse(os.path.exists(path))


class Words(unittest.TestCase):
    FIELDS = {'value', 'old', 'version', 'need', 'path', 'why', 'name', 'where'}

    def sentences(self):
        out = []

        def take(name, v):
            if isinstance(v, str):
                out.append((name, v))
            elif isinstance(v, dict):
                for k, x in v.items():
                    take('%s[%r]' % (name, k), x)
        for name in dir(W):
            if not name.startswith('_') and name.isupper():
                take(name, getattr(W, name))
        return out

    def test_every_sentence(self):
        got = self.sentences()
        self.assertGreater(len(got), 30)
        for name, text in got:
            with self.subTest(name=name):
                self.assertEqual(problems(text), [], text)
                self.assertNotIn('\n', text)
                self.assertNotRegex(text, r'(?i)\bsav(e|es|ed|ing|ings)\b|cheaper|less money|\d+ ?%|best model')
                self.assertNotRegex(re.sub(r'\{\w*\}', '', text), r'(?i)\bvendors?\b|\bassistants?\b')
                fields = {f for _, f, _, _ in string.Formatter().parse(text) if f}
                self.assertLessEqual(fields, self.FIELDS, text)

    def test_the_four_things_a_person_is_told(self):
        self.assertIn('five minutes', W.WHAT)
        self.assertIn('dearer to write', W.COST)
        # The key covers everything outside the main chat, as the page lists it: compaction and session titles too,
        # and a compaction writes the whole chat to the cache.
        for part in ('helpers', 'workflow agents', 'teammates', 'forks', 'compaction', 'session titles'):
            self.assertIn(part, W.WHAT)
        self.assertIn('whole chat', W.COST)
        self.assertIn('never turns it on by itself', W.YOURS)
        self.assertIn('usage credits', W.CREDITS)
        self.assertEqual(W.KEY, cs.KEY)

    def test_the_module_says_no_setting_by_itself(self):
        with open(cs.__file__, encoding='utf-8') as f:
            text = f.read()
        self.assertEqual(problems(text), [])


if __name__ == '__main__':
    unittest.main()
