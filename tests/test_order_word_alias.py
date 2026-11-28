import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# An order word this build does not know, such as one an earlier copy may have stored. This build takes no older word
# (ORDER_ALIASES is empty in both places). Wherever a setting is read (policy.settings_from, and so the settings store
# and the engine's settings), a word it does not know reads as the default order, refills first. Wherever one is set
# (engine.set_auto, autoloop.save_settings, `auto check --order`), a word it does not know is refused. OLD is a
# made-up older word. Made-up accounts only.
import os
import shutil
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import autocheck, core, engine
from switchboard.accounts import api, autoloop, policy
from fakes_accounts_api import install

OLD = 'an-older-order'
HOST = {'running': False, 'on': False, 'starting': False, 'problem': None, 'stage': None, 'waiting_for': None,
        'restarted': None}


class TheWords(unittest.TestCase):
    def test_no_older_word_is_taken_and_both_lists_agree(self):
        self.assertEqual(policy.ORDER_ALIASES, {})
        self.assertEqual(engine.ORDER_ALIASES, policy.ORDER_ALIASES)
        self.assertNotIn(OLD, policy.STRATEGIES)
        self.assertEqual(policy.DEFAULTS['strategy'], 'refills-first')
        self.assertEqual(engine.DEFAULT_STRATEGY, 'refills-first')

    def test_every_word_stays_as_it_is(self):
        for word in policy.STRATEGIES + (OLD, 'made-up-order', '', None, 3, [OLD]):
            self.assertEqual(policy.order_word(word), word)
            self.assertEqual(engine.order_word(word), word)


class WhereASettingIsRead(unittest.TestCase):
    def setUp(self):
        self.data = core.data_dir()
        shutil.rmtree(self.data, ignore_errors=True)
        os.makedirs(self.data)

    def test_the_policy_reads_it_as_refills_first(self):
        self.assertEqual(policy.settings_from({'strategy': OLD}).strategy, 'refills-first')
        # the same settings, so every decision on them is the same (policy.decide is pure)
        self.assertEqual(policy.settings_from({'strategy': OLD, 'enabled': True, 'weekly_at': 93}),
                         policy.settings_from({'strategy': 'refills-first', 'enabled': True, 'weekly_at': 93}))

    def test_the_settings_store_reads_it_and_the_rest_as_stored(self):
        core.set_setting(autoloop.POLICY_KEY, {'strategy': OLD, 'weekly_at': 93, 'stay_signed_in': False})
        self.assertEqual(autoloop.load_settings().strategy, 'refills-first')
        self.assertEqual(autoloop.settings_dict()['strategy'], 'refills-first')
        self.assertEqual(autoloop.settings_dict()['weekly_at'], 93.0)
        self.assertFalse(autoloop.load_settings().stay_signed_in)
        self.assertNotIn('stay_signed_in', autoloop.settings_dict())       # never among the page's settings

    def test_the_apps_engine_side_says_refills_first(self):
        fake = install(self)
        fake.auto['strategy'] = OLD
        with mock.patch.object(engine.HOST, 'status', return_value=dict(HOST)):
            self.assertEqual(engine.auto_settings()['strategy'], 'refills-first')


class WhereASettingIsSet(unittest.TestCase):
    def setUp(self):
        self.data = core.data_dir()
        shutil.rmtree(self.data, ignore_errors=True)
        os.makedirs(self.data)

    def test_the_settings_store_refuses_it_and_keeps_what_was_stored(self):
        autoloop.save_settings(strategy='most-room')
        with self.assertRaises(api.AccountsError):
            autoloop.save_settings(strategy=OLD)
        self.assertEqual(core.settings()[autoloop.POLICY_KEY]['strategy'], 'most-room')
        self.assertEqual(autoloop.load_settings().strategy, 'most-room')

    def test_the_engine_refuses_it(self):
        with self.assertRaises(core.SwitchError):
            engine.set_auto(strategy=OLD)

    def test_the_engine_hands_on_an_order_of_today(self):
        fake = install(self)
        with mock.patch.object(engine.HOST, 'ensure'):
            engine.set_auto(strategy='refills-first')
        self.assertEqual([c for c in fake.calls if c[0] == 'set_auto'][-1], ('set_auto', {'strategy': 'refills-first'}))

    def test_one_check_refuses_it_and_takes_an_order_of_today(self):
        with self.assertRaises(autocheck.Wrong):
            autocheck.parse(['why', '--order', OLD])
        self.assertEqual(autocheck.parse(['check', '--order=refills-first'])['order'], 'refills-first')


if __name__ == '__main__':
    unittest.main()
