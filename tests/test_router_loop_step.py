import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# The router's one look in each pass of the page's loop (switchboard/router/loop_step.py, called from server.loop):
# at most once a minute, never raising, a failure said once, the decisions it folded and the facts the apps told put
# into History, and nothing written for a person who never turned routing on.
import json
import os
import re
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from switchboard import history, server  # noqa: E402
from switchboard.router import loop_step, loop_words as LW, record, wire  # noqa: E402

import fakes_provider_kit as kit  # noqa: E402
from test_providers_words import problems  # noqa: E402
from test_router_wire import NEW, Home, snapshot  # noqa: E402
from test_watch_loop import LoopBase  # noqa: E402

NOW = kit.NOW


def fresh():
    loop_step._STATE.update(last=None, since=None, said=False)


def history_texts():
    try:
        return [r['text'] for r in history.read()]
    except FileNotFoundError:
        return []


class NeverTurnedOn(Home, kit.KitCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        fresh()

    def test_a_hundred_looks_write_nothing(self):
        before = snapshot(self.home, self.data)
        k = self.rig().kit
        for i in range(100):
            got = loop_step.step(now=NOW + i * 61, mono=1000.0 + i * 61, kit=k)
            self.assertFalse(got['on'])
            self.assertEqual(got['history'], 0)
        self.assertEqual(snapshot(self.home, self.data), before)


class OnceAMinute(unittest.TestCase):
    def setUp(self):
        fresh()

    def test_at_most_once_a_minute(self):
        calls = []
        with mock.patch.object(wire, 'pass_', lambda now, kit=None: calls.append(now) or {'signals': []}), \
                mock.patch.object(wire, 'history', lambda now, since: []):
            for mono in (100.0, 130.0, 159.9, 160.0, 200.0, 220.1):
                loop_step.step(now=mono, mono=mono)
        self.assertEqual(calls, [100.0, 160.0, 220.1])

    def test_a_failure_is_swallowed_and_said_once_until_a_look_works(self):
        said = []
        with mock.patch.object(loop_step.core, 'log', said.append), \
                mock.patch.object(wire, 'pass_', side_effect=RuntimeError('made up')):
            for i in range(3):
                self.assertEqual(loop_step.step(now=NOW, mono=1000.0 + i * 60), {'failed': True})
        self.assertEqual(said, ['routing: a look failed (RuntimeError)'])
        with mock.patch.object(wire, 'pass_', lambda now, kit=None: {'signals': []}), \
                mock.patch.object(wire, 'history', lambda now, since: []):
            loop_step.step(now=NOW, mono=2000.0)
        with mock.patch.object(loop_step.core, 'log', said.append), \
                mock.patch.object(wire, 'pass_', side_effect=RuntimeError('made up')):
            loop_step.step(now=NOW, mono=3000.0)
        self.assertEqual(len(said), 2)


class IntoHistory(Home, kit.KitCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        fresh()
        wire._GATHER['thread'] = None

    def drop(self, folder, name, obj):
        path = os.path.join(self.data, 'router', folder)
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, name), 'w', encoding='utf-8') as f:
            json.dump(obj, f)
        os.utime(os.path.join(path, name), (obj['t'], obj['t']))     # settled, as the fold wants

    def test_decisions_and_signals_go_to_history(self):
        k = self.kit()
        wire.turn_on(kit=k, claude_dir=self.claude, codex_home=self.codex, versions=NEW, now=NOW)
        self.drop('decisions', '%d-0000000a.json' % ((NOW - 10) * 1000), {
            't': NOW - 10, 'agent': 'claude', 'where': 'helper', 'type': 'Explore', 'kind': 'find', 'did': 'model',
            'from_model': 'opus', 'model': 'haiku', 'effort': 'medium', 'to': None, 'reason': 'route',
            'why': 'your route for lookups', 'passed': [], 'plan': NOW - 20})
        self.drop('signals', '%d-0000000b.json' % ((NOW - 5) * 1000),
                  {'t': NOW - 5, 'agent': 'claude', 'what': 'at_line', 'window': 'five_hour', 'pct': 91})
        self.drop('signals', '%d-0000000c.json' % ((NOW - 4) * 1000),
                  {'t': NOW - 4, 'agent': 'claude', 'what': 'limit_stop', 'window': None, 'pct': None})
        got = loop_step.step(now=NOW, mono=500.0, kit=k)
        self.assertTrue(got['on'])
        texts = history_texts()
        decided = [t for _, t in record.lines(record.read()[0])]
        self.assertTrue(decided)
        for t in decided:
            self.assertIn(t, texts)
        self.assertIn(LW.AT_LINE.format(app='Claude', window='5-hour', pct=91), texts)
        self.assertIn(LW.LIMIT_STOP.format(app='Claude'), texts)
        self.assertEqual(got['history'], len(decided) + 2)
        # The next look a minute later gives none of them again.
        self.assertEqual(loop_step.step(now=NOW + 61, mono=561.0, kit=k)['history'], 0)

    def test_a_trial_run_notes_a_signal_and_does_nothing_with_it(self):
        fact = {'t': NOW - 5, 'agent': 'claude', 'what': 'limit_stop', 'window': None, 'pct': None}
        said = []
        with mock.patch.object(wire, 'pass_', lambda now, kit=None: {'dry': True, 'signals': [fact]}), \
                mock.patch.object(wire, 'history', lambda now, since: []), \
                mock.patch.object(loop_step.core, 'log', said.append):
            got = loop_step.step(now=NOW, mono=700.0)
        self.assertEqual((got['history'], got['signals_ignored']), (0, 1))
        self.assertNotIn(LW.LIMIT_STOP.format(app='Claude'), history_texts())
        self.assertEqual(said, ['routing: 1 signal(s) in a trial run, nothing done'])

    def test_a_signal_it_cannot_say_is_left_out(self):
        for fact in ({'agent': 'nobody', 'what': 'limit_stop'}, {'agent': 'claude', 'what': 'at_line'},
                     {'agent': 'claude', 'what': 'at_line', 'window': 'year', 'pct': 5},
                     {'agent': 'claude', 'what': 'at_line', 'window': 'week', 'pct': 140}, 'text', None):
            with self.subTest(fact=fact):
                self.assertIsNone(loop_step.signal_line(fact))


class Words(unittest.TestCase):
    def test_every_sentence(self):
        for text in (LW.AT_LINE.format(app='Codex', window='weekly', pct=95), LW.LIMIT_STOP.format(app='Codex')):
            self.assertEqual(problems(text), [], text)


class InThePagesLoop(LoopBase):
    def test_one_call_a_pass_and_one_that_raises_ends_nothing(self):
        calls = []
        self.patch(loop_step, 'step', lambda: calls.append(1) or {})
        server.loop(ticks=3)
        self.assertEqual(len(calls), 3)
        self.patch(loop_step, 'step', mock.Mock(side_effect=RuntimeError('made up')))
        self.ticks[:] = []
        server.loop(ticks=3)
        self.assertTrue(self.ticks)
        with open(os.path.join(ROOT, 'switchboard', 'server.py'), encoding='utf-8') as f:
            self.assertEqual(len(re.findall(r'loop_step\.step\(', f.read())), 1)


if __name__ == '__main__':
    unittest.main()
