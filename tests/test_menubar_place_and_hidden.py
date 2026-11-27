import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Where the menu bar item is put the first time, and the one notice when a full menu bar hides it. The rules are plain
# Python (menubar_keep); AppKit only reads and writes the item's saved position and looks at its window, and neither is
# run here: macOS's preferences and windows are stand-ins.
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import core, menubar, menubar_keep, notify

KEY = 'NSStatusItem Preferred Position ' + menubar.AGENT


class Defaults(object):
    """The app's own preferences, as NSUserDefaults has them."""

    def __init__(self, saved=None):
        self.saved = dict(saved or {})
        self.writes = []

    def get(self, key):
        return self.saved.get(key)

    def put(self, key, value):
        self.writes.append((key, value))
        self.saved[key] = value


class NextToTheClock(unittest.TestCase):
    def setUp(self):
        self.settings = {}
        p = mock.patch.object(core, 'settings', lambda: dict(self.settings))
        p.start()
        self.addCleanup(p.stop)

    def test_the_first_time_its_position_is_asked_for_beside_the_clock(self):
        d = Defaults()
        self.assertEqual(menubar_keep.place(d.get, d.put), menubar_keep.NEXT_TO_CLOCK)
        self.assertEqual(d.writes, [(KEY, menubar_keep.NEXT_TO_CLOCK)])
        self.assertEqual(menubar_keep.position_key(menubar.AGENT), KEY)

    def test_a_position_already_saved_is_never_moved(self):
        for saved in (412.0, 0.0, 1):                   # the person placed it, or macOS saved where it was
            d = Defaults({KEY: saved})
            self.assertIsNone(menubar_keep.place(d.get, d.put))
            self.assertEqual(d.writes, [])

    def test_the_setting_turns_it_off(self):
        self.settings['menubar_position'] = False
        d = Defaults()
        self.assertIsNone(menubar_keep.place(d.get, d.put))
        self.assertEqual(d.writes, [])
        self.settings['menubar_position'] = True
        self.assertIsNotNone(menubar_keep.place(d.get, d.put))

    def test_when_the_preferences_cannot_be_read_or_written_the_item_is_made_as_before(self):
        def broken(key, value=None):
            raise RuntimeError('no preferences')
        self.assertIsNone(menubar_keep.place(broken, broken))
        d = Defaults()
        self.assertIsNone(menubar_keep.place(d.get, broken))

    def test_it_is_a_setting_config_knows(self):
        from switchboard import config
        row = config.BY_KEY['menubar_position']
        self.assertEqual((row.kind, row.default, row.file_key, row.group),
                         ('on-off', True, 'menubar_position', 'install'))


def facts(frame, visible=True, screens=None, removed=False):
    """A status item's window and the screens, in AppKit's screen points (y up): one 1512 x 982 screen with a notch
    from x 662 to 850 at the top and its menu bar shown, as a 14-inch MacBook Pro's."""
    if screens is None:
        screens = [{'frame': (0.0, 0.0, 1512.0, 982.0), 'notch': (662.0, 850.0), 'menu_bar': True}]
    return {'removed': removed, 'visible': visible, 'frame': frame, 'screens': screens}


class Hidden(unittest.TestCase):
    def test_on_screen_beside_the_clock_is_shown(self):
        self.assertFalse(menubar_keep.hidden(facts((1300.0, 950.0, 28.0, 32.0))))

    def test_behind_the_notch_is_hidden(self):
        self.assertTrue(menubar_keep.hidden(facts((700.0, 950.0, 28.0, 32.0))))
        self.assertTrue(menubar_keep.hidden(facts((650.0, 950.0, 28.0, 32.0))))       # partly under it

    def test_off_every_screen_or_not_shown_is_not_known(self):
        # macOS also leaves the window unshown or off the screen when nothing is full: a full-screen app, a menu bar
        # set to hide by itself. The notice says "the menu bar is full", so only the notch counts.
        self.assertIsNone(menubar_keep.hidden(facts((-200.0, 950.0, 28.0, 32.0))))
        self.assertIsNone(menubar_keep.hidden(facts((1300.0, 982.0, 28.0, 32.0))))          # above the screen's top
        self.assertIsNone(menubar_keep.hidden(facts((1300.0, 950.0, 28.0, 32.0), visible=False)))
        self.assertIsNone(menubar_keep.hidden({'visible': True, 'frame': None, 'screens': []}))

    def test_an_item_the_person_took_off_the_menu_bar_is_not_hidden(self):
        # Command-dragged off the bar, or turned off in System Settings, Menu Bar: macOS keeps it out on purpose.
        self.assertIsNone(menubar_keep.hidden(facts((700.0, 950.0, 28.0, 32.0), removed=True)))
        self.assertIsNone(menubar_keep.hidden(facts((700.0, 950.0, 28.0, 32.0), visible=False, removed=True)))

    def test_a_screen_whose_menu_bar_is_not_shown_says_nothing(self):
        # a full-screen app or a menu bar that hides by itself: its frame says nothing about a full menu bar
        away = [{'frame': (0.0, 0.0, 1512.0, 982.0), 'notch': (662.0, 850.0), 'menu_bar': False}]
        self.assertIsNone(menubar_keep.hidden(facts((700.0, 950.0, 28.0, 32.0), screens=away)))
        self.assertIsNone(menubar_keep.hidden(facts((1300.0, 950.0, 28.0, 32.0), screens=away)))

    def test_a_screen_with_no_notch(self):
        flat = [{'frame': (0.0, 0.0, 1920.0, 1080.0), 'notch': None}]
        self.assertFalse(menubar_keep.hidden(facts((700.0, 1056.0, 28.0, 24.0), screens=flat)))

    def test_a_second_screen(self):
        two = [{'frame': (0.0, 0.0, 1512.0, 982.0), 'notch': (662.0, 850.0)},
               {'frame': (1512.0, 0.0, 1920.0, 1080.0), 'notch': None}]
        self.assertFalse(menubar_keep.hidden(facts((2200.0, 1056.0, 28.0, 24.0), screens=two)))

    def test_nothing_known_is_not_hidden(self):
        self.assertIsNone(menubar_keep.hidden(None))


class Clock(object):
    def __init__(self):
        self.mono = 5000.0

    def tick(self, seconds):
        self.mono += seconds


class TheNotice(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.on = [True]
        self.clock = Clock()
        self.notice = self.make('login-1')

    def make(self, login):
        return menubar_keep.HiddenNotice(send=lambda n: self.sent.append(n) or True, notes_on=lambda: self.on[0],
                                         login=lambda: login)

    def look(self, hidden, seconds, notice=None):
        out = []
        for _ in range(int(seconds // 15)):
            out.append((notice or self.notice).look(hidden, self.clock.mono))
            self.clock.tick(15)
        return [x for x in out if x]

    def test_the_words(self):
        self.assertEqual(menubar_keep.HIDDEN,
                         "Switchboard's menu bar item is behind the notch because the menu bar is full. Click the "
                         "desktop so Finder is in front; when the S shows, hold Command and drag it toward the clock. "
                         "Your accounts are also in the terminal: switchboard")

    def test_hidden_for_more_than_a_minute_says_so_once(self):
        self.assertEqual(self.look(True, 60), [])                         # 0, 15, 30, 45 s: not yet a minute
        self.assertEqual(len(self.look(True, 30)), 1)
        self.assertEqual(self.look(True, 3600), [])
        self.assertEqual([(n.key, n.title, n.body) for n in self.sent],
                         [('menubar-hidden', 'Switchboard', menubar_keep.HIDDEN)])

    def test_shown_again_starts_the_minute_over(self):
        self.look(True, 45)
        self.look(False, 15)
        self.assertEqual(self.look(True, 60), [])
        self.assertEqual(self.sent, [])

    def test_once_per_login_even_when_it_comes_and_goes(self):
        self.look(True, 90)
        self.look(False, 600)
        self.look(True, 600)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0].instance, 'login login-1')
        later = self.make('login-2')                                       # the next login: it may say so again
        self.look(True, 90, later)
        self.assertEqual(len(self.sent), 2)

    def test_never_while_notifications_are_off(self):
        self.on[0] = False
        self.assertEqual(self.look(True, 600), [])
        self.assertEqual(self.sent, [])
        self.on[0] = True                                                  # turned on while it is still hidden
        self.assertEqual(len(self.look(True, 30)), 1)

    def test_not_known_counts_as_neither(self):
        self.look(True, 45)
        self.look(None, 15)
        self.assertEqual(self.look(True, 45), [])

    def test_a_menu_bar_started_again_in_the_same_login_does_not_say_it_twice(self):
        tmp = tempfile.mkdtemp(prefix='switchboard-hidden-')
        self.addCleanup(shutil.rmtree, tmp, True)
        path = os.path.join(tmp, 'notify.json')
        shown = []

        def run(cmd):
            shown.append(cmd[-1])
            return 0
        for _ in range(2):                                                 # two processes, one login
            n = menubar_keep.HiddenNotice(send=lambda note: notify.send(note, run=run, path=path),
                                          notes_on=lambda: True, login=lambda: 'login-1')
            self.look(True, 120, n)
        self.assertEqual(shown, [menubar_keep.HIDDEN])

    def test_the_login_is_read_from_the_mac(self):
        self.assertTrue(menubar_keep.login_id())
        self.assertEqual(menubar_keep.login_id(), menubar_keep.login_id())


if __name__ == '__main__':
    unittest.main()
