import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The usage data behind the week page: tokens weighed at today's prices, the three small fields the page draws (each
# project split by who, last week by who, the week's counts), one average for the chart and the words, and the
# highlights: a figure apart from its line, true to their rules, short and steady.
import copy
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
from test_insights_week import EVENT_KEYS, days_of, fixture_week, hours_of, page_of
from test_usage_series import D, NOW, REAL, UTC, close


# ---------------------------------------------------------------- weights at today's prices

class TodaysPrices(unittest.TestCase):
    """Opus 5.5 and Sonnet 5.5: a cache read is 0.05 of an input token ($0.20 against $4, $0.10 against $2), a cache
    write 1.25x for a 5-minute write and 2x for a 1-hour one, output 5x."""

    def test_the_weights(self):
        self.assertEqual(usage.WEIGHTS, {'input': 1.0, 'output': 5.0, 'cache_write': 1.25, 'cache_read': 0.05})
        self.assertEqual(usage.WRITE_1H, 2.0)
        self.assertAlmostEqual(usage.weight({'input': 100, 'output': 10, 'cache_write': 8, 'cache_read': 1000}),
                               100 + 50 + 10 + 50)
        # 8 written, 4 of them for an hour: 4 x 1.25 + 4 x 2
        self.assertAlmostEqual(usage.weight({'input': 100, 'output': 10, 'cache_write': 8, 'cache_read': 1000,
                                             'cache_write_1h': 4}), 100 + 50 + 5 + 8 + 50)
        self.assertAlmostEqual(usage.weight([100, 10, 8, 1000]), 210)
        self.assertAlmostEqual(usage.weight([100, 10, 8, 1000, 4]), 213)
        self.assertIn('0.05 x cache read', usage.WEIGHT_FORMULA)

    def write(self, root, cache_creation):
        path = os.path.join(root, '-made-up', 'aaaaaaaa-0000-0000-0000-000000000001.jsonl')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        u = {'input_tokens': 10, 'output_tokens': 20, 'cache_creation_input_tokens': 1000, 'cache_read_input_tokens': 4000}
        if cache_creation is not None:
            u['cache_creation'] = cache_creation
        with open(path, 'w') as f:
            f.write(json.dumps({'type': 'assistant', 'timestamp': '2026-11-10T09:00:00Z', 'requestId': 'req_1',
                                'message': {'id': 'msg_1', 'model': 'claude-opus-5-5', 'content': [], 'usage': u}}) + '\n')
        return path

    def summary(self, cache_creation, cache_path=None):
        root = tempfile.mkdtemp(prefix='switchboard-prices-')
        self.addCleanup(shutil.rmtree, root, True)
        self.write(root, cache_creation)
        return REAL(root, NOW - 7 * D, NOW, tz=UTC, cache_path=cache_path)

    def test_a_one_hour_write_counts_twice_an_input_token(self):
        s = self.summary({'ephemeral_5m_input_tokens': 600, 'ephemeral_1h_input_tokens': 400})
        t = s['totals']['tokens']
        self.assertEqual((t['cache_write'], t['cache_write_1h']), (1000, 400))
        close(self, s['totals']['weight'], 10 + 5 * 20 + 1.25 * 600 + 2 * 400 + 0.05 * 4000)

    def test_a_write_that_does_not_say_its_kind_is_a_five_minute_one(self):
        for said in (None, {}, {'ephemeral_1h_input_tokens': 'x'}, 'odd'):
            s = self.summary(said)
            self.assertEqual(s['totals']['tokens']['cache_write_1h'], 0, said)
            close(self, s['totals']['weight'], 10 + 5 * 20 + 1.25 * 1000 + 0.05 * 4000)

    def test_a_cache_from_the_old_weights_is_read_again(self):
        self.assertEqual(usage.CACHE_VERSION, 2)
        root = tempfile.mkdtemp(prefix='switchboard-prices-')
        self.addCleanup(shutil.rmtree, root, True)
        path = self.write(root, {'ephemeral_5m_input_tokens': 0, 'ephemeral_1h_input_tokens': 1000})
        st = os.stat(path)
        cache = os.path.join(root, 'cache.json')
        old_row = ['k', 1794301200.0, 'claude-opus-5-5', 10, 20, 1000, 4000, 0, 0, []]       # version 1: no 1-hour count
        with open(cache, 'w') as f:
            json.dump({'version': 1, 'files': {path: {'size': st.st_size, 'mtime_ns': st.st_mtime_ns,
                                                       'data': {'reqs': [old_row], 'custom': None, 'ai': None,
                                                                'folder': None, 'bad': 0, 'torn': False, 'nokey': 0}}}}, f)
        s = REAL(root, NOW - 7 * D, NOW, tz=UTC, cache_path=cache)
        self.assertEqual(s['files']['read'], 1)
        self.assertEqual(s['totals']['tokens']['cache_write_1h'], 1000)
        with open(cache) as f:
            self.assertEqual(json.load(f)['version'], 2)
        again = REAL(root, NOW - 7 * D, NOW, tz=UTC, cache_path=cache)
        self.assertEqual((again['files']['from_cache'], again['totals']['weight']), (1, s['totals']['weight']))

    def test_one_set_of_weights_everywhere(self):
        s, page = fixture_week()
        close(self, usage.weight(page['tokens']), page['week']['weight'])
        close(self, sum(h['weight'] for h in page['series']['hours']), page['week']['weight'])
        close(self, sum(r['detail']['weight'] for r in s['work']), s['totals']['weight'])


# ---------------------------------------------------------------- the three small fields

class SmallFields(unittest.TestCase):
    def test_each_project_split_by_who(self):
        s, page = fixture_week()
        rows = page['cuts']['work']
        for r in rows:
            self.assertEqual(set(r['who']), {'workflows', 'helpers', 'chat'})
            self.assertEqual(sum(r['who'].values()), 100, r)
        by = dict((r['label'], r) for r in s['work'])
        garden = by['Garden planner v2']['detail']
        want = usage.whole_percents([garden['workflows'], garden['helpers'], garden['own']])
        got = [r for r in rows if r['label'] == 'Garden planner v2'][0]['who']
        self.assertEqual([got['workflows'], got['helpers'], got['chat']], want)

    def test_the_rest_folded_keeps_its_split(self):
        def chat(label, weight, wf, help_, own):
            return {'label': label, 'share': 0, 'detail': {'weight': weight, 'workflows': wf, 'helpers': help_,
                                                           'own': own, 'models': []}}
        s, _ = fixture_week()
        s = copy.deepcopy(s)
        s['work'] = [chat('Chat %d' % i, 100.0 - i, 1.0, 0.0, 0.0) for i in range(7)] + \
            [chat('Small a', 10.0, 0.0, 1.0, 0.0), chat('Small b', 30.0, 0.0, 0.0, 1.0)]
        rows = usage.for_page(s, 7)['cuts']['work']
        self.assertEqual(rows[-1]['label'], '2 more')
        self.assertEqual(rows[-1]['who'], {'workflows': 0, 'helpers': 25, 'chat': 75})
        self.assertEqual(rows[0]['who'], {'workflows': 100, 'helpers': 0, 'chat': 0})

    def test_this_week_and_last_week_by_who_and_the_counts(self):
        last = {'totals': {'weight': 300.0}, 'who': [
            {'label': 'Your chat turns', 'detail': {'weight': 100.0}}, {'label': 'Helpers', 'detail': {'weight': 50.0}},
            {'label': 'Workflows', 'detail': {'weight': 150.0}}]}
        s, page = fixture_week(last=last)
        self.assertEqual(page['last_week'], {'weight': 300.0, 'who': {'chat': 100.0, 'helpers': 50.0, 'workflows': 150.0},
                                             'days': None})          # days: a summary without its days doesn't say
        who = dict((usage.WHO_LABEL[k], k) for k in usage.WHO)
        want = dict((who[r['label']], round(r['detail']['weight'], 2)) for r in s['who'])
        self.assertEqual(page['week']['who'], want)
        close(self, sum(page['week']['who'].values()), page['week']['weight'])
        t = s['totals']
        self.assertEqual(page['totals'], {'workflows': t['workflows'], 'agents': t['workflow_agents'],
                                          'helpers': t['helper_agents']})
        self.assertEqual(fixture_week(last={'totals': {'weight': 5.0}})[1]['last_week'], {'weight': 5.0, 'who': None, 'days': None})


# ---------------------------------------------------------------- one average for the chart and the words

class OneAverage(unittest.TestCase):
    def test_the_average_hour_is_one_with_use_and_never_the_hour_so_far(self):
        weights = [0] * 168
        for i in (10, 34, 58, 82, 106):
            weights[i] = 100
        weights[130] = 260
        weights[167] = 1                                         # the hour so far
        page = page_of(series={'days': days_of([0, 0, 0, 0, 100, 100, 760, 1]), 'hours': hours_of(weights)})
        got = insights.week_numbers(page)
        self.assertEqual(got['average']['hour'], round(760 / 6.0, 2))     # to the hundredth, as the payload gives a weight
        self.assertEqual(got['peak']['hour'], {'at': hours_of(weights)[130]['at'], 'weight': 260.0, 'times': 2.1})
        close(self, got['average']['day'], (100 + 100 + 760) / 3.0)   # the full days with use; today is never in it
        # three full days with use: spike()'s floor, so the busiest day is named
        self.assertEqual(got['peak']['day'], {'day': '2026-11-10', 'weight': 760.0, 'times': 2.4})

    def test_no_use_no_average_and_no_peak(self):
        got = insights.week_numbers(page_of(series={'days': days_of([0] * 8), 'hours': hours_of([0] * 168)}))
        self.assertEqual(got, {'average': {'hour': None, 'day': None}, 'peak': {'hour': None, 'day': None}})
        self.assertEqual(insights.week_numbers(page_of(series=None)), got)

    def test_the_payload_carries_it(self):
        s, page = fixture_week()
        self.assertEqual(set(page['average']), {'hour', 'day'})
        self.assertEqual(set(page['peak']), {'hour', 'day'})
        hours = page['series']['hours']
        used = [h['weight'] for h in hours[:-1] if h['weight'] > 0]
        close(self, page['average']['hour'], round(sum(used) / len(used), 2))
        self.assertEqual(page['peak']['hour']['at'], max(hours, key=lambda h: h['weight'])['at'])

    def test_the_spike_is_never_a_highlight(self):
        weights = [0] * 168
        for i in (10, 11, 34, 35, 58, 59):
            weights[i] = 100
        weights[154] = 1000
        page = page_of(series={'days': [], 'hours': hours_of(weights)})
        self.assertEqual(insights.find(page), [])
        self.assertEqual(insights.week_numbers(page)['peak']['hour']['times'], 4.4)


# ---------------------------------------------------------------- the highlights

def limit_page():
    week = {'since': insights.iso(NOW - 7 * D, UTC), 'until': insights.iso(NOW, UTC), 'weight': 1}

    def ev(t, kind, **f):
        e = dict.fromkeys(EVENT_KEYS)
        e.update(at=insights.iso(t, UTC), kind=kind, **f)
        return e
    return week, [ev(NOW - 2 * D + 120, 'limit', account='a@x.example', window='weekly', pct=95),
                  ev(NOW - 2 * D + 200, 'switch', **{'from': 'a@x.example', 'to': 'b@x.example',
                                                     'account': 'b@x.example', 'by': 'auto'})]


class Highlights(unittest.TestCase):
    def one(self, page, key):
        got = [x for x in insights.find(page) if x['key'] == key]
        return got[0] if got else None

    def said(self, page, key):
        x = self.one(page, key)
        return x and (x['figure'], x['text'])

    def test_every_highlight_has_a_figure_and_its_line(self):
        page = page_of(moved={'share': 43, 'to': 'Sonnet', 'from': ['Opus']})
        (x,) = insights.find(page)
        self.assertEqual(set(x), {'key', 'figure', 'text', 'numbers'})
        self.assertEqual((x['figure'], x['text']),
                         ('43%', 'of the week was helpers and workflow steps that ran on Opus because they chose no model.'))

    def test_the_moved_line_says_only_what_happened(self):
        self.assertEqual(self.said(page_of(moved={'share': 37, 'to': 'Sonnet', 'from': ['Opus', 'Fable']}), 'moved'),
                         ('37%', 'of the week was helpers and workflow steps that ran on Opus and Fable because they '
                                 'chose no model.'))
        self.assertEqual(self.said(page_of(moved={'share': 37, 'to': 'Sonnet'}), 'moved')[1],
                         'of the week was helpers and workflow steps that ran on their chat’s model because they chose no model.')
        self.assertIsNone(self.one(page_of(moved={'share': 19, 'to': 'Sonnet', 'from': ['Opus']}), 'moved'))
        for x in insights.find(page_of(moved={'share': 90, 'to': 'Sonnet', 'from': ['Opus']})):
            self.assertNotIn('Sonnet', x['figure'] + x['text'])        # never pushes you to Sonnet

    def test_the_context_line_tells_re_reading_from_saving(self):
        # weighed at today's prices: reads 0.05, writes 1.25, output 5
        tokens = {'input': 100000, 'output': 200000, 'cache_write': 1600000, 'cache_read': 70000000}
        # 100,000 + 1,000,000 + 2,000,000 + 3,500,000 = 6,600,000: reads 53%, storing 30%, replies 15%, input 2%
        x = self.one(page_of(tokens=tokens), 'context')
        self.assertEqual((x['figure'], x['text']), ('53%', 'of the use was re-reading context. Storing it was 30%, Claude’s replies 15%.'))
        self.assertEqual(x['numbers'], {'cache_read': 53, 'cache_write': 30, 'output': 15, 'input': 2})
        tokens['cache_read'] = 20000000          # 1,000,000 of 4,100,000: 24%, under the bar
        self.assertIsNone(self.one(page_of(tokens=tokens), 'context'))
        one_hour = dict(tokens, cache_read=70000000, cache_write_1h=1600000)       # every write for an hour: 2x
        x = self.one(page_of(tokens=one_hour), 'context')
        self.assertEqual(x['numbers']['cache_write'], 41)                  # 3,200,000 of 7,800,000

    def test_this_week_against_last_week_past_a_sixth(self):
        def said(this, last, now_who=None, then_who=None):
            return self.said(page_of(week={'weight': this, 'who': now_who}, last_week={'weight': last, 'who': then_who}),
                             'week')
        self.assertEqual(said(118, 100), ('18%', 'more than last week.'))
        self.assertEqual(said(115, 100), ('15%', 'more than last week.'))
        self.assertIsNone(said(114, 100))
        self.assertIsNone(said(86, 100))
        self.assertEqual(said(85, 100), ('15%', 'less than last week.'))
        self.assertEqual(said(250, 100), ('2.5×', 'last week’s use.'))
        self.assertIsNone(said(50, 0))
        self.assertIsNone(self.one(page_of(week={'weight': 50}, last_week=None), 'week'))

    def test_mostly_names_the_part_that_explains_most_of_the_change(self):
        now = {'workflows': 60.0, 'helpers': 40.0, 'chat': 20.0}
        then = {'workflows': 62.0, 'helpers': 18.0, 'chat': 20.0}       # +20: helpers +22, workflows -2
        self.assertEqual(self.said(page_of(week={'weight': 120, 'who': now}, last_week={'weight': 100, 'who': then}),
                                   'week'), ('20%', 'more than last week, mostly helpers.'))
        then = {'workflows': 50.0, 'helpers': 30.0, 'chat': 20.0}       # +20: workflows +10, helpers +10: neither most
        self.assertEqual(self.said(page_of(week={'weight': 120, 'who': now}, last_week={'weight': 100, 'who': then}),
                                   'week')[1], 'more than last week.')
        now = {'workflows': 20.0, 'helpers': 20.0, 'chat': 40.0}
        then = {'workflows': 60.0, 'helpers': 20.0, 'chat': 20.0}       # -20: workflows -40, chat +20
        self.assertEqual(self.said(page_of(week={'weight': 80, 'who': now}, last_week={'weight': 100, 'who': then}),
                                   'week'), ('20%', 'less than last week, mostly workflows.'))
        now = {'workflows': 10.0, 'helpers': 10.0, 'chat': 280.0}
        then = {'workflows': 10.0, 'helpers': 10.0, 'chat': 80.0}
        self.assertEqual(self.said(page_of(week={'weight': 300, 'who': now}, last_week={'weight': 100, 'who': then}),
                                   'week'), ('3×', 'last week’s use, mostly your own turns.'))

    def test_a_dominant_project(self):
        cuts = {'work': [{'label': 'Website', 'share': 90, 'note': ''}, {'label': 'Notes', 'share': 10, 'note': ''}]}
        self.assertEqual(self.said(page_of(cuts=cuts), 'project'), ('90%', 'of the week went to Website.'))
        cuts['work'][0]['share'] = 39
        self.assertIsNone(self.one(page_of(cuts=cuts), 'project'))

    def test_a_limit_and_the_switch_it_caused(self):
        week, evs = limit_page()
        self.assertEqual(self.said(page_of(week=week, events=evs), 'limit'),
                         ('95%', 'of the weekly limit on a@x.example, Monday 12 PM. Auto-switch moved you to b@x.example.'))

    def test_workflows_share_is_left_to_the_legend(self):
        who = [{'label': 'Workflows', 'share': 90, 'note': ''}]
        self.assertEqual(insights.find(page_of(cuts={'who': who})), [])

    def test_a_fixed_order_and_at_most_three(self):
        week, evs = limit_page()
        page = page_of(cuts={'work': [{'label': 'Website', 'share': 60, 'note': ''}]},
                       tokens={'input': 100000, 'output': 200000, 'cache_write': 1600000, 'cache_read': 70000000},
                       moved={'share': 37, 'to': 'Sonnet', 'from': ['Opus']},
                       week=dict(week, weight=130), last_week={'weight': 100}, events=evs)
        self.assertEqual([x['key'] for x in insights.find(page)], ['week', 'moved', 'limit'])     # context last
        page['last_week'] = None
        self.assertEqual([x['key'] for x in insights.find(page)], ['moved', 'limit', 'project'])
        page['moved'] = None
        self.assertEqual([x['key'] for x in insights.find(page)], ['limit', 'project', 'context'])

    def test_two_reads_give_the_same_three_in_the_same_order(self):
        s, page = fixture_week(last={'totals': {'weight': 1000.0}})
        first = insights.find(copy.deepcopy(page))
        page['at'] = page.get('at', 0) + 300                      # five minutes on, the same week
        self.assertEqual(insights.find(page), first)
        # near ties never reorder: the order is fixed, not a score
        a = page_of(moved={'share': 20, 'to': 'Sonnet', 'from': ['Opus']}, week={'weight': 1000}, last_week={'weight': 100})
        b = page_of(moved={'share': 99, 'to': 'Sonnet', 'from': ['Opus']}, week={'weight': 115}, last_week={'weight': 100})
        self.assertEqual([x['key'] for x in insights.find(a)], [x['key'] for x in insights.find(b)])

    def test_short_lines(self):
        week, evs = limit_page()
        page = page_of(cuts={'work': [{'label': 'A project with a long title that runs on and on and on', 'share': 60,
                                       'note': ''}]},
                       tokens={'input': 100000, 'output': 200000, 'cache_write': 1600000, 'cache_read': 70000000},
                       moved={'share': 37, 'to': 'Sonnet', 'from': ['Opus', 'Fable']},
                       week=dict(week, weight=1000, who={'chat': 900.0, 'helpers': 50.0, 'workflows': 50.0}),
                       last_week={'weight': 100, 'who': {'chat': 50.0, 'helpers': 25.0, 'workflows': 25.0}}, events=evs)
        for drop in ((), ('week',), ('week', 'moved'), ('week', 'moved', 'tokens')):
            p = dict(page, **dict((k, None) for k in drop))
            for x in insights.find(p):
                self.assertLessEqual(len(x['figure']), 5, x)
                self.assertLessEqual(len(x['text']), 100, x)

    def test_the_fixture_week(self):
        s, page = fixture_week()
        self.assertEqual([x['key'] for x in page['insights']], ['moved', 'project'])
        for x in page['insights']:
            self.assertRegex(x['figure'], r'^\d+(\.\d)?(%|×)$')


if __name__ == '__main__':
    unittest.main()
