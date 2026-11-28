import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# A test's child process is outside the audit hook of tests/_isolate.py, so that file also marks the run with one
# variable every child inherits, and the Keychain helper refuses to start the real /usr/bin/security while it is set.
# The children here guard themselves as well: an audit hook of their own notes and refuses any start of the real tool,
# so even a helper that failed to refuse it could not run it. What reached that hook is counted from its note; the
# real tool is never run. Stand-in tools are FakeSecurity (fakes_accounts).
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from fakes_accounts import FakeSecurity
from switchboard.accounts import keychain

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHILD = r'''
import json, os, sys
TOOL, NOTE = '/usr/bin/security', sys.argv[2]


def same(path):
    try:
        return os.path.samefile(os.fsdecode(path), TOOL)
    except (OSError, TypeError, ValueError):
        return False


def guard(event, args):
    # the program each event starts: Popen (executable, args, cwd, env), posix_spawn and exec (path, ...), spawn
    # (mode, path, ...); a bare name is the one Popen looks up on PATH
    at = {'subprocess.Popen': 0, 'os.posix_spawn': 0, 'os.exec': 0, 'os.spawn': 1}.get(event)
    if at is None or at >= len(args) or args[at] is None:
        return
    prog = os.fsdecode(args[at])
    if same(prog) or prog == 'security':
        with open(NOTE, 'a') as f:
            f.write('%s %s\n' % (event, prog))
        raise PermissionError('the real security tool, refused by the test')


sys.addaudithook(guard)
from switchboard.accounts import keychain
logs = []
kc = keychain.Keychain(tool=sys.argv[1], read_deadline=60.0, log=logs.append)
r = kc.read('Made-up item', 'tester', fresh=True, interactive=True)
print(json.dumps({'kind': r.kind, 'why': r.why, 'value': r.value, 'logs': logs,
                  'var': os.environ.get('SWITCHBOARD_TEST_RUN')}))
'''


class TheRealToolInATestsChild(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='real-tool-')
        self.home = os.path.join(self.tmp, 'home')
        self.data = os.path.join(self.tmp, 'data')
        os.makedirs(self.home)
        os.makedirs(self.data)
        self.note = os.path.join(self.tmp, 'starts-of-the-real-tool.txt')
        self.sec = FakeSecurity(self.tmp)
        self.sec.put('Made-up item', 'tester', 'made-up value')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def child(self, tool, without_the_variable=False):
        env = dict(os.environ)
        env.update(HOME=self.home, SWITCHBOARD_HOME=self.data, CLAUDE_CONFIG_DIR=os.path.join(self.home, '.claude'),
                   PYTHONDONTWRITEBYTECODE='1')
        if without_the_variable:
            env.pop('SWITCHBOARD_TEST_RUN', None)
        p = subprocess.run([sys.executable, '-B', '-c', CHILD, tool, self.note], cwd=ROOT, env=env,
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=300)
        self.assertEqual(p.returncode, 0, p.stderr.decode('utf-8', 'replace')[-2000:])
        got = json.loads(p.stdout.decode('utf-8').strip().splitlines()[-1])
        try:
            with open(self.note) as f:
                starts = f.read().splitlines()
        except FileNotFoundError:
            starts = []
        return got, starts

    def said_a_test_tried(self, logs):
        return any('a test run tried to start the real /usr/bin/security' in line for line in logs)

    def test_this_run_marks_itself_so_every_child_inherits_it(self):
        self.assertEqual(os.environ.get(keychain.TEST_RUN_VAR), '1')
        self.assertEqual(keychain.TEST_RUN_VAR, 'SWITCHBOARD_TEST_RUN')

    def test_a_tests_child_given_the_real_tool_is_refused_and_starts_nothing(self):
        got, starts = self.child(keychain.TOOL)
        self.assertEqual(starts, [])                                 # nothing even tried to start it
        self.assertEqual((got['kind'], got['why']), ('unreadable', 'test_run'))  # said apart from a missing tool
        self.assertTrue(self.said_a_test_tried(got['logs']), got['logs'])
        self.assertEqual(got['var'], '1')                            # inherited, never set by the child

    def test_the_real_tool_by_its_bare_name_or_another_spelling_is_refused_too(self):
        for tool in ('security', '/usr/bin/../bin/security'):
            got, starts = self.child(tool)
            self.assertEqual(starts, [], tool)
            self.assertEqual((got['kind'], got['why']), ('unreadable', 'test_run'), tool)
            self.assertTrue(self.said_a_test_tried(got['logs']), (tool, got['logs']))

    def test_a_stand_in_tool_is_not_refused(self):
        got, starts = self.child(self.sec.tool)
        self.assertEqual((got['kind'], got['value']), ('present', 'made-up value'))
        self.assertEqual(len(self.sec.calls('find')), 1)              # the stand-in's own log: it ran once
        self.assertEqual(starts, [])
        self.assertFalse(self.said_a_test_tried(got['logs']))

    def test_without_the_variable_the_helper_starts_the_tool_it_is_given_as_before(self):
        # the installed program's case: nothing sets the variable. Here the child's own guard is what stops the real
        # tool, and it saw the helper's one try
        got, starts = self.child(keychain.TOOL, without_the_variable=True)
        self.assertEqual(len(starts), 1, starts)
        self.assertIsNone(got['var'])
        self.assertFalse(self.said_a_test_tried(got['logs']))
        got, starts = self.child(self.sec.tool, without_the_variable=True)
        self.assertEqual((got['kind'], got['value']), ('present', 'made-up value'))


class WhichToolIsTheRealOne(unittest.TestCase):
    """Decided from the path alone; nothing is run. (A temp-folder link to the real tool could not be removed again
    under tests/_isolate.py, so links are shown on a stand-in put in the real tool's place.)"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='real-tool-name-')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_real_tool_by_any_path_and_nothing_else(self):
        stand_in = FakeSecurity(self.tmp).tool
        self.assertTrue(keychain.real_tool('/usr/bin/security'))
        self.assertTrue(keychain.real_tool('/usr/bin/../bin/security'))
        self.assertTrue(keychain.real_tool('security', {'PATH': '/usr/bin:/bin'}))
        self.assertFalse(keychain.real_tool('security', {'PATH': self.tmp}))
        self.assertFalse(keychain.real_tool(stand_in))
        self.assertFalse(keychain.real_tool(os.path.join(self.tmp, 'not-there')))

    def test_a_link_or_a_hard_link_to_the_tool_is_the_tool(self):
        stand_in = FakeSecurity(self.tmp).tool
        link, hard = os.path.join(self.tmp, 'tool-link'), os.path.join(self.tmp, 'tool-hard-link')
        os.symlink(stand_in, link)
        os.link(stand_in, hard)
        with mock.patch.object(keychain, 'TOOL', stand_in):
            self.assertTrue(keychain.real_tool(link))
            self.assertTrue(keychain.real_tool(hard))
            self.assertTrue(keychain.real_tool('fake-security', {'PATH': self.tmp}))
            self.assertFalse(keychain.real_tool('/usr/bin/security'))

    def test_the_product_never_sets_the_variable(self):
        here = os.path.join(ROOT, 'switchboard')
        setters = []
        for folder, _, names in os.walk(here):
            for name in names:
                if name.endswith(('.py', '.sh', '.plist')):
                    with open(os.path.join(folder, name), encoding='utf-8', errors='replace') as f:
                        text = f.read()
                    if 'SWITCHBOARD_TEST_RUN' in text and os.path.join(folder, name) != keychain.__file__:
                        setters.append(name)
        self.assertEqual(setters, [])
        with open(os.path.join(ROOT, 'install.sh'), encoding='utf-8') as f:
            self.assertNotIn('SWITCHBOARD_TEST_RUN', f.read())


if __name__ == '__main__':
    unittest.main()
