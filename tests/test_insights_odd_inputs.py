import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Odd inputs to the usage data: a limit line without its window, a 1-hour cache count above the writes.
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)

from switchboard import insights
from test_insights_prices_and_highlights import limit_page
from test_insights_week import page_of
from test_usage_series import D, NOW, REAL, UTC, close


class ALimitWithoutItsWindow(unittest.TestCase):
    """History writes a limit's window ("5-hour" or "weekly"); a line without one never reads "of the its limit"."""

    def said(self, window):
        week, evs = limit_page()
        evs[0]['window'] = window
        got = [x for x in insights.find(page_of(week=week, events=evs)) if x['key'] == 'limit']
        return got[0]['figure'], got[0]['text']

    def test_says_its_limit(self):
        self.assertEqual(self.said(None),
                         ('95%', 'of its limit on a@x.example, Monday 12 PM. Auto-switch moved you to b@x.example.'))

    def test_a_known_window_is_named(self):
        for window in ('weekly', '5-hour'):
            self.assertEqual(self.said(window), ('95%', 'of the %s limit on a@x.example, Monday 12 PM. Auto-switch moved '
                                                        'you to b@x.example.' % window))


class AOneHourCountAboveItsWrites(unittest.TestCase):
    """A request that gives its 1-hour writes but not its writes weighed the 1-hour part at 0.75 (2 less
    1.25) on top of nothing. A write is at least its 1-hour part."""

    def summary(self, usage):
        root = tempfile.mkdtemp(prefix='switchboard-1h-')
        self.addCleanup(shutil.rmtree, root, True)
        path = os.path.join(root, '-made-up', 'aaaaaaaa-0000-0000-0000-000000000002.jsonl')
        os.makedirs(os.path.dirname(path))
        with open(path, 'w') as f:
            f.write(json.dumps({'type': 'assistant', 'timestamp': '2026-11-10T09:00:00Z', 'requestId': 'req_1',
                                'message': {'id': 'msg_1', 'model': 'claude-opus-5-5', 'content': [], 'usage': usage}}) + '\n')
        return REAL(root, NOW - 7 * D, NOW, tz=UTC)

    def test_a_write_is_at_least_its_one_hour_part(self):
        s = self.summary({'input_tokens': 10, 'output_tokens': 20, 'cache_read_input_tokens': 4000,
                          'cache_creation': {'ephemeral_1h_input_tokens': 400}})
        t = s['totals']['tokens']
        self.assertEqual((t['cache_write'], t['cache_write_1h']), (400, 400))
        close(self, s['totals']['weight'], 10 + 5 * 20 + 2 * 400 + 0.05 * 4000)

    def test_a_write_that_gives_both_counts_keeps_both(self):
        s = self.summary({'input_tokens': 10, 'output_tokens': 20, 'cache_creation_input_tokens': 1000,
                          'cache_read_input_tokens': 4000, 'cache_creation': {'ephemeral_1h_input_tokens': 400}})
        t = s['totals']['tokens']
        self.assertEqual((t['cache_write'], t['cache_write_1h']), (1000, 400))
        close(self, s['totals']['weight'], 10 + 5 * 20 + 1.25 * 600 + 2 * 400 + 0.05 * 4000)


if __name__ == '__main__':
    unittest.main()
