import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""The words a chat hears at its start name the account it is on by its number in `switchboard`'s list, and no
account's address: not the one it is on, not the others.

That text is kept in the chat's own file and goes wherever the chat goes (an export, a shared chat, an organization's
logs), so no address goes into it. Every name here is made up.
  SWITCHBOARD_HOME="$(mktemp -d)/data" python3 -m unittest discover -s tests -p 'test_hooks_session_start_names_no_other_account.py'"""
import unittest

from fakes_links import A, B, C, G
from test_hooks_session import HookSandbox


def numbered(*emails, **more):
    out = dict((e, {'opens': None, 'slot': n}) for n, e in enumerate(emails, 1))
    for e, extra in more.items():
        out[e].update(extra)
    return out


class StartWords(HookSandbox):
    def start(self, sid='s-1'):
        return self.hook(self.event('SessionStart', sid=sid, source='startup'))

    def test_no_account_is_named_by_its_address(self):
        self.owners(active=A, accounts=numbered(A, B, G))
        said = self.start()
        self.assertIn("Claude accounts when one runs low (now account 1 in `switchboard`'s list).", said)
        for email in (A, B, G):
            self.assertNotIn(email, said)
        self.assertNotIn('@', said)

    def test_the_number_is_the_accounts_own(self):
        self.owners(active=B, accounts=numbered(A, B))
        self.assertIn("(now account 2 in `switchboard`'s list).", self.start('s-2'))
        self.owners(active=A, accounts={A: {'opens': None}})        # no number known: no number said
        said = self.start('s-3')
        self.assertIn('when one runs low.', said)
        self.assertNotIn(A, said)

    def test_a_removed_account_is_not_named(self):
        self.owners(active=A, accounts=numbered(A, B, C))
        self.start()
        self.owners(active=B, accounts=numbered(A, B, C), timeline=[[A, 0], [B, 1]])
        said = self.start()
        self.assertIn('This chat was on account 1 before', said)
        self.owners(active=A, accounts=numbered(A, B, C))
        self.start('s-9')
        self.owners(active=B, accounts=numbered(A, B, C, **{A: {'removed': True}}), timeline=[[A, 0], [B, 1]])
        said = self.start('s-9')
        self.assertIn('This chat was on another account before; what it made then belongs to another account.', said)
        self.assertNotIn('@', said)


if __name__ == '__main__':
    unittest.main()
