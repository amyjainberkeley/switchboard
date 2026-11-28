import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# What Switchboard writes into a Codex folder for the routes, and how it takes it back. Throwaway folders only.
#
# - toml_string: quotes, backslashes, newlines, control characters and non-ASCII come out as TOML basic strings
#   (read back with tomllib where the Python has it).
# - plan_files: one agents/switchboard-<kind>.toml per kind whose first usable choice for Codex names a model or an
#   effort, with name, description, developer_instructions, model, model_reasoning_effort.
# - apply/undo/status: each file whole; its hash kept in Switchboard's own record; a file Switchboard did not write
#   (or one changed since) is never overwritten or removed, and is named in one line; undo takes back exactly what
#   apply wrote.
# - hooks_apply/hooks_undo: the two entries merged into hooks.json with everything already there kept byte for byte;
#   a second apply changes nothing; a file that is not JSON is left alone; undo takes out only marked entries, also
#   after the person edited the file; config.toml is never touched.
import copy
import difflib
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)

from switchboard.router import adapter_words as W  # noqa: E402
from switchboard.router import codex_files as cf  # noqa: E402
from switchboard.router import hookline  # noqa: E402

try:
    import tomllib
except ImportError:             # Python 3.9 and 3.10 have no TOML reader
    tomllib = None

NOW = 1764239000.0


def make_plan(**over):
    p = {
        'v': 1, 'made_at': NOW - 10, 'good_for_s': 180, 'on': True, 'dry': False, 'by': 'switchboard test',
        'order': ['claude', 'codex'],
        'agents': {
            'claude': {'can': True, 'why_not': None, 'room': 'room', 'back_at': None, 'at_lines': False},
            'codex': {'can': True, 'why_not': None, 'room': 'room', 'back_at': None, 'at_lines': False},
        },
        'routes': {
            'find': [{'agent': 'codex', 'model': 'gpt-6-luna', 'effort': 'medium'}],
            'review': [{'agent': 'codex', 'model': 'gpt-6.1-sol', 'effort': 'high'}],
            'build': [{'agent': 'claude', 'model': 'opus', 'effort': 'high'},
                      {'agent': 'codex', 'model': 'gpt-6.1-sol', 'effort': None}],
        },
        'why': {}, 'helpers': {'force': False}, 'chat': {'effort': False, 'model_on_first_turn': False},
        'hand': {'on': True, 'write': False, 'max_s': 900}, 'carry_on': False,
        'kinds': {'order': ['build', 'fix', 'review', 'plan', 'find', 'write', 'other'], 'light': 'find', 'cut': 300,
                  'table': {}},
        'types': {},
    }
    p.update(over)
    return p


def read(path):
    with open(path, 'rb') as f:
        return f.read()


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(data if isinstance(data, bytes) else data.encode('utf-8'))


class Base(unittest.TestCase):
    def setUp(self):
        self.top = tempfile.mkdtemp(prefix='codex-files-')
        self.home = os.path.join(self.top, 'codex')
        os.makedirs(self.home)
        self.state = os.path.join(self.top, 'data', 'router', 'codex-files.json')
        self.agents = os.path.join(self.home, 'agents')
        self.addCleanup(shutil.rmtree, self.top, True)

    def apply(self, plan):
        return cf.apply(plan, codex_home=self.home, state_path=self.state, now=NOW)

    def undo(self):
        return cf.undo(codex_home=self.home, state_path=self.state)

    def names(self):
        return sorted(os.listdir(self.agents)) if os.path.isdir(self.agents) else []


class Toml(unittest.TestCase):
    def test_escapes(self):
        self.assertEqual(cf.toml_string('plain'), '"plain"')
        self.assertEqual(cf.toml_string('say "hi"'), '"say \\"hi\\""')
        self.assertEqual(cf.toml_string('C:\\path\\x'), '"C:\\\\path\\\\x"')
        self.assertEqual(cf.toml_string('a\nb\r\tc'), '"a\\nb\\r\\tc"')
        self.assertEqual(cf.toml_string('\x01\x1f\x7f\b\f'), '"\\u0001\\u001F\\u007F\\b\\f"')
        self.assertEqual(cf.toml_string('caf\u00e9 \u4e2d \U0001F642'), '"caf\u00e9 \u4e2d \U0001F642"')
        self.assertEqual(cf.toml_string('\ud800'), '"\ufffd"')         # a lone surrogate is not text

    @unittest.skipIf(tomllib is None, 'no tomllib in this Python')
    def test_reads_back(self):
        for s in ('say "hi"', 'C:\\path\\x', 'a\nb\r\tc', '\x01\x1f\x7f\b\f', 'caf\u00e9 \u4e2d \U0001F642',
                  '\'single\' and """triple"""', '\\u0041 is not an escape here', ''):
            with self.subTest(s=s):
                self.assertEqual(tomllib.loads('k = ' + cf.toml_string(s))['k'], s)

    def test_a_helper_file(self):
        text = cf.helper_text('find', 'gpt-6-luna', 'medium')
        self.assertTrue(text.startswith(W.FILE_TOP))
        self.assertIn('name = "switchboard-find"\n', text)
        self.assertIn('model = "gpt-6-luna"\n', text)
        self.assertIn('model_reasoning_effort = "medium"\n', text)
        self.assertNotIn('model = ', cf.helper_text('find', None, 'low').replace('model_reasoning_effort = ', ''))
        self.assertNotIn('model_reasoning_effort', cf.helper_text('find', 'gpt-6-luna', None))
        if tomllib is not None:
            got = tomllib.loads(text)
            self.assertEqual(sorted(got), ['description', 'developer_instructions', 'model',
                                           'model_reasoning_effort', 'name'])
            self.assertEqual(got['description'], W.HELPER_DESCRIPTION.format(about=W.KIND_ABOUT['find']))


class PlanFiles(Base):
    def files(self, plan):
        return dict((os.path.relpath(p, self.home), t) for p, t in cf.plan_files(plan, self.home, now=NOW))

    def test_one_file_per_kind_whose_codex_choice_names_a_model(self):
        got = self.files(make_plan())
        self.assertEqual(sorted(got), [os.path.join('agents', 'switchboard-find.toml'),
                                       os.path.join('agents', 'switchboard-review.toml')])   # build: Claude first
        got = self.files(make_plan(hand={'on': False, 'write': False, 'max_s': 900}))
        self.assertIn(os.path.join('agents', 'switchboard-build.toml'), got)     # Claude passed: hand is off
        self.assertNotIn('model_reasoning_effort', got[os.path.join('agents', 'switchboard-build.toml')])

    def test_no_file_for_a_choice_codex_cannot_take(self):
        out = make_plan()
        out['agents']['codex']['room'] = 'out'
        self.assertEqual(self.files(out), {})
        neither = make_plan(routes={'find': [{'agent': 'codex', 'model': None, 'effort': None}]})
        self.assertEqual(self.files(neither), {})
        self.assertEqual(self.files(make_plan(made_at=NOW - 1000)), {})
        self.assertEqual(self.files(make_plan(on=False)), {})
        self.assertEqual(self.files(None), {})

    def test_odd_words_are_left_out(self):
        p = make_plan(routes={'find': [{'agent': 'codex', 'model': 'bad model"\n', 'effort': 'medium'}],
                              '../x': [{'agent': 'codex', 'model': 'gpt-6-luna', 'effort': None}],
                              'Big': [{'agent': 'codex', 'model': 'gpt-6-luna', 'effort': None}]})
        got = self.files(p)
        self.assertEqual(sorted(got), [os.path.join('agents', 'switchboard-find.toml')])
        self.assertNotIn('model = ', got[os.path.join('agents', 'switchboard-find.toml')].replace(
            'model_reasoning_effort = ', ''))


class ApplyUndo(Base):
    def test_apply_writes_each_file_and_keeps_its_hash(self):
        got = self.apply(make_plan())
        self.assertEqual(self.names(), ['switchboard-find.toml', 'switchboard-review.toml'])
        self.assertEqual(sorted(os.path.basename(p) for p in got['written']), self.names())
        rec = json.loads(read(self.state).decode())
        own = rec['homes'][os.path.realpath(self.home)]['agents']
        for name in self.names():
            self.assertEqual(own[name], hashlib.sha256(read(os.path.join(self.agents, name))).hexdigest())
        self.assertEqual(os.stat(self.state).st_mode & 0o777, 0o600)
        st = cf.status(codex_home=self.home, state_path=self.state)
        self.assertEqual(sorted((os.path.basename(f['path']), f['state']) for f in st['files']),
                         [('switchboard-find.toml', 'ours'), ('switchboard-review.toml', 'ours')])
        self.assertFalse([n for n in os.listdir(self.agents) if n.startswith('.')])     # no temp file left

    def test_a_second_apply_changes_nothing(self):
        self.apply(make_plan())
        path = os.path.join(self.agents, 'switchboard-find.toml')
        os.utime(path, (1000, 1000))
        got = self.apply(make_plan())
        self.assertEqual((got['written'], got['removed'], got['left']), ([], [], []))
        self.assertEqual(os.stat(path).st_mtime, 1000)

    def test_a_new_plan_rewrites_and_removes_only_its_own(self):
        self.apply(make_plan())
        q = make_plan(routes={'find': [{'agent': 'codex', 'model': 'gpt-6-luna', 'effort': 'low'}]})
        got = self.apply(q)
        self.assertEqual(self.names(), ['switchboard-find.toml'])
        text = read(os.path.join(self.agents, 'switchboard-find.toml')).decode()
        self.assertIn('model_reasoning_effort = "low"', text)
        self.assertEqual([os.path.basename(p) for p in got['removed']], ['switchboard-review.toml'])

    def test_a_missing_or_old_plan_leaves_the_files_only_off_removes_them(self):
        # A plan past its time (the Mac slept, say) or no plan at all is not the person turning routing off: the
        # helper files an open Codex chat was told to use stay until a plan says otherwise.
        self.apply(make_plan())
        for plan in (None, make_plan(made_at=NOW - 1000), make_plan(v=2), 'not a plan'):
            with self.subTest(plan=plan if not isinstance(plan, dict) else (plan['v'], plan['made_at'])):
                got = self.apply(plan)
                self.assertEqual((got['written'], got['removed']), ([], []))
                self.assertEqual(self.names(), ['switchboard-find.toml', 'switchboard-review.toml'])
        got = self.apply(make_plan(on=False))
        self.assertEqual(sorted(os.path.basename(p) for p in got['removed']),
                         ['switchboard-find.toml', 'switchboard-review.toml'])
        self.assertEqual(self.names(), [])

    def test_a_file_of_the_persons_own_with_our_name_is_left_alone(self):
        mine = os.path.join(self.agents, 'switchboard-find.toml')
        write(mine, 'name = "my own"\n')
        got = self.apply(make_plan())
        self.assertEqual(read(mine), b'name = "my own"\n')
        self.assertEqual(got['left'], [W.NOT_OURS.format(path=mine)])
        self.undo()
        self.assertEqual(read(mine), b'name = "my own"\n')
        self.assertEqual(self.names(), ['switchboard-find.toml'])

    def test_a_file_changed_after_it_was_written_is_left_alone(self):
        self.apply(make_plan())
        path = os.path.join(self.agents, 'switchboard-find.toml')
        write(path, read(path) + b'# mine now\n')
        got = self.apply(make_plan(routes={}))
        self.assertEqual(got['left'], [W.CHANGED_SINCE.format(path=path)])
        self.assertTrue(read(path).endswith(b'# mine now\n'))
        self.assertEqual(self.names(), ['switchboard-find.toml'])
        self.apply(make_plan())                              # no longer ours: never written over
        self.assertTrue(read(path).endswith(b'# mine now\n'))

    def test_undo_takes_back_exactly_what_apply_wrote(self):
        self.apply(make_plan())
        got = self.undo()
        self.assertEqual(sorted(os.path.basename(p) for p in got['removed']),
                         ['switchboard-find.toml', 'switchboard-review.toml'])
        self.assertFalse(os.path.exists(self.agents))        # Switchboard made the folder: it goes when empty
        self.assertEqual(self.undo()['removed'], [])
        write(os.path.join(self.agents, 'theirs.toml'), 'name = "theirs"\n')
        self.apply(make_plan())
        path = os.path.join(self.agents, 'switchboard-review.toml')
        write(path, 'name = "edited"\n')
        got = self.undo()
        self.assertEqual(got['left'], [W.CHANGED_SINCE.format(path=path)])
        self.assertEqual(self.names(), ['switchboard-review.toml', 'theirs.toml'])

    def test_a_record_that_cannot_be_read_changes_nothing(self):
        write(self.state, 'not json')
        got = self.apply(make_plan())
        self.assertEqual(got['message'], W.BAD_RECORD.format(path=self.state))
        self.assertEqual(self.names(), [])
        self.assertEqual(self.undo()['message'], W.BAD_RECORD.format(path=self.state))

    def test_no_codex_folder_no_files(self):
        shutil.rmtree(self.home)
        self.assertEqual(self.apply(make_plan())['message'], W.NO_CODEX_HOME.format(path=self.home))
        got = cf.hooks_apply(codex_home=self.home, state_path=self.state)
        self.assertEqual((got['changed'], got['message']), (False, W.NO_CODEX_HOME.format(path=self.home)))
        self.assertFalse(os.path.exists(self.home))

    def test_a_trial_plan_writes_nothing(self):
        got = self.apply(make_plan(dry=True))
        self.assertTrue(got['dry'])
        self.assertEqual(got['message'], W.DRY_PLAN)
        self.assertEqual(sorted(got['would']), sorted(W.WOULD_WRITE.format(path=os.path.join(self.agents, n))
                                                      for n in ('switchboard-find.toml', 'switchboard-review.toml')))
        self.assertEqual(self.names(), [])
        self.assertFalse(os.path.exists(self.state))


ORIGINAL = '''{
    "hooks": {
        "PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "/usr/local/bin/check-bash", "timeout": 30}]}
        ],
        "SessionStart": [
            {
                "hooks": [
                    {"type": "command", "command": "echo hello-from-mine"}
                ]
            }
        ]
    },
    "other": [1, 2.50, "caf\\u00e9"]
}
'''


class Hooks(Base):
    def setUp(self):
        Base.setUp(self)
        self.path = os.path.join(self.home, 'hooks.json')
        self.config = os.path.join(self.home, 'config.toml')
        write(self.config, 'model = "gpt-6.1-sol"\n')
        os.utime(self.config, (1000, 1000))
        self.cmd = hookline.command('codex', python='/usr/bin/python3')

    def tearDown(self):
        self.assertEqual(read(self.config), b'model = "gpt-6.1-sol"\n')        # never touched
        self.assertEqual(os.stat(self.config).st_mtime, 1000)

    def on(self):
        return cf.hooks_apply(codex_home=self.home, state_path=self.state, command_line=self.cmd)

    def off(self):
        return cf.hooks_undo(codex_home=self.home, state_path=self.state)

    def ours(self, obj):
        return dict((e, [h for g in obj['hooks'].get(e, []) for h in g['hooks'] if h['command'] == self.cmd])
                    for e in ('SessionStart', 'UserPromptSubmit'))

    def test_a_new_file(self):
        got = self.on()
        self.assertEqual(got['message'], W.HOOKS_ON.format(path=self.path))
        self.assertEqual(got['trust'], W.TRUST)
        obj = json.loads(read(self.path).decode())
        want = {'type': 'command', 'command': self.cmd, 'timeout': 5}
        self.assertEqual(obj, {'hooks': {'SessionStart': [{'hooks': [want]}], 'UserPromptSubmit': [{'hooks': [want]}]}})
        self.assertTrue(self.cmd.endswith(hookline.MARK))
        self.assertEqual(self.off()['message'], W.HOOKS_OFF.format(path=self.path))
        self.assertFalse(os.path.exists(self.path))          # Switchboard made the file: it goes

    def test_what_was_there_is_kept_byte_for_byte(self):
        write(self.path, ORIGINAL)
        self.on()
        after = read(self.path).decode()
        ops = set(op for op, *_ in difflib.SequenceMatcher(None, ORIGINAL, after, autojunk=False).get_opcodes())
        self.assertEqual(ops, {'equal', 'insert'})            # nothing of the person's changed or went
        obj = json.loads(after)
        self.assertEqual(obj['other'], [1, 2.5, 'caf\u00e9'])
        self.assertEqual(obj['hooks']['PreToolUse'], json.loads(ORIGINAL)['hooks']['PreToolUse'])
        self.assertEqual(obj['hooks']['SessionStart'][0], json.loads(ORIGINAL)['hooks']['SessionStart'][0])
        self.assertEqual(self.ours(obj), {'SessionStart': [{'type': 'command', 'command': self.cmd, 'timeout': 5}],
                                          'UserPromptSubmit': [{'type': 'command', 'command': self.cmd,
                                                                'timeout': 5}]})
        self.off()
        self.assertEqual(read(self.path).decode(), ORIGINAL)

    def test_a_second_apply_changes_nothing(self):
        write(self.path, ORIGINAL)
        self.on()
        os.utime(self.path, (2000, 2000))
        once = read(self.path)
        got = self.on()
        self.assertEqual(got['message'], W.HOOKS_ALREADY.format(path=self.path))
        self.assertFalse(got['changed'])
        self.assertEqual(read(self.path), once)
        self.assertEqual(os.stat(self.path).st_mtime, 2000)

    def test_a_moved_python_is_brought_up_to_date_in_place(self):
        write(self.path, ORIGINAL)
        self.on()
        self.cmd = hookline.command('codex', python='/usr/local/bin/python3.14')
        self.assertTrue(self.on()['changed'])
        obj = json.loads(read(self.path).decode())
        self.assertEqual(len(self.ours(obj)['SessionStart']), 1)
        self.assertEqual(len([h for g in obj['hooks']['SessionStart'] for h in g['hooks']
                              if h['command'].endswith(hookline.MARK)]), 1)
        self.off()
        self.assertEqual(read(self.path).decode(), ORIGINAL)

    def test_a_file_that_is_not_json_is_left_alone(self):
        for text in ('{"hooks": ', '{"hooks": {"SessionStart": [}', '\ufeff{}'):
            write(self.path, text)
            got = self.on()
            with self.subTest(text=text):
                self.assertFalse(got['changed'])
                self.assertEqual(got['message'], W.HOOKS_NOT_JSON.format(path=self.path))
                self.assertEqual(read(self.path).decode('utf-8'), text)
        for text in ('[]', '{"hooks": []}', '{"hooks": {"SessionStart": {}}}', '{"hooks": {}, "hooks": {}}'):
            write(self.path, text)
            got = self.on()
            with self.subTest(text=text):
                self.assertEqual(got['message'], W.HOOKS_SHAPE.format(path=self.path))
                self.assertEqual(read(self.path).decode(), text)

    def test_undo_after_the_person_edited_the_file(self):
        write(self.path, ORIGINAL)
        self.on()
        obj = json.loads(read(self.path).decode())
        obj['hooks']['Stop'] = [{'hooks': [{'type': 'command', 'command': 'echo stop'}]}]
        obj['hooks']['UserPromptSubmit'][0]['hooks'].append({'type': 'command', 'command': 'echo also-mine'})
        obj['added'] = True
        write(self.path, json.dumps(obj, indent=1))
        got = self.off()
        self.assertTrue(got['changed'])
        left = json.loads(read(self.path).decode())
        want = copy.deepcopy(obj)
        want['hooks']['SessionStart'] = [g for g in want['hooks']['SessionStart']
                                         if not any(h['command'] == self.cmd for h in g['hooks'])]
        want['hooks']['UserPromptSubmit'][0]['hooks'] = [{'type': 'command', 'command': 'echo also-mine'}]
        self.assertEqual(left, want)
        self.assertNotIn(hookline.MARK, read(self.path).decode())
        self.assertEqual(self.off()['message'], W.HOOKS_NONE.format(path=self.path))

    def test_a_blank_file_comes_back_blank(self):
        write(self.path, '\n')
        self.on()
        self.off()
        self.assertEqual(read(self.path), b'\n')


if __name__ == '__main__':
    unittest.main()
