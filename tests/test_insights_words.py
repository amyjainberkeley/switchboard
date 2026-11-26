import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The wording and thresholds of the week's highlights and the busiest hour or day. The page's side is in
# test_page_week_details.py.
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)

from switchboard import insights
from switchboard.packs import memory as M
from test_insights_prices_and_highlights import limit_page
from test_insights_week import days_of, hours_of, page_of
from test_usage_series import D, NOW, UTC

CONTEXT_TOKENS = {'input': 100000, 'output': 200000, 'cache_write': 1600000, 'cache_read': 70000000}


def said(page, key):
    got = [x for x in insights.find(page) if x['key'] == key]
    return (got[0]['figure'], got[0]['text']) if got else None


# --- the biggest part
class MostlyNamesTheBiggestPart(unittest.TestCase):
    def test_mostly_names_the_biggest_part_not_the_last_that_passes(self):
        # workflows +50 and helpers +40 both explain over half of +50 (you fell by 40): workflows is "most"
        page = page_of(week={'weight': 160, 'who': {'workflows': 100, 'helpers': 60, 'chat': 0}},
                       last_week={'weight': 110, 'who': {'workflows': 50, 'helpers': 20, 'chat': 40}})
        self.assertEqual(said(page, 'week'), ('45%', 'more than last week, mostly workflows.'))
        # the same the other way round: helpers the bigger part, after workflows in the order
        page = page_of(week={'weight': 160, 'who': {'workflows': 60, 'helpers': 100, 'chat': 0}},
                       last_week={'weight': 110, 'who': {'workflows': 20, 'helpers': 50, 'chat': 40}})
        self.assertEqual(said(page, 'week'), ('45%', 'more than last week, mostly helpers.'))


# --- no peak under the floor
class NoPeakUnderTheFloor(unittest.TestCase):
    def test_no_peak_under_the_engines_floor(self):
        # one past hour of use and the hour so far is no week to name a busiest hour in; spike() agrees
        weights = [0] * 168
        weights[100], weights[167] = 50, 120
        page = page_of(series={'days': [], 'hours': hours_of(weights)})
        self.assertIsNone(insights.week_numbers(page)['peak']['hour'])
        self.assertIsNone(insights.spike(page))
        weights = [0] * 168
        for i in (10, 30, 50, 70, 90):
            weights[i] = 10
        weights[120] = 100                                  # six past hours with use: named
        page = page_of(series={'days': [], 'hours': hours_of(weights)})
        self.assertEqual(insights.week_numbers(page)['peak']['hour']['times'], 4.0)
        # days: two full days with use is under the floor of three
        page = page_of(series={'days': days_of([0, 0, 0, 0, 0, 10, 10, 5]), 'hours': []})
        self.assertIsNone(insights.week_numbers(page)['peak']['day'])
        page = page_of(series={'days': days_of([0, 0, 0, 0, 10, 10, 10, 5]), 'hours': []})
        self.assertEqual(insights.week_numbers(page)['peak']['day']['times'], 1.0)


# --- a thin last week
class AThinLastWeek(unittest.TestCase):
    def test_a_last_week_of_a_day_or_two_says_nothing(self):
        # "7× last week's use" from a last week with one day of use is true and easy to misread
        for days in (0, 1, 2):
            page = page_of(week={'weight': 700}, last_week={'weight': 100, 'days': days})
            self.assertIsNone(said(page, 'week'), days)
        page = page_of(week={'weight': 700}, last_week={'weight': 100, 'days': 3})
        self.assertEqual(said(page, 'week'), ('7×', 'last week’s use.'))
        # the payload says how many days of last week had use (the summary's days)
        last = {'totals': {'weight': 50.0}, 'days': [{'day': '2026-11-01', 'weight': 50.0}, {'day': '2026-11-02', 'weight': 0}]}
        s = {'window': {'since': NOW - 7 * D, 'until': NOW}, 'totals': {'weight': 1.0, 'tokens': {}}, 'workflows': []}
        self.assertEqual(insights.week(s, NOW, tz=UTC, last=last)['last_week']['days'], 1)
        self.assertIsNone(insights.week(s, NOW, tz=UTC, last={'totals': {'weight': 5.0}})['last_week']['days'])


# --- a limit before context
class ALimitBeforeContext(unittest.TestCase):
    def test_a_limit_week_puts_its_line_before_the_context_line(self):
        # the limit and its switch is the week's own news; re-reading context is said only when room is left
        week, evs = limit_page()
        page = page_of(cuts={'work': [{'label': 'Website', 'share': 60, 'note': ''}]}, tokens=CONTEXT_TOKENS,
                       moved={'share': 37, 'to': 'Sonnet', 'from': ['Opus']},
                       week=dict(week, weight=130), last_week={'weight': 100}, events=evs)
        self.assertEqual([x['key'] for x in insights.find(page)], ['week', 'moved', 'limit'])
        page['last_week'] = None
        self.assertEqual([x['key'] for x in insights.find(page)], ['moved', 'limit', 'project'])
        page['moved'] = None
        self.assertEqual([x['key'] for x in insights.find(page)], ['limit', 'project', 'context'])


# --- the context words
class TheContextWords(unittest.TestCase):
    def test_the_context_line_says_what_each_part_is(self):
        # "Saving it was 38%" read as a saving; "writing" had no object
        self.assertEqual(said(page_of(tokens=CONTEXT_TOKENS), 'context'),
                         ('53%', 'of the use was re-reading context. Storing it was 30%, Claude’s replies 15%.'))


# --- the limit words
class TheLimitWords(unittest.TestCase):
    def test_the_limit_line_is_a_sentence_that_names_both_accounts(self):
        # "95% weekly line" read as a line at 95; the account that reached it was never named
        week, evs = limit_page()
        self.assertEqual(said(page_of(week=week, events=evs), 'limit'),
                         ('95%', 'of the weekly limit on a@x.example, Monday 12 PM. Auto-switch moved you to b@x.example.'))


# --- the moved words
class TheMovedWords(unittest.TestCase):
    def test_the_moved_line_in_plain_words(self):
        # "named no model" is insider speech
        self.assertEqual(said(page_of(moved={'share': 43, 'to': 'Sonnet', 'from': ['Opus']}), 'moved'),
                         ('43%', 'of the week was helpers and workflow steps that ran on Opus because they chose no model.'))
        self.assertEqual(said(page_of(moved={'share': 43, 'to': 'Sonnet'}), 'moved')[1],
                         'of the week was helpers and workflow steps that ran on their chat’s model because they chose '
                         'no model.')


# --- Claude-Mem's share
class ClaudeMemsShare(unittest.TestCase):
    def test_the_observer_and_the_total_use_one_weight(self):
        # a 1-hour write weighed 1.25 in the observer's share and 2 in the total it is divided by
        home = tempfile.mkdtemp(prefix='switchboard-cm-')
        self.addCleanup(shutil.rmtree, home, True)
        cd = os.path.join(home, '.claude')
        obs = os.path.join(cd, 'projects', M.project_folder_name(os.path.join(home, '.claude-mem', 'observer-sessions')))
        os.makedirs(obs)
        now = time.time()
        with open(os.path.join(obs, 'o1.jsonl'), 'w') as f:
            f.write(json.dumps({'type': 'assistant', 'requestId': 'r1',
                                'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(now - 1800)),
                                'message': {'id': 'm1', 'model': 'claude-haiku-4-5', 'content': [],
                                            'usage': {'input_tokens': 10, 'output_tokens': 20,
                                                      'cache_creation_input_tokens': 1000,
                                                      'cache_creation': {'ephemeral_1h_input_tokens': 1000}}}}) + '\n')
        r = M.cm_measure(now - M.WEEK, now, cd, {}, home, os.path.join(home, 'cache.json'))
        self.assertAlmostEqual(r['observer_weight'], 10 + 5 * 20 + 2 * 1000)
        self.assertAlmostEqual(r['observer_weight'], r['total_weight'])
        self.assertAlmostEqual(r['share'], 100.0)


if __name__ == '__main__':
    unittest.main()
