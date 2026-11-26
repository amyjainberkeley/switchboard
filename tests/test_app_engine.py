import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The app's side of the account engine (switchboard/engine.py over accounts.api), its refusals,
# the guard against another switcher, and the automatic-switching loop's host with its watchdog. Made-up accounts
# only (tests/fakes_accounts_api.py); no process is started, no signal is sent: Popen and killpg are stand-ins.
import os
import plistlib
import shutil
import signal
import subprocess
import sys
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import core, engine
from switchboard.accounts import api
from fakes_accounts_api import install, account_id

A, B, C = 'ana@work.example', 'ana@home.example', 'bo@lab.example'


class Base(unittest.TestCase):
    def setUp(self):
        self.data = core.data_dir()
        shutil.rmtree(self.data, ignore_errors=True)
        os.makedirs(self.data)
        for p in (core.claude_json(),):
            if os.path.exists(p):
                os.remove(p)
        self.fake = install(self)
        self.agents = os.path.join(core.home(), 'Library', 'LaunchAgents')
        shutil.rmtree(self.agents, ignore_errors=True)


class Rows(Base):
    def test_rows_come_from_the_engine_in_order_with_the_live_one_active(self):
        a = self.fake.hold(A, 'org-w', plan='Max 20x', org_name='Work')
        self.fake.hold(B)
        self.fake.sign_in(B)
        now = time.time()
        self.fake.reading(a, five=(41.5, now + 3600), week=(70, now + 3 * 86400), models={'Fable': (12, now + 86400)},
                          at=now - 30, source='status_line')
        got = engine.listing()
        rows = got['rows']
        self.assertEqual([r['email'] for r in rows], [A, B])
        self.assertEqual([r['number'] for r in rows], [1, 2])
        self.assertEqual([r['active'] for r in rows], [False, True])
        r = rows[0]
        self.assertEqual((r['id'], r['org_id'], r['org_name'], r['plan']), (a, 'org-w', 'Work', 'Max 20x'))
        self.assertEqual(r['usage']['session']['pct'], 41.5)
        self.assertEqual(r['usage']['weekly']['pct'], 70)
        self.assertEqual(r['usage']['scoped'], [{'name': 'Fable', 'pct': 12, 'resets': engine._iso(now + 86400)}])
        self.assertEqual(r['models_seen'], ['Fable'])
        self.assertEqual(r['source'], 'status_line')
        self.assertAlmostEqual(r['usage_at'], now - 30, places=3)
        self.assertIsNone(rows[1]['usage'])             # no reading: not known, never 0%
        self.assertFalse(got['disagree'])

    def test_an_unknown_percent_stays_unknown(self):
        a = self.fake.hold(A)
        self.fake.reading(a, five=(None, None), week=(None, time.time() + 86400))
        u = engine.listing()['rows'][0]['usage']
        self.assertIsNone(u['session']['pct'])
        self.assertIsNone(u['weekly']['pct'])

    def test_a_live_login_not_kept_yet_is_one_row_marked_unmanaged(self):
        self.fake.hold(A)
        self.fake.sign_in(C, 'org-ivy')
        rows = engine.listing()['rows']
        self.assertEqual([(r['email'], r['unmanaged'], r['active']) for r in rows],
                         [(A, False, False), (C, True, True)])
        self.assertEqual(rows[1]['id'], account_id(C, 'org-ivy'))

    def test_when_the_login_cant_be_read_the_configs_label_marks_the_one_in_use(self):
        self.fake.hold(A, 'org-w')
        self.fake.hold(B)
        self.fake.sign_in(A, 'org-w')
        self.fake.live_state = api.Live(state=api.UNREADABLE, account_id=None, email=None, proof='none', why='timeout')
        rows = engine.listing()['rows']
        self.assertEqual([r['active'] for r in rows], [True, False])
        self.assertFalse(any(r['unmanaged'] for r in rows))

    def test_an_absent_login_marks_nothing_and_adds_no_row(self):
        self.fake.hold(A)
        self.fake.live_state = api.Live(state=api.ABSENT, account_id=None, email=None, proof='none')
        rows = engine.listing()['rows']
        self.assertEqual([(r['email'], r['active']) for r in rows], [(A, False)])

    def test_label_and_login_disagreeing_is_carried_to_the_page(self):
        a = self.fake.hold(A)
        self.fake.live_state = api.Live(state=api.PRESENT, account_id=a, email=A, proof='fingerprint', disagree=True)
        self.assertTrue(engine.listing()['disagree'])

    def test_readings_the_engine_cant_give_still_leave_the_accounts(self):
        self.fake.hold(A)
        self.fake.fail['readings'] = api.AccountsError('other', 'The readings file is busy.')
        self.assertEqual([r['email'] for r in engine.listing()['rows']], [A])

    def test_an_engine_that_cant_be_asked_is_one_plain_refusal(self):
        self.fake.fail['accounts'] = api.AccountsError('disk', 'The disk is full: free some space, then try again.')
        with self.assertRaises(engine.Refused) as cm:
            engine.listing()
        self.assertEqual(str(cm.exception), 'The disk is full: free some space, then try again.')

    def test_an_engine_not_built_yet_is_said_plainly(self):
        with mock.patch.object(api, 'accounts', side_effect=NotImplementedError('not built yet')):
            with self.assertRaises(engine.Refused) as cm:
                engine.listing()
        self.assertIn('not ready', str(cm.exception))

    def test_frozen_and_failed_readings_get_a_note(self):
        self.assertIn('can only be higher', engine.note_of({'frozen': True}))
        self.assertEqual(engine.note_of({'usage_error': 'No internet'}), 'No internet. These are the last numbers.')
        self.assertIsNone(engine.note_of({}))


class Changes(Base):
    def test_switch_goes_through_the_engine_with_its_kind_and_source(self):
        a = self.fake.hold(A)
        self.fake.hold(B)
        self.fake.sign_in(B)
        r = engine.switch(a, source='cli', reason='Picked in a terminal.')
        self.assertTrue(r.ok)
        self.assertIn(('switch', a, 'manual', 'cli', 'Picked in a terminal.'), self.fake.calls)

    def test_each_engine_refusal_is_its_own_sentence_with_the_right_fix(self):
        self.fake.hold(A)
        for kind, fix in (('keychain_locked', 'unlock'), ('keychain_slow', 'retry'), ('claude_busy', 'retry'),
                          ('dead_login', 'signin'), ('not_found', None), ('disk', None)):
            self.fake.fail['switch'] = api.AccountsError(kind, 'Plain words for %s.' % kind)
            with self.assertRaises(engine.Refused) as cm:
                engine.switch(A)
            self.assertEqual((str(cm.exception), cm.exception.kind, cm.exception.detail),
                             ('Plain words for %s.' % kind, fix, kind))

    def test_a_switch_the_engine_undid_is_a_refusal(self):
        a = self.fake.hold(A)
        undone = api.SwitchResult(ok=False, from_id='x', to_id=a, kind='manual', source='page', reason='',
                                  rolled_back=True, note='The new login did not read back, so the old one is back.')
        with mock.patch.object(api, 'switch', return_value=undone):
            with self.assertRaises(engine.Refused) as cm:
                engine.switch(a)
        self.assertEqual(str(cm.exception), 'The new login did not read back, so the old one is back.')

    def test_add_login_not_ready_says_what_to_do_instead(self):
        with mock.patch.object(api, 'add_login', side_effect=NotImplementedError('not built yet')):
            with self.assertRaises(engine.Refused) as cm:
                engine.add_login('/tmp/x')
        self.assertIn('/login', str(cm.exception))

    def test_saved_identities_are_email_and_organization(self):
        self.fake.hold(A, 'org-w')
        self.fake.hold(B)
        self.assertEqual(engine.saved_identities(), {(A, 'org-w'), (B, '')})


class AutoSettings(Base):
    def test_every_value_is_checked_before_anything_is_set(self):
        for bad in (True, 49.9, 100, float('nan'), float('inf'), '90'):
            with self.assertRaises(core.SwitchError):
                engine.set_auto(session_at=bad)
        with self.assertRaises(core.SwitchError):
            engine.set_auto(strategy='best')            # not one of the orders it takes
        with self.assertRaises(core.SwitchError):
            engine.set_auto(model='-rf /')
        self.assertFalse([c for c in self.fake.calls if c[0] == 'set_auto'])

    def test_lines_order_and_model_reach_the_engine(self):
        with mock.patch.object(engine.HOST, 'ensure') as ensure:
            engine.set_auto(session_at=85, weekly_at=97.5, model_at=92, strategy='most-room', model=' Fable ')
        self.assertEqual(self.fake.calls[-1], ('set_auto', {'session_at': 85.0, 'weekly_at': 97.5, 'model_at': 92.0,
                                                            'strategy': 'most-room', 'model': 'Fable'}))
        ensure.assert_not_called()                      # only on and off ask the loop's host to match them
        a = engine.auto_settings()
        self.assertEqual((a['session_at'], a['weekly_at'], a['model_at'], a['strategy'], a['models']),
                         (85.0, 97.5, 92.0, 'most-room', ['Fable']))

    def test_on_and_off_ask_the_loops_host_to_match_them(self):
        with mock.patch.object(engine.HOST, 'ensure') as ensure:
            engine.set_auto(enabled=True)
        ensure.assert_called_once()
        self.assertTrue(engine.auto_settings()['enabled'])

    def test_on_is_refused_while_another_program_switches_too(self):
        write_agent(self.agents, 'com.example.claude-switch', ['/usr/bin/python3', '/opt/switcher.py'])
        with mock.patch.object(engine.HOST, 'ensure') as ensure:
            with self.assertRaises(engine.Refused) as cm:
                engine.set_auto(enabled=True)
        self.assertEqual(str(cm.exception), "Another program is also switching Claude Code's login "
                                            "(com.example.claude-switch). Turn it off first.")
        self.assertEqual(cm.exception.kind, 'guard')
        ensure.assert_not_called()
        self.assertFalse(self.fake.auto['enabled'])
        engine.set_auto(enabled=False)                  # off is never refused

    def test_settings_the_engine_cant_give_are_off_and_not_available(self):
        self.fake.fail['auto_settings'] = api.AccountsError('other', 'busy')
        a = engine.auto_settings()
        self.assertEqual((a['available'], a['enabled'], a['strategy']), (False, False, 'refills-first'))


def write_agent(folder, label, args, **more):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, label + '.plist'), 'wb') as f:
        plistlib.dump(dict({'Label': label, 'ProgramArguments': args}, **more), f)


class AnotherSwitchingProgram(Base):
    def test_ours_and_unrelated_jobs_are_not_another_switcher(self):
        write_agent(self.agents, 'io.github.amyjainberkeley.switchboard',
                    ['/usr/bin/python3', '-m', 'switchboard', 'serve'])
        write_agent(self.agents, 'io.github.amyjainberkeley.switchboard.menubar', ['/x/switchboard-menubar'])
        write_agent(self.agents, 'com.anthropic.claude-updater', ['/Applications/Claude.app/updater'])
        write_agent(self.agents, 'com.example.backup', ['/usr/bin/rsync', '--account', 'x'])
        with open(os.path.join(self.agents, 'broken.plist'), 'w') as f:
            f.write('not a plist')
        self.assertIsNone(engine.foreign_switcher())

    def test_a_job_that_switches_claude_accounts_is_named(self):
        write_agent(self.agents, 'com.example.tool', ['/opt/bin/claude-accounts', 'rotate', '--auto'])
        self.assertEqual(engine.foreign_switcher(), 'com.example.tool')

    def test_a_disabled_job_does_not_count(self):
        write_agent(self.agents, 'com.example.claude-switcher', ['/opt/x'], Disabled=True)
        self.assertIsNone(engine.foreign_switcher())

    def test_no_launch_agents_folder_is_nothing(self):
        self.assertIsNone(engine.foreign_switcher(os.path.join(self.data, 'nowhere')))


class Health(Base):
    def test_health_items_say_each_thing_and_lines_only_what_is_wrong(self):
        h = {'keychain': {'ok': False, 'line': "macOS's Keychain is locked: unlock the Mac."}, 'facts': 'ok',
             'journal': None, 'store': 1, 'claims': [], 'extra': True}
        items = engine.health_items(h)
        self.assertIn((False, "macOS's Keychain is locked: unlock the Mac."), items)      # its label is not said twice
        self.assertIn((True, "Claude Code's conventions: ok"), items)
        self.assertIn((True, 'A cut-off switch: none'), items)
        self.assertIn((True, "The accounts' file: 1"), items)
        self.assertEqual(engine.health_lines(h), ["macOS's Keychain is locked: unlock the Mac."])
        self.assertEqual(engine.health_lines({'facts': {'ok': False, 'line': 'Claude Code 2.9 is newer than checked'}}),
                         ["Claude Code's conventions: Claude Code 2.9 is newer than checked"])

    def test_stage_words(self):
        self.assertEqual(engine.stage_words('network'), 'waiting for the network')
        self.assertEqual(engine.stage_words('keychain:read'), "waiting for macOS's Keychain")
        self.assertEqual(engine.stage_words('odd-stage'), 'at "odd-stage"')
        self.assertEqual(engine.stage_words(None), '')


# ---------- the loop's host ----------

class FakeProc(object):
    n = 4000

    def __init__(self, args, **kw):
        FakeProc.n += 1
        self.pid, self.args, self.kw = FakeProc.n, args, kw
        self.returncode = None
        self.signals = []
        self.ends_on_term = True

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired('loop', timeout)
        return self.returncode


class Clock(object):
    def __init__(self):
        self.mono, self.wall = 1000.0, 1.8e9

    def tick(self, s, wall=None):
        self.mono += s
        self.wall += s if wall is None else wall


class Host(Base):
    def setUp(self):
        Base.setUp(self)
        self.clock = Clock()
        self.host = engine.AutoHost(monotonic=lambda: self.clock.mono, wall=lambda: self.clock.wall)
        self.procs, self.killed = [], []
        self.host.popen = self.popen
        self.host.killpg = self.killpg
        self.host.looked_for_stale = True
        self.fake.auto['enabled'] = True
        self.logged = []
        p = mock.patch.object(engine, 'log', side_effect=self.logged.append)
        p.start()
        self.addCleanup(p.stop)

    def popen(self, args, **kw):
        p = FakeProc(args, **kw)
        self.procs.append(p)
        return p

    def killpg(self, pid, sig):
        self.killed.append((pid, sig))
        proc = next(p for p in self.procs if p.pid == pid)
        if sig == signal.SIGKILL or (sig == signal.SIGTERM and proc.ends_on_term):
            proc.returncode = -sig

    def beat(self, stage='reading', stuck=0.0, last=None, pid=None):
        self.fake.status.update(pid=self.procs[-1].pid if pid is None else pid, stage=stage, stuck_for_s=stuck,
                                last_pass_at=last if last is not None else self.clock.wall - stuck)

    def test_it_starts_the_loop_in_a_group_of_its_own_on_this_python(self):
        self.host.ensure()
        p = self.procs[-1]
        self.assertEqual(p.args[0], sys.executable)
        self.assertIn('switchboard.accounts.autoloop', ' '.join(p.args))
        self.assertTrue(p.kw.get('start_new_session'))
        self.assertEqual(p.kw['env'].get('SWITCHBOARD_AUTO_HOST'), 'page')
        with open(self.host.pid_file()) as f:
            self.assertEqual(f.read(), str(p.pid))
        self.assertTrue(self.host.running())

    def test_a_fresh_heartbeat_is_left_alone(self):
        self.host.ensure()
        for _ in range(10):
            self.clock.tick(60)
            self.beat('sleeping', stuck=20)
            self.host.ensure()
        self.assertEqual(len(self.procs), 1)
        self.assertEqual(self.killed, [])
        self.assertTrue(self.host.status()['on'])

    def test_a_loop_stuck_in_a_stage_is_restarted_and_the_stage_is_said(self):
        self.host.ensure()
        first = self.procs[-1]
        self.clock.tick(120)
        self.beat('network', stuck=120)
        self.host.ensure()
        self.assertEqual(len(self.procs), 1)            # not yet: 120 s
        self.clock.tick(70)
        self.beat('network', stuck=190)
        self.assertIn('stuck', self.host.status()['problem'])
        self.host.ensure()
        self.assertEqual(self.killed[0], (first.pid, signal.SIGTERM))
        self.assertEqual(len(self.procs), 2)            # a fresh one at once: no pause
        self.assertEqual(self.host.failures, 0)         # a watchdog restart is not counted as an exit
        self.assertTrue(any('stage "network"' in x for x in self.logged))
        st = core.load()
        self.assertTrue(any(e['text'] == 'Restarted automatic switching (it was stuck waiting for the network)'
                            for e in st['events']))
        self.assertIn('waiting for the network', self.host.status()['restarted'])

    def test_one_that_ignores_sigterm_is_killed_after_ten_seconds(self):
        self.host.ensure()
        self.procs[-1].ends_on_term = False
        self.clock.tick(200)
        self.beat('keychain', stuck=200)
        self.host.ensure()
        self.assertEqual([s for _, s in self.killed], [signal.SIGTERM, signal.SIGKILL])
        self.assertEqual(len(self.procs), 2)

    def test_a_kill_waits_for_a_switch_in_progress(self):
        self.host.ensure()
        self.clock.tick(200)
        self.beat('switching', stuck=200)
        self.host.ensure()
        self.assertEqual(self.killed, [])               # put off: it is writing a login
        self.clock.tick(30)
        self.beat('switching', stuck=230)
        self.host.ensure()
        self.assertEqual(self.killed, [])
        self.clock.tick(31)
        self.beat('switching', stuck=261)
        self.host.ensure()                              # 60 s later it goes all the same
        self.assertEqual(len(self.killed), 1)

    def test_a_sleep_never_counts_as_stuck(self):
        self.host.ensure()
        self.clock.tick(60)
        self.beat('sleeping', stuck=10)
        self.host.ensure()
        self.clock.tick(5, wall=3 * 3600)               # three hours asleep, five seconds awake
        self.beat('sleeping', stuck=3 * 3600)           # a heartbeat age taken from the wall clock
        self.host.ensure()
        self.assertEqual(self.killed, [])
        self.clock.tick(60)
        self.beat('waking', stuck=3 * 3600 + 60)
        self.host.ensure()
        self.assertEqual(self.killed, [])               # still inside its fresh 180 s after the wake
        self.clock.tick(130)
        self.beat('waking', stuck=3 * 3600 + 190)
        self.host.ensure()
        self.assertEqual(len(self.killed), 1)           # awake 190 s with no heartbeat: stuck after all

    def test_a_heartbeat_from_an_older_loop_counts_from_this_ones_start(self):
        self.fake.status.update(pid=1, stage='network', stuck_for_s=5000, last_pass_at=1)
        self.host.ensure()
        self.clock.tick(60)
        self.host.ensure()
        self.assertEqual(self.killed, [])
        self.assertTrue(self.host.status()['starting'])

    def test_a_loop_that_exits_by_itself_comes_back_after_a_pause_that_grows(self):
        # A first and a second death start again within seconds (2 s, then 10 s); the long pauses are for a loop that
        # keeps dying (engine.AutoHost.PAUSES).
        self.host.ensure()
        self.procs[-1].returncode = 1
        self.clock.tick(1)
        self.host.ensure()
        self.assertEqual(len(self.procs), 1)
        self.assertIn('exit 1', self.host.status()['problem'] or self.host.problem)
        self.clock.tick(1)
        self.host.ensure()
        self.assertEqual(len(self.procs), 1)
        self.clock.tick(1.5)
        self.host.ensure()
        self.assertEqual(len(self.procs), 2)
        self.procs[-1].returncode = 1
        self.clock.tick(1)
        self.host.ensure()
        self.clock.tick(9)
        self.host.ensure()
        self.assertEqual(len(self.procs), 2)            # 10 s the second time
        self.clock.tick(2)
        self.host.ensure()
        self.assertEqual(len(self.procs), 3)

    def test_off_leaves_it_reading_and_with_no_account_kept_it_stops_with_sigterm(self):
        self.fake.hold(A)
        self.host.ensure()
        p = self.procs[-1]
        self.fake.auto['enabled'] = False
        self.host.ensure()
        self.assertEqual(self.killed, [])               # off stops switching, never reading
        self.assertTrue(self.host.running())
        self.fake.remove(A)                             # nothing left to read
        self.host.ensure()
        self.assertEqual(self.killed, [(p.pid, signal.SIGTERM)])
        self.assertFalse(self.host.running())
        self.assertFalse(os.path.exists(self.host.pid_file()))

    def test_a_heartbeat_that_cant_be_read_is_measured_from_the_start(self):
        self.fake.fail['auto_status'] = api.AccountsError('other', 'busy')
        self.host.ensure()
        self.clock.tick(100)
        self.host.ensure()
        self.assertEqual(self.killed, [])
        self.clock.tick(100)
        self.host.ensure()
        self.assertEqual(len(self.killed), 1)           # no heartbeat for 200 s of awake time

    def test_status_says_what_the_loop_waits_for(self):
        self.host.ensure()
        self.clock.tick(10)
        self.beat('network', stuck=5)
        self.fake.status['waiting_for'] = 'network'
        self.host.ensure()
        s = self.host.status()
        self.assertEqual((s['on'], s['stage'], s['waiting_for']), (True, 'network', 'network'))


if __name__ == '__main__':
    unittest.main()
