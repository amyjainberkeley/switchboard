import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# Routing in the terminal (switchboard/router/terminal.py): the lines under the bare list and `switchboard routing`.
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))
sys.path.insert(0, HERE)

from switchboard import __main__ as cli, core  # noqa: E402
from switchboard.router import cache_setting, hand_words, settings, terminal, wire, words  # noqa: E402
from switchboard.router import terminal_words as T  # noqa: E402
from switchboard.router import wire_words as W  # noqa: E402

import fakes_provider_kit as kit  # noqa: E402
from test_providers_words import problems  # noqa: E402
from test_router_wire import Home, snapshot  # noqa: E402


def run(*args):
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(['routing'] + list(args))
    return code, out.getvalue().splitlines()


class TheListsLines(Home, kit.KitCase, unittest.TestCase):
    def test_off_is_not_one_line(self):
        # Never on, turned off, or a switches file that cannot be read: the bare list is as it was without routing.
        before = snapshot(self.home, self.data)
        self.assertEqual(terminal.list_lines(), [])
        self.assertEqual(snapshot(self.home, self.data), before)
        settings.set_switch('on', True)
        settings.set_switch('on', False)
        self.assertEqual(terminal.list_lines(), [])
        settings.set_switch('on', True)
        with open(settings.path(), 'w', encoding='utf-8') as f:
            f.write('{not json')
        self.assertEqual(terminal.list_lines(), [])

    def test_none_in_the_demo_and_none_when_it_fails(self):
        with mock.patch.object(core, 'DEMO', True):
            self.assertEqual(terminal.list_lines(), [])
        settings.set_switch('on', True)
        with mock.patch.object(wire, 'lines', side_effect=RuntimeError('made up')):
            self.assertEqual(terminal.list_lines(), [])

    def test_on_is_at_most_four_lines(self):
        settings.set_switch('on', True)
        with mock.patch.object(wire, 'lines', lambda now: ['a', 'b', 'c', 'd']):
            self.assertEqual(terminal.list_lines(), ['', 'a', 'b', 'c', 'd'])


class TheCommand(Home, kit.KitCase, unittest.TestCase):
    def test_alone_when_never_on_says_everything_and_writes_nothing(self):
        before = snapshot(self.home, self.data)
        code, out = run()
        self.assertEqual(code, 0)
        self.assertEqual(out[0], W.OFF)
        for name in settings.NAMES:
            self.assertIn(T.SWITCH.format(label=words.SWITCHES[name], state=T.OFF), out)
        for where in settings.WHERE:
            self.assertIn(T.DEFAULT.format(where=T.WHERE[where], what=T.DEFAULT_NONE), out)
        self.assertIn(T.NO_ROUTES, out)
        # Whose sign-in work handed to Codex runs on; or, where the hand-over is not in, one line that says so.
        self.assertIn(hand_words.CODEX_SIGN_IN if settings.HAND_READY else hand_words.HAND_NOT_YET, out)
        self.assertEqual(out[-2:], [T.LAST, T.LAST_LINE.format(line=W.LAST_NONE)])
        self.assertEqual(snapshot(self.home, self.data), before)

    def test_on_off_and_the_trial_are_the_wiring_s_own(self):
        for args, name, want in ((['on'], 'turn_on', ()), (['off'], 'turn_off', ()), (['trial', 'on'], 'trial', (True,)),
                                 (['trial', 'off'], 'trial', (False,)), (['set', 'on', 'on'], 'turn_on', ()),
                                 (['set', 'on', 'off'], 'turn_off', ()), (['set', 'dry', 'on'], 'trial', (True,))):
            with self.subTest(args=args):
                called = []
                with mock.patch.object(wire, name, lambda *a, **k: called.append((a, k)) or ['done']):
                    self.assertEqual(run(*args), (0, ['done']))
                self.assertEqual(called[0][0], want)
                if name == 'turn_on':           # the person's own count decides the helpers' cache
                    self.assertIs(called[0][1]['cache'], terminal._cache_pick)

    def test_set_a_switch(self):
        self.assertEqual(run('set', 'hand.write', 'on'),
                         (0, [T.SET_SWITCH.format(label=words.SWITCHES['hand.write'], state=T.ON)]))
        self.assertIs(settings.read()['hand']['write'], True)
        self.assertEqual(run('set', 'hand.write', 'maybe'), (0, [T.NOT_A_SWITCH.format(name='hand.write')]))
        self.assertIs(settings.read()['hand']['write'], True)

    def test_set_a_default_for_the_app_whose_list_knows_the_word(self):
        code, out = run('set', 'defaults.workflow.effort', 'xhigh')
        self.assertEqual(code, 0)
        self.assertEqual(settings.read()['defaults']['workflow']['effort'], 'xhigh')
        self.assertEqual(out, [T.SET_DEFAULT.format(where=T.WHERE['workflow'],
                                                    what=T.DEFAULT_EFFORT.format(effort='xhigh'))])
        run('set', 'defaults.helper.model', 'haiku')
        run('set', 'defaults.helper.model', 'gpt-6-luna')
        self.assertEqual(settings.read()['defaults']['helper']['model'], {'claude': 'haiku', 'codex': 'gpt-6-luna'})
        run('set', 'defaults.helper.model', 'none')
        self.assertIsNone(settings.read()['defaults']['helper']['model'])
        self.assertEqual(run('set', 'defaults.chat.model', 'nonesuch-9'),
                         (0, [T.NOT_KNOWN.format(word='nonesuch-9', what=T.WORD_MODEL)]))

    def test_a_name_there_is_not_and_words_it_does_not_take(self):
        code, out = run('set', 'hand.everything', 'on')
        self.assertEqual(code, 1)
        self.assertIn('hand.write', out[0])
        self.assertEqual(run('sideways'), (2, [T.USAGE]))
        self.assertEqual(run('trial', 'maybe'), (2, [T.USAGE]))

    def test_cache_shows_the_count_then_the_setting(self):
        code, out = run('cache')
        self.assertEqual(code, 0)
        self.assertEqual(out[0], T.COUNTING)
        self.assertTrue(len(out) >= 3, out)
        with mock.patch.object(cache_setting, 'apply', lambda value: {'says': 'set to ' + value, 'dry': None,
                                                                      'blocked': None}), \
                mock.patch.object(cache_setting, 'undo', lambda: {'says': 'put back', 'dry': None, 'blocked': None}):
            self.assertEqual(run('cache', 'on')[1][-1], 'set to 1h')
            self.assertEqual(run('cache', 'off')[1][-1], 'put back')

    def test_every_sentence(self):
        for name in [n for n in dir(T) if n.isupper()]:
            v = getattr(T, name)
            for text in (v.values() if isinstance(v, dict) else [v] if isinstance(v, str) else []):
                with self.subTest(name=name):
                    self.assertEqual(problems(text), [], text)


if __name__ == '__main__':
    unittest.main()
