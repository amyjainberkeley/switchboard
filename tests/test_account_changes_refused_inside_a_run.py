"""Inside a `switchboard run` shell (its CLAUDE_CONFIG_DIR is one of Switchboard's run folders), every call that
changes the account list or the login Claude Code is on refuses with one sentence, and changes nothing: a switch, an
add, a removal, a short name, the order, a kept token, the recovery copies and the uninstaller's removal of all."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import os
import time
import unittest

from fakes_accounts import Rig
from switchboard.accounts import api, context, tokens
from switchboard.accounts.api import AccountsError

SAID = 'This shell is inside a `switchboard run` session; change accounts from a normal terminal or the page.'


class InsideARun(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        self.a_text, self.b_text = self.rig.two_accounts()          # Claude Code is on B
        inner = os.path.join(self.rig.data, 'run', 'run-inner')
        os.makedirs(inner)
        self.rig.env['CLAUDE_CONFIG_DIR'] = inner

    def tearDown(self):
        self.use.__exit__(None, None, None)
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        self.rig.close()

    def test_every_change_to_the_list_or_the_login_in_use_refuses_and_changes_nothing(self):
        other = os.path.join(self.rig.root, 'signin')
        os.makedirs(other)
        before = (self.rig.doc(), self.rig.sec.items(), self.rig.config())
        calls = {'switch': lambda: api.switch('1'),
                 'add': api.add_current,
                 'add a sign-in folder': lambda: api.add_login(other),
                 'remove': lambda: api.remove('1'),
                 'short name': lambda: api.set_alias('1', 'home'),
                 'order': lambda: api.reorder(['uuid-b', 'uuid-a']),
                 'add a token': lambda: api.add_token('sk-ant-oat01-made-up-for-a-test', label='spare'),
                 'remove a token': lambda: api.remove_token('spare'),
                 'clear recovery copies': lambda: api.clear_leftovers([], everything=True),
                 'remove all': api.remove_all}
        for what, call in calls.items():
            with self.assertRaises(AccountsError, msg=what) as e:
                call()
            self.assertEqual(str(e.exception), SAID, what)
        self.assertEqual((self.rig.doc(), self.rig.sec.items(), self.rig.config()), before)   # the live item too

    def test_the_same_calls_work_from_a_normal_terminal(self):
        del self.rig.env['CLAUDE_CONFIG_DIR']
        api.set_alias('1', 'home')
        api.reorder(['uuid-b', 'uuid-a'])
        self.assertEqual([(a.id, a.alias) for a in api.accounts()], [('uuid-b', None), ('uuid-a', 'home')])
        self.assertEqual(api.switch('home').ok, True)


if __name__ == '__main__':
    unittest.main()
