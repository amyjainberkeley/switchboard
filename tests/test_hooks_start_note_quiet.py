import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""The note every chat gets at its start holds no email address and is short.

That text is kept in the chat's own file and goes wherever the chat goes (a shared transcript, a pasted bug report), so
it names the account Claude Code is on by its number in `switchboard`'s list, with its plan, and never by its address.
A resumed chat still hears which accounts it was on before, by number. Every name here is made up.
  SWITCHBOARD_HOME="$(mktemp -d)/data" python3 -m unittest discover -s tests -p 'test_hooks_start_note_quiet.py'"""
import json
import re
import time
import unittest

from fakes_links import A, B, C, G
from test_hooks_session import NOW, HookSandbox

from switchboard import artifacts

ACCOUNTS = {A: {'id': 'acct-a', 'profile': 'P1', 'profile_name': 'Work', 'link': 'proved', 'opens': 'P1', 'slot': 1,
                'plan': 'Max 20x'},
            B: {'id': 'acct-b', 'profile': 'P2', 'profile_name': 'Home', 'link': 'proved', 'opens': 'P2', 'slot': 2,
                'plan': 'Pro'},
            G: {'id': 'acct-g', 'profile': None, 'profile_name': None, 'link': 'none', 'opens': None, 'slot': 3,
                'plan': None}}
EMAILISH = re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}')
LONGEST = 400               # characters of the two sentences every chat gets (a resumed chat's third comes on top)


class StartNote(HookSandbox):
    def start(self, sid='s-1', source='startup'):
        return self.hook(self.event('SessionStart', sid=sid, source=source))

    def no_address(self, said):
        self.assertNotIn('@', said)
        for email in (A, B, C, G):
            self.assertNotIn(email, said)
        self.assertIsNone(EMAILISH.search(said))

    def test_no_address_the_number_and_plan_and_short(self):
        self.owners(active=A, accounts=ACCOUNTS,
                    chrome={'profiles': {'P1': {'extension': False, 'account': A}}, 'host': True})
        said = self.start()
        self.no_address(said)
        self.assertIn('(now account 1 in `switchboard`\'s list, Max 20x)', said)
        self.assertLessEqual(len(said), LONGEST)
        self.assertLessEqual(said.count('. '), 1, said)                  # two sentences
        self.assertIn('not deleted', said)
        self.assertIn('do not run /login or /logout', said)
        self.assertIn('`switchboard owner <link>`', said)

    def test_an_account_with_no_plan_word_or_number(self):
        self.owners(active=G, accounts=ACCOUNTS)
        self.assertIn('(now account 3 in `switchboard`\'s list)', self.start())
        old = {e: dict((k, v) for k, v in a.items() if k not in ('slot', 'plan')) for e, a in ACCOUNTS.items()}
        self.owners(active=A, accounts=old)                             # an owners.json from before numbers
        said = self.start('s-2')
        self.no_address(said)
        self.assertIn('when one runs low.', said)

    def test_a_resumed_chat_hears_where_it_was_by_number(self):
        self.owners(active=A, accounts=ACCOUNTS)
        self.start()
        self.owners(active=B, accounts=ACCOUNTS, timeline=[[A, NOW - 7200], [B, NOW - 60]])
        said = self.start(source='resume')
        self.no_address(said)
        self.assertIn('This chat was on account 1 before; what it made then belongs to account 1.', said)
        self.assertLessEqual(len(said), LONGEST + 100)
        self.assertIsNone(self.hook(self.event('UserPromptSubmit')))     # what a later hook needs: told at its start

    def test_an_account_no_longer_in_the_list_is_another_account(self):
        accts = dict(ACCOUNTS, **{C: {'id': 'acct-c', 'opens': None, 'slot': 4, 'removed': True}})
        self.owners(active=C, accounts=accts)
        self.start()
        self.owners(active=A, accounts=accts, timeline=[[C, NOW - 7200], [A, NOW - 60]])
        said = self.start(source='resume')
        self.no_address(said)
        self.assertIn('This chat was on another account before', said)

    def test_the_other_notes_still_name_the_account(self):
        self.owners(active=A, accounts=ACCOUNTS)
        self.start()
        self.owners(active=B, accounts=ACCOUNTS, timeline=[[A, NOW - 7200], [B, NOW - 60]])
        said = self.hook(self.event('UserPromptSubmit'))
        self.assertIn('Switchboard moved Claude Code from %s to %s' % (A, B), said)


class OddAddresses(unittest.TestCase):
    """Addresses of real shapes the simple pattern misses: an apostrophe, letters outside ASCII, a quoted local part,
    a host with no dot. In the list with a number, and not in it."""
    ODD = ("o'brien@work.example", 'ånå@exämple.example', '"ana smith"@work.example', 'ana@localhost')

    def test_none_of_them_stays_in_a_resumed_chats_note(self):
        from switchboard import session_hooks
        for odd in self.ODD:
            for listed in (True, False):
                accounts = {A: {'slot': 1, 'plan': 'Pro'}}
                if listed:
                    accounts[odd] = {'slot': 2}
                said = session_hooks.start_context({}, {'active': A, 'accounts': accounts}, {'account': odd}, False, 0)
                self.assertNotIn('@', said, (odd, listed))
                for part in ('brien', 'ana smith', 'localhost', 'exämple'):
                    self.assertNotIn(part, said, (odd, listed))
                self.assertIn('This chat was on %s before' % ('account 2' if listed else 'another account'), said)


class TheMapSaysNumberAndPlan(unittest.TestCase):
    def test_each_account_in_owners_json_has_its_number_and_plan_word(self):
        st = {'order': [A, B], 'active': A, 'timeline': [[A, time.time() - 60]], 'artifacts': [],
              'accounts': {A: {'connected': True, 'slot': 1, 'plan': 'max', 'tier': 'default_claude_max_20x',
                               'org': "ana@work.example's Organization"},
                           B: {'connected': True, 'slot': 2, 'plan': 'Team', 'org_type': 'claude_team',
                               'org': 'Northwind'}}}
        m = artifacts.owner_map(st, {}, extensions={}, host=None)
        self.assertEqual((m['accounts'][A]['slot'], m['accounts'][A]['plan']), (1, 'Max 20x'))
        self.assertEqual((m['accounts'][B]['slot'], m['accounts'][B]['plan']), (2, 'Team'))   # the plan, not the org
        json.dumps(m)


if __name__ == '__main__':
    unittest.main()
