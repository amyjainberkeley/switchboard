import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# Try routing without changing anything, and put it back (switchboard/router/tryit.py): the page's "Try it" and Undo.
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))
sys.path.insert(0, HERE)

from switchboard.router import plan, settings, tryit, wire  # noqa: E402
from switchboard.router import wire_words as W  # noqa: E402

import fakes_provider_kit as kit  # noqa: E402
from test_providers_words import problems  # noqa: E402
from test_router_wire import NEW, Home, snapshot  # noqa: E402


# These tests hold routing as it is once the hand-over and the one "continue" are in (router/ready.py): both then
# follow "on". tests/test_router_hand_ready.py holds the state this tree ships in.
_SHIPPED_FOLLOW_ON = settings.FOLLOW_ON


def setUpModule():
    settings.FOLLOW_ON = ('hand.on', 'carry_on')


def tearDownModule():
    settings.FOLLOW_ON = _SHIPPED_FOLLOW_ON

NOW = kit.NOW


class TryIt(Home, kit.KitCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        wire._GATHER['thread'] = None
        self.k = self.kit()
        self.where = dict(claude_dir=self.claude, codex_home=self.codex, versions=NEW)

    def test_off_then_try_then_undo_leaves_every_file_as_it_was(self):
        before = snapshot(self.home, times=False)
        data_before = self.outside_router()
        got = tryit.on(kit=self.k, now=NOW, **self.where)
        self.assertEqual(got[0], W.ON_TRIAL)
        self.assertLessEqual(len(got), tryit.MAX_LINES)
        p = plan.read()
        self.assertEqual((p['on'], p['dry']), (True, True))
        # In a trial run no Codex helper file is written: nothing would act on the routes.
        self.assertFalse(os.path.exists(os.path.join(self.codex, 'agents')))
        self.assertEqual(tryit.state(), {'trying': True, 'was_on': False, 'was_trial': False})
        got = tryit.off(kit=self.k, now=NOW, **self.where)
        self.assertEqual(got[0], W.TURNED_OFF)
        self.assertEqual(snapshot(self.home, times=False), before)
        self.assertEqual(self.outside_router(), data_before)
        self.assertIsNone(plan.read())
        s = settings.read()
        self.assertEqual((s['on'], s['dry']), (False, False))
        self.assertEqual(tryit.state(), {'trying': False, 'was_on': None, 'was_trial': None})

    def test_on_for_real_then_try_then_undo_stays_on_for_real(self):
        wire.turn_on(kit=self.k, now=NOW, **self.where)
        got = tryit.on(kit=self.k, now=NOW, **self.where)
        # The trial takes out the one Codex helper file routing wrote (reviews; lookups go to Claude first).
        self.assertEqual(got, [W.TRIAL_ON, W.PLAN_WRITTEN, W.CODEX_FILES_REMOVED[1]])
        self.assertTrue(plan.read()['dry'])
        self.assertEqual(tryit.state(), {'trying': True, 'was_on': True, 'was_trial': False})
        got = tryit.off(kit=self.k, now=NOW, **self.where)
        self.assertEqual(got, [W.TRIAL_OFF, W.PLAN_WRITTEN, W.CODEX_FILES_WROTE[1]])
        p = plan.read()
        self.assertEqual((p['on'], p['dry']), (True, False))

    def test_try_twice_then_undo_once(self):
        before = snapshot(self.home, times=False)
        tryit.on(kit=self.k, now=NOW, **self.where)
        tryit.on(kit=self.k, now=NOW + 5, **self.where)
        self.assertEqual(tryit.state(), {'trying': True, 'was_on': False, 'was_trial': False})
        tryit.off(kit=self.k, now=NOW + 10, **self.where)
        self.assertEqual(snapshot(self.home, times=False), before)
        self.assertFalse(settings.read()['on'])
        self.assertEqual(tryit.off(kit=self.k, now=NOW + 11, **self.where), [tryit.NOTHING])

    def test_undo_with_nothing_remembered_writes_nothing(self):
        before = snapshot(self.home, self.data)
        self.assertEqual(tryit.off(), [tryit.NOTHING])
        self.assertEqual(tryit.state(), {'trying': False, 'was_on': None, 'was_trial': None})
        self.assertEqual(snapshot(self.home, self.data), before)

    def test_never_raises(self):
        from unittest import mock
        with mock.patch.object(wire, 'turn_on_steps', side_effect=RuntimeError('made up')):
            got = tryit.on(kit=self.k, now=NOW, **self.where)
        self.assertEqual(got, [tryit.FAILED.format(why='made up')])

    def test_words(self):
        for text in (tryit.NOTHING, tryit.FAILED.format(why='a reason')):
            self.assertEqual(problems(text), [], text)


if __name__ == '__main__':
    unittest.main()
