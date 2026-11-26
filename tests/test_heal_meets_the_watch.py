import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Switchboard's self-repair beside its watch on itself, where the two meet. The self-check starts from the page's loop
# through the loop's own wrapper, on a thread of its own: a slow one or one that raises never holds up or ends the
# loop. A command that changes something while the page waits for its port says so in one sentence and makes the
# change here; a page from before the data-folder check is refused with the one thing to do. Three callers settle a
# switch cut off part-way: when two run at once, one settles it and the other reports no repair of its own. The locks
# the repairs take give up after the page's lock deadline and end cleanly. And after a move on older numbers, the read
# of the account landed on never renews the login Claude Code now holds, whether that login is there or lost.
# Stand-ins only (fakes_accounts, fakes_autoswitch, a page stand-in on a free port); made-up *.example accounts.
import fcntl
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from fakes_accounts import Rig
from fakes_autoswitch import FakeClock, FakeNet, set_policy
from switchboard import __main__ as cli
from switchboard import core, selfcheck, server, watch
from switchboard.accounts import api, autoloop, context, live, policy, putback, roster, sessions, switcher, tokens, \
    usage, vault
from test_cli_commands import Cli
from test_cli_with_page import FakePage, state
from test_watch_loop import LoopBase


# ---------- the self-check in the page's loop ----------

class TheSelfCheckInThePagesLoop(LoopBase):
    def setUp(self):
        LoopBase.setUp(self)
        self.patch(server.core, 'DEMO', False)
        self.patch(selfcheck, 'RUN', {'at': None, 'running': False})
        self.release = threading.Event()
        self.addCleanup(self.release.set)

    def test_a_slow_one_runs_on_its_own_thread_and_never_holds_the_loop_up(self):
        began = []

        def slow(fix, now):
            began.append(1)
            self.release.wait(30)               # a look that takes minutes
            return []
        self.patch(selfcheck, 'CHECKS', (slow,))
        server.loop(ticks=3)                    # returns: the loop went on
        self.assertEqual(len(self.ticks), 1)
        end = time.monotonic() + 5
        while not began and time.monotonic() < end:
            time.sleep(0.01)
        self.assertEqual(began, [1])            # one run, still going; never a second at once
        self.assertTrue(selfcheck.RUN['running'])
        self.assertNotEqual(server.STAGE['name'], 'checking what Switchboard keeps')   # the page's watchdog sees the
        self.release.set()                                                          # loop move on

    def test_one_that_raises_ends_nothing(self):
        def broken(fix, now):
            raise RuntimeError('made up')
        self.patch(selfcheck, 'CHECKS', (broken,))
        self.patch(selfcheck, 'tick', lambda now=None, start=None: (_ for _ in ()).throw(RuntimeError('made up tick')))
        said = self.run_loop()
        self.assertTrue(self.ticks)
        self.assertTrue(any('the self-check failed' in x for x in said), said)

    def test_a_run_that_raises_inside_its_thread_frees_the_next_run(self):
        def broken(fix, now):
            raise RuntimeError('made up')
        self.patch(selfcheck, 'CHECKS', (broken,))
        selfcheck.tick(now=1000.0, start=lambda job: job())
        self.assertFalse(selfcheck.RUN['running'])


# ---------- a change while the page waits for its port ----------

class Elsewhere(object):
    """Another program on the page's port: it answers, but not as Switchboard's page."""

    def __init__(self, test):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                data = b'<html>another program</html>'
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            do_POST = do_GET
        self.httpd = ThreadingHTTPServer(('127.0.0.1', core.port()), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        test.addCleanup(self.httpd.server_close)
        test.addCleanup(self.httpd.shutdown)


class TheTakenPort(Cli):
    def setUp(self):
        Cli.setUp(self)
        p = mock.patch.object(cli.engine.HOST, 'ensure')       # no loop is started for real
        p.start()
        self.addCleanup(p.stop)

    def held(self):
        watch.mark_port(core.port())            # the page's own mark: it runs, waiting for its port

    def test_another_program_on_the_port_one_sentence_and_the_change_is_made_here(self):
        self.two()
        self.held()
        Elsewhere(self)
        code, out, err = self.run_cli('auto', 'on')
        self.assertEqual(code, 0, (out, err))
        # the problem, that the change is made here all the same, and the one thing to do: the page's own words
        # for a taken port (watch.port_words), never the port in use
        self.assertEqual(err.strip().splitlines(), [
            "Port %d is in use by another program, so this command can't reach the page and makes the change itself. "
            "Pick a free port: switchboard update --port %d" % (core.port(), 7802 if core.port() == 7801 else 7801)])
        self.assertTrue(self.fake.auto['enabled'])                     # made here, as with no page

    def test_nothing_on_the_port_while_the_page_waits_for_it(self):
        self.two()
        self.held()
        code, out, err = self.run_cli('auto', 'on')
        self.assertEqual(code, 0, (out, err))
        self.assertEqual(err.strip().splitlines(), [
            'Nothing answers on port %d yet: the page waits there for the port to be free, so this command makes the '
            'change itself. Pick a free port: switchboard update --port %d'
            % (core.port(), 7802 if core.port() == 7801 else 7801)])
        self.assertTrue(self.fake.auto['enabled'])                     # made here, as with no page

    def test_without_the_mark_nothing_more_is_said_as_before(self):
        self.two()
        Elsewhere(self)
        code, out, err = self.run_cli('auto', 'on')
        self.assertEqual(code, 0, (out, err))
        self.assertEqual(err.strip(), '')

    def test_a_page_stopped_on_purpose_is_not_said_to_wait(self):
        self.two()
        self.held()
        watch.note_stopped()
        code, out, err = self.run_cli('auto', 'on')
        self.assertEqual(err.strip(), '')

    def test_sign_in_to_all_says_it_runs_on_the_page(self):
        self.two()
        self.held()
        Elsewhere(self)
        code, out, err = self.run_cli('signin')
        self.assertEqual(code, 1)
        self.assertEqual(out.strip(), 'Sign in to all runs on the page.')
        # Sign in to all can't go on here: its sentence never says a change was made
        self.assertEqual(err.strip(), "Port %d is in use by another program, so this command can't reach the page. "
                                      "Pick a free port: switchboard update --port %d"
                         % (core.port(), 7802 if core.port() == 7801 else 7801))

    def test_the_sentences_follow_the_rules_for_words(self):
        # the fact (and, for a command that goes on, that it makes the change itself) in one sentence, then the one
        # thing to do, in the same words the page's own note for a taken port ends with
        for refused in (True, False):
            for here in (True, False):
                said = cli.away_words(7799, refused, here)
                self.assertEqual(said.count('. '), 1, said)
                self.assertTrue(said.endswith('. ' + watch.free_port_words(7799)), said)
                self.assertEqual('makes the change itself' in said, here)
                self.assertNotIn('bypass', said.lower())
        self.assertTrue(watch.port_words(7799).endswith(watch.free_port_words(7799)))
        self.assertEqual(watch.free_port_words(7801), 'Pick a free port: switchboard update --port 7802')

    def test_a_check_says_no_change(self):
        self.two()
        self.held()
        Elsewhere(self)
        with mock.patch.object(cli.browser, 'can_see_tabs', lambda: False):
            code, out, err = self.run_cli('check')
        self.assertNotIn('makes the change itself', err)
        self.assertIn("so this command can't reach the page. Pick a free port", err)


class TheOlderPage(Cli):
    def test_it_is_refused_with_the_one_thing_to_do_and_nothing_is_sent(self):
        page = FakePage(self, {('GET', '/api/state'): (200, state(folder=None)), ('POST', '/api/auto'): (200, {})})
        with self.assertRaises(cli.engine.Refused) as e:
            cli.ask_page('/api/auto', {'enabled': False}, timeout=5)
        self.assertTrue(str(e.exception).endswith('Nothing was changed. Stop that page and start it again: '
                                                  'switchboard serve'), str(e.exception))
        self.assertEqual(page.posted, [])

    def test_with_the_background_job_it_names_the_restart_doctor_names(self):
        plist = os.path.join(core.home(), 'Library', 'LaunchAgents', cli.AGENT + '.plist')
        os.makedirs(os.path.dirname(plist))
        open(plist, 'w').close()
        self.assertEqual(cli.restart_page_words(),
                         'Restart the page: launchctl kickstart -k gui/%d/%s' % (os.getuid(), cli.AGENT))


# ---------- three callers of one cut-off switch's record ----------

class RigBase(unittest.TestCase):
    def setUp(self):
        self._home = os.environ.get('SWITCHBOARD_HOME')
        sessions._reset_for_tests()
        self.rig = Rig()
        self.ctx = self.rig.ctx
        os.makedirs(self.rig.data, exist_ok=True)
        os.environ['SWITCHBOARD_HOME'] = self.rig.data          # the loop's folder and the engine's, as on a Mac
        self.use = context.use(self.ctx)
        self.use.__enter__()
        putback.reset()
        self.rig.keep('uuid-a', 'ana@work.example', 'org-a')
        time.sleep(0.002)
        self.rig.keep('uuid-b', 'ana@home.example', 'org-b')    # Claude Code is on B

    def tearDown(self):
        self.use.__exit__(None, None, None)
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        putback.reset()
        self.rig.close()
        sessions._reset_for_tests()
        if self._home is None:
            os.environ.pop('SWITCHBOARD_HOME', None)
        else:
            os.environ['SWITCHBOARD_HOME'] = self._home


class TwoSettleOneRecord(RigBase):
    def cut_off(self):
        """A switch to A whose login was written but whose record was never dropped (the process was killed)."""
        saved = self.rig.vault('uuid-a')
        self.rig.set_live(saved)
        switcher._write_journal(self.ctx, {'id': 'made-up', 'step': 'writing', 'from_id': 'uuid-b', 'to_id': 'uuid-a',
                                           'kind': 'manual', 'pid': 1, 'target_fp': live.parse(saved).fingerprint})
        old = time.time() - 600
        os.utime(switcher.journal_path(self.ctx), (old, old))
        roster.forget_live()

    def race(self, loop_first):
        """The loop's settle (api.resolve_journal, as Loop._settle_cut_off calls it) and the self-check's at once."""
        self.cut_off()
        real, inside, now = switcher._resolve_locked, [], {'n': 0, 'most': 0}
        lock = threading.Lock()

        def slow(ctx):
            with lock:
                inside.append(threading.current_thread().name)
                now['n'] += 1
                now['most'] = max(now['most'], now['n'])
            try:
                time.sleep(0.4)                 # holding the switch lock: the other caller arrives meanwhile
                return real(ctx)
            finally:
                with lock:
                    now['n'] -= 1
        out = {}

        def by_loop():
            out['loop'] = api.resolve_journal()

        def by_check():
            out['check'] = selfcheck.check_journal(True, time.time())
        first, second = (by_loop, by_check) if loop_first else (by_check, by_loop)
        with mock.patch.object(switcher, '_resolve_locked', slow):
            t1 = threading.Thread(target=first, name='first')
            t2 = threading.Thread(target=second, name='second')
            t1.start()
            time.sleep(0.1)
            t2.start()
            t1.join(30)
            t2.join(30)
        self.assertEqual(now['most'], 1, inside)                     # never two on the record at once
        return out, inside

    def check_one_report(self, out):
        loop_said = bool(out['loop'])
        check_said = [r for r in out['check'] if r.state == selfcheck.REPAIRED]
        self.assertEqual(loop_said + len(check_said), 1, out)         # one settles it; the other says no repair
        self.assertFalse(os.path.exists(switcher.journal_path(self.ctx)))
        said = [e for e in self.rig.events if e[0].startswith('Repaired the cut-off switch')]
        self.assertEqual(len(said), 1, self.rig.events)

    def test_the_loop_first_then_the_self_check(self):
        out, inside = self.race(loop_first=True)
        self.assertEqual(inside[0], 'first')
        self.check_one_report(out)
        self.assertTrue(out['loop'])
        self.assertEqual([(r.state, r.found) for r in out['check']], [(selfcheck.OK, 'none')])

    def test_the_self_check_first_then_the_loop(self):
        out, inside = self.race(loop_first=False)
        self.check_one_report(out)
        self.assertIsNone(out['loop'])


# ---------- the locks the repairs take ----------

class Held(object):
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.f = open(path, 'a')
        fcntl.flock(self.f, fcntl.LOCK_EX)

    def release(self):
        if not self.f.closed:
            self.f.close()


class LocksGiveUpCleanly(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(core.data_dir(), ignore_errors=True)
        os.makedirs(core.data_dir())
        p = mock.patch.object(core, 'LOCK_DEADLINE', 0.5)
        p.start()
        self.addCleanup(p.stop)

    def timed(self, fn):
        started = time.monotonic()
        got = fn()
        self.assertLess(time.monotonic() - started, 5.0)
        return got

    def test_a_damaged_settings_file_while_another_process_holds_the_settings(self):
        core.write_json(core.data_path(core.SETTINGS_COPY), {'auto': True})
        with open(core.data_path('settings.json'), 'w') as f:
            f.write('{"auto": tr')
        held = Held(core.data_path('settings.json.lock'))
        self.addCleanup(held.release)
        self.assertEqual(self.timed(core.settings), {'auto': True})   # its copy for now; it used to raise LockTimeout
        held.release()
        self.assertIs(core.settings().get('auto'), True)               # restored at the next read
        with open(core.data_path('settings.json')) as f:
            self.assertEqual(json.load(f).get('auto'), True)

    def test_the_self_check_of_the_pages_file_while_its_lock_is_held(self):
        with open(core.data_path('state.json'), 'w') as f:
            f.write('{"accounts": ')
        held = Held(core.data_path('.lock'))
        self.addCleanup(held.release)
        with mock.patch.object(selfcheck, 'CHECKS', (selfcheck.check_state,)):
            got = self.timed(lambda: selfcheck.run(fix=True))
        self.assertEqual([(r.name, r.state) for r in got], [('state', selfcheck.FAILED)])

    def test_the_accounts_list_while_its_lock_is_held(self):
        rig = Rig()
        self.addCleanup(rig.close)
        use = context.use(rig.ctx)
        use.__enter__()
        self.addCleanup(use.__exit__, None, None, None)
        ctx = rig.ctx
        path = vault.store_path(ctx)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            f.write('{"accounts": ')
        core.write_json(path + '.1', {'schema': vault.SCHEMA, 'accounts': {}})
        held = Held(ctx.path('accounts.lock'))
        self.addCleanup(held.release)
        with mock.patch.object(vault, 'RESTORE_LOCK_S', 0.3):
            got = self.timed(lambda: selfcheck.check_accounts(True, time.time()))
        self.assertEqual([r.state for r in got], [selfcheck.FAILED])


# ---------- the read after a move on older numbers ----------

class TheLandingRead(RigBase):
    def setUp(self):
        RigBase.setUp(self)
        set_policy(enabled=True)
        self.clock = FakeClock(wall=time.time(), mono=time.monotonic())
        self.net = FakeNet(self.clock)
        deps = autoloop.Deps(net=self.net, net_ready=lambda: True, load=lambda: 0.5, power=lambda: (True, 100),
                             procs=lambda: 1, disk_free=lambda: 10 ** 12, history_note=lambda *a, **k: None,
                             notify=lambda res: None)
        self.loop = autoloop.Loop(deps=deps, clock=self.clock, rand=lambda: 0.5)
        res = switcher.switch(self.ctx, 'uuid-a', source='auto', kind='forced')      # the move on older numbers
        self.assertTrue(res.ok)
        roster.forget_live()
        a = json.loads(self.rig.vault('uuid-a'))['claudeAiOauth']
        self.a_access, self.a_refresh = a['accessToken'], a['refreshToken']
        self.net.set(self.a_access, (401, {}, {'error': 'made up'}))          # Anthropic turns the read away

    def landing(self):
        settings = autoloop.load_settings()
        ctx = usage.Context(active='uuid-a', order=['uuid-a', 'uuid-b'], lines=policy.lines_for(settings, 'uuid-a'),
                            busy=False, battery_factor=1.0)
        self.loop._read_landing('uuid-a', ctx, types.SimpleNamespace(settings=settings))

    def spent(self):
        return [t for t in self.rig.server.used if 'uuid-a' in t]

    def test_with_the_login_there_it_is_never_renewed(self):
        self.landing()
        self.assertGreaterEqual(self.net.count(self.a_access), 1)      # it was read
        self.assertEqual(self.spent(), [])

    def test_with_the_login_lost_it_is_never_renewed(self):
        self.rig.sec.drop(self.rig.service, self.rig.account)          # a storm of saves took it; the settings name A
        roster.forget_live()
        self.landing()
        self.assertEqual(self.spent(), [])
        res = tokens.refresh(self.ctx, 'uuid-a', budget_s=30, when_due=False)       # what a later read would ask
        self.assertEqual((res.outcome, res.why), ('skipped', 'live_missing'))
        self.assertEqual(self.spent(), [])


if __name__ == '__main__':
    unittest.main()
