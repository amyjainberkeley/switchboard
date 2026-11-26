import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# The usage poller and the readings store. Every outside thing is fake: the network (429 with Retry-After,
# timeouts, hangs), the token source, the clock (wall and monotonic apart).
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import core
from switchboard.accounts import api, usage
from fakes_autoswitch import FakeClock, FakeNet, FakeTokens, Home, T0, iso, usage_body

FIVE, WEEK, FABLE = '5-hour', 'weekly', 'model:Fable'
LINES = {FIVE: 90.0, WEEK: 95.0}
LINES_FABLE = {FIVE: 90.0, WEEK: 95.0, FABLE: 95.0}


def ok(body):
    return (200, {}, body)


class ParsingTheEndpoint(unittest.TestCase):
    def test_a_normal_answer_gives_three_windows_and_never_spend(self):
        w = usage.parse_usage(usage_body(five=41.5, week=63.0, models={'Fable': (12.0, T0 + 3600)},
                                         five_reset=T0 + 600, week_reset=T0 + 86400))
        self.assertEqual(w[FIVE], (41.5, T0 + 600))
        self.assertEqual(w[WEEK], (63.0, T0 + 86400))
        self.assertEqual(w[FABLE], (12.0, T0 + 3600))
        self.assertEqual(sorted(w), [FIVE, FABLE, WEEK])          # extra_usage (spend) is never a window

    def test_an_ultra_account_has_no_five_hour_window(self):
        w = usage.parse_usage(usage_body(week=20.0, week_reset=T0 + 9, null_five=True))
        self.assertNotIn(FIVE, w)
        self.assertEqual(w[WEEK][0], 20.0)

    def test_a_team_seat_reads_session_and_model_entries(self):
        body = {'five_hour': None, 'seven_day': None, 'limits': [
            {'kind': 'session', 'percent': 33, 'resets_at': iso(T0 + 100), 'scope': None},
            {'kind': 'weekly_scoped', 'percent': 70, 'scope': {'model': {'display_name': 'Opus'}}},
            {'kind': 'weekly_scoped', 'percent': 'x', 'scope': {'model': {'display_name': 'Bad'}}},
            {'kind': 'weekly_scoped', 'percent': 5, 'scope': {}}]}
        w = usage.parse_usage(body)
        self.assertEqual(w[FIVE], (33.0, T0 + 100))
        self.assertEqual(w['model:Opus'], (70.0, None))
        self.assertEqual(len(w), 2)

    def test_utilization_null_keeps_the_window_with_a_number_not_known(self):
        w = usage.parse_usage({'five_hour': {'utilization': None, 'resets_at': iso(T0 + 5)}})
        self.assertEqual(w[FIVE], (None, T0 + 5))

    def test_a_malformed_window_fails_the_whole_answer(self):
        for bad in ({'utilization': 'high'}, {'utilization': -1}, {'utilization': True}, {'resets_at': 'x'}, 'text',
                    ['a']):
            with self.assertRaises(usage.BadResponse, msg=repr(bad)):
                usage.parse_usage({'five_hour': {'utilization': 3}, 'seven_day': bad})
        with self.assertRaises(usage.BadResponse):
            usage.parse_usage(['not', 'an', 'object'])

    def test_iso_times_in_every_form_anthropic_and_python_disagree_on(self):
        base = usage.parse_iso('2026-11-16T20:39:00+00:00')
        self.assertIsNotNone(base)
        for text in ('2026-11-16T20:39:00Z', '2026-11-16T20:39:00.123+00:00', '2026-11-16T20:39:00.1234567+00:00',
                     '2026-11-16T20:39:00.5Z', '2026-11-16T20:39:00'):
            self.assertAlmostEqual(usage.parse_iso(text), base, delta=1, msg=text)
        for text in ('soon', '', None, 5, '2026-13-40T99:00:00Z'):
            self.assertIsNone(usage.parse_iso(text))

    def test_retry_after_is_seconds_only_and_never_unbounded(self):
        self.assertEqual(usage.parse_retry_after('600'), 600.0)
        self.assertEqual(usage.parse_retry_after('0'), 0.0)
        self.assertEqual(usage.parse_retry_after('-5'), 0.0)
        self.assertEqual(usage.parse_retry_after('inf'), usage.RETRY_CAP_429)
        self.assertEqual(usage.parse_retry_after('1e400'), usage.RETRY_CAP_429)
        self.assertIsNone(usage.parse_retry_after('Wed, 21 Oct 2015 07:28:00 GMT'))
        self.assertIsNone(usage.parse_retry_after('nan'))
        self.assertIsNone(usage.parse_retry_after(None))


class Poller(Home):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock()
        self.net = FakeNet(self.clock)
        self.tokens = FakeTokens()
        self.p = usage.Poller(clock=self.clock, net=self.net, token_for=self.tokens, rand=lambda: 0.5)

    def ctx(self, active='a1', order=('a1', 'a2', 'a3'), lines=LINES, **kw):
        return usage.Context(active=active, order=order, lines=lines, **kw)

    def body(self, five=10.0, week=20.0, **kw):
        return ok(usage_body(five=five, week=week, five_reset=kw.pop('five_reset', T0 + 4 * 3600),
                             week_reset=kw.pop('week_reset', T0 + 4 * 86400), **kw))

    def test_one_pass_reads_the_active_account_and_two_others_with_an_honest_user_agent(self):
        for t in ('tok-a1', 'tok-a2', 'tok-a3', 'tok-a4'):
            self.net.set(t, self.body())
        start = self.clock.wall()
        rep = self.p.poll(self.ctx(order=('a1', 'a2', 'a3', 'a4')))
        self.assertEqual(rep.tried, ['a1', 'a2', 'a3'])
        tok, headers = self.net.calls[0]
        self.assertEqual(headers['User-Agent'], 'Switchboard/%s' % core.VERSION)
        self.assertEqual(headers['anthropic-beta'], 'oauth-2025-04-20')
        self.assertNotIn('claude-code', headers['User-Agent'].lower())
        r = usage.readings(now=self.clock.wall())
        self.assertEqual(sorted(r), ['a1', 'a2', 'a3'])
        self.assertEqual(r['a1'].source, 'endpoint')
        self.assertEqual([w.name for w in r['a1'].windows], [FIVE, WEEK])
        self.assertEqual(r['a1'].read_at, start)                        # each number dated at its own answer
        self.assertGreater(r['a3'].read_at, start)                       # (reads are spaced out in a pass)

    def test_the_token_never_reaches_a_log_or_the_store(self):
        self.net.set('tok-a1', (429, {'retry-after': '60'}, {'error': 'tok-a1'}))
        self.p.poll(self.ctx(order=('a1',)))
        with open(core.data_path('usage.json')) as f:
            self.assertNotIn('tok-a1', f.read())
        log = core.data_path('switchboard.log')
        if os.path.exists(log):
            with open(log) as f:
                self.assertNotIn('tok-a1', f.read())

    def test_a_429_with_retry_after_600_means_no_read_of_that_account_for_600_seconds(self):
        self.net.set('tok-a1', (429, {'Retry-After': '600'}, {}), self.body())
        rep = self.p.poll(self.ctx(order=('a1',)))
        self.assertEqual(rep.failed, {'a1': 'http-429'})
        r = usage.readings(now=self.clock.wall())['a1']
        self.assertTrue(r.frozen)
        self.assertIn('limiting usage reads', r.error)
        self.assertIn('can only be higher', r.error)
        for _ in range(9):
            self.clock.advance(66)                 # 594 s in all
            self.p.poll(self.ctx(order=('a1',)))
        self.assertEqual(self.net.count('tok-a1'), 1)
        self.clock.advance(7)
        self.p.poll(self.ctx(order=('a1',)))
        self.assertEqual(self.net.count('tok-a1'), 2)
        self.assertFalse(usage.readings(now=self.clock.wall())['a1'].frozen)

    def test_a_long_retry_after_gets_a_margin_and_never_more_than_75_minutes(self):
        self.net.set('tok-a1', (429, {'retry-after': '3600'}, {}))
        self.p.poll(self.ctx(order=('a1',)))
        p = self.p.plans['a1']
        self.assertAlmostEqual(p.until - self.clock.mono(), 4500, delta=1)
        self.net.set('tok-a2', (429, {'retry-after': '86400'}, {}))
        self.p.poll(self.ctx(active='a2', order=('a2',)))
        self.assertAlmostEqual(self.p.plans['a2'].until - self.clock.mono(), 4500, delta=1)

    def test_retry_after_zero_and_none_on_429(self):
        self.net.set('tok-a1', (429, {'retry-after': '0'}, {}))
        self.net.set('tok-a2', (429, {}, {}))
        self.p.poll(self.ctx(order=('a1', 'a2')))
        self.assertAlmostEqual(self.p.plans['a1'].until - self.clock.mono(), 300, delta=1)
        self.assertAlmostEqual(self.p.plans['a2'].until - self.clock.mono(), 360, delta=1)

    def test_after_a_429_reads_slow_down_for_an_hour(self):
        self.net.set('tok-a2', (429, {'retry-after': '400'}, {}), self.body(), self.body(), self.body())
        ctx = self.ctx(active='a1', order=('a2',))
        self.p.poll(ctx)
        self.clock.advance(401)
        self.p.poll(ctx)
        first = self.p.plans['a2'].interval
        self.assertGreaterEqual(first, usage.AIMD_MIN)
        self.clock.advance(first * 1.2)
        self.p.poll(ctx)
        self.assertAlmostEqual(self.p.plans['a2'].interval, min(usage.AIMD_MAX, first * 1.5), delta=1)

    def test_never_more_than_24_reads_an_hour_for_one_account(self):
        self.net.set('tok-a2', self.body())
        for _ in range(60):
            self.p.poll(self.ctx(order=('a2',)), only=['a2'])        # asked again and again (before a switch)
            self.clock.advance(30)
        self.assertEqual(self.net.count('tok-a2'), usage.BUDGET)

    def test_network_failures_back_off_doubling_and_the_exponent_never_overflows(self):
        self.net.set('tok-a1', 'network')
        waits = []
        for _ in range(6):
            self.p.poll(self.ctx(order=('a1',)), only=['a1'])
            p = self.p.plans['a1']
            waits.append(round(p.until - self.clock.mono()))
            self.clock.advance(p.until - self.clock.mono() + 1)
        self.assertEqual(waits, [60, 120, 240, 480, 960, 1800])
        self.p.plans['a1'].fails = 5000
        self.p._failed(self.p.plans['a1'], 'network', None)
        self.assertEqual(round(self.p.plans['a1'].until - self.clock.mono()), 1800)

    def test_one_account_failing_never_stops_the_others(self):
        self.net.set('tok-a1', 'boom')
        self.net.set('tok-a2', self.body())
        rep = self.p.poll(self.ctx(order=('a1', 'a2')))
        self.assertEqual(rep.failed, {'a1': 'error'})
        self.assertEqual(rep.ok, ['a2'])
        self.assertIn('a2', usage.readings(now=self.clock.wall()))

    def test_a_401_gets_one_fresh_token_for_another_account_and_none_for_the_active_one(self):
        self.net.set('tok-a2', (401, {}, {}))
        self.net.set('new-a2', self.body())
        self.tokens.fresh_value['a2'] = 'new-a2'
        rep = self.p.poll(self.ctx(active='a1', order=('a2',)))
        self.assertEqual(rep.ok, ['a2'])
        self.assertIn(('a2', True), self.tokens.calls)
        self.net.set('tok-a1', (401, {}, {}))
        self.tokens.fresh_value['a1'] = 'new-a1'
        rep = self.p.poll(self.ctx(active='a1', order=('a1',)))
        self.assertEqual(rep.failed, {'a1': 'http-401'})
        self.assertNotIn(('a1', True), self.tokens.calls)          # Claude Code owns the active login

    def test_an_organization_that_refuses_oauth_is_named(self):
        body = {'error': {'details': {'error_code': 'oauth_not_allowed_for_organization'}}}
        self.net.set('tok-a1', (403, {}, body))
        self.p.poll(self.ctx(order=('a1',)))
        r = usage.readings(now=self.clock.wall())['a1']
        self.assertIn('organization does not allow', r.error)

    def test_a_read_that_hangs_ends_at_its_deadline_and_hanging_reads_are_capped(self):
        self.net.set('tok-a1', 'hang')
        self.releases.append(self.net.release)
        rep = self.p.poll(self.ctx(order=('a1',)))
        self.assertEqual(rep.failed, {'a1': 'timeout'})
        for aid in ('h2', 'h3'):
            self.net.set('tok-%s' % aid, 'hang')
            self.p.poll(self.ctx(active=aid, order=(aid,)))
        calls = len(self.net.calls)
        self.net.set('tok-h4', self.body())
        rep = self.p.poll(self.ctx(active='h4', order=('h4',)))
        self.assertEqual(rep.failed, {'h4': 'timeout'})              # three still hang: no fourth thread
        self.assertEqual(len(self.net.calls), calls)

    def test_a_token_fetch_that_hangs_costs_its_deadline_and_the_pass_goes_on(self):
        self.tokens.clock = self.clock
        self.tokens.hang['a1'] = self.blocker()
        self.net.set('tok-a2', self.body())
        started = self.clock.mono()
        rep = self.p.poll(self.ctx(order=('a1', 'a2')))
        self.assertEqual(rep.failed, {'a1': 'no-access-token'})
        self.assertEqual(rep.ok, ['a2'])
        self.assertLess(self.clock.mono() - started, usage.TOKEN_DEADLINE + usage.READ_DEADLINE + 2)
        self.assertEqual(self.net.count('tok-a1'), 0)

    def test_two_passes_never_run_at_once(self):
        self.net.set('tok-a1', 'hang')
        self.releases.append(self.net.release)
        held = self.p._polling.acquire(False)
        self.assertTrue(held)
        try:
            self.assertEqual(self.p.poll(self.ctx(order=('a1',))).skipped, 'busy')
        finally:
            self.p._polling.release()
        self.assertEqual(self.net.count(), 0)

    def test_an_expired_active_token_sends_nothing_and_spends_no_budget(self):
        self.tokens.answers['a1'] = api.Read(api.UNREADABLE, why='expired')
        rep = self.p.poll(self.ctx(order=('a1',)))
        self.assertEqual(rep.failed, {'a1': 'token-expired'})
        self.assertEqual(self.net.count(), 0)
        self.assertEqual(len(self.p.plans['a1'].reads), 0)
        self.assertFalse(rep.offline)
        self.assertIn('renews it when it is next used', usage.readings(now=self.clock.wall())['a1'].error)

    def test_a_lower_number_without_a_reset_never_replaces_an_open_window(self):
        self.net.set('tok-a1', self.body(five=40.0, week=55.0),
                     ok({'five_hour': {'utilization': 0, 'resets_at': None},
                         'seven_day': {'utilization': 0, 'resets_at': None}}))
        self.p.poll(self.ctx(order=('a1',)))
        first = self.clock.wall()
        self.clock.advance(400)
        self.p.poll(self.ctx(order=('a1',)), only=['a1'])
        r = usage.readings(now=self.clock.wall())['a1']
        self.assertEqual([(w.name, w.pct) for w in r.windows], [(FIVE, 40.0), (WEEK, 55.0)])
        self.assertEqual(r.read_at, first)                           # and it does not look fresh either

    def test_a_lower_number_with_a_new_reset_is_a_new_window(self):
        self.net.set('tok-a1', self.body(five=80.0), self.body(five=3.0, five_reset=T0 + 9 * 3600))
        self.p.poll(self.ctx(order=('a1',)))
        self.clock.advance(400)
        self.p.poll(self.ctx(order=('a1',)), only=['a1'])
        self.assertEqual(usage.readings(now=self.clock.wall())['a1'].windows[0].pct, 3.0)

    def test_a_model_window_the_endpoint_no_longer_reports_is_gone(self):
        self.net.set('tok-a1', self.body(models={'Fable': (50.0, T0 + 86400)}), self.body())
        self.p.poll(self.ctx(order=('a1',)))
        self.assertIn(FABLE, [w.name for w in usage.readings(now=self.clock.wall())['a1'].windows])
        self.clock.advance(400)
        self.p.poll(self.ctx(order=('a1',)), only=['a1'])
        self.assertNotIn(FABLE, [w.name for w in usage.readings(now=self.clock.wall())['a1'].windows])

    def test_a_window_whose_reset_passed_reads_not_known_never_the_old_number(self):
        self.net.set('tok-a1', self.body(five=95.0, five_reset=T0 + 100))
        self.p.poll(self.ctx(order=('a1',)))
        self.assertEqual(usage.readings(now=T0 + 50)['a1'].windows[0].pct, 95.0)
        w = usage.readings(now=T0 + 101)['a1'].windows[0]
        self.assertIsNone(w.pct)
        self.assertEqual(w.resets_at, T0 + 100)

    def test_fresh_status_line_numbers_mean_no_endpoint_read_for_the_active_account(self):
        self.assertTrue(usage.feed_status_line('a1', {'five_hour': {'used_percentage': 30, 'resets_at': T0 + 3600},
                                                      'seven_day': {'used_percentage': 40,
                                                                    'resets_at': T0 + 86400}},
                                               self.clock.wall(), now=self.clock.wall()))
        self.net.set('tok-a1', self.body())
        rep = self.p.poll(self.ctx(order=('a1',)))
        self.assertEqual(rep.tried, [])
        rep = self.p.poll(self.ctx(order=('a1',), lines=LINES_FABLE))       # a model window counts: only the
        self.assertEqual(rep.tried, ['a1'])                                  # endpoint has it
        self.clock.advance(usage.STATUS_LINE_FRESH + 1)
        rep = self.p.poll(self.ctx(order=('a1',)))
        self.assertEqual(rep.tried, [])                                      # its own plan: 5 minutes
        self.clock.advance(usage.ACTIVE_EVERY)
        rep = self.p.poll(self.ctx(order=('a1',)))
        self.assertEqual(rep.tried, ['a1'])

    def test_cadence_near_a_line_far_and_idle_and_at_a_limit(self):
        self.net.set('tok-a1', self.body(five=80.0))                         # near its 90 line
        self.net.set('tok-a2', self.body(five=10.0))
        self.p.poll(self.ctx(order=('a1', 'a2')))
        self.assertEqual(self.p.plans['a1'].interval, usage.NEAR_EVERY)
        self.assertEqual(self.p.plans['a2'].interval, usage.NEAR_EVERY)     # a switch may come: candidates fresh
        self.net.set('tok-b1', self.body(five=10.0, week=10.0))
        self.net.set('tok-b2', self.body(five=10.0))
        ctx = self.ctx(active='b1', order=('b1', 'b2'))
        self.p.poll(ctx)
        self.clock.advance(400)
        self.p.poll(ctx, only=['b2'])
        self.assertEqual(self.p.plans['b2'].interval, usage.FAR_IDLE_EVERY)
        busy = self.ctx(active='b1', order=('b1', 'b2'), busy=True)
        self.clock.advance(400)
        self.p.poll(busy, only=['b2'])
        self.assertEqual(self.p.plans['b2'].interval, usage.OTHERS_EVERY)
        self.net.set('tok-c2', self.body(five=100.0, five_reset=self.clock.wall() + 200))
        self.p.poll(self.ctx(active='c1', order=('c2',)))
        p = self.p.plans['c2']
        self.assertEqual(p.interval, usage.EXHAUSTED_EVERY)
        self.assertAlmostEqual(p.next - self.clock.mono(), 200 + usage.RESET_SLACK, delta=1)

    def test_others_are_read_every_90_s_once_the_active_account_is_near_a_line(self):
        self.net.set('tok-a1', self.body(five=20.0, week=20.0))
        self.net.set('tok-a2', self.body(five=10.0, week=10.0))
        ctx = self.ctx(order=('a1', 'a2'))
        self.p.poll(ctx)
        self.assertEqual(self.p.plans['a2'].interval, usage.FAR_IDLE_EVERY)          # far and idle: 15 minutes
        self.net.set('tok-a1', self.body(five=80.0, week=20.0))                      # the active one climbs
        self.clock.advance(usage.ACTIVE_EVERY + 1)
        self.p.poll(ctx)                                                              # reads a1; a2 not due yet
        self.clock.advance(5)
        rep = self.p.poll(ctx)
        self.assertEqual(rep.tried, ['a2'])                                          # near a line: escalated

    def test_a_low_battery_slows_reads_and_a_busy_mac_does_not(self):
        self.net.set('tok-a2', self.body())
        self.p.poll(self.ctx(active='a1', order=('a2',), busy=True, battery_factor=3.0))
        p = self.p.plans['a2']
        self.assertAlmostEqual(p.next - self.clock.mono(), usage.OTHERS_EVERY * 3, delta=1)
        with self.assertRaises(TypeError):
            self.ctx(load_factor=2.0)                                  # the load is no longer the poller's to know

    def test_after_a_wake_every_account_once_and_a_429_wait_still_holds(self):
        order = ('a1', 'a2', 'a3', 'a4', 'a5')
        for a in order:
            self.net.set('tok-' + a, self.body())
        self.net.set('tok-a5', (429, {'retry-after': '1200'}, {}))
        self.net.set('tok-a4', 'network')
        self.p.poll(self.ctx(order=order))
        self.p.poll(self.ctx(order=order))
        self.net.set('tok-a4', self.body())
        n = len(self.net.calls)
        self.clock.advance(30)
        self.p.wake()
        rep = self.p.poll(self.ctx(order=order))
        self.assertEqual(sorted(rep.tried), ['a1', 'a2', 'a3', 'a4'])        # a4's network wait lifted, a5's 429 not
        self.assertEqual(rep.tried[0], 'a1')
        self.assertEqual(len(self.net.calls), n + 4)

    def test_a_wake_asked_for_before_a_plan_is_loaded_still_reads_that_account(self):
        self.net.set('tok-a2', self.body())
        self.p.poll(self.ctx(active='a1', order=('a2',)))                  # a2 planned minutes out, then a restart
        p2 = usage.Poller(clock=self.clock, net=self.net, token_for=self.tokens, rand=lambda: 0.5)
        p2.wake()
        self.clock.advance(10)
        self.assertEqual(p2.poll(self.ctx(active='a1', order=('a2',))).tried, ['a2'])

    def test_a_new_process_keeps_the_retry_after_even_when_the_clock_went_back(self):
        self.net.set('tok-a1', (429, {'retry-after': '900'}, {}))
        self.p.poll(self.ctx(order=('a1',)))
        later = FakeClock(wall=self.clock.wall() - 3600, mono=1.0)          # a new boot, the clock an hour back
        p2 = usage.Poller(clock=later, net=self.net, token_for=self.tokens, rand=lambda: 0.5)
        p2.poll(self.ctx(order=('a1',)))
        self.assertEqual(self.net.count('tok-a1'), 1)
        self.assertLessEqual(p2.plans['a1'].until - later.mono(), usage.RETRY_CAP_429)
        later.advance(usage.RETRY_CAP_429 + 1)
        self.net.set('tok-a1', self.body())
        p2.poll(self.ctx(order=('a1',)))
        self.assertEqual(self.net.count('tok-a1'), 2)

    def test_the_budget_survives_a_restart(self):
        self.net.set('tok-a2', self.body())
        for _ in range(20):
            self.p.poll(self.ctx(order=('a2',)), only=['a2'])
            self.clock.advance(60)
        p2 = usage.Poller(clock=self.clock, net=self.net, token_for=self.tokens, rand=lambda: 0.5)
        for _ in range(20):
            p2.poll(self.ctx(order=('a2',)), only=['a2'])
            self.clock.advance(30)
        self.assertEqual(self.net.count('tok-a2'), usage.BUDGET)

    def test_an_offline_pass_is_told_apart_from_anthropic_refusing(self):
        for a in ('a1', 'a2', 'a3'):
            self.net.set('tok-' + a, 'network')
        self.assertTrue(self.p.poll(self.ctx()).offline)
        self.net.set('tok-b1', (503, {}, {}))
        self.assertFalse(self.p.poll(self.ctx(active='b1', order=('b1',))).offline)


class TheStore(Home):
    def test_usage_cache_json_is_never_read_and_a_strange_store_reads_empty(self):
        with open(core.data_path('usage-cache.json'), 'w') as f:
            json.dump({'v': 2, 'accounts': {'a1': {'usage': {'five_hour': 99}}}}, f)
        self.assertEqual(usage.readings(), {})
        for text in ('{"v": 2, "accounts": {"a1": {}}}', 'not json', '[]', '{"v": 1, "accounts": []}'):
            with open(core.data_path('usage.json'), 'w') as f:
                f.write(text)
            self.assertEqual(usage.readings(), {}, text)

    def test_seven_days_of_samples_for_five_accounts_stay_small(self):
        clock = FakeClock()
        for i in range(7 * 24 * 4 + 10):
            now = T0 + i * 900
            for a in range(5):
                # each window's reset is the next boundary after now: it stays put until it has passed
                five = T0 + a * 600 + 18000 * ((now - T0 - a * 600) // 18000 + 1)
                week = T0 + a * 600 + 7 * 86400 * ((now - T0 - a * 600) // (7 * 86400) + 1)
                self.assertTrue(usage.feed_status_line('acct-%d' % a, {
                    'five_hour': {'used_percentage': i % 20 * 4 + a, 'resets_at': five},
                    'seven_day': {'used_percentage': (i % 672) / 7.0 + a, 'resets_at': week}}, now, now=now), (i, a))
        store = usage.load_store()
        self.assertTrue(all(len(rows) <= 7 * 24 * 4 + 1 for rows in store['samples'].values()))
        self.assertLess(os.path.getsize(core.data_path('usage.json')), 200 * 1024)
        rows = usage.samples('acct-0')
        self.assertGreaterEqual(rows[0][0], T0 + (7 * 24 * 4 + 9) * 900 - 7 * 86400 - 900)
        del clock

    def test_written_owner_only_and_atomically(self):
        usage.feed_status_line('a1', {'seven_day': {'used_percentage': 5, 'resets_at': T0 + 99}}, T0, now=T0)
        st = os.stat(core.data_path('usage.json'))
        self.assertEqual(st.st_mode & 0o777, 0o600)
        self.assertEqual([n for n in os.listdir(core.data_dir()) if n.endswith('.tmp')], [])


class TheStatusLine(Home):
    RL = {'five_hour': {'used_percentage': 42.0, 'resets_at': T0 + 3600},
          'seven_day': {'used_percentage': 61.0, 'resets_at': T0 + 3 * 86400},
          'spend_limit': {'used_percentage': 10}}

    def test_it_feeds_the_active_account_with_its_own_time_and_source(self):
        self.assertTrue(usage.feed_status_line('a1', self.RL, T0 - 20, now=T0))
        r = usage.readings(now=T0)['a1']
        self.assertEqual(r.source, 'status_line')
        self.assertEqual([(w.name, w.pct, w.source, w.read_at) for w in r.windows],
                         [(FIVE, 42.0, 'status_line', T0 - 20), (WEEK, 61.0, 'status_line', T0 - 20)])

    def test_refused_when_old_dated_ahead_or_already_refilled(self):
        self.assertFalse(usage.feed_status_line('a1', self.RL, T0 - 601, now=T0))
        self.assertFalse(usage.feed_status_line('a1', self.RL, T0 + 30, now=T0))
        self.assertFalse(usage.feed_status_line('a1', {'five_hour': {'used_percentage': 9, 'resets_at': T0 - 1}},
                                                T0, now=T0))
        self.assertFalse(usage.feed_status_line('a1', {'five_hour': {'used_percentage': 'x'}}, T0, now=T0))
        self.assertFalse(usage.feed_status_line('', self.RL, T0, now=T0))
        self.assertEqual(usage.readings(now=T0), {})

    def test_numbers_carrying_another_accounts_open_window_are_not_taken(self):
        usage.feed_status_line('a2', self.RL, T0 - 100, now=T0 - 100)       # a2 was on these windows
        self.assertFalse(usage.feed_status_line('a1', self.RL, T0, now=T0))
        self.assertNotIn('a1', usage.readings(now=T0))
        own = {'five_hour': {'used_percentage': 50.0, 'resets_at': T0 + 7000}}
        self.assertTrue(usage.feed_status_line('a1', own, T0, now=T0))
        self.assertTrue(usage.feed_status_line('a1', {'five_hour': {'used_percentage': 52.0,
                                                                    'resets_at': T0 + 7010}}, T0 + 60, now=T0 + 60))
        self.assertFalse(usage.feed_status_line('a1', {'five_hour': {'used_percentage': 60.0,
                                                                     'resets_at': T0 + 3600}}, T0 + 90, now=T0 + 90))

    def test_live_numbers_beside_an_older_model_window_show_each_its_own_age(self):
        clock = FakeClock(wall=T0 - 400)
        net, tokens = FakeNet(clock), FakeTokens()
        net.set('tok-a1', ok(usage_body(five=40.0, week=60.0, models={'Fable': (70.0, T0 + 86400)},
                                        five_reset=T0 + 3600, week_reset=T0 + 3 * 86400)))
        usage.Poller(clock=clock, net=net, token_for=tokens).poll(
            usage.Context(active='a1', order=('a1',), lines=LINES_FABLE))
        usage.feed_status_line('a1', self.RL, T0 - 10, now=T0)
        r = usage.readings(now=T0)['a1']
        ages = dict((w.name, (w.source, w.read_at)) for w in r.windows)
        self.assertEqual(ages[FIVE], ('status_line', T0 - 10))
        self.assertEqual(ages[FABLE], ('endpoint', T0 - 400))
        self.assertEqual(r.read_at, T0 - 400)                  # the reading's age is its oldest number's
        self.assertEqual(r.source, 'status_line')

    def test_collecting_saved_records_takes_only_the_active_accounts_own(self):
        folder = core.data_path('live')
        os.makedirs(folder)

        def rec(name, at, account=None, own=False, five=30.0):
            d = {'v': 1, 'session_id': name, 'at': at, 'account': account,
                 'rate_limits': {'five_hour': {'used_percentage': five, 'resets_at': T0 + 3600}}}
            if own:
                d['own_profile'] = True
            with open(os.path.join(folder, name + '.json'), 'w') as f:
                json.dump(d, f)
        rec('old', T0 - 200, five=10.0)                          # before the switch: the old account's
        rec('mine', T0 - 50, 'a1@acct.example', five=33.0)
        rec('other', T0 - 20, 'a2@acct.example', five=77.0)      # shown under another name
        rec('profile', T0 - 10, own=True, five=88.0)             # a Claude Code on a profile of its own
        self.assertTrue(usage.collect_status_lines('a1', T0 - 120, 'A1@acct.example', now=T0))
        self.assertEqual(usage.readings(now=T0)['a1'].windows[0].pct, 33.0)
        self.assertFalse(usage.collect_status_lines('a1', T0 - 40, 'a1@acct.example', now=T0))


if __name__ == '__main__':
    unittest.main()
