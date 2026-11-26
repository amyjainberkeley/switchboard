import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# Notifications going on by themselves at a first run race the person's own switch the way auto-switch
# does: the person's choice is marked first, and the two take turns. Fakes only: every settings
# file is a throwaway one (test_notify_hooks.Base). No notification is shown and nothing runs claude.
import os
import sys
import threading
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import core, keep, notify_hooks as nh, server
import test_notify_hooks  # its throwaway Claude Code folder and managed-settings folder


def in_thread(target, *args, **kwargs):
    t = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
    t.start()
    return t


class NotifyChoiceStaysYours(test_notify_hooks.Base):
    """keep.notify_by_itself runs at the end of a first run (the page's tick, `switchboard setup`); the person's on
    or off comes from the page's switch or `switchboard notify on|off` (both server.act). Each test puts the other
    side exactly in the window it races."""

    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = False
        keep.reset()
        self.addCleanup(keep.reset)
        self.patch(server, 'NOTIFY_RUN', self.run_ok)

    def ours(self):
        return self.read().count(nh.MARK) if os.path.exists(self.path) else 0

    def test_off_while_they_go_on_by_themselves_stays_off(self):
        person, real = [], nh.status

        def status(*a, **k):                                   # the first run has looked at the settings: off now
            if not person:
                person.append(in_thread(server.act, '/api/notify', {'on': False}))
                person[0].join(1.0)
            return real(*a, **k)
        self.patch(nh, 'status', status)
        keep.notify_by_itself()
        person[0].join(10)
        self.assertFalse(person[0].is_alive())
        self.assertEqual(self.ours(), 0)                       # the person's off is what holds
        self.assertIs(core.settings().get('notify_set_by_you'), True)
        self.assertFalse(keep.notify_by_itself())
        self.assertEqual(self.ours(), 0)

    def test_a_first_run_between_the_change_and_the_mark_does_not_put_them_back(self):
        nh.turn(True)                                          # in, by an earlier switch: the person turns them off
        self.assertEqual(self.ours(), 3)
        first, real = [], nh.turn

        def turn(on, *a, **k):                                 # the hooks are out: a first run ends now
            said = real(on, *a, **k)
            if not on and not first:
                first.append(in_thread(keep.notify_by_itself))
                first[0].join(1.0)
            return said
        self.patch(nh, 'turn', turn)
        server.act('/api/notify', {'on': False})
        first[0].join(10)
        self.assertFalse(first[0].is_alive())
        self.assertEqual(self.ours(), 0)
        self.assertIs(core.settings().get('notify_set_by_you'), True)

    def test_a_switch_that_fails_is_still_yours(self):
        def refuse(on, *a, **k):
            raise nh.PlanError("Claude Code's settings file can't be written.")
        with mock.patch.object(nh, 'turn', refuse):
            with self.assertRaises(server.SwitchError):
                server.act('/api/notify', {'on': False})
        self.assertIs(core.settings().get('notify_set_by_you'), True)
        self.assertFalse(keep.notify_by_itself())              # off, as the person asked, even though it failed
        self.assertEqual(self.ours(), 0)


if __name__ == '__main__':
    unittest.main()
