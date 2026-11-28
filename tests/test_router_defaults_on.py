import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# The defaults once routing is on: handing work to another app on the person's route and the one "continue" after a
# move at a limit are on unless the person turned them off; letting that app change files stays off; every default
# by where work was started stays unset. And an app the person never added gets nothing: it cannot take work in any
# plan, the rule never hands work to it, and a hundred passes write nothing for it.
import json
import os
import shutil
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))
sys.path.insert(0, HERE)

from switchboard import core  # noqa: E402
from switchboard.router import plan, resolve, settings, wire  # noqa: E402

import fakes_provider_kit as kit  # noqa: E402
from test_router_wire import NEW, Home, snapshot  # noqa: E402


# These tests hold routing as it is once the hand-over and the one "continue" are in (router/ready.py): both then
# follow "on". tests/test_router_hand_ready.py holds the state this tree ships in.
_SHIPPED_FOLLOW_ON = settings.FOLLOW_ON


def setUpModule():
    settings.FOLLOW_ON = ('hand.on', 'carry_on')


def tearDownModule():
    settings.FOLLOW_ON = _SHIPPED_FOLLOW_ON

NOW = kit.NOW
UNSET = {w: {'model': None, 'effort': None} for w in settings.WHERE}


def stored():
    with open(settings.path(), encoding='utf-8') as f:
        return json.load(f)


class Base(unittest.TestCase):
    def setUp(self):
        p = core.data_path('router')
        if os.path.isdir(p):
            shutil.rmtree(p)


class OnceRoutingIsOn(Base):
    def test_off_until_routing_is_on(self):
        got = settings.read()
        self.assertEqual((got['on'], got['hand'], got['carry_on']), (False, {'on': False, 'write': False}, False))

    def test_routing_on_brings_the_hand_and_carry_on_and_nothing_else(self):
        got = settings.set_switch('on', True)
        self.assertEqual(got['hand'], {'on': True, 'write': False})
        self.assertIs(got['carry_on'], True)
        self.assertEqual(got['defaults'], UNSET)
        self.assertEqual((got['dry'], got['helpers'], got['chat']),
                         (False, {'force': False}, {'effort': False, 'model_on_first_turn': False}))
        self.assertEqual(settings.read(), got)
        # Not written as the person's choice: the file leaves both out, so they keep following "on".
        doc = stored()
        self.assertNotIn('carry_on', doc)
        self.assertEqual(doc['hand'], {'write': False})

    def test_routing_off_again_takes_both_off(self):
        settings.set_switch('on', True)
        got = settings.set_switch('on', False)
        self.assertEqual((got['hand']['on'], got['carry_on']), (False, False))
        self.assertEqual(settings.read(), got)

    def test_the_person_turning_either_off_is_kept_through_off_and_on(self):
        for name in settings.FOLLOW_ON:
            with self.subTest(name=name):
                settings.reset()
                settings.set_switch('on', True)
                settings.set_switch(name, False)
                settings.set_switch('on', False)
                got = settings.set_switch('on', True)
                self.assertIs(settings.get(got, name), False)
                other = [n for n in settings.FOLLOW_ON if n != name][0]
                self.assertIs(settings.get(got, other), True)
                self.assertEqual(settings.read(), got)

    def test_the_person_turning_either_on_while_routing_is_off_is_kept(self):
        got = settings.set_switch('carry_on', True)
        self.assertEqual((got['on'], got['carry_on'], got['hand']['on']), (False, True, False))
        self.assertIs(stored()['carry_on'], True)

    def test_a_file_that_names_neither_follows_on(self):
        os.makedirs(core.data_path('router'), exist_ok=True)
        with open(settings.path(), 'w', encoding='utf-8') as f:
            f.write('{"v": 1, "on": true}')
        got = settings.read()
        self.assertEqual((got['hand'], got['carry_on']), ({'on': True, 'write': False}, True))

    def test_the_plan_carries_them(self):
        settings.set_switch('on', True)
        got = plan.build(NOW, None, ['claude'], {}, {}, {}, {}, settings.read(), 'switchboard')
        self.assertEqual((got['on'], got['hand'], got['carry_on'], got['defaults']),
                         (True, {'on': True, 'write': False, 'max_s': plan.HAND_MAX_S}, True, UNSET))
        # A switches dict that leaves them out acts the same way.
        got = plan.build(NOW, None, ['claude'], {}, {}, {}, {}, {'on': True}, 'switchboard')
        self.assertEqual((got['hand']['on'], got['carry_on'], got['hand']['write']), (True, True, False))
        got = plan.build(NOW, None, ['claude'], {}, {}, {}, {}, {'on': False}, 'switchboard')
        self.assertEqual((got['hand']['on'], got['carry_on']), (False, False))


class NeverAdded(Home, kit.KitCase, unittest.TestCase):
    """The other app was never added: it is not in the person's order, so routing on gives it nothing."""

    def setUp(self):
        super().setUp()
        wire._GATHER['thread'] = None

    def test_never_added_gets_nothing_in_a_hundred_passes(self):
        k = self.kit(codex_added=False)
        codex_before = snapshot(self.codex)
        wire.turn_on(kit=k, claude_dir=self.claude, codex_home=self.codex, versions=NEW, now=NOW)
        self.assertIs(settings.read()['hand']['on'], True)
        for i in range(100):
            now = NOW + i * 60
            got = wire.pass_(now, kit=k)
            self.assertTrue(got['on'])
            p = plan.read()
            self.assertIsNotNone(p, 'pass %d wrote no plan' % i)
            self.assertNotIn('codex', p['order'])
            self.assertIs(p['hand']['on'], True)
            a = p['agents'].get('codex')
            self.assertTrue(a is None or (a['can'] is False and a['why_not'] == 'not_added'), a)
            for kind in list(p['routes']) + [None, 'other']:
                for where in (None,) + settings.WHERE:
                    answer = resolve.resolve(p, kind, 'claude', p['made_at'] + 1, where)
                    self.assertNotEqual(answer['do'], 'hand', (kind, where, answer))
                    self.assertNotEqual(answer.get('to'), 'codex')
        # The routes did name the other app, so the rule had something to pass.
        self.assertIn('codex', [c['agent'] for c in plan.read()['routes']['review']])
        self.assertEqual(snapshot(self.codex), codex_before)
        router = os.path.join(self.data, 'router')
        for name in ('hands.jsonl', 'codex-files.json', 'hand-out', 'stamps'):
            self.assertFalse(os.path.exists(os.path.join(router, name)), name)
        for folder, _dirs, names in os.walk(router):
            for name in names:
                if name in ('plan.json', 'plan.json.tmp'):
                    continue
                with open(os.path.join(folder, name), 'rb') as f:
                    self.assertNotIn(b'codex', f.read(), os.path.join(folder, name))


if __name__ == '__main__':
    unittest.main()
