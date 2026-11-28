import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# The count behind the helpers' cache switch (switchboard/router/cache_count.py): from the records Claude Code keeps
# for each helper and workflow agent, how often a helper stored its whole context again after a wait of five minutes
# to an hour. Every record here is made up, in a throwaway folder. A marker word sits in every text field and in the
# folder names, and must come out of nothing the count returns, says, prints or writes.
import contextlib
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from switchboard.router import cache_count as cc  # noqa: E402
from switchboard.router import count_words as W  # noqa: E402
from test_providers_words import problems  # noqa: E402  (the one guard every package is held to)

MARK = 'zqxmarkerword'
NOW = 1764000000.0                  # a fixed "now"; every request time is set from it
DAY = 86400.0
OPUS = 'claude-opus-5-5'
HAIKU = 'claude-haiku-5'


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.') + '%03dZ' % round((t % 1) * 1000)


def req(t, n, model=OPUS, i=10, o=500, cw=0, cr=0, w1h=0, usage=True, extra=None):
    """One request line of a helper's record, with the marker in every text field Claude Code writes."""
    msg = {'id': 'msg_%s_%d' % (MARK, n), 'type': 'message', 'role': 'assistant', 'model': model,
           'content': [{'type': 'text', 'text': 'answer %s' % MARK},
                       {'type': 'thinking', 'thinking': MARK, 'signature': MARK},
                       {'type': 'tool_use', 'id': 'toolu_%s' % MARK, 'name': 'Bash', 'input': {'command': MARK}}],
           'stop_reason': 'tool_use'}
    if usage:
        msg['usage'] = {'input_tokens': i, 'output_tokens': o, 'cache_creation_input_tokens': cw,
                        'cache_read_input_tokens': cr,
                        'cache_creation': {'ephemeral_5m_input_tokens': cw - w1h, 'ephemeral_1h_input_tokens': w1h}}
    d = {'type': 'assistant', 'timestamp': iso(t), 'requestId': 'req_%s_%d' % (MARK, n), 'isSidechain': True,
         'cwd': '/Users/%s/%s' % (MARK, MARK), 'gitBranch': MARK, 'slug': MARK, 'sessionId': MARK, 'uuid': MARK,
         'agentId': MARK, 'message': msg}
    d.update(extra or {})
    return json.dumps(d)


def user(t):
    """A tool result coming back, which mentions "usage" and "assistant" in its text: never a request."""
    return json.dumps({'type': 'user', 'timestamp': iso(t), 'cwd': MARK, 'isSidechain': True,
                       'message': {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': MARK,
                                                                'content': '"usage" "type":"assistant" %s' % MARK}]},
                       'toolUseResult': {'stdout': MARK}})


# A helper that had its cache warm (reads it), and a request that stored it all again.
def warm(t, n, **k):
    return req(t, n, cr=50000, cw=2000, **k)


def cold(t, n, **k):
    return req(t, n, cr=0, cw=53000, **k)


ALREADY = 52510         # what cold() after warm() stored again: warm's 10 + 2000 + 50000 in and 500 out


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='sb-count-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.projects = os.path.join(self.tmp, 'projects')
        self.session = os.path.join(self.projects, '-Users-%s-site' % MARK, 'sess-%s' % MARK)
        self.data = os.path.join(self.tmp, 'data')
        self.n = 0

    def record(self, lines, workflow=False, name=None, newline=True):
        """A helper's record (or a workflow agent's), with a meta file beside it that is never read."""
        self.n += 1
        folder = os.path.join(self.session, 'subagents')
        if workflow:
            folder = os.path.join(folder, 'workflows', 'wf_%s' % MARK)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name or 'agent-%s%d.jsonl' % (MARK, self.n))
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + ('\n' if newline else ''))
        with open(path[:-len('.jsonl')] + '.meta.json', 'w', encoding='utf-8') as f:
            json.dump({'agentType': MARK, 'description': 'find %s' % MARK}, f)
        return path

    def run_count(self, **k):
        k.setdefault('now', NOW)
        k.setdefault('cache_file', os.path.join(self.data, 'router', 'cache-count.json'))
        return cc.count([self.projects], **k)


class TheWait(Base):
    def test_a_restore_after_ten_minutes_counts(self):
        t = NOW - DAY
        self.record([warm(t, 1), user(t + 1), cold(t + 600, 2)])
        r = self.run_count()
        self.assertEqual((r['records_read'], r['records_unread'], r['requests'], r['restores']), (1, 0, 2, 1))
        self.assertEqual(r['restore_tokens'], ALREADY)
        self.assertEqual(r['hour_would_read'], ALREADY)
        self.assertEqual(r['cache_writes'], 2000 + 53000)
        self.assertEqual(r['hour_would_write'], 2000 + 53000 - ALREADY)

    def test_writes_already_made_for_an_hour_are_not_in_the_hour_rate_figure(self):
        t = NOW - DAY
        self.record([req(t, 1, cr=50000, cw=2000, w1h=2000), warm(t + 60, 2), cold(t + 660, 3)])
        r = self.run_count()
        self.assertEqual((r['restores'], r['cache_writes'], r['cache_writes_1h']), (1, 57000, 2000))
        self.assertEqual(r['hour_would_write'], 57000 - 2000 - ALREADY)
        self.assertEqual(cc.say(r)[2], 'With an hour cache, up to 52,510 of the tokens stored again are read from the '
                                       'cache instead, and the other 2,490 tokens your helpers write to the cache '
                                       'are written at the dearer hour rate.')

    def test_the_lower_edge_five_minutes_exactly_counts_and_a_moment_before_does_not(self):
        t = NOW - DAY
        self.record([warm(t, 1), cold(t + 300, 2)])
        self.record([warm(t, 3), cold(t + 299.999, 4)])
        r = self.run_count()
        self.assertEqual((r['requests'], r['restores']), (4, 1))

    def test_the_upper_edge_an_hour_exactly_does_not_count_and_a_moment_before_does(self):
        t = NOW - DAY
        self.record([warm(t, 1), cold(t + 3599.999, 2)])
        self.record([warm(t, 3), cold(t + 3600, 4)])
        r = self.run_count()
        self.assertEqual((r['requests'], r['restores'], r['restores_over_an_hour']), (4, 1, 1))

    def test_a_request_after_more_than_an_hour_is_not_one_an_hour_would_spare(self):
        t = NOW - DAY
        self.record([warm(t, 1), cold(t + 3 * 3600, 2)])
        r = self.run_count()
        self.assertEqual((r['restores'], r['restore_tokens'], r['restores_over_an_hour']), (0, 0, 1))
        self.assertEqual(r['hour_would_write'], 55000)

    def test_a_first_request_stores_everything_and_is_not_a_restore(self):
        self.record([cold(NOW - DAY, 1)])
        r = self.run_count()
        self.assertEqual((r['requests'], r['restores'], r['cache_writes']), (1, 0, 53000))

    def test_a_request_that_read_its_cache_after_a_wait_is_not_a_restore(self):
        t = NOW - DAY
        self.record([warm(t, 1), warm(t + 1200, 2)])
        r = self.run_count()
        self.assertEqual((r['requests'], r['restores']), (2, 0))

    def test_a_request_that_read_a_fifth_of_its_context_after_a_wait_is_not_a_restore(self):
        t = NOW - DAY
        self.record([warm(t, 1), req(t + 900, 2, cr=12000, cw=41000)])
        self.assertEqual(self.run_count()['restores'], 0)

    def test_a_wait_after_a_request_that_already_wrote_for_an_hour_is_not_counted(self):
        t = NOW - DAY
        self.record([req(t, 1, cr=50000, cw=2000, w1h=2000), cold(t + 900, 2)])
        r = self.run_count()
        self.assertEqual((r['restores'], r['cache_writes_1h']), (0, 2000))

    def test_a_write_given_only_by_its_parts_is_read_from_its_parts(self):
        t = NOW - DAY
        d = json.loads(cold(t + 600, 2))
        del d['message']['usage']['cache_creation_input_tokens']
        self.record([warm(t, 1), json.dumps(d)])
        r = self.run_count()
        self.assertEqual((r['restores'], r['cache_writes']), (1, 2000 + 53000))

    def test_a_wait_after_a_request_that_only_read_its_cache_counts_as_a_five_minute_wait(self):
        # What the record cannot say: a request that wrote nothing shows no cache life. It is read as five minutes
        # (the helpers' own lifetime unless the person chose an hour), as the docstring says.
        t = NOW - DAY
        self.record([warm(t, 1), req(t + 60, 2, cr=52000, cw=0), cold(t + 660, 3)])
        self.assertEqual(self.run_count()['restores'], 1)

    def test_a_small_context_stored_again_is_not_counted(self):
        t = NOW - DAY
        self.record([req(t, 1, cr=9000, cw=500), req(t + 900, 2, cr=0, cw=9500)])
        self.assertEqual(self.run_count()['restores'], 0)

    def test_the_wait_is_measured_from_the_request_before_it_even_before_the_window(self):
        since = NOW - 2 * DAY
        self.record([warm(since - 120, 1), cold(since + 480, 2)])
        r = self.run_count(since=since)
        self.assertEqual((r['requests'], r['restores']), (1, 1))

    def test_one_request_on_three_lines_counts_once_with_its_largest_numbers(self):
        t = NOW - DAY
        self.record([warm(t, 1), req(t + 600, 2, cr=0, cw=53000), req(t + 600.2, 2, cr=0, cw=1000),
                     req(t + 600.4, 2, cr=0, cw=0, o=0)])
        r = self.run_count()
        self.assertEqual((r['requests'], r['restores'], r['cache_writes']), (2, 1, 55000))

    def test_lines_out_of_order_are_put_in_time_order(self):
        t = NOW - DAY
        self.record([cold(t + 600, 2), warm(t, 1)])
        self.assertEqual(self.run_count()['restores'], 1)

    def test_workflow_agents_count_and_the_chat_itself_does_not(self):
        t = NOW - DAY
        self.record([warm(t, 1), cold(t + 600, 2)], workflow=True)
        os.makedirs(self.session, exist_ok=True)
        with open(self.session + '.jsonl', 'w', encoding='utf-8') as f:
            f.write('\n'.join([warm(t, 3), cold(t + 600, 4)]) + '\n')
        r = self.run_count()
        self.assertEqual((r['records_read'], r['requests'], r['restores']), (1, 2, 1))


class Records(Base):
    def test_a_record_with_no_usage_is_read_and_holds_no_request(self):
        t = NOW - DAY
        # The second line holds the word "usage" in its text, so it is parsed, and still has no usage block.
        self.record([req(t, 1, usage=False), user(t + 1), req(t + 700, 2, usage=False, extra={'slug': 'usage'})])
        r = self.run_count()
        self.assertEqual((r['records_read'], r['records_unread'], r['requests'], r['restores']), (1, 0, 0, 0))

    def test_a_broken_line_is_skipped_and_counted_and_the_rest_is_read(self):
        t = NOW - DAY
        self.record([warm(t, 1), '{"type":"assistant","message":{"usage": {broken', cold(t + 600, 2)])
        r = self.run_count()
        self.assertEqual((r['records_read'], r['bad_lines'], r['requests'], r['restores']), (1, 1, 2, 1))

    def test_a_last_line_still_being_written_is_not_counted_as_broken(self):
        t = NOW - DAY
        self.record([warm(t, 1), cold(t + 600, 2), cold(t + 700, 3)[:-40]], newline=False)
        r = self.run_count()
        self.assertEqual((r['bad_lines'], r['requests'], r['restores']), (0, 2, 1))

    def test_a_record_of_nothing_but_broken_lines_could_not_be_read(self):
        self.record(['{"type":"assistant","usage" x', '{"usage": "type":"assistant" ]'])
        self.record([warm(NOW - DAY, 1)])
        r = self.run_count()
        self.assertEqual((r['records_read'], r['records_unread'], r['requests']), (1, 1, 1))

    def test_a_record_that_cannot_be_opened_could_not_be_read(self):
        path = self.record([warm(NOW - DAY, 1)])
        os.chmod(path, 0)
        self.addCleanup(os.chmod, path, stat.S_IRUSR | stat.S_IWUSR)
        if os.access(path, os.R_OK):
            self.skipTest('this user can read a file with no read bit')
        r = self.run_count()
        self.assertEqual((r['records_read'], r['records_unread'], r['requests']), (0, 1, 0))

    def test_a_linked_record_is_not_followed(self):
        real = os.path.join(self.tmp, 'elsewhere.jsonl')
        with open(real, 'w', encoding='utf-8') as f:
            f.write(warm(NOW - DAY, 1) + '\n')
        folder = os.path.join(self.session, 'subagents')
        os.makedirs(folder)
        os.symlink(real, os.path.join(folder, 'agent-link.jsonl'))
        r = self.run_count()
        self.assertEqual((r['records_read'], r['requests']), (0, 0))

    def test_no_folder_at_all_counts_nothing(self):
        r = cc.count([os.path.join(self.tmp, 'none')], now=NOW, cache_file=False)
        self.assertEqual((r['records_read'], r['records_unread'], r['requests'], r['restores']), (0, 0, 0, 0))
        self.assertFalse(r['stopped_early'])

    def test_an_endless_number_is_read_as_nothing_and_the_count_goes_on(self):
        t = NOW - DAY
        self.record([warm(t, 1), req(t + 300, 2, i=float('inf'), cr=0, cw=53000),
                     req(t + 400, 3).replace('"output_tokens": 500', '"output_tokens": 1e999')
                     .replace('"input_tokens": 10', '"input_tokens": 1' + '0' * 400), cold(t + 1000, 4)])
        r = self.run_count()
        self.assertEqual((r['records_read'], r['requests'], r['restores']), (1, 4, 2))

    def test_a_time_with_an_odd_digit_is_no_time_and_the_count_goes_on(self):
        t = NOW - DAY
        stamp = iso(t + 100)
        odd = warm(t + 100, 3).replace(stamp, stamp[:11] + '0\u00b2' + stamp[13:])
        self.record([warm(t, 1), odd, cold(t + 600, 2)])
        r = self.run_count()
        self.assertEqual((r['records_read'], r['requests'], r['restores']), (1, 2, 1))

    def test_a_line_nested_too_deep_is_skipped_and_counted(self):
        t = NOW - DAY
        deep = '{"type":"assistant","message":{"usage":' + '[' * 100000 + ']' * 100000 + '}}'
        self.record([warm(t, 1), deep, cold(t + 600, 2)])
        r = self.run_count()
        self.assertEqual((r['records_read'], r['bad_lines'], r['requests'], r['restores']), (1, 1, 2, 1))

    def test_a_synthetic_notice_is_not_a_request(self):
        t = NOW - DAY
        self.record([warm(t, 1), req(t + 400, 9, model='<synthetic>', cr=0, cw=0, i=0, o=0), cold(t + 600, 2)])
        r = self.run_count()
        self.assertEqual((r['requests'], r['restores']), (2, 1))


class Models(Base):
    def test_two_models_are_split_by_the_catalogs_family_names(self):
        t = NOW - DAY
        self.record([warm(t, 1), cold(t + 600, 2)])
        self.record([warm(t, 3, model=HAIKU), cold(t + 900, 4, model=HAIKU), cold(t + 1000, 5, model=HAIKU)])
        r = self.run_count()
        self.assertEqual(sorted(r['by_family']), ['Haiku', 'Opus'])
        self.assertEqual(r['by_family']['Opus'], {'requests': 2, 'restores': 1, 'restore_tokens': ALREADY,
                                                  'cache_writes': 55000})
        self.assertEqual(r['by_family']['Haiku']['requests'], 3)
        self.assertEqual(r['by_family']['Haiku']['restores'], 1)
        self.assertEqual(r['restores'], 2)

    def test_a_model_the_catalog_does_not_name_is_other_and_its_name_is_not_kept(self):
        t = NOW - DAY
        self.record([warm(t, 1, model='model-%s-7' % MARK), cold(t + 600, 2, model='model-%s-7' % MARK)])
        r = self.run_count()
        self.assertEqual(list(r['by_family']), ['Other'])
        self.assertNotIn(MARK, json.dumps(r))


class TheWindow(Base):
    def test_only_the_last_28_days_even_when_asked_for_more(self):
        self.record([warm(NOW - 29 * DAY, 1), cold(NOW - 29 * DAY + 600, 2)])
        self.record([warm(NOW - 27 * DAY, 3), cold(NOW - 27 * DAY + 600, 4)])
        r = self.run_count(since=NOW - 40 * DAY)
        self.assertEqual(r['since'], NOW - 28 * DAY)
        self.assertEqual(r['days'], 28)
        self.assertEqual((r['requests'], r['restores']), (2, 1))

    def test_a_shorter_window_is_kept(self):
        self.record([warm(NOW - 5 * DAY, 1), cold(NOW - 5 * DAY + 600, 2)])
        self.record([warm(NOW - 1 * DAY, 3), cold(NOW - 1 * DAY + 600, 4)])
        r = self.run_count(since=NOW - 2 * DAY)
        self.assertEqual((r['since'], r['days'], r['requests'], r['restores']), (NOW - 2 * DAY, 2, 2, 1))

    def test_nothing_after_now(self):
        self.record([warm(NOW + 100, 1), cold(NOW + 800, 2)])
        self.assertEqual(self.run_count()['requests'], 0)

    def test_a_record_last_changed_before_the_window_is_not_opened(self):
        path = self.record([warm(NOW - 3 * DAY, 1)])
        old = NOW - 30 * DAY
        os.utime(path, (old, old))
        real_open = open
        opened = []

        def watch(p, *a, **k):
            opened.append(p)
            return real_open(p, *a, **k)
        with mock.patch('builtins.open', watch):
            r = self.run_count(cache_file=False)
        self.assertNotIn(path, opened)
        self.assertEqual((r['records_read'], r['requests']), (0, 0))


class TimeLimit(Base):
    def test_it_stops_at_its_time_limit_and_says_so(self):
        for k in range(6):
            self.record([warm(NOW - DAY - k * 100, k)])
        ticks = iter(range(0, 10000, 10))
        r = self.run_count(limit_s=25, clock=lambda: next(ticks), cache_file=False)
        self.assertTrue(r['stopped_early'])
        self.assertGreater(r['records_left'], 0)
        self.assertLess(r['records_read'], 6)
        self.assertEqual(r['records_read'] + r['records_unread'] + r['records_left'], 6)
        self.assertEqual(r['requests'], r['records_read'])
        self.assertIn(W.STOPPED.format(s=25, read=r['records_read'], total=6), cc.say(r))

    def test_the_newest_records_are_read_first(self):
        for k in range(4):
            p = self.record([warm(NOW - DAY - k * 100, k)])
            os.utime(p, (NOW - (k + 1) * 1000, NOW - (k + 1) * 1000))
        newest = self.record([warm(NOW - 10, 99), cold(NOW - 5, 100)])
        os.utime(newest, (NOW, NOW))
        ticks = iter(range(0, 10000, 10))       # one tick for the search (one chat folder), then one per record
        r = self.run_count(limit_s=25, clock=lambda: next(ticks), cache_file=False)
        self.assertTrue(r['stopped_early'])
        self.assertEqual((r['records_read'], r['requests']), (1, 2))

    def test_a_long_record_is_stopped_inside_and_left_out_whole(self):
        t = NOW - DAY
        self.record([warm(t + k, k) for k in range(5000)])
        ticks = iter(range(0, 1000000, 10))     # the search at 10, the record at 20, its 100th line at 30
        r = self.run_count(limit_s=25, clock=lambda: next(ticks), cache_file=False)
        self.assertTrue(r['stopped_early'])
        self.assertEqual((r['records_read'], r['records_left'], r['requests']), (0, 1, 0))

    def test_a_few_very_long_request_lines_are_checked_against_the_clock_one_by_one(self):
        t = NOW - DAY
        pad = {'toolUseResult': {'stdout': 'x' * 1100000}}
        self.record([warm(t + k, k, extra=pad) for k in range(3)])
        ticks = iter(range(0, 10000, 10))       # the search at 10, the record at 20, its first line at 30
        r = self.run_count(limit_s=25, clock=lambda: next(ticks), cache_file=False)
        self.assertTrue(r['stopped_early'])
        self.assertEqual((r['records_read'], r['requests']), (0, 0))

    def test_looking_for_records_is_held_to_the_time_limit_too(self):
        for k in range(5):
            self.session = os.path.join(self.projects, '-Users-%s-site' % MARK, 'sess-%s-%d' % (MARK, k))
            self.record([warm(NOW - DAY - k * 100, k)])
        self.run_count()
        cache = os.path.join(self.data, 'router', 'cache-count.json')
        ticks = iter(range(0, 10000, 10))
        r = self.run_count(limit_s=25, clock=lambda: next(ticks))
        self.assertTrue(r['stopped_early'])
        self.assertFalse(r['found_all'])
        self.assertEqual(r['records_read'], 0)
        lines = cc.say(r)
        self.assertIn(W.STOPPED_LOOKING.format(s=25), lines)
        self.assertFalse(any(' of ' in x and 'helper records.' in x for x in lines), lines)
        with open(cache, encoding='utf-8') as f:
            self.assertEqual(len(json.load(f)['records']), 5)      # records it did not reach stay in the cache file
        self.assertTrue(self.run_count()['found_all'])

    def test_a_record_too_long_for_the_limit_does_not_hold_back_the_older_ones(self):
        big = self.record([warm(NOW - DAY + k, k) for k in range(500)])
        os.utime(big, (NOW, NOW))
        small = self.record([warm(NOW - 2 * DAY, 900)])
        os.utime(small, (NOW - DAY, NOW - DAY))

        def run(limit):
            ticks = iter(range(0, 100000, 10))      # one tick for the search, one per record, one per 100 lines
            return self.run_count(limit_s=limit, clock=lambda: next(ticks))
        first = run(45)         # the long one starts at 20 of 45, and is stopped at its 300th line
        self.assertTrue(first['stopped_early'])
        self.assertEqual((first['records_read'], first['records_left']), (0, 2))
        second = run(45)        # the long one is left out, and the older one is read
        self.assertEqual((second['records_read'], second['requests'], second['records_left']), (1, 1, 1))
        self.assertFalse(second['stopped_early'])
        self.assertIn(W.TOO_LONG_ONE.format(s=45), cc.say(second))
        self.assertEqual(run(45)['records_left'], 1)
        third = run(100000)     # a longer limit tries it again
        self.assertEqual((third['records_read'], third['requests'], third['records_left']), (2, 501, 0))

    def test_a_record_that_started_late_is_not_marked_too_long(self):
        small = self.record([warm(NOW - DAY, 900)])
        os.utime(small, (NOW, NOW))
        big = self.record([warm(NOW - 2 * DAY + k, k) for k in range(500)])
        os.utime(big, (NOW - DAY, NOW - DAY))
        for _ in range(2):
            ticks = iter(range(0, 100000, 10))      # the big one starts at 30 of 45: under half the limit was left
            r = self.run_count(limit_s=45, clock=lambda: next(ticks))
            self.assertEqual((r['records_read'], r['records_too_long'], r['records_left']), (1, 0, 1))
        with open(os.path.join(self.data, 'router', 'cache-count.json'), encoding='utf-8') as f:
            self.assertNotIn(cc.LONG_MARK, f.read())

    def test_only_records_too_long_are_never_said_as_no_records(self):
        r = dict(cc.empty(NOW - 28 * DAY, NOW), records_too_long=2, records_left=2, limit_s=60)
        self.assertEqual(cc.say(r), ['Switchboard has no count yet: it could not read a whole helper record from the '
                                     'last 28 days.', W.TOO_LONG_MANY.format(n=2, s=60)])

    def test_the_real_clock_keeps_the_limit(self):
        self.record([warm(NOW - DAY, 1)])
        r = self.run_count(limit_s=-1, cache_file=False)        # time.monotonic: the limit is passed at once
        self.assertTrue(r['stopped_early'])
        self.assertFalse(r['found_all'])
        self.assertEqual(r['records_read'], 0)
        r = self.run_count(limit_s=600, cache_file=False)
        self.assertEqual((r['stopped_early'], r['found_all'], r['records_read']), (False, True, 1))
        self.assertGreaterEqual(r['took_s'], 0.0)

    def test_the_default_limit_is_sixty_seconds(self):
        self.assertEqual(cc.LIMIT_S, 60)
        self.record([warm(NOW - DAY, 1)])
        r = self.run_count()
        self.assertFalse(r['stopped_early'])
        self.assertEqual(r['records_left'], 0)


class NoWords(Base):
    def test_the_marker_is_in_nothing_returned_said_printed_or_written(self):
        t = NOW - DAY
        self.record([warm(t, 1), user(t + 1), cold(t + 600, 2), '{"broken %s' % MARK])
        self.record([warm(t, 3, model=HAIKU), cold(t + 900, 4, model=HAIKU)], workflow=True)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            r = self.run_count()
            lines = cc.say(r)
        self.assertEqual(r['restores'], 2)
        self.assertNotIn(MARK, json.dumps(r))
        self.assertNotIn(MARK, ' '.join(lines))
        self.assertEqual(out.getvalue() + err.getvalue(), '')
        written = []
        for dirpath, _, files in os.walk(self.data):
            for fn in files:
                written.append(os.path.join(dirpath, fn))
        self.assertTrue(written)
        for path in written:
            with open(path, 'rb') as f:
                raw = f.read()
            self.assertNotIn(MARK.encode(), raw, path)
            self.assertNotIn(b'Users', raw, path)
            self.assertNotIn(b'msg_', raw, path)
            self.assertNotIn(b'req_', raw, path)

    def test_only_numbers_and_family_names_come_back(self):
        t = NOW - DAY
        self.record([warm(t, 1), cold(t + 600, 2)])
        r = self.run_count()

        def strings(x):
            if isinstance(x, str):
                yield x
            elif isinstance(x, dict):
                for k, v in x.items():
                    yield from strings(k)
                    yield from strings(v)
            elif isinstance(x, (list, tuple)):
                for v in x:
                    yield from strings(v)
        names = set(cc.RESULT_KEYS) | set(cc.FAMILY_KEYS) | {'Opus', 'Haiku', 'Sonnet', 'Fable', 'Other'}
        self.assertLessEqual(set(strings(r)), names)


class TheCacheFile(Base):
    def path(self):
        return os.path.join(self.data, 'router', 'cache-count.json')

    def test_a_second_run_takes_unchanged_records_from_the_cache_file(self):
        t = NOW - DAY
        self.record([warm(t, 1), cold(t + 600, 2)])
        self.record([warm(t, 3, model=HAIKU)])
        first = self.run_count()
        self.assertEqual(first['from_cache'], 0)
        self.assertEqual(stat.S_IMODE(os.stat(self.path()).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(self.path())).st_mode), 0o700)
        with mock.patch.object(cc, '_read_record', side_effect=AssertionError('read again')):
            second = self.run_count()
        self.assertEqual(second['from_cache'], 2)
        for k in ('records_read', 'requests', 'restores', 'restore_tokens', 'cache_writes', 'by_family'):
            self.assertEqual(second[k], first[k], k)

    def test_a_record_that_changed_is_read_again(self):
        t = NOW - DAY
        path = self.record([warm(t, 1)])
        self.run_count()
        with open(path, 'a', encoding='utf-8') as f:
            f.write(cold(t + 600, 2) + '\n')
        r = self.run_count()
        self.assertEqual((r['from_cache'], r['requests'], r['restores']), (0, 2, 1))

    def test_a_record_across_the_window_edge_is_read_again_not_taken_whole(self):
        self.record([warm(NOW - 10 * DAY, 1), warm(NOW - DAY, 2), cold(NOW - DAY + 600, 3)])
        self.assertEqual(self.run_count()['requests'], 3)
        r = self.run_count(since=NOW - 2 * DAY)
        self.assertEqual((r['from_cache'], r['requests'], r['restores']), (0, 2, 1))

    def test_records_no_longer_there_leave_the_cache_file(self):
        t = NOW - DAY
        a = self.record([warm(t, 1)])
        self.record([warm(t, 2)])
        self.run_count()
        with open(self.path(), encoding='utf-8') as f:
            self.assertEqual(len(json.load(f)['records']), 2)
        os.unlink(a)
        self.run_count()
        with open(self.path(), encoding='utf-8') as f:
            self.assertEqual(len(json.load(f)['records']), 1)

    def test_a_count_stopped_early_keeps_what_the_cache_file_knew_of_the_records_it_did_not_reach(self):
        for k in range(3):
            self.record([warm(NOW - DAY - k * 100, k)])
        self.run_count()
        ticks = iter(range(0, 10000, 10))
        r = self.run_count(limit_s=15, clock=lambda: next(ticks))
        self.assertTrue(r['stopped_early'])
        with open(self.path(), encoding='utf-8') as f:
            self.assertEqual(len(json.load(f)['records']), 3)

    def test_a_broken_cache_file_is_ignored_and_written_again(self):
        self.record([warm(NOW - DAY, 1)])
        os.makedirs(os.path.dirname(self.path()))
        with open(self.path(), 'w', encoding='utf-8') as f:
            f.write('{not json')
        r = self.run_count()
        self.assertEqual((r['from_cache'], r['requests']), (0, 1))
        with open(self.path(), encoding='utf-8') as f:
            self.assertEqual(json.load(f)['v'], cc.CACHE_VERSION)

    def test_the_cache_file_is_written_by_switchboards_own_writer(self):
        from switchboard import core
        self.record([warm(NOW - DAY, 1)])
        before = core.writes()
        with mock.patch.object(core, 'write_bytes', wraps=core.write_bytes) as wrote:
            self.run_count()
        self.assertEqual(core.writes(), before + 1)
        self.assertEqual(wrote.call_args[0][0], self.path())
        self.assertEqual(os.listdir(os.path.dirname(self.path())), ['cache-count.json'])

    def test_a_cache_file_that_cannot_be_written_leaves_the_count_whole(self):
        from switchboard import core
        self.record([warm(NOW - DAY, 1)])
        full = core.DiskFull(28, 'No space left on device', self.path())
        with mock.patch.object(core, 'write_bytes', side_effect=full):
            r = self.run_count()
        self.assertEqual(r['requests'], 1)

    def test_a_cache_file_entry_of_the_wrong_shape_is_read_again_and_no_word_comes_back_through_it(self):
        t = NOW - DAY
        self.record([warm(t, 1), cold(t + 600, 2)])
        first = self.run_count()
        with open(self.path(), encoding='utf-8') as f:
            good = json.load(f)
        key = next(iter(good['records']))
        entry = good['records'][key]
        for bad in (entry[:4] + ['x'] + entry[5:], entry[:5] + [None] + entry[6:], entry[:6] + [-5] + entry[7:],
                    entry[:4] + [True] + entry[5:], entry[:-1] + [{MARK: [1, 1, 1, 1]}],
                    entry[:-1] + [{'Opus': [1, 'x', 1, 1]}], entry[:-1] + [{'Opus': [1, 1]}]):
            with self.subTest(bad=bad):
                with open(self.path(), 'w', encoding='utf-8') as f:
                    json.dump(dict(good, records={key: bad}), f)
                r = self.run_count()
                self.assertEqual(r['from_cache'], 0)
                self.assertEqual((r['requests'], r['restores'], r['by_family']),
                                 (first['requests'], first['restores'], first['by_family']))
                self.assertNotIn(MARK, json.dumps(r))

    def test_the_cache_files_keys_are_salted_so_a_guessed_place_cannot_be_checked_against_them(self):
        import hashlib
        path = self.record([warm(NOW - DAY, 1)])
        self.run_count()
        with open(self.path(), encoding='utf-8') as f:
            saved = json.load(f)
        plain = hashlib.blake2b(path.encode('utf-8'), digest_size=12).hexdigest()
        self.assertNotIn(plain, saved['records'])
        self.assertRegex(saved.get('salt', ''), r'^[0-9a-f]{32}$')
        self.assertEqual(self.run_count()['from_cache'], 1)
        with open(self.path(), encoding='utf-8') as f:
            self.assertEqual(json.load(f)['salt'], saved['salt'])

    def test_no_cache_file_when_told_not_to(self):
        self.record([warm(NOW - DAY, 1)])
        self.run_count(cache_file=False)
        self.assertFalse(os.path.exists(self.data))

    def test_the_default_cache_file_is_under_switchboards_data_folder(self):
        self.record([warm(NOW - DAY, 1)])
        cc.count([self.projects], now=NOW)
        home = os.environ['SWITCHBOARD_HOME']
        self.assertTrue(os.path.isfile(os.path.join(home, 'router', 'cache-count.json')))


class Words(Base):
    def test_every_sentence_keeps_the_word_rules(self):
        names = [n for n in dir(W) if n.isupper() and isinstance(getattr(W, n), str)]
        self.assertGreaterEqual(len(names), 5)
        for n in names:
            text = getattr(W, n)
            with self.subTest(name=n):
                self.assertEqual(problems(text), [], text)
                self.assertNotRegex(text, r'(?i)sav(e|es|ed|ing)\b|cheaper|less money|\$|percent|%|best model')
                self.assertNotIn('\n', text)

    def test_every_line_say_makes_with_real_numbers_keeps_the_word_rules(self):
        base = dict(cc.empty(NOW - 28 * DAY, NOW), requests=9000, hour_would_read=1234567, hour_would_write=7654321)
        states = [base, dict(base, records_read=40, restores=2010), dict(base, records_read=40, restores=1),
                  dict(base, records_read=40), dict(base, records_read=3, records_unread=1, stopped_early=True,
                                                    records_left=7, records_too_long=2),
                  dict(base, records_unread=1), dict(base, stopped_early=True, found_all=False),
                  dict(base, records_read=1, records_too_long=1, records_left=1)]
        for st in states:
            for text in cc.say(st):
                with self.subTest(text=text):
                    self.assertEqual(problems(text), [], text)
                    self.assertNotRegex(text, r'(?i)sav(e|es|ed|ing)\b|cheaper|less money|\$|percent|%|best model|'
                                              r'\bcosts?\b|\{|\}')

    def test_what_the_switch_shows_from_a_count(self):
        t = NOW - DAY
        self.record([warm(t, 1), cold(t + 600, 2), warm(t + 700, 3), cold(t + 1400, 4)])
        lines = cc.say(self.run_count())
        self.assertEqual(lines[0], 'In the last 28 days your helpers stored a context of 20,000 tokens or more again '
                                   '2 times after a wait of five minutes to an hour.')
        self.assertIn(W.WEIGH, lines)
        self.assertTrue(any('105,020' in x for x in lines), lines)

    def test_thousands_are_written_with_commas_and_one_is_once(self):
        r = dict(cc.empty(NOW - 28 * DAY, NOW), records_read=3, requests=9000, restores=2010)
        self.assertIn('2,010 times', cc.say(r)[0])
        r['restores'] = 1
        self.assertIn('tokens or more again once after', cc.say(r)[0])

    def test_no_records_and_no_restores_are_said_plainly(self):
        self.assertEqual(cc.say(cc.empty(NOW - 28 * DAY, NOW)), [W.NO_RECORDS.format(span='the last 28 days')])
        r = dict(cc.empty(NOW - 28 * DAY, NOW), records_read=4, requests=40)
        self.assertEqual(cc.say(r)[0], W.NONE.format(when='In the last 28 days', size='20,000'))

    def test_records_that_could_not_be_read_are_said(self):
        r = dict(cc.empty(NOW - 28 * DAY, NOW), records_read=4, records_unread=2, requests=40, restores=3)
        self.assertIn(W.UNREAD_MANY.format(n=2), cc.say(r))


class WhatIsSaid(Base):
    """The count's sentences are true in every state count() can reach."""

    def test_no_record_read_is_never_said_as_no_restore(self):
        r = dict(cc.empty(NOW - 28 * DAY, NOW), records_unread=3)
        lines = cc.say(r)
        self.assertFalse(any('no helper' in x.lower() for x in lines), lines)
        self.assertEqual(lines[0], 'Switchboard has no count yet: it could not read a whole helper record from the '
                                   'last 28 days.')
        self.assertIn(W.UNREAD_MANY.format(n=3), lines)

    def test_stopped_before_the_first_record_is_never_said_as_no_restore(self):
        r = dict(cc.empty(NOW - 28 * DAY, NOW), stopped_early=True, records_left=5, limit_s=60)
        lines = cc.say(r)
        self.assertFalse(any('no helper' in x.lower() for x in lines), lines)
        self.assertTrue(lines[0].startswith('Switchboard has no count yet'), lines)

    def test_no_restore_in_part_of_the_records_names_how_many_were_read(self):
        r = dict(cc.empty(NOW - 28 * DAY, NOW), records_read=4, records_unread=1, requests=40)
        self.assertEqual(cc.say(r)[0], 'In the 4 helper records Switchboard could read, no helper stored a context of '
                                       '20,000 tokens or more again after a wait of five minutes to an hour.')
        r = dict(cc.empty(NOW - 28 * DAY, NOW), records_read=1, stopped_early=True, records_left=2, requests=4)
        self.assertEqual(cc.say(r)[0], 'In the one helper record Switchboard could read, no helper stored a context '
                                       'of 20,000 tokens or more again after a wait of five minutes to an hour.')

    def test_a_count_says_a_forks_copied_requests_can_count_twice(self):
        base = dict(cc.empty(NOW - 28 * DAY, NOW), records_read=4, requests=40)
        self.assertEqual(cc.say(dict(base, restores=3))[1], 'A fork starts with a copy of the requests before it, so '
                                                            'those can be counted twice.')
        self.assertFalse(any('fork' in x for x in cc.say(base)))

    def test_a_window_under_a_day_is_said_as_today_and_never_as_28_days(self):
        today = cc.empty(NOW - 3600, NOW)
        self.assertEqual(today['days'], 0)
        self.assertEqual(cc.say(today), ['Switchboard found no helper records from today on this Mac.'])
        self.assertTrue(cc.say(dict(today, records_read=2, restores=3))[0].startswith('Today your helpers stored'))
        self.assertTrue(cc.say(dict(cc.empty(NOW - DAY, NOW), records_read=2))[0].startswith('In the last day no '))
        self.assertTrue(cc.say(dict(cc.empty(NOW - 5 * DAY, NOW), records_unread=2))[0].endswith(
            'from the last 5 days.'))

    def test_every_count_sentence_says_the_size_it_counts(self):
        base = dict(cc.empty(NOW - 28 * DAY, NOW), records_read=4, requests=40)
        for n in (0, 1, 2010):
            with self.subTest(n=n):
                first = cc.say(dict(base, restores=n))[0]
                self.assertIn('a context of 20,000 tokens or more', first)
        self.assertEqual(cc.say(base)[0], 'In the last 28 days no helper stored a context of 20,000 tokens or more '
                                          'again after a wait of five minutes to an hour.')
        self.assertIn(cc._num(cc.MIN_CONTEXT), cc.say(dict(base, restores=3))[0])


if __name__ == '__main__':
    unittest.main()
