import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The week under the accounts is kept on disk: a restart shows the last one at once (read within a day, in the shape the
# page draws) and reads it again soon after; a very busy Mac still reads a week more than an hour old, a nearly full
# disk reads no more, and a read that fails leaves the last week on the page. The page keeps saying "Reading your
# week…" through answers that come too late and asks again in 10 s instead of a minute, and its dates end when the
# week was read.
# Run with:
#   SWITCHBOARD_HOME="$(mktemp -d)/data" taskpolicy -b python3 -m unittest discover -s tests -p 'test_server_week_kept.py'
# (its page tests start headless Chrome: run the file under the Chrome lock)
import json
import os
import stat
import sys
import time
import unittest
from datetime import datetime
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)

from switchboard import connect, lanes, linkapp, server
import test_app
from test_app import CHROME, ChromePage

REAL_READ = server.read_usage          # the real read, kept before the tests put a stand-in in its place
PAGE = {'days': 7, 'cuts': {'who': [{'label': 'Workflows', 'share': 100, 'note': ''}]}, 'moved': None,
        'series': {'hours': [], 'days': []}}


class KeptOnDisk(unittest.TestCase):
    def setUp(self):
        saved = dict(server.USAGE)
        self.addCleanup(lambda: (server.USAGE.clear(), server.USAGE.update(saved)))
        server.USAGE.update(page=None, extras=None, at=0, running=False)
        for target, values in ((server.EASE, {'aware': False, 'on': False, 'load': 0.0}), (server.DISK, {'low': False})):
            p = mock.patch.dict(target, values)
            p.start()
            self.addCleanup(p.stop)
        self.path = server.data_path(server.WEEK_FILE)
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)   # as ~/.switchboard always is
        self.addCleanup(lambda: os.path.exists(self.path) and os.remove(self.path))
        reads = mock.patch.object(server, 'read_usage', lambda: None)   # usage_view's thread does nothing here
        reads.start()
        self.addCleanup(reads.stop)

    def read(self, fails=False):
        """server.read_usage itself (not the stand-in), on a made-up history."""
        def summarize(*a, **k):
            if fails:
                raise OSError('no history')
            return {'window': {'since': 0}}
        with mock.patch.object(server.usage, 'summarize', summarize), \
                mock.patch.object(server.usage, 'for_page', lambda s, d: dict(PAGE)), \
                mock.patch.object(server, 'models_from_history', lambda s: {'moved': None, 'exact': False, 'log': []}), \
                mock.patch.object(server.claudecode, 'share', lambda until: None), \
                mock.patch.object(server.insights, 'read_week', lambda s, until, page: {'insights': []}):
            REAL_READ()

    def asked(self):
        """Whether usage_view starts a read now (it marks one running before it starts the thread)."""
        server.USAGE['running'] = False
        view = server.usage_view()
        return server.USAGE['running'], view

    def keep(self, kept):
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write(kept if isinstance(kept, str) else json.dumps(kept))

    def test_a_read_week_is_kept_and_a_restart_shows_it_at_once(self):
        self.read()
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        with open(self.path, encoding='utf-8') as f:
            kept = json.load(f)
        self.assertEqual(kept['page']['cuts'], PAGE['cuts'])
        self.assertLess(abs(kept['at'] - time.time()), 60)
        server.USAGE.update(page=None, extras=None, at=0, running=False)        # a restart
        self.assertTrue(server.load_week())
        running, view = self.asked()
        self.assertEqual(view['state'], 'ready')                               # at once, not "reading"
        self.assertEqual(view['cuts'], PAGE['cuts'])
        self.assertEqual(server.USAGE['extras'], {'moved': None, 'exact': False, 'log': []})
        self.assertFalse(running)                                              # just read: not read again yet

    def test_an_old_kept_week_shows_and_is_read_again(self):
        self.read()
        with open(self.path, encoding='utf-8') as f:
            kept = json.load(f)
        kept['at'] -= server.USAGE_EVERY + 5
        self.keep(kept)
        server.USAGE.update(page=None, extras=None, at=0, running=False)
        self.assertTrue(server.load_week())
        running, view = self.asked()
        self.assertEqual(view['state'], 'ready')
        self.assertTrue(running)

    def test_a_failed_read_is_never_kept_and_the_last_week_stays_on_the_page(self):
        self.read()
        with open(self.path, encoding='utf-8') as f:
            before = f.read()
        good = server.USAGE['page']
        self.read(fails=True)
        with open(self.path, encoding='utf-8') as f:
            self.assertEqual(f.read(), before)                                  # the last good week stays on disk
        self.assertIs(server.USAGE['page'], good)                              # and on the page
        self.assertEqual(server.USAGE['extras'], {'moved': None, 'exact': False, 'log': []})
        self.assertFalse(server.USAGE['running'])
        self.assertLess(abs(server.USAGE['at'] - time.time()), 60)             # tried again in five minutes, not at once
        server.USAGE.update(page=None, extras=None, at=0)                     # with no week yet, a failed read says so
        self.read(fails=True)
        self.assertTrue(server.USAGE['page'].get('failed'))

    def test_a_week_whose_hours_do_not_say_where_they_went_is_shown_and_read_again_at_once(self):
        # a build from before an hour said its projects, models and the rest kept only its weight and who: after an
        # update that week shows at once, and the first read is not put off, so the hours soon say more
        hour = {'at': '2026-11-19T10:00:00-08:00', 'weight': 5.0, 'who': {'chat': 5.0, 'helpers': 0.0, 'workflows': 0.0}}
        says = dict(hour, projects=[{'label': 'Website', 'share': 100}])
        idle = dict(hour, weight=0.0)
        now = time.time()
        for hours, busy, again in (([hour], False, True), ([says], False, False), ([idle, says], False, False),
                                   ([idle], False, False),             # no use: nothing to say more of
                                   ([hour], True, True),               # a very busy Mac does not put this read off
                                   ([says], True, False)):
            with self.subTest(hours=hours, busy=busy), mock.patch.dict(server.EASE, {'aware': busy, 'on': busy}):
                self.keep({'page': dict(PAGE, series={'hours': hours, 'days': []}), 'extras': None, 'at': now - 10})
                server.USAGE.update(page=None, extras=None, at=0, running=False)
                self.assertTrue(server.load_week(now))
                running, view = self.asked()
                self.assertEqual(view['state'], 'ready')                       # shown at once either way
                self.assertEqual(view['series']['hours'], hours)
                self.assertEqual(running, again)
        with mock.patch.dict(server.DISK, {'low': True}):                      # a nearly full disk still reads no more
            self.keep({'page': dict(PAGE, series={'hours': [hour], 'days': []}), 'extras': None, 'at': now - 10})
            server.USAGE.update(page=None, extras=None, at=0, running=False)
            self.assertTrue(server.load_week(now))
            self.assertFalse(self.asked()[0])

    def test_a_week_weighed_before_reads_had_a_weight_per_model_is_shown_and_read_again_at_once(self):
        # a build from before a cache read was weighed at its own model's price kept tokens with no cache_read_low:
        # after an update that week shows at once, and the first read is not put off, even on a very busy Mac
        old = {'input': 10, 'output': 20, 'cache_write': 30, 'cache_read': 400, 'cache_write_1h': 0}
        new = dict(old, cache_read_low=0)
        none_read = dict(old, cache_read=0)
        now = time.time()
        for tokens, busy, again in ((old, False, True), (old, True, True), (new, False, False), (new, True, False),
                                    (none_read, True, False),         # no reads: nothing weighs differently
                                    (None, True, False), ('x', True, False), ([], True, False)):
            with self.subTest(tokens=tokens, busy=busy), mock.patch.dict(server.EASE, {'aware': busy, 'on': busy}):
                self.keep({'page': dict(PAGE, tokens=tokens), 'extras': None, 'at': now - 10})
                server.USAGE.update(page=None, extras=None, at=0, running=False)
                self.assertTrue(server.load_week(now))
                running, view = self.asked()
                self.assertEqual(view['state'], 'ready')                       # shown at once either way
                self.assertEqual(view['tokens'], tokens)
                self.assertEqual(running, again)

    def test_hours_in_no_shape_at_all_never_stop_the_page_from_starting(self):
        now = time.time()
        for hours in (None, 'hours', 7, {}, [None, 'x', 3, []], [{'weight': 'heavy'}], [{'weight': None, 'projects': 1}],
                      [{}], [{'weight': 2}, 'x']):
            with self.subTest(hours=hours):
                self.assertIn(server._says_where({'series': {'hours': hours}}), (True, False))
                self.keep({'page': dict(PAGE, series={'hours': hours, 'days': []}), 'extras': None, 'at': now - 10})
                server.USAGE.update(page=None, extras=None, at=0, running=False)
                self.assertTrue(server.load_week(now))                         # the kept week is still taken
        self.assertTrue(server._says_where({}))
        self.assertTrue(server._says_where({'series': None}))
        self.assertFalse(server._says_where({'series': {'hours': [{'weight': 2}, 'x']}}))
        self.assertTrue(server._says_where({'series': {'hours': [{'weight': 2}, {'weight': 1, 'projects': []}]}}))

    def test_a_damaged_missing_old_or_strange_file_is_left_alone(self):
        self.assertFalse(server.load_week())
        now = time.time()
        good = {'days': 7, 'cuts': {}, 'series': {'hours': [], 'days': []}}
        for text in ('{', '[]', '{"page": {"failed": true}, "at": 5}', '{"page": {}, "at": true}',
                     '{"page": %s, "at": NaN}' % json.dumps(good), '{"page": %s, "at": Infinity}' % json.dumps(good),
                     {'page': {'days': 7, 'series': {}}, 'at': now},                        # no cuts
                     {'page': {'days': 7, 'cuts': {}}, 'at': now},                          # no series
                     {'page': dict(good, failed=True), 'at': now},
                     {'page': good, 'at': now - server.WEEK_KEPT_FOR - 5},                  # read more than a day ago
                     {'page': good, 'at': now + server.WEEK_AHEAD + 60}):                   # dated ahead (clock set back)
            with self.subTest(text=text):
                server.USAGE.update(page=None, extras=None, at=0)
                self.keep(text)
                self.assertFalse(server.load_week())
                self.assertIsNone(server.USAGE['page'])
        server.USAGE.update(page=None, extras=None, at=0)
        self.keep({'page': good, 'extras': ['not', 'a', 'dict'], 'at': now - 60})        # the week fine, its extras not
        self.assertTrue(server.load_week())
        self.assertEqual(server.USAGE['page'], good)
        self.assertIsNone(server.USAGE['extras'])

    def test_a_week_already_read_is_never_replaced_by_the_kept_one(self):
        self.read()
        server.USAGE.update(page=dict(PAGE, moved='this run'))
        self.assertFalse(server.load_week())
        self.assertEqual(server.USAGE['page']['moved'], 'this run')

    def test_a_very_busy_mac_still_reads_a_week_over_an_hour_old(self):
        server.EASE.update(aware=True, on=True, load=5.0)
        server.USAGE.update(page=dict(PAGE), at=time.time() - 600)
        self.assertFalse(self.asked()[0])                                      # ten minutes old: it waits
        server.USAGE.update(at=time.time() - server.USAGE_STALE - 5)
        self.assertTrue(self.asked()[0])                                       # over an hour: read anyway

    def test_a_nearly_full_disk_reads_no_more_not_even_a_week_over_an_hour_old(self):
        server.DISK.update(low=True)
        server.USAGE.update(page=dict(PAGE), at=time.time() - server.USAGE_STALE - 5)
        self.assertFalse(self.asked()[0])


class Stub(object):
    """Stands in for the page's HTTP server: nothing listens."""

    def __init__(self, *a, **kw):
        pass

    def serve_forever(self):
        pass


class ThePageStarting(test_app.Sandbox):
    def test_it_shows_the_kept_week_and_reads_the_week_twenty_seconds_on(self):
        for target in (server.EASE, server.LINKS):
            p = mock.patch.dict(target, dict(target))                         # put back as they were, after serve()
            p.start()
            self.addCleanup(p.stop)
        loaded, timers = [], []

        class Timer(object):
            def __init__(self, interval, function, *a, **k):
                self.interval, self.function, self.daemon, self.started = interval, function, False, False
                timers.append(self)

            def start(self):
                self.started = True
        with mock.patch.dict(os.environ, {'SWITCHBOARD_PORT': '7879'}), \
                mock.patch.object(server, 'ThreadingHTTPServer', Stub), mock.patch.object(server, 'loop', lambda *a, **k: None), \
                mock.patch.object(server.signal, 'signal', lambda *a: None), mock.patch.object(connect, 'sweep', lambda: None), \
                mock.patch.object(server, 'resolve_journal', lambda: None), mock.patch.object(linkapp, 'announce_port', lambda: None), \
                mock.patch.object(lanes, 'resume', lambda: None), \
                mock.patch.object(server, 'load_week', lambda: loaded.append(1) or True), \
                mock.patch.object(server.threading, 'Timer', Timer):
            server.serve()
        self.assertEqual(loaded, [1])                                          # the kept week, at once
        reads = [t for t in timers if t.function is server.usage_view]
        self.assertEqual([(t.interval, t.daemon, t.started) for t in reads], [(20, True, True)])   # and a read soon after


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class ThePageKeepsReading(ChromePage):
    def test_answers_that_come_too_late_say_reading_and_it_asks_again_soon(self):
        real, calls = server.usage_view, []

        def late(fresh=False):
            if sys._getframe(1).f_code.co_name == 'history_view':
                return real(fresh)                    # History's own ask is answered: only the week's own asks miss
            calls.append(fresh)
            if len(calls) <= 3:
                raise RuntimeError('a very busy Mac')         # the page gets no week, as when the answer is late
            return real(fresh)
        p = mock.patch.object(server, 'usage_view', late)
        p.start()
        self.addCleanup(p.stop)
        got = self.run_page("""
      const chart = $('#wk-chart'), seen = [];
      const note = () => { const t = (chart.querySelector('.wk-none') || {}).textContent; if (t && seen[seen.length - 1] !== t) seen.push(t); };
      note(); new MutationObserver(note).observe(chart, {childList: true, subtree: true, characterData: true});
      const t0 = performance.now();
      for (let i = 0; i < 1200 && !chart.dataset.by; i++) await wait(100);
      done({seen, drawn: chart.dataset.by || null, ms: Math.round(performance.now() - t0)});
""", budget=150000)
        self.assertGreaterEqual(len(calls), 4)                                 # three missed in a row, then one answered
        self.assertIn('Reading your week…', got['seen'])
        self.assertNotIn('Your week can’t be read right now.', got['seen'])
        self.assertIn(got['drawn'], ('hour', 'day'))                           # the week, once it came
        self.assertLess(got['ms'], 75000)                 # asked again 10 to 15 s apart; a minute apart would be 180 s

    def test_the_weeks_dates_end_when_it_was_read(self):
        page = server.usage_view()                                             # the demo's week
        read = time.time() - 2 * 86400
        page['at'] = read                                                      # as a week kept two days ago says
        p = mock.patch.object(server, 'usage_view', lambda fresh=False: dict(page))
        p.start()
        self.addCleanup(p.stop)
        got = self.run_page("""
      for (let i = 0; i < 100 && !$('#wk-chart').dataset.by; i++) await wait(100);
      done($('#wk-range').textContent);
""")
        a, b = datetime.fromisoformat(page['series']['hours'][0]['at']).astimezone(), datetime.fromtimestamp(read)
        end = str(b.day) if a.month == b.month else '%s %d' % (b.strftime('%b'), b.day)
        self.assertEqual(got, '%s %d – %s' % (a.strftime('%b'), a.day, end))


if __name__ == '__main__':
    unittest.main()
