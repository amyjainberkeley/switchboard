import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# The two names the first run imports (`from .router import wire, hand_words`): hand_words.FIRST_RUN, a list of plain
# lines, and wire.turn_on(), called with no arguments, which returns plain lines, is safe twice and never raises.
import inspect
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))
sys.path.insert(0, HERE)

from switchboard.router import codex_files, hand_words, plan, plugin_setting, settings, wire  # noqa: E402

import fakes_provider_kit as kit  # noqa: E402
from test_providers_words import problems  # noqa: E402
from test_router_wire import Home  # noqa: E402


class TheSeam(Home, kit.KitCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        wire._GATHER['thread'] = None

    def plain(self, lines):
        self.assertIsInstance(lines, list)
        self.assertTrue(lines)
        for x in lines:
            self.assertIsInstance(x, str)
            self.assertTrue(x and '\n' not in x, lines)

    def test_first_run_lines(self):
        # The first run prints these, then turn_on's lines; it needs none of its own (turn_on's first line is the
        # bare list's sentence), so the list stays empty and the step stays at turn_on's at most three lines.
        self.assertEqual(hand_words.FIRST_RUN, [])
        with mock.patch.object(plan, 'gather', lambda now, kit=None: plan.build(now, None, ['claude'], {}, {}, {}, {},
                                                                                 settings.read(), 'switchboard')):
            got = wire.turn_on()
        self.plain(got)
        self.assertLessEqual(len(got), wire.TURN_ON_BRIEF_MAX)
        for x in got:
            self.assertEqual(problems(x), [], x)

    def test_turn_on_takes_no_argument_and_is_safe_twice(self):
        self.assertTrue(all(p.default is not p.empty for p in inspect.signature(wire.turn_on).parameters.values()))
        with mock.patch.object(plan, 'gather', lambda now, kit=None: plan.build(now, None, ['claude'], {}, {}, {}, {},
                                                                                 settings.read(), 'switchboard')):
            first = wire.turn_on()
            second = wire.turn_on()
        self.plain(first)
        self.plain(second)
        self.assertIs(settings.read()['on'], True)

    def test_turn_on_never_raises(self):
        boom = mock.Mock(side_effect=RuntimeError('made up'))
        for target, name in ((settings, 'set_switch'), (plan, 'gather'), (plan, 'write'), (plugin_setting, 'apply'),
                             (codex_files, 'hooks_apply'), (codex_files, 'apply'),
                             (codex_files, 'default_codex_home'), (wire, '_codex_ready'), (wire, '_plugin_line')):
            with self.subTest(step=name), mock.patch.object(target, name, boom):
                self.plain(wire.turn_on())


if __name__ == '__main__':
    unittest.main()
