import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""Starting an app on a new task (switchboard do): each adapter's start_task, the task file, the ledger's task row.

- start_task only says how (a Launch) and starts nothing. The argument list is exact: the chosen program, Codex's -C
  folder, the model and effort words from the list of models, then the fixed first message (words.TASK_WORDS), which
  names the hop and the task file and holds no word of the task. Any other extra word is refused, so no word that
  loosens an app's sandbox, approvals or permission checks is ever passed. Each argument list, made from the list of
  models for each copied version, is taken by a stand-in that refuses what that version's help lacks.
- The task file is written beside the data folder, under the notes folder (folders 0700, the file 0600), with a fresh
  nonce on its headings; the person's words are quoted, secrets and control characters taken out; the project rule
  files are listed, never opened; "Asked for: high" when an effort could not be given.
- The ledger keeps a task row of how the pick was made: codes, a path, a size and a count, never a word of the task.
Every name, folder, key and word is made up.
"""
import dataclasses
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))

import test_providers_claude_adapter as cta  # noqa: E402  (its made-up Mac and its stand-in of Claude Code 2.1.309)
from fakes_codex import VERSIONS  # noqa: E402
from fakes_codex import Rig as CodexRig  # noqa: E402
from fakes_providers import make_reach, run_real  # noqa: E402
from switchboard import core  # noqa: E402
from switchboard.providers import catalog, codex_facts, contract, handoff, run, tasks, words  # noqa: E402
from switchboard.providers.contract import IN_APP, IN_TERMINAL, PRINT, ProviderError, Unsupported  # noqa: E402

HOP, TASK = 'h-0123abcd', 't-0a0b0c0d'
KEY = 'made-up-key-0123456789'
SECRET = 'ghp_fake_made_up_0123456789abcdef'
NOW = 1795600000.0


def task_file(root):
    folder = os.path.join(root, 'notes', TASK)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    path = os.path.join(folder, HOP + '.md')
    with open(path, 'w') as f:
        f.write('# Switchboard task\n')
    return path


class CodexStart(unittest.TestCase):
    version = '0.169.1'

    def setUp(self):
        self.rig = CodexRig(self, env={'CODEX_API_KEY': KEY, 'MADE_UP_PLAIN': 'kept'})
        self.fake = self.rig.app('ChatGPT.app', self.version)
        self.folder = os.path.join(self.rig.root, 'Users', 'maya', 'my site')
        os.makedirs(self.folder)
        self.path = task_file(self.rig.root)
        self.p = self.rig.provider
        self.first = words.TASK_WORDS.format(hop=HOP, path=self.path)

    def test_the_argument_list_exact(self):
        extra = ('--model', 'gpt-6.2-sol', '-c', 'model_reasoning_effort="high"')
        got = self.p.start_task(self.folder, HOP, self.path, IN_TERMINAL, extra)
        self.assertEqual(got.argv, (self.fake.path, '-C', self.folder) + extra + (self.first,))
        self.assertEqual((got.provider, got.how, got.cwd, got.first_words), ('codex', IN_TERMINAL, self.folder,
                                                                             self.first))
        self.assertNotIn('CODEX_API_KEY', got.env)
        self.assertEqual(got.env.get('MADE_UP_PLAIN'), 'kept')
        self.assertEqual(got.line, run.shell_line(got.argv, self.folder, self.rig.reach.env))
        self.assertIn('env -u CODEX_API_KEY', got.line)
        self.assertEqual(self.rig.calls.execs, [])                      # it only says how
        self.assertEqual([r for r in self.fake.runs() if r != ['--version']], [])

    def test_with_no_extra_words(self):
        got = self.p.start_task(self.folder, HOP, self.path, IN_TERMINAL)
        self.assertEqual(got.argv, (self.fake.path, '-C', self.folder, self.first))

    def test_the_first_message_names_the_file_and_nothing_of_the_task(self):
        self.assertEqual(self.first.count(self.path), 1)
        self.assertTrue(self.first.startswith('Switchboard task %s. ' % HOP))
        self.assertNotIn('{', words.TASK_WORDS.replace('{hop}', '').replace('{path}', ''))

    def test_any_other_word_is_refused(self):
        for extra in (('--dangerously-bypass-approvals-and-sandbox',), ('-s', 'danger-full-access'),
                      ('-a', 'never'), ('-c', 'approval_policy="never"'), ('-c', 'sandbox_mode="danger-full-access"'),
                      ('--model',), ('--model', '--sandbox'), ('--no-daemon', 'x'), ('-m', 'two words'),
                      ('--add-dir', '/'), ('--model', 'gpt-6.2-sol', '--model')):
            with self.subTest(extra=extra):
                with self.assertRaises(ValueError):
                    self.p.start_task(self.folder, HOP, self.path, IN_TERMINAL, extra)

    def test_an_older_program_than_the_one_that_wrote_the_chats_is_refused(self):
        self.rig.chatfiles.newest = '0.170.0'
        with self.assertRaises(ProviderError) as got:
            self.p.start_task(self.folder, HOP, self.path, IN_TERMINAL)
        self.assertEqual(got.exception.kind, 'too_old')

    def test_the_desktop_link_cannot_carry_it(self):
        got = self.p.start_task(self.folder, HOP, self.path, IN_APP)
        self.assertIsInstance(got, Unsupported)
        self.assertEqual((got.what, got.why), (contract.START_APP, codex_facts.NO_APP_TASK))
        self.assertEqual(self.rig.calls.links, [])

    def test_the_line_to_print(self):
        got = self.p.start_task(self.folder, HOP, self.path, PRINT, ('--model', 'gpt-6-luna'))
        self.assertEqual(got.how, PRINT)
        self.assertIn("--model gpt-6-luna", got.line)

    def test_a_relative_folder_or_file_or_another_id(self):
        for args in (('site', HOP, self.path), (self.folder, 'h-xyz', self.path), (self.folder, HOP, 'h.md'),
                     (self.folder, '../x', self.path)):
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    self.p.start_task(*args, how=IN_TERMINAL)


class CodexStandInTakesIt(unittest.TestCase):
    """The argument list for each copied version, its extra words from the list of models for that version, run on a
    stand-in that takes only what that version's help has."""

    def test_each_version(self):
        for version in VERSIONS:
            with self.subTest(version=version):
                case = CodexStart('setUp')
                case.version = version
                case.setUp()
                try:
                    extra = catalog.start_args('codex', 'gpt-6.2-astra', 'high', version)
                    launch = case.p.start_task(case.folder, HOP, case.path, IN_TERMINAL, extra)
                    ran = run_real(list(launch.argv), env=dict(launch.env), cwd=launch.cwd, timeout=20.0)
                    self.assertEqual(ran.code, 0, (version, ran.err))
                    self.assertEqual(case.fake.forbidden(), [])
                    start = [r for r in case.fake.records() if r.get('command') == ''][-1]
                    want = {'--cd': [case.folder], '--model': ['gpt-6.2-astra']}
                    if version == '0.169.1':
                        want['--config'] = ['model_reasoning_effort="high"']
                    self.assertEqual(start['options'], want)
                    self.assertEqual(start['positionals'], [case.first])
                finally:
                    case.doCleanups()


class ClaudeStart(cta.Mac):
    def setUp(self):
        super().setUp()
        self.program = os.path.realpath(self.versions('2.1.330')[0])
        self.path = task_file(self.root)
        self.first = words.TASK_WORDS.format(hop=HOP, path=self.path)

    def test_the_argument_list_exact(self):
        got = self.p.start_task(self.folder, HOP, self.path, IN_TERMINAL, ('--model', 'sonnet', '--effort', 'high'))
        self.assertEqual(got.argv, (self.program, '--model', 'sonnet', '--effort', 'high', self.first))
        self.assertEqual((got.provider, got.how, got.cwd, got.first_words), ('claude', IN_TERMINAL, self.folder,
                                                                             self.first))
        self.assertEqual(self.calls.execs, [])

    def test_with_no_extra_words(self):
        got = self.p.start_task(self.folder, HOP, self.path, IN_TERMINAL)
        self.assertEqual(got.argv, (self.program, self.first))

    def test_any_other_word_is_refused(self):
        for extra in (('--dangerously-skip-permissions',), ('--permission-mode', 'bypassPermissions'), ('-p', 'x'),
                      ('--model',), ('--model', '-p'), ('--resume', 'aaaaaaaa-0000-4000-8000-00000000000a'),
                      ('--effort', 'high', '--print', 'x'), ('--settings', '{}')):
            with self.subTest(extra=extra):
                with self.assertRaises(ValueError):
                    self.p.start_task(self.folder, HOP, self.path, IN_TERMINAL, extra)

    def test_no_desktop_link(self):
        got = self.p.start_task(self.folder, HOP, self.path, IN_APP)
        self.assertIsInstance(got, Unsupported)
        self.assertEqual(got.why, words.NO_CLAUDE_LINK)

    def test_the_stand_in_takes_it(self):          # it refuses an option 2.1.309's help lacks (exit 1); 3: no session
        for version, extra in (('2.1.309', catalog.start_args('claude', 'haiku', 'medium', '2.1.309')),
                               ('2.1.300', catalog.start_args('claude', 'haiku', 'medium', '2.1.300'))):
            with self.subTest(version=version):
                got = self.p.start_task(self.folder, HOP, self.path, IN_TERMINAL, extra)
                ran = run_real(list(got.argv), env=dict(got.env), cwd=got.cwd, timeout=20.0)
                self.assertEqual((ran.code, 'unknown option' in ran.err), (3, False), ran.err)
                self.assertEqual(len(got.argv), 6 if extra else 2)


class TheStandInRefusesWhatTheAdaptersRefuse(unittest.TestCase):
    """tests/fakes_provider_kit.FakeProvider.start_task, which every command test runs on, takes exactly the extra
    words, hop ids, paths and ways the real adapters take."""
    EXTRAS = (('--model', 'gpt-6.2-sol', '-c', 'model_reasoning_effort="high"'), ('-m', 'gpt-6-luna'),
              ('--model', 'sonnet', '--effort', 'high'), ('--effort', 'low'), (),
              ('--dangerously-bypass-approvals-and-sandbox',), ('-s', 'danger-full-access'), ('-a', 'never'),
              ('-c', 'approval_policy="never"'), ('--model',), ('--model', '--sandbox'), ('--no-daemon', 'x'),
              ('--dangerously-skip-permissions',), ('--permission-mode', 'bypassPermissions'), ('-p', 'x'),
              ('--settings', '{}'), ('--add-dir', '/'))

    def setUp(self):
        import fakes_provider_kit as kit
        self.root = os.path.realpath(tempfile.mkdtemp(prefix='sb-fake-start-'))
        self.addCleanup(shutil.rmtree, self.root, True)
        rig = kit.Rig(self.root)
        self.fakes = {'claude': rig.provider('claude', found=kit.claude_found(self.root)),
                      'codex': rig.provider('codex', found=kit.codex_found(self.root))}
        self.codex = CodexStart('setUp')
        self.codex.setUp()
        self.addCleanup(self.codex.doCleanups)
        self.claude = ClaudeStart('setUp')
        self.claude.setUp()
        self.addCleanup(self.claude.doCleanups)
        self.path = task_file(self.root)
        self.folder = os.path.join(self.root, 'site')
        os.makedirs(self.folder, exist_ok=True)

    def refuses(self, start, *args, **kw):
        try:
            got = start(*args, **kw)
        except ValueError:
            return 'refused'
        return 'unsupported' if isinstance(got, Unsupported) else 'taken'

    def test_the_same_extra_words(self):
        for pid, real in (('codex', self.codex), ('claude', self.claude)):
            for extra in self.EXTRAS:
                with self.subTest(provider=pid, extra=extra):
                    want = self.refuses(real.p.start_task, real.folder, HOP, real.path, IN_TERMINAL, extra)
                    got = self.refuses(self.fakes[pid].start_task, self.folder, HOP, self.path, IN_TERMINAL, extra)
                    self.assertEqual(got, want)

    def test_the_same_ids_paths_and_ways(self):
        for pid, real in (('codex', self.codex), ('claude', self.claude)):
            for folder, hop, path, how in (('site', HOP, '/p.md', IN_TERMINAL), (None, 'h-xyz', '/p.md', IN_TERMINAL),
                                           (None, HOP, 'p.md', IN_TERMINAL), (None, HOP, None, IN_APP)):
                with self.subTest(provider=pid, case=(folder, hop, path, how)):
                    want = self.refuses(real.p.start_task, folder or real.folder, hop, path or real.path, how)
                    got = self.refuses(self.fakes[pid].start_task, folder or self.folder, hop, path or self.path, how)
                    self.assertEqual(got, want)
        got = self.fakes['codex'].start_task(self.folder, HOP, self.path, IN_APP)
        self.assertEqual(got.why, codex_facts.NO_APP_TASK)


class TheTaskFile(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix='sb-task-file-'))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.reach, _ = make_reach(self.root, env={'HOME': os.path.join(self.root, 'home'), 'PATH': '/usr/bin:/bin',
                                                   'MADE_UP_TOKEN': 'made-up-value-in-the-shell-0123'})
        self.folder = os.path.join(self.root, 'repo', 'site')
        os.makedirs(os.path.join(self.root, 'repo', '.git'))
        os.makedirs(os.path.join(self.folder, '.claude', 'rules'))
        for rel, text in (('CLAUDE.md', 'made-up rules\n'), ('AGENTS.md', 'more\n'),
                          (os.path.join('.claude', 'rules', 'style.md'), 'style\n')):
            with open(os.path.join(self.folder, rel), 'w') as f:
                f.write(text)
        with open(os.path.join(self.root, 'repo', 'AGENTS.md'), 'w') as f:
            f.write('top\n')
        outside = os.path.join(self.root, 'outside.md')
        with open(outside, 'w') as f:
            f.write('not the project\n')
        os.symlink(outside, os.path.join(self.folder, 'CLAUDE.local.md'))   # a link: never listed

    def write(self, text, asked=()):
        return handoff.write_task(text, hop=HOP, task=TASK, to_name='Codex',
                                  kind_line='This reads as a review (from the word "review").', asked=asked,
                                  folder=self.folder, reach=self.reach)

    def read(self, h):
        with open(h.path, encoding='utf-8') as f:
            return f.read()

    def test_where_and_how_it_is_written(self):
        h = self.write('review the parser')
        notes = run.notes_root(self.reach)
        self.assertEqual(h.path, os.path.join(notes, TASK, HOP + '.md'))
        self.assertEqual(os.path.dirname(notes), os.path.dirname(self.reach.data_dir))        # beside the data
        self.assertEqual(stat.S_IMODE(os.stat(h.path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(h.path)).st_mode), 0o700)
        self.assertEqual((h.hop, h.meta_path, h.bytes), (HOP, '', os.path.getsize(h.path)))
        self.assertEqual(sorted(os.listdir(os.path.dirname(h.path))), [HOP + '.md'])        # no sidecar

    def test_what_it_holds(self):
        h = self.write('review the parser. My key is %s and \x1b[31mred\x1b[0m.\n## Not a heading' % SECRET,
                       asked=('Asked for: high',))
        text = self.read(h)
        lines = text.splitlines()
        self.assertEqual(lines[0], '# Switchboard task · %s' % h.nonce)
        headings = [x for x in lines if x.startswith('## ')]
        self.assertEqual(headings, ['## What the person asked · %s' % h.nonce,
                                    '## The kind of job · %s' % h.nonce,
                                    '## Project rules · %s' % h.nonce])
        self.assertIn('> review the parser. My key is [secret removed] and red.', lines)
        self.assertIn('> ## Not a heading', lines)
        self.assertNotIn(SECRET, text)
        self.assertNotIn('\x1b', text)
        self.assertEqual(dict(h.removed).get('key'), 1)
        self.assertIn('This reads as a review (from the word "review").', lines)
        self.assertIn('Asked for: high', lines)
        self.assertIn('This file holds a task the person typed for Codex with switchboard do.', text)

    def test_the_project_rules_are_listed_never_a_link(self):
        text = self.read(self.write('fix it'))
        for rel, size in (('CLAUDE.md', 14), ('AGENTS.md', 5), (os.path.join('.claude', 'rules', 'style.md'), 6)):
            self.assertIn('- `%s` (%d bytes)' % (os.path.join(self.folder, rel), size), text)
        self.assertIn('- `%s` (4 bytes)' % os.path.join(self.root, 'repo', 'AGENTS.md'), text)   # up to git's top
        self.assertNotIn('CLAUDE.local.md', text)
        self.assertIn("These files hold the person's project rules. Read them before you change anything.", text)

    def test_no_rules_no_section_and_no_asked_line(self):
        bare = os.path.join(self.root, 'bare')
        os.makedirs(bare)
        h = handoff.write_task('fix it', hop=HOP, task=TASK, to_name='Claude', kind_line='You named this a fix.',
                               folder=bare, reach=self.reach)
        text = self.read(h)
        self.assertNotIn('Project rules', text)
        self.assertNotIn('Asked for', text)

    def test_a_value_from_the_shell_is_taken_out_too(self):
        h = self.write('use made-up-value-in-the-shell-0123 to log in')
        self.assertNotIn('made-up-value-in-the-shell-0123', self.read(h))
        self.assertEqual(dict(h.removed).get('known_value'), 1)

    def test_a_folder_that_cannot_be_written(self):
        blocked = os.path.join(self.root, 'blocked')
        with open(blocked, 'w') as f:
            f.write('a file where the notes folder would go\n')
        reach = dataclasses.replace(self.reach, data_dir=os.path.join(blocked, 'data'))
        with self.assertRaises(ProviderError) as got:
            handoff.write_task('fix it', hop=HOP, task=TASK, to_name='Codex', kind_line='x', folder=self.folder,
                               reach=reach)
        self.assertEqual(got.exception.kind, 'disk')

    def test_ids_of_another_shape(self):
        with self.assertRaises(ValueError):
            handoff.write_task('x', hop='../h', task=TASK, to_name='Codex', kind_line='x', folder=self.folder,
                               reach=self.reach)


class TheTaskRow(unittest.TestCase):
    JOB = {'kind': 'review', 'by': 'word', 'provider': 'codex', 'model': 'gpt-6.2-astra', 'effort': 'high',
           'reason': 'route', 'passed': [['claude', 'out']], 'file': '/made-up/notes/t-0a0b0c0d/h-0123abcd.md',
           'bytes': 812, 'removed': 1, 'state': 'started', 'version': '0.169.1'}

    def setUp(self):
        self.path = core.data_path(contract.LEDGER_FILE)
        for p in (self.path,):
            self.addCleanup(lambda p=p: os.path.exists(p) and os.remove(p))
            if os.path.exists(p):
                os.remove(p)

    def saved(self):
        with open(self.path) as f:
            return json.load(f)

    def test_add_then_mark_failed(self):
        self.assertEqual(tasks.add_task(TASK, '/made-up/site', dict(self.JOB), NOW), TASK)
        row = self.saved()['tasks'][TASK]
        self.assertEqual(row, {'id': TASK, 'folder': '/made-up/site', 'made_at': NOW,
                               'origin': {'provider': 'codex', 'chat': None}, 'job': self.JOB})
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        got = tasks.update_task(TASK, state='failed')
        self.assertEqual(got['job']['state'], 'failed')
        self.assertEqual(self.saved()['tasks'][TASK]['job']['state'], 'failed')
        self.assertEqual(self.saved()['hops'], [])

    def test_it_keeps_no_word_of_the_task(self):
        for extra in ({'word': 'review'}, {'task': 'review the parser'}, {'state': 'printed'}):
            with self.subTest(extra=extra):
                with self.assertRaises(ValueError):
                    tasks.add_task(TASK, '/made-up/site', dict(self.JOB, **extra), NOW)
        self.assertFalse(os.path.exists(self.path))

    def test_ids_and_twice(self):
        with self.assertRaises(ValueError):
            tasks.add_task('t-xyz', '/made-up/site', dict(self.JOB), NOW)
        tasks.add_task(TASK, '/made-up/site', dict(self.JOB), NOW)
        with self.assertRaises(ValueError):
            tasks.add_task(TASK, '/made-up/site', dict(self.JOB), NOW)
        with self.assertRaises(ProviderError) as got:
            tasks.update_task('t-0000000f', state='failed')
        self.assertEqual(got.exception.kind, 'not_found')
        with self.assertRaises(ValueError):
            tasks.update_task(TASK, word='review')

    def test_pruned_with_the_notes(self):
        tasks.add_task(TASK, '/made-up/site', dict(self.JOB), NOW)
        tasks.prune(NOW + 86400)
        self.assertIn(TASK, self.saved()['tasks'])
        tasks.prune(NOW + (contract.NOTE_KEEP_DAYS + 1) * 86400)
        self.assertNotIn(TASK, self.saved()['tasks'])


if __name__ == '__main__':
    unittest.main()
