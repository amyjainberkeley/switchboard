import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Switchboard has no window and no panel of its own: the item in the menu bar and its menu, the page in the browser,
# and the terminal. Nothing of a window, a panel or the stills that drew them is left, and no file of the tree holds
# the program id Switchboard had before its rename, apart from the one-time sweep of an older install that
# tests/test_no_old_ids.py lists place by place.
#
# Made-up accounts only. Nothing is drawn: the menu bar class is made on stand-in AppKit modules.
import io
import os
import queue
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)

from switchboard import menubar  # noqa: E402
import test_menubar as tm  # noqa: E402

OLD_ID = 'io.github.' + 'claude-switchboard'      # put together, so this file never names it whole
SKIP_DIRS = ('.git', '__pycache__')     # git's own files, and what Python writes when it compiles


class TheOldProgramId(unittest.TestCase):
    def test_no_file_of_the_tree_holds_it_but_the_sweep_of_an_older_install(self):
        import test_no_old_ids                  # the places the sweep needs it: the installer, the uninstaller, ...
        held = []
        for top, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in files:
                path = os.path.join(top, name)
                if name == '.git' or os.path.samefile(path, os.path.abspath(__file__)):
                    continue                    # a worktree's .git is a file; this test names the id it looks for
                with open(path, 'rb') as f:
                    if OLD_ID.encode() in f.read():
                        held.append(os.path.relpath(path, ROOT).replace(os.sep, '/'))
        self.assertEqual(sorted(set(held) - set(test_no_old_ids.ALLOWED)), [])


class TheStills(unittest.TestCase):
    def test_gone(self):
        self.assertFalse(os.path.exists(os.path.join(ROOT, 'switchboard', 'stills.py')))
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(menubar.main(['--stills']), 2)
        self.assertEqual(out.getvalue(), menubar.USAGE + '\n')


class TheWindowAndThePanel(unittest.TestCase):
    def test_nothing_of_them_is_left_in_the_module(self):
        for name in ('window_ops', 'window_row_ops', 'window_act', 'offer_ops', 'answer_ops', 'text_width', 'wrap',
                     'chat_mark', 'period_key', 'controls', 'ask_window', 'window_asked', 'window_path',
                     'WINDOW_NOTE', 'ASKED_FOR', 'READING', 'WIN_W', 'PANEL_W', 'ANSWER_FOR', 'EVERYTHING_ELSE'):
            with self.subTest(name=name):
                self.assertFalse(hasattr(menubar, name))

    def test_nor_in_the_menu_bar_class(self):
        p = mock.patch.dict(sys.modules, tm.stand_in_pyobjc(queue.Queue()))
        p.start()
        self.addCleanup(p.stop)
        menubar._UI.clear()
        self.addCleanup(menubar._UI.clear)
        ui = menubar._ui()
        self.assertEqual(sorted(ui), ['MenuBar', 'Row', 'colours', 'is_dark', 'item_image', 'paint', 'paint_item', 'run',
                                      'window_facts'])      # (window_facts: the menu bar item's own place, not a window)
        for name in ('show_window', 'draw_window', 'windowWillClose_', 'windowAsked_', 'offer_now', 'show_panel',
                     'hide_panel', 'show_answer', 'pressed', 'window_offer', 'page_'):
            with self.subTest(name=name):
                self.assertFalse(hasattr(ui['MenuBar'], name))


if __name__ == '__main__':
    unittest.main()
