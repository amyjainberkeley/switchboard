import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# Where an account's own switch lines, its rank and the counted models are kept: with auto-switch's own settings
# ('auto_policy' in settings.json), as 'own_lines': {account id: {'session_at': N, 'weekly_at': N}}, 'ranks':
# {account id: N} and 'model' (one name, as before, or a list of names). None set is no entry, and none at all is no
# key, so a line or rank set and taken away leaves the settings file as it was, byte for byte. A damaged value, or one
# for an account that is no longer saved, reads as not set and is dropped when the settings are next written. Saving the
# same thing twice changes nothing. Made-up accounts only.
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import core
from switchboard.accounts import api, autoloop, policy
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

    def test_own_lines_are_kept_merged_read_back_and_taken_away(self):
        got = autoloop.save_settings(line=('a2', {'session_at': 85}))
        self.assertEqual(got['own_lines'], {'a2': {'session_at': 85.0}})
        autoloop.save_settings(line=('a2', {'weekly_at': 90.5}))
        autoloop.save_settings(line=('a3', {'weekly_at': 70}))
        self.assertEqual(core.settings()['auto_policy'], {'own_lines': {'a2': {'session_at': 85.0, 'weekly_at': 90.5},
                                                                        'a3': {'weekly_at': 70.0}}})
        self.assertEqual(autoloop.load_settings().own, (('a2', (('session_at', 85.0), ('weekly_at', 90.5))),
                                                        ('a3', (('weekly_at', 70.0),))))
        self.assertEqual(api.auto_settings()['own_lines']['a3'], {'weekly_at': 70.0})
        autoloop.save_settings(line=('a2', {'session_at': None}))           # one line back on the shared one
        self.assertEqual(autoloop.settings_dict()['own_lines'], {'a2': {'weekly_at': 90.5}, 'a3': {'weekly_at': 70.0}})
        autoloop.save_settings(line=('a2', None))                           # every line back on the shared ones
        self.assertEqual(autoloop.settings_dict()['own_lines'], {'a3': {'weekly_at': 70.0}})

    def test_ranks_are_kept_read_back_and_taken_away(self):
        autoloop.save_settings(rank=('a3', 1))
        got = autoloop.save_settings(rank=('a2', 2))
        self.assertEqual(got['ranks'], {'a2': 2, 'a3': 1})
        self.assertEqual(autoloop.load_settings().ranks, (('a2', 2), ('a3', 1)))
        autoloop.save_settings(rank=('a3', None))
        self.assertEqual(core.settings()['auto_policy'], {'ranks': {'a2': 2}})

    def test_several_models_are_kept_as_a_list_and_one_as_before(self):
        got = autoloop.save_settings(model='Fable')
        self.assertEqual(core.settings()['auto_policy']['model'], 'Fable')
        self.assertNotIn('models', got)
        for given in (['Fable', 'Opus 5'], 'Fable, Opus 5', ' Fable ,Opus  5,, fable'):
            with self.subTest(given=given):
                got = autoloop.save_settings(model=given)
                self.assertEqual(core.settings()['auto_policy']['model'], ['Fable', 'Opus 5'])
                self.assertEqual((got['model'], got['models']), ('Fable', ['Fable', 'Opus 5']))
                self.assertEqual(autoloop.load_settings().models, ('Fable', 'Opus 5'))
        for clear in ('', []):
            with self.subTest(clear=clear):
                autoloop.save_settings(model='Fable, Opus 5')
                autoloop.save_settings(model=clear)
                self.assertNotIn('model', core.settings().get('auto_policy') or {})

    def test_setting_and_taking_away_leaves_the_settings_file_as_it_was_byte_for_byte(self):
        core.set_setting('notify', True)
        for before in (None, {'strategy': 'most-room', 'weekly_at': 93.0, 'rules': {'a1': 'last'}}):
            with self.subTest(before=before):
                if before is not None:
                    autoloop.save_settings(strategy='most-room', weekly_at=93, rule=('a1', 'last'))
                was = settings_bytes()
                autoloop.save_settings(line=('a2', {'session_at': 80, 'weekly_at': 90}))
                autoloop.save_settings(rank=('a3', 4))
                self.assertNotEqual(settings_bytes(), was)
                autoloop.save_settings(rank=('a3', None))
                autoloop.save_settings(line=('a2', {'session_at': None, 'weekly_at': None}))
                self.assertEqual(settings_bytes(), was)

    def test_saving_twice_changes_nothing(self):
        for kw in ({'line': ('a2', {'session_at': 80})}, {'rank': ('a2', 3)}, {'model': 'Fable, Opus 5'},
                   {'line': ('a2', None)}, {'rank': ('a3', None)}, {'strategy': 'stay-then-refills'}):
            with self.subTest(kw=kw):
                autoloop.save_settings(**kw)
                was = settings_bytes()
                autoloop.save_settings(**kw)
                self.assertEqual(settings_bytes(), was)

    def test_a_wrong_value_is_refused_and_nothing_is_written(self):
        core.set_setting('notify', True)
        was = settings_bytes()
        lines = 'Pick a number from 50 to 99.9.'
        for kw, kind, words in (
                ({'line': ('nobody', {'session_at': 80})}, 'not_found', 'That account is not saved in Switchboard.'),
                ({'rank': ('nobody', 2)}, 'not_found', 'That account is not saved in Switchboard.'),
                ({'line': ('a2', {'session_at': 120})}, 'other', lines),
                ({'line': ('a2', {'session_at': 49})}, 'other', lines),
                ({'line': ('a2', {'session_at': True})}, 'other', lines),
                ({'line': ('a2', {'session_at': float('nan')})}, 'other', lines),
                ({'line': ('a2', {'model_at': 80})}, 'other', 'Give an account its own 5-hour or weekly line.'),
                ({'line': ('a2', {})}, 'other', 'Give an account its own 5-hour or weekly line.'),
                ({'line': ('a2', 80)}, 'other', 'Give an account its own 5-hour or weekly line.'),
                ({'line': 'a2'}, 'other', 'Give an account its own 5-hour or weekly line.'),
                ({'rank': ('a2', 0)}, 'other', 'Pick a whole number from 1 to 99.'),
                ({'rank': ('a2', 100)}, 'other', 'Pick a whole number from 1 to 99.'),
                ({'rank': ('a2', 1.5)}, 'other', 'Pick a whole number from 1 to 99.'),
                ({'rank': ('a2', True)}, 'other', 'Pick a whole number from 1 to 99.'),
                ({'rank': ('a2', '2')}, 'other', 'Pick a whole number from 1 to 99.'),
                ({'rank': 2}, 'other', 'Pick a whole number from 1 to 99.'),
                ({'model': 'Fable, <b>'}, 'other', 'That is not a model name (letters, digits and spaces, like '
                                                   'Fable).'),
                ({'model': ['Fable', 7]}, 'other', 'That is not a model name (letters, digits and spaces, like '
                                                   'Fable).'),
                ({'strategy': 'consume'}, 'other', 'Pick refills-first, most-room or stay-then-refills.')):
            with self.subTest(kw=kw):
                with self.assertRaises(api.AccountsError) as e:
                    autoloop.save_settings(**kw)
                self.assertEqual((e.exception.kind, str(e.exception)), (kind, words))
        self.assertEqual(settings_bytes(), was)

    def test_damaged_values_and_gone_accounts_read_as_not_set_and_go_at_the_next_write(self):
        core.set_setting('auto_policy', {'own_lines': {'a2': {'session_at': 'x', 'five': 80}, 'gone': {'weekly_at': 80},
                                                       'a3': {'weekly_at': 75}},
                                         'ranks': {'a2': 0, 'gone': 1, 'a3': 2, 'a1': 'first'}, 'model': [7]})
        st = autoloop.load_settings()
        self.assertEqual((st.own, st.ranks, st.model, st.models),
                         ((('a3', (('weekly_at', 75.0),)), ('gone', (('weekly_at', 80.0),))),
                          (('a3', 2), ('gone', 1)), None, ()))
        autoloop.save_settings(weekly_at=93)
        self.assertEqual(core.settings()['auto_policy'], {'own_lines': {'a3': {'weekly_at': 75.0}}, 'ranks': {'a3': 2},
                                                          'model': [7], 'weekly_at': 93.0})
        # the model, as before, is left as it is stored until it is set again: it reads as none

    def test_removing_an_account_takes_its_lines_and_rank_with_it(self):
        autoloop.save_settings(line=('a2', {'weekly_at': 80}), rank=('a2', 1))
        autoloop.save_settings(rank=('a3', 2))
        autoloop.forget_rules(['a2'])
        self.assertEqual(core.settings()['auto_policy'], {'ranks': {'a3': 2}})
        autoloop.forget_rules(['a3'])
        self.assertNotIn('auto_policy', core.settings())

    def test_a_removal_that_lands_first_wins(self):
        def gone():
            return [a for a in self.engine.accounts() if a.id != 'a2']
        with mock.patch.object(api, 'accounts', side_effect=[self.engine.accounts(), gone()]):
            with self.assertRaises(api.AccountsError) as e:
                autoloop.save_settings(rank=('a2', 1))
        self.assertEqual(e.exception.kind, 'not_found')
        self.assertNotIn('auto_policy', core.settings())

    def test_through_the_api(self):
        got = api.set_auto(line=('a2', {'weekly_at': 80}), rank=('a3', 1), model=['Fable', 'Opus 5'])
        self.assertEqual((got['own_lines'], got['ranks'], got['models']),
                         ({'a2': {'weekly_at': 80.0}}, {'a3': 1}, ['Fable', 'Opus 5']))
        self.assertEqual(api.set_auto(rank=('a3', None), line=('a2', None))['model'], 'Fable')
        self.assertEqual(core.settings()['auto_policy'], {'model': ['Fable', 'Opus 5']})

    def test_the_policy_reads_what_the_store_keeps(self):
        autoloop.save_settings(line=('a2', {'session_at': 80}), rank=('a2', 1), model='Fable, Opus 5',
                               strategy='stay-then-refills')
        self.assertEqual(autoloop.load_settings(), policy.Settings(
            own=(('a2', (('session_at', 80.0),)),), ranks=(('a2', 1),), model='Fable', models=('Fable', 'Opus 5'),
            strategy='stay-then-refills'))


if __name__ == '__main__':
    unittest.main()
