import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# Whether the person's own count calls for an hour cache for helpers (switchboard/router/cache_rule.py), each side of
# each bound, and the loop's one look after routing is turned on (router/loop_step.py).
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))

from switchboard import core, history  # noqa: E402
from switchboard.router import cache_count, cache_rule, cache_setting, loop_step, loop_words as LW, wire  # noqa: E402

GOOD = {'stopped_early': False, 'records_read': 50, 'restores': 20, 'hour_would_read': 1000, 'hour_would_write': 1000}


def r(**kw):
    out = dict(GOOD)
    out.update(kw)
    return out


class Pick(unittest.TestCase):
    def test_each_side_of_each_bound(self):
        self.assertEqual(cache_rule.pick(GOOD), '1h')                   # 1150 > 900
        self.assertIsNone(cache_rule.pick(r(restores=19)))
        self.assertIsNone(cache_rule.pick(r(stopped_early=True, records_read=199)))
        self.assertEqual(cache_rule.pick(r(stopped_early=True, records_read=200)), '1h')
        # 1.15 * read against 1.2 * 0.75 * write = 0.9 * write
        self.assertIsNone(cache_rule.pick(r(hour_would_read=900, hour_would_write=1150)))     # equal: not more
        self.assertEqual(cache_rule.pick(r(hour_would_read=901, hour_would_write=1150)), '1h')
        self.assertIsNone(cache_rule.pick(r(hour_would_read=0, hour_would_write=0)))
        for bad in (None, 'text', {}, r(restores='many')):
            self.assertIsNone(cache_rule.pick(bad))

    def test_never_five_minutes(self):
        for res in (GOOD, r(restores=0), r(hour_would_read=0)):
            self.assertIn(cache_rule.pick(res), ('1h', None))


class TheLoopsOneLook(unittest.TestCase):
    def setUp(self):
        loop_step._STATE.update(last=None, since=None, said=False)
        for name in (loop_step.CACHE_LOOKED, wire.CACHE_MARK):
            try:
                os.remove(core.data_path(name))
            except FileNotFoundError:
                pass

    def step(self, mono, dry=False):
        with mock.patch.object(wire, 'pass_', lambda now, kit=None: {'on': True, 'dry': dry, 'signals': []}), \
                mock.patch.object(wire, 'history', lambda now, since: []), \
                mock.patch.object(loop_step, '_start', lambda fn: fn()):
            return loop_step.step(now=1764239000.0, mono=mono)

    def test_once_after_turn_on_and_said_in_history(self):
        applied = []
        with mock.patch.object(cache_count, 'count', lambda folders, **k: dict(GOOD)), \
                mock.patch.object(cache_setting, 'apply', lambda v: applied.append(v) or {'written': True}):
            self.assertTrue(self.step(100.0)['cache_look'])
            self.assertFalse(self.step(200.0)['cache_look'])
        self.assertEqual(applied, ['1h'])
        self.assertTrue(os.path.exists(core.data_path(wire.CACHE_MARK)))
        self.assertIn(LW.CACHE_ON, [x['text'] for x in history.read()])

    def test_nothing_set_when_the_count_does_not_call_for_it_or_in_a_trial(self):
        applied = []
        with mock.patch.object(cache_count, 'count', lambda folders, **k: r(restores=3)), \
                mock.patch.object(cache_setting, 'apply', lambda v: applied.append(v) or {'written': True}):
            self.assertFalse(self.step(100.0, dry=True)['cache_look'])
            self.assertTrue(self.step(200.0)['cache_look'])
        self.assertEqual(applied, [])
        self.assertFalse(os.path.exists(core.data_path(wire.CACHE_MARK)))


if __name__ == '__main__':
    unittest.main()
