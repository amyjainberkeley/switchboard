import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# The menu bar: what the provider rows do not say, under them (menubar.provider_items), from the state's `providers`
# (the page's board, through providers.view.under_list).
#
# - A provider with a row says everything there: no line of its own here, and no line to carry a chat on.
# - An added provider that cannot be continued to has no row: one line, its name and why ("Codex: not signed in").
# - Only Claude added and Codex on the Mac: the line that adds it. Nothing to say, or no board: no items at all.
# - The line to carry a chat on only when there is no provider row at all.
# - Every item is a 'line' (a menu item that does nothing when clicked): the menu starts nothing from them.
# - menu() puts them after the provider rows and before Auto-switch.
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))

from switchboard import core, demo, menubar  # noqa: E402
from switchboard.providers import view, words  # noqa: E402

NOW = 1795600000.0
ADD = ('line', 'Codex is on this Mac too: switchboard add codex adds it.')


def signed_out(now):
    """The demo's board with Codex added and signed out, as providers/view.py builds it: it cannot be continued to, so
    it has no row, and with one provider left to start on a note there is nowhere to carry a chat on."""
    b = demo.providers(now)
    codex = dict(b['providers'][1], signed_in=False, state=view.SIGNED_OUT, why=words.STATE_SIGNED_OUT, email=None,
                 seats=[], fit_why=words.STATE_SIGNED_OUT)
    entries = [b['providers'][0], codex]
    return dict(b, providers=entries, carry=view._carry(entries))


class Items(unittest.TestCase):
    def test_a_provider_with_a_row_has_no_line_here(self):
        for out in (False, True):
            with self.subTest(out=out):
                board, rows = demo.providers(NOW, out=out), demo.provider_rows(NOW, out=out)
                self.assertEqual(len(rows), 1)
                self.assertEqual(menubar.provider_items(board, NOW, rows), [])

    def test_an_added_provider_with_no_row_says_why(self):
        self.assertEqual(menubar.provider_items(signed_out(NOW), NOW, []),
                         [('separator',), ('line', 'Codex: not signed in')])

    def test_the_line_to_carry_a_chat_on_only_with_no_provider_row(self):
        self.assertEqual(menubar.provider_items(demo.providers(NOW), NOW),
                         [('separator',), ('line', 'To carry a chat on: switchboard continue')])

    def test_only_claude_added_and_codex_on_the_mac(self):
        self.assertEqual(menubar.provider_items(demo.providers(NOW, codex=False), NOW, []), [('separator',), ADD])

    def test_nothing_to_say(self):
        self.assertEqual(menubar.provider_items(dict(demo.providers(NOW, codex=False), add=[]), NOW), [])
        self.assertEqual(menubar.provider_items(None, NOW), [])
        self.assertEqual(menubar.provider_items({'providers': 7}, NOW), [])
        self.assertEqual(menubar.provider_items(demo.providers(NOW), NOW, 'not rows'),
                         menubar.provider_items(demo.providers(NOW), NOW))

    def test_every_item_is_a_line(self):
        for b in (demo.providers(NOW), demo.providers(NOW, codex=False), signed_out(NOW)):
            self.assertEqual({x[0] for x in menubar.provider_items(b, NOW)}, {'separator', 'line'})


class InTheMenu(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = True
        demo.STATE.clear()
        demo.FACTS.clear()
        self.addCleanup(demo.STATE.clear)
        self.addCleanup(demo.FACTS.clear)

    def lines(self, items):
        return [x for x in items if x[0] == 'line']

    def test_the_demo_draws_codex_once_as_its_row(self):
        items = menubar.menu(demo.view())
        got = [x[1] for x in items if x[0] == 'row' and x[1].get('provider')]
        self.assertEqual([(r['email'], r['plan']) for r in got], [('maya@northwind.example', 'Plus')])
        self.assertEqual(self.lines(items), [('line', 'Claude'), ('line', 'OpenAI')])     # the headings, nothing else

    def test_codex_signed_out_one_line_after_the_rows_and_before_auto_switch(self):
        demo.FACTS['providers'] = signed_out(NOW)
        items = menubar.menu(demo.view())
        self.assertFalse([x for x in items if x[0] == 'row' and x[1].get('provider')])
        rows = [i for i, x in enumerate(items) if x[0] == 'row']
        codex = items.index(('line', 'Codex: not signed in'))
        auto = next(i for i, x in enumerate(items) if x[0] == 'auto')
        self.assertLess(max(rows), codex)
        self.assertLess(codex, auto)
        self.assertEqual(items[codex - 1], ('separator',))
        self.assertEqual(self.lines(items), [('line', 'Codex: not signed in')])            # no heading, no carry line

    def test_codex_on_the_mac_and_not_added_the_line_that_adds_it(self):
        demo.FACTS['providers'] = demo.providers(NOW, codex=False)
        items = menubar.menu(demo.view())
        self.assertEqual(self.lines(items), [ADD])

    def test_a_state_with_no_board_has_no_such_line(self):
        v = demo.view()
        v.pop('providers')
        self.assertFalse([x for x in menubar.menu(v) if x[0] == 'line' and 'switchboard' in x[1]])


if __name__ == '__main__':
    unittest.main()
