import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
import os
import tempfile

# Tests for switchboard/models.py.
#
# Every test works in its own throwaway folder: a made-up Claude Code folder, a made-up
# Switchboard record, an empty environment and a stated Claude Code version. Nothing reads or
# writes the real ~/.claude or ~/.switchboard.
import glob
import json
import shutil
import stat
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import models as M

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'models')
NEW = [{'where': '/opt/claude/bin/claude', 'version': '2.1.320', 'primary': True}]
VAR = M.SUBAGENT_VAR
EXPLORE = M.EXPLORE_FILE


def fixture(name):
    with open(os.path.join(FIX, 'settings', name), 'rb') as f:
        return f.read()


def summary(name):
    with open(os.path.join(FIX, 'summaries', name)) as f:
        return json.load(f)


class Box(object):
    """A made-up Claude Code folder plus Switchboard's record, in a temp folder."""

    def __init__(self, settings=None, agents=(), environ=None, versions=None, managed=None):
        self.root = tempfile.mkdtemp(prefix='switchboard-models-')
        self.cd = os.path.realpath(os.path.join(self.root, 'claude'))
        os.makedirs(self.cd)
        self.state = os.path.join(self.root, 'data', 'models.json')
        self.settings = os.path.join(self.cd, 'settings.json')
        self.agents = os.path.join(self.cd, 'agents')
        self.explore = os.path.join(self.agents, EXPLORE)
        self.environ = {} if environ is None else environ
        self.versions = NEW if versions is None else versions
        self.managed = managed or os.path.join(self.root, 'no-managed')
        if settings is not None:
            with open(self.settings, 'wb') as f:
                f.write(fixture(settings))
        for rel in agents:
            dst = os.path.join(self.agents, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy(os.path.join(FIX, 'agents', rel), dst)

    def plan(self, preset='by-the-job', **kw):
        return M.plan(preset, self.cd, self.state, self.environ, self.managed, self.versions, **kw)

    def current(self):
        return M.current(self.cd, self.state, self.environ, self.managed, self.versions)

    def apply(self, preset='by-the-job', **kw):
        return M.apply(self.plan(preset, **kw))

    def undo(self, **kw):
        return M.undo(self.cd, self.state, **kw)

    def read(self, path=None):
        try:
            with open(path or self.settings, 'rb') as f:
                return f.read()
        except FileNotFoundError:
            return None

    def write(self, data, path=None):
        with open(path or self.settings, 'wb') as f:
            f.write(data)

    def state_bytes(self):
        return self.read(self.state)

    def backups(self):
        return sorted(glob.glob(self.settings + '.switchboard-backup'))

    def files(self):
        """Every file under the Claude folder, with its bytes."""
        out = {}
        for dp, dn, fn in os.walk(self.cd):
            for f in fn:
                p = os.path.join(dp, f)
                out[os.path.relpath(p, self.cd)] = self.read(p)
        return out


ADDED = ',\n    "CLAUDE_CODE_SUBAGENT_MODEL": "sonnet"'


class TestPresets(unittest.TestCase):

    def test_presets_are_data(self):
        ids = [p['id'] for p in M.PRESETS]
        self.assertEqual(ids, ['off', 'by-the-job'])
        self.assertEqual([p['id'] for p in M.PRESETS if p['default']], ['by-the-job'])
        self.assertEqual(M.preset('by-the-job')['settings'], {('env', VAR): 'sonnet'})
        self.assertEqual(M.preset('off')['settings'], {})
        self.assertEqual(M.preset('off')['agents'], {})

    def test_everything_small_is_not_offered(self):
        with self.assertRaises(M.Refused) as e:
            M.preset('everything-small')
        self.assertIn('ignores the `model` field', str(e.exception))
        with self.assertRaises(M.Refused):
            Box().plan('everything-small')
        with self.assertRaises(M.Refused):
            Box().plan('no-such-preset')

    def test_explore_file_frontmatter(self):
        fm = M._frontmatter(M.EXPLORE_TEXT)
        self.assertEqual(fm['name'], 'Explore')
        self.assertEqual(fm['model'], 'haiku')
        self.assertEqual(fm['omitClaudeMd'], 'true')
        self.assertEqual(fm['disallowedTools'], 'Write, Edit, NotebookEdit')
        self.assertNotIn('effort', fm)       # nothing else changes: no effort line
        head = M.EXPLORE_TEXT.split('---\n')[1]
        for line in head.splitlines():
            if line and not line.startswith('#'):
                key, value = line.split(': ', 1)
                self.assertNotIn(': ', value)       # a plain YAML scalar stays one value
                self.assertNotIn(' #', value)


class TestPlanAndApply(unittest.TestCase):

    def test_unrelated_keys_only_our_line_is_added(self):
        b = Box('unrelated.json')
        before = b.read()
        p = b.plan()
        self.assertIsNone(p['blocked'])
        self.assertEqual([(e['what'], e['key'], e['old'], e['new'], e['op']) for e in p['edits']],
                         [('setting', 'env.' + VAR, None, 'sonnet', 'add'),
                          ('file', None, None, 'model: haiku', 'create')])
        self.assertTrue(all(e['says'] and '\n' not in e['says'] for e in p['edits']))
        self.assertEqual(b.read(), before)                  # planning writes nothing
        self.assertFalse(os.path.exists(b.agents))
        json.dumps(p)                                       # the plan is plain data
        self.assertNotIn('sk-fake', json.dumps(p))          # and carries no settings content
        r = M.apply(p)
        after = b.read()
        self.assertEqual(after, before.replace(b'"sk-fake-not-a-real-key-000"',
                                               b'"sk-fake-not-a-real-key-000"' + ADDED.encode()))
        want = json.loads(before)
        want['env'][VAR] = 'sonnet'
        self.assertEqual(json.loads(after), want)
        self.assertEqual(list(json.loads(after)), list(json.loads(before)))     # key order kept
        self.assertEqual(b.read(b.explore).decode(), M.EXPLORE_TEXT)
        self.assertEqual(r['changed'], [b.explore, b.settings])
        self.assertEqual(len(r['backups']), 1)
        self.assertEqual(b.read(r['backups'][0]), before)
        self.assertNotIn(b'sk-fake', b.state_bytes())        # the record holds no settings content

    def test_missing_file_is_created_and_undo_removes_it(self):
        b = Box(None)
        p = b.plan()
        self.assertEqual(p['fingerprints'][b.settings], None)
        self.assertTrue(p['edits'][0]['made_file'])
        M.apply(p)
        self.assertEqual(b.read(), b'{\n  "env": {\n    "CLAUDE_CODE_SUBAGENT_MODEL": "sonnet"\n  }\n}\n')
        self.assertEqual(stat.S_IMODE(os.stat(b.settings).st_mode), 0o600)
        self.assertEqual(b.backups(), [])                   # nothing existed, nothing to back up
        b.undo()
        self.assertIsNone(b.read())
        self.assertFalse(os.path.exists(b.agents))          # the folder Switchboard made goes too
        self.assertEqual(b.files(), {})

    def test_missing_file_off_deletes_what_switchboard_created(self):
        b = Box(None)
        b.apply()
        r = M.apply(b.plan('off'))
        self.assertEqual(b.files(), {})                     # not even a backup is left in the Claude folder
        self.assertFalse(os.path.exists(b.agents))
        self.assertTrue(all(x.startswith(os.path.dirname(b.state)) for x in r['backups']))
        b.undo()
        self.assertEqual(json.loads(b.read()), {'env': {VAR: 'sonnet'}})    # and Off can still be undone

    def test_empty_object(self):
        b = Box('empty-object.json')
        b.apply()
        self.assertEqual(b.read(), b'{\n  "env": {\n    "CLAUDE_CODE_SUBAGENT_MODEL": "sonnet"\n  }\n}\n')
        M.apply(b.plan('off'))
        self.assertEqual(b.read(), b'{}\n')                 # Off takes exactly our part back out

    def test_blank_file(self):
        b = Box('blank.json')
        b.apply()
        self.assertEqual(json.loads(b.read()), {'env': {VAR: 'sonnet'}})
        b.undo()
        self.assertEqual(b.read(), b'')                     # undo is byte for byte, even for an empty file

    def test_formats_kept_and_off_is_exact(self):
        cases = {
            'odd.json': ',"CLAUDE_CODE_SUBAGENT_MODEL":"sonnet"',            # compact env inside a CRLF file
            'compact.json': ',"env":{"CLAUDE_CODE_SUBAGENT_MODEL":"sonnet"}',
            'one-line-spaced.json': ', "env": {"CLAUDE_CODE_SUBAGENT_MODEL": "sonnet"}',
            'four-space.json': ',\n    "env": {\n        "CLAUDE_CODE_SUBAGENT_MODEL": "sonnet"\n    }',
            'unrelated.json': ADDED,
        }
        for name, piece in cases.items():
            with self.subTest(name):
                b = Box(name)
                before = b.read()
                b.apply()
                after = b.read()
                self.assertEqual(len(after) - len(before), len(piece.encode()))
                i = next(i for i in range(len(before) + 1) if before[:i] != after[:i]) - 1
                self.assertEqual(after[:i] + after[i + len(piece.encode()):], before)
                self.assertEqual(after[i:i + len(piece.encode())], piece.encode())
                want = json.loads(before)
                want.setdefault('env', {})[VAR] = 'sonnet'
                self.assertEqual(json.loads(after), want)
                M.apply(b.plan('off'))
                self.assertEqual(b.read(), before)          # Off is the exact inverse here
                self.assertFalse(os.path.exists(b.explore))

    def test_env_empty_object(self):
        b = Box('env-empty.json')
        before = b.read()
        b.apply()
        self.assertEqual(b.read(), before.replace(
            b'"env": {}', b'"env": {\n    "CLAUDE_CODE_SUBAGENT_MODEL": "sonnet"\n  }'))
        M.apply(b.plan('off'))
        self.assertEqual(b.read(), before)

    def test_crlf_and_tabs_kept(self):
        b = Box('odd.json')
        b.apply()
        after = b.read()
        self.assertEqual(after.count(b'\r\n'), fixture('odd.json').count(b'\r\n'))
        self.assertNotIn(b'\n', after.replace(b'\r\n', b''))
        self.assertFalse(after.endswith(b'\n'))             # it had no final newline; still none
        self.assertIn('b\\u00e4r é'.encode('utf-8'), after)  # escapes and UTF-8 untouched

    def test_users_own_model_setting_wins(self):
        b = Box('own-model.json')
        before = b.read()
        p = b.plan()
        self.assertEqual([e['what'] for e in p['edits']], ['file'])     # only the Explore helper
        self.assertIn('Yours wins', p['skipped'][0]['why'])
        M.apply(p)
        self.assertEqual(b.read(), before)
        pic = b.current()
        self.assertEqual(pic['env'][VAR], {'value': 'opus', 'set_by': 'you'})
        self.assertIn('opus', pic['helpers_with_no_model_run'])
        self.assertTrue(any('CLAUDE_CODE_EFFORT_LEVEL=high' in n for n in pic['notes']))

    def test_users_inherit_value_is_theirs_too(self):
        b = Box('own-inherit.json')
        p = b.plan()
        self.assertEqual([e['what'] for e in p['edits']], ['file'])
        self.assertTrue(any(VAR in s['why'] for s in p['skipped']))

    def test_shell_export_wins(self):
        b = Box('empty-object.json', environ={VAR: 'opus'})
        p = b.plan()
        self.assertEqual([e['what'] for e in p['edits']], ['file'])
        self.assertIn('environment Claude Code starts from', p['skipped'][0]['why'])

    def test_managed_setting_wins(self):
        b = Box('empty-object.json', managed=os.path.join(FIX, 'managed'))
        p = b.plan()
        self.assertEqual([e['what'] for e in p['edits']], ['file'])
        self.assertIn('Managed settings set', p['skipped'][0]['why'])
        pic = b.current()
        self.assertEqual(pic['managed']['env'], {VAR: 'haiku'})
        self.assertTrue(any('availableModels list has no Sonnet' in w for w in pic['warnings']))

    def test_force_blocks(self):
        for kw in ({'settings': 'force.json'}, {'settings': 'empty-object.json', 'environ': {M.FORCE_VAR: '1'}}):
            with self.subTest(kw):
                b = Box(**kw)
                before = b.files()
                p = b.plan()
                self.assertIn('ignores the `model` field', p['blocked'])
                self.assertEqual(p['edits'], [])
                with self.assertRaises(M.Refused):
                    M.apply(p)
                self.assertEqual(b.files(), before)
                self.assertTrue(any('FORCE' in w for w in b.current()['warnings']))
        b = Box('empty-object.json', environ={M.FORCE_VAR: '0'})
        self.assertIsNone(b.plan()['blocked'])              # "0" is off

    def test_broken_settings_are_refused(self):
        for name, words in (('comment.json', 'strict JSON'), ('trailing-comma.json', 'strict JSON'),
                            ('dup-env.json', 'twice'), ('env-string.json', 'not an object'),
                            ('bom.json', 'byte-order mark'), ('nan.json', 'not valid JSON')):
            with self.subTest(name):
                b = Box(name)
                before = b.files()
                p = b.plan()
                self.assertTrue(p['blocked'] and words.replace('twice', '2 times') in p['blocked']
                                if words == 'twice' else words in p['blocked'], p['blocked'])
                with self.assertRaises(M.Refused):
                    M.apply(p)
                self.assertEqual(b.files(), before)
                self.assertTrue(b.current()['warnings'])

    def test_linked_settings_file_is_left_alone(self):
        b = Box(None)
        target = os.path.join(b.root, 'dotfiles-settings.json')
        with open(target, 'wb') as f:
            f.write(fixture('unrelated.json'))
        os.symlink(target, b.settings)
        p = b.plan()
        self.assertEqual([e['what'] for e in p['edits']], ['file'])
        self.assertIn('is a link', p['skipped'][0]['why'])
        M.apply(p)
        self.assertTrue(os.path.islink(b.settings))
        self.assertEqual(b.read(target), fixture('unrelated.json'))

    def test_idempotent(self):
        b = Box('unrelated.json', agents=['reviewer.md'])
        b.apply()
        files, state, backups = b.files(), b.state_bytes(), b.backups()
        p = b.plan()
        self.assertEqual((p['edits'], p['forget'], p['skipped']), ([], [], []))
        self.assertEqual(M.apply(p), {'changed': [], 'backups': [], 'edits': []})
        self.assertEqual((b.files(), b.state_bytes(), b.backups()), (files, state, backups))
        self.assertEqual(b.plan('off')['edits'][0]['op'], 'remove')     # and Off still knows what is ours

    def test_changed_under_us_refuses(self):
        b = Box('unrelated.json')
        p = b.plan()
        edited = fixture('unrelated.json').replace(b'"dark"', b'"light"')
        b.write(edited)                          # e.g. /config changed the theme meanwhile
        with self.assertRaises(M.Refused) as e:
            M.apply(p)
        self.assertIn('changed since this plan was made', str(e.exception))
        self.assertEqual(b.read(), edited)
        self.assertFalse(os.path.exists(b.agents))      # nothing at all was written
        self.assertIsNone(b.state_bytes())
        M.apply(b.plan())                               # a fresh plan works
        self.assertIn(b'"light"', b.read())

    def test_agent_file_appearing_under_us_refuses(self):
        b = Box('empty-object.json')
        p = b.plan()
        os.makedirs(b.agents)
        b.write(b'mine', b.explore)
        with self.assertRaises(M.Refused):
            M.apply(p)
        self.assertEqual(b.read(), fixture('empty-object.json'))
        self.assertEqual(b.read(b.explore), b'mine')

    def test_record_changed_under_us_refuses(self):
        b = Box('empty-object.json')
        p1, p2 = b.plan(), b.plan()
        M.apply(p1)
        with self.assertRaises(M.Refused):
            M.apply(p2)

    def test_mode_kept_and_backup_matches(self):
        b = Box('unrelated.json')
        os.chmod(b.settings, 0o640)
        r = b.apply()
        self.assertEqual(stat.S_IMODE(os.stat(b.settings).st_mode), 0o640)
        self.assertEqual(stat.S_IMODE(os.stat(r['backups'][0]).st_mode), 0o600)      # private, whatever the file's
        self.assertEqual(os.path.basename(r['backups'][0]), 'settings.json.switchboard-backup')
        self.assertEqual(glob.glob(os.path.join(b.cd, '.*switchboard-tmp')), [])     # no temp files left

    def test_failure_part_way_puts_everything_back(self):
        b = Box('unrelated.json')
        before = b.files()
        real = M._atomic_write

        def boom(path, data, mode):
            if path.endswith('settings.json'):
                raise OSError('disk full')
            return real(path, data, mode)
        with mock.patch.object(M, '_atomic_write', boom):
            with self.assertRaises(OSError):
                b.apply()
        after = b.files()
        for k in list(after):
            if '.switchboard-' in k:
                del after[k]                     # a backup may remain; it changes nothing
        self.assertEqual(after, before)
        self.assertFalse(os.path.exists(b.agents))
        self.assertIsNone(b.state_bytes())

    def test_write_between_check_and_swap(self):
        b = Box('unrelated.json')
        theirs = fixture('unrelated.json').replace(b'"dark"', b'"light"')
        real = M._backup_next_to

        def claude_code_writes_now(path, data, mode):
            out = real(path, data, mode)
            b.write(theirs)                      # e.g. /model saved a default at this very moment
            return out
        with mock.patch.object(M, '_backup_next_to', claude_code_writes_now):
            with self.assertRaises(M.Refused) as e:
                b.apply()
        self.assertIn('changed while Switchboard was writing', str(e.exception))
        self.assertEqual(b.read(), theirs)                   # their write is kept
        self.assertFalse(os.path.exists(b.agents))           # the file written before it is taken back
        self.assertIsNone(b.state_bytes())

    def test_off_with_broken_settings_still_removes_the_agent_file(self):
        b = Box('empty-object.json')
        b.apply()
        b.write(b'{ "env": { "CLAUDE_CODE_SUBAGENT_MODEL": "sonnet" }, }')     # now has a trailing comma
        p = b.plan('off')
        self.assertIsNone(p['blocked'])
        self.assertEqual([e['op'] for e in p['edits']], ['delete'])
        self.assertIn('strict JSON', p['skipped'][0]['why'])
        M.apply(p)
        self.assertFalse(os.path.exists(b.explore))
        self.assertEqual(b.read(), b'{ "env": { "CLAUDE_CODE_SUBAGENT_MODEL": "sonnet" }, }')     # untouched
        owned = json.loads(b.state_bytes())['dirs'][b.cd]['owned']
        self.assertEqual(list(owned['settings']), ['env.' + VAR])     # still Switchboard's, to take out later
        self.assertEqual(owned['agents'], {})

    def test_state_save_failure_puts_files_back(self):
        b = Box('empty-object.json')
        with mock.patch.object(M, '_save_state', side_effect=OSError('read-only')):
            with self.assertRaises(OSError):
                b.apply()
        self.assertEqual(b.read(), fixture('empty-object.json'))
        self.assertFalse(os.path.exists(b.agents))

    def test_busy_chat_refuses_unless_asked(self):
        b = Box('empty-object.json')
        chat = os.path.join(b.cd, 'projects', '-Users-ana-garden', 'abc.jsonl')
        os.makedirs(os.path.dirname(chat))
        b.write(b'{}\n', chat)
        self.assertEqual(M.busy(b.cd), 1)
        p = b.plan()
        with self.assertRaises(M.Refused) as e:
            M.apply(p)
        self.assertIn('running', str(e.exception))
        self.assertEqual(b.read(), fixture('empty-object.json'))
        M.apply(p, busy_ok=True)
        self.assertEqual(json.loads(b.read()), {'env': {VAR: 'sonnet'}})
        old = time.time() - 3600
        os.utime(chat, (old, old))
        self.assertEqual(M.busy(b.cd), 0)
        b.undo()


class TestUndo(unittest.TestCase):

    def test_undo_is_byte_for_byte(self):
        for name in ('unrelated.json', 'odd.json', 'compact.json', 'env-empty.json', 'four-space.json',
                     'empty-object.json', 'blank.json', None):
            with self.subTest(name):
                b = Box(name, agents=['reviewer.md'])
                before = b.files()
                b.apply()
                self.assertNotEqual(b.files(), before)
                r = b.undo()
                self.assertEqual(b.files(), before)
                self.assertIsNone(r['preset'])
                self.assertEqual(b.current()['ours'], [])
                with self.assertRaises(M.Refused):
                    b.undo()                     # nothing left to undo

    def test_undo_refuses_after_a_later_change(self):
        b = Box('unrelated.json')
        b.apply()
        changed = b.read().replace(b'"dark"', b'"light"')
        b.write(changed)
        with self.assertRaises(M.Refused) as e:
            b.undo()
        self.assertIn('Choose Off instead', str(e.exception))
        self.assertEqual(b.read(), changed)
        self.assertTrue(os.path.exists(b.explore))       # nothing was changed, not even the other file
        M.apply(b.plan('off'))                           # Off takes out only ours, keeps "light"
        want = json.loads(fixture('unrelated.json'))
        want['theme'] = 'light'
        self.assertEqual(json.loads(b.read()), want)
        self.assertEqual(b.read(), fixture('unrelated.json').replace(b'"dark"', b'"light"'))
        self.assertFalse(os.path.exists(b.explore))

    def test_undo_refuses_when_backup_altered(self):
        b = Box('unrelated.json')
        r = b.apply()
        b.write(b'{}', r['backups'][0])
        with self.assertRaises(M.Refused) as e:
            b.undo()
        self.assertIn('backup', str(e.exception))

    def test_undo_of_off_restores_switchboards_part(self):
        b = Box('unrelated.json')
        b.apply()
        backup = b.settings + '.switchboard-backup'
        on = dict((k, v) for k, v in b.files().items() if k != os.path.basename(backup))
        M.apply(b.plan('off'))
        self.assertFalse(os.path.exists(b.agents))       # Switchboard made the folder, Off took it away
        b.undo()
        self.assertEqual(b.files(), on)                  # and the one backup, used, is gone
        self.assertEqual(b.current()['preset'], 'by-the-job')
        self.assertEqual(b.plan()['edits'], [])

    def test_only_the_last_change_can_be_undone_exactly(self):
        # One backup per settings file: each change replaces it, so the change before the last can no longer
        # be put back byte for byte. Undo then refuses and changes nothing; Off still takes out Switchboard's key.
        b = Box('unrelated.json')
        b.apply()
        M.apply(b.plan('off'))
        b.apply()
        b.undo()
        after_one = b.files()
        with self.assertRaises(M.Refused) as cm:
            b.undo()
        self.assertIn('Nothing was changed', str(cm.exception))
        self.assertEqual(b.files(), after_one)


class TestOwnership(unittest.TestCase):

    def test_user_changed_our_value(self):
        b = Box('empty-object.json')
        b.apply()
        b.write(b.read().replace(b'"sonnet"', b'"haiku"'))
        p = b.plan('off')
        self.assertEqual([e['what'] for e in p['edits']], ['file'])      # the setting is theirs now
        self.assertIn('yours now', p['forget'][0]['says'])
        M.apply(p)
        self.assertEqual(json.loads(b.read()), {'env': {VAR: 'haiku'}})
        self.assertEqual(b.current()['env'][VAR]['set_by'], 'you')
        p = b.plan()
        self.assertEqual([e['what'] for e in p['edits']], ['file'])
        self.assertIn('Yours wins', ' '.join(s['why'] for s in p['skipped']))

    def test_user_removed_our_value(self):
        b = Box('unrelated.json')
        b.apply()
        b.write(fixture('unrelated.json'))
        p = b.plan('off')
        self.assertEqual(p['forget'][0]['key'], 'env.' + VAR)
        M.apply(p)
        self.assertEqual(b.read(), fixture('unrelated.json'))
        b.apply()                                       # choosing the preset again adds it again
        self.assertIn(ADDED.encode(), b.read())

    def test_user_edited_our_agent_file(self):
        b = Box('empty-object.json')
        b.apply()
        b.write(M.EXPLORE_TEXT.replace('model: haiku', 'model: sonnet').encode(), b.explore)
        p = b.plan()
        self.assertEqual(p['edits'], [])
        self.assertIn('yours now', p['forget'][0]['says'])
        M.apply(p)
        M.apply(b.plan('off'))
        self.assertIn(b'model: sonnet', b.read(b.explore))       # left alone
        self.assertEqual([a['set_by'] for a in b.current()['agents']], ['you'])

    def test_user_deleted_our_agent_file(self):
        b = Box('empty-object.json')
        b.apply()
        os.unlink(b.explore)
        p = b.plan('off')
        self.assertEqual([e['what'] for e in p['edits']], ['setting'])
        self.assertIn('already gone', p['forget'][0]['says'])
        b2 = Box('empty-object.json')
        b2.apply()
        os.unlink(b2.explore)
        self.assertEqual(b2.plan()['edits'][0]['op'], 'create')

    def test_off_with_nothing_of_ours(self):
        b = Box('unrelated.json', agents=['reviewer.md'])
        p = b.plan('off')
        self.assertEqual((p['edits'], p['forget']), ([], []))
        self.assertEqual(M.apply(p)['changed'], [])

    def test_off_keeps_env_the_user_added_to(self):
        b = Box('empty-object.json')
        b.apply()
        d = json.loads(b.read())
        b.write(b.read().replace(b'"sonnet"', b'"sonnet",\n    "MINE": "1"'))
        M.apply(b.plan('off'))
        self.assertEqual(json.loads(b.read()), {'env': {'MINE': '1'}})
        self.assertEqual(d, {'env': {VAR: 'sonnet'}})


class TestExploreHelper(unittest.TestCase):

    def test_users_own_explore_wins(self):
        b = Box('empty-object.json', agents=['research/my-explore.md'])
        p = b.plan()
        self.assertEqual([e['what'] for e in p['edits']], ['setting'])
        self.assertIn('your own Explore', p['skipped'][0]['why'])
        pic = b.current()
        self.assertEqual([(a['name'], a['model']) for a in pic['agents']], [('Explore', 'sonnet')])

    def test_user_adds_own_explore_later(self):
        b = Box('empty-object.json', agents=['reviewer.md'])
        b.apply()
        os.makedirs(os.path.join(b.agents, 'research'))
        shutil.copy(os.path.join(FIX, 'agents', 'research', 'my-explore.md'), os.path.join(b.agents, 'research'))
        p = b.plan()
        self.assertEqual([(e['what'], e['op']) for e in p['edits']], [('file', 'delete')])
        self.assertIn('your own Explore', p['edits'][0]['says'])
        M.apply(p)
        self.assertFalse(os.path.exists(b.explore))
        self.assertEqual(sorted(a['name'] for a in b.current()['agents']), ['Explore', 'reviewer'])
        self.assertEqual(b.plan()['edits'], [])                 # and it stays out
        self.assertEqual(json.loads(b.read()), {'env': {VAR: 'sonnet'}})

    def test_explore_turned_off_or_denied(self):
        for kw, words in (({'environ': {M.NO_EXPLORE_PLAN_VAR: '1'}}, 'turned the built-in Explore off'),
                          ({'settings': 'deny-explore.json'}, 'deny Agent(Explore)')):
            with self.subTest(words):
                kw.setdefault('settings', 'empty-object.json')
                b = Box(**kw)
                p = b.plan()
                self.assertEqual([e['what'] for e in p['edits']], ['setting'])
                self.assertIn(words, p['skipped'][0]['why'])

    def test_lookups_can_be_left_out(self):
        b = Box('empty-object.json')
        p = b.plan(lookups=False)
        self.assertEqual([e['what'] for e in p['edits']], ['setting'])
        M.apply(p)
        self.assertFalse(os.path.exists(b.agents))

    def test_existing_agents_folder_is_kept(self):
        b = Box('empty-object.json', agents=['reviewer.md'])
        p = b.plan()
        self.assertFalse(p['edits'][1]['made_dir'])
        self.assertNotIn('restart', p['edits'][1]['says'])
        M.apply(p)
        M.apply(b.plan('off'))
        self.assertEqual(os.listdir(b.agents), ['reviewer.md'])
        b2 = Box('empty-object.json')
        self.assertIn('restart', b2.plan()['edits'][1]['says'])     # a new folder needs a restart

    def test_agent_backups_stay_out_of_the_agents_folder(self):
        b = Box('empty-object.json', agents=['reviewer.md'])
        b.apply()
        r = M.apply(b.plan('off'))
        agent_copies = [x for x in r['backups'] if 'switchboard-explore' in x]
        self.assertEqual(len(agent_copies), 1)
        self.assertFalse(agent_copies[0].startswith(b.cd))
        self.assertEqual(b.read(agent_copies[0]).decode(), M.EXPLORE_TEXT)
        self.assertEqual(os.listdir(b.agents), ['reviewer.md'])


class TestVersions(unittest.TestCase):

    def test_old_claude_code_blocks(self):
        b = Box('empty-object.json', versions=[{'where': '/x/claude', 'version': '2.1.250', 'primary': True}])
        p = b.plan()
        self.assertIn('older than 2.1.251', p['blocked'])
        self.assertIsNone(b.plan('off')['blocked'])              # Off is always allowed

    def test_old_second_install_is_a_note(self):
        b = Box('empty-object.json', versions=NEW + [{'where': '/usr/local/bin/claude', 'version': '2.1.126',
                                                     'primary': False}])
        p = b.plan()
        self.assertIsNone(p['blocked'])
        self.assertTrue(any('/usr/local/bin/claude' in n for n in p['notes']))
        self.assertTrue(any('/usr/local/bin/claude' in w for w in b.current()['warnings']))

    def test_explore_needs_omit_claude_md(self):
        b = Box('empty-object.json', versions=[{'where': '/x/claude', 'version': '2.1.260', 'primary': True}])
        p = b.plan()
        self.assertEqual([e['what'] for e in p['edits']], ['setting'])
        self.assertIn('omitClaudeMd', p['skipped'][0]['why'])

    def test_unknown_version_is_said(self):
        b = Box('empty-object.json', versions=[])
        self.assertTrue(any('could not find' in n for n in b.plan()['notes']))

    def test_versions_read_from_disk(self):
        root = tempfile.mkdtemp(prefix='switchboard-models-')
        native = os.path.join(root, 'home', '.local', 'share', 'claude', 'versions', '2.1.330')
        os.makedirs(os.path.dirname(native))
        with open(native, 'w') as f:
            f.write('#!/bin/sh\n')
        os.chmod(native, 0o755)
        bin1 = os.path.join(root, 'bin1')
        os.makedirs(bin1)
        os.symlink(native, os.path.join(bin1, 'claude'))
        pkg = os.path.join(root, 'npm', 'lib', 'node_modules', '@anthropic-ai', 'claude-code')
        os.makedirs(os.path.join(pkg, 'bin'))
        with open(os.path.join(pkg, 'package.json'), 'w') as f:
            json.dump({'name': '@anthropic-ai/claude-code', 'version': '2.1.126'}, f)
        exe = os.path.join(pkg, 'bin', 'claude.exe')
        with open(exe, 'w') as f:
            f.write('x')
        os.chmod(exe, 0o755)
        bin2 = os.path.join(root, 'bin2')
        os.makedirs(bin2)
        os.symlink(exe, os.path.join(bin2, 'claude'))
        got = M._versions(home=os.path.join(root, 'home'), path=os.pathsep.join([bin1, bin2, bin1]), extra=[])
        self.assertEqual([(v['version'], v['primary']) for v in got], [('2.1.330', True), ('2.1.126', False)])


class TestCurrent(unittest.TestCase):

    def test_picture(self):
        b = Box('unrelated.json', agents=['reviewer.md'], environ={'ANTHROPIC_MODEL': 'opus', 'HOME': '/x'})
        pic = b.current()
        self.assertEqual(pic['env'], {})                         # unrelated env entries are never read out
        self.assertNotIn('sk-fake', json.dumps(pic))
        self.assertEqual(pic['environ'], {'ANTHROPIC_MODEL': 'opus'})
        self.assertEqual(pic['agents'], [{'file': os.path.join('agents', 'reviewer.md'),
                                          'path': os.path.join(b.agents, 'reviewer.md'), 'name': 'reviewer',
                                          'model': 'sonnet', 'effort': 'high', 'set_by': 'you'}])
        self.assertEqual(pic['ours'], [])
        self.assertIn("your chat's model", pic['helpers_with_no_model_run'])
        b.apply()
        pic = b.current()
        self.assertEqual(pic['env'][VAR], {'value': 'sonnet', 'set_by': 'switchboard'})
        self.assertEqual(sorted(o['what'] for o in pic['ours']), ['file', 'setting'])
        self.assertEqual(pic['preset'], 'by-the-job')
        self.assertIn('sonnet', pic['helpers_with_no_model_run'])
        explore = [a for a in pic['agents'] if a['name'] == 'Explore'][0]
        self.assertEqual((explore['model'], explore['set_by']), ('haiku', 'switchboard'))

    def test_haiku_chat_warning(self):
        pic = Box('haiku-model.json').current()
        self.assertTrue(any('larger model than your chat' in w for w in pic['warnings']))

    def test_unreadable_record_blocks(self):
        b = Box('empty-object.json')
        os.makedirs(os.path.dirname(b.state))
        b.write(b'not json', b.state)
        self.assertIn("can't be read", b.plan()['blocked'])
        self.assertTrue(b.current()['state_problem'])
        with self.assertRaises(M.Refused):
            b.undo()

    def test_missing_claude_folder(self):
        p = M.plan('by-the-job', '/nonexistent/claude-folder', '/nonexistent/state.json', {}, None, NEW)
        self.assertIn('no Claude Code folder', p['blocked'])

    def test_defaults_resolve_at_call_time(self):
        with mock.patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': '/a/b', 'SWITCHBOARD_HOME': '/c/d'}):
            self.assertEqual(M.default_claude_dir(), '/a/b')
            self.assertEqual(M.default_state_path(), '/c/d/models.json')


class TestJsonEditor(unittest.TestCase):
    """Adding then taking out our line gives back the same bytes, whatever the formatting."""

    def test_round_trip_many_formats(self):
        docs = [{}, {'theme': 'dark'}, {'env': {}}, {'env': {'A': '1'}}, {'a': [1, {'b': None}], 'env': {'A': 'x'},
                                                                         'z': 'é "\\'}]
        styles = [dict(indent=None), dict(indent=None, separators=(',', ':')), dict(indent=0), dict(indent=2),
                  dict(indent=4), dict(indent='\t'), dict(indent=2, ensure_ascii=False)]
        for d in docs:
            for s in styles:
                for nl in ('\n', '\r\n'):
                    text = json.dumps(d, **s).replace('\n', nl) + nl
                    with self.subTest(doc=d, style=s, nl=repr(nl)):
                        doc_ = M._Doc(text.encode('utf-8'), 'x.json')
                        added, made = M._env_set(doc_, VAR, 'sonnet')
                        want = json.loads(json.dumps(d))
                        want.setdefault('env', {})[VAR] = 'sonnet'
                        self.assertEqual(json.loads(added), want)
                        self.assertEqual(made, 'env' not in d)
                        doc2 = M._Doc(added.encode('utf-8'), 'x.json')
                        back = M._env_del(doc2, VAR, made)
                        if d.get('env') == {} and s.get('indent') is not None:
                            self.assertEqual(json.loads(back), d)       # an empty {} may lose inner spaces
                        elif d:
                            self.assertEqual(back, text)
                        else:
                            self.assertEqual(json.loads(back), d)

    def test_changing_an_existing_value(self):
        doc_ = M._Doc(b'{"env": {"A": "1", "CLAUDE_CODE_SUBAGENT_MODEL" : "sonnet" }}', 'x.json')
        text, made = M._env_set(doc_, VAR, 'haiku')
        self.assertEqual(text, '{"env": {"A": "1", "CLAUDE_CODE_SUBAGENT_MODEL" : "haiku" }}')
        self.assertFalse(made)

    def test_check_catches_a_wrong_splice(self):
        doc_ = M._Doc(b'{"a": 1}', 'x.json')
        with self.assertRaises(M.Refused):
            M._checked(doc_, '{"a": 2}', {'a': 1})


class TestWouldHaveMoved(unittest.TestCase):
    """The made-up week: 39% ran on the chat's model with no model set. By hand:
    to Sonnet: lane:infra 20 + verify:beds 6 + research:soil 4 = 30 (workflow and general-purpose on Opus);
    to Haiku: Find watering code 2 (Explore, Opus chat) + Find seeds 0.5 (Explore on Opus under a Fable chat);
    not moved: fork 3, custom my-reviewer 2, Plan 1, general-purpose already on Sonnet 0.5;
    moved up: general-purpose under a Haiku chat 0.5. 30 + 2 + 3 + 2 + 1 + 0.5 + 0.5 = 39."""

    def test_exact(self):
        r = M.would_have_moved(summary('with-helper-rows.json'))
        self.assertTrue(r['exact'])
        self.assertAlmostEqual(r['no_model_set'], 0.39)
        self.assertAlmostEqual(r['to_sonnet'], 0.30)
        self.assertAlmostEqual(r['to_haiku'], 0.025)
        self.assertAlmostEqual(r['share'], 0.325)
        self.assertAlmostEqual(r['moved_up'], 0.005)
        self.assertEqual([(x['label'], x['to']) for x in r['from']],
                         [('lane:infra', 'Sonnet'), ('verify:beds', 'Sonnet'), ('research:soil', 'Sonnet'),
                          ('Find watering code', 'Haiku'), ('Find seeds', 'Haiku')])
        nc = dict((x['reason'], x['share']) for x in r['not_counted'])
        self.assertEqual(sorted(nc), ['already', 'custom', 'fork', 'plan'])
        for k, v in (('fork', 0.03), ('custom', 0.02), ('plan', 0.01), ('already', 0.005)):
            self.assertAlmostEqual(nc[k], v)
        in_base_moved = r['share'] - 0.005          # Find seeds is outside the 39%
        self.assertAlmostEqual(in_base_moved + r['moved_up'] + sum(nc.values()), r['no_model_set'])

    def test_without_lookups(self):
        r = M.would_have_moved(summary('with-helper-rows.json'), lookups=False)
        self.assertAlmostEqual(r['share'], 0.30)
        self.assertAlmostEqual(dict((x['reason'], x['share']) for x in r['not_counted'])['explore_off'], 0.02)

    def test_todays_summary_shape_gives_a_bound(self):
        r = M.would_have_moved(summary('today-shape.json'))
        self.assertFalse(r['exact'])
        self.assertIsNone(r['share'])
        self.assertAlmostEqual(r['at_most'], 0.385)          # Opus 38 + Sonnet 0.5 (Explore could move)
        self.assertAlmostEqual(r['no_model_set'], 0.39)
        self.assertEqual(r['from'][0], {'label': 'lane:infra', 'share': 0.20, 'to': None})
        self.assertIn("doesn't say which kind of helper", r['why'])
        self.assertAlmostEqual(M.would_have_moved(summary('today-shape.json'), lookups=False)['at_most'], 0.38)

    def test_empty(self):
        self.assertEqual(M.would_have_moved({})['at_most'], 0.0)
        self.assertEqual(M.would_have_moved({'helper_rows': []})['share'], 0.0)

    def test_named_and_unknown_rows(self):
        rows = [{'type': None, 'set_model': None, 'family': 'Opus', 'chat_family': 'Opus', 'share': 0.1},
                {'type': 'general-purpose', 'set_model': 'opus', 'family': 'Opus', 'chat_family': 'Opus',
                 'share': 0.2},
                {'type': 'general-purpose', 'set_model': None, 'family': 'Opus', 'chat_family': 'Sonnet',
                 'share': 0.3}]
        r = M.would_have_moved({'helper_rows': rows})
        self.assertEqual(r['share'], 0.0)
        self.assertAlmostEqual(r['no_model_set'], 0.1)
        self.assertEqual([x['reason'] for x in r['not_counted']], ['unknown'])


class TestSeen(unittest.TestCase):

    def test_exact(self):
        r = M.seen(summary('with-helper-rows.json'))
        self.assertTrue(r['exact'])
        h = dict((x['helper'], x) for x in r['helpers'])
        self.assertEqual(r['helpers'][0]['helper'], 'workflow agents')
        self.assertAlmostEqual(h['workflow agents']['share'], 0.36)
        self.assertEqual([m['family'] for m in h['workflow agents']['models']], ['Opus', 'Sonnet'])
        self.assertAlmostEqual(h['workflow agents']['models'][0]['share'], 0.26 / 0.36)
        self.assertAlmostEqual(h['workflow agents']['no_model_set'], 0.26 / 0.36)
        self.assertEqual(h['Explore']['models'], [{'family': 'Opus', 'share': 1.0}])
        self.assertEqual(h['forks']['requests'], 30)
        self.assertEqual(h['claude-code-guide']['models'], [{'family': 'Haiku', 'share': 1.0}])
        k = dict((x['kind'], x) for x in r['kinds'])
        self.assertAlmostEqual(k['build']['share'], 0.35)

    def test_todays_summary_shape(self):
        r = M.seen(summary('today-shape.json'))
        self.assertFalse(r['exact'])
        self.assertEqual(r['workflows'][0]['id'], 'wf_garden-001')
        self.assertEqual(r['helpers'], [])


class TestLimits(unittest.TestCase):

    def test_every_limit_has_its_doc_sentence(self):
        got = M.limits()
        self.assertEqual(len(got), len(M.LIMITS))
        for item in got:
            self.assertTrue(item['cannot'] and item['why'])
            self.assertTrue(item['docs'])
            for d in item['docs']:
                self.assertTrue(d['url'].startswith('https://code.claude.com/docs/en/'))
                self.assertTrue(len(d['quote']) > 20)

    def test_docstring_lists_the_same_limits(self):
        doc_ = ' '.join(M.__doc__.split())
        for cannot, _, _ in M.LIMITS:
            self.assertIn(' '.join(cannot.split()), doc_)

    def test_every_doc_key_is_used_or_known(self):
        for key in M.DOCS:
            q, url = M.doc(key)
            self.assertTrue(url.startswith(M.DOCS_BASE))


if __name__ == '__main__':
    unittest.main()
