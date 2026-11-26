import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""The words a chat hears at its start name the account it is on and count the others, never their addresses.

That text is kept in the chat's own file and goes wherever the chat goes (an export, a shared chat, an organization's
logs), so another account's address never goes into it. Every name here is made up.
  SWITCHBOARD_HOME="$(mktemp -d)/data" python3 -m unittest discover -s tests -p 'test_hooks_session_start_names_no_other_account.py'"""
import unittest

from fakes_links import A, B, C, G
from test_hooks_session import HookSandbox


class StartWords(HookSandbox):
    def start(self, sid='s-1'):
        return self.hook(self.event('SessionStart', sid=sid, source='startup'))

    def test_the_others_are_counted_not_named(self):
        self.owners(active=A)                               # A, B and G
        said = self.start()
        self.assertIn("Claude accounts when one runs low (now on %s; 2 other accounts)." % A, said)
        for other in (B, G):
            self.assertNotIn(other, said)
        self.assertNotIn('the others', said)

    def test_one_other_and_none(self):
        self.owners(active=A, accounts={A: {'opens': None}, B: {'opens': None}})
        self.assertIn('(now on %s; 1 other account).' % A, self.start('s-2'))
        self.owners(active=A, accounts={A: {'opens': None}})
        self.assertIn('(now on %s).' % A, self.start('s-3'))

    def test_a_removed_account_is_not_counted(self):
        self.owners(active=A, accounts={A: {'opens': None}, B: {'opens': None}, C: {'opens': None, 'removed': True}})
        said = self.start()
        self.assertIn('(now on %s; 1 other account).' % A, said)
        self.assertNotIn(C, said)


if __name__ == '__main__':
    unittest.main()
