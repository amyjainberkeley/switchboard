import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The week's re-reading takeaway weighs a cache read by the request's own model, as the week's total and its
# highlight do: 0.025 of an input token for the reads kept as cache_read_low (Fable 5.2, Mythos 5.2), 0.05 for the
# rest. So the share the takeaway shows is the share the week section shows beside it.
#
# Pure: no file, no process.
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))

from switchboard import insights, optimize

# Weighted: input 1,000; output 200 x 5 = 1,000; cache write 800 x 1.25 = 1,000; cache read 400,000, all of it on
# Fable 5.2: 400,000 x 0.025 = 10,000. Re-reading is 10,000 of 13,000 = 76.9%, 77 in whole percents. Weighed at 0.05
# for every model it would be 20,000 of 23,000 = 87%.
TOKENS = {'input': 1000, 'output': 200, 'cache_write': 800, 'cache_read': 400000, 'cache_write_1h': 0,
          'cache_read_low': 400000}


def rereading(page):
    got = [t for t in optimize.takeaways(page, {}) if t['id'] == 'rereading']
    return got[0]['measured']['value'] if got else None


class TheRereadingTakeaway(unittest.TestCase):
    def test_reads_on_fable_weigh_what_the_week_weighs_them(self):
        page = {'tokens': dict(TOKENS)}
        self.assertEqual(rereading(page), 77)
        self.assertEqual(rereading(page), insights._context(page)[3]['cache_read'])

    def test_the_same_reads_on_another_model_weigh_as_before(self):
        page = {'tokens': dict(TOKENS, cache_read_low=0)}
        self.assertEqual(rereading(page), 87)
        self.assertEqual(rereading(page), insights._context(page)[3]['cache_read'])

    def test_a_week_kept_from_before_reads_had_a_weight_per_model_reads_as_it_did(self):
        page = {'tokens': dict((k, v) for k, v in TOKENS.items() if k != 'cache_read_low')}
        self.assertEqual(rereading(page), 87)


if __name__ == '__main__':
    unittest.main()
