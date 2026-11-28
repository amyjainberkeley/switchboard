import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# Two things of the record the joined plugin and hand need: the one "continue" after a move is a decision with its
# History line, and a hand-over is told once, by the hand helper's own record.
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))

from switchboard.router import hand, record, words  # noqa: E402

BASE = {'t': 1764239012.5, 'agent': 'claude', 'where': 'chat', 'type': None, 'kind': None, 'from_model': None,
        'model': None, 'effort': None, 'to': None, 'reason': 'moved', 'why': None, 'passed': [], 'plan': 1764239000.0}


class CarryOn(unittest.TestCase):
    def test_a_continue_after_a_move_is_a_decision_with_its_line(self):
        rec = dict(BASE, did='carry_on')
        self.assertTrue(record.good(rec))
        self.assertEqual(record.line(rec), words.LINE_CARRY_ON.format(subject='Your chat in Claude'))
        self.assertIn((words.LINE_CARRY_ON, 'info'), words.HISTORY)


class OneHandOver(unittest.TestCase):
    def rec(self, type_):
        return dict(BASE, where='helper', type=type_, kind='review', did='hand', to='codex', model='gpt-6.2-astra',
                    reason='route', why='your route for reviews')

    def test_only_the_hand_helpers_own_record_is_told(self):
        self.assertEqual(record.HAND_TYPE, hand.HELPER_TYPE)
        self.assertIsNotNone(record.line(self.rec(hand.HELPER_TYPE)))
        for type_ in (None, 'general-purpose', 'Explore'):
            with self.subTest(type=type_):
                self.assertIsNone(record.line(self.rec(type_)))
        self.assertEqual(len(record.lines([self.rec('general-purpose'), self.rec(hand.HELPER_TYPE)])), 1)


if __name__ == '__main__':
    unittest.main()
