import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The series of usage.summarize(..., series_since=t): every request in its local hour and its local day, from t on,
# split by who, project, model and kind, in the same pass as the rest.
# The payload, the insights and the readings log are tested in test_insights_week.py and test_server_week.py.
#
# The history under tests/fixtures/usage/projects is made up; the numbers below are worked out by hand in
# test_usage.py's comments (w() is the weighting).
import calendar
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)

from switchboard import usage

try:
    from zoneinfo import ZoneInfo
    LA = ZoneInfo('America/Los_Angeles')
except Exception:  # noqa: BLE001 - no time zone database: those tests are skipped
    LA = None

FIXTURE = os.path.join(HERE, 'fixtures', 'usage', 'projects')
UTC = timezone.utc
D, H = 86400, 3600
NOV9 = calendar.timegm((2026, 11, 9, 0, 0, 0))
NOV10 = calendar.timegm((2026, 11, 10, 0, 0, 0))
NOV11 = calendar.timegm((2026, 11, 11, 0, 0, 0))
NOW = calendar.timegm((2026, 11, 11, 12, 30, 0))      # Wednesday, Nov 11 2026, 12:30 UTC
REAL = usage.summarize


def w(i, o, cw, cr):
    # today’s prices: a cache read is 0.05 of an input token, a 1-hour cache write 2 (every write in
    # the made-up tree is a 1-hour one, as Claude Code writes them: cache_creation.ephemeral_1h_input_tokens)
    return i + 5 * o + 2 * cw + 0.05 * cr


def w_fable(i, o, cw, cr):
    # the same on Fable 5.2, but a cache read there is 0.025 of its input token ($0.20 against $8)
    return i + 5 * o + 2 * cw + 0.025 * cr


# The requests of Nov 10 (UTC) by hour, from test_usage.py's hand count:
CHAT_9 = w(10, 200, 1000, 5000) + w(5, 100, 0, 6000) + w(5, 50, 100, 6100)              # m1 m2 m3: 4825
HELPERS_9 = (w(3, 300, 2000, 0) + w(2, 100, 0, 2000) + w(100, 20, 0, 0) + w(4, 80, 500, 1000)
             + w(6, 60, 0, 3000) + w(7, 70, 700, 0))                                    # h1 h2 f1 h3 h4: 9972
WORKFLOWS_10 = w(50, 500, 3000, 10000) + w(5, 50, 0, 12000) + w(8, 400, 1000, 4000)       # w1a (10:00:00.000) w1b w2a
CHAT_12 = w(3, 40, 0, 6200) + w(1, 10, 0, 100)                                          # m4 m5: 569
# 14:00Z, 15:00:00 (no zone), 23:59:59.999; n1 and n2 are the Fable chat's
N2, N1, R1 = w_fable(30, 300, 400, 500), w_fable(20, 100, 0, 0), w(1, 2, 3, 4)
M0, R0, R2 = w(9, 9, 9, 9), w(100, 100, 100, 100), w(100, 100, 100, 100)                # Nov 9 23:59:59.999, Nov 9 22:00, Nov 11 00:00
TOTAL = CHAT_9 + HELPERS_9 + WORKFLOWS_10 + CHAT_12 + N2 + N1 + R1                      # 32358.7
WEEK = TOTAL + M0 + R0 + R2                                                              # NOW’s week: 34041.15


def close(test, a, b, msg=None):
    test.assertAlmostEqual(a, b, delta=1e-6 * max(1.0, abs(b)), msg=msg)


def by_t(rows):
    return dict((r['t'], r) for r in rows)


def write_chat(root, project, session, reqs, title=None):
    """A made-up chat: [(timestamp, model, (input, output, cache write, cache read))]."""
    path = os.path.join(root, project, session + '.jsonl')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        if title:           # compact, as Claude Code writes every line (the reader takes the first line's spacing)
            f.write(json.dumps({'type': 'custom-title', 'customTitle': title}, separators=(',', ':')) + '\n')
        for i, (ts, model, t) in enumerate(reqs):
            f.write(json.dumps({'type': 'assistant', 'timestamp': ts, 'requestId': 'req_%s_%d' % (session[:6], i),
                                'message': {'id': 'msg_%s_%d' % (session[:6], i), 'model': model, 'content': [],
                                            'usage': {'input_tokens': t[0], 'output_tokens': t[1],
                                                      'cache_creation_input_tokens': t[2],
                                                      'cache_read_input_tokens': t[3]}}},
                               separators=(',', ':')) + '\n')
    return path


def local_iso(t, tz):
    return datetime.fromtimestamp(t, tz).isoformat(timespec='seconds')


def set_tz(test, name):
    """This process's local time zone for one test (TZ and tzset), put back after."""
    old = os.environ.get('TZ')

    def back():
        if old is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = old
        time.tzset()
    test.addCleanup(back)
    os.environ['TZ'] = name
    time.tzset()


# ---------------------------------------------------------------- the series, in summarize's own pass

class Series(unittest.TestCase):
    def test_buckets_add_up_to_the_week(self):
        s = REAL(FIXTURE, NOV10, NOV11, tz=UTC, series_since=NOV10)
        hours, days = s['series']['hours'], s['series']['days']
        self.assertEqual([h['t'] for h in hours], [NOV10 + i * H for i in range(24)])
        close(self, sum(h['weight'] for h in hours), s['totals']['weight'])
        close(self, sum(d['weight'] for d in days), s['totals']['weight'])
        close(self, s['totals']['weight'], TOTAL)
        for h in hours:
            close(self, sum(h['who'].values()), h['weight'])
            self.assertEqual(list(h['who']), list(usage.WHO))
        for d in days:
            for part in ('who', 'projects', 'models', 'kinds'):
                close(self, sum(d[part].values()), d['weight'], part)
            self.assertEqual(list(d['models']), list(usage.FAMILIES))
            self.assertEqual(list(d['kinds']), list(usage.KINDS))

    def test_each_hour_by_hand(self):
        s = REAL(FIXTURE, NOV10, NOV11, tz=UTC, series_since=NOV10)
        got = by_t(s['series']['hours'])
        want = {9: (CHAT_9, HELPERS_9, 0), 10: (0, 0, WORKFLOWS_10), 12: (CHAT_12, 0, 0), 14: (N2, 0, 0),
                15: (N1, 0, 0), 23: (R1, 0, 0)}
        for hour in range(24):
            chat, helpers, workflows = want.get(hour, (0, 0, 0))
            h = got[NOV10 + hour * H]
            close(self, h['who']['chat'], chat, hour)
            close(self, h['who']['helpers'], helpers, hour)
            close(self, h['who']['workflows'], workflows, hour)

    def test_the_day_by_project_model_and_kind(self):
        s = REAL(FIXTURE, NOV10, NOV11, tz=UTC, series_since=NOV10)
        (day,) = s['series']['days']
        self.assertEqual(day['day'], '2026-11-10')
        garden = CHAT_9 + HELPERS_9 + WORKFLOWS_10 + CHAT_12
        self.assertEqual(sorted(day['projects']), ['Garden planner v2', 'Weekly notes', 'recipes'])
        close(self, day['projects']['Garden planner v2'], garden)
        close(self, day['projects']['Weekly notes'], N1 + N2)
        close(self, day['projects']['recipes'], R1)
        close(self, day['who']['chat'], CHAT_9 + CHAT_12 + N1 + N2 + R1)
        close(self, day['who']['helpers'], HELPERS_9)
        close(self, day['who']['workflows'], WORKFLOWS_10)
        m = dict((r['label'], r['detail']['weight']) for r in s['model'])
        k = dict((r['label'], r['detail']['weight']) for r in s['kind'])
        for name in usage.FAMILIES:
            close(self, day['models'][name], m[name], name)        # one day, so the same as the week's cut
        for name in usage.KINDS:
            close(self, day['kinds'][name], k[name], name)

    def test_chats_with_one_title_are_one_project(self):
        tmp = tempfile.mkdtemp(prefix='switchboard-week-')
        self.addCleanup(shutil.rmtree, tmp, True)
        write_chat(tmp, '-p1', 'a1', [('2026-11-10T10:00:00.000Z', 'claude-opus-5-5', (10, 0, 0, 0))], title='Website')
        write_chat(tmp, '-p2', 'a2', [('2026-11-10T11:00:00.000Z', 'claude-opus-5-5', (20, 0, 0, 0))], title='Website')
        s = REAL(tmp, NOV10, NOV11, tz=UTC, series_since=NOV10)
        self.assertEqual(s['series']['days'][0]['projects'], {'Website': 30.0})
        self.assertEqual([r['label'] for r in s['work']], ['Website', 'Website'])     # History's Work: a row each

    def test_a_request_on_an_hour_or_day_boundary(self):
        s = REAL(FIXTURE, NOV9, NOV11 + H, tz=UTC, series_since=NOV9)
        hours = by_t(s['series']['hours'])
        close(self, hours[NOV10 + 10 * H]['who']['workflows'], WORKFLOWS_10)     # w1a at 10:00:00.000 is 10 o'clock's
        close(self, hours[NOV10 + 9 * H]['who']['workflows'], 0)
        close(self, hours[NOV10 + 15 * H]['weight'], N1)                          # n1 at 15:00:00.000
        close(self, hours[NOV10 + 14 * H]['weight'], N2)                          # n2 at 16:00+02:00
        close(self, hours[NOV9 + 23 * H]['weight'], M0)                           # m0 at 23:59:59.999
        close(self, hours[NOV10 + 23 * H]['weight'], R1)                          # r1 at 23:59:59.999
        close(self, hours[NOV11]['weight'], R2)                                   # r2 at exactly midnight: the new day
        days = dict((d['day'], d['weight']) for d in s['series']['days'])
        self.assertEqual(sorted(days), ['2026-11-09', '2026-11-10', '2026-11-11'])
        close(self, days['2026-11-09'], M0 + R0)
        close(self, days['2026-11-11'], R2)
        # A window that ends at midnight leaves the request at midnight out, and has no hour or day after it.
        s = REAL(FIXTURE, NOV10, NOV11, tz=UTC, series_since=NOV10)
        self.assertEqual(s['series']['hours'][-1]['t'], NOV10 + 23 * H)
        self.assertEqual([d['day'] for d in s['series']['days']], ['2026-11-10'])

    def test_the_series_can_start_before_the_week_and_the_rest_stays_the_same(self):
        plain = REAL(FIXTURE, NOV10, NOV11, tz=UTC)
        wide = REAL(FIXTURE, NOV10, NOV11, tz=UTC, series_since=NOV9)
        self.assertNotIn('series', plain)                                         # only when asked
        series = wide.pop('series')
        plain.pop('files')
        wide.pop('files')
        self.assertEqual(json.dumps(plain, sort_keys=True), json.dumps(wide, sort_keys=True))
        days = dict((d['day'], d['weight']) for d in series['days'])
        close(self, days['2026-11-09'], M0 + R0)                                  # the day before the window, whole
        close(self, sum(days.values()), REAL(FIXTURE, NOV9, NOV11, tz=UTC)['totals']['weight'])
        self.assertEqual((series['since'], series['until']), (NOV9, NOV11))

    def test_a_time_zone_change(self):
        # The Mac moves from UTC to UTC+10 between two reads: the days follow the clock it has now, and the cache
        # (which keeps only UTC times) is used as it is.
        tmp = tempfile.mkdtemp(prefix='switchboard-week-')
        self.addCleanup(shutil.rmtree, tmp, True)
        cache = os.path.join(tmp, 'usage-cache.json')
        set_tz(self, 'UTC')
        first = REAL(FIXTURE, NOV10, NOV11, cache_path=cache, series_since=NOV10)
        self.assertEqual([d['day'] for d in first['series']['days']], ['2026-11-10'])
        self.assertEqual(first['series']['hours'][0]['t'], NOV10)
        set_tz(self, 'Etc/GMT-10')                                    # UTC+10
        moved = REAL(FIXTURE, NOV10, NOV11, cache_path=cache, series_since=NOV10)
        self.assertEqual(moved['files']['read'], 0)
        days = dict((d['day'], d['weight']) for d in moved['series']['days'])
        self.assertEqual(sorted(days), ['2026-11-10', '2026-11-11'])
        close(self, days['2026-11-11'], N1 + N2 + R1)                 # 14:00Z is midnight in UTC+10
        close(self, days['2026-11-10'], TOTAL - (N1 + N2 + R1))
        self.assertEqual(moved['series']['hours'][0]['t'], NOV10)     # whole-hour zones share UTC's hours

    @unittest.skipIf(LA is None, 'no time zone database')
    def test_a_daylight_saving_change(self):
        # Nov 1 2026, Los Angeles: 2:00 PDT goes back to 1:00 PST, so the day has 25 hours and 1 AM comes twice.
        tmp = tempfile.mkdtemp(prefix='switchboard-week-')
        self.addCleanup(shutil.rmtree, tmp, True)
        write_chat(tmp, '-dst', 'dst001', [('2026-11-01T07:00:00.000Z', 'claude-opus-5-5', (1, 0, 0, 0)),     # 00:00 PDT
                                           ('2026-11-01T08:30:00.000Z', 'claude-opus-5-5', (10, 0, 0, 0)),    # 1:30 PDT
                                           ('2026-11-01T09:30:00.000Z', 'claude-opus-5-5', (20, 0, 0, 0)),    # 1:30 PST
                                           ('2026-11-02T07:59:59.000Z', 'claude-opus-5-5', (2, 0, 0, 0)),     # 23:59:59 PST
                                           ('2026-11-02T08:00:00.000Z', 'claude-opus-5-5', (4, 0, 0, 0))],    # Nov 2 00:00
                   title='Clocks')
        start = calendar.timegm((2026, 11, 1, 7, 0, 0))
        until = calendar.timegm((2026, 11, 2, 9, 0, 0))
        s = REAL(tmp, start, until, tz=LA, series_since=start)
        hours = s['series']['hours']
        self.assertEqual(len(hours), 26)
        self.assertEqual([h['t'] for h in hours], [start + i * H for i in range(26)])
        self.assertEqual([h['weight'] for h in hours[1:3]], [10.0, 20.0])          # each 1 AM its own hour
        self.assertEqual([local_iso(h['t'], LA) for h in hours[1:3]],
                         ['2026-11-01T01:00:00-07:00', '2026-11-01T01:00:00-08:00'])
        self.assertEqual([(d['day'], d['weight']) for d in s['series']['days']], [('2026-11-01', 33.0), ('2026-11-02', 4.0)])
        self.assertEqual(sum(1 for h in hours if local_iso(h['t'], LA).startswith('2026-11-01')), 25)

    def test_an_empty_week(self):
        s = REAL(FIXTURE, NOV11 + D, NOV11 + 2 * D, tz=UTC, series_since=NOV11 + D)
        self.assertEqual(len(s['series']['hours']), 24)
        self.assertTrue(all(h['weight'] == 0 and set(h['who'].values()) == {0} for h in s['series']['hours']))
        (day,) = s['series']['days']
        self.assertEqual((day['day'], day['weight'], day['projects']), ('2026-11-12', 0, {}))
        self.assertEqual(set(day['models'].values()) | set(day['kinds'].values()), {0})
        missing = REAL(os.path.join(tempfile.gettempdir(), 'no-such-switchboard-root'), NOV10, NOV11, tz=UTC,
                       series_since=NOV10)
        self.assertEqual(len(missing['series']['hours']), 24)
        self.assertEqual(missing['totals']['weight'], 0)

    def test_series_after_until_is_empty(self):
        s = REAL(FIXTURE, NOV10, NOV11, tz=UTC, series_since=NOV11 + D)
        self.assertEqual((s['series']['hours'], s['series']['days']), ([], []))
        close(self, s['totals']['weight'], TOTAL)


class SeriesCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-week-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, 'projects')
        shutil.copytree(FIXTURE, self.root)
        self.cache = os.path.join(self.tmp, 'data', 'usage-cache.json')

    def run_it(self):
        return REAL(self.root, NOV10, NOV11, cache_path=self.cache, tz=UTC, series_since=NOV9)

    def same(self, a, b):
        a, b = dict(a), dict(b)
        a.pop('files')
        b.pop('files')
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

    def test_the_cache_path_gives_the_same_series(self):
        first = self.run_it()
        self.assertEqual((first['files']['read'], first['files']['from_cache']), (10, 0))
        second = self.run_it()
        self.assertEqual((second['files']['read'], second['files']['from_cache']), (0, 10))
        self.same(first, second)
        self.same(first, REAL(self.root, NOV10, NOV11, tz=UTC, series_since=NOV9))

    def test_a_cache_version_bump_rebuilds_the_cache(self):
        first = self.run_it()
        with mock.patch.object(usage, 'CACHE_VERSION', usage.CACHE_VERSION + 1):
            rebuilt = self.run_it()
            self.assertEqual((rebuilt['files']['read'], rebuilt['files']['from_cache']), (10, 0))
            with open(self.cache) as f:
                self.assertEqual(json.load(f)['version'], usage.CACHE_VERSION)
            again = self.run_it()
            self.assertEqual(again['files']['read'], 0)
        self.same(first, rebuilt)
        self.same(first, again)


if __name__ == '__main__':
    unittest.main()
