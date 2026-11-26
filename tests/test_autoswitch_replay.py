import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# One day of automatic switching as a scenario, with fixed times and made-up accounts. Each event is a case the
# policy and the loop must get right. Settings: refills-first, 5-hour 90, weekly 95, model Fable.
#   Earlier switches that day (OLD_SWITCHES): 03:21:55 optional, 04:26:47 forced (5-hour 90%), 08:20:36 optional,
#   10:09:55 forced (5-hour 90%), 13:23:32 optional. Then 15:02:01 optional ("refills first") and 15:07:34 optional
#   again, 5 min 33 s later: the first move may go, the second must be held. A session's Claude Doc made just before
#   15:02 holds the first move too, or it would be left on the account the session moved away from.
#   Keychain reads time out from 10:29:51 to 10:39:41, and the loop then says nothing until 13:23:31: the heartbeat
#   must name the stuck stage, and a read that never answers must cost one stage, not the loop. The Mac is awake from
#   10:00 to 13:13:33, then asleep with the lid closed (a DarkWake at 13:22:20; awake again 13:29:30): only awake
#   time counts toward a restart.
#   Offline: every account's read fails with "network" in the same second at 17:19:57-59. That is offline, not stuck.
import json
import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import core
from switchboard.accounts import api, autoloop, policy, usage
from fakes_autoswitch import FakeClock, FakeEngine, FakeNet, FakeTokens, Home, T0, account, make_loop, set_policy, \
    usage_body

H, D = 3600.0, 86400.0
DAY = T0 - 15 * H                       # T0 stands for 15:00:00 that day; every time below is relative to it


def at(hms):
    h, m, s = (int(x) for x in hms.split(':'))
    return DAY + h * H + m * 60 + s


OLD_SWITCHES = [('03:21:55', 'optional'), ('04:26:47', 'forced'), ('08:20:36', 'optional'), ('10:09:55', 'forced'),
                ('13:23:32', 'optional')]
ONE, THREE, FIVE = 'acct-1', 'acct-3', 'acct-5'
ACCTS = [account(ONE, 1, email='one@first.example'), account(THREE, 3, email='three@second.example'),
         account(FIVE, 5, email='five@third.example')]
FABLE = policy.Settings(enabled=True, session_at=90.0, weekly_at=95.0, model_at=95.0, model='Fable',
                        strategy='refills-first')
WEEKLY_RESET = {THREE: at('15:00:00') + 100 * H, FIVE: at('15:00:00') + 30 * H, ONE: at('15:00:00') + 19 * H}


def reading(aid, now, five, week, fable, age=60.0):
    t = now - age
    return api.Reading(aid, (api.Window('5-hour', five, now + 3 * H, t, 'endpoint'),
                             api.Window('weekly', week, WEEKLY_RESET[aid], t, 'endpoint'),
                             api.Window('model:Fable', fable, WEEKLY_RESET[aid], t, 'endpoint')), t, 'endpoint')


def since_and_recent(now, extra=()):
    times = [(at(t), k) for t, k in OLD_SWITCHES] + list(extra)
    since = now - max(t for t, _ in times)
    recent = sum(1 for t, k in times if k == 'optional' and now - t <= 6 * H)
    return since, recent


class TwoOptionalSwitchesMinutesApart(unittest.TestCase):
    """The policy makes at most the first move, and never the second five and a half minutes later."""

    def snap(self, now, active, readings, extra=(), **kw):
        since, recent = since_and_recent(now, extra)
        base = dict(busy=False, idle_for_s=1200.0, since_switch_s=since, optional_recent=recent, load_per_core=1.0,
                    battery_pct=80.0, on_ac=True)
        base.update(kw)
        return policy.Snapshot(now=now, accounts=tuple(ACCTS), readings=readings, settings=FABLE, active=active,
                               **base)

    def at_1502(self, **kw):
        now = at('15:02:01')
        return self.snap(now, THREE, {THREE: reading(THREE, now, 40.0, 50.0, 30.0),
                                      FIVE: reading(FIVE, now, 10.0, 60.0, 20.0),
                                      ONE: reading(ONE, now, 10.0, 20.0, 10.0, age=1200.0)}, **kw)

    def at_1507(self, **kw):
        now = at('15:07:34')
        return self.snap(now, FIVE, {THREE: reading(THREE, now, 40.0, 50.0, 30.0),
                                     FIVE: reading(FIVE, now, 11.0, 60.0, 20.0),
                                     ONE: reading(ONE, now, 10.0, 20.0, 10.0, age=20.0)},
                         extra=[(at('15:02:01'), 'optional')], **kw)

    def test_the_first_move_is_a_real_gain_and_may_go_when_nothing_would_be_stranded(self):
        d = policy.decide(self.at_1502())
        self.assertEqual((d.action, d.kind, d.target), ('switch', 'optional', FIVE))

    def test_the_second_move_five_and_a_half_minutes_later_is_held(self):
        s = self.at_1507()
        self.assertAlmostEqual(s.since_switch_s, 333, delta=1)
        d = policy.decide(s)
        self.assertEqual((d.action, d.kind, d.target), ('stay', 'optional', ONE))
        self.assertIn('the last switch was', d.hold)
        self.assertIn('45 minutes', d.hold)

    def test_even_without_the_dwell_two_optional_moves_in_six_hours_is_the_most(self):
        d = policy.decide(dataclass_replace(self.at_1507(), since_switch_s=3 * H))
        self.assertEqual(d.action, 'stay')
        self.assertIn('2 optional switches', d.hold)

    def test_the_session_doc_holds_the_first_move_too(self):
        doc = {'kind': 'doc', 'url': 'https://claude.ai/artifact/DocDocDocDocDocDoc5678', 'at': at('14:58:30'),
               'until': at('15:28:30'), 'account_id': THREE}
        d = policy.decide(self.at_1502(obligations=(doc,)))
        self.assertEqual((d.action, d.kind), ('stay', 'optional'))
        self.assertIn('Claude Doc made as three@second.example', d.hold)
        d = policy.decide(self.at_1502(busy=True, idle_for_s=0.0))
        self.assertEqual((d.action, d.hold), ('stay', 'Claude Code is working'))


def dataclass_replace(obj, **kw):
    import dataclasses
    return dataclasses.replace(obj, **kw)


class TheAfternoonThroughTheLoop(Home):
    """The loop itself, pass by pass from 14:55 to 16:00, the network answering with the scenario's numbers."""

    def setUp(self):
        super().setUp()
        self.clock = FakeClock(wall=at('14:55:00'))
        self.engine = FakeEngine(ACCTS, THREE, self.clock)
        self.net = FakeNet(self.clock)
        set_policy(model='Fable')
        autoloop.update_state(lambda s: s.update(switches=[{'t': at(t), 'kind': k, 'source': 'auto'}
                                                           for t, k in OLD_SWITCHES]))
        autoloop.update_state(lambda s: autoloop._set_live(s, self.clock, THREE))
        autoloop.update_state(lambda s: s['live'].update(t=at('13:23:32'), mono=None))

        def answer(aid, five, week, fable):
            def a(clock):
                if aid == ONE and clock.wall() < at('15:07:00'):
                    return (429, {'retry-after': '300'}, {})          # account 1 had no fresh numbers at 15:02
                return (200, {}, usage_body(five=five, week=week, models={'Fable': (fable, WEEKLY_RESET[aid])},
                                            five_reset=clock.wall() + 3 * H, week_reset=WEEKLY_RESET[aid]))
            return a
        self.net.set('tok-' + THREE, answer(THREE, 40.0, 50.0, 30.0))
        self.net.set('tok-' + FIVE, answer(FIVE, 10.0, 60.0, 20.0))
        self.net.set('tok-' + ONE, answer(ONE, 10.0, 20.0, 10.0))

    def run_afternoon(self, loop, until='16:00:00'):
        while self.clock.wall() < at(until):
            res = loop.run_pass()
            self.clock.advance(res.delay)
        return [(s['at'], s['from'], s['to'], s['kind']) for s in self.engine.switches]

    def test_one_optional_move_in_the_hour_and_none_at_1507(self):
        self.clock.advance(at('15:01:00') - self.clock.wall())
        loop = make_loop(self.engine, self.clock, self.net, FakeTokens())
        loop.scanner.look = lambda timeline, now=None, light=False: {'busy': False, 'idle_for_s': 1200.0,
                                                                      'obligations': []}
        moves = self.run_afternoon(loop)
        self.assertEqual(len(moves), 1, moves)
        t, frm, to, kind = moves[0]
        self.assertEqual((frm, to, kind), (THREE, FIVE, 'optional'))
        self.assertTrue(at('15:01:00') <= t < at('15:03:00'), t - at('15:00:00'))
        self.assertFalse(any(at('15:07:00') <= m[0] for m in moves))

    def test_with_the_sessions_doc_open_nothing_moves_until_it_has_waited_its_half_hour(self):
        root = os.path.join(os.environ['CLAUDE_CONFIG_DIR'], 'projects', 'proj')
        os.makedirs(root)
        chat = os.path.join(root, 'session.jsonl')
        created = at('14:58:30')
        text = json.dumps({'verdict': 'ok', 'created': {'id': 'd'},
                           'frame': {'slug': 'notes', 'url': 'https://claude.ai/artifact/DocDocDocDocDocDoc5678'}},
                          separators=(',', ':'))
        with open(chat, 'w') as f:
            f.write(json.dumps({'type': 'user', 'timestamp': datetime.fromtimestamp(created, timezone.utc).isoformat(),
                                'message': {'content': [{'type': 'tool_result', 'tool_use_id': 'x',
                                                         'content': text}]}}) + '\n')
        os.utime(chat, (created, created))
        self.clock.advance(at('14:59:00') - self.clock.wall())          # the session made its Doc at 14:58:30
        loop = make_loop(self.engine, self.clock, self.net, FakeTokens())
        moves = self.run_afternoon(loop)
        self.assertEqual(len(moves), 1, moves)
        self.assertGreaterEqual(moves[0][0], created + 1800)             # the Doc's half hour
        self.assertEqual(moves[0][1:], (THREE, FIVE, 'optional'))


class SilenceAfterAKeychainOutage(Home):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock(wall=at('10:39:41'), mono=200000.0)

    def stuck_since_1039(self):
        lease = autoloop.Lease()
        self.assertTrue(lease.acquire(self.clock))
        self.addCleanup(lease.release)
        os.ftruncate(lease.fd, 0)
        os.lseek(lease.fd, 0, os.SEEK_SET)
        os.write(lease.fd, json.dumps({'pid': 424242}).encode())
        autoloop.Heartbeat(self.clock, pid=424242).beat('live')          # the last thing it did: read the Keychain

    def test_the_heartbeat_names_the_stuck_stage_and_a_restart_is_due_three_minutes_in(self):
        self.stuck_since_1039()
        self.clock.advance(170)
        self.assertFalse(autoloop.status(self.clock)['restart'])
        self.clock.advance(11)                                           # 10:42:42: 181 s awake without a beat
        st = autoloop.status(self.clock)
        self.assertTrue(st['restart'])
        self.assertEqual(st['stage'], 'live')
        self.assertEqual(st['why'], "stuck reading Claude Code's login (waiting for the Keychain) for 3 min")
        kills = []
        note = autoloop.watchdog(self.clock, kill=lambda pid, sig: kills.append(pid), wait_s=0.1,
                                 sleep=lambda s: None)
        self.assertIn("stuck reading Claude Code's login", note)
        self.assertEqual(set(kills), {424242})

    def test_awake_time_counts_and_the_lid_closed_does_not(self):
        self.stuck_since_1039()
        self.clock.advance(at('13:13:33') - at('10:39:41'))                # awake until 13:13:33
        before = autoloop.status(self.clock)['stuck_for_s']
        self.assertAlmostEqual(before, at('13:13:33') - at('10:39:41'), delta=1)
        self.clock.slept(at('13:22:20') - at('13:13:33'))                  # clamshell sleep until the DarkWake
        self.assertEqual(autoloop.status(self.clock)['stuck_for_s'], before)

    def test_a_keychain_read_that_never_answers_costs_one_stage_not_the_loop(self):
        accts = [account('acct-1', 1), account('acct-3', 3)]
        engine = FakeEngine(accts, 'acct-1', self.clock)
        engine.block_live = self.blocker()
        net = FakeNet(self.clock)
        for a in ('acct-1', 'acct-3'):
            net.set('tok-' + a, (200, {}, usage_body(five=20.0, week=30.0, five_reset=self.clock.wall() + H,
                                                     week_reset=self.clock.wall() + D)))
        set_policy()
        loop = make_loop(engine, self.clock, net, FakeTokens())
        self.assertTrue(loop.lease.acquire(self.clock))
        self.addCleanup(loop.lease.release)
        worst = 0.0
        while self.clock.wall() < at('10:55:00'):
            res = loop.run_pass()
            st = autoloop.status(self.clock)
            self.assertFalse(st['restart'], st)
            worst = max(worst, st['beat_age_s'] or 0.0)
            loop.sleep(res.delay)
        self.assertLess(worst, 2 * autoloop.BEAT_EVERY)
        hb = usage.read_json(core.data_path(autoloop.HEARTBEAT))
        self.assertGreater(hb['last_pass_at'], at('10:54:00'))
        with open(core.data_path(autoloop.LOG)) as f:
            log = f.read()
        self.assertIn('live overran its %d s deadline' % autoloop.STAGE_DEADLINE['live'], log)
        self.assertIn('live skipped: the last one is still running', log)
        self.assertEqual(engine.live_calls, 1)                              # never a second read piled on


class TheOfflineEvening(Home):
    def test_1719_every_read_failing_at_once_is_offline_not_stuck(self):
        clock = FakeClock(wall=at('17:19:57'))
        accts = [account('acct-%d' % i, i) for i in range(1, 6)]
        engine = FakeEngine(accts, 'acct-1', clock)
        net = FakeNet(clock)
        for a in accts:
            net.set('tok-' + a.id, 'network')
        set_policy()
        loop = make_loop(engine, clock, net, FakeTokens(), net_ready=lambda: False)
        self.assertTrue(loop.lease.acquire(clock))
        self.addCleanup(loop.lease.release)
        loop.poller.wake()
        res = loop.run_pass()
        self.assertTrue(res.offline)
        st = autoloop.status(clock)
        self.assertIsNotNone(st['offline_since'])
        self.assertFalse(st['restart'])
        stamps = [c for c in net.calls]
        self.assertEqual(len(stamps), 5)


if __name__ == '__main__':
    unittest.main()
