import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
import os

# Tests for the two switches of switchboard/models.py: "Models by the job" (four helper files and one note in
# Claude Code's user folder, no setting) and "Sonnet for helpers" (CLAUDE_CODE_SUBAGENT_MODEL, and
# CLAUDE_CODE_SUBAGENT_EFFORT where Claude Code reads it). Either, both or neither; one record; every change
# undone exactly, in any order. Every test works in made-up folders (test_models.Box); nothing reads or writes the
# real ~/.claude or ~/.switchboard.
import itertools
import json
import re
import shutil
import stat
import sys
import unittest
import warnings

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import models as M
import test_models as TM

FIX = TM.FIX
VAR, EFFORT = M.SUBAGENT_VAR, M.SUBAGENT_EFFORT_VAR
OLD = [{'where': '/x/claude', 'version': '2.1.309', 'primary': True}]          # before CLAUDE_CODE_SUBAGENT_EFFORT
FILES = ['switchboard-lookup.md', 'switchboard-reader.md', 'switchboard-reviewer.md', 'switchboard-verifier.md']


class Box(TM.Box):
    """test_models.Box, plus Claude Code's user rules folder."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.rules = os.path.join(self.cd, 'rules')
        self.note = os.path.join(self.rules, M.NOTE_FILE)

    def plan(self, preset='helpers', **kw):
        return M.plan(preset, self.cd, self.state, self.environ, self.managed, self.versions, **kw)

    def apply(self, preset='helpers', **kw):
        return M.apply(self.plan(preset, **kw))

    def helper(self, fn):
        return os.path.join(self.agents, fn)

    def on_parts(self):
        return set(p for p in M.PARTS if self.current()['parts'][p])

    def turn(self, part, on):
        """One switch, as the page turns it: only that part moves; the other stays as it is."""
        now = self.on_parts()
        want = (now | {part}) if on else (now - {part})
        return M.apply(self.plan(M.preset_with(want), only=part))

    def without_backups(self):
        return dict((k, v) for k, v in self.files().items() if not k.endswith('.switchboard-backup'))


def front(text):
    """The front matter of a helper file, every top-level key (comment lines left out)."""
    head = text.split('---\n')[1]
    out = {}
    for line in head.splitlines():
        if line and not line.startswith('#'):
            key, value = line.split(': ', 1)
            out[key] = value
    return out


# ---------- a record written by the build before this one ----------

class RecordWrittenBeforeThisBuild(unittest.TestCase):
    """The build before this one had one switch, Sonnet for helpers: preset "by-the-job" without its Explore file,
    one env key. Its record (tests/fixtures/models/record-before, written by that build in made-up folders) must
    read the same and be undone exactly."""

    def setUp(self):
        self.b = b = Box('unrelated.json')
        self.before = b.read()
        with open(os.path.join(FIX, 'record-before', 'settings.json'), 'rb') as f:
            b.write(f.read())
        os.chmod(b.settings, 0o644)
        b.write(self.before, b.settings + '.switchboard-backup')
        with open(os.path.join(FIX, 'record-before', 'models.json')) as f:
            text = f.read().replace('@CLAUDE@', b.cd).replace('@DATA@', os.path.dirname(b.state))
        os.makedirs(os.path.dirname(b.state))
        b.write(text.encode(), b.state)

    def test_it_reads_as_sonnet_for_helpers_on(self):
        pic = self.b.current()
        self.assertIsNone(pic['state_problem'])
        self.assertEqual(pic['preset'], 'by-the-job')
        self.assertEqual(pic['parts']['sonnet'], ['env.' + VAR])
        self.assertEqual(pic['parts']['helpers'], [])
        self.assertEqual(pic['env'][VAR], {'value': 'sonnet', 'set_by': 'switchboard'})

    def test_undo_gives_the_bytes_back(self):
        r = self.b.undo()
        self.assertEqual(self.b.read(), self.before)
        self.assertEqual(self.b.files(), {'settings.json': self.before})     # its one backup is gone too
        self.assertIsNone(r['preset'])
        self.assertEqual(self.b.current()['ours'], [])

    def test_off_gives_the_bytes_back(self):
        for only in (None, 'sonnet'):
            with self.subTest(only=only):
                self.setUp()
                M.apply(self.b.plan('off', only=only))
                self.assertEqual(self.b.read(), self.before)
                self.assertEqual(self.b.current()['ours'], [])

    def test_helpers_on_and_off_leave_its_line_as_it_is(self):
        on = self.b.read()
        self.b.turn('helpers', True)
        self.assertEqual(self.b.read(), on)                  # no effort line comes with it: that switch did not move
        self.assertEqual(sorted(os.listdir(self.b.agents)), FILES)
        self.assertEqual(self.b.current()['preset'], 'helpers-and-sonnet')
        self.b.turn('helpers', False)
        self.assertEqual(self.b.read(), on)
        self.assertFalse(os.path.exists(self.b.agents))
        self.b.turn('sonnet', False)
        self.assertEqual(self.b.read(), self.before)


# ---------- Models by the job: four helpers and one note, no setting ----------

class HelperFiles(unittest.TestCase):

    def test_on_writes_four_helpers_and_a_note_and_no_setting(self):
        b = Box('unrelated.json')
        before = b.read()
        p = b.plan()
        self.assertIsNone(p['blocked'])
        self.assertEqual([e['what'] for e in p['edits']], ['file'] * 5)
        self.assertEqual(sorted(os.path.basename(e['file']) for e in p['edits']), sorted(FILES + [M.NOTE_FILE]))
        self.assertTrue(all(e['says'] and '\n' not in e['says'] for e in p['edits']))
        self.assertEqual(b.read(), before)                   # planning writes nothing
        json.dumps(p)
        M.apply(p)
        self.assertEqual(b.read(), before)                   # the settings file is not touched at all
        self.assertEqual(b.backups(), [])
        self.assertEqual(sorted(os.listdir(b.agents)), FILES)
        self.assertEqual(os.listdir(b.rules), [M.NOTE_FILE])
        for h in M.HELPERS:
            self.assertEqual(b.read(b.helper(h['file'])).decode(), h['text'])
        self.assertEqual(b.read(b.note).decode(), M.NOTE_TEXT)
        pic = b.current()
        self.assertEqual(pic['preset'], 'helpers')
        self.assertEqual(sorted(pic['parts']['helpers']), sorted(FILES + [M.NOTE_FILE]))
        self.assertEqual(pic['parts']['sonnet'], [])
        owned = json.loads(b.state_bytes())['dirs'][b.cd]['owned']
        self.assertEqual((sorted(owned['agents']), list(owned['rules']), owned['settings']),
                         (FILES, [M.NOTE_FILE], {}))

    def test_each_helper_is_on_its_model_and_effort(self):
        got = dict((h['name'], (h['file'], front(h['text']).get('model'), front(h['text']).get('effort')))
                   for h in M.HELPERS)
        self.assertEqual(got, {'lookup': ('switchboard-lookup.md', 'haiku', 'medium'),
                               'reader': ('switchboard-reader.md', 'sonnet', 'medium'),
                               'reviewer': ('switchboard-reviewer.md', 'sonnet', 'high'),
                               'verifier': ('switchboard-verifier.md', 'sonnet', 'high')})

    def test_every_field_is_one_the_docs_name(self):
        # Claude Code ignores a field it does not know without a word, so a misspelt field would do nothing.
        named = set(re.findall(r'\| `([A-Za-z]+)` \|', M.doc('agent_rows')[0]))      # the front matter table's rows
        self.assertTrue({'name', 'description', 'model', 'effort', 'disallowedTools', 'omitClaudeMd'} <= named)
        models = set(re.findall(r'`([a-z]+)`', M.doc('agent_model')[0]))
        efforts = set(re.findall(r'`([a-z]+)`', M.doc('effort_levels')[0]))
        for h in M.HELPERS:
            with self.subTest(h['name']):
                fm = front(h['text'])
                self.assertEqual(M._frontmatter(h['text']), fm)   # what current() reads back is what was written
                self.assertEqual(set(fm) - named, set())
                self.assertEqual(fm['name'], h['name'])
                self.assertIn(fm['model'], models)
                if 'effort' in fm:
                    self.assertIn(fm['effort'], efforts)
                self.assertIn(fm.get('omitClaudeMd', 'true'), ('true',))
                for key, value in fm.items():
                    self.assertNotIn(': ', value)                 # a plain YAML scalar stays one value
                    self.assertNotIn(' #', value)
                    self.assertFalse(value[:1] in '"\'[{>|&*!%@`', value)

    def test_each_says_when_to_use_it_and_who_wrote_it(self):
        for h in M.HELPERS:
            with self.subTest(h['name']):
                fm = front(h['text'])
                self.assertTrue(fm['description'].startswith('Use '), fm['description'])
                self.assertLess(len(fm['description']), 300)
                comments = [x for x in h['text'].split('---\n')[1].splitlines() if x.startswith('#')]
                self.assertEqual(len(comments), 3)
                self.assertIn('Switchboard', comments[0])
                self.assertIn('becomes yours', comments[2])
                body = h['text'].split('---\n', 2)[2]
                self.assertTrue(body.strip())

    def test_no_helper_has_the_write_or_edit_tool(self):
        tools = dict((h['name'], front(h['text']).get('disallowedTools', '')) for h in M.HELPERS)
        for name in ('lookup', 'reader', 'reviewer', 'verifier'):     # the verifier makes scratch files by command
            self.assertEqual(tools[name], 'Write, Edit, NotebookEdit')

    def test_project_rules_reach_the_ones_that_need_them(self):
        omit = dict((h['name'], front(h['text']).get('omitClaudeMd')) for h in M.HELPERS)
        self.assertEqual(omit, {'lookup': 'true', 'reader': 'true', 'reviewer': None, 'verifier': None})

    def test_the_note_is_short_plain_and_about_jobs_only(self):
        text = M.NOTE_TEXT
        self.assertLess(len(text.splitlines()), 25)
        for name in ('`lookup`', '`reader`', '`reviewer`', '`verifier`'):
            self.assertIn(name, text)
        low = text.lower()
        for word in ('haiku', 'sonnet', 'high', 'workflow', 'build', 'again'):
            self.assertIn(word, low)
        for word in ('account', 'limit', 'usage', 'quota', 'switchboard switch'):
            self.assertNotIn(word, low)
        self.assertNotIn('---', text.splitlines()[0])                   # no front matter: it loads in every chat
        # a workflow stage names a model only: no public page says how a script names an effort for one
        flow = ' '.join(text.split('In a workflow script', 1)[1].split('\n')[:2])
        self.assertIn('name a model for each step', flow)
        self.assertNotIn('effort', flow)
        self.assertIn('When you start a helper yourself and name an effort', text)

    def test_off_takes_out_exactly_what_on_put_in(self):
        for name, folders in (('unrelated.json', False), ('empty-object.json', True), (None, False)):
            with self.subTest(name=name, folders=folders):
                b = Box(name)
                if folders:                                     # folders of yours, with a file in each
                    os.makedirs(b.agents)
                    os.makedirs(b.rules)
                    b.write(b'---\nname: mine\ndescription: Mine.\n---\n', os.path.join(b.agents, 'mine.md'))
                    b.write(b'Be brief.\n', os.path.join(b.rules, 'mine.md'))
                before = b.files()
                b.apply()
                self.assertNotEqual(b.files(), before)
                r = M.apply(b.plan('off'))
                self.assertEqual(b.files(), before)            # byte for byte, and the folders Switchboard made go
                self.assertTrue(all(not x.startswith(b.cd) for x in r['backups']))   # copies stay out of both
                self.assertEqual(b.current()['ours'], [])

    def test_undo_is_byte_for_byte(self):
        for name in ('unrelated.json', 'blank.json', None):
            with self.subTest(name):
                b = Box(name)
                before = b.files()
                b.apply()
                b.undo()
                self.assertEqual(b.files(), before)
                self.assertIsNone(b.current()['preset'])

    def test_your_own_helper_with_one_of_the_names_stays(self):
        b = Box('unrelated.json', agents=['reviewer.md'])
        mine = b.read(b.helper('reviewer.md'))
        p = b.plan()
        self.assertEqual(sorted(os.path.basename(e['file']) for e in p['edits']),
                         sorted([f for f in FILES if 'reviewer' not in f] + [M.NOTE_FILE]))
        left = [s for s in p['skipped'] if 'reviewer' in s['what']]
        self.assertEqual(len(left), 1)
        self.assertIn('your own helper named reviewer', left[0]['why'])
        self.assertIn('reviewer.md', left[0]['why'])
        M.apply(p)
        self.assertEqual(b.read(b.helper('reviewer.md')), mine)
        self.assertFalse(os.path.exists(b.helper('switchboard-reviewer.md')))
        self.assertEqual(b.plan()['edits'], [])                          # and it stays out
        M.apply(b.plan('off'))
        self.assertEqual(os.listdir(b.agents), ['reviewer.md'])

    def test_a_name_that_differs_only_in_case_is_yours_too(self):
        b = Box('empty-object.json')
        os.makedirs(os.path.join(b.agents, 'mine'))
        b.write(b'---\nname: Lookup\ndescription: My finder.\nmodel: sonnet\n---\nFind.\n',
                os.path.join(b.agents, 'mine', 'finder.md'))
        p = b.plan()
        self.assertNotIn('switchboard-lookup.md', [os.path.basename(e['file']) for e in p['edits']])
        self.assertTrue(any('lookup' in s['what'] for s in p['skipped']))

    def test_you_add_your_own_later_and_switchboards_comes_out(self):
        b = Box('empty-object.json')
        b.apply()
        shutil.copy(os.path.join(FIX, 'agents', 'reviewer.md'), b.helper('my-reviewer.md'))
        p = b.plan()
        self.assertEqual([(os.path.basename(e['file']), e['op']) for e in p['edits']],
                         [('switchboard-reviewer.md', 'delete')])
        self.assertIn('your own helper named reviewer', p['edits'][0]['says'])
        M.apply(p)
        self.assertEqual(sorted(os.listdir(b.agents)), sorted(['my-reviewer.md'] + [f for f in FILES if 'reviewer' not in f]))

    def test_an_edited_helper_becomes_yours_and_off_leaves_it_and_names_it(self):
        b = Box('empty-object.json')
        b.apply()
        path = b.helper('switchboard-reader.md')
        edited = b.read(path).replace(b'effort: medium', b'effort: high')
        b.write(edited, path)
        p = b.plan('off')
        self.assertEqual([f['file'] for f in p['forget']], [path])
        self.assertIn('yours now', p['forget'][0]['says'])
        self.assertNotIn(path, [e['file'] for e in p['edits']])
        M.apply(p)
        self.assertEqual(b.read(path), edited)                   # left as you made it
        self.assertEqual(os.listdir(b.agents), ['switchboard-reader.md'])
        self.assertFalse(os.path.exists(b.rules))
        self.assertEqual([a['set_by'] for a in b.current()['agents']], ['you'])

    def test_an_edited_note_becomes_yours_too(self):
        b = Box('empty-object.json')
        b.apply()
        b.write(b.read(b.note) + b'Also: be brief.\n', b.note)
        M.apply(b.plan('off'))
        self.assertTrue(b.read(b.note).endswith(b'Also: be brief.\n'))
        self.assertFalse(os.path.exists(b.agents))

    def test_a_file_of_the_same_name_that_is_not_switchboards_stays(self):
        b = Box('empty-object.json')
        os.makedirs(b.rules)
        b.write(b'My own models note.\n', b.note)
        p = b.plan()
        self.assertNotIn(b.note, [e['file'] for e in p['edits']])
        self.assertTrue(any(s['what'] == b.note for s in p['skipped']))
        M.apply(p)
        self.assertEqual(b.read(b.note), b'My own models note.\n')
        M.apply(b.plan('off'))
        self.assertEqual(b.read(b.note), b'My own models note.\n')

    def test_a_denied_helper_or_one_your_organization_defines_is_left_out(self):
        b = Box(None)
        b.write(b'{"permissions": {"deny": ["Agent(lookup)"]}}')
        managed = os.path.join(b.root, 'managed')
        os.makedirs(os.path.join(managed, '.claude', 'agents'))
        b.write(b'---\nname: verifier\ndescription: Ours.\n---\nCheck.\n',
                os.path.join(managed, '.claude', 'agents', 'check.md'))
        b.managed = managed
        p = b.plan()
        self.assertEqual(sorted(os.path.basename(e['file']) for e in p['edits']),
                         ['switchboard-models.md', 'switchboard-reader.md', 'switchboard-reviewer.md'])
        why = ' '.join(s['why'] for s in p['skipped'])
        self.assertIn('Agent(lookup)', why)
        self.assertIn('managed settings define a helper named verifier', why)

    def test_when_every_helper_is_left_out_the_note_stays_out_too(self):
        b = Box('empty-object.json')
        os.makedirs(b.agents)
        for h in M.HELPERS:
            b.write(('---\nname: %s\ndescription: Mine.\n---\nMine.\n' % h['name']).encode(),
                    b.helper('mine-%s.md' % h['name']))
        p = b.plan()
        self.assertEqual(p['edits'], [])
        self.assertTrue(any(s['what'] == b.note for s in p['skipped']))

    def test_effort_level_set_anywhere_comes_first_and_the_plan_says_so(self):
        for kw, where in (({'environ': {M.EFFORT_VAR: 'low'}}, 'the environment Claude Code starts from'),
                          ({'mine': b'{"env": {"CLAUDE_CODE_EFFORT_LEVEL": "high"}}'}, 'your user settings')):
            with self.subTest(where):
                mine = kw.pop('mine', None)
                b = Box('empty-object.json', **kw)
                if mine:
                    b.write(mine)
                self.assertEqual(b.current()['effort_level']['where'], where)
                for preset in ('helpers', 'sonnet'):
                    notes = ' '.join(b.plan(preset)['notes'])
                    self.assertIn('CLAUDE_CODE_EFFORT_LEVEL=', notes)
                    self.assertIn('comes first', notes)
                    self.assertIn("but not the `CLAUDE_CODE_EFFORT_LEVEL` environment variable", notes)
        self.assertIsNone(Box('empty-object.json').current()['effort_level'])
        self.assertFalse(any('CLAUDE_CODE_EFFORT_LEVEL' in n for n in Box('empty-object.json').plan()['notes']))

    def test_the_explore_file_is_not_part_of_it(self):
        b = Box('empty-object.json')
        b.apply()
        self.assertFalse(os.path.exists(b.explore))
        self.assertEqual(M.preset('helpers')['settings'], {})

    def test_force_and_old_claude_code_block_it_and_off_is_always_allowed(self):
        b = Box('force.json')
        self.assertIn('ignores the `model` field', b.plan()['blocked'])
        b = Box('empty-object.json', versions=[{'where': '/x/claude', 'version': '2.1.250', 'primary': True}])
        self.assertIn('older than 2.1.271', b.plan()['blocked'])            # its own version, not Sonnet's
        self.assertIsNone(b.plan('off')['blocked'])


# ---------- Sonnet for helpers: the model, and its effort where Claude Code reads it ----------

class SonnetForHelpers(unittest.TestCase):

    def test_a_new_claude_code_gets_the_effort_too(self):
        b = Box('unrelated.json')
        before = b.read()
        p = b.plan('sonnet')
        self.assertEqual([(e['key'], e['new'], e['op']) for e in p['edits']],
                         [('env.' + VAR, 'sonnet', 'add'), ('env.' + EFFORT, 'high', 'add')])
        self.assertIn('high effort', p['edits'][1]['says'])
        self.assertIn('forks, forked skills and teammates', p['edits'][1]['says'])
        self.assertTrue(any("It doesn't apply to forks, forked skills, or teammates" in n for n in p['notes']))
        M.apply(p)
        self.assertEqual(b.read(), before.replace(
            b'"sk-fake-not-a-real-key-000"',
            b'"sk-fake-not-a-real-key-000",\n    "CLAUDE_CODE_SUBAGENT_MODEL": "sonnet",\n'
            b'    "CLAUDE_CODE_SUBAGENT_EFFORT": "high"'))
        self.assertFalse(os.path.exists(b.agents))
        self.assertEqual(b.current()['parts']['sonnet'], ['env.' + VAR, 'env.' + EFFORT])
        M.apply(b.plan('off'))
        self.assertEqual(b.read(), before)                   # Off takes out both

    def test_an_older_claude_code_gets_the_model_alone_and_the_plan_says_why(self):
        b = Box('unrelated.json', versions=OLD)
        before = b.read()
        p = b.plan('sonnet')
        self.assertEqual([e['key'] for e in p['edits']], ['env.' + VAR])
        why = [s['why'] for s in p['skipped'] if s['what'] == 'env.' + EFFORT]
        self.assertEqual(len(why), 1)
        self.assertIn('2.1.316', why[0])
        self.assertIn('2.1.309', why[0])
        M.apply(p)
        self.assertNotIn(EFFORT, json.loads(b.read())['env'])
        M.apply(b.plan('off'))
        self.assertEqual(b.read(), before)                   # Off takes out the one it wrote

    def test_an_older_second_install_is_a_note(self):
        b = Box('empty-object.json', versions=TM.NEW + [{'where': '/usr/local/bin/claude', 'version': '2.1.300',
                                                        'primary': False}])
        p = b.plan('sonnet')
        self.assertEqual(len(p['edits']), 2)
        self.assertTrue(any('/usr/local/bin/claude' in n and '2.1.316' in n for n in p['notes']))

    def test_an_unknown_version_gets_both_and_says_what_the_effort_needs(self):
        b = Box('empty-object.json', versions=[])
        p = b.plan('sonnet')
        self.assertEqual(len(p['edits']), 2)
        self.assertTrue(any('2.1.316' in n for n in p['notes']))

    def test_an_effort_set_elsewhere_is_left_and_said(self):
        for kw, where in (({'environ': {EFFORT: 'low'}}, 'environment Claude Code starts from'),
                          ({'settings': None, 'mine': b'{"env": {"CLAUDE_CODE_SUBAGENT_EFFORT": "max"}}'}, 'Yours wins'),
                          ({'managed': True}, 'Managed settings set')):
            with self.subTest(where):
                mine = kw.pop('mine', None)
                managed = kw.pop('managed', False)
                kw.setdefault('settings', 'empty-object.json')
                b = Box(**kw)
                if mine:
                    b.write(mine)
                if managed:
                    b.managed = os.path.join(b.root, 'managed')
                    os.makedirs(b.managed)
                    b.write(b'{"env": {"CLAUDE_CODE_SUBAGENT_EFFORT": "medium"}}',
                            os.path.join(b.managed, 'managed-settings.json'))
                before = b.read()
                p = b.plan('sonnet')
                self.assertEqual([e['key'] for e in p['edits']], ['env.' + VAR])
                self.assertIn(where, [s['why'] for s in p['skipped'] if s['what'] == 'env.' + EFFORT][0])
                self.assertTrue(any(EFFORT in n for n in b.current()['notes']))
                M.apply(p)
                M.apply(b.plan('off'))
                self.assertEqual(b.read(), before)

    def test_the_effort_goes_in_only_with_the_model(self):
        b = Box('empty-object.json', environ={VAR: 'opus'})
        p = b.plan('sonnet')
        self.assertEqual(p['edits'], [])
        self.assertTrue(any(s['what'] == 'env.' + EFFORT for s in p['skipped']))

    def test_off_after_you_changed_the_effort_takes_out_the_model_alone(self):
        b = Box('empty-object.json')
        b.apply('sonnet')
        b.write(b.read().replace(b'"high"', b'"low"'))
        p = b.plan('off')
        self.assertEqual([e['key'] for e in p['edits']], ['env.' + VAR])
        self.assertIn('yours now', p['forget'][0]['says'])
        M.apply(p)
        self.assertEqual(json.loads(b.read()), {'env': {EFFORT: 'low'}})

    def test_the_earlier_preset_writes_the_effort_too(self):
        b = Box('empty-object.json')
        p = b.plan('by-the-job', lookups=False)
        self.assertEqual([e['key'] for e in p['edits']], ['env.' + VAR, 'env.' + EFFORT])

    def test_the_docs_give_both_versions(self):
        self.assertEqual(M.NEEDS_EFFORT, (2, 1, 316))
        self.assertIn('v2.1.315', M.doc('subagent_effort')[0])
        self.assertIn('Added `CLAUDE_CODE_SUBAGENT_EFFORT`', M.doc('subagent_effort_added')[0])
        self.assertTrue(M.doc('subagent_effort_added')[1].endswith('/changelog'))
        self.assertIn(EFFORT, M.MODEL_VARS)


# ---------- the two together ----------

class TheTwoTogether(unittest.TestCase):
    """Either, both or neither, in every order: each combination gives the same bytes, whatever the way to it."""

    def test_presets_are_data(self):
        self.assertEqual(dict((p['id'], tuple(p['parts'])) for p in M.PRESETS),
                         {'off': (), 'helpers': ('helpers',), 'sonnet': ('sonnet',),
                          'helpers-and-sonnet': ('helpers', 'sonnet'), 'by-the-job': ('sonnet', 'explore')})
        for parts, pid in (((), 'off'), (('helpers',), 'helpers'), (('sonnet',), 'sonnet'),
                           (('sonnet', 'helpers'), 'helpers-and-sonnet')):
            self.assertEqual(M.preset_with(parts), pid)
        self.assertEqual(M.PART_NAMES, {'helpers': 'Models by the job', 'sonnet': 'Sonnet for helpers'})

    def test_every_order_gives_the_same_bytes(self):
        steps = [('helpers', True), ('sonnet', True), ('helpers', False), ('sonnet', False)]
        seen = {}
        for name in ('unrelated.json', 'compact.json', None):
            for order in itertools.permutations(steps):
                b = Box(name)
                on, ok = set(), True
                seen.setdefault((name, frozenset()), b.without_backups())
                for part, turn_on in order:
                    if turn_on == (part in on):
                        ok = False                  # turning on what is on, or off what is off: not an order
                        break
                    b.turn(part, turn_on)
                    on = (on | {part}) if turn_on else (on - {part})
                    with self.subTest(name=name, order=order, now=sorted(on)):
                        got = b.without_backups()
                        self.assertEqual(got, seen.setdefault((name, frozenset(on)), got))
                        self.assertEqual(b.on_parts(), on)
                        st = json.loads(b.state_bytes())
                        self.assertEqual(list(st['dirs']), [b.cd])                 # one record
                        self.assertEqual(st['dirs'][b.cd]['preset'], M.preset_with(on))
                if ok:
                    self.assertEqual(b.without_backups(), seen[(name, frozenset())])
        self.assertEqual(len(seen), 12)

    def test_undo_after_each_step_goes_back_one_step(self):
        # One settings change in the run, so every step can be undone (the settings file keeps one backup copy).
        b = Box('unrelated.json')
        snaps = [b.without_backups()]
        for part, on in (('helpers', True), ('sonnet', True), ('helpers', False)):
            b.turn(part, on)
            snaps.append(b.without_backups())
        for want in reversed(snaps[:-1]):
            b.undo()
            self.assertEqual(b.without_backups(), want)
        self.assertEqual(b.files(), snaps[0])               # and the backup copy is gone with the last undo
        self.assertEqual(b.current()['ours'], [])

    def test_turning_one_leaves_what_you_did_to_the_other(self):
        b = Box('empty-object.json')
        b.turn('helpers', True)
        os.unlink(b.helper('switchboard-lookup.md'))          # you took one helper out
        b.turn('sonnet', True)
        self.assertFalse(os.path.exists(b.helper('switchboard-lookup.md')))     # Sonnet on did not put it back
        b.turn('sonnet', False)
        b.turn('helpers', False)
        self.assertFalse(os.path.exists(b.agents))
        self.assertEqual(b.read(), TM.fixture('empty-object.json'))


# ---------- found by the review of the first build (each of these failed on it) ----------

LINK_WHY = 'is a link'
SHAPES_ONCE_LOST = (b'', b'\n', b'{\n  "env": {\n  },\n  "theme": "dark"\n}\n')


def own_reviewer(raw):
    return raw.replace(b'NAME', b'reviewer')


class TheNoteIsTrueWithBothOn(unittest.TestCase):

    def test_it_says_nothing_of_which_model_an_unnamed_step_runs_on(self):
        # With Sonnet for helpers on, a step that names no model runs on Sonnet, not on the chat's model.
        self.assertNotIn("run on this chat's model", M.NOTE_TEXT)
        self.assertNotIn("chat's own model", M.NOTE_TEXT)
        for line in ('Building, fixing and deciding are not jobs for these four. Keep them in this chat, or hand them '
                     'to a helper that\nnames no model.',
                     'A helper with one of these names that a project or the command line defines takes the place of '
                     'the one above there.',
                     'lookups, and none for steps that build.\n',
                     "When a helper's answer looks wrong, do that step again in this chat.\n"):
            self.assertIn(line, M.NOTE_TEXT)
        self.assertNotIn('chat keeps its model', ' '.join(h['says'] for h in M.HELPERS).lower())


class LinksAndReadOnlyAfterOn(unittest.TestCase):
    """A settings file, a helper file or the note that becomes a link, or is set read-only, after On."""

    def linked(self, b, path):
        target = os.path.join(b.root, 'dotfiles', os.path.basename(path))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.move(path, target)                     # what a dotfiles tool does: the same bytes, now a link
        os.symlink(target, path)
        return target

    def test_undo_never_writes_through_a_link(self):
        b = Box('unrelated.json')
        b.apply('sonnet')
        target = self.linked(b, b.settings)
        held = b.read(target)
        with self.assertRaises(M.Refused) as e:
            b.undo()
        self.assertIn(LINK_WHY, str(e.exception))
        self.assertTrue(os.path.islink(b.settings))
        self.assertEqual(b.read(target), held)

    def test_undo_never_writes_a_read_only_file(self):
        b = Box('unrelated.json')
        b.apply('sonnet')
        os.chmod(b.settings, 0o444)
        self.addCleanup(os.chmod, b.settings, 0o644)
        held = b.read()
        with self.assertRaises(M.Refused) as e:
            b.undo()
        self.assertIn('read-only', str(e.exception))
        self.assertEqual((b.read(), stat.S_IMODE(os.stat(b.settings).st_mode)), (held, 0o444))

    def test_undo_never_removes_a_read_only_settings_file_it_made(self):
        b = Box(None)
        b.turn('sonnet', True)                       # Switchboard makes the file
        os.chmod(b.settings, 0o444)
        self.addCleanup(os.chmod, b.settings, 0o644)
        held = b.read()
        with self.assertRaises(M.Refused) as e:
            b.undo()
        self.assertIn('read-only', str(e.exception))
        self.assertEqual((b.read(), stat.S_IMODE(os.stat(b.settings).st_mode)), (held, 0o444))
        self.assertEqual(b.current()['parts']['sonnet'], ['env.' + VAR, 'env.' + EFFORT])

    def test_a_read_only_helper_file_or_note_still_goes_both_ways(self):
        for way in ('undo', 'off'):
            with self.subTest(way=way):
                b = Box('empty-object.json')
                before = b.files()
                b.turn('helpers', True)
                for p in (b.helper('switchboard-reviewer.md'), b.note):
                    os.chmod(p, 0o444)                   # Switchboard's own files: it made them, it takes them away
                if way == 'undo':
                    b.undo()
                else:
                    b.turn('helpers', False)
                self.assertEqual(b.files(), before)
                self.assertFalse(os.path.exists(b.agents) or os.path.exists(b.rules))

    def test_off_takes_nothing_through_a_link_and_says_why(self):
        b = Box('unrelated.json')
        b.turn('sonnet', True)
        b.turn('helpers', True)
        target = self.linked(b, b.settings)
        held = b.read(target)
        p = b.plan('helpers', only='sonnet')
        self.assertEqual(p['edits'], [])
        self.assertTrue(all(LINK_WHY in s['why'] for s in p['skipped']), p['skipped'])
        self.assertEqual(sorted(s['what'] for s in p['skipped']), ['env.' + EFFORT, 'env.' + VAR])
        M.apply(p)
        self.assertTrue(os.path.islink(b.settings))
        self.assertEqual(b.read(target), held)
        self.assertEqual(b.current()['parts']['sonnet'], ['env.' + VAR, 'env.' + EFFORT])   # still Switchboard's

    def test_a_helper_file_that_became_a_link_is_yours(self):
        b = Box('empty-object.json')
        b.apply()
        path = b.helper('switchboard-reviewer.md')
        target = self.linked(b, path)
        held = b.read(target)
        self.assertNotIn('switchboard-reviewer.md', b.current()['parts']['helpers'])
        with self.assertRaises(M.Refused):
            b.undo()                                  # undo would take a link away: no
        p = b.plan('off')
        self.assertNotIn(path, [e['file'] for e in p['edits']])
        self.assertIn(path, [f['file'] for f in p['forget']])
        M.apply(p)
        self.assertTrue(os.path.islink(path))
        self.assertEqual(b.read(target), held)
        self.assertEqual(os.listdir(b.agents), ['switchboard-reviewer.md'])


class OffGivesTheBytesBackAfterTheOtherSwitch(unittest.TestCase):
    """Sonnet on, Models by the job on (or on and off), Sonnet off: Off takes the plan's way, and must still give
    these three settings files back byte for byte."""

    def test_three_shapes(self):
        for raw in SHAPES_ONCE_LOST:
            for between in ((('helpers', True),), (('helpers', True), ('helpers', False))):
                with self.subTest(raw=raw, between=between):
                    b = Box(None)
                    b.write(raw)
                    b.turn('sonnet', True)
                    for part, on in between:
                        b.turn(part, on)
                    b.turn('sonnet', False)
                    self.assertEqual(b.read(), raw)
                    b.turn('helpers', False)
                    self.assertEqual(b.without_backups(), {'settings.json': raw})
                    self.assertEqual(b.current()['ours'], [])

    def test_a_file_changed_since_is_edited_as_before(self):
        b = Box(None)
        b.write(SHAPES_ONCE_LOST[2])
        b.turn('sonnet', True)
        b.turn('helpers', True)
        b.write(b.read().replace(b'"dark"', b'"light"'))            # you changed it: no copy can be put back
        b.turn('sonnet', False)
        self.assertEqual(json.loads(b.read()), {'env': {}, 'theme': 'light'})


class TheLastChecksComeBeforeTheCopy(unittest.TestCase):
    """A settings file that became a link, or was set read-only, after the plan was made: apply() refuses before it
    replaces the copy beside the file (the status line keeps its own copy in the same file)."""

    def test_the_copy_beside_stays_as_it_was(self):
        for how in ('link', 'read-only'):
            with self.subTest(how=how):
                b = Box('unrelated.json')
                copy = b.settings + '.switchboard-backup'
                b.write(b"the status line's copy\n", copy)
                p = b.plan('sonnet')
                self.assertTrue(p['edits'])
                held = b.read()
                target = b.settings
                if how == 'link':
                    target = os.path.join(b.root, 'dotfiles', 'settings.json')
                    os.makedirs(os.path.dirname(target))
                    shutil.move(b.settings, target)
                    os.symlink(target, b.settings)
                else:
                    os.chmod(b.settings, 0o444)
                    self.addCleanup(os.chmod, b.settings, 0o644)
                with self.assertRaises(M.Refused) as e:
                    M.apply(p)
                self.assertIn('is a link now' if how == 'link' else 'is set read-only', str(e.exception))
                self.assertEqual((os.path.islink(b.settings), b.read(target)), (how == 'link', held))
                self.assertEqual(b.read(copy), b"the status line's copy\n")
                self.assertIsNone(b.state_bytes())


class YourOwnHelperNamedInOtherYamlForms(unittest.TestCase):
    """Claude Code reads a helper file's front matter with a YAML reader. When this reader gets no plain name from a
    file of yours, every one of the four names that stands as a word in its front matter counts as yours."""
    FORMS = ('name:\n  reviewer', 'name: >-\n  reviewer', 'name: |-\n  reviewer', 'name: >\n  reviewer',
             'name: |\n  reviewer', 'name: !!str reviewer', 'name: &n reviewer', '? name\n: reviewer',
             '  name: reviewer', 'name: ""\nx: reviewer', 'name: *n\nx: &n reviewer', 'name: [reviewer]',
             'name: {first: reviewer}')

    def mine(self, b, text):
        os.makedirs(b.agents, exist_ok=True)
        b.write(text.encode('utf-8'), b.helper('mine.md'))

    def added(self, b):
        p = b.plan()
        return sorted(os.path.basename(e['file']) for e in p['edits'] if os.path.dirname(e['file']) == b.agents)

    def test_switchboards_own_stays_out(self):
        for spell in self.FORMS:
            with self.subTest(spell=spell):
                b = Box('empty-object.json')
                pre = '  ' if spell.startswith(' ') else ''
                self.mine(b, '---\n%s\n%sdescription: Mine.\n---\n\nMine.\n' % (spell, pre))
                self.assertEqual(self.added(b), [f for f in FILES if 'reviewer' not in f])
                self.assertTrue(any('your own helper named reviewer (agents/mine.md)' in s['why']
                                    for s in b.plan()['skipped']))

    def test_every_name_that_stands_as_a_word_is_taken_in_any_case(self):
        b = Box('empty-object.json')
        self.mine(b, '---\nname: >-\n  my-helper\ndescription: A Reader and a LOOKUP; not a proofreader, nor two '
                     'reviewers.\n---\n\nIt asks the verifier.\n')
        self.assertEqual(self.added(b), ['switchboard-reviewer.md', 'switchboard-verifier.md'])

    def test_a_name_that_is_read_is_judged_by_itself_alone(self):
        b = Box('empty-object.json')
        self.mine(b, '---\nname: helper-two\ndescription: Works with the reviewer and the verifier.\n---\n\nMine.\n')
        self.assertEqual(self.added(b), FILES)

    def test_only_a_file_with_front_matter(self):
        b = Box('empty-object.json')
        self.mine(b, '---\nname: >-\n  reviewer\n')                          # no closing line: no front matter
        self.assertEqual(self.added(b), FILES)

    def test_no_warning_on_a_newer_python(self):
        with warnings.catch_warnings():
            warnings.simplefilter('error')
            self.assertEqual(M._unquote(' reviewer # mine'), 'reviewer')


class YourOwnHelperWrittenOtherWays(unittest.TestCase):
    FORMS = (b'---\nname: "NAME"  # mine\ndescription: Mine.\n---\n\nMine.\n',
             b"---\nname: 'NAME' # mine\ndescription: Mine.\n---\n\nMine.\n",
             b'---\n"name": NAME\ndescription: Mine.\n---\n\nMine.\n',
             b"---\n'name': NAME\ndescription: Mine.\n---\n\nMine.\n",
             b'\xef\xbb\xbf---\nname: NAME\ndescription: Mine.\n---\n\nMine.\n',
             b'---\nname: "NAME"\t# mine\ndescription: Mine.\n---\n\nMine.\n',
             b'---\nname: "NAME"\ndescription: "A # in quotes"\n---\n\nMine.\n')

    def test_the_name_is_read(self):
        for raw in self.FORMS:
            with self.subTest(raw=raw):
                text = own_reviewer(raw).decode('utf-8')
                self.assertEqual(M._frontmatter(text)['name'], 'reviewer')
        self.assertEqual(M._frontmatter('---\nname: x\ndescription: "A # in quotes"\n---\n')['description'],
                         'A # in quotes')

    def test_switchboards_own_stays_out(self):
        for raw in self.FORMS:
            with self.subTest(raw=raw):
                b = Box('empty-object.json')
                os.makedirs(b.agents)
                b.write(own_reviewer(raw), b.helper('mine.md'))
                p = b.plan()
                self.assertNotIn('switchboard-reviewer.md', [os.path.basename(e['file']) for e in p['edits']])
                self.assertTrue(any('your own helper named reviewer' in s['why'] for s in p['skipped']))


class ModelsByTheJobHasItsOwnBlocks(unittest.TestCase):

    def test_it_needs_2_1_271_in_its_own_words(self):
        for v in ('2.1.250', '2.1.260', '2.1.270'):
            with self.subTest(v=v):
                b = Box('empty-object.json', versions=[{'where': '/x/claude', 'version': v, 'primary': True}])
                why = b.plan('helpers')['blocked']
                self.assertIn("is older than 2.1.271, which the helper files' `omitClaudeMd` field needs (", why)
                self.assertIn('Requires Claude Code v2.1.271 or later', why)
                self.assertTrue(why.endswith('). Update Claude Code first.'), why)
                self.assertNotIn('newest version', why)
                self.assertNotIn(VAR, why)
        b = Box('empty-object.json', versions=[{'where': '/x/claude', 'version': '2.1.260', 'primary': True}])
        self.assertIsNone(b.plan('sonnet')['blocked'])                  # Sonnet for helpers keeps 2.1.251
        b = Box('empty-object.json', versions=[{'where': '/x/claude', 'version': '2.1.271', 'primary': True}])
        self.assertIsNone(b.plan('helpers')['blocked'])
        b = Box('empty-object.json', versions=[])
        self.assertTrue(any('2.1.271' in n for n in b.plan('helpers')['notes']))

    def test_a_read_only_settings_file_does_not_block_it(self):
        b = Box('unrelated.json')
        os.chmod(b.settings, 0o444)
        self.addCleanup(os.chmod, b.settings, 0o644)
        p = b.plan('helpers')
        self.assertIsNone(p['blocked'])
        self.assertEqual(len(p['edits']), 5)
        self.assertIn('read-only', b.plan('sonnet')['blocked'])          # the setting still can't go in

    def test_a_settings_file_that_cant_be_read_blocks_it_in_its_own_words(self):
        why = ("Claude Code's settings file can't be read, so Switchboard can't tell whether you deny one of these "
               'helpers.')
        for name in ('comment.json', 'env-string.json', 'dup-env.json'):
            with self.subTest(name):
                self.assertEqual(Box(name).plan('helpers')['blocked'], why)     # a plan writes nothing either way
        self.assertEqual(M.HELPERS_UNREADABLE_WHY, why)
        self.assertEqual(M.HELPERS_UNREADABLE, why + ' Nothing was written.')  # what a refused press says


class HelpersAreNotCalledReadOnly(unittest.TestCase):

    def test_none_has_write_or_edit_and_none_is_called_read_only(self):
        for h in M.HELPERS:
            with self.subTest(h['name']):
                fm = front(h['text'])
                self.assertEqual(fm['disallowedTools'], 'Write, Edit, NotebookEdit')
                self.assertIn('with no Write or Edit tool', fm['description'])
                self.assertNotIn('read-only', fm['description'].lower())
                self.assertIn('with no Write or Edit tool', h['says'])
                self.assertNotIn('read-only', h['says'])
        verifier = front(M.VERIFIER_TEXT)['description']
        self.assertTrue(verifier.endswith('On Sonnet at high effort, with no Write or Edit tool. It is told never to '
                                          'change the code under test.'), verifier)
        self.assertIn('- You may run commands, and make scratch files with a command in a temporary folder. Never '
                      'change the code or files under test; if something needs fixing, say what and where.\n',
                      M.VERIFIER_TEXT)
        self.assertIn('It can still change files through Bash.', M.doc('tools_bash')[0])


class DocsTidy(unittest.TestCase):

    def test_the_fields_come_from_the_front_matter_table(self):
        rows = M.doc('agent_rows')
        self.assertTrue(rows[1].endswith('#supported-frontmatter-fields'))
        self.assertEqual(sorted(re.findall(r'\| `([A-Za-z]+)` \|', rows[0])),
                         ['description', 'disallowedTools', 'effort', 'model', 'name', 'omitClaudeMd'])
        self.assertNotIn('agent_fields', M.DOCS)

    def test_the_version_it_needs_is_said_plainly(self):
        with open(os.path.join(os.path.dirname(FIX), '..', '..', 'docs', 'how-it-works.md'), encoding='utf-8') as f:
            how = f.read()
        self.assertIn('It needs Claude Code 2.1.271 or newer: two of the helper files use `omitClaudeMd`, which needs '
                      'it. A settings file that is set read-only does not stop it, since it writes no setting; one '
                      "that can't be read does, since it may deny one of the helpers.", how)
        self.assertNotIn('the newest version a field', how)
        omit = sorted(h['name'] for h in M.HELPERS if 'omitClaudeMd' in front(h['text']))
        self.assertEqual(omit, ['lookup', 'reader'])                         # two of the helper files
        self.assertIn('Requires Claude Code v2.1.271 or later', M.doc('omit_claude_md')[0])

    def test_three_more_sentences(self):
        self.assertIn('The per-invocation parameter requires Claude Code v2.1.292 or later.', M.doc('effort_param')[0])
        self.assertIn('Haiku 5.5 default to `medium`', M.doc('haiku_default')[0])
        says = M._file_out('agents', M.EXPLORE_FILE, '/x/agents/' + M.EXPLORE_FILE, None, None, {})['says']
        self.assertNotIn('restart', says)

    def test_the_security_list_names_the_helper_files_and_the_note(self):
        with open(os.path.join(os.path.dirname(FIX), '..', '..', 'SECURITY.md'), encoding='utf-8') as f:
            line = next(x for x in f.read().splitlines() if x.startswith("- What Switchboard writes into Claude Code's"))
        self.assertIn("Models by the job's four helper files in `~/.claude/agents/` and its note in `~/.claude/rules/`",
                      line)


# ---------- what history can say ----------

class FromHistory(unittest.TestCase):
    ROWS = [{'type': 'lookup', 'set_model': 'haiku', 'family': 'Haiku', 'chat_family': 'Opus', 'share': 0.1,
             'requests': 9, 'kind': 'research'},
            {'type': 'reviewer', 'set_model': 'sonnet', 'family': 'Sonnet', 'chat_family': 'Opus', 'share': 0.2,
             'requests': 4, 'kind': 'review'},
            {'type': 'general-purpose', 'set_model': None, 'family': 'Opus', 'chat_family': 'Opus', 'share': 0.3,
             'requests': 3, 'kind': 'build'}]

    def test_seen_names_the_helpers(self):
        h = dict((x['helper'], x) for x in M.seen({'helper_rows': self.ROWS})['helpers'])
        self.assertEqual(h['lookup']['models'], [{'family': 'Haiku', 'share': 1.0}])
        self.assertEqual(h['reviewer']['models'], [{'family': 'Sonnet', 'share': 1.0}])
        self.assertEqual(h['lookup']['no_model_set'], 0.0)

    def test_no_number_for_what_the_helpers_would_have_moved(self):
        r = M.would_have_moved({'helper_rows': self.ROWS})
        self.assertAlmostEqual(r['share'], 0.3)            # only the general-purpose helper: Sonnet for helpers
        self.assertNotIn('helpers', r)
        self.assertEqual(r['not_counted'], [])             # helpers that name their model are not part of it


if __name__ == '__main__':
    unittest.main()
