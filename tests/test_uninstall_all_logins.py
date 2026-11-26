"""`uninstall.sh --all` takes every login of Switchboard's out of the Keychain, through the program.

tidy --all removes each kept account with api.remove, then asks api.remove_all for the rest: a switch's safety copy, a
refresh's successor, and what an earlier removal left (the three services "Switchboard login", "Switchboard login
(previous)" and "Switchboard recovery"), with the recovery files. Claude Code's own login and any other program's
items are never touched. The engine half runs on the account fakes (an in-process Keychain, and once the real wrapper
with a child-process stand-in for /usr/bin/security); the command half on tests/fakes_accounts_api.py.
tests/install-check.sh runs the real installed program's tidy --all in a throwaway home with a stand-in security tool.
"""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import io
import json
import os
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

from fakes_accounts import Rig, login_text
from switchboard.accounts import api, context, roster, switcher, tokens, vault
from switchboard.accounts.api import AccountsError

SERVICES = ('Switchboard login', 'Switchboard login (previous)', 'Switchboard recovery')
DAY_MS = 86400 * 1000


def a_login(n):
    now_ms = int(time.time() * 1000)
    return login_text('access-%s' % n, 'refresh-%s' % n, now_ms + 3600 * 1000, now_ms + 30 * DAY_MS)


class EngineCase(unittest.TestCase):
    real_keychain = False

    def setUp(self):
        self.rig = Rig(real_keychain=self.real_keychain)
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()

    def tearDown(self):
        self.use.__exit__(None, None, None)
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        self.rig.close()

    def ours(self):
        return sorted(k for k in self.rig.sec.items() if k[0] in SERVICES)

    def recovery_files(self):
        folder = self.ctx.path('recovery')
        return sorted(n for n in (os.listdir(folder) if os.path.isdir(folder) else [])
                      if n.endswith('.json') and n != 'manifest.json')

    def everything(self):
        """Two kept accounts (Claude Code on B), one removed earlier, and every kind of login a store can hold."""
        self.rig.keep('uuid-c', 'c@example.com', 'org-c')
        roster.remove(self.ctx, 'uuid-c')                     # removed earlier...
        self.rig.sec.put('Switchboard login (previous)', 'uuid-c', '{"generations": []}')   # ...leaving this behind
        self.rig.two_accounts()
        self.rig.sec.put('Switchboard login (previous)', 'uuid-a', '{"generations": []}')
        vault.stash(self.ctx, 'successor:uuid-c', a_login('c2'), 'uuid-c', 'removed while refreshing')
        vault.stash(self.ctx, 'safety:0123456789abcdef', a_login('x'), None, 'before switching')
        self.rig.kc.rule('write', service='Switchboard recovery', account='safety:fedcba9876543210', why='denied')
        self.assertEqual(vault.stash(self.ctx, 'safety:fedcba9876543210', a_login('y'), None, 'before switching'),
                         (True, 'file'))                       # the Keychain refused it: an owner-only file
        self.rig.sec.put('Some other app', 'someone', 'theirs')
        self.live = self.rig.live_text()
        self.assertEqual(len(self.ours()), 6)
        self.assertEqual(self.recovery_files(), ['safety_fedcba9876543210.json'])


class RemoveAll(EngineCase):
    def test_every_item_of_the_three_services_goes_and_nothing_else_is_touched(self):
        self.everything()
        before = len(self.rig.kc.calls)
        said = api.remove_all()
        self.assertEqual(said, ['Account a@example.com: removed, with its saved login.',
                                'Account b@example.com: removed, with its saved login.',
                                'Earlier logins and recovery copies: 5 removed.'])
        self.assertEqual(self.ours(), [])
        self.assertEqual(self.recovery_files(), [])
        self.assertEqual(vault.stashed(self.ctx), [])
        self.assertEqual(self.rig.live_text(), self.live)                  # Claude Code's login, as it was
        self.assertEqual(self.rig.sec.get('Some other app', 'someone'), 'theirs')
        doc = self.rig.doc()
        self.assertEqual((doc['accounts'], doc['order']), ({}, []))
        self.assertEqual(sorted(doc['removed']), ['uuid-a', 'uuid-b', 'uuid-c'])
        self.assertEqual(api.accounts(), [])
        self.assertFalse([c for c in self.rig.kc.calls[before:] if c[0] in ('read', 'write')])   # never read or written
        self.assertIn(('Removed b@example.com from Switchboard', 'info'), [e[:2] for e in self.rig.events])

    def test_after_each_account_was_removed_it_takes_the_rest_and_a_second_run_finds_nothing(self):
        self.everything()
        for target in ('uuid-a', 'uuid-b'):
            api.remove(target)                                             # what tidy --all does first
        self.assertEqual(len(self.ours()), 3)                              # c's earlier login and two recovery copies
        self.assertEqual(api.remove_all(), ['Earlier logins and recovery copies: 4 removed.'])
        self.assertEqual((self.ours(), self.recovery_files()), ([], []))
        deletes = self.rig.kc.count('delete')
        self.assertEqual(api.remove_all(), [])
        self.assertEqual(self.rig.kc.count('delete'), deletes)             # only looked: nothing was there

    def test_an_item_the_keychain_wont_give_up_is_named_and_its_record_stays_for_the_next_run(self):
        self.everything()
        self.rig.kc.rule('delete', service='Switchboard recovery', account='safety:0123456789abcdef', why='locked')
        self.rig.kc.rule('delete', service='Switchboard login (previous)', account='uuid-c', why='locked')
        with self.assertRaises(AccountsError) as cm:
            api.remove_all()
        self.assertEqual(cm.exception.kind, 'keychain_locked')
        self.assertEqual(str(cm.exception), "Couldn't take c@example.com's earlier login and a recovery copy out of "
                                            "the Keychain: it is locked.")
        self.assertEqual(self.ours(), [('Switchboard login (previous)', 'uuid-c'),
                                       ('Switchboard recovery', 'safety:0123456789abcdef')])
        self.assertEqual([e['key'] for e in vault.stashed(self.ctx)], ['safety:0123456789abcdef'])
        self.assertIn('uuid-c', self.rig.doc()['removed'])
        self.assertEqual(api.accounts(), [])                               # what could go went
        self.assertEqual(api.remove_all(), ['Earlier logins and recovery copies: 2 removed.'])   # unlocked: the rest
        self.assertEqual((self.ours(), vault.stashed(self.ctx)), ([], []))

    def test_an_account_whose_login_stays_is_still_kept(self):
        self.rig.two_accounts()
        self.rig.kc.rule('delete', service='Switchboard login', account='uuid-a', why='timeout')
        with self.assertRaises(AccountsError) as cm:
            api.remove_all()
        self.assertEqual((cm.exception.kind, str(cm.exception)),
                         ('keychain_slow', "Couldn't take a@example.com's saved login out of the Keychain: it didn't "
                                           "answer in time."))
        self.assertEqual([a.email for a in api.accounts()], ['a@example.com'])
        self.assertEqual(self.ours(), [('Switchboard login', 'uuid-a')])

    def test_while_a_run_holds_an_account_or_from_inside_one_nothing_is_removed(self):
        self.everything()
        with vault.changing(self.ctx) as doc:
            doc['claims']['uuid-a'] = {'pid': os.getpid(), 'start': 'self-start'}    # a live `switchboard run`
        with self.assertRaises(AccountsError) as cm:
            api.remove_all()
        self.assertEqual((cm.exception.kind, str(cm.exception)),
                         ('claimed', 'a@example.com is in use by a `switchboard run`; run this again once it ends. '
                                     'Nothing was removed.'))
        self.assertEqual(len(self.ours()), 6)
        with vault.changing(self.ctx) as doc:
            doc['claims'].clear()
        self.rig.env['CLAUDE_CONFIG_DIR'] = os.path.join(self.ctx.path('run'), 'x')
        with self.assertRaises(AccountsError):
            api.remove_all()
        self.assertEqual(len(self.ours()), 6)

    def test_a_cut_off_switch_is_settled_first_and_while_it_cant_be_nothing_is_removed(self):
        self.everything()
        with open(switcher.journal_path(self.ctx), 'w') as f:
            json.dump({'pid': 1, 'from_id': 'uuid-a', 'to_id': 'uuid-b', 'step': 'write', 'locks': [],
                       'safety_key': 'safety:0123456789abcdef'}, f)
        self.rig.kc.rule('read', service=self.rig.service, account=self.rig.account, why='locked', times=-1)
        with self.assertRaises(AccountsError) as cm:
            api.remove_all()
        self.assertEqual(str(cm.exception), 'A switch was cut off; Switchboard will settle it as soon as the Keychain '
                                            'answers. Nothing was removed; run this again then.')
        self.assertEqual(len(self.ours()), 6)                              # its safety copy above all
        self.rig.kc.rules[:] = []
        said = api.remove_all()                                            # the Keychain answers: settled, then all
        self.assertEqual(said[-1], 'Earlier logins and recovery copies: 5 removed.')
        self.assertEqual(self.ours(), [])
        self.assertFalse(os.path.exists(switcher.journal_path(self.ctx)))


class RemoveAllThroughTheRealWrapper(EngineCase):
    """keychain.Keychain and a child-process stand-in for /usr/bin/security: exists (no -w) and delete, nothing else."""
    real_keychain = True

    def test_the_stand_in_keychain_ends_with_nothing_of_switchboards(self):
        self.rig.two_accounts()
        vault.stash(self.ctx, 'safety:0123456789abcdef', a_login('x'), None, 'before switching')
        for target in ('uuid-a', 'uuid-b'):
            api.remove(target)
        before = len(self.rig.sec.calls())
        self.assertEqual(api.remove_all(), ['Earlier logins and recovery copies: 1 removed.'])
        self.assertEqual(self.ours(), [])
        verbs = set(c['verb'] for c in self.rig.sec.calls()[before:])
        self.assertEqual(verbs - {'list'}, {'exists', 'delete'})            # never a secret read (-w), never a write


class TidyAll(unittest.TestCase):
    """The command half: tidy --all removes each account, then asks the engine for the rest."""

    def setUp(self):
        from fakes_accounts_api import install
        self.fake = install(self)
        self.fake.hold('ana@work.example', 'org-work')
        self.fake.hold('ana@home.example')
        q = mock.patch('switchboard.lanes.lanes', return_value=[])          # no gateway keys here
        q.start()
        self.addCleanup(q.stop)

    def tidy(self, *args):
        from switchboard import __main__ as cli
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cli.main(['tidy'] + list(args))
        return rc, out.getvalue()

    def asked(self):
        return [c[0] for c in self.fake.calls if c[0] in ('remove', 'remove_all')]

    def test_each_account_then_the_rest(self):
        self.fake.remove_all = lambda: (self.fake.calls.append(('remove_all',)) or
                                        ['Earlier logins and recovery copies: 2 removed.'])
        api.remove_all = self.fake.remove_all
        rc, said = self.tidy('--all')
        self.assertEqual(rc, 0, said)
        self.assertEqual(self.asked(), ['remove', 'remove', 'remove_all'])
        self.assertIn('Account ana@work.example: removed, with its saved login.\n'
                      'Account ana@home.example: removed, with its saved login.\n'
                      'Earlier logins and recovery copies: 2 removed.\n', said)

    def test_what_the_engine_could_not_take_out_is_named_and_tidy_fails(self):
        self.fake.fail['remove_all'] = AccountsError('keychain_locked', "Couldn't take a recovery copy out of the "
                                                                        "Keychain: it is locked.")
        rc, said = self.tidy('--all')
        self.assertEqual(rc, 1, said)
        self.assertIn("Saved logins: left as they are (Couldn't take a recovery copy out of the Keychain: it is "
                      "locked.)\n", said)

    def test_an_account_left_behind_stops_it_there(self):
        self.fake.fail['remove'] = AccountsError('keychain_locked', "macOS's Keychain is locked.")
        rc, said = self.tidy('--all')
        self.assertEqual(rc, 1, said)
        self.assertNotIn('remove_all', self.asked())                      # the account's own line says what to do
        self.assertNotIn('Saved logins', said)

    def test_a_plain_tidy_never_asks(self):
        rc, said = self.tidy()
        self.assertEqual((rc, self.asked()), (0, []), said)


if __name__ == '__main__':
    unittest.main()
