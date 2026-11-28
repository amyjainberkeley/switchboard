import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""The item need switch's own process: what the hook starts to ask the engine, and how it answers.

The hook never switches by itself: it starts a process of its own (session_hooks.item_need_command), in a session of
its own, which runs the engine's gate (_item_need_in_process) and answers in one JSON line. The hook waits a few
seconds for it; a switch that takes longer goes on by itself, never cut off part-way by the hook's end. Here the
package is a stand-in, or the real package in a throwaway home with no account saved (so nothing can switch)."""
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest

from switchboard import session_hooks
from switchboard.accounts import keychain

OWNER = 'b@lab.example'


class ItsOwnProcess(unittest.TestCase):
    def stand_in(self, body):
        """A package folder whose session_hooks holds only `body`."""
        root = tempfile.mkdtemp(prefix='switchboard-item-need-')
        self.addCleanup(shutil.rmtree, root, True)
        os.makedirs(os.path.join(root, 'switchboard'))
        open(os.path.join(root, 'switchboard', '__init__.py'), 'w').close()
        with open(os.path.join(root, 'switchboard', 'session_hooks.py'), 'w') as f:
            f.write(body)
        return root

    def test_it_runs_the_gate_and_its_answer_comes_back(self):
        root = self.stand_in('def _item_need_in_process(owner, account_id, why):\n'
                             '    if (account_id, why) == ("acct-b", "why " + owner):\n'
                             '        return owner, None\n'
                             '    return None, "no room"\n')
        cmd = session_hooks.item_need_command(root, OWNER, 'acct-b', 'why ' + OWNER)
        self.assertEqual(session_hooks._ask_item_need(OWNER, 'acct-b', 'why ' + OWNER, budget=120, command=cmd),
                         (OWNER, None))
        cmd = session_hooks.item_need_command(root, OWNER, 'acct-x', 'why ' + OWNER)
        self.assertEqual(session_hooks._ask_item_need(OWNER, 'acct-x', 'why ' + OWNER, budget=120, command=cmd),
                         (None, 'no room'))

    def test_a_slow_switch_goes_on_by_itself_in_a_session_of_its_own(self):
        d = tempfile.mkdtemp(prefix='switchboard-item-need-')
        self.addCleanup(shutil.rmtree, d, True)
        root = self.stand_in('import os, time\n'
                             'def _item_need_in_process(owner, account_id, why):\n'
                             '    open(why + "/sid", "w").write(str(os.getsid(0)))\n'
                             '    time.sleep(8)\n'
                             '    open(why + "/done", "w").write("1")\n'
                             '    return owner, None\n')
        cmd = session_hooks.item_need_command(root, OWNER, 'acct-b', d)
        self.assertEqual(session_hooks._ask_item_need(OWNER, 'acct-b', d, budget=0.3, command=cmd), (None, None))
        self.assertFalse(os.path.exists(os.path.join(d, 'done')))      # the hook did not wait for it
        for _ in range(1200):
            if os.path.exists(os.path.join(d, 'done')):
                break
            time.sleep(0.1)
        self.assertTrue(os.path.exists(os.path.join(d, 'done')))        # it finished although nobody waited
        with open(os.path.join(d, 'sid')) as f:
            self.assertNotEqual(int(f.read()), os.getsid(0))

    def test_the_real_package_answers_in_that_process(self):
        self.assertEqual(os.environ.get(keychain.TEST_RUN_VAR), '1')  # the Keychain helper refuses the real one
        parent = os.path.dirname(os.path.dirname(os.path.abspath(session_hooks.__file__)))
        r = subprocess.run(session_hooks.item_need_command(parent, 'nobody@lab.example', None, 'a chat asked'),
                           capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertIsNone(got['went'])                  # no account is saved in this home: nothing can move
        self.assertTrue(isinstance(got['why'], str) and got['why'], got)


if __name__ == '__main__':
    unittest.main()
