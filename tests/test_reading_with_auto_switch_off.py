import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The accounts are read whether auto-switch is on or off: the setting turns switching off, never reading.
# 1. The loop, pass by pass with the setting off: it reads, renews saved logins, and never switches, never says it
#    would, never notes that every account is out (fakes_autoswitch: no network, no Keychain).
# 2. The page's host of that loop: with the setting off it runs while an account is kept, turning the setting off
#    does not stop it, turning it on restarts nothing, no account kept or the demo never starts it.
# 3. What the page, the menu bar, the terminal and doctor say about it. Made-up accounts only; no process is started
#    and no signal is sent (Popen and killpg are stand-ins, as in test_app_engine.py).
import io
import os
import shutil
import signal
import socket
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import __main__ as cli
from switchboard import core, engine, menubar, server, term
from switchboard.accounts import api, autoloop, policy, usage
from fakes_accounts_api import install
from fakes_autoswitch import FakeClock, FakeEngine, FakeNet, FakeTokens, Home, T0, account, make_loop, set_policy, \
    usage_body
from test_app_engine import Clock, FakeProc

A, B = 'ana@work.example', 'ana@home.example'
H, D = 3600.0, 86400.0


def ok(five, week):
    return (200, {}, usage_body(five=five, week=week, five_reset=T0 + 4 * H, week_reset=T0 + 4 * D))


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ---------- 1. the loop with the setting off ----------

class TheLoopWithSwitchingOff(Home):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock()
        self.engine = FakeEngine([account('a1', 1), account('a2', 2), account('a3', 3)], 'a1', self.clock)
        self.net = FakeNet(self.clock)
        self.upkeeps = []
        self.engine.upkeep_pass = lambda budget_s=60.0: self.upkeeps.append(budget_s) or []
        set_policy(enabled=False)

    def loop(self, **kw):
        return make_loop(self.engine, self.clock, self.net, FakeTokens(), **kw)

    def answers(self, a1, a2, a3):
        for aid, a in (('a1', a1), ('a2', a2), ('a3', a3)):
            self.net.set('tok-' + aid, a)

    def log_text(self):
        try:
            with open(core.data_path(autoloop.LOG)) as f:
                return f.read()
        except OSError:
            return ''

    def test_it_reads_every_account_and_renews_saved_logins_but_never_switches(self):
        self.answers(ok(97.0, 30.0), ok(10.0, 20.0), ok(10.0, 20.0))    # the account in use past its line
        loop = self.loop()
        for _ in range(3):
            res = loop.run_pass()
            self.assertEqual((res.decision.action, res.decision.reason), ('stay', 'Automatic switching is off.'))
            self.clock.advance(15 * 60)
        self.assertEqual(self.engine.switches, [])
        self.assertEqual([self.net.count('tok-' + a) >= 2 for a in ('a1', 'a2', 'a3')], [True] * 3, self.net.calls)
        self.assertTrue(self.upkeeps)                                   # saved logins are kept fresh all the same
        r = usage.readings(now=self.clock.wall())
        self.assertEqual(sorted(r), ['a1', 'a2', 'a3'])
        self.assertEqual(autoloop.load_state().get('switches') or [], [])

    def test_a_dry_run_never_says_it_would_switch(self):
        self.answers(ok(100.0, 30.0), ok(10.0, 20.0), ok(10.0, 20.0))
        self.loop(dry_run=True).run_pass()
        self.assertNotIn('would switch', self.log_text())
        self.assertIn('pass 1: stay', self.log_text())

    def test_every_account_out_is_not_noted_while_it_is_off(self):
        self.answers(ok(100.0, 100.0), ok(100.0, 100.0), ok(100.0, 100.0))
        loop = self.loop()
        res = loop.run_pass()
        self.assertIsNone(res.decision.hold)
        self.assertEqual(loop.notes, [])                                 # no "Every account is out." in the history
        self.assertFalse(autoloop.load_state().get('all_out'))
        self.assertLessEqual(res.delay, autoloop.PASS_EVERY * 1.1)       # its next pass comes as usual: no long wait

    def test_turned_on_while_it_runs_the_next_pass_switches(self):
        self.answers(ok(97.0, 30.0), ok(10.0, 20.0), ok(10.0, 20.0))
        loop = self.loop()
        self.assertEqual(loop.run_pass().decision.action, 'stay')
        set_policy(enabled=True)                                         # the page's switch, while the loop runs
        self.clock.advance(60)
        res = loop.run_pass()
        self.assertIsNotNone(res.switched)
        self.assertEqual([(s['to'], s['kind']) for s in self.engine.switches], [('a2', 'forced')])

    def test_the_policy_says_stay_for_every_kind_of_switch_while_off(self):
        snap = policy.Snapshot(now=T0, accounts=(account('a1', 1), account('a2', 2)), readings={},
                               settings=policy.settings_from({'enabled': False}), active='a1', live_refused=True)
        self.assertEqual(policy.decide(snap).action, 'stay')
        self.assertEqual(policy.need(snap, 'a2').action, 'stay')


# ---------- 2. the loop's host ----------

class HostBase(unittest.TestCase):
    def setUp(self):
        self.data = core.data_dir()
        shutil.rmtree(self.data, ignore_errors=True)
        os.makedirs(self.data)
        self.fake = install(self)
        self.clock = Clock()
        self.host = engine.AutoHost(monotonic=lambda: self.clock.mono, wall=lambda: self.clock.wall)
        self.procs, self.killed = [], []
        self.host.popen = self.popen
        self.host.killpg = self.killpg
        self.host.looked_for_stale = True
        self.logged = []
        for target, name, value in ((engine, 'log', self.logged.append), (engine, 'HOST', self.host),
                                    (engine, 'foreign_switcher', lambda folder=None: None)):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = False
        env = mock.patch.dict(os.environ, {'SWITCHBOARD_PORT': str(free_port())})   # a page is never asked on its
        env.start()                                                                  # real port
        self.addCleanup(env.stop)

    def popen(self, args, **kw):
        p = FakeProc(args, **kw)
        self.procs.append(p)
        return p

    def killpg(self, pid, sig):
        self.killed.append((pid, sig))
        proc = next(p for p in self.procs if p.pid == pid)
        if sig == signal.SIGKILL or (sig == signal.SIGTERM and proc.ends_on_term):
            proc.returncode = -sig

    def beat(self, stage='sleeping', stuck=0.0):
        self.fake.status.update(pid=self.procs[-1].pid, stage=stage, stuck_for_s=stuck,
                                last_pass_at=self.clock.wall - stuck)


class TheHostWithSwitchingOff(HostBase):
    def test_with_the_setting_off_and_one_account_kept_the_loop_starts(self):
        self.fake.hold(A)
        self.assertFalse(self.fake.auto['enabled'])
        self.host.ensure()
        self.assertEqual(len(self.procs), 1)
        self.assertTrue(self.host.running())

    def test_turning_the_setting_off_does_not_stop_it(self):
        self.fake.hold(A)
        self.fake.hold(B)
        engine.set_auto(enabled=True)
        p = self.procs[-1]
        engine.set_auto(enabled=False)
        self.host.ensure()                                              # and the tick after it
        self.assertEqual(self.killed, [])
        self.assertIs(self.procs[-1], p)
        self.assertTrue(self.host.running())

    def test_turning_it_on_while_it_runs_restarts_nothing(self):
        self.fake.hold(A)
        self.host.ensure()
        p = self.procs[-1]
        self.clock.tick(30)
        self.beat()
        engine.set_auto(enabled=True)
        self.assertEqual((self.procs, self.killed), ([p], []))          # the same loop reads the setting each pass
        self.assertTrue(engine.auto_settings()['on'])

    def test_with_no_account_kept_it_does_not_run(self):
        self.host.ensure()
        self.assertEqual(self.procs, [])
        self.assertFalse(engine.auto_settings()['reading_wanted'])

    def test_with_the_setting_on_it_runs_with_no_account_too(self):
        self.fake.auto['enabled'] = True
        self.host.ensure()
        self.assertEqual(len(self.procs), 1)

    def test_removing_the_last_account_stops_it_while_off(self):
        self.fake.hold(A)
        self.host.ensure()
        p = self.procs[-1]
        self.fake.remove(A)
        self.host.ensure()
        self.assertEqual(self.killed, [(p.pid, signal.SIGTERM)])
        self.assertFalse(self.host.running())

    def test_the_demo_never_starts_it(self):
        core.DEMO = True
        self.fake.hold(A)
        self.fake.auto['enabled'] = True
        self.host.ensure()
        self.assertEqual(self.procs, [])

    def test_a_look_at_the_accounts_that_fails_leaves_it_running(self):
        self.fake.hold(A)
        self.host.ensure()
        self.fake.fail['accounts'] = api.AccountsError('other', "Switchboard's account list can't be read.")
        self.host.ensure()
        self.assertEqual(self.killed, [])
        self.assertTrue(self.host.running())

    def test_a_stuck_loop_is_restarted_with_the_setting_off_and_said_in_words_of_reading(self):
        self.fake.hold(A)
        self.host.ensure()
        first = self.procs[-1]
        self.clock.tick(190)
        self.beat('network', stuck=190)
        a = engine.auto_settings()
        self.assertIsNone(a['problem'])                                 # auto-switch is off: nothing of it is stuck
        self.assertEqual(a['reading_problem'], 'Reading the accounts is stuck (waiting for the network). Switchboard '
                                               'restarts it.')
        self.host.ensure()
        self.assertEqual(self.killed[0], (first.pid, signal.SIGTERM))
        self.assertEqual(len(self.procs), 2)
        texts = [e['text'] for e in core.load()['events']]
        self.assertIn('Restarted reading the accounts (it was stuck waiting for the network)', texts)
        self.assertRegex(engine.auto_settings()['restarted'],
                         r'^Restarted reading the accounts at \d\d:\d\d \(it was stuck waiting for the network\)\.$')


# ---------- 3. what it says ----------

class TheStatus(HostBase):
    def test_off_with_an_account_kept_says_auto_switch_off_and_reading_on(self):
        self.fake.hold(A)
        self.host.ensure()
        self.clock.tick(30)
        self.beat()
        a = engine.auto_settings()
        self.assertEqual((a['enabled'], a['on'], a['problem']), (False, False, None))
        self.assertEqual((a['reading'], a['reading_wanted'], a['reading_problem']), (True, True, None))
        self.assertTrue(a['running'])
        v = server.auto_view()                                          # the page's auto view carries them
        self.assertEqual((v['reading'], v['reading_wanted'], v['reading_problem'], v['line']), (True, True, None, None))

    def test_a_loop_that_stopped_is_said_as_reading_while_off_and_as_switching_while_on(self):
        self.fake.hold(A)
        self.host.ensure()
        self.procs[-1].returncode = 1
        self.clock.tick(1)
        self.host.ensure()
        a = engine.auto_settings()
        self.assertEqual((a['reading'], a['problem']), (False, None))
        self.assertEqual(a['reading_problem'], 'Reading the accounts stopped (exit 1). It starts again by itself.')
        self.fake.auto['enabled'] = True
        a = engine.auto_settings()
        self.assertEqual(a['problem'], 'Automatic switching stopped (exit 1). It starts again by itself.')
        self.assertIsNone(a['reading_problem'])                         # said once, by `problem`

    def test_on_and_healthy_keeps_its_meaning(self):
        self.fake.auto['enabled'] = True
        self.host.ensure()
        self.clock.tick(30)
        self.beat()
        a = engine.auto_settings()
        self.assertEqual((a['on'], a['reading'], a['reading_wanted'], a['problem'], a['reading_problem']),
                         (True, True, True, None, None))

    def test_the_history_notes_a_reading_problem_while_off(self):
        self.fake.hold(A)
        self.host.ensure()
        self.procs[-1].returncode = 1
        self.clock.tick(1)
        self.host.ensure()
        from switchboard import history
        with core.locked() as st:
            history.note_tick(st, {}, {}, {}, engine.auto_settings(), None)
        self.assertIn('Reading the accounts stopped (exit 1). It starts again by itself.',
                      [e['text'] for e in core.load()['events']])


class TheWords(unittest.TestCase):
    def test_the_menu_bar_says_a_reading_problem_while_off(self):
        off = {'enabled': False, 'on': False, 'reading': False, 'reading_wanted': True, 'session_at': 90,
               'weekly_at': 95, 'reading_problem': 'Reading the accounts stopped (exit 1). It starts again by itself.'}
        self.assertEqual(menubar.auto_note(off), 'Reading the accounts stopped (exit 1). It starts again by itself.')
        self.assertIsNone(menubar.auto_note(dict(off, reading=True, reading_problem=None)))
        said = 'Restarted reading the accounts at 10:42 (it was stuck).'
        self.assertEqual(menubar.auto_note(dict(off, reading=True, reading_problem=None, restarted=said)), said)
        self.assertIsNone(menubar.auto_note(dict(off, reading_wanted=False, reading_problem=None, restarted=said)))

    def test_switchboard_auto_says_the_accounts_are_still_read_while_off(self):
        off = {'enabled': False, 'reading_wanted': True}
        self.assertEqual(term.auto_line(off), 'Auto-switch is off: the accounts are still read, and Claude Code moves '
                                              'only when you move it. Turn it on: switchboard auto on')
        self.assertEqual(term.auto_line(off, yours=True),
                         'Auto-switch is off, as you set it: the accounts are still read, and Claude Code moves only '
                         'when you move it. Turn it on: switchboard auto on')
        self.assertEqual(term.auto_line(dict(off, reading_wanted=False)),            # nothing kept: nothing to read
                         'Auto-switch is off. Turn it on: switchboard auto on')
        self.assertEqual(term.auto_line(dict(off, reading_wanted=False), yours=True),
                         'Auto-switch is off until you turn it on: switchboard auto on')
        self.assertEqual(term.auto_line(off, reading=False), 'Auto-switch is off. Turn it on: switchboard auto on')


class Doctor(HostBase):
    def lines(self, kept, page=False):
        said = []
        with mock.patch.object(cli, 'page_running', lambda: page):
            cli._doctor_auto(lambda good, text, fix='': said.append((good, text, fix)), kept)
        return said

    def test_off_with_accounts_kept_and_the_page_down(self):
        self.fake.hold(A)
        got = self.lines(1)
        self.assertIn((None, 'Reading the accounts: not running', 'It runs inside the page: start it with '
                                                                   '`switchboard serve`.'), got)
        self.assertIn((None, 'Automatic switching: off', 'Turn it on: switchboard auto on'), got)

    def test_off_with_a_fresh_heartbeat_reading_is_ok(self):
        self.fake.hold(A)
        with open(self.host.pid_file(), 'w') as f:
            f.write(str(os.getpid()))
        self.fake.status.update(pid=os.getpid(), stage='sleeping', stuck_for_s=0.0)
        got = self.lines(1, page=True)
        self.assertEqual(got[0][:2], (True, 'Reading the accounts: last heartbeat 0 s ago, between checks (pid %d)'
                                      % os.getpid()))
        self.assertIn((None, 'Automatic switching: off', 'Turn it on: switchboard auto on'), got)

    def test_on_and_stuck_says_no_once(self):
        self.fake.auto['enabled'] = True
        with open(self.host.pid_file(), 'w') as f:
            f.write(str(os.getpid()))
        self.fake.status.update(pid=os.getpid(), stage='network', stuck_for_s=600.0)
        got = self.lines(0, page=True)
        self.assertEqual([g for g, _, _ in got].count(False), 1, got)
        self.assertEqual(got[0][:2], (False, 'Reading the accounts: running but no heartbeat for 10 min (waiting '
                                             'for the network)'))
        self.assertEqual(got[1][:2], (None, 'Automatic switching: on'))

    def test_nothing_kept_and_off(self):
        got = self.lines(0)
        self.assertEqual(got[0][:2], (None, 'Reading the accounts: no account kept yet'))

    def test_the_whole_doctor_has_both_lines(self):
        self.fake.hold(A)
        self.fake.hold(B)
        out = io.StringIO()
        with mock.patch.object(cli, 'page_running', lambda: False), \
                mock.patch.object(cli.linkapp, 'status', lambda fresh=False: {'installed': False, 'on': False}), \
                mock.patch.object(cli.browser, 'present', lambda: False), \
                mock.patch.object(cli.browser, 'profiles', lambda: {}), redirect_stdout(out):
            cli.doctor()
        self.assertIn(' --  Reading the accounts: not running\n', out.getvalue())
        self.assertIn(' --  Automatic switching: off\n', out.getvalue())


if __name__ == '__main__':
    unittest.main()
