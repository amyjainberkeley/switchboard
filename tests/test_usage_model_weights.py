import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# A cache read is weighed at the price of the request's own model: on Fable 5.2 and Mythos 5.2 a cache read costs
# $0.20 a million against $8 for input (0.025), on Opus 5.5 $0.20 against $4 and on Sonnet 5.5 $0.10 against $2
# (0.05). Every other weight is the same on all three: output 5, a 5-minute write 1.25, a 1-hour write 2.
# The yardstick stays weighted tokens: a Fable token and an Opus token of the same kind count the same.
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)

from switchboard import insights, usage
from test_usage_series import D, NOW, REAL, UTC, close


def history(root, requests):
    """One made-up chat under root, one request per (model, usage) pair, an hour apart."""
    path = os.path.join(root, '-made-up', 'aaaaaaaa-0000-0000-0000-000000000003.jsonl')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        for n, (model, u) in enumerate(requests):
            f.write(json.dumps({'type': 'assistant', 'timestamp': '2026-11-10T%02d:00:00Z' % (9 + n),
                                'requestId': 'req_%d' % n,
                                'message': {'id': 'msg_%d' % n, 'model': model, 'content': [], 'usage': u}}) + '\n')


READS = {'input_tokens': 0, 'output_tokens': 0, 'cache_read_input_tokens': 1000}


class ACacheReadAtItsModelsPrice(unittest.TestCase):

    def summary(self, requests, **kw):
        root = tempfile.mkdtemp(prefix='switchboard-weights-')
        self.addCleanup(shutil.rmtree, root, True)
        history(root, requests)
        return REAL(root, NOW - 7 * D, NOW, tz=UTC, **kw)

    def test_the_same_reads_weigh_by_their_model(self):
        s = self.summary([('claude-fable-5-2', READS), ('claude-opus-5-5', READS), ('claude-sonnet-5-5', READS)])
        m = dict((r['label'], r['detail']['weight']) for r in s['model'])
        close(self, m['Fable'], 0.025 * 1000)
        close(self, m['Opus'], 0.05 * 1000)
        close(self, m['Sonnet'], 0.05 * 1000)
        close(self, s['totals']['weight'], 25 + 50 + 50)
        close(self, usage.weight(s['totals']['tokens']), s['totals']['weight'])     # the tokens alone say it too

    def test_a_model_whose_price_is_not_known_keeps_005(self):
        for model in ('claude-fable-5-1', 'claude-opus-4-1', 'gpt-x', '', None):
            self.assertEqual(usage.cache_read_weight(model), 0.05, model)
        for model in ('claude-fable-5-2', 'claude-mythos-5-2', 'claude-fable-5-2[1m]', 'Claude-Fable-5-2'):
            self.assertEqual(usage.cache_read_weight(model), 0.025, model)
        s = self.summary([('claude-made-up-9', READS)])
        close(self, s['totals']['weight'], 0.05 * 1000)

    def test_a_long_context_variant_is_still_fable(self):
        s = self.summary([('claude-fable-5-2[1m]', READS), ('claude-mythos-5-2', READS)])
        close(self, s['totals']['weight'], 0.025 * 1000 * 2)

    def test_the_hours_weigh_the_same_way(self):
        s = self.summary([('claude-fable-5-2', READS), ('claude-opus-5-5', READS)], series_since=NOW - 7 * D)
        used = [h for h in s['series']['hours'] if h['weight']]
        self.assertEqual([round(h['weight'], 6) for h in used], [25.0, 50.0])
        for h in used:
            close(self, usage.weight(h['tokens']), h['weight'])

    def test_the_week_re_reading_share_on_fable_is_what_the_prices_give(self):
        # By hand, in dollars a million: input 100 x $8 = 800, output 200 x $40 = 8,000, a 1-hour write
        # 1,000 x $16 = 16,000, cache reads 100,000 x $0.20 = 20,000. Re-reading: 20,000 of 44,800, 44.6%. The
        # page's whole percents add to 100 by the largest remainders (1.8, 17.9, 35.7, 44.6 give 2, 18, 36, 44): 44%.
        # (At 0.05 it was 5,000 of 8,100 weighted tokens, 61.7%.)
        s = self.summary([('claude-fable-5-2', {'input_tokens': 100, 'output_tokens': 200,
                                                'cache_creation': {'ephemeral_1h_input_tokens': 1000},
                                                'cache_read_input_tokens': 100000})])
        t = s['totals']['tokens']
        wt = usage.weighted(t)
        close(self, wt['cache_read'] / sum(wt.values()), 20000.0 / 44800)
        close(self, s['totals']['weight'], 44800.0 / 8)               # dollars over the input price: weighted tokens
        got = insights._context({'tokens': t})
        self.assertEqual((got[1], got[3]), ('44%', {'cache_read': 44, 'cache_write': 36, 'output': 18, 'input': 2}))

    def test_the_result_says_which_models_weigh_a_read_less(self):
        s = self.summary([('claude-opus-5-5', READS)])
        self.assertEqual(s['weighting']['cache_read_by_model'], {'claude-fable-5-2': 0.025, 'claude-mythos-5-2': 0.025})
        self.assertIn('0.025 x on Fable 5.2', s['weighting']['formula'])

    def test_the_formula_names_every_model_that_weighs_a_read_less(self):
        self.assertIn('+ 0.05 x cache read (0.025 x on Fable 5.2 and Mythos 5.2) + 5 x output', usage.WEIGHT_FORMULA)
        self.assertIn(usage.WEIGHT_FORMULA, usage.WEIGHT_NOTE)

    def test_a_cached_read_weighs_the_same(self):
        root = tempfile.mkdtemp(prefix='switchboard-weights-')
        self.addCleanup(shutil.rmtree, root, True)
        history(os.path.join(root, 'projects'), [('claude-fable-5-2', READS)])
        cache = os.path.join(root, 'usage-cache.json')
        first = REAL(os.path.join(root, 'projects'), NOW - 7 * D, NOW, tz=UTC, cache_path=cache)
        again = REAL(os.path.join(root, 'projects'), NOW - 7 * D, NOW, tz=UTC, cache_path=cache)
        self.assertEqual((first['files']['read'], again['files']['from_cache']), (1, 1))
        close(self, first['totals']['weight'], 25.0)
        close(self, again['totals']['weight'], 25.0)


if __name__ == '__main__':
    unittest.main()
