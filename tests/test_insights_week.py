import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The /api/usage payload's week parts (insights.week), its insights, each rule and its words (insights.find),
# History's switches and limits, and the readings log, ~/.switchboard/readings.jsonl (insights.note_readings and
# read_readings). The fixture's numbers are worked out in test_usage_series.py.
import calendar
import copy
import fcntl
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)

from switchboard import core, history, insights, usage
from test_usage_series import (CHAT_9, D, FIXTURE, H, HELPERS_9, LA, M0, NOW, R0, R2, REAL, TOTAL,
                                  UTC, WEEK, WORKFLOWS_10, close)

# ---------------------------------------------------------------- the payload

def fixture_week(lines=(), readings=None, last=None):
    s = REAL(FIXTURE, NOW - 7 * D, NOW, tz=UTC, series_since=insights.series_start(NOW, UTC))
    page = dict(usage.for_page(s, 7), at=NOW)
    page.update(insights.week(s, NOW, tz=UTC, last=last, lines=lines, readings=readings))
    page['insights'] = insights.find(page)                  # as insights.read_week does for the server
    return s, page


DAY_KEYS = {'day', 'weight', 'who', 'projects', 'models', 'kinds'}
HOUR_KEYS = {'at', 'weight', 'who'}                              # an hour with no use
WENT_KEYS = {'projects', 'models', 'kinds', 'tokens', 'agents', 'workflows', 'tools'}    # what one with use adds
EVENT_KEYS = {'at', 'kind', 'from', 'to', 'account', 'why', 'by', 'window', 'pct'}
READING_KEYS = {'at', 'five', 'week', 'models'}
WORKFLOW_KEYS = {'name', 'chat', 'share', 'weight', 'agents', 'phases'}
NEW_KEYS = {'week', 'series', 'last_week', 'tokens', 'workflows', 'events', 'readings', 'insights',
            'totals', 'average', 'peak'}                         # the last three: totals, average, peak


class Payload(unittest.TestCase):
    def test_the_series_start_is_local_midnight_seven_days_before_today(self):
        self.assertEqual(insights.series_start(NOW, UTC), calendar.timegm((2026, 11, 4, 0, 0, 0)))
        if LA is not None:          # Nov 8, after the change back: Nov 1 began at 00:00 PDT (07:00 UTC)
            self.assertEqual(insights.series_start(calendar.timegm((2026, 11, 8, 20, 0, 0)), LA),
                             calendar.timegm((2026, 11, 1, 7, 0, 0)))

    def test_eight_days_and_168_hours(self):
        s, page = fixture_week()
        days, hours = page['series']['days'], page['series']['hours']
        self.assertEqual([d['day'] for d in days], ['2026-11-%02d' % n for n in range(4, 12)])
        self.assertEqual(len(hours), 168)
        self.assertEqual(hours[0]['at'], '2026-11-04T13:00:00+00:00')
        self.assertEqual(hours[-1]['at'], '2026-11-11T12:00:00+00:00')            # the hour so far
        for d in days:
            self.assertEqual(set(d), DAY_KEYS)
        for h in hours:
            self.assertEqual(set(h), HOUR_KEYS | WENT_KEYS if h['weight'] > 0 else HOUR_KEYS)
        weights = dict((d['day'], d['weight']) for d in days)
        close(self, weights['2026-11-09'], M0 + R0)
        close(self, weights['2026-11-10'], TOTAL)
        close(self, weights['2026-11-11'], R2)
        close(self, sum(weights.values()), WEEK)
        close(self, sum(h['weight'] for h in hours), WEEK)                        # every request is after 13:00 Nov 4
        hour = dict((h['at'], h) for h in hours)
        close(self, hour['2026-11-10T09:00:00+00:00']['weight'], CHAT_9 + HELPERS_9)
        close(self, hour['2026-11-10T10:00:00+00:00']['who']['workflows'], WORKFLOWS_10)

    def test_the_same_five_projects_every_day_then_other(self):
        s, page = fixture_week()
        labels = [[p['label'] for p in d['projects']] for d in page['series']['days']]
        self.assertEqual(labels, [['Garden planner v2', 'Weekly notes', 'recipes', 'Other']] * 8)
        nov9 = page['series']['days'][5]
        self.assertEqual(nov9['day'], '2026-11-09')
        self.assertEqual([round(p['weight'], 2) for p in nov9['projects']], [round(M0, 2), 0, round(R0, 2), 0])
        for d in page['series']['days']:
            close(self, sum(p['weight'] for p in d['projects']), d['weight'])
        # More than five: the rest go to Other, every day.
        many = {'series': {'since': 0, 'until': NOW, 'hours': [], 'days': [
            {'day': '2026-11-10', 'weight': 21.0, 'who': {}, 'models': {}, 'kinds': {},
             'projects': dict(('chat %d' % i, float(i)) for i in range(1, 7))}]},
                'totals': {'weight': 21.0, 'tokens': {}}, 'workflows': [], 'window': {'since': NOW - 7 * D, 'until': NOW}}
        got = insights.week(many, NOW, tz=UTC)['series']['days']
        self.assertEqual([p['label'] for p in got[0]['projects']], ['chat 6', 'chat 5', 'chat 4', 'chat 3', 'chat 2', 'Other'])
        self.assertEqual(got[6]['projects'][-1], {'label': 'Other', 'weight': 1.0})

    def test_week_tokens_workflows_and_last_week(self):
        s, page = fixture_week(last={'totals': {'weight': 12345.678}})
        self.assertEqual(page['week'], {'since': '2026-11-04T12:30:00+00:00', 'until': '2026-11-11T12:30:00+00:00',
                                        'weight': round(WEEK, 2), 'who': page['week']['who']})   # who: test_insights_prices_and_highlights
        self.assertEqual(page['tokens'], {'input': 469, 'output': 2591, 'cache_write': 8912, 'cache_read': 56113,
                                          'cache_write_1h': 8912})
        close(self, usage.weight(page['tokens']), WEEK)
        self.assertEqual(page['last_week'], {'weight': 12345.68, 'who': None, 'days': None})
        (wf,) = page['workflows']
        self.assertEqual(set(wf), WORKFLOW_KEYS)
        self.assertEqual((wf['name'], wf['chat'], wf['share'], wf['agents'], wf['phases']),
                         ('garden-build', 'Garden planner v2', 41, 2, ['Build', 'Verify']))
        self.assertEqual(fixture_week()[1]['last_week'], None)                    # not read: not known, never 0

    def test_the_whole_payload_is_plain_json(self):
        s, page = fixture_week()
        self.assertEqual(json.loads(json.dumps(page)), page)
        self.assertLessEqual(NEW_KEYS, set(page))

    def test_what_the_fixture_week_says(self):
        s, page = fixture_week()
        got = insights.find(page)
        # H1 and W2 named no model and ran on their Opus chat's (6105 + 4208 of 34053.65 at today's prices)
        self.assertEqual([x['key'] for x in got], ['moved', 'project'])
        self.assertEqual([x['figure'] + ' ' + x['text'] for x in got], [
            '30% of the week was helpers and workflow steps that ran on Opus because they chose no model.',
            '87% of the week went to Garden planner v2.'])
        # the spike is the chart's: named in place from page['peak'], said in words by insights.spike
        self.assertEqual(insights.spike(page)[1], 'Tuesday 9 AM was your busiest hour: 4× an average one.')
        self.assertEqual(page['peak']['hour']['at'], '2026-11-10T09:00:00+00:00')


class Events(unittest.TestCase):
    LINES = [
        {'t': NOW - 9 * D, 'kind': 'switch', 'text': 'x', 'from': 'a@x.example', 'to': 'b@x.example', 'by': 'auto'},
        {'t': NOW - 2 * D, 'kind': 'limit', 'text': 'x', 'account': 'a@x.example', 'window': 'weekly', 'pct': 95,
         'line': 95, 'reason': 'weekly at 95%'},
        {'t': NOW - 2 * D + 30, 'kind': 'switch', 'text': 'x', 'from': 'a@x.example', 'to': 'b@x.example',
         'account': 'b@x.example', 'by': 'auto', 'reason': 'weekly at 95%, refills first'},
        {'t': NOW - 1 * D, 'kind': 'limit', 'text': 'x', 'account': 'b@x.example', 'window': '5-hour',
         'reason': '5-hour refilled'},
        {'t': NOW - 3600, 'kind': 'setting', 'text': 'Notifications: on'},
        {'t': NOW - 60, 'kind': 'switch', 'text': 'x', 'to': 'a@x.example', 'account': 'a@x.example', 'by': 'you'},
        {'t': NOW + 60, 'kind': 'switch', 'text': 'x', 'to': 'b@x.example', 'by': 'you'},
    ]

    def test_switches_and_limits_reached_in_the_series_span(self):
        got = insights.events(self.LINES, insights.series_start(NOW, UTC), NOW, UTC)
        self.assertEqual([(e['kind'], e['at']) for e in got], [('limit', '2026-11-09T12:30:00+00:00'),
                                                               ('switch', '2026-11-09T12:30:30+00:00'),
                                                               ('switch', '2026-11-11T12:29:00+00:00')])
        for e in got:
            self.assertEqual(set(e), EVENT_KEYS)
        limit, auto, mine = got
        self.assertEqual((limit['account'], limit['window'], limit['pct'], limit['why'], limit['from']),
                         ('a@x.example', 'weekly', 95, 'weekly at 95%', None))
        self.assertEqual((auto['from'], auto['to'], auto['account'], auto['by'], auto['why']),
                         ('a@x.example', 'b@x.example', 'b@x.example', 'auto', 'weekly at 95%, refills first'))
        self.assertEqual((mine['by'], mine['why'], mine['from']), ('you', None, None))

    def test_read_from_history(self):
        tmp = tempfile.mkdtemp(prefix='switchboard-week-')
        self.addCleanup(shutil.rmtree, tmp, True)
        with mock.patch.dict(os.environ, {'SWITCHBOARD_HOME': tmp}):
            history.record('limit', 'a@x.example reached its weekly switch line: 95%', t=NOW - 3600,
                           account='a@x.example', window='weekly', pct=95, line=95, reason='weekly at 95%')
            history.record('switch', 'Switched Claude Code to b@x.example', t=NOW - 3500, to='b@x.example',
                           account='b@x.example', by='auto', reason='weekly at 95%, refills first', **{'from': 'a@x.example'})
            got = insights.read_events(NOW - D, NOW, UTC)
        self.assertEqual([(e['kind'], e['account']) for e in got], [('limit', 'a@x.example'), ('switch', 'b@x.example')])


# ---------------------------------------------------------------- the insights: each rule and its words

def hours_of(weights, start=NOW - 167 * H - 30 * 60):
    return [{'at': insights.iso(start + i * H, UTC), 'weight': float(x), 'who': {'chat': float(x), 'helpers': 0.0,
                                                                                  'workflows': 0.0}}
            for i, x in enumerate(weights)]


def days_of(weights, last='2026-11-11'):
    end = datetime.strptime(last, '%Y-%m-%d')
    return [{'day': (end - timedelta(days=len(weights) - 1 - i)).strftime('%Y-%m-%d'), 'weight': float(x)}
            for i, x in enumerate(weights)]


def page_of(**parts):
    p = {'days': 7, 'cuts': {}, 'moved': None, 'series': {'days': [], 'hours': []}, 'week': None, 'last_week': None,
         'tokens': None, 'events': []}
    p.update(parts)
    return p


class Rules(unittest.TestCase):
    # Each highlight is a figure and the rest of its line; the chart names the spike, so the
    # spike rule (insights.spike) is never a highlight; the rest keep a fixed order. test_insights_prices_and_highlights.py has the rest.
    def keys(self, page):
        return [x['key'] for x in insights.find(page)]

    def one(self, page, key):
        if key in ('hour', 'day'):                # the spike: said on the chart, not in the highlights
            got = insights.spike(page)
            return got and got[0] == key and {'key': got[0], 'text': got[1], 'numbers': got[2]} or None
        got = [x for x in insights.find(page) if x['key'] == key]
        return got[0] if got else None

    def line(self, x):
        return x and (x['figure'] + ' ' + x['text'])

    def test_the_busiest_hour(self):
        weights = [0] * 168                       # hour 0 is Nov 4, 1 PM (UTC); hour 167, Nov 11 noon, is the one so far
        for i in (10, 11, 34, 35, 58, 59):
            weights[i] = 100
        weights[154] = 1000                       # Tuesday Nov 10, 11 PM
        got = self.one(page_of(series={'days': [], 'hours': hours_of(weights)}), 'hour')
        # 6 hours of 100 and one of 1000: an average hour with any use is 1600/7, and 1000 is 4.4 times that
        self.assertEqual(got['text'], 'Tuesday 11 PM was your busiest hour: 4× an average one.')
        self.assertEqual(got['numbers'], {'at': '2026-11-10T23:00:00+00:00', 'weight': 1000.0, 'average': 228.57,
                                          'times': 4.4})
        weights[154] = 280                        # 280 / (880/7) = 2.2: still a spike
        self.assertEqual(self.one(page_of(series={'days': [], 'hours': hours_of(weights)}), 'hour')['text'],
                         'Tuesday 11 PM was your busiest hour: 2.2× an average one.')
        weights[154] = 200                        # 200 / (800/7) = 1.75: not one
        self.assertIsNone(self.one(page_of(series={'days': [], 'hours': hours_of(weights)}), 'hour'))

    def test_the_hour_so_far_is_never_in_the_average(self):
        weights = [0] * 168
        for i in (10, 34, 58, 82, 106):
            weights[i] = 100
        weights[130] = 260
        weights[167] = 1                          # the hour so far: counted, it would make 2.4x of 2.1x
        got = self.one(page_of(series={'days': [], 'hours': hours_of(weights)}), 'hour')
        self.assertEqual(got['numbers']['times'], 2.1)                 # 260 / (760/6)
        weights[106] = 0                          # five hours with use: too few to say what an average one is
        self.assertIsNone(self.one(page_of(series={'days': [], 'hours': hours_of(weights)}), 'hour'))

    def test_too_few_hours_says_the_busiest_day_instead(self):
        weights = [0] * 168
        weights[20], weights[130] = 100, 500
        hours = hours_of(weights)
        self.assertIsNone(self.one(page_of(series={'days': [], 'hours': hours}), 'hour'))
        days = days_of([100, 120, 0, 80, 500, 100, 0, 30])            # Nov 4 .. Nov 11 (today, so far)
        got = self.one(page_of(series={'days': days, 'hours': hours}), 'day')
        # an average full day with use: (100 + 120 + 80 + 500 + 100) / 5 = 180; Sunday Nov 8 is 2.8 times that
        self.assertEqual(got['text'], 'Sunday was your busiest day: 2.8× an average one.')
        self.assertEqual(got['numbers'], {'day': '2026-11-08', 'weight': 500.0, 'average': 180.0, 'times': 2.8})
        days = days_of([600, 120, 0, 80, 100, 100, 0, 30])
        self.assertEqual(self.one(page_of(series={'days': days, 'hours': hours}), 'day')['text'],
                         'Nov 4 was your busiest day: 3× an average one.')   # a week ago: by its date

    def test_a_dominant_project(self):
        cuts = {'work': [{'label': 'Website', 'share': 46, 'note': ''}, {'label': 'Notes', 'share': 30, 'note': ''},
                         {'label': '3 more', 'share': 24, 'note': ''}]}
        got = self.one(page_of(cuts=cuts), 'project')
        self.assertEqual((self.line(got), got['numbers']), ('46% of the week went to Website.', {'label': 'Website', 'share': 46}))
        cuts['work'][0]['share'] = 39
        self.assertIsNone(self.one(page_of(cuts=cuts), 'project'))
        cuts['work'] = [{'label': 'Website', 'share': 25, 'note': ''}, {'label': 'Website', 'share': 20, 'note': ''},
                        {'label': '12 more', 'share': 55, 'note': ''}]
        self.assertEqual(self.line(self.one(page_of(cuts=cuts), 'project')), '45% of the week went to Website.')

    def test_who_did_the_work(self):
        # the chart's legend gives who did the work ("Workflows 76%"), so no highlight repeats it
        who = [{'label': 'Workflows', 'share': 76, 'note': ''}, {'label': 'Helpers', 'share': 14, 'note': ''},
               {'label': 'You, in chat', 'share': 10, 'note': ''}]
        self.assertIsNone(self.one(page_of(cuts={'who': who}), 'workflows'))
        self.assertEqual(insights.find(page_of(cuts={'who': who})), [])

    def test_context_re_reading(self):
        # a split of 43% cache reads, 36% cache writes, 20% output, 1% input (of 1,000,000 weighted)
        # (at older weights a cache read weighed twice as much as today's: 8,600,000 reads are the same 430,000)
        tokens = {'input': 10000, 'output': 40000, 'cache_write': 288000, 'cache_read': 8600000}
        got = self.one(page_of(tokens=tokens), 'context')
        self.assertEqual(self.line(got), '43% of the use was re-reading context. Storing it was 36%, Claude’s replies 20%.')
        self.assertEqual(got['numbers'], {'cache_read': 43, 'cache_write': 36, 'output': 20, 'input': 1})
        self.assertIsNone(self.one(page_of(tokens={'input': 1000000, 'output': 0, 'cache_write': 0,
                                                    'cache_read': 8000000}), 'context'))   # 29%: under its 30%
        self.assertIsNone(self.one(page_of(tokens={'input': 0, 'output': 0, 'cache_write': 0, 'cache_read': 0}),
                                   'context'))

    def test_what_sonnet_for_helpers_would_move(self):
        self.assertEqual(self.line(self.one(page_of(moved={'share': 37, 'to': 'Sonnet', 'from': ['Opus']}), 'moved')),
                         '37% of the week was helpers and workflow steps that ran on Opus because they chose no model.')
        self.assertIsNone(self.one(page_of(moved={'share': 19, 'to': 'Sonnet'}), 'moved'))
        self.assertIsNone(self.one(page_of(moved=None), 'moved'))

    def test_this_week_against_last_week(self):
        def said(this, last):
            got = self.one(page_of(week={'weight': this}, last_week={'weight': last}), 'week')
            return self.line(got)
        self.assertEqual(said(131, 100), '31% more than last week.')
        self.assertEqual(said(75, 100), '25% less than last week.')
        self.assertEqual(said(250, 100), '2.5× last week’s use.')
        self.assertEqual(said(120, 100), '20% more than last week.')
        self.assertEqual(said(115, 100), '15% more than last week.')     # a sixth more or less is worth a line
        self.assertIsNone(said(114, 100))
        self.assertIsNone(said(86, 100))
        self.assertIsNone(said(50, 0))                     # nothing last week: no comparison
        self.assertIsNone(self.one(page_of(week={'weight': 50}, last_week=None), 'week'))
        self.assertEqual(self.one(page_of(week={'weight': 131}, last_week={'weight': 100}), 'week')['numbers'],
                         {'change': 31, 'weight': 131, 'last_week': 100, 'mostly': None})

    def test_a_limit_and_the_switch_it_caused(self):
        week = {'since': insights.iso(NOW - 7 * D, UTC), 'until': insights.iso(NOW, UTC), 'weight': 1}

        def ev(t, kind, **f):
            e = dict.fromkeys(EVENT_KEYS)
            e.update(at=insights.iso(t, UTC), kind=kind, **f)
            return e
        limit = ev(NOW - 2 * D + 120, 'limit', account='a@x.example', window='weekly', pct=95)
        switch = ev(NOW - 2 * D + 200, 'switch', **{'from': 'a@x.example', 'to': 'b@x.example', 'account': 'b@x.example',
                                                    'by': 'auto'})
        got = self.one(page_of(week=week, events=[limit, switch]), 'limit')
        self.assertEqual(self.line(got), '95% of the weekly limit on a@x.example, Monday 12 PM. Auto-switch moved you to b@x.example.')
        self.assertEqual(got['numbers'], {'at': '2026-11-09T12:33:20+00:00', 'window': 'weekly', 'pct': 95,
                                          'from': 'a@x.example', 'to': 'b@x.example', 'count': 1})
        for other in (dict(switch, by='you'), dict(switch, **{'from': 'c@x.example'}),
                      dict(switch, at=insights.iso(NOW - 2 * D + 120 + 16 * 60, UTC))):
            self.assertIsNone(self.one(page_of(week=week, events=[limit, other]), 'limit'), other)
        early = dict(switch, at=insights.iso(NOW - 2 * D + 120 - 5 * 60, UTC))      # the switch was seen first
        self.assertIsNotNone(self.one(page_of(week=week, events=[limit, early]), 'limit'))

    def test_at_most_three_ranked_by_how_unusual_then_a_fixed_order(self):
        weights = [0] * 168
        for i in range(100, 160):
            weights[i] = 100
        weights[130] = 1000                                         # 1000 / 115 = 8.7x: 4.3 times its bar of 2
        page = page_of(series={'days': [], 'hours': hours_of(weights)},
                       cuts={'work': [{'label': 'Website', 'share': 60, 'note': ''}],               # 1.5
                             'who': [{'label': 'Workflows', 'share': 75, 'note': ''}]},             # 1.5
                       tokens={'input': 10000, 'output': 40000, 'cache_write': 288000, 'cache_read': 4300000},  # 1.58
                       moved={'share': 37, 'to': 'Sonnet'},                                         # 1.85
                       week={'weight': 130}, last_week={'weight': 100})                             # 1.5
        # the spike is the chart's; the rest in a fixed order (week, moved, limit, project, context: the
        # context line last, as it passes in nearly every week)
        self.assertEqual(self.keys(page), ['week', 'moved', 'project'])
        page['tokens']['cache_read'] *= 2                                             # at today's prices: 43% re-reading
        self.assertEqual(self.keys(page), ['week', 'moved', 'project'])
        page['moved'] = None
        self.assertEqual(self.keys(page), ['week', 'project', 'context'])
        self.assertEqual(insights.find(copy.deepcopy(page)), insights.find(page))
        self.assertEqual(insights.find(page_of()), [])

    def test_the_words_are_fixed_words_numbers_and_names(self):
        s, page = fixture_week()
        texts = [x['text'] for x in insights.find(page)]
        patterns = [r'^of the week was helpers and workflow steps that ran on [A-Z][a-z]+( and [A-Z][a-z]+)? '
                    r'because they chose no model\.$',
                    r'^of the week went to .+\.$']
        self.assertEqual(len(texts), len(patterns))
        for x, pattern in zip(insights.find(page), patterns):
            self.assertRegex(x['figure'], r'^\d+%$')
            self.assertRegex(x['text'], pattern)
            self.assertLessEqual(len(x['text']), 100, x['text'])
        (k, said, numbers) = insights.spike(page)                                    # the chart's, in words
        self.assertRegex(said, r'^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|[A-Z][a-z]{2} \d+,) \d+ '
                               r'[AP]M was your busiest hour: [\d.]+× an average one\.$')


# ---------------------------------------------------------------- the readings log

class Readings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-week-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = mock.patch.dict(os.environ, {'SWITCHBOARD_HOME': os.path.join(self.tmp, 'data')})
        env.start()
        self.addCleanup(env.stop)
        mem = mock.patch.dict(insights.READINGS, {'last': {}, 'loaded': False, 'pruned_at': None})
        mem.start()
        self.addCleanup(mem.stop)
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = False
        self.path = core.data_path('readings.jsonl')

    def lines(self):
        with open(self.path) as f:
            return [json.loads(x) for x in f.read().splitlines()]

    def restart(self):
        insights.READINGS.update(last={}, loaded=False, pruned_at=None)

    def test_a_line_only_when_a_number_changed(self):
        t = NOW
        took = [('a@x.example', t, 40.0, 70, {'Fable': 80.0}), ('b@x.example', t, None, 91.5, {})]
        self.assertEqual(insights.note_readings(took, now=t), 2)
        self.assertEqual(insights.note_readings([('a@x.example', t + 60, 40, 70.0, {'Fable': 80})], now=t + 60), 0)
        self.assertEqual(insights.note_readings([('a@x.example', t + 120, 41, 70, {'Fable': 80})], now=t + 120), 1)
        self.restart()                                       # the page started again: it reads the last lines first
        self.assertEqual(insights.note_readings([('a@x.example', t + 180, 41, 70, {'Fable': 80})], now=t + 180), 0)
        self.assertEqual(insights.note_readings([('b@x.example', t + 180, None, 91.5, {})], now=t + 180), 0)
        self.assertEqual(self.lines(), [
            {'t': t, 'account': 'a@x.example', 'five': 40, 'week': 70, 'models': {'Fable': 80}},
            {'t': t, 'account': 'b@x.example', 'five': None, 'week': 91.5, 'models': {}},
            {'t': t + 120, 'account': 'a@x.example', 'five': 41, 'week': 70, 'models': {'Fable': 80}}])

    def test_owner_only_and_appended_whole(self):
        insights.note_readings([('a@x.example', NOW, 1, 2, {})], now=NOW)
        with open(self.path, 'rb') as f:
            first = f.read()
        insights.note_readings([('a@x.example', NOW + 60, 3, 4, {})], now=NOW + 60)
        with open(self.path, 'rb') as f:
            both = f.read()
        self.assertTrue(both.startswith(first) and both.endswith(b'\n'))
        self.assertEqual(oct(os.stat(self.path).st_mode & 0o777), '0o600')

    def test_pruned_to_eight_days(self):
        old, new = NOW - 8 * D - 60, NOW - 8 * D + 60
        insights.note_readings([('a@x.example', old, 1, 1, {}), ('b@x.example', new, 2, 2, {})], now=old)
        self.assertEqual(len(self.lines()), 2)
        insights.READINGS['pruned_at'] = NOW - insights.READINGS_PRUNE_EVERY        # due again
        insights.note_readings([('a@x.example', NOW, 3, 3, {})], now=NOW)
        self.assertEqual([(x['account'], x['t']) for x in self.lines()], [('b@x.example', new), ('a@x.example', NOW)])

    def test_capped(self):
        with mock.patch.object(insights, 'READINGS_CAP', 2000):
            for i in range(60):
                insights.note_readings([('a@x.example', NOW + i, i, i, {})], now=NOW + i)
                self.assertLessEqual(os.path.getsize(self.path), 2000)
        kept = self.lines()
        self.assertLess(len(kept), 60)
        self.assertEqual(kept[-1]['five'], 59)                                       # the newest stay
        self.assertEqual([x['five'] for x in kept], list(range(60 - len(kept), 60)))

    def test_a_bad_line_is_skipped_and_goes_at_the_next_prune(self):
        insights.note_readings([('a@x.example', NOW - 60, 1, 1, {})], now=NOW - 60)
        with open(self.path, 'a') as f:
            f.write('not json\n[1, 2]\n{"t": "soon", "account": "a@x.example"}\n{"t": %r}\n' % NOW)
        insights.note_readings([('a@x.example', NOW - 30, 2, 2, {})], now=NOW - 30)
        with open(self.path, 'a') as f:
            f.write('{"t": %r, "account": "a@x.example", "five": 9' % NOW)                 # torn: still being written
        got = insights.read_readings(NOW - D, NOW)
        self.assertEqual([(x['five'], x['week']) for x in got['a@x.example']], [(1, 1), (2, 2)])
        self.restart()
        insights.READINGS['pruned_at'] = NOW - insights.READINGS_PRUNE_EVERY
        insights.note_readings([('a@x.example', NOW, 3, 3, {})], now=NOW)
        with open(self.path) as f:
            text = f.read()
        self.assertNotIn('not json', text)
        self.assertEqual([x['five'] for x in self.lines()], [1, 2, 3])

    def test_never_blocks_and_never_raises(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd = os.open(self.path + '.lock', os.O_WRONLY | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)                     # another writer holds it: this tick's lines wait
        t = time.monotonic()
        self.assertEqual(insights.note_readings([('a@x.example', NOW, 1, 1, {})], now=NOW), 0)
        self.assertLess(time.monotonic() - t, 1)
        os.close(fd)
        self.assertEqual(insights.note_readings([('a@x.example', NOW, 1, 1, {})], now=NOW), 1)
        blocker = os.path.join(self.tmp, 'file')
        with open(blocker, 'w') as f:
            f.write('x')
        with mock.patch.dict(os.environ, {'SWITCHBOARD_HOME': os.path.join(blocker, 'data')}):
            self.restart()
            self.assertEqual(insights.note_readings([('a@x.example', NOW, 5, 5, {})], now=NOW), 0)
            self.assertEqual(insights.read_readings(NOW - D, NOW), {})
        self.assertEqual(insights.note_readings(None, now=NOW), 0)
        self.assertEqual(insights.note_readings([('a@x.example', float('nan'), 1, 1, {})], now=NOW), 0)

    def test_the_demo_writes_nothing(self):
        core.DEMO = True
        self.assertEqual(insights.note_readings([('a@x.example', NOW, 1, 1, {})], now=NOW), 0)
        self.assertFalse(os.path.exists(self.path))

    def test_what_the_page_gets(self):
        rows = [{'t': NOW - 3600 + i * 60, 'five': float(i), 'week': 50.0, 'models': {}} for i in range(30)]
        rows += [{'t': NOW - 1700, 'five': 95.0, 'week': 60.0, 'models': {'Fable': 81}},
                 {'t': NOW - 1690, 'five': 2.0, 'week': 60.0, 'models': {'Fable': 81}}]      # a peak, then the refill
        got = insights.readings_view({'a@x.example': rows}, UTC)['a@x.example']
        for r in got:
            self.assertEqual(set(r), READING_KEYS)
        self.assertEqual([(r['at'], r['five']) for r in got], [
            ('2026-11-11T11:44:00+00:00', 14.0),          # the last reading in each quarter hour
            ('2026-11-11T11:59:00+00:00', 29.0),
            ('2026-11-11T12:01:40+00:00', 95.0),          # the one before a drop is kept: the peak
            ('2026-11-11T12:01:50+00:00', 2.0)])
        self.assertEqual(got[-1]['models'], {'Fable': 81})

    def test_taken_from_the_state_as_the_tick_reads_it(self):
        st = core.blank_state()
        for email, usage_at, u in (('a@x.example', NOW, {'session': {'pct': 40.04}, 'weekly': {'pct': 70},
                                                          'scoped': [{'name': 'Fable', 'pct': 80}]}),
                                   ('b@x.example', NOW - 10, {'session': None, 'weekly': {'pct': None}, 'scoped': []}),
                                   ('c@x.example', None, {'session': {'pct': 1}, 'weekly': {'pct': 2}}),
                                   ('d@x.example', NOW, None)):
            st['order'].append(email)
            st['accounts'][email] = {'email': email, 'usage': u, 'usage_at': usage_at}
        self.assertEqual(insights.take_readings(st), [('a@x.example', NOW, 40.0, 70, {'Fable': 80})])


if __name__ == '__main__':
    unittest.main()
