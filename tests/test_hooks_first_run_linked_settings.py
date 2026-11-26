import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""A first run leaves a linked settings.json alone.

People who keep Claude Code's settings.json in a dotfiles folder have it as a symbolic link. The paths Switchboard
writes there (its Python, its folder) are this Mac's, and the file may go to other Macs, so the status line and hooks
do not go in by themselves: one log line says so, and the file is left byte for byte. Turning a piece on by hand still
works, through the link. A throwaway home only.
  SWITCHBOARD_HOME="$(mktemp -d)/data" python3 -m unittest discover -s tests -p 'test_hooks_first_run_linked_settings.py'"""
import os
import unittest
from unittest import mock

from test_hooks_first_run import Home

from switchboard import core, keep, notify_hooks, session_hooks, statusline

MINE = b'{\n  "model": "opus"\n}\n'


class Linked(Home):
    def link(self):
        dotfiles = os.path.join(os.path.dirname(self.claude), 'dotfiles')
        os.makedirs(dotfiles)
        self.target = os.path.join(dotfiles, 'settings.json')
        with open(self.target, 'wb') as f:
            f.write(MINE)
        os.symlink(self.target, self.settings)

    def test_nothing_goes_in_by_itself_and_one_line_says_so(self):
        self.link()
        logged = []
        with mock.patch.object(core, 'log', lambda text, *a, **k: logged.append(text)):
            self.first_run()
            self.assertFalse(keep.notify_by_itself())
            self.assertIsNone(keep.statusline_by_itself())
            self.assertFalse(keep.session_hooks_by_itself())
        self.assertTrue(os.path.islink(self.settings))
        self.assertEqual(self.read(), MINE)
        said = [x for x in logged if 'is a link' in x]
        self.assertEqual(said, ['%s is a link, so the status line and hooks did not go on by themselves; turning '
                                'them on still works' % self.settings])
        s = core.settings()
        for key in ('notify_by_itself', 'statusline_by_itself', 'session_hooks_by_itself'):
            self.assertFalse(s.get(key), key)

    def test_turning_them_on_by_hand_still_works_through_the_link(self):
        self.link()
        self.first_run()
        notify_hooks.turn(True)
        session_hooks.turn(True)
        statusline.apply(statusline.install_plan(self.settings))
        self.assertTrue(os.path.islink(self.settings))
        for mod in (notify_hooks, session_hooks):
            self.assertIs(mod.status()['in'], True)
        self.assertTrue(statusline.is_ours(statusline._settings_file(self.settings)['obj'].get('statusLine')))

    def test_a_plain_file_still_gets_them_at_the_first_run(self):
        self.write(MINE)
        self.first_run()
        self.assertIs(notify_hooks.status()['in'], True)
        self.assertIs(session_hooks.status()['in'], True)


if __name__ == '__main__':
    unittest.main()
