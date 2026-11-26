import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# `switchboard auto check`: one pass of auto-switch now, in this terminal, then exit, with the loop's own exit codes.
# Every outside thing is a stand-in (tests/fakes_autoswitch.py): the account engine, the network, the clock.
import io
import json
import os
import sys
import threading
import time
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import autocheck, core, history
from switchboard.accounts import autoloop, usage
from fakes_autoswitch import FakeClock, FakeEngine, FakeNet, FakeTokens, Home, T0, account, set_policy, usage_body

H, D = 3600.0, 86400.0


def ok(five, week, five_reset=None, week_reset=None):
    return (200, {}, usage_body(five=five, week=week, five_reset=five_reset or T0 + 4 * H,
                                week_reset=week_reset or T0 + 4 * D))


class CheckCase(Home):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock()
        self.accts = [account('a1', 1), account('a2', 2), account('a3', 3)]
        self.engine = FakeEngine(self.accts, 'a1', self.clock)
        self.net = FakeNet(self.clock)
        self.tokens = FakeTokens()
        self.disk = 10 ** 10
        set_policy()

    def deps(self, **kw):
        d = dict(accounts=self.engine.accounts, live=self.engine.live, switch=self.engine.switch,
                 keep_pass=self.engine.keep_pass, upkeep_pass=self.engine.upkeep_pass,
                 resolve_journal=self.engine.resolve_journal, access_token=self.tokens, net=self.net,
                 net_ready=lambda: True, load=lambda: 0.5, power=lambda: (True, 100), procs=lambda: 1,
                 disk_free=lambda: self.disk, history_note=lambda text, kind='info', **f: None)
        d.update(kw)
        return autoloop.Deps(**d)

    def answers(self, a1, a2, a3):
        self.net.set('tok-a1', a1)
        self.net.set('tok-a2', a2)
        self.net.set('tok-a3', a3)

    def check(self, *args, **deps):
        out, err = io.StringIO(), io.StringIO()
        code = autocheck.main(['check'] + list(args), deps=self.deps(**deps), clock=self.clock, out=out, err=err)
        return code, out.getvalue(), err.getvalue()

    def settings_bytes(self):
        try:
            with open(core.data_path('settings.json'), 'rb') as f:
                return f.read()
        except OSError:
            return b''


class ExitCodes(CheckCase):
    def test_a_check_that_moves_claude_code_exits_0_and_history_says_it_came_from_a_check(self):
        self.answers(ok(92.0, 30.0), ok(10.0, 20.0, week_reset=T0 + 2 * D), ok(10.0, 20.0, week_reset=T0 + 3 * D))
        code, out, _ = self.check()
        self.assertEqual(code, autoloop.EXIT_SWITCHED)
        self.assertEqual(len(self.engine.switches), 1)
        sw = self.engine.switches[0]
        self.assertEqual((sw['to'], sw['kind'], sw['source']), ('a2', 'forced', 'check'))
        self.assertTrue(sw['reason'].startswith('auto check: '), sw['reason'])
        self.assertIn('5-hour at 92%', sw['reason'])
        self.assertTrue(out.startswith('Switched Claude Code to a2@acct.example: '), out)
        self.assertEqual([(r['kind'], r['to']) for r in autoloop.load_state()['switches']], [('forced', 'a2')])

    def test_nothing_to_do_exits_4(self):
        set_policy(strategy='most-room')
        self.answers(ok(20.0, 40.0), ok(30.0, 40.0), ok(30.0, 40.0))
        code, out, _ = self.check()
        self.assertEqual(code, autoloop.EXIT_NOTHING)
        self.assertEqual(self.engine.switches, [])
        self.assertTrue(out.startswith('Stays on a1@acct.example: '), out)

    def test_a_move_it_wants_with_nowhere_to_go_exits_3(self):
        self.answers(ok(100.0, 40.0), ok(100.0, 40.0), ok(100.0, 40.0))
        code, out, _ = self.check()
        self.assertEqual(code, autoloop.EXIT_BLOCKED)
        self.assertEqual(self.engine.switches, [])
        self.assertIn('Every account is out', out)

    def test_a_move_held_back_before_it_starts_exits_3_and_says_why(self):
        self.disk = 1024
        self.answers(ok(92.0, 30.0), ok(10.0, 20.0), ok(10.0, 20.0))
        code, out, _ = self.check()
        self.assertEqual(code, autoloop.EXIT_BLOCKED)
        self.assertEqual(self.engine.switches, [])
        self.assertIn('It did not move: disk almost full', out)

    def test_a_switch_that_fails_exits_1(self):
        self.engine.fail_switch = 'no answer from the Keychain'
        self.answers(ok(92.0, 30.0), ok(10.0, 20.0), ok(10.0, 20.0))
        code, out, _ = self.check()
        self.assertEqual(code, autoloop.EXIT_ERROR)
        self.assertIn('It did not move: switch failed', out)

    def test_a_pass_that_raises_exits_1_and_lets_the_lease_go(self):
        def broken(loop):
            raise RuntimeError('made up')
        real = autocheck.CheckLoop.run_pass
        autocheck.CheckLoop.run_pass = broken
        self.addCleanup(setattr, autocheck.CheckLoop, 'run_pass', real)
        code, out, _ = self.check()
        self.assertEqual(code, autoloop.EXIT_ERROR)
        self.assertEqual(out, 'The check did not finish (RuntimeError).\n')
        self.assertFalse(autoloop.lease_owner()['held'])
        self.assertEqual(autoloop.ONE_CHECK, [])


class TheLease(CheckCase):
    def hold_lease(self, decision=None, last_pass_at=None):
        lease = autoloop.Lease()
        self.assertTrue(lease.acquire(self.clock))
        self.addCleanup(lease.release)
        hb = {'v': 1, 'pid': os.getpid(), 'boot': self.clock.boot(), 'stage': 'sleeping',
              'wall': self.clock.wall(), 'mono': self.clock.mono()}
        if decision:
            hb.update(decision=decision, last_pass_at=last_pass_at, last_pass_mono=self.clock.mono())
        usage.write_atomic(core.data_path(autoloop.HEARTBEAT), hb)
        return lease

    def test_while_the_loop_holds_the_lease_a_check_moves_nothing_and_exits_5(self):
        self.hold_lease({'action': 'stay', 'target': None, 'kind': None, 'hold': None,
                         'reason': 'a1@acct.example is under its lines.'}, last_pass_at=T0 - 120)
        self.answers(ok(92.0, 30.0), ok(10.0, 20.0), ok(10.0, 20.0))
        code, out, _ = self.check()
        self.assertEqual(code, autoloop.EXIT_BUSY)
        self.assertEqual(self.engine.switches, [])
        self.assertEqual(self.net.count(), 0)
        self.assertEqual(out.count('\n'), 1, out)
        self.assertIn("Switchboard's loop already runs (process %d), so this check moves nothing." % os.getpid(), out)
        self.assertIn('a1@acct.example is under its lines.', out)
        self.assertIn(usage.clock_text(T0 - 120, T0), out)

    def test_with_no_decision_yet_it_says_so(self):
        self.hold_lease()
        code, out, _ = self.check()
        self.assertEqual(code, autoloop.EXIT_BUSY)
        self.assertIn('It has not decided yet.', out)

    def test_the_lease_is_let_go_after_a_check(self):
        self.answers(ok(20.0, 40.0), ok(30.0, 40.0), ok(30.0, 40.0))
        self.check()
        self.assertFalse(autoloop.lease_owner()['held'])
        self.assertEqual(usage.read_json(core.data_path(autoloop.HEARTBEAT))['stage'], 'stopped')


class AutoSwitchOff(CheckCase):
    def test_with_auto_switch_off_a_check_still_runs_and_turns_nothing_on(self):
        set_policy(enabled=False)
        before = self.settings_bytes()
        self.answers(ok(92.0, 30.0), ok(10.0, 20.0), ok(10.0, 20.0))
        code, out, _ = self.check()
        self.assertEqual(code, autoloop.EXIT_SWITCHED)
        self.assertEqual(self.engine.switches[0]['to'], 'a2')
        self.assertEqual(self.settings_bytes(), before)
        s = core.settings()
        self.assertIs(s.get('auto'), False)
        self.assertNotIn('auto_set_by_you', s)
        self.assertFalse(autoloop.load_settings().enabled)
        self.assertEqual(autoloop.ONE_CHECK, [])
        self.assertIn('Auto-switch is off; this check decides as if it were on.', out)


class ThePace(CheckCase):
    def test_a_check_right_after_another_reads_nothing_and_says_how_old_its_numbers_are(self):
        self.answers(ok(20.0, 40.0), ok(30.0, 40.0), ok(30.0, 40.0))
        self.check()
        first = self.net.count()
        self.assertGreater(first, 0)
        self.clock.advance(40)
        code, out, _ = self.check()
        self.assertEqual(self.net.count(), first)
        self.assertRegex(out, r'In use: a1@acct\.example, .*; numbers 4\d s old\.')


class TheLogAndTheHistory(CheckCase):
    def test_the_loop_log_marks_a_check_and_names_no_email(self):
        self.answers(ok(92.0, 30.0), ok(10.0, 20.0), ok(10.0, 20.0))
        self.check()
        with open(core.data_path(autoloop.LOG)) as f:
            text = f.read()
        self.assertIn('check: pass 1: switched to account 2', text)
        self.assertNotIn('@', text)

    def test_a_move_by_a_check_reads_in_the_log_as_auto_with_its_reason(self):
        rec = history.from_event('Switched Claude Code to a2@acct.example', 'switch',
                                 {'h': 'switch', 'to': 'a2@acct.example', 'frm': 'a1@acct.example',
                                  'reason': 'auto check: a1@acct.example 5-hour at 92% (line 90%)', 'by': 'auto'})
        self.assertEqual(history.parts(rec)[1], 'auto: auto check: a1@acct.example 5-hour at 92% (line 90%)')


class TheStream(CheckCase):
    def test_log_follow_as_json_shows_a_move_a_check_makes_as_it_happens(self):
        got, stop = [], threading.Event()
        follower = threading.Thread(target=history.follow, args=(got.append,),
                                    kwargs={'as_json': True, 'every': 0.05, 'stop': stop})
        follower.start()
        self.addCleanup(follower.join, 10)
        self.addCleanup(stop.set)

        def switch(target, kind='manual', source='cli', reason=''):
            # The account engine's own line for a switch it made (switcher.py), with the facts it gives.
            res = self.engine.switch(target, kind=kind, source=source, reason=reason)
            to = '%s@acct.example' % target
            history.from_event('Switched Claude Code to %s' % to, 'switch',
                               {'h': 'switch', 'account': to, 'to': to, 'frm': 'a1@acct.example', 'reason': reason,
                                'by': 'you' if source in ('page', 'menu', 'cli') else 'auto'})
            return res
        self.answers(ok(92.0, 30.0), ok(10.0, 20.0), ok(10.0, 20.0))
        self.assertEqual(self.check(switch=switch)[0], autoloop.EXIT_SWITCHED)
        end = time.monotonic() + 20
        while not got and time.monotonic() < end:
            time.sleep(0.05)
        moves = [json.loads(x) for x in got if json.loads(x).get('kind') == 'switch']
        self.assertEqual(len(moves), 1, got)
        self.assertEqual((moves[0]['to'], moves[0]['from'], moves[0]['by']),
                         ('a2@acct.example', 'a1@acct.example', 'auto'))
        self.assertTrue(moves[0]['reason'].startswith('auto check: a1@acct.example 5-hour at 92%'), moves[0])


class TheCommand(CheckCase):
    def test_switchboard_auto_check_reaches_the_check(self):
        from switchboard import __main__ as cli
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(['auto', 'check', '--made-up']), 2)
        self.assertEqual(out.getvalue(), 'Unknown word: --made-up.\n%s\n' % '\n'.join(autocheck.FORMS))
        self.assertEqual(self.engine.switches, [])

    def test_the_help_names_it_after_auto_on_off_and_auto_forms_list_it_second(self):
        from switchboard import __main__ as cli
        lines = cli.command_help('auto').splitlines()
        self.assertTrue(lines[0].startswith('switchboard auto [on|off]'))
        self.assertTrue(lines[1].startswith('switchboard auto check'), lines[1])
        self.assertEqual(cli.AUTO_FORMS[1:1 + len(autocheck.FORMS)], autocheck.FORMS)
        self.assertEqual(cli.AUTO_FORMS[1 + len(autocheck.FORMS)], 'switchboard auto last NAME')


class TheDemo(CheckCase):
    def test_the_demo_refuses_a_check(self):
        core.DEMO = True
        self.addCleanup(setattr, core, 'DEMO', False)
        with self.assertRaises(core.SwitchError):
            self.check()


class AnotherProgram(CheckCase):
    def test_a_check_refuses_while_another_program_switches_the_login(self):
        from switchboard import engine
        real = engine.foreign_switcher
        engine.foreign_switcher = lambda folder=None: 'com.example.swapper'
        self.addCleanup(setattr, engine, 'foreign_switcher', real)
        self.answers(ok(92.0, 30.0), ok(10.0, 20.0), ok(10.0, 20.0))
        code, out, _ = self.check()
        self.assertEqual(code, autoloop.EXIT_ERROR)
        self.assertEqual(self.engine.switches, [])
        self.assertIn('com.example.swapper', out)


if __name__ == '__main__':
    unittest.main()
