import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The page looks after the sign-in watch (server.signin_watch_tick), and looks at Claude Code's sign-in itself while
# the watch and the loop that reads the accounts are both quiet (server.standin_tick). On a fake clock, with a made-up
# launchctl, made-up heartbeats and a made-up engine: no test reaches launchd, the Keychain or Claude Code. The watch's
# module is a made-up one put on the path (this copy may not have the real one), or none.
import contextlib
import importlib.machinery
import json
import os
import random
import shutil
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import core, server  # noqa: E402

UID = os.getuid()
TARGET = 'gui/%d/io.github.amyjainberkeley.switchboard.watch' % UID
WALL = 1.8e9                                # the fake wall clock's start
REAL_HEARTBEAT = server.watch_heartbeat
REAL_ALIVE = server._alive


def watch_module(test, there=True):
    """A made-up watch module on the path for the length of the test (or, with there=False, certainly none)."""
    name = server.WATCH_MODULE
    p = mock.patch.dict(sys.modules)
    p.start()
    test.addCleanup(p.stop)
    if there:
        m = types.ModuleType(name)
        m.__spec__ = importlib.machinery.ModuleSpec(name, None)
        sys.modules[name] = m
    else:
        sys.modules[name] = None             # find_spec raises ValueError for it: no watch


@contextlib.contextmanager
def no_lock():
    yield {'events': []}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-watch-page-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = mock.patch.dict(os.environ, {'HOME': os.path.join(self.tmp, 'home'),
                                           'SWITCHBOARD_HOME': os.path.join(self.tmp, 'data')})
        env.start()
        self.addCleanup(env.stop)
        os.makedirs(os.path.join(self.tmp, 'data'))
        self.agents = os.path.join(self.tmp, 'home', 'Library', 'LaunchAgents')
        os.makedirs(self.agents)
        self.plist = os.path.join(self.agents, server.WATCH_JOB + '.plist')
        self.install_job()
        for d, fresh in ((server.WATCH, {'next': None, 'stale': 0, 'down_since': None, 'calls': [], 'kicked_at': None,
                                         'started_at': None, 'gave_up': False}),
                         (server.STANDIN, {'next': None, 'on': False, 'thread': None, 'started': None,
                                           'late_said': False, 'looked_at': None, 'errors': {}})):
            p = mock.patch.dict(d, fresh, clear=True)
            p.start()
            self.addCleanup(p.stop)
        self.calls, self.answers, self.logs, self.events = [], [], [], []
        self.hb = {}
        self.alive, self.cmd = set(), {}
        for name, value in (('watch_launchctl', self.launchctl), ('watch_heartbeat', lambda: self.hb),
                            ('_alive', lambda pid: pid in self.alive),
                            ('_command_of', lambda pid: self.cmd.get(pid, '')),
                            ('log', self.logs.append), ('locked', no_lock),
                            ('event', lambda st, text, kind='info', **k: self.events.append(text))):
            p = mock.patch.object(server, name, value)
            p.start()
            self.addCleanup(p.stop)
        watch_module(self)

    def install_job(self, there=True):
        if there:
            with open(self.plist, 'w') as f:
                f.write('<plist/>')
        elif os.path.exists(self.plist):
            os.remove(self.plist)

    def launchctl(self, *args):
        self.calls.append(args)
        return self.answers.pop(0) if self.answers else (0, '')

    def beat(self, age, pid=4321, **more):
        self.hb = dict({'pid': pid, 'beat_at': WALL - age}, **more)

    def look(self, at):
        """One look of the page's at fake time `at` (seconds): monotonic and wall clocks move together."""
        return server.signin_watch_tick(now=1000.0 + at, wall=WALL)


class LooksAfterTheWatch(Base):
    def test_a_fresh_beat_is_left_alone(self):
        self.alive.add(4321)                            # a watch that beats is a live process
        self.beat(5)
        for i in range(20):
            self.assertIsNone(self.look(i * 30))
        self.assertEqual(self.calls, [])

    def test_one_stale_look_is_not_enough(self):
        self.beat(300)
        self.look(0)
        self.assertEqual(self.calls, [])

    def test_two_stale_looks_kickstart_it_without_k(self):
        self.hb = {}                                    # no heartbeat file at all counts as stale
        self.look(0)
        self.assertEqual(self.look(30), 'started')
        self.assertEqual(self.calls, [('kickstart', TARGET)])

    def test_looks_come_every_30_s_however_often_the_loop_passes(self):
        self.beat(300)
        for t in (0, 5, 10, 20, 29):
            self.look(t)
        self.assertEqual(self.calls, [])                # still one look: the one at 0
        self.look(30)
        self.assertEqual(self.calls, [('kickstart', TARGET)])

    def test_after_a_kickstart_that_worked_it_waits_for_the_first_beat(self):
        self.alive.add(4321)                            # a watch that beats is a live process
        self.beat(300)
        self.look(0)
        self.look(30)
        self.look(60)                                   # 30 s after it: its first beat may be on its way
        self.assertEqual(len(self.calls), 1)
        self.beat(2)
        self.assertIsNone(self.look(90))
        self.assertEqual(server.WATCH['stale'], 0)
        self.assertIn('the sign-in watch is beating again', self.logs)

    def test_not_loaded_then_bootstrap_with_its_plist(self):
        self.beat(300)
        self.answers = [(113, 'Could not find service "io.github.amyjainberkeley.switchboard.watch" in domain'),
                        (0, '')]
        self.look(0)
        self.assertEqual(self.look(30), 'loaded')
        self.assertEqual(self.calls, [('kickstart', TARGET), ('bootstrap', 'gui/%d' % UID, self.plist)])

    def test_another_failure_is_not_answered_with_bootstrap(self):
        self.beat(300)
        self.answers = [(5, 'Input/output error')]
        self.look(0)
        self.assertEqual(self.look(30), 'failed')
        self.assertEqual(self.calls, [('kickstart', TARGET)])

    def kicked_then_silent(self, pid_alive=True, is_watch=True, kick_rc=0):
        self.beat(300, pid=4321)
        if pid_alive:
            self.alive.add(4321)
        self.cmd[4321] = '/usr/bin/python3 -I -B -c import runpy; run_module("switchboard.accounts.signin_watch") x' \
            if is_watch else '/usr/bin/python3 something else'
        self.answers = [(kick_rc, '')]
        self.look(0)
        self.look(30)                                   # kickstart
        self.look(60)                                   # 30 s on
        self.look(90)                                   # 60 s on: still no beat
        return [c for c in self.calls]

    def test_k_once_a_live_watch_has_not_beaten_for_60_s_after_a_kickstart_that_worked(self):
        self.assertEqual(self.kicked_then_silent(), [('kickstart', TARGET), ('kickstart', '-k', TARGET)])

    def test_never_k_for_a_pid_that_is_not_alive(self):
        self.assertEqual(self.kicked_then_silent(pid_alive=False), [('kickstart', TARGET), ('kickstart', TARGET)])

    def test_never_k_for_a_pid_that_is_not_the_watch(self):
        self.assertEqual(self.kicked_then_silent(is_watch=False), [('kickstart', TARGET), ('kickstart', TARGET)])

    def test_never_k_after_a_kickstart_that_failed(self):
        calls = self.kicked_then_silent(kick_rc=5)
        self.assertNotIn('-k', [a for c in calls for a in c])

    def test_never_k_after_a_bootstrap_only(self):
        self.beat(300, pid=4321)
        self.alive.add(4321)
        self.cmd[4321] = 'python -c run_module("switchboard.accounts.signin_watch")'
        self.answers = [(113, ''), (0, '')]
        for t in range(0, 300, 30):
            self.look(t)
        self.assertEqual(self.calls[:2], [('kickstart', TARGET), ('bootstrap', 'gui/%d' % UID, self.plist)])
        self.assertNotIn('-k', [a for c in self.calls[2:3] for a in c])       # the next try is a plain kickstart

    def test_three_calls_in_ten_minutes_then_one_history_line_and_one_try_every_ten_minutes(self):
        self.beat(300)
        self.answers = [(5, '')] * 50
        times = []
        for t in range(0, 3600, 30):
            before = len(self.calls)
            self.look(t)
            if len(self.calls) > before:
                times.append(t)
        self.assertEqual(times[:3], [30, 60, 90])
        self.assertEqual(self.events, [server.WATCH_GAVE_UP])     # one History line, once
        for t in times:
            self.assertLessEqual(len([u for u in times if t <= u < t + 600]), 3)
        later = times[3:]
        self.assertTrue(later)
        self.assertEqual([b - a for a, b in zip(later, later[1:])], [600] * (len(later) - 1))
        self.assertEqual(server.watch_view(now=1000.0 + 3600, wall=WALL)['line'], server.WATCH_GAVE_UP)

    def test_the_line_once_it_gave_up_says_what_to_do(self):
        self.assertIn('Run the installer again to start it', server.WATCH_GAVE_UP)
        self.assertIn('`switchboard doctor`', server.WATCH_GAVE_UP)

    def test_nothing_while_its_plist_is_absent(self):
        self.install_job(False)
        self.beat(3000)
        for t in range(0, 1200, 30):
            self.assertIsNone(self.look(t))
        self.assertEqual(self.calls, [])

    def test_nothing_when_this_copy_has_no_watch(self):
        watch_module(self, there=False)
        self.assertFalse(server.watch_there())
        self.beat(3000)
        for t in range(0, 1200, 30):
            self.assertIsNone(self.look(t))
        self.assertEqual(self.calls, [])

    def test_a_beat_dated_ahead_counts_as_none(self):
        self.beat(-600)                                 # the clock was set back ten minutes
        self.look(0)
        self.look(30)
        self.assertEqual(self.calls, [('kickstart', TARGET)])

    def test_the_heartbeat_file_itself_is_read(self):
        self.alive.add(4321)                            # a watch that beats is a live process
        with mock.patch.object(server, 'watch_heartbeat', REAL_HEARTBEAT):
            with open(core.data_path(server.WATCH_BEAT), 'w') as f:
                json.dump({'pid': 4321, 'beat_at': WALL - 3}, f)
            self.assertTrue(server.watch_beat(WALL)['fresh'])
            with open(core.data_path(server.WATCH_BEAT), 'w') as f:
                f.write('{"pid": 1, "beat_at": ')     # cut off
            self.assertEqual(server.watch_beat(WALL), {'hb': {}, 'age': None, 'fresh': False, 'gone': False})


class AStoppedWatchIsNotRunning(Base):
    """A beat counts as a running watch's only when it is recent, has no `stopped_at` (a clean stop writes one last
    beat with it) and, when it names a pid, that process is alive. signin_watch_tick, the page state and doctor all read
    server.watch_beat."""

    def test_a_beat_3_s_old_with_stopped_at(self):
        self.alive.add(4321)
        self.beat(3, stopped_at=WALL - 3)
        self.assertFalse(server.watch_beat(WALL)['fresh'])
        self.assertFalse(server.watch_view(now=1000.0, wall=WALL)['running'])
        self.look(0)
        self.assertEqual(self.look(30), 'started')
        self.assertEqual(self.calls, [('kickstart', TARGET)])

    def test_a_beat_3_s_old_whose_pid_is_dead(self):
        self.alive.discard(4321)
        self.beat(3, pid=4321)
        self.assertFalse(server.watch_beat(WALL)['fresh'])
        self.assertFalse(server.watch_view(now=1000.0, wall=WALL)['running'])
        self.look(0)
        self.assertEqual(self.look(30), 'started')

    def test_a_live_one(self):
        self.alive.add(4321)
        self.beat(3, pid=4321)
        self.assertTrue(server.watch_beat(WALL)['fresh'])
        self.assertTrue(server.watch_view(now=1000.0, wall=WALL)['running'])
        for i in range(5):
            self.assertIsNone(self.look(i * 30))
        self.assertEqual(self.calls, [])

    def test_a_stopped_at_that_is_not_a_number_is_no_stop(self):
        self.alive.add(4321)
        self.beat(3, stopped_at='soon')
        self.assertTrue(server.watch_beat(WALL)['fresh'])

    def test_a_pid_that_is_not_a_whole_number_of_a_process_counts_as_dead(self):
        with mock.patch.object(server, '_alive', REAL_ALIVE):
            for pid in ('4321', 4321.0, True, [4321], 0, -1):
                self.beat(3, pid=pid)
                self.assertFalse(server.watch_beat(WALL)['fresh'], repr(pid))
            self.beat(3, pid=os.getpid())                   # a live process, really asked
            self.assertTrue(server.watch_beat(WALL)['fresh'])
            self.hb = {'beat_at': WALL - 3}                 # no pid at all: the age alone
            self.assertTrue(server.watch_beat(WALL)['fresh'])


class TheLineOnThePage(Base):
    def test_nothing_while_all_is_well_and_one_line_after_two_minutes_down(self):
        self.alive.add(4321)                            # a watch that beats is a live process
        self.beat(5)
        self.look(0)
        self.assertIsNone(server.watch_view(now=1000.0, wall=WALL)['line'])
        self.beat(100)
        self.look(30)
        self.assertIsNone(server.watch_view(now=1000.0 + 30, wall=WALL)['line'])
        self.assertIsNone(server.watch_view(now=1000.0 + 150, wall=WALL)['line'])     # down 2 min exactly: not yet
        self.assertEqual(server.watch_view(now=1000.0 + 151, wall=WALL)['line'], server.WATCH_DOWN)
        self.install_job(False)                         # being removed: nothing to say
        self.assertIsNone(server.watch_view(now=1000.0 + 200, wall=WALL)['line'])


class StandsIn(Base):
    def setUp(self):
        super().setUp()
        self.loop_age, self.wanted = 300.0, True
        for target, name, value in ((server, '_loop_beat_age', lambda: self.loop_age),
                                    (server.engine, 'switching_on', lambda: False),
                                    (server.engine, 'loop_wanted', lambda enabled: self.wanted)):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.looks = []

    def engine(self, watch_look=True, put_back_pass=True, look=None):
        for name, want in (('watch_look', watch_look), ('put_back_pass', put_back_pass)):
            if want:
                fn = look or ((lambda hint: self.looks.append(('watch_look', hint))) if name == 'watch_look' else
                              (lambda: self.looks.append(('put_back_pass',))))
                p = mock.patch.object(server.api, name, fn, create=True)
            elif hasattr(server.api, name):
                p = mock.patch.object(server.api, name, None)
            else:
                continue
            p.start()
            self.addCleanup(p.stop)

    def tick(self, at):
        got = server.standin_tick(now=1000.0 + at, wall=WALL)
        t = server.STANDIN['thread']
        if t is not None:
            t.join(5)
        return got

    def test_both_beats_old_the_page_has_the_engine_look(self):
        self.engine()
        self.beat(300)
        self.assertEqual(self.tick(0), 'looked')
        self.assertEqual(self.looks, [('watch_look', {'keychain': False, 'login_file': None, 'settings': False,
                                                      'tick': True})])
        self.assertEqual(self.tick(10), None)           # every 30 s
        self.assertEqual(self.tick(30), 'looked')
        self.assertEqual(len(self.looks), 2)
        self.assertEqual(len([x for x in self.logs if 'the page looks at' in x]), 1)
        self.assertIsNotNone(server.watch_view()['page_looked_at'])
        self.assertTrue(server.watch_view()['page_looking'])

    def test_a_watch_that_says_it_stopped_is_quiet_at_once(self):
        self.engine()
        self.alive.add(4321)
        self.beat(3, stopped_at=WALL - 3)               # its last beat, 3 s ago, says it stopped
        self.assertEqual(self.tick(0), 'looked')
        self.assertEqual(len([x for x in self.logs if 'the sign-in watch has stopped' in x and
                              'the page looks at' in x]), 1, self.logs)

    def test_a_watch_whose_process_is_gone_is_quiet_at_once(self):
        self.engine()
        self.beat(3)                                    # a beat 3 s old from a process that is no longer there
        self.assertEqual(self.tick(0), 'looked')

    def test_a_stopped_watch_and_a_beating_loop_and_it_does_not(self):
        self.engine()
        self.beat(3, stopped_at=WALL - 3)
        self.loop_age = 20.0
        self.assertIsNone(self.tick(0))
        self.assertEqual(self.looks, [])

    def test_one_beat_fresh_and_it_does_not(self):
        self.engine()
        self.alive.add(4321)                            # a watch that beats is a live process
        self.beat(5)
        self.assertIsNone(self.tick(0))
        self.beat(300)
        self.loop_age = 20.0
        self.assertIsNone(self.tick(30))
        self.assertEqual(self.looks, [])

    def test_no_watch_heartbeat_at_all_and_a_quiet_loop(self):
        self.engine()
        self.hb = {}
        self.loop_age = None
        self.assertEqual(self.tick(0), 'looked')

    def test_no_account_kept_and_auto_switch_off_nothing_to_put_back(self):
        self.engine()
        self.beat(300)
        self.wanted = False
        self.assertIsNone(self.tick(0))
        self.wanted = None                              # can't tell: it looks
        self.assertEqual(self.tick(30), 'looked')

    def test_it_hands_back_with_one_line(self):
        self.engine()
        self.beat(300)
        self.tick(0)
        self.tick(30)
        self.alive.add(4321)                            # the watch runs again
        self.beat(3)
        self.tick(60)
        self.tick(90)
        self.assertEqual(len(self.looks), 2)
        self.assertEqual(len([x for x in self.logs if 'stops looking' in x]), 1)
        self.assertFalse(server.STANDIN['on'])

    def test_only_put_back_pass(self):
        self.engine(watch_look=False)
        self.beat(300)
        self.assertEqual(self.tick(0), 'looked')
        self.assertEqual(self.looks, [('put_back_pass',)])

    def test_neither_and_nothing_happens(self):
        self.engine(watch_look=False, put_back_pass=False)
        self.beat(300)
        self.assertIsNone(self.tick(0))
        self.assertIsNone(server.STANDIN['thread'])
        self.assertEqual(self.logs, [])

    def test_a_look_that_hangs_never_stops_the_page_and_no_second_one_starts(self):
        release, entered = threading.Event(), []

        def hang(hint):
            entered.append(1)
            release.wait(30)
        self.engine(look=hang)
        self.beat(300)
        self.addCleanup(release.set)
        self.assertEqual(server.standin_tick(now=1000.0, wall=WALL), 'looked')
        for at in (30, 60, 90):
            self.assertIsNone(server.standin_tick(now=1000.0 + at, wall=WALL))
        self.assertEqual(len([x for x in self.logs if 'has run' in x]), 1)
        release.set()
        server.STANDIN['thread'].join(5)
        self.assertEqual(server.standin_tick(now=1000.0 + 120, wall=WALL), 'looked')
        server.STANDIN['thread'].join(5)
        self.assertEqual(len(entered), 2)

    def test_a_look_that_raises_is_said_by_its_class_name_only(self):
        def boom():
            raise RuntimeError('someone@work.example: cannot read /Users/someone/.claude/.credentials.json')
        server._standin_run(boom)
        self.assertEqual(self.logs, ["the page's own look at Claude Code's sign-in failed (RuntimeError)"])

    def test_a_look_that_raises_is_said_once_in_ten_minutes_and_the_looks_go_on(self):
        def boom(hint):
            raise RuntimeError('the Keychain said no')
        self.engine(look=boom)
        self.beat(300)
        for i in range(1000):
            self.assertEqual(self.tick(i * 30), 'looked')
        said = [x for x in self.logs if 'failed' in x]
        self.assertEqual(len(said), 1)                  # the same error, inside ten minutes of the page's own clock
        self.assertEqual(len(server.STANDIN['errors']), 1)


class InThePagesOwnLoop(unittest.TestCase):
    """server.loop runs both on every pass, each wrapped on its own: one that raises never stops the other, nor the
    pass."""

    def test_every_pass_runs_both_and_one_that_raises_stops_nothing(self):
        from switchboard import engine, keep
        ran, logs = [], []

        def signin_watch_tick(now=None, wall=None):             # (the loop's log names a step by its function's name)
            ran.append('watch')
            raise RuntimeError('someone@work.example: cannot read /Users/someone/.claude/.credentials.json')
        patches = [(server, 'signin_watch_tick', signin_watch_tick),
                   (server, 'standin_tick', lambda now=None, wall=None: ran.append('in')),
                   (server, 'tick', lambda: {'rows': None}), (server, 'keep_tick', lambda looked: None),
                   (server, 'mac_tick', lambda now=None: ran.append('mac')),
                   (server, 'disk_tick', lambda run=None: False),
                   (server, 'load_per_core', lambda: 0.0), (server, 'readings_pass', lambda: None),
                   (server, 'refresh_link_status', lambda *a, **k: None),
                   (server, 'consent_refresh', lambda *a, **k: None),
                   (server, 'decide_tick', lambda *a, **k: None), (server.artifacts, 'published_tick', lambda: None),
                   (server, 'log', logs.append), (keep, 'login_changed', lambda: False),
                   (keep, 'due', lambda now=None: False), (keep, 'wait_for', lambda default, now=None: 0.0),
                   (engine.HOST, 'ensure', lambda now=None: None), (server.WAKE, 'wait', lambda timeout=None: False),
                   (server.claudecode, 'CAP', {'running': True, 'at': 0}),
                   (server.claudecode, 'HEALTH', {'running': True, 'at': 0})]
        for target, name, value in patches:
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        server.loop(ticks=3)
        self.assertEqual(ran, ['mac', 'watch', 'in'] * 3)
        self.assertEqual([x for x in logs if x.startswith('signin_watch_tick failed')],
                         ['signin_watch_tick failed (RuntimeError)'] * 3)      # its class name: never its words
        self.assertFalse([x for x in logs if '@' in x or '/Users/' in x], logs)


class NeverBreaksItsRules(Base):
    """Thousands of made-up steps from fixed seeds: random heartbeats (fresh, stale, dated ahead, missing, damaged),
    random launchctl answers, random gaps between passes, the job file and the watch's module coming and going. After
    every step: never more than WATCH_CALLS launchctl calls in any WATCH_SPAN seconds; `-k` only after a plain
    kickstart that worked at least WATCH_HUNG seconds before with no fresh beat seen since, while the heartbeat's pid is
    alive and is the watch; never a call while the job's plist is absent or this copy has no watch."""

    def test_the_rules_hold_over_many_made_up_runs(self):
        made = []
        for seed in (1, 2, 3, 4, 5, 6):
            with self.subTest(seed=seed):
                made += self.run_one(random.Random(seed), steps=1500)
        kinds = set(args[0] if args[1] != '-k' else '-k' for _, args in made)
        self.assertEqual(kinds, {'kickstart', 'bootstrap', '-k'})     # every kind of call was made and checked
        self.assertGreater(len(made), 300)

    def run_one(self, rnd, steps):
        server.WATCH.update(next=None, stale=0, down_since=None, calls=[], kicked_at=None, started_at=None,
                            gave_up=False)
        clock = {'mono': 1000.0, 'wall': WALL}
        model = {'ok_at': None, 'there': True, 'plist': True}
        made = []                                       # (time, args)

        def there():
            # a look begins: one that finds no watch or no job file forgets every try (as the page does)
            if not model['there'] or not os.path.exists(self.plist):
                model['ok_at'] = None
            return model['there']

        def heartbeat():
            hb = self.hb
            age = server.watch_beat(clock['wall'], hb)
            if age['fresh']:
                model['ok_at'] = None
            return hb

        def launchctl(*args):
            now = clock['mono']
            self.assertTrue(model['plist'] and os.path.exists(self.plist), 'a call with no plist')
            self.assertTrue(model['there'], 'a call with no watch module')
            made.append((now, args))
            self.assertLessEqual(len([t for t, _ in made if 0 <= now - t < server.WATCH_SPAN]), server.WATCH_CALLS)
            if '-k' in args:
                self.assertIsNotNone(model['ok_at'], '-k with no kickstart that worked')
                self.assertGreaterEqual(now - model['ok_at'], server.WATCH_HUNG)
                pid = self.hb.get('pid') if isinstance(self.hb, dict) else None
                self.assertIn(pid, self.alive)
                self.assertIn(server.WATCH_MODULE, self.cmd.get(pid, ''))
            rc = rnd.choice((0, 0, 113, 5, 127, 37))
            said = 'Could not find service' if rc == 113 and rnd.random() < 0.5 else ''
            if args[0] == 'kickstart' and rc == 0:
                model['ok_at'] = now
            elif args[0] == 'bootstrap' or rc != 0:
                model['ok_at'] = None
            return rc, said

        with mock.patch.object(server, 'watch_there', there), \
                mock.patch.object(server, 'watch_heartbeat', heartbeat), \
                mock.patch.object(server, 'watch_launchctl', launchctl):
            for _ in range(steps):
                r = rnd.random()
                if r < 0.03:
                    model['plist'] = not model['plist']
                    self.install_job(model['plist'])
                elif r < 0.05:
                    model['there'] = not model['there']
                kind = rnd.random()
                pid = rnd.choice((4321, 4322, 1, None))
                if kind < 0.25:
                    self.hb = {'pid': pid, 'beat_at': clock['wall'] - rnd.uniform(0, 40)}
                elif kind < 0.7:
                    self.hb = {'pid': pid, 'beat_at': clock['wall'] - rnd.uniform(46, 5000)}
                elif kind < 0.8:
                    self.hb = {'pid': pid, 'beat_at': clock['wall'] + rnd.uniform(6, 4000)}
                elif kind < 0.9:
                    self.hb = {}
                else:
                    self.hb = {'pid': 'x', 'beat_at': 'soon'}
                self.alive = set(p for p in (4321, 4322) if rnd.random() < 0.6)
                self.cmd = {4321: 'python -c run_module("switchboard.accounts.signin_watch") /x/app',
                            4322: 'python -c something else'}
                gap = rnd.choice((rnd.uniform(0, 25), rnd.uniform(25, 70), rnd.uniform(70, 900)))
                clock['mono'] += gap
                clock['wall'] += gap + (rnd.uniform(-300, 3000) if rnd.random() < 0.02 else 0)
                server.signin_watch_tick(now=clock['mono'], wall=clock['wall'])
        return made


if __name__ == '__main__':
    unittest.main()
