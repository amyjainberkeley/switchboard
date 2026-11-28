"""The sign-in watch as a process: one per data folder, a clean stop, --once, the default look, and the files it
leaves (owner-only, kept to a size, with no email, token or path in them).

The processes started here get a throwaway home and data folder and a look that does nothing (CHILD): the engine's
real look reads the Keychain through `security`, and a child process is outside the audit hook of tests/_isolate.py.
No page job file exists, so they never reach launchd.
"""
import _isolate  # noqa: F401 - first: keeps every write in temp folders

import contextlib
import io
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

from switchboard.accounts import autoloop, context, facts, usage
from switchboard.accounts import signin_watch as sw

from fakes_autoswitch import FakeClock
from signin_watch_fakes import Answer, FakeRunner, Sim, SimChanges, T0_MONO

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN = 'sk-ant-oat01-' + 'Zq9' * 12
EMAIL = 'someone@mail.example'
# `python -m switchboard.accounts.signin_watch`, with the look an engine without one gets
CHILD = ('import sys\n'
         'from switchboard.accounts import signin_watch as sw\n'
         'sw.default_look = lambda api_module=None, log=None: ((lambda hint: None), '
         '"this build of the engine has no look")\n'
         'sys.exit(sw.main(sys.argv[1:]))\n')


def read_lines(path):
    with open(path) as f:
        return f.read().splitlines()


class Folders(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='signin-watch-proc-')
        self.home = os.path.join(self.tmp, 'home')
        self.data = os.path.join(self.tmp, 'data')
        os.makedirs(self.home)
        os.makedirs(self.data)
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
                p.wait(10)
            p.stdout.close()
            p.stderr.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def env(self):
        env = dict(os.environ)
        env.update(HOME=self.home, SWITCHBOARD_HOME=self.data, CLAUDE_CONFIG_DIR=os.path.join(self.home, '.claude'),
                   SWITCHBOARD_PORT='7949', PYTHONDONTWRITEBYTECODE='1')
        return env

    def start(self, *args):
        p = subprocess.Popen([sys.executable, '-B', '-c', CHILD] + list(args),
                             cwd=ROOT, env=self.env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
        self.procs.append(p)
        return p

    def run_child(self, *args, timeout=120):
        p = self.start(*args)
        out, err = p.communicate(timeout=timeout)
        return p.returncode, out.decode('utf-8', 'replace'), err.decode('utf-8', 'replace')

    def path(self, name):
        return os.path.join(self.data, name)


class ProcessTest(Folders):
    def test_a_second_start_says_so_and_exits_5(self):
        lease = autoloop.Lease(self.path(sw.LEASE))
        self.assertTrue(lease.acquire(usage.Clock()))
        try:
            rc, out, err = self.run_child()
            self.assertEqual(rc, sw.EXIT_BUSY, err)
            self.assertIn('The sign-in watch already runs (process %d).' % os.getpid(), err)
            rc, out, err = self.run_child('--once')
            self.assertEqual(rc, sw.EXIT_BUSY, '--once never looks beside a running watch')
        finally:
            lease.release()

    def test_sigterm_ends_it_within_two_seconds_and_lets_go_of_the_lease(self):
        p = self.start()
        beat = self.path(sw.HEARTBEAT)
        end = time.monotonic() + 90
        while time.monotonic() < end and p.poll() is None and not usage.read_json(beat):
            time.sleep(0.1)
        hb = usage.read_json(beat)
        self.assertIsNotNone(hb, 'the watch started and beat')
        self.assertEqual(hb['pid'], p.pid)
        self.assertTrue(autoloop.lease_owner(self.path(sw.LEASE))['held'])
        time.sleep(0.5)
        t0 = time.monotonic()
        p.send_signal(signal.SIGTERM)
        rc = p.wait(30)
        took = time.monotonic() - t0
        self.assertEqual(rc, 0)
        self.assertLess(took, 2.0)
        self.assertFalse(autoloop.lease_owner(self.path(sw.LEASE))['held'])
        hb = usage.read_json(beat)
        self.assertIsNotNone(hb.get('stopped_at'))
        self.assertEqual(hb['problem'], 'this build of the engine has no look')
        kinds = [json.loads(x)['kind'] for x in read_lines(self.path(sw.RECORD))]
        self.assertEqual((kinds[0], kinds[-1]), ('start', 'stop'))
        for name in (sw.HEARTBEAT, sw.RECORD, sw.LOG, sw.LEASE):
            self.assertEqual(stat.S_IMODE(os.stat(self.path(name)).st_mode) & 0o077, 0, name)
        print('\nSIGTERM to exit: %.2f s' % took)

    def test_once_with_nothing_to_do_exits_0(self):
        rc, out, err = self.run_child('--once')
        self.assertEqual(rc, sw.EXIT_NOTHING, err)
        self.assertFalse(os.path.exists(self.path(sw.HEARTBEAT)), '--once is not a running watch')
        self.assertFalse(autoloop.lease_owner(self.path(sw.LEASE))['held'])

    def test_status_prints_plain_lines(self):
        rc, out, err = self.run_child('--status')
        self.assertEqual((rc, out), (0, 'The sign-in watch is not running.\n'))


class FakeStop(object):
    """The Event a second copy waits on, on the fake clock: wait() moves the clock by the time asked, or, with
    `term_after`, calls the SIGTERM handler the copy set after that many seconds (as launchd's bootout would)."""

    def __init__(self, clock, handlers, term_after=None):
        self.clock, self.handlers, self.term_after = clock, handlers, term_after
        self.flag = False
        self.waits = []

    def set(self):
        self.flag = True

    def is_set(self):
        return self.flag

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self.term_after is not None and self.term_after < timeout:
            self.clock.advance(self.term_after)
            self.handlers[signal.SIGTERM](signal.SIGTERM, None)
        else:
            self.clock.advance(timeout)
        return self.flag


class SecondCopyTest(Folders):
    """A second copy beside the watch that holds the lease. launchd starts the job again ThrottleInterval (10 s) after
    each exit (KeepAlive), so a copy that exited at once would start every 10 s for as long as the other one runs: it
    says so once, waits BUSY_WAIT_S (SIGTERM ends the wait at once: launchd kills 20 s after it), then exits 5."""

    def setUp(self):
        super().setUp()
        p = mock.patch.dict(os.environ, {'HOME': self.home, 'SWITCHBOARD_HOME': self.data,
                                         'CLAUDE_CONFIG_DIR': os.path.join(self.home, '.claude')})
        p.start()
        self.addCleanup(p.stop)
        self.lease = autoloop.Lease(self.path(sw.LEASE))
        self.assertTrue(self.lease.acquire(usage.Clock()))
        self.addCleanup(self.lease.release)
        self.handlers = {}
        p = mock.patch.object(sw.signal, 'signal', lambda sig, fn: self.handlers.__setitem__(sig, fn))
        p.start()
        self.addCleanup(p.stop)
        self.clock = FakeClock()

    def main(self, *args, term_after=None):
        stop = FakeStop(self.clock, self.handlers, term_after)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = sw.main(list(args), stop=stop)
        return rc, err.getvalue(), stop

    def test_it_says_so_once_waits_15_s_and_exits_5(self):
        t0 = self.clock.mono()
        rc, err, stop = self.main()
        self.assertEqual(rc, sw.EXIT_BUSY)
        self.assertEqual(err, 'The sign-in watch already runs (process %d).\n' % os.getpid())
        self.assertEqual(stop.waits, [sw.BUSY_WAIT_S])
        self.assertEqual(sw.BUSY_WAIT_S, 15.0)
        self.assertEqual(self.clock.mono() - t0, 15.0)
        self.assertEqual(set(self.handlers), {signal.SIGTERM, signal.SIGINT})
        self.assertFalse(os.path.exists(self.path(sw.HEARTBEAT)), 'it never ran as a watch')

    def test_sigterm_during_the_wait_ends_it_at_once(self):
        t0 = self.clock.mono()
        rc, err, stop = self.main(term_after=3.0)
        self.assertEqual(rc, sw.EXIT_BUSY)
        self.assertEqual(self.clock.mono() - t0, 3.0)
        self.assertTrue(stop.is_set())
        self.assertEqual(len(err.splitlines()), 1)

    def test_once_never_waits(self):
        rc, err, stop = self.main('--once')
        self.assertEqual(rc, sw.EXIT_BUSY)
        self.assertEqual(stop.waits, [])
        self.assertEqual(len(err.splitlines()), 1)

    def test_a_real_sigterm_ends_a_waiting_second_copy_within_two_seconds(self):
        p = self.start()
        line = p.stderr.readline().decode('utf-8', 'replace')
        self.assertEqual(line, 'The sign-in watch already runs (process %d).\n' % os.getpid())
        time.sleep(0.5)
        self.assertIsNone(p.poll(), 'it waits')
        t0 = time.monotonic()
        p.send_signal(signal.SIGTERM)
        rc = p.wait(30)
        took = time.monotonic() - t0
        self.assertEqual(rc, sw.EXIT_BUSY)
        self.assertLess(took, 2.0)
        print('\nSIGTERM to exit, second copy: %.2f s' % took)


class DefaultLookTest(unittest.TestCase):
    def test_the_engines_watch_look_comes_first(self):
        seen = []
        api = types.SimpleNamespace(watch_look=lambda hint: seen.append(('watch_look', hint)) or 'put_back',
                                    put_back_pass=lambda: seen.append(('put_back_pass',)))
        look, problem = sw.default_look(api)
        self.assertEqual(look({'tick': True}), 'put_back')
        self.assertEqual((seen, problem), ([('watch_look', {'tick': True})], None))

    def test_then_put_back_pass_with_no_hint(self):
        seen = []
        api = types.SimpleNamespace(put_back_pass=lambda: seen.append('called') or None)
        look, problem = sw.default_look(api)
        self.assertIsNone(look({'keychain': True}))
        self.assertEqual((seen, problem), (['called'], None))

    def test_then_a_look_that_does_nothing_said_once(self):
        logs = []
        look, problem = sw.default_look(types.SimpleNamespace(watch_look='not callable'), logs.append)
        self.assertIsNone(look({'tick': True}))
        self.assertEqual(problem, 'this build of the engine has no look')
        self.assertEqual(len(logs), 1)

    def test_this_base_has_neither(self):
        from switchboard.accounts import api
        look, problem = sw.default_look(api) if not (hasattr(api, 'watch_look') or hasattr(api, 'put_back_pass')) \
            else (None, 'skip')
        if problem == 'skip':
            self.skipTest('this tree has an engine look')
        self.assertEqual(problem, 'this build of the engine has no look')


class OnceTest(unittest.TestCase):
    def watch(self, answer, duration=0.0):
        sim = Sim()
        lines = []

        def look(hint):
            if isinstance(answer, Exception):
                raise answer
            return answer
        w = sw.Watch(SimChanges(sim), look, clock=sim.clock, record=sw.Record(clock=sim.clock, sink=lines.append),
                     spawn=FakeRunner(sim, lambda: duration))
        return w, lines

    def test_exit_codes(self):
        w, lines = self.watch(None)
        self.assertEqual(w.once(), sw.EXIT_NOTHING)
        w, lines = self.watch(Answer('nothing'))
        self.assertEqual(w.once(), sw.EXIT_NOTHING)
        self.assertEqual(lines, [])
        w, lines = self.watch(Answer('put_back', seen={'item': 'absent', 'email': EMAIL}))
        self.assertEqual(w.once(), sw.EXIT_DID)
        self.assertIn('"action":"put_back"', lines[0])
        self.assertNotIn(EMAIL, lines[0])
        w, lines = self.watch(KeyError(TOKEN))
        self.assertEqual(w.once(), sw.EXIT_ERROR)
        self.assertNotIn(TOKEN, ''.join(lines))

    def test_a_look_that_never_ends(self):
        with mock.patch.object(sw, 'LOOK_DEADLINE_S', 0.3):
            w, lines = self.watch(None, duration=10 ** 9)
            self.assertEqual(w.once(), sw.EXIT_ERROR)


class FilesTest(Folders):
    def test_build_takes_the_engines_context(self):
        env = {'CLAUDE_CONFIG_DIR': os.path.join(self.home, 'cc')}
        ctx = context.Context(env=env, home=self.home, data_dir=self.data, keychain=None, http=None)
        launched = []
        w = sw.build(ctx, look=lambda hint: None, launch=lambda *a: launched.append(a) or 0, port=7949)
        try:
            self.assertEqual(w.changes.keychain_dir, os.path.join(self.home, 'Library', 'Keychains'))
            self.assertEqual(w.changes.login_file, facts.credentials_file(env, self.home))
            self.assertEqual(w.changes.settings_file, facts.global_config(env, self.home))
            self.assertEqual(w.page.plist, os.path.join(self.home, 'Library', 'LaunchAgents',
                                                        'io.github.amyjainberkeley.switchboard.plist'))
            self.assertEqual(w.page.port, 7949)
            self.assertEqual(w.beat_path, self.path(sw.HEARTBEAT))
            self.assertIs(w.page.journal(), False, 'no switch part-way in a fresh data folder')
            with open(self.path('switch-journal.json'), 'w') as f:
                f.write('{"phase": "writing"}')
            self.assertIs(w.page.journal(), True, 'a switch part-way')
            with open(self.path('switch-journal.json'), 'w') as f:
                f.write('not json')
            self.assertIs(w.page.journal(), True, 'a journal that cannot be read: maybe part-way')
        finally:
            w.changes.close()
        self.assertEqual(launched, [])

    def test_no_secret_reaches_any_file_even_through_the_paths(self):
        home = os.path.join(self.tmp, EMAIL, TOKEN, 'Secret Chat Title')
        os.makedirs(os.path.join(home, 'Library', 'Keychains'))
        with open(os.path.join(home, 'Library', 'Keychains', 'login.keychain-db'), 'wb') as f:
            f.write(b'k' * 64)
        env = {'CLAUDE_CONFIG_DIR': os.path.join(home, '.claude-' + EMAIL)}
        login = facts.credentials_file(env, home)
        ctx = context.Context(env=env, home=home, data_dir=self.data, keychain=None, http=None)
        answers = iter([Answer('put_back', seen={'email': EMAIL, 'token': TOKEN, EMAIL: 1, 'path': home,
                                                 'deep': {'x': TOKEN}, 'n': 2}),
                        {'action': TOKEN, 'watch': False, 'seen': {'who': EMAIL}},
                        Answer(EMAIL, seen={'title': 'Secret Chat Title'})] + [None] * 50)
        os.makedirs(os.path.dirname(login))
        w = sw.build(ctx, look=lambda hint: next(answers), launch=lambda *a: 0, port=7949)
        try:
            with open(login, 'w') as f:
                f.write(json.dumps({'claudeAiOauth': {'accessToken': TOKEN, 'email': EMAIL}}))
            w.run(until=usage.Clock().mono() + 4.0)
            w.look = lambda hint: Answer(EMAIL, seen={'title': 'Secret Chat Title', 'home': home})
            self.assertEqual(w.once(), sw.EXIT_DID)
        finally:
            w.changes.close()
        text = ''
        for name in os.listdir(self.data):
            with open(self.path(name), 'rb') as f:
                text += f.read().decode('utf-8', 'replace')
        self.assertIn('"login_file":"appeared"', text)
        self.assertIn('"action":"put_back"', text)
        for secret in (EMAIL, TOKEN, 'Secret Chat Title', 'claudeAiOauth', self.tmp):
            self.assertNotIn(secret, text)

    def test_the_record_is_owner_only_and_keeps_three_older_files(self):
        clock = FakeClock()
        old = os.umask(0)
        try:
            rec = sw.Record(self.path(sw.RECORD), clock, max_bytes=2000)
            for i in range(400):
                rec.add('change', hint=sw.no_hint(), n=i)
        finally:
            os.umask(old)
        names = sorted(x for x in os.listdir(self.data) if x.startswith(sw.RECORD))
        self.assertEqual(names, [sw.RECORD, sw.RECORD + '.1', sw.RECORD + '.2', sw.RECORD + '.3'])
        for name in names:
            self.assertEqual(stat.S_IMODE(os.stat(self.path(name)).st_mode), 0o600, name)
            for line in read_lines(self.path(name)):
                self.assertEqual(json.loads(line)['kind'], 'change')
        last = [json.loads(x) for x in read_lines(self.path(sw.RECORD))][-1]
        self.assertEqual(last['n'], 399)

    def test_clean_keeps_numbers_words_and_fingerprints_only(self):
        self.assertEqual(sw.clean({'a': 1, 'b': 2.5, 'c': True, 'd': None, 'e': 'absent', 'f': '0123456789abcdef',
                                   'g': '0123456789ABCDEF', 'h': 'person', 'i': float('inf'), 'j': [1, 2],
                                   'k': {'l': {'m': {'n': 1}}}, 'Bad': 1, 'with space': 1, 'x1': 1}),
                         {'a': 1, 'b': 2.5, 'c': True, 'd': None, 'e': 'absent', 'f': '0123456789abcdef', 'i': None,
                          'k': {}})

    def test_status_lines_from_a_heartbeat(self):
        clock = FakeClock()
        usage.write_atomic(self.path(sw.HEARTBEAT), {
            'pid': 77, 'beat_at': clock.wall() - 4, 'mode': 'events', 'looks': 12, 'last_look_at': clock.wall() - 60,
            'last_change_at': clock.wall() - 61, 'last_action': 'put_back', 'last_action_at': clock.wall() - 60,
            'problem': None})
        lines = sw.status_lines(self.data, clock)
        self.assertEqual(lines[0], 'The sign-in watch is not running.')
        self.assertTrue(lines[1].startswith('Last beat: ') and lines[1].endswith(', noticing by events.'), lines)
        self.assertTrue(lines[2].startswith('Looks: 12, the last at '), lines)
        self.assertTrue(any(x.startswith('Last action: put back at ') for x in lines), lines)


class StopTest(unittest.TestCase):
    def test_a_stop_waits_for_the_look_under_way(self):
        sim = Sim()
        lines = []
        state = {}

        class Slow(object):
            def __init__(self, fn):
                state['fn'] = fn
                self.t0 = time.monotonic()

            def done(self):
                return time.monotonic() - self.t0 > 0.5

            def outcome(self):
                return Answer('put_back'), None
        w = sw.Watch(SimChanges(sim), lambda hint: None, clock=sim.clock,
                     record=sw.Record(clock=sim.clock, sink=lines.append), spawn=Slow)
        w._start(sim.clock.mono())
        w.stop.set()
        t0 = time.monotonic()
        w.run()
        self.assertGreaterEqual(time.monotonic() - t0, 0.4)
        self.assertEqual([json.loads(x)['kind'] for x in lines], ['start', 'look', 'stop'])
        self.assertEqual(T0_MONO, sim.clock.mono())


if __name__ == '__main__':
    unittest.main()
