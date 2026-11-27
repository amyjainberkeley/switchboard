"""`uninstall.sh --all` with a program that can't start still takes every Keychain item of Switchboard's own out.

When no program runs `tidy --all` (its Python is gone: 126 or 127; or its folder is), the uninstaller empties
Switchboard's services itself, in plain shell, one item at a time. That loop named the three services of saved logins
and the gateway's keys, but not the two the account engine keeps one-year tokens and API keys under
(switchboard/accounts/bearer.py SERVICES), so a token or a key stayed in the Keychain after "remove everything".
Homebrew's `--zap` runs the same `uninstall.sh --all`.

The whole script runs in a throwaway home, as in tests/test_uninstall_takes_the_notes.py: the home is a new temp
folder (so launchd and macOS's list of apps are left alone, and --no-agent is given as well), SWITCHBOARD_APPLICATIONS
points into it, and the script is a copy whose Keychain tool is a stand-in holding made-up items in a JSON file.
"""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from switchboard import keys
from switchboard.accounts import bearer, vault

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, 'uninstall.sh')
KEYCHAIN_LINE = 'SECURITY=/usr/bin/security'
# Every service the program keeps an item of Switchboard's own under, read from the code that writes them.
OURS = (vault.SERVICE_CURRENT, vault.SERVICE_PREVIOUS, vault.SERVICE_RECOVERY, keys.SERVICE) + \
    tuple(bearer.SERVICES.values())
NOT_OURS = [['Claude Code-credentials', 'tester'], ['Some other app', 'someone']]

TOOL = r'''#!%(python)s -IB
"""A stand-in for /usr/bin/security: delete-generic-password -s SERVICE takes out one item of that service, or exits
44 when there is none. Every call is written down."""
import json
import sys

STORE, CALLS = %(store)r, %(calls)r
args = sys.argv[1:]
with open(CALLS, 'a') as f:
    f.write(' '.join(args) + '\n')
with open(STORE) as f:
    items = json.load(f)
if len(args) != 3 or args[:2] != ['delete-generic-password', '-s']:
    sys.exit(1)
for i, (service, account) in enumerate(items):
    if service == args[2]:
        del items[i]
        with open(STORE, 'w') as f:
            json.dump(items, f)
        sys.exit(0)
sys.exit(44)
'''


class TheShellLoop(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix='sb-uninstall-sweep-'))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.home = os.path.join(self.root, 'home')
        self.data = os.path.join(self.home, '.switchboard')
        self.store = os.path.join(self.root, 'store.json')
        self.calls = os.path.join(self.root, 'calls.log')
        os.makedirs(os.path.join(self.data, 'bin'))
        os.makedirs(os.path.join(self.data, 'app', 'switchboard'))
        os.makedirs(os.path.join(self.root, 'Applications'))
        with open(os.path.join(self.data, 'settings.json'), 'w') as f:
            f.write('{"port": 7981}\n')
        with open(os.path.join(self.data, 'accounts.json'), 'w') as f:
            json.dump({'schema': 1, 'accounts': {'acct-a': {'email': 'maya@northwind.example'}}}, f)
        program = os.path.join(self.data, 'bin', 'switchboard')
        with open(program, 'w') as f:                    # a program whose Python is gone
            f.write('#!/bin/sh\nexit 127\n')
        os.chmod(program, 0o755)
        tool = os.path.join(self.root, 'security')
        with open(tool, 'w') as f:
            f.write(TOOL % {'python': sys.executable, 'store': self.store, 'calls': self.calls})
        os.chmod(tool, 0o755)
        with open(SCRIPT, encoding='utf-8') as f:
            text = f.read()
        if text.count(KEYCHAIN_LINE) != 1 or text.count('/usr/bin/security') != 1:     # (never an assert: -O drops those)
            raise RuntimeError('uninstall.sh names the Keychain tool in another way now: this test must not run it')
        self.script = os.path.join(self.root, 'uninstall.sh')
        with open(self.script, 'w', encoding='utf-8') as f:
            f.write(text.replace(KEYCHAIN_LINE, 'SECURITY="%s"' % tool))

    def seed(self, items):
        with open(self.store, 'w') as f:
            json.dump(items, f)

    def left(self):
        with open(self.store) as f:
            return json.load(f)

    def run_all(self):
        if os.path.realpath(self.home) == os.path.realpath(os.path.expanduser('~')):
            raise RuntimeError('this would run the uninstaller in the real home folder')
        env = {'HOME': self.home, 'PATH': '/usr/bin:/bin:/usr/sbin:/sbin',
               'SWITCHBOARD_APPLICATIONS': os.path.join(self.root, 'Applications')}
        return subprocess.run(['/bin/sh', self.script, '--all', '--no-agent'], env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True, timeout=180)

    def test_a_token_and_an_api_key_leave_the_keychain_with_the_logins(self):
        self.seed([[bearer.SERVICES[bearer.TOKEN], 'bearer-1'], [bearer.SERVICES[bearer.KEY], 'bearer-2'],
                   [vault.SERVICE_CURRENT, 'acct-a'], [vault.SERVICE_RECOVERY, 'safety:0123456789abcdef'],
                   [keys.SERVICE, 'work']] + NOT_OURS)
        done = self.run_all()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('Switchboard could not start', done.stderr)
        self.assertEqual(self.left(), NOT_OURS)                 # Claude Code's and another program's stay
        self.assertFalse(os.path.exists(self.data))

    def test_the_loop_asks_for_exactly_switchboards_own_services(self):
        self.seed(NOT_OURS)
        done = self.run_all()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        with open(self.calls) as f:
            asked = f.read().splitlines()
        self.assertEqual(sorted(asked), sorted('delete-generic-password -s %s' % s for s in OURS))


if __name__ == '__main__':
    unittest.main()
