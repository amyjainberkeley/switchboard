import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# One pace rule for every read of an account (usage.Poller): near a line the reads never stop, no hour holds more
# than 23 of them on the schedule and on request together, a busy Mac reads as often, a read someone asked for waits
# for a saved read, a read before a switch does not, and the account in use with a lapsed login is tried again in a
# minute. The clock, the network, the token source and the account engine are all fakes.
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import core
from switchboard.accounts import api, autoloop, usage
from fakes_autoswitch import FakeClock, FakeEngine, FakeNet, FakeTokens, Home, T0, account, make_loop, set_policy, \
    usage_body

FIVE, WEEK = '5-hour', 'weekly'
LINES = {FIVE: 90.0, WEEK: 95.0}
H = 3600.0
MOST_AN_HOUR = 23
LONGEST_GAP = 200.0


def ok(five, week=20.0):
    return (200, {}, usage_body(five=five, week=week, five_reset=T0 + 4.5 * H, week_reset=T0 + 6 * 86400))


class TimedNet(FakeNet):
    """The fake network, noting when each read was sent (monotonic) and with which token."""

    def __init__(self, clock):
        super().__init__(clock)
        self.sent = []

    def __call__(self, url, headers, timeout):
        self.sent.append((headers['Authorization'].split(' ', 1)[1], self.clock.mono()))
        return super().__call__(url, headers, timeout)

    def times(self, token):
        return [t for tok, t in self.sent if tok == token]


def gaps(times):
    return [b - a for a, b in zip(times, times[1:])]


def longest_gap(times):
    return max(gaps(times) or [0.0])


def most_in_an_hour(times):
    """The most sends any 60 minutes hold (each window starts at a send)."""
    return max([sum(1 for u in times if t <= u < t + H) for t in times] or [0])


class Pace(Home):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock()
        self.net = TimedNet(self.clock)
        self.tokens = FakeTokens()
        self.p = usage.Poller(clock=self.clock, net=self.net, token_for=self.tokens, rand=lambda: 0.5)

    def ctx(self, active='a1', order=('a1',), **kw):
        return usage.Context(active=active, order=order, lines=LINES, **kw)

    def ask(self, ctx, *ids):
        """A read someone asked for (the page's Read again)."""
        return self.p.poll(ctx, only=list(ids), fresh_within=usage.ASK_FRESH, asked=True)

    def before_a_switch(self, ctx, *ids):
        return self.p.poll(ctx, only=list(ids), fresh_within=autoloop.VERIFY_FRESH)

    def drive(self, ctx, watch, seconds, also=None):
        """The loop's part: a pass whenever next_due says one of `watch` is due, and at least once a minute. `also`,
        when given, runs after each pass (a page asking, say)."""
        end = self.clock.mono() + seconds
        while self.clock.mono() < end:
            rep = self.p.poll(ctx)
            self.assertEqual(rep.failed, {})
            if also:
                also()
            now = self.clock.mono()
            ahead = [d - now for d in (self.p.next_due(a) for a in watch) if d is not None and d > now]
            self.clock.advance(min([60.0] + ahead))

    def assert_never_stops(self, times, who):
        gap, most = longest_gap(times), most_in_an_hour(times)
        said = '%s: %d reads, the longest gap %d s, the most in one hour %d' % (who, len(times), gap, most)
        self.assertLessEqual(gap, LONGEST_GAP, said)
        self.assertLessEqual(most, MOST_AN_HOUR, said)
        return said


class NearALine(Pace):
    def test_the_account_in_use_near_its_line_for_three_hours_is_never_left_unread(self):
        self.net.set('tok-a1', ok(80.0))                                 # 80% against a 90 line: near
        self.drive(self.ctx(), ('a1',), 3 * H)
        times = self.net.times('tok-a1')
        self.assert_never_stops(times, 'the account in use')
        self.assertEqual([round(g) for g in gaps(times)[:7]], [90] * 6 + [180])   # the saved reads, then the pace

    def test_an_account_a_switch_could_go_to_is_never_left_unread_either(self):
        self.net.set('tok-a1', ok(80.0))
        self.net.set('tok-a2', ok(10.0))
        self.drive(self.ctx(order=('a1', 'a2')), ('a1', 'a2'), 3 * H)
        self.assert_never_stops(self.net.times('tok-a1'), 'the account in use')
        self.assert_never_stops(self.net.times('tok-a2'), 'the account a switch could go to')

    def test_with_jitter_and_a_long_stretch_the_same_holds(self):
        self.p.rand = random.Random(26).random
        self.net.set('tok-a1', ok(85.0))
        self.net.set('tok-a2', ok(30.0))
        self.net.set('tok-a3', ok(40.0))
        self.drive(self.ctx(order=('a1', 'a2', 'a3')), ('a1', 'a2', 'a3'), 4 * H)
        for aid in ('a1', 'a2', 'a3'):
            self.assert_never_stops(self.net.times('tok-' + aid), aid)


class APass(Pace):
    def test_a_pass_takes_a_read_due_in_the_next_15_s_and_sends_it_at_its_moment(self):
        self.net.set('tok-a1', ok(20.0))
        self.p.poll(self.ctx())
        due = self.p.next_due('a1')                                       # 5 minutes on
        self.clock.advance(due - self.clock.mono() - usage.PACE_SOON - 1)
        self.assertEqual(self.p.poll(self.ctx()).tried, [])               # 16 s ahead: the next pass
        self.clock.advance(2)
        self.assertEqual(self.p.poll(self.ctx()).tried, ['a1'])           # 14 s ahead: this one,
        self.assertAlmostEqual(self.net.times('tok-a1')[-1], due, delta=0.001)   # sent at its moment

    def test_never_before_its_saved_read_is_back(self):
        self.net.set('tok-a1', ok(80.0))
        ctx = self.ctx()
        while self.ask(ctx, 'a1').tried:
            self.clock.advance(usage.ASK_FRESH + 1)
        plan = self.p.plans['a1']
        back = self.p._saved_back(plan, self.clock.mono())
        self.clock.advance(back - self.clock.mono() - usage.PACE_SOON + 1)
        self.assertEqual(self.p.poll(ctx).tried, ['a1'])
        self.assertGreaterEqual(self.net.times('tok-a1')[-1], back - 0.001)


class TheLoopsOwnPasses(Pace):
    """The loop wakes for the account in use's next read only, rests at least MIN_SLEEP between passes, and its passes
    come 54 to 66 s apart: near a line its own passes keep every account read as the driver above does."""

    def test_near_a_line_for_three_hours_no_account_is_left_unread(self):
        for seed in (1, 3):
            with self.subTest(seed=seed):
                self.net.sent = []
                clock = self.clock
                engine = FakeEngine([account('n%d' % seed, 1), account('t%d' % seed, 2)], 'n%d' % seed, clock)
                set_policy()
                # the hard lines alone: at 80% the 5-hour sign line (60) would move it to t as a sign switch
                core.update_setting(autoloop.POLICY_KEY, lambda old: dict(old or {}, sign_lines=False))
                self.net.set('tok-n%d' % seed, ok(80.0))
                self.net.set('tok-t%d' % seed, ok(10.0))
                loop = make_loop(engine, clock, self.net, self.tokens)
                loop.rand = loop.poller.rand = random.Random(seed).random
                end = clock.mono() + 3 * H
                while clock.mono() < end:
                    clock.advance(loop.run_pass().delay)
                self.assertEqual(engine.switches, [])
                self.assert_never_stops(self.net.times('tok-n%d' % seed), 'the account in use')
                self.assert_never_stops(self.net.times('tok-t%d' % seed), 'the account a switch could go to')


class ALatePass(Pace):
    """A pass's later stages (the transcript scan, upkeep) can run on past what its usage stage looked ahead to. The
    account in use's read that then falls due less than MIN_SLEEP after the pass ends, or before it ends, wakes the
    loop MIN_SLEEP after the pass at the latest, not a whole pass (54 to 66 s) later."""

    def an_hour_with_a_slow_scan(self, lands):
        """The account in use near its line for an hour of the loop's own passes. Whenever its next read is 15 to 60 s
        away once the usage stage is done (a read that stage did not take), the transcript scan runs until `lands`
        seconds from it (-10: the pass ends 10 s before it; +2: 2 s after). Returns the sends, those reads' moments, and
        the moment the hour's last sleep ended."""
        clock = self.clock
        engine = FakeEngine([account('a1', 1)], 'a1', clock)
        set_policy()
        self.net.set('tok-a1', ok(80.0))
        loop = make_loop(engine, clock, self.net, self.tokens)
        late = []

        def look(*a, **kw):
            due = loop.poller.next_due('a1')
            if due is not None and usage.PACE_SOON < due - clock.mono() <= autoloop.PASS_EVERY:
                late.append(due)
                clock.advance(due - clock.mono() + lands)
            return {}
        loop.scanner.look = look
        end = clock.mono() + H
        while clock.mono() < end:
            clock.advance(loop.run_pass().delay)
        self.assertEqual(engine.switches, [])
        self.assertGreaterEqual(len(late), 10)
        return self.net.times('tok-a1'), late, clock.mono()

    def assert_min_sleep_after_the_pass(self, times, late, lands, stop):
        for due in late:
            sent = min([t for t in times if t >= due - 0.001] or [stop])     # (the hour may end before that pass)
            self.assertLessEqual(sent - (due + lands), autoloop.MIN_SLEEP + 0.001,
                                 'due %+.0f s from the pass end, sent %.0f s after it' % (-lands, sent - due - lands))

    def test_a_read_due_just_after_the_pass_ends_goes_min_sleep_after_it(self):
        times, late, stop = self.an_hour_with_a_slow_scan(-10.0)
        self.assert_min_sleep_after_the_pass(times, late, -10.0, stop)
        self.assert_never_stops(times, 'the account in use, each pass ending 10 s before its read')

    def test_a_read_that_fell_due_during_the_pass_goes_min_sleep_after_it(self):
        times, late, stop = self.an_hour_with_a_slow_scan(2.0)
        self.assert_min_sleep_after_the_pass(times, late, 2.0, stop)
        self.assert_never_stops(times, 'the account in use, each pass ending 2 s after its read was due')

    def test_a_read_the_usage_stage_left_never_wakes_the_loop_every_min_sleep(self):
        clock = self.clock
        engine = FakeEngine([account('a1', 1)], 'a1', clock)
        set_policy()
        self.net.set('tok-a1', ok(80.0))
        loop = make_loop(engine, clock, self.net, self.tokens)
        delays = []
        end = clock.mono() + H
        while clock.mono() < end:
            if delays:                                    # read from Anthropic once, then Claude Code's own numbers
                usage.feed_status_line('a1', {'five_hour': {'used_percentage': 80.0, 'resets_at': T0 + 4.5 * H}},
                                       clock.wall(), now=clock.wall())
            delays.append(loop.run_pass().delay)
            clock.advance(delays[-1])
        self.assertEqual(len(self.net.times('tok-a1')), 1)      # the planned read, long past, is left each pass
        self.assertGreaterEqual(min(delays[2:]), autoloop.PASS_EVERY * 0.9, delays[:6])


class FarFromTheLines(Pace):
    def test_the_account_in_use_every_5_minutes_give_or_take_the_jitter(self):
        self.p.rand = random.Random(5).random
        self.net.set('tok-a1', ok(20.0))
        self.drive(self.ctx(), ('a1',), 2 * H)
        g = gaps(self.net.times('tok-a1'))
        self.assertGreaterEqual(len(g), 20)
        for x in g:
            self.assertTrue(usage.ACTIVE_EVERY * (1 - usage.JITTER) <= x <= usage.ACTIVE_EVERY * (1 + usage.JITTER),
                            x)

    def loop_reads(self, load):
        """The account in use's reads in an hour of the loop's own passes, on a Mac at `load` (per core)."""
        engine = FakeEngine([account('a1', 1)], 'a1', self.clock)
        set_policy()
        self.net.set('tok-a1', ok(20.0))
        loop = make_loop(engine, self.clock, self.net, self.tokens, load=lambda: load)
        end = self.clock.mono() + H
        while self.clock.mono() < end:
            self.clock.advance(loop.run_pass().delay)
        self.assertEqual(engine.switches, [])
        return gaps(self.net.times('tok-a1'))

    def test_a_busy_mac_reads_as_often(self):
        busy = self.loop_reads(213 / 8.0)                                 # far above LOAD_HIGH
        self.assertGreaterEqual(len(busy), 10)
        for x in busy:
            self.assertAlmostEqual(x, usage.ACTIVE_EVERY, delta=1, msg='under load: %s' % busy)

    def test_a_quiet_mac_reads_the_same(self):
        for x in self.loop_reads(0.5):
            self.assertAlmostEqual(x, usage.ACTIVE_EVERY, delta=1)


class AskedFor(Pace):
    def test_sent_at_once_when_a_saved_read_is_there(self):
        self.net.set('tok-a1', ok(20.0))
        self.net.set('tok-a2', ok(10.0))
        ctx = self.ctx(order=('a1', 'a2'))
        self.p.poll(ctx)                                                   # both read on the schedule
        self.clock.advance(usage.ASK_FRESH + 1)
        rep = self.ask(ctx, 'a1', 'a2')
        self.assertEqual(rep.tried, ['a1', 'a2'])
        self.assertEqual(rep.held, [])
        self.assertEqual(len(self.net.sent), 4)

    def test_not_sent_for_an_account_read_under_a_minute_ago(self):
        self.net.set('tok-a1', ok(20.0))
        self.p.poll(self.ctx())
        self.clock.advance(usage.ASK_FRESH - 2)
        rep = self.ask(self.ctx(), 'a1', 'a1')
        self.assertEqual((rep.tried, rep.held), ([], ['a1']))
        self.assertEqual(len(self.net.sent), 1)
        self.clock.advance(3)
        self.assertEqual(self.ask(self.ctx(), 'a1', 'a1').tried, ['a1'])   # named twice: read once

    def test_asked_every_30_s_for_an_hour_as_well_as_the_schedule(self):
        for five in (80.0, 20.0):                                          # near a line, and far
            with self.subTest(five=five):
                self.net.sent = []
                aid = 'n1' if five > 50 else 'f1'
                self.net.set('tok-' + aid, ok(five))
                ctx = self.ctx(active=aid, order=(aid,))
                start = self.clock.mono()
                while self.clock.mono() < start + H:
                    self.p.poll(ctx)
                    self.ask(ctx, aid)
                    self.clock.advance(30)
                self.assert_never_stops(self.net.times('tok-' + aid), 'asked every 30 s')
                self.assertGreaterEqual(len(self.net.sent), 20)

    def test_not_sent_during_a_wait(self):
        self.net.set('tok-a1', (429, {'retry-after': '600'}, {}))
        self.net.set('tok-a2', 'network')
        ctx = self.ctx(order=('a1', 'a2'))
        self.p.poll(ctx)
        self.clock.advance(30)                                             # a2 waits 60 s, a1 600 s
        rep = self.ask(ctx, 'a1', 'a2')
        self.assertEqual((rep.tried, sorted(rep.held)), ([], ['a1', 'a2']))
        self.clock.advance(300)
        self.assertEqual(self.ask(ctx, 'a1').tried, [])
        self.assertEqual(len(self.net.sent), 2)

    def test_not_sent_in_the_hour_after_a_429s_wait(self):
        self.net.set('tok-a1', (429, {'retry-after': '600'}, {}), ok(20.0))
        ctx = self.ctx()
        self.p.poll(ctx)
        lift = self.p.plans['a1'].lift
        self.assertAlmostEqual(lift - self.clock.mono(), 600, delta=1)
        self.clock.advance(601)
        self.assertEqual(self.ask(ctx, 'a1').tried, [])                    # the wait is over, the hour is not
        self.assertEqual(self.p.poll(ctx).tried, ['a1'])                   # the schedule (slowed) still reads
        self.clock.advance(usage.AIMD_WINDOW / 2)
        self.assertEqual(self.ask(ctx, 'a1').tried, [])
        self.clock.advance(lift + usage.AIMD_WINDOW + 1 - self.clock.mono())
        self.assertEqual(self.ask(ctx, 'a1').tried, ['a1'])

    def test_not_sent_with_no_saved_read(self):
        self.net.set('tok-a1', ok(20.0))
        ctx = self.ctx()
        for _ in range(5):                                                 # 4, 3.3, 2.7, 2.0, 1.4 saved: all go
            self.assertEqual(self.ask(ctx, 'a1').tried, ['a1'])
            self.clock.advance(usage.ASK_FRESH + 1)
        self.assertEqual(self.ask(ctx, 'a1').held, ['a1'])                 # 0.7 saved: held
        back = self.p.next_due('a1')
        self.assertGreater(back, self.clock.mono())
        self.clock.advance(back - self.clock.mono())
        self.assertEqual(self.ask(ctx, 'a1').tried, ['a1'])


class BeforeASwitch(Pace):
    def test_sent_with_no_saved_read_left_inside_the_hard_budget(self):
        self.net.set('tok-a2', ok(10.0))
        ctx = self.ctx(order=('a1', 'a2'))
        while self.ask(ctx, 'a2').tried:
            self.clock.advance(usage.ASK_FRESH + 1)
        plan = self.p.plans['a2']
        self.assertLess(self.p._saved(plan, self.clock.mono()), 1)
        self.assertEqual(self.before_a_switch(ctx, 'a2').tried, ['a2'])     # no saved read: it goes all the same
        self.assertEqual(self.p._saved(plan, self.clock.mono()), 0.0)      # and the reads after it wait for theirs
        self.assertAlmostEqual(self.p._saved_back(plan, self.clock.mono()) - self.clock.mono(), usage.PACE_EVERY,
                               delta=1)
        start = self.clock.mono()
        while self.clock.mono() < start + H:
            self.before_a_switch(ctx, 'a2')
            self.clock.advance(autoloop.VERIFY_FRESH + 1)
        times = self.net.times('tok-a2')
        self.assertEqual(most_in_an_hour(times), usage.BUDGET)              # the hard budget is the last guard

    def test_the_read_after_a_wake_needs_no_saved_read(self):
        self.net.set('tok-a1', ok(20.0))
        ctx = self.ctx()
        while self.ask(ctx, 'a1').tried:
            self.clock.advance(usage.ASK_FRESH + 1)
        self.assertLess(self.p._saved(self.p.plans['a1'], self.clock.mono()), 1)
        self.assertEqual(self.p.poll(ctx).tried, [])
        self.p.wake()
        self.assertIsNone(self.p.next_due('a1'))                           # due now
        self.assertEqual(self.p.poll(ctx).tried, ['a1'])
        self.assertEqual(self.p.poll(ctx).tried, [])                       # once


class ARefusedToken(Pace):
    """A 401 for another account renews its token and may send a second request inside the same read."""

    def refused_with_one_saved_read(self, ctx):
        self.net.set('tok-a2', ok(10.0))
        for _ in range(4):
            self.assertEqual(self.ask(ctx, 'a2').tried, ['a2'])
            self.clock.advance(usage.ASK_FRESH + 1)
        self.assertTrue(1 <= self.p._saved(self.p.plans['a2'], self.clock.mono()) < 2)
        self.net.set('tok-a2', (401, {}, {}))
        self.net.set('new-a2', ok(10.0))
        self.tokens.fresh_value['a2'] = 'new-a2'

    def test_the_renewed_token_waits_for_a_saved_read_when_the_read_needed_one(self):
        ctx = self.ctx(order=('a1', 'a2'))
        self.refused_with_one_saved_read(ctx)
        rep = self.ask(ctx, 'a2')
        self.assertEqual(rep.failed, {'a2': 'http-401'})
        self.assertIn(('a2', True), self.tokens.calls)                     # renewed,
        self.assertEqual(self.net.times('new-a2'), [])                     # not sent: the first took the saved read
        self.tokens.answers['a2'] = api.Read(api.PRESENT, value='new-a2', provenance='keychain')
        plan = self.p.plans['a2']
        self.clock.advance(self.p._saved_back(plan, self.clock.mono()) - self.clock.mono())
        self.assertEqual(self.ask(ctx, 'a2').ok, ['a2'])                   # the next read takes it
        self.assertEqual(len(self.net.times('new-a2')), 1)

    def test_before_a_switch_the_renewed_token_goes_at_once(self):
        ctx = self.ctx(order=('a1', 'a2'))
        self.refused_with_one_saved_read(ctx)
        self.assertEqual(self.before_a_switch(ctx, 'a2').ok, ['a2'])
        self.assertEqual(len(self.net.times('new-a2')), 1)


class ARestart(Pace):
    def test_the_saved_reads_survive_a_restart(self):
        self.net.set('tok-a1', ok(20.0))
        ctx = self.ctx()
        while self.ask(ctx, 'a1').tried:
            self.clock.advance(usage.ASK_FRESH + 1)
        sent = len(self.net.sent)
        saved = self.p._saved(self.p.plans['a1'], self.clock.mono())
        self.assertLess(saved, 1)
        self.assertEqual(len(usage.load_store()['accounts']['a1']['ep']['reads']), sent)
        p2 = usage.Poller(clock=self.clock, net=self.net, token_for=self.tokens, rand=lambda: 0.5)
        self.p = p2
        self.assertEqual(self.ask(ctx, 'a1').held, ['a1'])                 # still none saved
        self.assertEqual(p2.poll(ctx).tried, [])                           # on the schedule either
        self.assertAlmostEqual(p2._saved(p2.plans['a1'], self.clock.mono()), saved, delta=0.001)
        self.clock.advance(p2.next_due('a1') - self.clock.mono())
        self.assertEqual(self.ask(ctx, 'a1').tried, ['a1'])
        self.assertEqual(len(self.net.sent), sent + 1)


class ALapsedLogin(Pace):
    def tries(self, aid):
        return sum(1 for a, fresh in self.tokens.calls if a == aid and not fresh)

    def test_the_account_in_use_is_tried_again_after_60_s_another_after_300(self):
        expired = api.Read(api.UNREADABLE, why='expired')
        self.tokens.answers['a1'] = expired
        self.tokens.answers['a2'] = expired
        ctx = self.ctx(order=('a1', 'a2'))
        rep = self.p.poll(ctx)
        self.assertEqual(rep.failed, {'a1': 'token-expired', 'a2': 'token-expired'})
        self.assertEqual(self.net.sent, [])
        self.assertAlmostEqual(self.p.next_due('a1') - self.clock.mono(), usage.NO_TOKEN_WAIT_ACTIVE, delta=1)
        self.assertAlmostEqual(self.p.next_due('a2') - self.clock.mono(), usage.NO_TOKEN_WAIT, delta=1)
        start = self.clock.mono()
        self.clock.advance(59)
        self.p.poll(ctx)
        self.assertEqual((self.tries('a1'), self.tries('a2')), (1, 1))
        self.clock.advance(start + 61 - self.clock.mono())
        self.p.poll(ctx)
        self.assertEqual((self.tries('a1'), self.tries('a2')), (2, 1))
        self.clock.advance(start + 299 - self.clock.mono())
        self.p.poll(ctx)
        self.assertEqual(self.tries('a2'), 1)
        self.clock.advance(start + 302 - self.clock.mono())
        self.p.poll(ctx)
        self.assertEqual(self.tries('a2'), 2)
        self.assertEqual(self.net.sent, [])

    def test_a_missing_token_for_the_account_in_use_waits_a_minute_too(self):
        self.tokens.answers['a1'] = api.Read(api.UNREADABLE, why='no_saved_login')
        rep = self.p.poll(self.ctx())
        self.assertEqual(rep.failed, {'a1': 'no-access-token'})
        self.assertAlmostEqual(self.p.next_due('a1') - self.clock.mono(), usage.NO_TOKEN_WAIT_ACTIVE, delta=1)
        self.tokens.answers.pop('a1')
        self.net.set('tok-a1', ok(20.0))
        self.clock.advance(usage.NO_TOKEN_WAIT_ACTIVE + 1)
        self.assertEqual(self.p.poll(self.ctx()).ok, ['a1'])


if __name__ == '__main__':
    unittest.main()
