import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# Where an account's auto-switch rule is kept: with auto-switch's own settings ('auto_policy' in settings.json), as
# 'rules': {account id: 'last' | 'out'}. No rule is no entry, and no rules is no key, so a rule set and taken away
# leaves the settings file as it was, byte for byte. A rule for an account that is not saved, or a word that is not one
# of the two, is no rule, and is dropped when the settings are next written. Removing an account takes its rule with it;
# a renewal or a new sign-in to the same account keeps it, and it never passes to another account. Made-up accounts.
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import core
from switchboard.accounts import api, autoloop, context, policy, tokens, vault
from fakes_accounts import Rig
from fakes_autoswitch import FakeEngine, Home, account


def settings_bytes():
    with open(core.data_path('settings.json'), 'rb') as f:
        return f.read()


class TheStore(Home):
    def setUp(self):
        super().setUp()
        self.engine = FakeEngine([account('a1', 1), account('a2', 2), account('a3', 3)], 'a1')
        p = mock.patch.object(api, 'accounts', self.engine.accounts)
        p.start()
        self.addCleanup(p.stop)

    def test_a_rule_is_kept_read_back_and_taken_away(self):
        got = autoloop.save_settings(rule=('a2', 'last'))
        self.assertEqual(got['rules'], {'a2': 'last'})
        autoloop.save_settings(rule=('a3', 'out'))
        self.assertEqual(core.settings()['auto_policy'], {'rules': {'a2': 'last', 'a3': 'out'}})
        self.assertEqual(autoloop.load_settings().rules, (('a2', 'last'), ('a3', 'out')))
        self.assertEqual(api.auto_settings()['rules'], {'a2': 'last', 'a3': 'out'})
        autoloop.save_settings(rule=('a2', 'out'))
        self.assertEqual(autoloop.settings_dict()['rules'], {'a2': 'out', 'a3': 'out'})
        autoloop.save_settings(rule=('a2', 'in'))
        self.assertEqual(autoloop.settings_dict()['rules'], {'a3': 'out'})

    def test_taking_a_rule_away_leaves_the_settings_file_as_it_was_byte_for_byte(self):
        core.set_setting('notify', True)
        for before in (None, {'strategy': 'most-room', 'weekly_at': 93.0}):
            with self.subTest(before=before):
                if before is not None:
                    autoloop.save_settings(strategy='most-room', weekly_at=93)
                was = settings_bytes()
                autoloop.save_settings(rule=('a2', 'last'))
                autoloop.save_settings(rule=('a3', 'out'))
                self.assertNotEqual(settings_bytes(), was)
                autoloop.save_settings(rule=('a3', 'in'))
                autoloop.save_settings(rule=('a2', 'in'))
                self.assertEqual(settings_bytes(), was)
                self.assertEqual('auto_policy' in core.settings(), before is not None)

    def test_in_for_an_account_with_no_rule_writes_nothing(self):
        core.set_setting('notify', True)
        was = settings_bytes()
        autoloop.save_settings(rule=('a2', 'in'))
        self.assertEqual(settings_bytes(), was)

    def test_an_unknown_account_or_word_is_refused_and_nothing_is_written(self):
        core.set_setting('notify', True)
        was = settings_bytes()
        for rule, kind, words in ((('nobody', 'last'), 'not_found', 'That account is not saved in Switchboard.'),
                                  (('a2', 'never'), 'other', 'Pick in, last or out for an account.'),
                                  (('a2', None), 'other', 'Pick in, last or out for an account.'),
                                  (('a2',), 'other', 'Pick in, last or out for an account.'),
                                  ('a2', 'other', 'Pick in, last or out for an account.')):
            with self.subTest(rule=rule):
                with self.assertRaises(api.AccountsError) as e:
                    autoloop.save_settings(rule=rule)
                self.assertEqual((e.exception.kind, str(e.exception)), (kind, words))
        self.assertEqual(settings_bytes(), was)

    def test_rules_stored_by_hand_as_junk_read_as_none_and_are_dropped_at_the_next_save(self):
        core.set_setting('auto_policy', {'strategy': 'most-room', 'rules': {'a1': 'never', 'a2': 'last',
                                                                             'gone': 'out', 'a3': ['out']}})
        self.assertEqual(autoloop.load_settings().rules, (('a2', 'last'), ('gone', 'out')))
        autoloop.save_settings(weekly_at=94)
        self.assertEqual(core.settings()['auto_policy'], {'strategy': 'most-room', 'rules': {'a2': 'last'},
                                                          'weekly_at': 94.0})
        for junk in ('out', ['a2', 'last'], 7):
            with self.subTest(junk=junk):
                core.set_setting('auto_policy', {'strategy': 'most-room', 'rules': junk})
                self.assertEqual(autoloop.load_settings().rules, ())
                self.assertEqual(autoloop.load_settings().strategy, 'most-room')
                autoloop.save_settings(model_at=94)
                self.assertNotIn('rules', core.settings()['auto_policy'])

    def test_a_save_with_no_rules_stored_never_reads_the_accounts(self):
        with mock.patch.object(api, 'accounts', side_effect=AssertionError('read the accounts')):
            autoloop.save_settings(strategy='most-room', weekly_at=93, model='Fable')
            autoloop.save_settings(model='')
        self.assertEqual(core.settings()['auto_policy'], {'strategy': 'most-room', 'weekly_at': 93.0})

    def test_a_list_that_cannot_be_read_drops_no_rule_and_refuses_a_change(self):
        autoloop.save_settings(rule=('a2', 'last'))
        with mock.patch.object(api, 'accounts', side_effect=api.AccountsError('other', 'busy')):
            autoloop.save_settings(weekly_at=94)
            self.assertEqual(autoloop.settings_dict()['rules'], {'a2': 'last'})
            with self.assertRaises(api.AccountsError):
                autoloop.save_settings(rule=('a3', 'out'))
        self.assertEqual(autoloop.settings_dict()['rules'], {'a2': 'last'})

    def test_a_list_that_reads_as_empty_drops_no_rule(self):
        autoloop.save_settings(rule=('a2', 'out'))
        with mock.patch.object(api, 'accounts', return_value=[]):     # not there for a moment, say
            autoloop.save_settings(weekly_at=94)
        self.assertEqual(autoloop.settings_dict()['rules'], {'a2': 'out'})

    def test_a_rule_written_as_its_account_is_removed_is_refused_and_leaves_no_rule(self):
        core.set_setting('notify', True)
        was = settings_bytes()
        lists = [self.engine.accounts(), [a for a in self.engine.accounts() if a.id != 'a2']]
        with mock.patch.object(api, 'accounts', side_effect=lambda: lists.pop(0) if len(lists) > 1 else lists[0]):
            with self.assertRaises(api.AccountsError) as e:     # removed after the first look, before the write
                autoloop.save_settings(rule=('a2', 'out'))
        self.assertEqual((e.exception.kind, str(e.exception)), ('not_found', 'That account is not saved in Switchboard.'))
        self.assertEqual(settings_bytes(), was)

    def test_forgetting_rules(self):
        autoloop.save_settings(rule=('a2', 'last'))
        autoloop.save_settings(rule=('a3', 'out'))
        autoloop.forget_rules(['a2'])
        self.assertEqual(autoloop.settings_dict()['rules'], {'a3': 'out'})
        autoloop.forget_rules()
        self.assertNotIn('auto_policy', core.settings())
        self.assertNotIn('rules', autoloop.settings_dict())              # no rule: the settings read as before


class TheAccountsId(unittest.TestCase):
    """A rule is kept under the account's id (vault.account_key): Anthropic's account id, or id/organization when the
    same person is kept for a second organization. The engine's own tests prove a login is kept under it again on a
    renewal and a new sign-in; this pins what the rule leans on."""

    def setUp(self):
        self.rig = Rig()
        self.use = context.use(self.rig.ctx)
        self.use.__enter__()
        for p in (core.data_path('settings.json'),):
            if os.path.exists(p):
                os.remove(p)
        self.rig.two_accounts()                       # a (uuid-a, org-a) and b (uuid-b, org-b); Claude Code on b

    def tearDown(self):
        self.use.__exit__(None, None, None)
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        self.rig.close()

    def test_a_new_sign_in_or_a_renewal_keeps_the_rule(self):
        api.set_auto(rule=('uuid-a', 'last'))
        self.rig.keep('uuid-a', 'a@example.com', 'org-a')               # signed in again to the same account
        self.rig.claude_renews()                                        # Claude Code renewed its login
        self.assertEqual(api.find('a@example.com').id, 'uuid-a')
        self.assertEqual(api.auto_settings()['rules'], {'uuid-a': 'last'})

    def test_it_never_passes_to_another_account(self):
        api.set_auto(rule=('uuid-a', 'out'))
        self.rig.keep('uuid-c', 'c@example.com', 'org-c')
        self.assertEqual(api.find('c@example.com').id, 'uuid-c')
        self.assertEqual(api.auto_settings()['rules'], {'uuid-a': 'out'})
        # the same person kept for a second organization is a second account, under its own id
        doc = {'accounts': {'uuid-a': {'uuid': 'uuid-a', 'org_id': 'org-a'}}}
        self.assertEqual(vault.account_key(doc, 'uuid-a', 'org-a'), 'uuid-a')
        self.assertEqual(vault.account_key(doc, 'uuid-a', 'org-z'), 'uuid-a/org-z')
        self.assertEqual(vault.account_key(doc, 'uuid-x', 'org-a'), 'uuid-x')

    def test_removing_an_account_removes_its_rule(self):
        api.set_auto(rule=('uuid-a', 'last'))
        api.set_auto(rule=('uuid-b', 'out'))
        api.remove('a@example.com')
        self.assertEqual(api.auto_settings()['rules'], {'uuid-b': 'out'})
        self.rig.keep('uuid-a', 'a@example.com', 'org-a')               # kept again later: it starts with no rule
        self.assertEqual(api.find('a@example.com').id, 'uuid-a')
        self.assertEqual(policy.rule_of(autoloop.load_settings(), 'uuid-a'), 'in')

    def test_a_removal_that_is_refused_keeps_the_rule(self):
        api.set_auto(rule=('uuid-a', 'last'))
        with self.assertRaises(api.AccountsError):
            api.remove('nobody@example.com')
        self.assertEqual(api.auto_settings()['rules'], {'uuid-a': 'last'})


if __name__ == '__main__':
    unittest.main()
