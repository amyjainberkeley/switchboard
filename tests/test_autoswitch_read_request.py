import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# A read someone asked for, from the page's process to the loop's: the page writes <data>/read-request.json
# ({"at", "all"}), the loop's sleep ends within two seconds of it, and the next pass reads the account in use (and with
# "all" every other account) as a read someone asked for, once, then says in its heartbeat which request it answered
# and how many accounts it read and held back. A request written while no loop ran, an old one, and one that comes
# too soon after the last are never a read of their own; a page that asks every two seconds for an hour never makes
# the loop spin or an account be read more than the pace allows. The clock, the network, the token source and the
# account engine are all fakes; the requests are written the way the page's process writes them.
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import core
from switchboard.accounts import autoloop, usage
from fakes_autoswitch import FakeClock, FakeEngine, FakeNet, FakeTokens, Home, T0, account, make_loop, set_policy, \
    usage_body

H = 3600.0
REQUEST = 'read-request.json'


def ok(five=20.0, week=30.0):
    return (200, {}, usage_body(five=five, week=week, five_reset=T0 + 4 * H, week_reset=T0 + 5 * 86400))


class TimedNet(FakeNet):
    """The fake network, noting when each read was sent (monotonic) and with which token."""

    def __init__(self, clock):
        super().__init__(clock)
        self.sent = []

    def __call__(self, url, headers, timeout):
        self.sent.append((headers['Authorization'].split(' ', 1)[1], self.clock.mono()))
        return super().__call__(url, headers, timeout)

    def reads(self, token=None):
        return [t for tok, t in self.sent if token is None or tok == token]


class Asking(Home):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock()
        self.engine = FakeEngine([account('a1', 1), account('a2', 2), account('a3', 3)], 'a1', self.clock)
        self.net = TimedNet(self.clock)
        for aid in ('a1', 'a2', 'a3'):
            self.net.set('tok-' + aid, ok())
        set_policy()

    def loop(self):
        return make_loop(self.engine, self.clock, self.net, FakeTokens())

    def ask(self, all_accounts=True, at=None):
        """What the page's process writes for "Read again" (atomically: a new file each time)."""
        at = round(self.clock.wall() if at is None else at, 3)
        usage.write_atomic(core.data_path(REQUEST), {'at': at, 'all': all_accounts})
        return at

    def heartbeat(self):
        return usage.read_json(core.data_path('auto-heartbeat.json')) or {}

    def started(self):
        """A loop that has made its first pass (which reads every account) and then waited two minutes."""
        loop = self.loop()
        loop.run_pass()
        self.clock.advance(120)
        return loop


class TheSleep(Asking):
    def test_a_request_ends_the_sleep_within_two_seconds(self):
        loop = self.started()
        start, real = self.clock.mono(), self.clock.wait
        asked = []

        def wait(event, s):                       # the page asks 7 s into a minute's sleep
            got = real(event, s)
            if not asked and self.clock.mono() - start >= 7.0:
                asked.append(self.ask())
            return got
        self.clock.wait = wait
        loop.sleep(60.0)
        self.assertTrue(asked)
        self.assertLessEqual(self.clock.mono() - start, 7.0 + 2.0)

    def test_with_no_request_it_sleeps_its_whole_time_and_beats_every_30_s(self):
        loop = self.started()
        beats, real = [], loop.hb.beat
        loop.hb.beat = lambda *a, **k: (beats.append(self.clock.mono()), real(*a, **k))
        start = self.clock.mono()
        loop.sleep(600.0)
        self.assertEqual(self.clock.mono() - start, 600.0)
        self.assertEqual(len(beats), 20)                  # the request file is looked at every 2 s; the heartbeat
        self.assertTrue(all(b - a == 30.0 for a, b in zip(beats, beats[1:])))     # is written every 30 s, as before


class APass(Asking):
    def test_answers_one_request_once_reading_every_account(self):
        loop = self.started()
        before = len(self.net.reads())
        at = self.ask()
        loop.run_pass()
        self.assertEqual(sorted(tok for tok, _ in self.net.sent[before:]), ['tok-a1', 'tok-a2', 'tok-a3'])
        asked = self.heartbeat().get('asked') or {}
        self.assertEqual((asked.get('at'), asked.get('read'), asked.get('held')), (at, 3, 0))
        self.clock.advance(30)
        n = len(self.net.reads())
        loop.run_pass()                                   # the same request again: nothing more
        self.assertEqual(len(self.net.reads()), n)

    def test_all_false_reads_only_the_account_in_use(self):
        loop = self.started()
        before = len(self.net.reads())
        self.ask(all_accounts=False)
        loop.run_pass()
        self.assertEqual([tok for tok, _ in self.net.sent[before:]], ['tok-a1'])
        self.assertEqual((self.heartbeat().get('asked') or {}).get('read'), 1)

    def test_an_account_read_under_a_minute_ago_is_held_back_and_said(self):
        loop = self.loop()
        loop.run_pass()                                   # every account read just now
        self.clock.advance(20)
        before = len(self.net.reads())
        at = self.ask()
        loop.run_pass()
        self.assertEqual(len(self.net.reads()), before)
        asked = self.heartbeat().get('asked') or {}
        self.assertEqual((asked.get('at'), asked.get('read'), asked.get('held')), (at, 0, 3))

    def test_an_old_request_is_not_read_for(self):
        loop = self.started()
        before = len(self.net.reads())
        self.ask(at=self.clock.wall() - 121)
        loop.run_pass()
        self.assertEqual(len(self.net.reads()), before)
        self.assertNotIn('asked', self.heartbeat())

    def test_a_request_written_while_no_loop_ran_is_not_read_for(self):
        self.ask()
        loop = self.loop()                                # starts after it
        loop.run_pass()
        self.clock.advance(120)
        before = len(self.net.reads())
        loop.run_pass()
        self.assertEqual(len(self.net.reads()), before)
        self.assertNotIn('asked', self.heartbeat())
        start = self.clock.mono()
        loop.sleep(60.0)                                  # nor does it cut a sleep short
        self.assertEqual(self.clock.mono() - start, 60.0)


class JustWoken(Asking):
    def test_a_request_while_the_network_settles_is_read_for_on_the_first_pass_after(self):
        loop = self.started()
        loop.settle_until = self.clock.mono() + autoloop.NET_SETTLE      # the network is back after a wake: 30 s more
        before = len(self.net.reads())
        at = self.ask()
        loop.run_pass()                                   # nothing can be read yet: not said answered either
        self.assertEqual(len(self.net.reads()), before)
        self.assertNotIn('asked', self.heartbeat())
        self.clock.advance(autoloop.NET_SETTLE + 1)
        loop.run_pass()
        self.assertEqual(sorted(tok for tok, _ in self.net.sent[before:]), ['tok-a1', 'tok-a2', 'tok-a3'])
        asked = self.heartbeat().get('asked') or {}
        self.assertEqual((asked.get('at'), asked.get('read'), asked.get('held')), (at, 3, 0))

class OddFiles(Asking):
    """The request file as a loop may find it: a folder in its place, a file that is not JSON, a request dated in the
    future, or a look that fails outright. None ends a sleep, is read for or stops the loop."""

    def sleeps_and_reads_nothing(self, loop):
        start, before = self.clock.mono(), len(self.net.reads())
        loop.sleep(60.0)
        self.assertEqual(self.clock.mono() - start, 60.0)
        loop.run_pass()
        self.assertEqual(len(self.net.reads()), before)
        self.assertNotIn('asked', self.heartbeat())

    def test_a_folder_in_its_place(self):
        loop = self.started()
        os.mkdir(core.data_path(REQUEST))
        self.sleeps_and_reads_nothing(loop)

    def test_a_file_that_is_not_json(self):
        loop = self.started()
        with open(core.data_path(REQUEST), 'w') as f:
            f.write('{"at": 17')
        self.sleeps_and_reads_nothing(loop)

    def test_a_request_dated_in_the_future(self):
        loop = self.started()
        self.ask(at=self.clock.wall() + 60)
        self.sleeps_and_reads_nothing(loop)

    def test_a_look_that_fails_ends_neither_the_sleep_nor_the_loop(self):
        loop = self.started()

        def broken():
            raise RuntimeError('made up')
        loop._take_request = broken                       # (a pass's own look is under the loop's guard: run())
        self.ask()
        start = self.clock.mono()
        loop.sleep(60.0)
        self.assertEqual(self.clock.mono() - start, 60.0)
        with open(core.data_path('auto.log')) as f:
            self.assertIn('a request for a read could not be taken', f.read())

class TwoRequestsCloseTogether(Asking):
    """The loop takes only the newest request file, so a request written before it took the last one asks for no less
    than that one did, and is a request of its own (the loop takes each time once). Once the loop has answered, the
    next request asks only for what it says."""

    def written(self):
        return usage.read_json(core.data_path(REQUEST))

    def test_every_account_then_the_account_in_use_100_ms_later_reads_every_account(self):
        loop = self.started()
        first = autoloop.request_read(True, now=self.clock.wall())
        self.clock.advance(0.1)
        second = autoloop.request_read(False, now=self.clock.wall())
        self.assertEqual(self.written(), {'at': second, 'all': True})
        before = len(self.net.reads())
        loop.run_pass()
        self.assertEqual(sorted(tok for tok, _ in self.net.sent[before:]), ['tok-a1', 'tok-a2', 'tok-a3'])
        self.assertGreater(second, first)
        self.assertEqual((self.heartbeat().get('asked') or {}).get('at'), second)

    def test_two_in_one_millisecond_are_two_requests(self):
        w = self.clock.wall()
        first = autoloop.request_read(False, now=w)
        second = autoloop.request_read(False, now=w)
        self.assertEqual(round(second - first, 3), 0.001)

    def test_once_answered_the_next_asks_only_for_what_it_says(self):
        loop = self.started()
        autoloop.request_read(True, now=self.clock.wall())
        loop.run_pass()
        self.clock.advance(61)
        autoloop.request_read(False, now=self.clock.wall())
        self.assertFalse(self.written()['all'])
        before = len(self.net.reads())
        loop.run_pass()
        self.assertEqual([tok for tok, _ in self.net.sent[before:]], ['tok-a1'])

class TooSoon(Asking):
    def test_a_second_request_within_20_s_is_answered_with_no_pass_of_its_own(self):
        loop = self.started()
        self.ask()
        loop.run_pass()
        self.clock.advance(5)
        n, start, real = len(self.net.reads()), self.clock.mono(), self.clock.wait
        again = []

        def wait(event, s):
            got = real(event, s)
            if not again and self.clock.mono() - start >= 4.0:
                again.append(self.ask())
            return got
        self.clock.wait = wait
        loop.sleep(60.0)
        self.assertEqual(self.clock.mono() - start, 60.0)         # the sleep went on
        self.assertEqual(len(self.net.reads()), n)
        asked = self.heartbeat().get('asked') or {}
        self.assertEqual((asked.get('at'), asked.get('read'), asked.get('held')), (again[0], 0, 0))

    def test_every_account_asked_for_after_the_account_in_use_alone_still_goes(self):
        loop = self.started()
        self.ask(all_accounts=False)
        loop.run_pass()
        self.clock.advance(5)
        start, real = self.clock.mono(), self.clock.wait
        asked = []

        def wait(event, s):
            got = real(event, s)
            if not asked and self.clock.mono() - start >= 4.0:
                asked.append(self.ask(all_accounts=True))
            return got
        self.clock.wait = wait
        loop.sleep(60.0)
        self.assertLessEqual(self.clock.mono() - start, 4.0 + 2.0)
        before = len(self.net.reads())
        loop.run_pass()
        self.assertEqual(sorted(tok for tok, _ in self.net.sent[before:]), ['tok-a2', 'tok-a3'])


class APageThatAsksAllTheTime(Asking):
    def test_an_hour_of_a_request_every_2_s_never_spins_the_loop_or_reads_past_the_pace(self):
        self.an_hour_of_requests(lambda all_accounts: self.ask(all_accounts=all_accounts))

    def test_the_same_written_as_the_pages_process_writes_them(self):
        self.an_hour_of_requests(lambda all_accounts: autoloop.request_read(all_accounts, now=self.clock.wall()))

    def an_hour_of_requests(self, ask):
        loop = self.started()
        start = self.clock.mono()
        real = self.clock.wait
        last = [start]

        def wait(event, s):
            got = real(event, s)
            if self.clock.mono() - last[0] >= 2.0:
                last[0] = self.clock.mono()
                ask(bool(int(self.clock.mono()) % 4))                       # all and not, mixed
            return got
        self.clock.wait = wait
        passes = 0
        while self.clock.mono() - start < H:
            passes += 1
            loop.sleep(loop.run_pass().delay)
        self.assertLessEqual(passes, 2 * H / 20.0 + 1)          # at most two passes in any 20 s, never a spin
        for tok in ('tok-a1', 'tok-a2', 'tok-a3'):
            times = [t for t in self.net.reads(tok) if t >= start]
            most = max(sum(1 for u in times if t <= u < t + H) for t in times)
            self.assertLessEqual(most, 23, tok)
            self.assertLessEqual(max(b - a for a, b in zip(times, times[1:])), 200.0, tok)


if __name__ == '__main__':
    unittest.main()
