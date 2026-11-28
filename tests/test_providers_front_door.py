import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""One person's story through the front door (switchboard routes, route, do), on the real modules of every part.

The made-up Mac is the flow story's own (test_providers_flow.World: a home, a site folder, Claude Code and Codex as
stand-in programs on a made-up PATH, Codex's saved numbers from a chat in another folder), and every command runs
through `cli.main(args, kit)` with `cli.default_kit(reach)`: the real adapters, kinds, routes, list of models, pick,
choices, task file, ledger and History. Exec is recorded, never done; each recorded argument list is then run on the
stand-in of that app, which takes only what its version's help has.

The steps run in order (test_01 to test_10), each building on what the one before left. The task's own words carry a
made-up canary and a made-up key: the last step proves the canary is in the task files and nowhere else Switchboard
wrote, and the key is in none. Every name, path, word and key here is made up.
"""
import json
import os
import re
import stat
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))

import test_providers_flow as flow  # noqa: E402  (its made-up Mac; its own story is not run from here)
from fakes_providers import run_real  # noqa: E402
from test_providers_claude_adapter import FLAGS_309  # noqa: E402
from switchboard import history  # noqa: E402
from switchboard.providers import contract, kinds, words  # noqa: E402

CANARY = 'QZFRONTDOORQZ'
SECRET = 'ghp_fake_made_up_0123456789abcdef'


def rows(order, **set_):
    return ['  %-7s %s' % (k, set_.get(k, 'follows your order (%s)' % order)) for k in kinds.KINDS]


END = ('Presets: by-the-job. switchboard routes preset NAME turns one on, and switchboard routes preset off turns it '
       'off.')


class FrontDoor(unittest.TestCase):
    """The ten steps, in order: each builds on the state the one before left."""

    @classmethod
    def setUpClass(cls):
        cls.w = flow.World()

    @classmethod
    def tearDownClass(cls):
        cls.w.close()

    def task_files(self):
        out = []
        for folder, _, names in os.walk(self.w.notes):
            out += [os.path.join(folder, n) for n in names if re.match(r'^h-[0-9a-f]{8}\.md\Z', n)]
        return sorted(out)

    def ledger(self):
        with open(os.path.join(self.w.data, contract.LEDGER_FILE)) as f:
            return json.load(f)

    # 1. ------------------------------------------------------------------------------------------------------------
    def test_01_a_fresh_install_has_no_routes(self):
        code, text = self.w.sb('routes')
        self.assertEqual(code, 0, text)
        self.assertEqual(text.splitlines(), ['Routes, kind by kind (no preset is on):'] + rows('Claude') + [END])
        self.assertFalse(os.path.exists(os.path.join(self.w.data, 'providers', 'routes.json')))

    # 2. ------------------------------------------------------------------------------------------------------------
    def test_02_route_with_claude_alone(self):
        code, text = self.w.sb('route', 'review the footer changes')
        self.assertEqual(code, 0, text)
        self.assertEqual(text.splitlines(), [
            'This reads as a review, from the word "review". It goes to Claude: no provider is known to have room, '
            "and Claude's numbers are not known.",
            'Claude starts on its own default model.'])

    # 3. ------------------------------------------------------------------------------------------------------------
    def test_03_add_codex_and_set_a_route_for_reviews(self):
        w = self.w
        self.assertEqual(w.sb('add', 'codex', tty=True)[0], 0)
        code, text = w.sb('routes', 'set', 'review', 'codex:gpt-6.2-astra:high', 'claude:sonnet')
        self.assertEqual((code, text), (0, 'The route for review is now codex:gpt-6.2-astra:high, then '
                                           'claude:sonnet.\n'))
        path = os.path.join(w.data, 'providers', 'routes.json')
        self.assertEqual(flow.mode(path), 0o600)
        code, text = w.sb('routes')
        self.assertEqual(text.splitlines()[1:-1], rows('Claude, then Codex',
                                                       review='codex:gpt-6.2-astra:high, then claude:sonnet'))

    # 4. ------------------------------------------------------------------------------------------------------------
    def test_04_route_shows_the_pick_and_writes_nothing(self):
        before = self.task_files()
        code, text = self.w.sb('route', '--', 'review the footer changes')
        self.assertEqual(code, 0, text)
        self.assertEqual(text.splitlines(), [
            'This reads as a review, from the word "review". It goes to Codex, the first choice in your routes for '
            'reviews.',
            'It asks Codex for GPT-6.2 Astra at high effort.'])
        self.assertEqual((self.task_files(), self.ledger()['tasks'] if os.path.exists(
            os.path.join(self.w.data, contract.LEDGER_FILE)) else {}), (before, {}))
        self.assertEqual(self.w.calls.execs, [])

    # 5. ------------------------------------------------------------------------------------------------------------
    def test_05_do_from_an_assistants_own_shell_prints_the_line(self):
        code, text = self.w.sb('do', 'review the footer changes', CANARY)
        self.assertEqual(code, 0, text)
        self.assertEqual(text.splitlines(), ['To start this task, run this in a terminal:',
                                             "  switchboard do -- 'review the footer changes %s'" % CANARY])
        self.assertEqual((self.w.calls.execs, self.task_files()), ([], []))

    # 6. ------------------------------------------------------------------------------------------------------------
    def test_06_do_at_a_terminal_asks_once_and_starts_codex_on_the_task(self):
        w = self.w
        code, text = w.sb('do', 'review the footer changes.', CANARY, SECRET, tty=True, answers=['y'])
        self.assertEqual(code, 0, text)
        self.assertEqual(w.term.asked, [words.PROMPT])
        self.assertIn(words.DO_QUESTION.format(to='Codex', vendor='OpenAI', jobs=words.DO_QUESTION_JOBS, work='',
                                               basename='site').rstrip('\n'), text)
        (path,) = self.task_files()
        self.assertEqual(flow.mode(path), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode), 0o700)
        with open(path, encoding='utf-8') as f:
            note = f.read()
        self.assertIn('> review the footer changes. %s [secret removed]' % CANARY, note.splitlines())
        self.assertNotIn(SECRET, note)
        hop = os.path.basename(path)[:-3]
        first = words.TASK_WORDS.format(hop=hop, path=path)
        (exe, argv, env, cwd), = w.calls.execs
        self.assertEqual(argv, (w.codex.path, '-C', w.site, '--model', 'gpt-6.2-astra', '-c',
                                'model_reasoning_effort="high"', first))
        self.assertEqual((exe, cwd), (w.codex.path, w.site))
        self.assertFalse(set(flow.KEYS) & set(env))
        self.assertEqual(text.splitlines()[-2:], [
            'Starting Codex 0.169.1 (your PATH) in this folder.',
            'The task file: %s (%s; 1 place that looked like a secret taken out of your words, best effort).'
            % (path, words.size(os.path.getsize(path)))])
        # the stand-in takes this argument list as Codex 0.169.1's own parser would
        ran = run_real(list(argv), env=env, cwd=cwd, timeout=20.0)
        self.assertEqual(ran.code, 0, ran.err)
        start = [r for r in w.codex.records() if r.get('command') == ''][-1]
        self.assertEqual(start['options'], {'--cd': [w.site], '--model': ['gpt-6.2-astra'],
                                            '--config': ['model_reasoning_effort="high"']})
        self.assertEqual(start['positionals'], [first])
        self.assertEqual(w.codex.forbidden(), [])
        # the ledger's task row, the yes kept, one History line
        (tid, row), = self.ledger()['tasks'].items()
        self.assertEqual(os.path.basename(os.path.dirname(path)), tid)
        self.assertEqual(row['job'], {'kind': 'review', 'by': 'word', 'provider': 'codex', 'model': 'gpt-6.2-astra',
                                      'effort': 'high', 'reason': 'route', 'passed': [], 'file': path,
                                      'bytes': os.path.getsize(path), 'removed': 1, 'state': 'started',
                                      'version': '0.169.1'})
        with open(os.path.join(w.data, contract.SETTINGS_FILE)) as f:
            self.assertEqual(json.load(f)['folders'][w.site]['consent']['codex']['state'], 'yes')
        last = history.read()[-1]
        self.assertEqual((last['kind'], last['text']), ('info', 'Started Codex on a new task in site (a review)'))

    # 7. ------------------------------------------------------------------------------------------------------------
    def test_07_the_preset_on_and_claude_named_for_a_lookup(self):
        w = self.w
        code, text = w.sb('routes', 'preset', 'by-the-job')
        self.assertEqual((code, text), (0, 'The preset by-the-job is on: it fills find and write, tried in your '
                                           'order (Claude, then Codex).\n'))
        code, text = w.sb('do', '--on', 'claude', 'where is the retry logic', CANARY, tty=True, answers=['y'])
        self.assertEqual(code, 0, text)
        self.assertEqual(text.splitlines()[:2], [
            'This reads as a lookup, from the word "where". It goes to Claude, as you named it.',
            'It asks Claude for Haiku at medium effort.'])
        exe, argv, env, cwd = w.calls.execs[-1]
        first = argv[-1]
        path = re.match(r'^Switchboard task (h-[0-9a-f]{8})\. Read the file (.+?) first: ', first).group(2)
        self.assertEqual(first, words.TASK_WORDS.format(hop=os.path.basename(path)[:-3], path=path))
        self.assertIn(path, self.task_files())
        with open(path, encoding='utf-8') as f:
            self.assertIn('> where is the retry logic %s' % CANARY, f.read().splitlines())
        self.assertEqual((exe, cwd), (w.claude, w.site))
        self.assertEqual(argv[:-1], (w.claude, '--model', 'haiku', '--effort', 'medium'))
        self.assertTrue({a for a in argv[1:-1] if a.startswith('-')} <= set(FLAGS_309))     # 2.1.309's help has them
        self.assertEqual(history.read()[-1]['text'], 'Started Claude on a new task in site (a lookup)')

    # 8. ------------------------------------------------------------------------------------------------------------
    def test_08_the_preset_off_puts_back_the_persons_own_route(self):
        w = self.w
        code, text = w.sb('routes', 'preset', 'off')
        self.assertEqual(code, 0, text)
        code, text = w.sb('routes')
        self.assertEqual(text.splitlines(), ['Routes, kind by kind (no preset is on):'] + rows(
            'Claude, then Codex', review='codex:gpt-6.2-astra:high, then claude:sonnet') + [END])

    # 9. ------------------------------------------------------------------------------------------------------------
    def test_09_a_stay_and_a_never_are_honoured(self):
        w = self.w
        self.assertEqual(w.sb('stay', 'claude')[0], 0)
        code, text = w.sb('route', 'review it')
        self.assertEqual(text.splitlines()[0], 'This reads as a review, from the word "review". It goes to Claude: '
                                               "this folder stays on Claude, and Claude's numbers are not known.")
        execs = len(w.calls.execs)
        code, text = w.sb('do', '--on', 'codex', 'review it', tty=True)
        self.assertEqual((code, text.splitlines()[-1]), (1, 'This folder stays on Claude. switchboard stay off lifts '
                                                            'that.'))
        self.assertEqual(w.sb('stay', 'off')[0], 0)
        self.assertEqual(w.sb('allow', 'codex', tty=True, answers=['never'])[0], 0)
        code, text = w.sb('do', '--on', 'codex', 'review it', tty=True)
        self.assertEqual((code, text.splitlines()[-1]), (1, words.NEVER.format(to='Codex', to_id='codex')))
        self.assertEqual(len(w.calls.execs), execs)
        code, text = w.sb('do', 'review it', CANARY, tty=True)            # Codex is passed; Claude takes it
        self.assertEqual(code, 0, text)
        self.assertIn("Codex was passed: this folder's chats never go to it.", text.splitlines())
        self.assertEqual(w.calls.execs[-1][1][:3], (w.claude, '--model', 'sonnet'))

    # 10. -----------------------------------------------------------------------------------------------------------
    def test_10_the_tasks_words_are_in_the_task_files_and_nowhere_else(self):
        w = self.w
        files = self.task_files()
        self.assertEqual(len(files), 3)
        for path in files:
            with open(path, encoding='utf-8') as f:
                self.assertIn(CANARY, f.read())
        for top in (w.data, w.notes):
            for folder, _, names in os.walk(top):
                for name in names:
                    path = os.path.join(folder, name)
                    with open(path, 'rb') as f:
                        data = f.read()
                    with self.subTest(path=path):
                        self.assertNotIn(SECRET.encode(), data)
                        if path not in files:
                            self.assertNotIn(CANARY.encode(), data)
        for _, argv, env, _ in w.calls.execs:
            self.assertNotIn(CANARY, ' '.join(argv) + ' '.join(env.values()))


if __name__ == '__main__':
    unittest.main()
