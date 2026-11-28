import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The automatic loop and the put-back: a lost login replayed through the loop (a switch, then Claude Code loses its
# login a minute later), the pace of its looks after a switch, the put-back as a stage of a pass (also while the network
# settles, and with automatic switching off), and a forced switch that still follows when Claude Code then empties the
# login. Stand-ins only (fakes_accounts, fakes_autoswitch); made-up *.example accounts; no notification is shown.
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from fakes_autoswitch import FakeClock, FakeEngine, FakeNet, FakeTokens, Home, account, make_loop, set_policy, \
    usage_body
from fakes_accounts import Rig
from switchboard import history, notify
from switchboard.accounts import api, autoloop, context, live, putback, roster, sessions, tokens

MCP = {'mcpOAuth': {'docs-server': {'accessToken': 'mcp-made-up-1', 'expiresAt': 1}}}
H = 3600.0


def ok(five, week):
    now = time.time()
    return (200, {}, usage_body(five=five, week=week, five_reset=now + 4 * H, week_reset=now + 4 * 86400))


class Timed(object):
    """Things that happen at set wall times while the loop sleeps (applied after each wait of the fake clock)."""

    def __init__(self, clock):
        self.clock = clock
        self.due = []
        self.after = []
        real = clock.wait

        def wait(event, s):
            got = real(event, s)
            for item in [d for d in self.due if d[0] <= clock.wall()]:
                self.due.remove(item)
                item[1]()
            for fn in self.after:
                fn()
            return got
        clock.wait = wait

    def at(self, t, fn):
        self.due.append((t, fn))


class RigLoop(unittest.TestCase):
    """The loop across the real account engine on its stand-ins."""

    def setUp(self):
        self._old_home = os.environ.get('SWITCHBOARD_HOME')
        self.tmp = tempfile.mkdtemp(prefix='put-back-loop-')
        os.environ['SWITCHBOARD_HOME'] = os.path.join(self.tmp, 'data')
        os.makedirs(os.environ['SWITCHBOARD_HOME'])
        sessions._reset_for_tests()
        self.clock = FakeClock(wall=time.time(), mono=time.monotonic())
        self.rig = Rig(wall=self.clock.wall, mono=self.clock.mono, sleep=self.clock.sleep)
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        putback.reset()
        self.rig.keep('uuid-a', 'ana@work.example', 'org-a')
        self.clock.advance(1.0)                 # two sign-ins never share an end stamp to the millisecond
        self.rig.keep('uuid-b', 'ana@home.example', 'org-b', siblings=MCP)          # Claude Code is on B
        self.net = FakeNet(self.clock)
        self.told = []
        self.notes = []
        self.moved = []                         # the notification of a move that keeps Claude Code signed in
        deps = autoloop.Deps(net=self.net, net_ready=lambda: True, load=lambda: 0.5, power=lambda: (True, 100),
                             procs=lambda: 1, disk_free=lambda: 10 ** 12,
                             history_note=lambda text, kind='info', **f: self.notes.append(text),
                             notify=self.told.append, notify_move=self.moved.append)
        self.loop = autoloop.Loop(deps=deps, clock=self.clock, rand=lambda: 0.5)

    def tearDown(self):
        self.use.__exit__(None, None, None)
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        putback.reset()
        self.rig.close()
        sessions._reset_for_tests()
        if self._old_home is None:
            os.environ.pop('SWITCHBOARD_HOME', None)
        else:
            os.environ['SWITCHBOARD_HOME'] = self._old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def lose_login(self):
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.rig.set_live(json.dumps(dict(MCP, discoveryState={'made-up': 1}), separators=(',', ':')))

    def has_login(self):
        text = self.rig.live_text()
        return bool(text) and live.parse(text).kind == 'oauth'

    def repairs(self):
        return [e for e in self.rig.events if e[2].get('h') == 'repair']


class TheLostLoginAfterASwitchReplay(RigLoop):
    def test_a_login_lost_a_minute_after_a_switch_is_back_within_seconds(self):
        set_policy(enabled=True)
        api.switch('uuid-a', kind='manual', source='page')               # the page's switch, at t0
        history.note('Switched Claude Code to ana@work.example', 'switch', h='switch', account='ana@work.example',
                     by='you')
        t0 = self.clock.wall()
        saved_a = self.rig.vault('uuid-a')
        self.assertTrue(self.has_login())
        timed = Timed(self.clock)
        timed.at(t0 + 66, lambda: self.rig.sec.drop(self.rig.service, self.rig.account))
        timed.at(t0 + 70, lambda: self.rig.set_live(json.dumps(dict(MCP, discoveryState={'made-up': 1}))))
        back = []
        timed.after.append(lambda: back.append(self.clock.wall()) if not back and self.clock.wall() >= t0 + 66
                           and self.has_login() else None)
        self.loop.sleep(120.0)                                            # the loop was waiting for its next pass
        self.assertTrue(back, 'the login never came back')
        self.assertLessEqual(back[0], t0 + 80)
        blob = json.loads(self.rig.live_text())
        self.assertEqual(blob['claudeAiOauth'], json.loads(saved_a)['claudeAiOauth'])
        self.assertEqual(blob['mcpOAuth'], MCP['mcpOAuth'])               # the connector logins that were in the item
        self.assertEqual(self.rig.vault('uuid-a'), saved_a)               # the saved login was not changed
        self.assertEqual(len(self.repairs()), 1)
        self.assertEqual(self.repairs()[0][0], 'Claude Code lost its sign-in; Switchboard put ana@work.example back')
        self.assertEqual([r.action for r in self.told], ['put_back'])     # one notification
        with open(os.path.join(os.environ['SWITCHBOARD_HOME'], autoloop.LOG)) as f:
            log = f.read()
        self.assertIn('put back: Claude Code lost its sign-in; Switchboard put <account> back', log)
        self.assertNotIn('ana@work.example', log)

    def test_with_automatic_switching_off_the_login_is_put_back_by_the_passes(self):
        set_policy(enabled=False)
        self.lose_login()
        self.loop.run_pass()
        self.assertFalse(self.has_login())                                # seen once: not yet
        self.clock.advance(putback.PUT_BACK_AFTER_S + 1)
        res = self.loop.run_pass()
        self.assertTrue(self.has_login())
        self.assertEqual(res.decision.reason, 'Automatic switching is off.')
        self.assertEqual(autoloop.load_state()['live']['id'], 'uuid-b')    # the same pass read it again
        self.assertEqual(len(self.repairs()), 1)
        self.assertEqual(len(self.told), 1)

    def test_a_chosen_sign_out_while_the_loop_watches_is_never_undone(self):
        set_policy(enabled=True)
        api.switch('uuid-a', kind='manual', source='page')
        history.note('Switched Claude Code to ana@work.example', 'switch', h='switch', account='ana@work.example',
                     by='you')
        t0 = self.clock.wall()
        timed = Timed(self.clock)
        timed.at(t0 + 30, lambda: (self.rig.sec.drop(self.rig.service, self.rig.account),
                                   self.rig.set_config(None)))
        writes = []
        self.rig.kc.on_call = lambda verb, service, acct: writes.append(1) if verb == 'write' and \
            service == self.rig.service else None
        self.loop.sleep(200.0)
        for _ in range(3):
            self.loop.sleep(self.loop.run_pass().delay)
        self.assertEqual(writes, [])
        self.assertIsNone(self.rig.live_text())
        self.assertEqual(self.repairs(), [])

    def test_after_a_put_back_an_emptied_login_still_brings_the_forced_switch(self):
        set_policy(enabled=True)
        self.lose_login()
        self.loop.run_pass()
        self.clock.advance(putback.PUT_BACK_AFTER_S + 1)
        self.loop.run_pass()
        self.assertTrue(self.has_login())
        blob = json.loads(self.rig.live_text())                           # Anthropic refused it: Claude Code empties it
        blob['claudeAiOauth'].update({'accessToken': '', 'refreshToken': ''})
        self.rig.set_live(json.dumps(blob, separators=(',', ':')))
        roster.forget_live()
        self.clock.advance(30)
        res = self.loop.run_pass()
        self.assertEqual((res.decision.action, res.decision.kind, res.decision.target), ('switch', 'forced', 'uuid-a'),
                         res.decision.reason)
        self.assertIsNotNone(res.switched)
        self.assertEqual(json.loads(self.rig.live_text())['claudeAiOauth']['refreshToken'],
                         json.loads(self.rig.vault('uuid-a'))['claudeAiOauth']['refreshToken'])


class ThePace(Home):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock()
        self.engine = FakeEngine([account('a1', 1), account('a2', 2)], 'a1', self.clock)
        self.net = FakeNet(self.clock)
        self.looks = []
        self.reads = []
        real = self.engine.live
        self.engine.live = lambda: (self.reads.append(self.clock.mono()), real())[1]

    def put_back(self):
        self.looks.append(self.clock.mono())
        return api.PutBack('leave', reason='Claude Code has a login')

    def loop(self):
        return make_loop(self.engine, self.clock, self.net, FakeTokens(), put_back=self.put_back)

    def drive(self, loop, until):
        """Pass, sleep, pass... until `until`. Returns the first pass's result; self.passes: (start, end, delay) each
        (a pass takes a little fake time: its stages are waited for on the fake clock)."""
        self.passes = []
        res = first = None
        while self.clock.mono() < until:
            if res is not None:
                loop.sleep(res.delay)
            start = self.clock.mono()
            res = loop.run_pass()
            first = first or res
            self.passes.append((start, self.clock.mono(), res.delay))
        return first

    def assert_pace_kept(self):
        """Every sleep lasted exactly the delay its pass asked for: the looks never moved a pass."""
        for (_, e0, d0), (s1, _, _) in zip(self.passes, self.passes[1:]):
            self.assertAlmostEqual(s1 - e0, d0, delta=0.02)

    def test_after_a_switch_the_looks_are_five_seconds_apart_for_three_minutes_then_as_before(self):
        set_policy(enabled=True)
        self.net.set('tok-a1', ok(97.0, 30.0))                            # a1 is past its line: a forced switch
        self.net.set('tok-a2', ok(10.0, 20.0))
        t0 = self.clock.mono()
        first = self.drive(self.loop(), t0 + 600)
        self.assertIsNotNone(first.switched)
        looks = sorted(self.looks + self.reads)
        inside = [t for t in looks if t - t0 <= autoloop.WATCH_AFTER_SWITCH_S]
        gaps = [b - a for a, b in zip(inside, inside[1:])]
        self.assertGreaterEqual(len(self.looks), 30)
        took = max(e - s for s, e, _ in self.passes)                    # a pass's read is at its start
        self.assertLessEqual(max(gaps), autoloop.WATCH_EVERY_S + took + 0.01, gaps)
        self.assertGreaterEqual(min(b - a for a, b in zip(self.looks, self.looks[1:])), autoloop.WATCH_EVERY_S - 0.01)
        self.assertEqual([t for t in self.looks if t - t0 > autoloop.WATCH_AFTER_SWITCH_S + autoloop.WATCH_EVERY_S],
                         [])
        self.assertLessEqual(len(self.looks), autoloop.WATCH_AFTER_SWITCH_S / autoloop.WATCH_EVERY_S)
        self.assert_pace_kept()

    def test_without_a_switch_the_loop_keeps_its_pace_and_makes_no_extra_looks(self):
        set_policy(enabled=True)
        self.net.set('tok-a1', ok(20.0, 30.0))
        self.net.set('tok-a2', ok(10.0, 20.0))
        t0 = self.clock.mono()
        self.drive(self.loop(), t0 + 600)
        self.assertEqual(self.looks, [])
        self.assert_pace_kept()

    def test_a_switch_the_page_makes_while_the_loop_sleeps_starts_the_looks(self):
        set_policy(enabled=True)
        self.net.set('tok-a1', ok(20.0, 30.0))
        loop = self.loop()
        res = loop.run_pass()
        timed = Timed(self.clock)
        noted = []
        timed.at(self.clock.wall() + 20, lambda: (noted.append(self.clock.mono()), history.note(
            'Switched Claude Code to a2@acct.example', 'switch', h='switch', account='a2@acct.example', by='you',
            t=self.clock.wall())))
        loop.sleep(res.delay)
        self.assertTrue(noted)
        self.assertTrue(self.looks)
        self.assertLessEqual(self.looks[0] - noted[0], autoloop.REQUEST_LOOK + autoloop.WATCH_EVERY_S + 0.01)


class AStageOfThePass(Home):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock()
        self.engine = FakeEngine([account('a1', 1), account('a2', 2)], 'a1', self.clock)
        self.calls = []
        self.answer = api.PutBack('wait', account='a1@acct.example', reason='seen missing once', watch=True)
        self.told = []

    def put_back(self):
        self.calls.append(self.clock.mono())
        res = self.answer
        if res.action == 'put_back':
            self.engine.live_state = api.PRESENT                          # the login is back
        return res

    def loop(self):
        return make_loop(self.engine, self.clock, FakeNet(self.clock), FakeTokens(), put_back=self.put_back,
                         notify=self.told.append)

    def test_a_missing_login_runs_the_put_back_stage_and_a_login_present_does_not(self):
        set_policy(enabled=True)
        loop = self.loop()
        loop.run_pass()
        self.assertEqual(self.calls, [])
        self.engine.live_state = api.ABSENT
        loop.run_pass()
        self.assertEqual(len(self.calls), 1)
        self.engine.live_state = api.UNREADABLE                           # a failed read is never acted on
        loop.run_pass()
        self.assertEqual(len(self.calls), 1)

    def test_it_runs_while_the_network_settles_after_a_wake(self):
        set_policy(enabled=True)
        loop = self.loop()
        loop.settle_until = self.clock.mono() + 30
        self.engine.live_state = api.ABSENT
        loop.run_pass()
        self.assertEqual(len(self.calls), 1)

    def test_after_a_put_back_the_same_pass_reads_the_login_again_and_tells_once(self):
        set_policy(enabled=True)
        loop = self.loop()
        self.engine.live_state = api.ABSENT
        self.answer = api.PutBack('put_back', account='a1@acct.example', reason='put back', watch=True,
                                  line='Claude Code lost its sign-in; Switchboard put a1@acct.example back',
                                  at=self.clock.wall())
        before = self.engine.live_calls
        loop.run_pass()
        self.assertEqual(self.engine.live_calls - before, 2)
        self.assertEqual(autoloop.load_state()['live']['id'], 'a1')
        self.assertEqual([r.action for r in self.told], ['put_back'])

    def test_a_put_back_that_fails_or_overruns_never_stops_the_pass(self):
        set_policy(enabled=True)
        loop = self.loop()
        self.engine.live_state = api.ABSENT

        def boom():
            raise RuntimeError('made-up failure')
        loop.d.put_back = boom
        res = loop.run_pass()
        self.assertIsNotNone(res.decision)
        self.assertIsNone(res.error)


class TheNotification(unittest.TestCase):
    def test_it_names_the_account_by_its_whole_email_and_promises_nothing_about_open_chats(self):
        res = api.PutBack('put_back', account='ana@home.example', reason='', line='x', at=1794837600.0)
        note = autoloop.put_back_note(res)
        self.assertEqual(note.title, 'Switchboard')
        self.assertEqual(note.body, 'Claude Code lost its sign-in. Switchboard put ana@home.example back.')
        self.assertEqual(note.instance, '1794837600')

    def test_each_put_back_is_told_even_two_in_one_hour(self):
        shown = []
        path = os.path.join(tempfile.mkdtemp(prefix='put-back-notify-'), 'notify.json')
        self.addCleanup(shutil.rmtree, os.path.dirname(path), True)
        for at in (1794837600.0, 1794837600.0 + 900):
            note = autoloop.put_back_note(api.PutBack('put_back', account='ana@home.example', at=at))
            notify.send(note, run=lambda cmd: shown.append(cmd[-1]) or 0, path=path, now=at)
            notify.send(note, run=lambda cmd: shown.append(cmd[-1]) or 0, path=path, now=at + 1)   # never twice
        self.assertEqual(len(shown), 2)

    def test_none_is_sent_while_notifications_are_off(self):
        sent = []
        res = api.PutBack('put_back', account='ana@home.example', reason='', line='x', at=1794837600.0)
        with mock.patch.object(notify, 'send', lambda note, **k: sent.append(note)):
            from switchboard import core
            core.set_setting('notify', False)
            autoloop.notify_put_back(res)
            self.assertEqual(sent, [])
            core.set_setting('notify', True)
            autoloop.notify_put_back(res)
            self.assertEqual([n.key for n in sent], ['put-back'])


if __name__ == '__main__':
    unittest.main()
