import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The session hooks go on by themselves at the first run, like the notification hooks and the
# status line: once, never after the person set them, never where Claude Code runs no hooks, never over the person's
# own hooks; and whatever order the three of Switchboard's pieces come out in, Claude Code's settings file is back to
# its exact bytes (or gone again, when there was none). A throwaway home only.
import itertools
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import core, keep, notify_hooks, server, session_hooks, statusline

THEIRS = (b'{\n  "statusLine": {"type": "command", "command": "my-line"},\n  "hooks": {\n    "SessionStart": [\n'
          b'      {"hooks": [{"type": "command", "command": "echo mine"}]}\n    ],\n    "PreToolUse": [\n'
          b'      {"matcher": "Bash", "hooks": [{"type": "command", "command": "check-bash"}]}\n    ]\n  },\n'
          b'  "model": "opus"\n}\n')
HOOKS_ONLY = (b'{\n  "hooks": {\n    "UserPromptSubmit": [\n      {"hooks": [{"type": "command", "command": "log-prompt"}]}\n'
              b'    ]\n  },\n  "env": {"FOO": "1"}\n}\n')
PIECES = {'status line': lambda: server.statusline_turn(False),
          'notification hooks': lambda: notify_hooks.turn(False),
          'session hooks': lambda: session_hooks.turn(False)}


class Home(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-i2-keep-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.fresh()
        for target, name, value in ((statusline, 'precompile', lambda *a, **k: None),
                                    (notify_hooks, 'precompile', lambda *a, **k: None),
                                    (session_hooks, 'precompile', lambda *a, **k: [])):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = False
        keep.reset()
        self.addCleanup(keep.reset)

    def fresh(self):
        """A new home and data folder (each order starts from nothing of Switchboard's)."""
        n = len(os.listdir(self.tmp))
        home = os.path.join(self.tmp, 'h%d' % n)
        self.claude = os.path.join(home, '.claude')
        os.makedirs(self.claude)
        self.settings = os.path.join(self.claude, 'settings.json')
        p = mock.patch.dict(os.environ, {'HOME': home, 'SWITCHBOARD_HOME': os.path.join(self.tmp, 'd%d' % n),
                                         'CLAUDE_CONFIG_DIR': self.claude})
        p.start()
        self.addCleanup(p.stop)
        for k in ('SWITCHBOARD_STATE', 'SWITCHBOARD_NOTIFY_STATE'):
            os.environ.pop(k, None)

    def write(self, data):
        if data is not None:
            with open(self.settings, 'wb') as f:
                f.write(data)

    def read(self):
        try:
            with open(self.settings, 'rb') as f:
                return f.read()
        except FileNotFoundError:
            return None

    def first_run(self):
        """The page's first run ending: a look that finds an account held after one that found none."""
        keep.reset()
        self.assertFalse(keep.first_run_tick([]))
        keep.first_run_tick([{'email': 'ana@work.example', 'org_id': '', 'unmanaged': False}])


class FirstRun(Home):
    def test_the_session_hooks_go_in_at_the_first_run_beside_the_persons_own(self):
        self.write(THEIRS)
        self.first_run()
        self.assertIs(session_hooks.status()['in'], True)
        self.assertIs(notify_hooks.status()['in'], True)
        now = self.read()
        for mine in (b'echo mine', b'check-bash', b'my-line'):            # theirs stay, every one
            self.assertIn(mine, now)
        self.assertTrue(core.settings().get('session_hooks_by_itself'))
        self.assertIn(keep.SESSION_HOOKS_ON, [e['text'] for e in core.load()['events']])

    def test_once_and_never_after_the_person_set_them(self):
        self.first_run()
        session_hooks.turn(False)
        self.first_run()                                                  # a first run again: they stay out
        self.assertIs(session_hooks.status()['in'], False)
        self.fresh()
        core.set_setting('session_hooks', False)                          # quiet by the person's choice
        self.first_run()
        self.assertIs(session_hooks.status()['in'], False)
        self.fresh()
        keep.session_hooks_by_you(lambda: None)
        self.first_run()
        self.assertIs(session_hooks.status()['in'], False)

    def test_not_where_claude_code_runs_no_hooks_nor_without_its_folder(self):
        self.write(b'{"disableAllHooks": true}\n')
        self.first_run()
        self.assertEqual(self.read(), b'{"disableAllHooks": true}\n')
        self.fresh()
        shutil.rmtree(self.claude)
        self.assertFalse(keep.session_hooks_by_itself())
        self.assertFalse(os.path.exists(self.claude))


class ExactUndo(Home):
    def test_every_order_of_the_three_pieces_puts_the_file_back_exactly(self):
        for start, name in ((HOOKS_ONLY, 'their hooks'), (None, 'no file'), (THEIRS, 'their status line too')):
            for order in itertools.permutations(sorted(PIECES)):
                with self.subTest(start=name, order=order):
                    self.fresh()
                    self.write(start)
                    self.first_run()                                      # in, by themselves
                    self.assertIs(session_hooks.status()['in'], True)
                    self.assertIs(notify_hooks.status()['in'], True)
                    # Switchboard's status line goes in, except over a status line of the person's own
                    self.assertIs(server.statusline_status()['on'], start is not THEIRS)
                    for piece in order:
                        PIECES[piece]()
                    self.assertEqual(self.read(), start)


if __name__ == '__main__':
    unittest.main()
