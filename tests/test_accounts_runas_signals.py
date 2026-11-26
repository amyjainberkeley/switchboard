"""`switchboard run` ended by SIGTERM or SIGHUP (a closed Terminal window) still removes its private Keychain item and
folder: the signal goes on to Claude Code, and once it has ended the run tidies up as after a Ctrl-C.

The run happens in a child process (a signal to this test process would end the test run), on a Keychain kept in a
file both processes share, with the stand-in `claude` of the existing run tests."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import os
import signal
import subprocess
import sys
import time
import unittest

from test_accounts_runas import RunCase
from switchboard.accounts import vault

TESTS = os.path.dirname(os.path.abspath(__file__))

CHILD = r'''
import json, sys
sys.path.insert(0, %(tests)r)
sys.path.insert(0, %(root)r)
from fakes_accounts import FileKeychain
from switchboard.accounts import context, net, runas


class NoNetwork:
    def resolve(self, host, deadline_s):
        return False

    def request(self, *a, **kw):
        raise net.NetError('dns', False, 'no network in this child')


spec = json.loads(sys.argv[1])
ctx = context.Context(env=spec['env'], home=spec['home'], data_dir=spec['data'],
                      keychain=FileKeychain(spec['keychain']),
                      http=NoNetwork(), events=lambda *a: None, log=lambda text: None, disk_free=lambda p: 10 ** 12,
                      proc_start=lambda pid: 'x', pid_alive=lambda pid: True, claude=spec['claude'])
code = runas.run_as(ctx, 'uuid-a', ['-p', 'hi'], env=spec['run_env'], timeout=120, bare=True)
print(json.dumps({'code': code}))
'''


class ARunEndedBySignalTidiesUp(RunCase):
    def setUp(self):
        super().setUp()
        self.release = os.path.join(self.rig.root, 'release')
        self.spec(wait=self.release)                    # the stand-in claude runs until this file exists
        self.script = os.path.join(self.rig.root, 'run-child.py')
        with open(self.script, 'w') as f:
            f.write(CHILD % {'tests': TESTS, 'root': os.path.dirname(TESTS)})

    def tearDown(self):
        self.let_go()
        super().tearDown()

    def let_go(self):
        try:
            with open(self.release, 'w'):
                pass
        except OSError:
            pass

    def run_items(self):
        return [k for k in self.rig.kc.items() if k[0].startswith('Claude Code-credentials-')]

    def run_folders(self):
        run = os.path.join(self.rig.data, 'run')
        return os.listdir(run) if os.path.isdir(run) else []

    def end_with(self, signum):
        spec = {'env': self.rig.env, 'home': self.rig.home, 'data': self.rig.data, 'keychain': self.rig.kc.path,
                'claude': self.claude, 'run_env': self.env()}
        proc = subprocess.Popen([sys.executable, self.script, json.dumps(spec)], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, start_new_session=True)
        try:
            end = time.time() + 180                     # a loaded Mac at background priority starts Pythons slowly
            seen = []
            while time.time() < end and proc.poll() is None:
                claim = vault.load(self.ctx)['claims'].get('uuid-a') or {}
                state = (sorted(claim), self.run_items())
                if not seen or seen[-1] != state:
                    seen.append(state)
                if claim.get('child') and self.run_items():
                    break
                time.sleep(0.05)
            else:
                if proc.poll() is None:
                    proc.kill()
                out, err = proc.communicate()
                self.fail('the run never started its claude: seen %r, out %r, err %s'
                          % (seen, out[-500:], err.decode('utf-8', 'replace')[-2000:]))
            self.assertEqual(len(self.run_items()), 1)
            proc.send_signal(signum)
            proc.wait(timeout=60)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        return proc

    def check_tidy(self):
        self.assertEqual(self.run_items(), [])
        self.assertEqual(self.run_folders(), [])
        self.assertEqual(vault.load(self.ctx)['claims'], {})
        self.assertEqual(self.rig.live_text(), self.b_text)            # the live login never moved

    def test_sigterm(self):
        self.end_with(signal.SIGTERM)
        self.check_tidy()

    def test_sighup(self):
        self.end_with(signal.SIGHUP)
        self.check_tidy()


if __name__ == '__main__':
    unittest.main()
