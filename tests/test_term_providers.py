import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# `switchboard status` (and bare `switchboard`): the group of providers after the accounts, from the state's
# `providers` (providers/view.py), in term.providers_lines.
#
# - Claude and Codex added, Codex out: a blank line, "Providers, in your order", one line each (name, then its state,
#   plan and windows; Claude's says the accounts above), a blank line, the line to type to carry a chat on.
# - Only Claude added and Codex on the Mac: the one line that adds it. Nothing to say: no lines at all.
# - A narrow terminal: a provider's words go on under its name column, never past the width.
# - Colour: the heading bold, the rest plain; nothing coloured off a terminal.
# - show() prints the group after the accounts and their closing line, before the question; a state with no board
#   prints none.
import io
import os
import re
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))

from switchboard import __main__ as cli  # noqa: E402
from switchboard import core, demo, term  # noqa: E402
from switchboard.providers import words  # noqa: E402

NOW = 1795600000.0
D = 86400.0


def board(**kw):
    b = demo.providers(NOW)
    b.update(kw)
    return b


class Lines(unittest.TestCase):
    def test_claude_and_codex_with_codex_out(self):
        got = term.providers_lines(board(), NOW)
        back = board()['providers'][1]['back_at']
        self.assertEqual(got, ['', 'Providers, in your order',
                               '  Claude  has room · the accounts above',
                               '  Codex   is out until %s · ChatGPT Pro · weekly 100%%' % words.when(back, NOW),
                               '',
                               'To carry a chat on in the next one with room, type in its folder: switchboard continue'])

    def test_only_claude_added_and_codex_on_the_mac(self):
        self.assertEqual(term.providers_lines(demo.providers(NOW, codex=False), NOW),
                         ['', 'Codex is on this Mac too: switchboard add codex adds it.'])

    def test_nothing_to_say(self):
        self.assertEqual(term.providers_lines(dict(demo.providers(NOW, codex=False), add=[]), NOW), [])
        self.assertEqual(term.providers_lines(None, NOW), [])
        self.assertEqual(term.providers_lines({'providers': 'damaged'}, NOW), [])

    def test_a_narrow_terminal(self):
        got = term.providers_lines(board(), NOW, width=40)
        self.assertLessEqual(max(len(x) for x in got), 40, got)
        at = got.index('  Codex   is out until %s' % words.when(board()['providers'][1]['back_at'], NOW))
        self.assertEqual(got[at + 1:at + 3], ['          ChatGPT Pro · weekly 100%', ''])
        self.assertIn('switchboard continue', '\n'.join(got[at:]))

    def test_colour(self):
        got = term.providers_lines(board(), NOW, colour=True)
        self.assertEqual(got[1], '\x1b[1mProviders\x1b[0m\x1b[2m, in your order\x1b[0m')
        self.assertEqual(set(re.findall(r'\x1b\[([0-9;]*)m', '\n'.join(got))), {'0', '1', '2'})
        self.assertNotIn('\x1b', '\n'.join(term.providers_lines(board(), NOW)))


class InTheView(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = True
        demo.STATE.clear()
        demo.FACTS.clear()
        self.addCleanup(demo.STATE.clear)
        self.addCleanup(demo.FACTS.clear)

    def shown(self, view):
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(cli, 'identity', lambda: None):
            self.assertEqual(cli.show(view), 0)
        return out.getvalue().splitlines()

    def test_after_the_accounts_and_their_closing_line(self):
        got = self.shown(demo.view())
        start = got.index('Providers, in your order')
        self.assertEqual(got[start - 1], '')
        last_account = max(i for i, x in enumerate(got) if '@' in x and i < start)
        self.assertLess(last_account, start)
        self.assertTrue(got[start + 1].startswith('  Claude  has room'))
        self.assertEqual(got[-1], 'To carry a chat on in the next one with room, type in its folder: '
                                  'switchboard continue')

    def test_a_state_with_no_board_prints_none(self):
        v = demo.view()
        v.pop('providers')
        self.assertNotIn('Providers, in your order', self.shown(v))


if __name__ == '__main__':
    unittest.main()
