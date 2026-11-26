import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# The automatic loop, pass by pass, with every outside thing fake: the account engine, the network
# (429, offline), the clock (wall and monotonic apart, sleeps), load, battery and ps.
import io
import json
import os
import signal
import sys
import threading
import time
import unittest
from contextlib import redirect_stderr

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import core
from switchboard.accounts import api, autoloop, policy, usage
from fakes_autoswitch import (FakeClock, FakeEngine, FakeNet, FakeTokens, Home, T0, account, make_loop,
                            set_policy, usage_body)

H, D = 3600.0, 86400.0


def ok(five, week, five_reset=None, week_reset=None):
    return (200, {}, usage_body(five=five, week=week, five_reset=five_reset or T0 + 4 * H,
                                week_reset=week_reset or T0 + 4 * D))


class LoopCase(Home):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock()
        self.accts = [account('a1', 1), account('a2', 2), account('a3', 3)]
        self.engine = FakeEngine(self.accts, 'a1', self.clock)
        self.net = FakeNet(self.clock)
        self.tokens = FakeTokens()
        set_policy()

    def loop(self, **kw):
        return make_loop(self.engine, self.clock, self.net, self.tokens, **kw)

    def answers(self, a1, a2, a3):
        self.net.set('tok-a1', a1)
        self.net.set('tok-a2', a2)
        self.net.set('tok-a3', a3)

    def heartbeat(self):
        return usage.read_json(core.data_path(autoloop.HEARTBEAT)) or {}

    def log_text(self):
        try:
            with open(core.data_path(autoloop.LOG)) as f:
                return f.read()
        except OSError:
            return ''


class ForcedAndOptional(LoopCase):
    def test_a_line_reached_switches_at_once_and_says_so(self):
        self.answers(ok(92.0, 30.0), ok(10.0, 20.0, week_reset=T0 + 2 * D), ok(10.0, 20.0, week_reset=T0 + 3 * D))
        res = self.loop().run_pass()
        self.assertIsNotNone(res.switched)
        self.assertEqual(res.code, autoloop.EXIT_SWITCHED)
        sw = self.engine.switches[0]
        self.assertEqual((sw['to'], sw['kind'], sw['source']), ('a2', 'forced', 'auto'))
        self.assertIn('5-hour at 92%', sw['reason'])
        self.assertNotIn('send your message again', sw['reason'])         # still working at 92%: nothing stopped
        st = autoloop.load_state()
        self.assertEqual([(r['kind'], r['to']) for r in st['switches']], [('forced', 'a2')])
        self.assertEqual(st['live']['id'], 'a2')
        self.assertNotIn('@', self.log_text())                             # the log names accounts by number

    def test_a_limit_hit_tells_the_session_to_send_its_message_again(self):
        self.answers(ok(100.0, 30.0), ok(10.0, 20.0), ok(10.0, 20.0))
        self.loop().run_pass()
        self.assertIn('If a reply stopped, send your message again.', self.engine.switches[0]['reason'])

    def test_an_optional_switch_goes_only_when_claude_code_is_quiet(self):
        self.answers(ok(20.0, 40.0, week_reset=T0 + 5 * D), ok(10.0, 50.0, week_reset=T0 + 2 * D),
                     ok(10.0, 30.0, week_reset=T0 + 6 * D))
        root = os.path.join(os.environ['CLAUDE_CONFIG_DIR'], 'projects', 'p')
        os.makedirs(root)
        chat = os.path.join(root, 's.jsonl')
        with open(chat, 'w') as f:
            f.write('{"type":"assistant","message":{"stop_reason":"end_turn","content":[]}}\n')
        now = time.time()
        os.utime(chat, (now, now))
        loop = self.loop()
        loop.scanner.look = lambda timeline, now=None, light=False: {'busy': True, 'idle_for_s': 0.0,
                                                                      'obligations': []}
        res = loop.run_pass()
        self.assertEqual((res.decision.action, res.decision.kind, res.decision.target), ('stay', 'optional', 'a2'))
        self.assertIn('working', res.decision.hold)
        self.assertEqual(self.engine.switches, [])
        loop.scanner.look = lambda timeline, now=None, light=False: {'busy': False, 'idle_for_s': 900.0,
                                                                      'obligations': []}
        self.clock.advance(60)
        res = loop.run_pass()
        self.assertEqual([(s['to'], s['kind']) for s in self.engine.switches], [('a2', 'optional')])

    def test_an_optional_switch_waits_when_its_target_cannot_be_read_again(self):
        self.answers(ok(20.0, 40.0, week_reset=T0 + 5 * D), ok(10.0, 50.0, week_reset=T0 + 2 * D),
                     ok(10.0, 30.0, week_reset=T0 + 6 * D))
        loop = self.loop()
        loop.scanner.look = lambda timeline, now=None, light=False: {'busy': False, 'idle_for_s': 900.0,
                                                                      'obligations': []}
        loop.d.switch = lambda *a, **k: self.fail('no switch on a failed pass')
        ctx = usage.Context(active='a1', order=('a1', 'a2', 'a3'), lines={'5-hour': 90.0, 'weekly': 95.0})
        loop.poller.poll(ctx)
        self.clock.advance(120)                                             # 2 minutes old: re-read first
        self.net.set('tok-a2', 'network')
        snap = autoloop.gather(loop.d, self.clock, activity={'busy': False, 'idle_for_s': 900.0})
        d = policy.decide(snap)
        self.assertEqual((d.action, d.kind, d.target), ('switch', 'optional', 'a2'))
        self.assertIsNone(loop._act(d, snap, ctx, (True, 90), self.clock.mono()))
        self.assertIn('could not be read again', self.log_text())
        loop.d.switch = self.engine.switch
        self.net.set('tok-a2', ok(10.0, 50.0, week_reset=T0 + 2 * D))
        self.clock.advance(61)
        snap = autoloop.gather(loop.d, self.clock, activity={'busy': False, 'idle_for_s': 900.0})
        self.assertIsNotNone(loop._act(policy.decide(snap), snap, ctx, (True, 90), self.clock.mono()))

    def test_a_switch_made_by_anyone_since_the_snapshot_drops_this_one(self):
        self.answers(ok(95.0, 30.0), ok(10.0, 20.0), ok(10.0, 20.0))
        loop = self.loop()
        ctx = usage.Context(active='a1', order=('a1', 'a2', 'a3'), lines={'5-hour': 90.0, 'weekly': 95.0})
        loop.poller.poll(ctx)
        snap = autoloop.gather(loop.d, self.clock, activity={'busy': False, 'idle_for_s': 900.0})
        taken = self.clock.mono()
        self.clock.advance(5)
        autoloop.update_state(lambda s: autoloop.record_switch(s, self.clock, 'manual', 'page', 'a1', 'a3'))
        self.assertIsNone(loop._act(policy.decide(snap), snap, ctx, (True, 90), taken))
        self.assertEqual(self.engine.switches, [])

    def test_exit_codes_of_a_single_pass(self):
        self.answers(ok(20.0, 40.0), ok(30.0, 40.0), ok(30.0, 40.0))
        set_policy(strategy='most-room')
        self.assertEqual(self.loop().run_pass().code, autoloop.EXIT_NOTHING)
        self.answers(ok(100.0, 40.0), ok(100.0, 40.0), ok(100.0, 40.0))
        loop = self.loop()
        loop.poller.wake()
        self.clock.advance(1000)
        self.assertEqual(loop.run_pass().code, autoloop.EXIT_BLOCKED)


class AccountRules(LoopCase):
    """Use last and Keep out, through a whole pass: a rule changed while a pass reads is the one it decides on."""

    def keep(self, **rules):
        core.update_setting(autoloop.POLICY_KEY, lambda old: dict(old or {}, rules=rules))

    def quiet(self, loop, then=None):
        def look(timeline, now=None, light=False):
            if then:
                then()
            return {'busy': False, 'idle_for_s': 900.0, 'obligations': []}
        loop.scanner.look = look

    def test_keep_out_pressed_while_a_pass_reads_is_the_rule_it_decides_on(self):
        self.answers(ok(92.0, 30.0), ok(10.0, 20.0, week_reset=T0 + 2 * D), ok(10.0, 20.0, week_reset=T0 + 3 * D))
        loop = self.loop()
        self.quiet(loop, then=lambda: self.keep(a2='out'))      # pressed after the pass read its settings
        loop.run_pass()
        self.assertEqual([s['to'] for s in self.engine.switches], ['a3'])

    def test_keep_out_pressed_on_the_account_in_use_while_a_pass_reads_keeps_claude_code_there(self):
        self.answers(ok(92.0, 30.0), ok(10.0, 20.0), ok(10.0, 20.0))
        loop = self.loop()
        self.quiet(loop, then=lambda: self.keep(a1='out'))
        res = loop.run_pass()
        self.assertEqual(self.engine.switches, [])
        self.assertEqual(res.decision.action, 'stay')
        self.assertIn('kept out of auto-switch', res.decision.reason)

    def test_keep_out_pressed_while_the_switch_is_checked_again_drops_the_switch(self):
        self.answers(ok(92.0, 30.0), ok(10.0, 20.0), ok(96.0, 30.0))
        loop = self.loop()
        self.quiet(loop)
        real = loop.poller.poll

        def poll(ctx, only=None, **kw):
            if only:                                            # the check on fresh numbers just before the move
                self.keep(a2='out')
            return real(ctx, only=only, **kw)
        loop.poller.poll = poll
        res = loop.run_pass()
        self.assertEqual(self.engine.switches, [])
        self.assertIn('switch dropped on fresh numbers', self.log_text())
        self.assertIsNone(res.switched)

    def not_due(self, loop, aid):
        real = loop.poller.order                # the pass's own reads leave this account out: not due yet
        loop.poller.order = lambda ctx, store: [x for x in real(ctx, store) if x[0] != aid]

    def test_before_running_an_account_past_its_line_an_unread_account_used_last_is_read(self):
        self.keep(a3='last')
        self.answers(ok(100.0, 30.0), ok(93.0, 10.0), ok(5.0, 5.0))
        loop = self.loop()
        self.not_due(loop, 'a3')
        res = loop.run_pass()
        self.assertEqual([s['to'] for s in self.engine.switches], ['a3'], self.log_text())
        self.assertIn('a3@acct.example, used last, has room', res.switched.reason)

    def test_an_account_used_last_that_still_cannot_be_read_leaves_the_move_past_a_line_as_it_was(self):
        self.keep(a3='last')
        self.answers(ok(100.0, 30.0), ok(93.0, 10.0), 'network')
        loop = self.loop()
        self.not_due(loop, 'a3')
        res = loop.run_pass()
        self.assertEqual([s['to'] for s in self.engine.switches], ['a2'])
        self.assertIn('no account is under its lines, but a2@acct.example can still work', res.switched.reason)
        self.assertEqual(self.net.count('tok-a3'), 1)        # it was tried once, before the move

    def test_the_account_in_use_kept_out_at_its_limit_is_never_moved_off(self):
        self.keep(a1='out')
        self.answers(ok(100.0, 30.0), ok(10.0, 20.0), ok(10.0, 20.0))
        res = self.loop().run_pass()
        self.assertEqual(self.engine.switches, [])
        self.assertEqual(res.decision.action, 'stay')
        self.assertIn('It is kept out of auto-switch, so Claude Code stays on it.', res.decision.reason)

    def test_the_only_account_with_room_kept_out_is_never_moved_to(self):
        self.keep(a2='out')
        self.answers(ok(100.0, 30.0), ok(10.0, 20.0), ok(100.0, 30.0))
        res = self.loop().run_pass()
        self.assertEqual(self.engine.switches, [])
        self.assertEqual(res.decision.action, 'stay')
        self.assertIn('a2@acct.example is kept out of auto-switch', res.decision.reason)


class RefusedNow(LoopCase):
    def test_a_limit_notice_under_the_account_in_use_means_it_is_refused_now(self):
        st = {'live': {'t': T0 - 600, 'id': 'a1'}}
        reading = api.Reading('a1', (api.Window('5-hour', 70.0, T0 + H, T0 - 900, 'endpoint'),), T0 - 900,
                              'endpoint', True)
        self.assertTrue(autoloop.refused_now({'limited_at': T0 - 60}, 'a1', st, {'a1': reading}, T0))
        self.assertFalse(autoloop.refused_now({'limited_at': T0 - 700}, 'a1', st, {'a1': reading}, T0))   # before
        self.assertFalse(autoloop.refused_now({'limited_at': T0 - 601}, 'a1', st, {}, T0 + 10))           # old
        fresh = api.Reading('a1', (api.Window('5-hour', 40.0, T0 + H, T0 - 10, 'status_line'),), T0 - 10,
                            'status_line')
        self.assertFalse(autoloop.refused_now({'limited_at': T0 - 60}, 'a1', st, {'a1': fresh}, T0))     # room since

    def test_frozen_at_70_percent_with_no_status_line_still_leaves_at_the_limit(self):
        self.answers((429, {'retry-after': '3600'}, {}), ok(10.0, 20.0), ok(10.0, 20.0))
        usage.feed_status_line('a1', {'five_hour': {'used_percentage': 70.0, 'resets_at': T0 + 4 * H}},
                               T0 - 3000, now=T0 - 3000 + 1)
        loop = self.loop()
        loop.scanner.look = lambda timeline, now=None, light=False: {'busy': True, 'idle_for_s': 0.0,
                                                                      'obligations': [], 'limited_at': None}
        loop.run_pass()
        self.assertEqual(self.engine.switches, [])                     # 70% (a lower bound): it stays
        loop.scanner.look = lambda timeline, now=None, light=False: {'busy': True, 'idle_for_s': 0.0,
                                                                      'obligations': [],
                                                                      'limited_at': self.clock.wall() - 20}
        self.clock.advance(60)
        loop.run_pass()
        self.assertEqual([(s['to'], s['kind']) for s in self.engine.switches], [('a2', 'forced')])
        self.assertIn('refusing requests now', self.engine.switches[0]['reason'])
        self.assertIn('send your message again', self.engine.switches[0]['reason'])


class StagesAndTheHeartbeat(LoopCase):
    def test_a_keychain_read_that_never_answers_never_silences_the_loop(self):
        self.engine.block_live = self.blocker()
        self.answers(ok(20.0, 40.0), ok(20.0, 40.0), ok(20.0, 40.0))
        loop = self.loop()
        res = loop.run_pass()
        self.assertEqual(res.decision.action, 'stay')
        self.assertIn("can't be read right now", res.decision.reason)
        limit = autoloop.STAGE_DEADLINE['live']             # (35 s: longer than a Keychain read under load)
        self.assertIn('live overran its %d s deadline; the pass carries on' % limit, self.log_text())
        hb = self.heartbeat()
        self.assertEqual(hb['pass_n'], 1)
        self.assertIsNotNone(hb['last_pass_at'])
        self.assertIn('took longer than %d s' % limit, hb['note'])
        calls = self.engine.live_calls
        loop.run_pass()                                         # the stuck read is not started a second time
        self.assertEqual(self.engine.live_calls, calls)
        self.assertIn('live skipped: the last one is still running', self.log_text())
        self.assertEqual(self.heartbeat()['pass_n'], 2)

    def test_a_switch_that_hangs_is_never_abandoned_and_shows_as_a_stale_heartbeat(self):
        self.engine.block_switch = self.blocker()
        self.answers(ok(95.0, 30.0), ok(10.0, 20.0), ok(10.0, 20.0))
        loop = self.loop()
        self.assertTrue(loop.lease.acquire(self.clock))
        self.addCleanup(loop.lease.release)
        done = threading.Event()
        threading.Thread(target=lambda: (loop.run_pass(), done.set()), daemon=True).start()
        seen_false = seen_true = False
        end = time.time() + 60
        while time.time() < end and not seen_true:
            st = autoloop.status(self.clock)
            if st['stage'] == 'switching' and st['beat_age_s'] is not None:
                self.assertEqual(st['restart'], st['beat_age_s'] > autoloop.STUCK_AFTER + autoloop.SWITCH_GRACE)
                seen_false = seen_false or (not st['restart'] and st['beat_age_s'] > autoloop.STUCK_AFTER)
                seen_true = st['restart']
                if seen_true:
                    self.assertIn('switching accounts', st['why'])
            time.sleep(0.02)
        self.assertTrue(seen_true)
        self.assertTrue(seen_false)                      # past 180 s it waited the switch's extra 60 s
        self.assertFalse(done.is_set())                  # and the switch was never abandoned
        self.engine.block_switch.set()
        self.assertTrue(done.wait(30))
        self.assertEqual([s['to'] for s in self.engine.switches], ['a2'])

    def test_a_file_lock_held_by_a_hung_process_never_hangs_anything(self):
        import fcntl
        fd = os.open(core.data_path(usage.STORE_LOCK), os.O_WRONLY | os.O_CREAT, 0o600)
        fd2 = os.open(core.data_path(autoloop.STATE_LOCK), os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            fcntl.flock(fd2, fcntl.LOCK_EX)
            started = time.monotonic()
            self.assertFalse(usage.feed_status_line('a1', {'five_hour': {'used_percentage': 5,
                                                                         'resets_at': T0 + H}}, T0, now=T0))
            self.assertIsNone(autoloop.update_state(lambda s: s.update(x=1)))
            self.assertLess(time.monotonic() - started, 3 * usage.LOCK_WAIT + 2)
        finally:
            os.close(fd)
            os.close(fd2)

    def test_the_heartbeat_beats_through_a_long_sleep(self):
        loop = self.loop()
        beats = []
        real = loop.hb.beat
        loop.hb.beat = lambda *a, **k: (beats.append(self.clock.mono()), real(*a, **k))
        loop.sleep(600)
        gaps = [b - a for a, b in zip(beats, beats[1:])]
        self.assertGreaterEqual(len(beats), 20)
        self.assertTrue(all(g <= autoloop.BEAT_EVERY for g in gaps))

    def test_the_loop_stops_when_asked_and_says_so(self):
        self.answers(ok(20.0, 40.0), ok(20.0, 40.0), ok(20.0, 40.0))
        loop = self.loop()
        t = threading.Thread(target=loop.run, daemon=True)
        t.start()
        time.sleep(0.5)
        loop.stop.set()
        t.join(30)
        self.assertFalse(t.is_alive())
        self.assertEqual(self.heartbeat()['stage'], 'stopped')
        self.assertGreaterEqual(self.heartbeat()['pass_n'], 1)


class TheLeaseAndTheWatchdog(LoopCase):
    def test_one_loop_holds_the_lease_a_second_one_exits_naming_it(self):
        l1, l2 = autoloop.Lease(), autoloop.Lease()
        self.assertTrue(l1.acquire(self.clock))
        self.assertFalse(l2.acquire(self.clock))
        owner = autoloop.lease_owner()
        self.assertEqual((owner['held'], owner['pid']), (True, os.getpid()))
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(autoloop.main(['--once']), autoloop.EXIT_BUSY)
        self.assertIn('Automatic switching already runs (process %d)' % os.getpid(), err.getvalue())
        l1.release()
        self.assertFalse(autoloop.lease_owner()['held'])
        self.assertTrue(l2.acquire(self.clock))
        l2.release()

    def stuck_heartbeat(self, pid, age, stage='usage'):
        lease = autoloop.Lease()
        self.assertTrue(lease.acquire(self.clock))
        self.addCleanup(lease.release)
        os.ftruncate(lease.fd, 0)
        os.lseek(lease.fd, 0, os.SEEK_SET)
        os.write(lease.fd, json.dumps({'pid': pid, 'since': T0}).encode())
        hb = autoloop.Heartbeat(self.clock, pid=pid)
        hb.beat(stage)
        self.clock.advance(age)
        return lease

    def test_the_watchdog_stops_a_stuck_loop_by_its_recorded_pid_and_marks_the_restart(self):
        self.stuck_heartbeat(999999, 400)
        st = autoloop.status(self.clock)
        self.assertTrue(st['restart'])
        self.assertIn('reading usage (waiting for Anthropic)', st['why'])
        kills = []
        note = autoloop.watchdog(self.clock, kill=lambda pid, sig: kills.append((pid, sig)), wait_s=0.2,
                                 sleep=lambda s: None)
        self.assertEqual(kills, [(999999, signal.SIGTERM), (999999, signal.SIGKILL)])
        self.assertIn('Restarted automatic switching: it was stuck reading usage', note)
        marker = autoloop.take_restart_marker()
        self.assertEqual(marker['stage'], 'usage')
        self.assertIsNone(autoloop.take_restart_marker())

    def test_the_watchdog_leaves_a_beating_loop_and_a_stranger_alone(self):
        self.stuck_heartbeat(999999, 100)
        self.assertIsNone(autoloop.watchdog(self.clock, kill=lambda *a: self.fail('killed a beating loop')))
        hb = autoloop.Heartbeat(self.clock, pid=888888)            # a heartbeat whose pid does not hold the lease
        hb.beat('usage')
        self.clock.advance(1000)
        self.assertFalse(autoloop.status(self.clock)['restart'])
        self.assertIsNone(autoloop.watchdog(self.clock, kill=lambda *a: self.fail('killed a stranger')))

    def test_after_a_restart_with_a_saved_schedule_everyone_is_still_read_once(self):
        self.answers(ok(20.0, 40.0), ok(20.0, 40.0), ok(20.0, 40.0))
        self.loop().run_pass()                                   # plans saved: the next reads minutes out
        n = len(self.net.calls)
        self.clock.advance(30)
        res = self.loop(after_restart=True).run_pass()
        self.assertEqual(sorted(res.report.tried), ['a1', 'a2', 'a3'])
        self.assertEqual(len(self.net.calls), n + 3)

    def test_after_a_restart_the_first_pass_reads_everyone_and_makes_no_optional_switch(self):
        self.answers(ok(20.0, 40.0, week_reset=T0 + 5 * D), ok(10.0, 50.0, week_reset=T0 + 2 * D),
                     ok(10.0, 30.0, week_reset=T0 + 6 * D))
        self.net.set('tok-a4', ok(10.0, 30.0))
        self.engine.accts.append(account('a4', 4))
        loop = self.loop(after_restart=True)
        loop.scanner.look = lambda timeline, now=None, light=False: {'busy': False, 'idle_for_s': 900.0,
                                                                      'obligations': []}
        res = loop.run_pass()
        self.assertEqual(sorted(res.report.tried), ['a1', 'a2', 'a3', 'a4'])
        self.assertEqual((res.decision.action, res.decision.kind), ('stay', 'optional'))
        self.assertIn('restarted', res.decision.hold)
        self.clock.advance(60)
        loop.run_pass()
        self.assertEqual([s['kind'] for s in self.engine.switches], ['optional'])


class WakeLoadBatteryOffline(LoopCase):
    def test_after_a_wake_nothing_is_read_or_switched_until_the_network_is_back_and_settled(self):
        self.answers(ok(20.0, 40.0), ok(20.0, 40.0), ok(20.0, 40.0))
        ready = [False]
        loop = self.loop(net_ready=lambda: ready[0])
        loop.run_pass()
        n = len(self.net.calls)
        self.answers(ok(99.0, 40.0), ok(10.0, 40.0), ok(10.0, 40.0))       # it filled up while asleep
        self.clock.slept(3 * H)
        self.clock.advance(5)
        res = loop.run_pass()
        self.assertTrue(res.decision.reason.startswith('The Mac just woke'))
        self.assertEqual(len(self.net.calls), n)
        self.assertTrue(self.heartbeat()['settling'])
        self.assertEqual(res.delay, autoloop.NET_CHECK_EVERY)
        ready[0] = True
        self.clock.advance(5)
        res = loop.run_pass()                                               # back, then 30 s more
        self.assertEqual(len(self.net.calls), n)
        self.assertEqual(self.engine.switches, [])
        self.clock.advance(31)
        res = loop.run_pass()
        self.assertEqual(sorted(t for t, _ in self.net.calls[n:]), ['tok-a1', 'tok-a2', 'tok-a3'])
        self.assertEqual([s['kind'] for s in self.engine.switches], ['forced'])
        self.assertIn('a sleep or a clock change', self.log_text())

    def test_while_the_network_stays_down_after_a_wake_the_checks_back_off(self):
        loop = self.loop(net_ready=lambda: False)
        loop.run_pass()
        self.clock.slept(2 * H)
        delays = []
        for _ in range(7):
            res = loop.run_pass()
            delays.append(res.delay)
            self.clock.advance(res.delay)
        self.assertEqual(delays, [5.0, 10.0, 20.0, 40.0, 60.0, 60.0, 60.0])

    def test_a_sleep_never_counts_as_a_hang(self):
        loop = self.loop()
        self.assertTrue(loop.lease.acquire(self.clock))
        self.addCleanup(loop.lease.release)
        loop.hb.beat('sleeping')
        self.clock.slept(3 * H)
        self.clock.advance(5)
        st = autoloop.status(self.clock)
        self.assertFalse(st['restart'])
        self.assertEqual(st['stuck_for_s'], 0.0)

    def test_a_clock_change_never_stretches_or_cuts_the_dwell(self):
        self.answers(ok(95.0, 30.0), ok(10.0, 20.0), ok(10.0, 20.0))
        loop = self.loop()
        loop.run_pass()
        self.assertEqual(len(self.engine.switches), 1)
        self.clock.advance(600)
        for step in (3600.0, -7200.0):
            self.clock.step(step)
            since = autoloop.since_switch(autoloop.load_state(), self.clock)
            self.assertAlmostEqual(since, 600, delta=5, msg=step)

    def test_heavy_load_reads_no_transcript_and_makes_no_optional_switch_but_reads_as_often(self):
        self.answers(ok(20.0, 40.0, week_reset=T0 + 5 * D), ok(10.0, 50.0, week_reset=T0 + 2 * D),
                     ok(10.0, 30.0, week_reset=T0 + 6 * D))
        procs = []
        loop = self.loop(load=lambda: 213 / 8.0, procs=lambda: procs.append(1) or 1)
        res = loop.run_pass()
        self.assertEqual(procs, [])
        self.assertEqual((res.decision.action, res.decision.kind), ('stay', 'optional'))
        self.assertIn('very busy', res.decision.hold)
        p = loop.poller.plans['a1']                                 # usage climbs fastest on a busy Mac: no slower
        self.assertAlmostEqual(p.next - self.clock.mono(), usage.ACTIVE_EVERY, delta=5)   # (the pass's own seconds)

    def test_battery_rules(self):
        self.answers(ok(20.0, 40.0, week_reset=T0 + 5 * D), ok(10.0, 50.0, week_reset=T0 + 2 * D),
                     ok(10.0, 30.0, week_reset=T0 + 6 * D))
        quiet = {'busy': False, 'idle_for_s': 900.0, 'obligations': []}
        loop = self.loop(power=lambda: (False, 24))
        loop.scanner.look = lambda timeline, now=None, light=False: quiet
        res = loop.run_pass()
        self.assertIn('battery is at 24%', res.decision.hold)
        self.assertGreaterEqual(res.delay, autoloop.PASS_EVERY * 3 * 0.9)
        self.answers(ok(92.0, 40.0), ok(10.0, 50.0), ok(10.0, 30.0))
        loop = self.loop(power=lambda: (False, 4))
        self.clock.advance(1000)                                   # past every read planned at 3x on 24%
        res = loop.run_pass()
        self.assertEqual(res.decision.kind, 'forced')
        self.assertEqual(self.engine.switches, [])                  # 4%: no credential written for a line
        self.assertIn('battery at 4%', self.log_text())
        self.answers(ok(100.0, 40.0), ok(10.0, 50.0), ok(10.0, 30.0))
        self.clock.advance(1000)
        loop.run_pass()
        self.assertEqual([s['kind'] for s in self.engine.switches], ['forced'])   # truly blocked: it goes

    def test_an_offline_pass_keeps_the_status_quo_and_says_since_when(self):
        self.answers(ok(95.0, 30.0), ok(10.0, 20.0), ok(10.0, 20.0))
        ready = [True]
        loop = self.loop(net_ready=lambda: ready[0])
        loop.d.switch = lambda *a, **k: None                        # the first pass reads, switches nothing
        loop.run_pass()
        loop.d.switch = self.engine.switch
        self.answers('network', 'network', 'network')
        ready[0] = False
        loop.poller.wake()
        self.clock.advance(120)
        res = loop.run_pass()
        self.assertTrue(res.offline)
        self.assertEqual(res.decision.reason, 'No connection to Anthropic; Claude Code stays where it is.')
        since = self.heartbeat()['offline_since']
        self.assertAlmostEqual(since, self.clock.wall(), delta=0.01)
        self.assertEqual(autoloop.status(self.clock)['offline_since'], since)
        self.clock.advance(60)
        self.assertTrue(loop.run_pass().offline)
        self.assertEqual(self.engine.switches, [])
        ready[0] = True
        self.answers(ok(95.0, 30.0), ok(10.0, 20.0), ok(10.0, 20.0))
        self.clock.advance(60)
        res = loop.run_pass()
        self.assertFalse(res.offline)
        self.assertIsNone(self.heartbeat()['offline_since'])
        self.assertIn('back online', self.log_text())

    def test_every_account_out_stays_put_says_it_once_and_switches_at_the_first_refill(self):
        def a2(clock):
            if clock.wall() < T0 + H:
                return ok(100.0, 40.0, five_reset=T0 + H)
            return ok(5.0, 40.0, five_reset=T0 + 6 * H)
        self.answers(ok(100.0, 40.0, five_reset=T0 + 3 * H), a2, ok(100.0, 40.0, five_reset=T0 + 2 * H))
        loop = self.loop()
        delays, switched_at = [], None
        while self.clock.wall() < T0 + 6 * H:
            res = loop.run_pass()
            if res.switched and switched_at is None:
                switched_at = self.clock.wall()
            if switched_at is None:
                delays.append((self.clock.wall(), res.delay))
            self.clock.advance(res.delay)
        self.assertEqual([s['to'] for s in self.engine.switches], ['a2'])
        self.assertTrue(T0 + H <= switched_at <= T0 + H + 3 * 60, switched_at - T0)
        self.assertEqual([n for n in loop.notes if n[1] == 'limit'],
                         [(loop.notes[0][0], 'limit')])
        self.assertTrue(loop.notes[0][0].startswith('Every account is out. Back at '))
        for at, d in delays:                    # out: no pass a minute, but never past the first refill
            self.assertTrue(autoloop.PASS_EVERY <= d <= autoloop.MAX_SLEEP, d)
            self.assertLessEqual(at + d, max(at + autoloop.PASS_EVERY, T0 + H + usage.RESET_SLACK + 1))
        self.assertLess(len(delays), 30)
        self.assertIsNone(autoloop.load_state()['all_out'])


class WhatThePageIsTold(LoopCase):
    def test_while_the_loop_runs_api_decide_says_what_the_loop_decided(self):
        self.answers(ok(20.0, 40.0, week_reset=T0 + 5 * D), ok(10.0, 50.0, week_reset=T0 + 2 * D),
                     ok(10.0, 30.0, week_reset=T0 + 6 * D))
        loop = self.loop()
        self.assertTrue(loop.lease.acquire(self.clock))
        self.addCleanup(loop.lease.release)
        busy = {'busy': False, 'idle_for_s': 900.0, 'obligations': [{'account_id': 'a1', 'kind': 'doc', 'url': 'u',
                                                                       'at': self.clock.wall() - 60}]}
        loop.scanner.look = lambda timeline, now=None, light=False: busy
        res = loop.run_pass()
        self.assertIn('Claude Doc', res.decision.hold)
        d = autoloop.loop_decision(self.clock)
        self.assertEqual((d.action, d.kind, d.target, d.hold), ('stay', 'optional', 'a2', res.decision.hold))
        loop.lease.release()
        self.assertIsNone(autoloop.loop_decision(self.clock))          # no loop holds the lease: not its word
        self.assertTrue(loop.lease.acquire(self.clock))
        self.assertIsNotNone(autoloop.loop_decision(self.clock))
        self.clock.advance(autoloop.DECISION_FRESH + 1)
        self.assertIsNone(autoloop.loop_decision(self.clock))          # too old: a look from outside instead

    def test_a_history_note_that_hangs_cannot_stall_a_pass(self):
        self.answers(ok(100.0, 40.0), ok(100.0, 40.0), ok(100.0, 40.0))
        hang = self.blocker()
        loop = self.loop(history_note=lambda *a, **k: self.clock.block_here(hang))
        res = loop.run_pass()
        self.assertEqual(res.decision.hold, 'every account is out')
        self.assertIn('noting overran its 10 s deadline', self.log_text())
        self.assertEqual(self.heartbeat()['pass_n'], 1)


class NeedSwitchesAndSettings(LoopCase):
    def test_settings_are_checked_before_anything_is_written(self):
        before = core.read_json(core.data_path('settings.json'))
        for kw in (dict(session_at=49.9), dict(weekly_at=100), dict(model_at=True), dict(session_at=float('nan')),
                   dict(strategy='best'), dict(enabled='yes'), dict(model='Fable; rm -rf')):
            with self.assertRaises(api.AccountsError, msg=kw):
                autoloop.save_settings(**kw)
        self.assertEqual(core.read_json(core.data_path('settings.json')), before)
        got = autoloop.save_settings(enabled=False, session_at=85, model='Fable', strategy='most-room')
        self.assertEqual(got, {'enabled': False, 'session_at': 85.0, 'weekly_at': 95.0, 'model_at': 95.0,
                               'model': 'Fable', 'strategy': 'most-room'})
        self.assertIs(core.settings()['auto'], False)                 # the key the app already uses
        self.assertIsNone(autoloop.save_settings(model='')['model'])

    def test_a_need_switch_goes_with_room_once_per_ten_minutes_per_reason(self):
        self.answers(ok(20.0, 40.0), ok(10.0, 50.0), ok(10.0, 30.0))
        loop = self.loop()
        loop.run_pass()
        d = loop.d
        res = autoloop.need_switch('a3', 'Claude in Chrome is signed in as account 3', deps=d, clock=self.clock)
        self.assertEqual((res.to_id, res.kind, res.source), ('a3', 'need', 'hook'))
        self.engine.active = 'a1'
        autoloop.update_state(lambda s: autoloop._set_live(s, self.clock, 'a1'))
        self.assertIsNone(autoloop.need_switch('a3', 'Claude in Chrome is signed in as account 3', deps=d,
                                               clock=self.clock))
        self.assertEqual(len(self.engine.switches), 1)
        self.assertIsNotNone(autoloop.need_switch('a3', 'another reason', deps=d, clock=self.clock))
        self.engine.active = 'a1'
        autoloop.update_state(lambda s: autoloop._set_live(s, self.clock, 'a1'))
        self.clock.advance(autoloop.NEED_EVERY + 1)
        self.net.set('tok-a3', ok(10.0, 30.0))
        loop.poller.poll(usage.Context(active='a1', order=('a3',)), only=['a3'])
        self.assertIsNotNone(autoloop.need_switch('a3', 'Claude in Chrome is signed in as account 3', deps=d,
                                                  clock=self.clock))

    def test_a_need_switch_never_lands_without_room(self):
        self.answers(ok(20.0, 40.0), ok(88.0, 50.0), ok(10.0, 30.0))
        loop = self.loop()
        loop.run_pass()
        self.assertIsNone(autoloop.need_switch('a2', 'chrome', deps=loop.d, clock=self.clock))
        self.assertEqual(self.engine.switches, [])
        self.assertIn('need switch not made', self.log_text())


if __name__ == '__main__':
    unittest.main()
