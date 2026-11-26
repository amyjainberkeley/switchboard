import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# The menu bar: the other assistants after the account rows (menubar.provider_items), from the state's `providers`.
#
# - Claude and Codex added, Codex out: a separator, one line for Codex (its state, plan and window), and the line to
#   carry a chat on. Claude has no line of its own here: its rows are the account rows just above.
# - Only Claude added and Codex on the Mac: the line that adds it. Nothing to say, or no board: no items at all.
# - Every item is a 'line' (a menu item that does nothing when clicked): the menu starts nothing from them.
# - menu() puts them after the account rows and before Auto-switch.
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))

from switchboard import core, demo, menubar  # noqa: E402
from switchboard.providers import words  # noqa: E402

NOW = 1795600000.0


class Items(unittest.TestCase):
    def test_claude_and_codex_with_codex_out(self):
        b = demo.providers(NOW)
        back = b['providers'][1]['back_at']
        self.assertEqual(menubar.provider_items(b, NOW),
                         [('separator',), ('line', 'Codex: is out until %s · ChatGPT Pro · weekly 100%%'
                                           % words.when(back, NOW)),
                          ('line', 'To carry a chat on: switchboard continue')])

    def test_only_claude_added_and_codex_on_the_mac(self):
        self.assertEqual(menubar.provider_items(demo.providers(NOW, codex=False), NOW),
                         [('separator',), ('line', 'Codex is on this Mac too: switchboard add codex adds it.')])

    def test_nothing_to_say(self):
        self.assertEqual(menubar.provider_items(dict(demo.providers(NOW, codex=False), add=[]), NOW), [])
        self.assertEqual(menubar.provider_items(None, NOW), [])
        self.assertEqual(menubar.provider_items({'providers': 7}, NOW), [])

    def test_every_item_is_a_line(self):
        for b in (demo.providers(NOW), demo.providers(NOW, codex=False)):
            self.assertEqual({x[0] for x in menubar.provider_items(b, NOW)}, {'separator', 'line'})


class InTheMenu(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = True
        demo.STATE.clear()
        demo.FACTS.clear()
        self.addCleanup(demo.STATE.clear)
        self.addCleanup(demo.FACTS.clear)

    def test_after_the_account_rows_and_before_auto_switch(self):
        items = menubar.menu(demo.view())
        rows = [i for i, x in enumerate(items) if x[0] == 'row']
        codex = next(i for i, x in enumerate(items) if x[0] == 'line' and x[1].startswith('Codex: '))
        auto = next(i for i, x in enumerate(items) if x[0] == 'auto')
        self.assertLess(max(rows), codex)
        self.assertLess(codex, auto)
        self.assertEqual(items[codex - 1], ('separator',))
        self.assertEqual(items[codex + 1], ('line', 'To carry a chat on: switchboard continue'))

    def test_a_state_with_no_board_has_no_such_line(self):
        v = demo.view()
        v.pop('providers')
        self.assertFalse([x for x in menubar.menu(v) if x[0] == 'line' and 'switchboard' in x[1]])


if __name__ == '__main__':
    unittest.main()
