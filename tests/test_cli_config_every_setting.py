import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# `switchboard config` knows every setting something reads: the three kept in the settings file by their own modules
# (run_history, update_check, network_trust) are rows that `config set` sets through each module's own setter and
# `config unset` puts back; each account's own lines and rank are shown and name their own commands; several counted
# models are shown and taken; the order's words name every order. A key something reads is never "read by nothing".
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import config, core, engine
from test_cli_commands import Cli, A, B

# Each setting kept by a module of its own: (key, its default, another value).
FILE_SETTINGS = (('run_history', 'shared', 'apart'), ('update_check', 'off', 'daily'),
                 ('network_trust', 'mac', 'python'))


class NothingIsReadByNothing(unittest.TestCase):
    def test_the_keys_the_new_commands_write_are_known(self):
        s = {'auto_policy': {'own_lines': {'x': {'session_at': 80}}, 'ranks': {'x': 1}},
             'update_check': 'daily', 'network_trust': 'python', 'run_history': 'apart'}
        self.assertEqual(config.not_read(s), [])


class FileSettings(Cli):
    def test_each_is_a_row_that_config_sets_gets_and_unsets(self):
        self.two()
        for key, default, other in FILE_SETTINGS:
            with self.subTest(key=key):
                self.assertIn(key, config.BY_KEY)
                self.assertEqual(self.run_cli('config', 'get', key), (0, default + '\n', ''))
                self.assertEqual(self.run_cli('config', 'set', key, other),
                                 (0, '%s is %s now (it was %s).\n' % (key, other, default), ''))
                self.assertEqual(core.settings().get(key), other)              # the stored key, as the module reads it
                self.assertEqual(self.run_cli('config', 'get', key), (0, other + '\n', ''))
                self.assertEqual(self.run_cli('config', 'set', key, other),
                                 (0, '%s is %s already.\n' % (key, other), ''))
                self.assertEqual(self.run_cli('config', 'unset', key)[0], 0)
                self.assertEqual(self.run_cli('config', 'get', key), (0, default + '\n', ''))

    def test_each_is_read_by_its_own_module(self):
        from switchboard import foldercmd, newer, trust
        self.two()
        for key, _, other in FILE_SETTINGS:
            self.run_cli('config', 'set', key, other)
        self.assertEqual((foldercmd.history_setting(), newer.check_setting(), trust.mode()),
                         ('apart', 'daily', 'python'))

    def test_a_value_it_does_not_take_is_refused_naming_the_key(self):
        self.two()
        self.assertEqual(self.run_cli('config', 'set', 'update_check', 'weekly')[0],
                         'refused: update_check takes off or daily.')
        self.assertNotIn('update_check', core.settings())

    def test_listed_with_their_default_and_as_commands(self):
        self.two()
        self.run_cli('config', 'set', 'run_history', 'apart')
        code, out, _ = self.run_cli('config')
        self.assertEqual(code, 0)
        self.assertNotIn('nothing reads it', out)
        line = next(x for x in out.splitlines() if x.startswith('  run_history '))
        self.assertIn('apart', line)
        self.assertIn('default shared', line)
        code, out, _ = self.run_cli('config', '--as-commands')
        self.assertIn('switchboard config set run_history apart\n', out)


class PerAccount(Cli):
    def test_own_lines_and_ranks_are_shown_and_name_their_commands(self):
        a, b = self.two()
        self.fake.auto['own_lines'] = {b: {'session_at': 80, 'weekly_at': 90}}
        self.fake.auto['ranks'] = {b: 1}
        self.assertEqual(self.run_cli('config', 'get', 'auto_policy.own_lines'),
                         (0, '%s 80%% 5-hour · 90%% weekly\n' % B, ''))
        self.assertEqual(self.run_cli('config', 'get', 'auto_policy.ranks'), (0, '%s rank 1\n' % B, ''))
        self.assertEqual(self.run_cli('config', 'set', 'auto_policy.ranks', '2')[0],
                         'refused: auto_policy.ranks is set with switchboard auto rank NAME N|off.')
        self.assertEqual(self.run_cli('config', 'set', 'auto_policy.own_lines', '80')[0],
                         'refused: auto_policy.own_lines is set with switchboard auto line NAME [--five-hour N] '
                         '[--week N].')
        code, out, _ = self.run_cli('config', '--as-commands')
        self.assertIn('switchboard auto line %s --five-hour 80 --week 90\n' % B, out)
        self.assertIn('switchboard auto rank %s 1\n' % B, out)
        code, out, _ = self.run_cli('config')
        self.assertNotIn('nothing reads it', out)

    def test_two_accounts_with_one_email_are_two_names(self):
        a = self.fake.hold(A, 'org-w', org_name='Northwind')
        b = self.fake.hold(A, 'org-p', org_name='Pinecone')
        self.fake.sign_in(A, 'org-w')
        self.fake.auto['rules'] = {a: 'last', b: 'out'}
        self.fake.auto['ranks'] = {a: 2, b: 1}
        self.assertEqual(self.run_cli('config', 'get', 'auto_policy.rules'),
                         (0, '%s (Northwind) last, %s (Pinecone) out\n' % (A, A), ''))
        self.assertEqual(self.run_cli('config', 'get', 'auto_policy.ranks'),
                         (0, '%s (Northwind) rank 2, %s (Pinecone) rank 1\n' % (A, A), ''))
        code, out, _ = self.run_cli('config', '--as-commands')
        self.assertIn("switchboard auto out '%s (Pinecone)'\n" % A, out)
        self.assertIn("switchboard auto rank '%s (Northwind)' 2\n" % A, out)


class Models(Cli):
    def test_several_counted_models_are_shown_and_taken(self):
        self.two()
        self.assertEqual(self.run_cli('config', 'set', 'auto_policy.model', 'Fable,Opus'),
                         (0, 'auto_policy.model is Fable,Opus now (it was none).\n', ''))
        self.assertEqual(self.run_cli('config', 'get', 'auto_policy.model'), (0, 'Fable,Opus\n', ''))
        code, out, _ = self.run_cli('config', '--as-commands')
        self.assertIn('switchboard config set auto_policy.model Fable,Opus\n', out)
        self.assertEqual(self.run_cli('config', 'set', 'auto_policy.model', 'Fable'),
                         (0, 'auto_policy.model is Fable now (it was Fable,Opus).\n', ''))


class Orders(unittest.TestCase):
    def test_the_orders_words_name_every_order(self):
        words = config.BY_KEY['auto_policy.strategy'].words
        self.assertEqual(len(engine.STRATEGIES), 3)
        for said in ('Refills first', 'Most room', 'Stay, then refills first'):
            self.assertIn(said, words)


if __name__ == '__main__':
    unittest.main()
