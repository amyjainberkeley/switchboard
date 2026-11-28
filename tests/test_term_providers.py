import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# `switchboard status` (and bare `switchboard`): under the list, only what the numbered rows do not say, from
# view.under_list (providers/view.py), drawn by term.under_list:
#
# - An added provider with no row (not on this Mac, not signed in, sign-in not seen here, older than the one that
#   wrote your chats): one dim line, its name and why.
# - A provider on this Mac that is not added: public's one line that adds it, word for word.
# - The line to carry a chat on: only when there is no provider row at all.
# - A blank line first; nothing at all when there is nothing to say. No heading, no line for Claude (the accounts
#   above are its rows), no line for a provider that has a row. A command to type is never split across two lines.
# - show() prints them after the accounts and their closing line, before the question.
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
from switchboard.providers import rows as provider_rows  # noqa: E402
from switchboard.providers import view, words  # noqa: E402

NOW = 1795600000.0
OFFER = 'Codex is on this Mac too: switchboard add codex adds it.'
CARRY = 'To carry a chat on in the next one with room, type in its folder: switchboard continue'


def drawn(items, width=None, colour=False):
    """term.under_list's lines for what view.under_list gives (here: `items`)."""
    with mock.patch.object(view, 'under_list', lambda board, now, rows=(): list(items), create=True):
        return term.under_list({'v': 1}, NOW, (), width=width, colour=colour)


class Lines(unittest.TestCase):
    def test_an_added_provider_with_no_row_is_one_dim_line(self):
        self.assertEqual(drawn([('provider', 'Codex', 'not signed in')]), ['', '  Codex  not signed in'])
        self.assertEqual(drawn([('provider', 'Codex', 'not signed in')], colour=True),
                         ['', '\x1b[2m  Codex  not signed in\x1b[0m'])

    def test_names_line_up(self):
        self.assertEqual(drawn([('provider', 'Codex', 'not signed in'), ('provider', 'Tern', 'not on this Mac')]),
                         ['', '  Codex  not signed in', '  Tern   not on this Mac'])

    def test_the_offer_and_the_carry_line_are_public_words_plain(self):
        self.assertEqual(drawn([('add', 'Codex', OFFER)]), ['', OFFER])
        self.assertEqual(drawn([('add', 'Codex', OFFER)], colour=True), ['', OFFER])
        self.assertEqual(drawn([('provider', 'Tern', 'not signed in'), ('add', 'Codex', OFFER), ('carry', '', CARRY)]),
                         ['', '  Tern  not signed in', OFFER, CARRY])

    def test_nothing_to_say(self):
        self.assertEqual(drawn([]), [])

    def test_a_narrow_terminal(self):
        got = drawn([('provider', 'Codex', 'older than the one that wrote your chats'), ('add', 'Codex', OFFER),
                     ('carry', '', CARRY)], width=36)
        self.assertLessEqual(max(len(x) for x in got), 36, got)
        self.assertEqual(got[1:3], ['  Codex  older than the one that', '         wrote your chats'])
        self.assertIn('switchboard add codex adds it.', got)        # a command is never split
        self.assertTrue(any(x.endswith('switchboard continue') or x == 'switchboard continue' for x in got), got)


def board(**codex):
    """The demo's board, its Codex changed by `codex`."""
    b = demo.providers(NOW)
    b['providers'][1].update(codex)
    return b


class WithTheBoard(unittest.TestCase):
    def under(self, b, rows=None):
        return term.under_list(b, NOW, provider_rows.from_board(b) if rows is None else rows)

    def test_a_provider_with_a_row_is_not_said_again(self):
        b = board()
        self.assertTrue(provider_rows.from_board(b))
        self.assertEqual(self.under(b), [])

    def test_added_and_signed_out(self):
        b = dict(board(state='signed_out', seats=[], plan=None, fit_why=words.STATE_SIGNED_OUT), carry=False)
        self.assertEqual(provider_rows.from_board(b), [])
        self.assertEqual(self.under(b), ['', '  Codex  not signed in'])

    def test_on_this_mac_and_not_added(self):
        b = demo.providers(NOW, codex=False)
        self.assertEqual(self.under(b), ['', OFFER])

    def test_the_carry_line_only_with_no_row(self):
        b = dict(board(), carry=True)
        self.assertEqual(self.under(b), [])
        self.assertEqual(self.under(b, rows=()), ['', CARRY])

    def test_nothing_to_say(self):
        self.assertEqual(self.under(dict(demo.providers(NOW, codex=False), add=[])), [])
        self.assertEqual(term.under_list(None, NOW), [])
        self.assertEqual(term.under_list({'providers': 'damaged'}, NOW), [])


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

    def test_the_demo_draws_codex_once_as_its_row(self):
        got = self.shown(demo.view())
        self.assertEqual([x for x in got if 'OpenAI' in x or 'Codex' in x],
                         ['6  maya@northwind.example   OpenAI · Plus'])
        self.assertNotIn('Providers, in your order', got)
        self.assertFalse(any(re.match(r'\s+Claude\s', x) for x in got), got)

    def test_after_the_accounts_and_their_closing_line(self):
        v = demo.view()
        v['providers']['providers'][1].update(state='signed_out', seats=[], plan=None, fit_why=words.STATE_SIGNED_OUT)
        v['providers']['carry'] = False         # as the board says it: a signed-out Codex is nowhere to carry to
        v['provider_rows'] = []
        got = self.shown(v)
        self.assertEqual(got[-2:], ['', '  Codex  not signed in'])
        self.assertTrue(got[-3].startswith('   weekly') or got[-3].startswith('   5-hour'), got)

    def test_a_state_with_no_board_prints_nothing_under_the_list(self):
        v = demo.view()
        v.pop('providers')
        v['provider_rows'] = []
        got = self.shown(v)
        self.assertTrue(got[-1].startswith('   weekly'), got)


if __name__ == '__main__':
    unittest.main()
