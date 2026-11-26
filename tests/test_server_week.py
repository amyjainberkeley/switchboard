import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The week's data on the page: /api/usage read in the background (server.read_usage: the week, then the week
# before), the tick's readings log line, the demo's made-up week, and `uninstall --all`.
import json
import os
import re
import shutil
import sys
import time
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from switchboard import core, demo, history, insights, server, usage
import test_app
import test_insights_week as week_tests
from test_insights_week import EVENT_KEYS, NEW_KEYS, fixture_week, page_of
from test_usage_series import D, FIXTURE, H, NOW, REAL, WEEK, close, set_tz


class FromTheTick(test_app.Sandbox):
    def setUp(self):
        super().setUp()
        mem = mock.patch.dict(insights.READINGS, {'last': {}, 'loaded': False, 'pruned_at': None})
        mem.start()
        self.addCleanup(mem.stop)

    def test_the_tick_writes_what_it_read(self):
        self.rows = [test_app.row(1, test_app.A, True, 40, five=12), test_app.row(2, test_app.B, False, 20)]
        server.tick()
        server.tick()                                       # the same numbers again: no new line
        with open(core.data_path('readings.jsonl')) as f:
            got = [json.loads(x) for x in f.read().splitlines()]
        self.assertEqual([(x['account'], x['five'], x['week']) for x in got], [(test_app.A, 12, 40), (test_app.B, 0, 20)])
        self.rows[0] = test_app.row(1, test_app.A, True, 41, five=13)
        server.tick()
        with open(core.data_path('readings.jsonl')) as f:
            self.assertEqual(len(f.read().splitlines()), 3)

    def test_a_failing_log_never_stops_the_tick(self):
        self.rows = [test_app.row(1, test_app.A, True, 40)]
        self.patch(insights, 'note_readings', mock.Mock(side_effect=RuntimeError('broken')))
        server.tick()                                       # does not raise
        self.assertEqual(server.view()['accounts'][0]['email'], test_app.A)


# ---------------------------------------------------------------- /api/usage, read in the background

class TheServer(test_app.Sandbox):
    def setUp(self):
        super().setUp()
        set_tz(self, 'UTC')
        os.rmdir(self.projects)
        shutil.copytree(FIXTURE, self.projects)
        clock = types.SimpleNamespace(**dict((k, getattr(time, k)) for k in dir(time) if not k.startswith('_')))
        clock.time = lambda: NOW
        self.patch(server, 'time', clock)
        self.calls = []

        def spy(root, since, until, **k):
            self.calls.append((since, until, k.get('series_since')))
            return REAL(root, since, until, **k)
        self.patch(usage, 'summarize', spy)

    def test_the_week_and_the_week_before_two_reads(self):
        server.read_usage()
        start = insights.series_start(NOW, None)
        self.assertEqual(self.calls, [(NOW - 7 * D, NOW, start), (NOW - 14 * D, NOW - 7 * D, None)])
        page = server.usage_view()
        self.assertEqual(page['state'], 'ready')
        self.assertLessEqual(NEW_KEYS | {'cuts', 'moved', 'days', 'at'}, set(page))
        self.assertEqual(len(page['series']['days']), 8)
        self.assertEqual(len(page['series']['hours']), 168)
        self.assertEqual(page['last_week'], {'weight': 0, 'who': {'chat': 0.0, 'helpers': 0.0, 'workflows': 0.0}, 'days': 0})
        close(self, page['week']['weight'], WEEK)
        self.assertEqual([x['key'] for x in page['insights']], ['moved', 'project'])    # the spike is the chart's
        self.assertEqual(insights._at(page['peak']['hour']['at']).timestamp(), NOW - D - 3.5 * H)      # Nov 10, 9 AM UTC
        self.assertEqual(page['events'], [])
        self.assertEqual(page['readings'], {})
        self.assertEqual(json.loads(server.to_json(page)), page)

    def test_events_and_readings_come_from_their_files(self):
        history.record('limit', 'a@x.example reached its 5-hour switch line: 91%', t=NOW - 3 * H, account='a@x.example',
                       window='5-hour', pct=91, line=90, reason='5-hour at 91%')
        history.record('switch', 'Switched Claude Code to b@x.example', t=NOW - 3 * H + 40, to='b@x.example',
                       account='b@x.example', by='auto', reason='5-hour at 91%, refills first', **{'from': 'a@x.example'})
        with mock.patch.dict(insights.READINGS, {'last': {}, 'loaded': False, 'pruned_at': None}):
            insights.note_readings([('a@x.example', NOW - 4 * H, 80, 50, {}), ('a@x.example', NOW - 3 * H, 91, 51, {})],
                                   now=NOW)
        server.read_usage()
        page = server.usage_view()
        self.assertEqual([(e['kind'], e['account']) for e in page['events']], [('limit', 'a@x.example'),
                                                                               ('switch', 'b@x.example')])
        self.assertEqual([r['five'] for r in page['readings']['a@x.example']], [80, 91])
        got = insights.find(page_of(week=page['week'], events=page['events']))        # the limit rule alone
        self.assertEqual([x['figure'] + ' ' + x['text'] for x in got],
                         ['91% of the 5-hour limit on a@x.example, Wednesday 9 AM. Auto-switch moved you to b@x.example.'])

    def test_a_week_part_that_fails_leaves_the_rest(self):
        def broken(root, since, until, **k):
            if 'series_since' not in k:
                raise ValueError('last week is broken')
            return REAL(root, since, until, **k)
        self.patch(usage, 'summarize', broken)
        server.read_usage()
        page = server.usage_view()
        self.assertEqual(page['last_week'], None)
        self.assertTrue(page['cuts'])
        self.assertEqual(len(page['series']['hours']), 168)

    def test_a_failed_read_is_marked_failed(self):
        self.patch(usage, 'summarize', mock.Mock(side_effect=ValueError('broken')))
        server.read_usage()
        self.assertEqual(server.usage_view(), {'days': 7, 'cuts': {}, 'moved': None, 'failed': True, 'state': 'ready'})


# ---------------------------------------------------------------- the demo

def shape(x):
    """The payload's shape: dict keys and value types, lists by their first item."""
    if isinstance(x, dict):
        return dict((k, shape(v)) for k, v in x.items())
    if isinstance(x, list):
        return [shape(x[0])] if x else []
    if isinstance(x, bool) or x is None:
        return type(x).__name__
    if isinstance(x, (int, float)):
        return 'number'
    return type(x).__name__


class TheDemo(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = True
        demo.STATE.clear()
        demo.FACTS.clear()
        self.addCleanup(demo.STATE.clear)
        self.addCleanup(demo.FACTS.clear)

    def test_the_same_shape_as_the_real_payload(self):
        page = server.usage_view()
        _, real = fixture_week(lines=week_tests.Events.LINES, readings={'a@x.example': [{'t': NOW - 60, 'five': 1.0, 'week': 2.0,
                                                                              'models': {}}]},
                               last={'totals': {'weight': 1.0}, 'who': [{'label': 'Helpers', 'detail': {'weight': 1.0}}],
                                     'days': [{'day': '2026-11-01', 'weight': 1.0}]})
        real['insights'] = insights.find(real)
        for key in sorted(NEW_KEYS - {'readings', 'insights', 'events', 'peak', 'series'}):
            self.assertEqual(shape(page[key]), shape(real[key]), key)
        # the series: the days, an hour with no use, and an hour with use (it says where it went), each the same shape;
        # an hour's accounts are there only where History says them
        self.assertEqual(set(page['series']), set(real['series']))
        self.assertEqual(shape(page['series']['days']), shape(real['series']['days']))
        for used in (False, True):
            mine, theirs = (dict(next(h for h in p['series']['hours'] if bool(h['weight']) == used)) for p in (page, real))
            for h in (mine, theirs):
                self.assertTrue(all(isinstance(e, str) and '@' in e for e in h.pop('accounts', [])))
            self.assertEqual(shape(mine), shape(theirs), used)
            self.assertEqual('projects' in mine, used)
        # the fixture's week has too few full days for a busiest day (spike()'s floor): the demo's has one
        self.assertEqual(set(page['peak']), set(real['peak']))
        self.assertEqual(shape(page['peak']['hour']), shape(real['peak']['hour']))
        self.assertEqual(shape(page['peak']['day']), {'day': 'str', 'weight': 'number', 'times': 'number'})
        self.assertIsNone(real['peak']['day'])
        self.assertEqual(shape(list(page['readings'].values())[0]), shape(list(real['readings'].values())[0]))
        self.assertTrue(page['events'] and real['events'])
        for e in page['events'] + real['events']:                    # a limit has no from or to, a switch no window
            self.assertEqual(set(e), EVENT_KEYS)
        self.assertEqual(set(page['insights'][0]), {'key', 'figure', 'text', 'numbers'})
        self.assertEqual(len(page['series']['days']), 8)
        self.assertEqual(len(page['series']['hours']), 168)
        self.assertEqual(json.loads(server.to_json(page)), page)
        self.assertEqual(page['cuts'], demo.USAGE['cuts'])                        # History's Work stays as it was

    def test_one_clear_spike_and_a_switch(self):
        page = server.usage_view()
        weights = [h['weight'] for h in page['series']['hours']]
        active = [x for x in weights[:-1] if x > 0]
        self.assertGreaterEqual(max(weights), 4 * sum(active) / len(active))
        self.assertEqual(sorted(weights, reverse=True)[1] * 2 < max(weights), True)   # one, not two
        # the chart names the spike in place (page['peak']), so no highlight repeats it
        self.assertEqual(page['peak']['hour']['weight'], max(weights))
        self.assertGreaterEqual(page['peak']['hour']['times'], 4)
        self.assertNotIn('hour', [x['key'] for x in page['insights']])
        switches = [e for e in page['events'] if e['kind'] == 'switch' and e['by'] == 'auto']
        self.assertTrue(switches)
        self.assertLessEqual(len(page['insights']), 3)
        texts = [x['text'] for x in insights.find(page)]
        self.assertEqual(texts, [x['text'] for x in page['insights']])            # made by the same rules

    def test_made_up_accounts_only_and_consistent_with_them(self):
        page = server.usage_view()
        accounts = set(demo.STATE['accounts'])
        self.assertLessEqual(set(page['readings']), accounts)
        self.assertEqual(set(page['readings']), accounts)
        for e in page['events']:
            for k in ('from', 'to', 'account'):
                self.assertTrue(e[k] is None or e[k] in accounts, e)
        for email, rows in page['readings'].items():
            now = demo.STATE['accounts'][email]['usage']
            self.assertEqual(rows[-1]['week'], now['weekly']['pct'], email)
            five = (now.get('session') or {}).get('pct')
            self.assertEqual(rows[-1]['five'], five, email)
        ivy = page['readings']['maya@ivy.example']
        self.assertEqual(ivy[-1]['models'], {'Fable': 80})
        text = json.dumps(page)
        for found in re.findall(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+', text):
            self.assertTrue(found.endswith('.example'), found)


# ---------------------------------------------------------------- uninstall --all

class Uninstall(unittest.TestCase):
    """The readings log is in ~/.switchboard: `uninstall --all` removes that folder whole, and a plain uninstall keeps
    it with the settings. (tests/install-check.sh case 6 runs both in a throwaway home.)"""

    def test_the_log_is_in_the_folder_all_removes_and_a_plain_uninstall_keeps(self):
        self.assertEqual(os.path.dirname(core.data_path(insights.READINGS_FILE)), core.data_dir())
        with open(os.path.join(ROOT, 'uninstall.sh')) as f:
            text = f.read()
        self.assertIn('DATA="$HOME/.switchboard"', text)
        self.assertRegex(text, r'if \[ "\$ALL" = 1 \]; then\n  rm -rf "\$DATA"\n')
        plain = re.search(r'\nrm -rf "\$DATA/app".*?\n(?=echo)', text, re.S).group(0)
        self.assertNotIn('readings', plain)

    def test_the_installer_check_proves_it(self):
        with open(os.path.join(HERE, 'install-check.sh')) as f:
            text = f.read()
        case6 = text[text.index('== 6.'):text.index('if want 7')]
        self.assertIn('readings.jsonl', case6)
        self.assertIn('a plain uninstall keeps the limit readings', case6)
        self.assertIn('uninstall --all removes the limit readings', case6)


if __name__ == '__main__':
    unittest.main()
