import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# A process that is told to stop, or exits, lets go of every lock of Claude Code's it holds (each only while it is
# still the folder it made) and ends every `security` helper it still has running, so neither outlives it: a lock left
# behind blocks Claude Code's saves until its 15 s stale time, and a helper left behind may land a write later. The
# processes here are children given a throwaway home and data folder and the stand-in tool (FakeSecurity) only; they
# inherit this run's mark, so the Keychain helper could not start the real tool in them either.
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from fakes_accounts import FakeSecurity, Rig
from switchboard.accounts import ccfiles, facts, live

OLD = '{"claudeAiOauth":{"accessToken":"sk-ant-oat01-old","refreshToken":"sk-ant-ort01-old"}}'
NEW = '{"claudeAiOauth":{"accessToken":"sk-ant-oat01-new","refreshToken":"sk-ant-ort01-new"}}'

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# spec: {'home', 'tool', 'mode'}. Prints one JSON line once it is ready, then waits for SIGTERM (the stop handlers of
# the sign-in watch and the loop set an event and let main return), or exits by itself ('exit' modes).
CHILD = r'''
import json, os, signal, sys, threading, time
spec = json.loads(sys.argv[1])
from switchboard.accounts import ccfiles, keychain
env, home, mode = {'USER': 'tester'}, spec['home'], spec['mode']
keychain.DIALOG_UP = lambda: bool(spec.get('dialog'))      # is macOS showing a Keychain dialog: the test says
stop, ready = threading.Event(), threading.Event()
got = {}


def say(**facts_):
    print(json.dumps(facts_), flush=True)


def hold_the_save_lock():
    with ccfiles.storage_lock(env, home) as lock:
        got['ident'] = list(lock.record()['ident'][:2])
        ready.set()
        threading.Event().wait(600)                 # a look that never ends


def read_through_a_slow_tool():
    kc = keychain.Keychain(tool=spec['tool'], read_deadline=600.0)
    ready.set()
    kc.read('Made-up item', 'tester', fresh=True, interactive=True)


if mode in ('lock', 'lock-own-stop'):
    threading.Thread(target=hold_the_save_lock, daemon=True).start()
elif mode in ('helper-exit', 'helper'):
    threading.Thread(target=read_through_a_slow_tool, daemon=True).start()
else:
    ready.set()
ready.wait(60)
if mode == 'lock-own-stop':
    # a stop path that calls the function itself and leaves without the interpreter's exit handlers
    def own_stop(*_):
        ccfiles.release_all()
        os._exit(0)
    signal.signal(signal.SIGTERM, own_stop)
else:
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
if mode == 'after':
    ccfiles.release_all()
    out = {}
    try:
        with ccfiles.storage_lock(env, home, budget_s=1.0):
            out['lock'] = 'taken'
    except ccfiles.LockBusy:
        out['lock'] = 'refused'
    r = keychain.Keychain(tool=spec['tool'], read_deadline=60.0).read('Made-up item', 'tester', fresh=True,
                                                                       interactive=True)
    out['read'] = [r.kind, r.why]
    say(**out)
    sys.exit(0)
if mode.startswith('helper'):
    for _ in range(600):                            # until the stand-in has started (its own log says so)
        if os.path.exists(spec['calls']) and os.path.getsize(spec['calls']):
            break
        time.sleep(0.1)
say(pid=os.getpid(), **got)
if mode == 'helper-exit':
    sys.exit(0)
while not stop.wait(0.1):
    pass
sys.exit(0)
'''


class Children(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='let-go-')
        self.home = os.path.join(self.tmp, 'home')
        self.data = os.path.join(self.tmp, 'data')
        os.makedirs(os.path.join(self.home, '.claude'))
        os.makedirs(self.data)
        self.sec = FakeSecurity(self.tmp)
        self.lock = facts.storage_lock({'USER': 'tester'}, self.home)
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
                p.wait(10)
            p.stdout.close()
            p.stderr.close()
        for c in self.sec.calls():                          # never leave a stand-in running (only ours, by its command)
            ps = subprocess.run(['/bin/ps', '-o', 'command=', '-p', str(c['pid'])], capture_output=True)
            if self.sec.tool in ps.stdout.decode('utf-8', 'replace'):
                try:
                    os.kill(c['pid'], signal.SIGKILL)
                except OSError:
                    pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start(self, mode, **more):
        env = dict(os.environ)
        env.update(HOME=self.home, SWITCHBOARD_HOME=self.data, CLAUDE_CONFIG_DIR=os.path.join(self.home, '.claude'),
                   PYTHONDONTWRITEBYTECODE='1')
        spec = {'home': self.home, 'tool': self.sec.tool, 'mode': mode,
                'calls': os.path.join(self.sec.state, 'calls.jsonl')}
        spec.update(more)
        p = subprocess.Popen([sys.executable, '-B', '-c', CHILD, json.dumps(spec)], cwd=ROOT, env=env,
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.procs.append(p)
        line = p.stdout.readline()
        self.assertTrue(line, p.stderr.read().decode('utf-8', 'replace')[-2000:] if p.poll() is not None else '')
        return p, json.loads(line)

    def gone_within(self, check, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if check():
                return True
            time.sleep(0.02)
        return check()

    def test_a_stop_by_sigterm_lets_go_of_the_save_lock_within_a_second(self):
        p, got = self.start('lock')
        self.assertTrue(os.path.isdir(self.lock))
        p.send_signal(signal.SIGTERM)
        self.assertTrue(self.gone_within(lambda: not os.path.exists(self.lock), 1.0), 'the save lock was left behind')
        self.assertEqual(p.wait(30), 0)

    def test_a_stop_path_that_calls_it_itself_lets_go_too(self):
        p, got = self.start('lock-own-stop')
        p.send_signal(signal.SIGTERM)
        self.assertTrue(self.gone_within(lambda: not os.path.exists(self.lock), 1.0), 'the save lock was left behind')
        self.assertEqual(p.wait(30), 0)

    def test_a_lock_another_process_took_over_meanwhile_is_never_removed(self):
        p, got = self.start('lock')
        os.rename(self.lock, self.lock + '.moved-aside')    # what a takeover of a stale lock does, then its own lock
        os.mkdir(self.lock)
        theirs = os.lstat(self.lock).st_ino
        p.send_signal(signal.SIGTERM)
        self.assertEqual(p.wait(30), 0)
        self.assertTrue(os.path.isdir(self.lock))
        self.assertEqual(os.lstat(self.lock).st_ino, theirs)
        os.rmdir(self.lock)
        os.rmdir(self.lock + '.moved-aside')

    def test_a_process_that_exits_ends_the_security_helper_it_still_has_running(self):
        self.sec.rule(verb='find', delay=120.0, times=1)
        p, got = self.start('helper-exit')
        helper = self.sec.calls('find')[0]['pid']
        self.assertEqual(p.wait(30), 0)
        self.assertTrue(self.gone_within(lambda: not self.alive(helper), 2.0), 'the helper outlived its process')

    def test_a_stop_by_sigterm_ends_it_too(self):
        self.sec.rule(verb='find', delay=120.0, times=1)
        p, got = self.start('helper')
        helper = self.sec.calls('find')[0]['pid']
        p.send_signal(signal.SIGTERM)
        self.assertEqual(p.wait(30), 0)
        self.assertTrue(self.gone_within(lambda: not self.alive(helper), 2.0), 'the helper outlived its process')

    def test_but_no_helper_is_ended_while_a_keychain_dialog_is_showing(self):
        # one of them may be drawing it, and ending it tears the dialog down: it is left to finish by itself
        self.sec.rule(verb='find', delay=120.0, times=1)
        p, got = self.start('helper-exit', dialog=True)
        helper = self.sec.calls('find')[0]['pid']
        self.assertEqual(p.wait(30), 0)
        time.sleep(1.5)                                     # past the second a helper gets once it is told to end
        self.assertTrue(self.alive(helper), 'the helper was ended while a dialog was showing')

    def test_the_dialog_check_on_the_way_out_is_the_one_the_calls_use(self):
        from switchboard.accounts import keychain
        self.assertIs(keychain.DIALOG_UP, keychain.security_agent_up)

    def test_after_it_has_run_the_process_takes_no_lock_but_other_keychain_calls_still_run(self):
        # a renewal's answer that arrives on the way out must still be saved in its account's slot
        self.sec.put('Made-up item', 'tester', 'made-up value')
        p, got = self.start('after')
        self.assertEqual(got, {'lock': 'refused', 'read': ['present', None]})
        self.assertEqual(len(self.sec.calls('find')), 1)
        self.assertFalse(os.path.exists(self.lock))
        self.assertEqual(p.wait(30), 0)

    def alive(self, pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


class AWriteNeedsTheLockItWasGiven(unittest.TestCase):
    """A lock let go of by release_all, or taken over as stale, while a thread still means to write under it."""

    def setUp(self):
        self.rig = Rig()
        self.ctx = self.rig.ctx
        self.rig.set_live(OLD)

    def tearDown(self):
        self.rig.close()

    def test_a_lock_that_was_let_go_is_refused_and_nothing_is_written(self):
        with live.storage_lock(self.ctx) as held:
            held.release()                                  # as release_all does from another thread
            with self.assertRaises(ccfiles.LockBusy):
                live.write_live(self.ctx, NEW, held=held)
        self.assertEqual(self.rig.live_text(), OLD)
        self.assertEqual(self.rig.kc.count('write'), 0)

    def test_a_lock_taken_over_meanwhile_is_refused_too(self):
        path = facts.storage_lock(self.rig.env, self.rig.home)
        with live.storage_lock(self.ctx) as held:
            os.rename(path, path + '.moved-aside')
            os.mkdir(path)
            try:
                with self.assertRaises(ccfiles.LockBusy):
                    live.write_live(self.ctx, NEW, held=held)
            finally:
                os.rmdir(path)
                os.rmdir(path + '.moved-aside')
        self.assertEqual(self.rig.live_text(), OLD)
        self.assertEqual(self.rig.kc.count('write'), 0)


if __name__ == '__main__':
    unittest.main()
