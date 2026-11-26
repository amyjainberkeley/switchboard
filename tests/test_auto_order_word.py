import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The stored word for automatic switching's default order is 'refills-first', the page's "Refills first". A word no
# reader knows (a settings file written by hand, or by another build) reads as that default order at every reader:
# the policy, the settings store, the app's engine side, the page's plan, plan.py, the terminal, the demo and the page.
# Made-up accounts only.
import os
import shutil
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import core, demo, engine, plan, server, term
from switchboard.accounts import api, autoloop, policy
from fakes_accounts_api import install

UNKNOWN = 'made-up-order'           # never a word any build stored


class TheWord(unittest.TestCase):
    def test_every_list_of_orders_starts_with_refills_first(self):
        for where in (policy.STRATEGIES, plan.STRATEGIES, engine.STRATEGIES):
            self.assertEqual(where, ('refills-first', 'most-room', 'stay-then-refills'))
        self.assertEqual(policy.DEFAULTS['strategy'], 'refills-first')
        self.assertEqual(policy.Settings().strategy, 'refills-first')
        self.assertEqual(plan.DEFAULTS['strategy'], 'refills-first')
        self.assertEqual(engine.DEFAULT_STRATEGY, 'refills-first')

    def test_a_wrong_word_is_refused_in_one_sentence_naming_both(self):
        with self.assertRaises(api.AccountsError) as e:
            autoloop.save_settings(strategy=UNKNOWN)
        self.assertEqual(str(e.exception), 'Pick refills-first, most-room or stay-then-refills.')


class AnUnknownWordReadsAsTheDefault(unittest.TestCase):
    def setUp(self):
        self.data = core.data_dir()
        shutil.rmtree(self.data, ignore_errors=True)
        os.makedirs(self.data)

    def test_the_policy(self):
        self.assertEqual(policy.settings_from({'strategy': UNKNOWN}).strategy, 'refills-first')

    def test_the_settings_store(self):
        core.set_setting(autoloop.POLICY_KEY, {'strategy': UNKNOWN, 'weekly_at': 93})
        self.assertEqual(autoloop.load_settings().strategy, 'refills-first')
        self.assertEqual(autoloop.settings_dict()['strategy'], 'refills-first')
        self.assertEqual(autoloop.settings_dict()['weekly_at'], 93.0)         # the rest is still read

    def test_the_apps_engine_side(self):
        fake = install(self)
        fake.auto['strategy'] = UNKNOWN
        with mock.patch.object(engine.HOST, 'status', return_value={'running': False, 'on': False, 'starting': False,
                                                                     'problem': None, 'stage': None,
                                                                     'waiting_for': None, 'restarted': None}):
            self.assertEqual(engine.auto_settings()['strategy'], 'refills-first')

    def test_the_pages_plan(self):
        self.assertEqual(server.plan_settings({'strategy': UNKNOWN})['strategy'], 'refills-first')

    def test_plan_py(self):
        self.assertEqual(plan._settings({'strategy': UNKNOWN})['strategy'], 'refills-first')
        now = 1_800_000_000
        accounts = [{'name': n, 'weekly_used_pct': used, 'weekly_resets_at': now + days * 86400, 'scoped': [],
                     'active': n == 'a'} for n, used, days in (('a', 10, 1), ('b', 60, 2), ('c', 5, 6))]
        names = [j['name'] for j in plan.order(accounts, {'strategy': UNKNOWN}, now)]
        self.assertEqual(names, [j['name'] for j in plan.order(accounts, {'strategy': 'refills-first'}, now)])
        self.assertEqual(names, ['a', 'b', 'c'])                    # b refills sooner than c, though c has more room

    def test_the_terminal(self):
        on = {'enabled': True, 'session_at': 90, 'weekly_at': 95}
        self.assertEqual(term.auto_line(dict(on, strategy=UNKNOWN)), term.auto_line(dict(on, strategy='refills-first')))
        self.assertIn('using what refills first', term.auto_line(dict(on, strategy=UNKNOWN)))

    def test_the_demo_keeps_its_order_on_an_unknown_word(self):
        core.DEMO = True
        self.addCleanup(setattr, core, 'DEMO', False)
        demo.STATE.clear()
        demo.FACTS.clear()
        self.addCleanup(demo.STATE.clear)
        self.addCleanup(demo.FACTS.clear)
        self.assertEqual(demo.view()['auto']['strategy'], 'refills-first')
        demo.act('/api/auto', {'strategy': UNKNOWN})
        self.assertEqual(demo.view()['auto']['strategy'], 'refills-first')

    def test_the_page(self):
        with open(os.path.join(HERE, '..', 'switchboard', 'web', 'index.html'), encoding='utf-8') as f:
            html = f.read()
        # the Order pick and the settings it copies both read through orderOf, which knows only the page's orders
        self.assertIn("const orderOf = (w) => (ORDERS.some((o) => o[0] === w) ? w : ORDERS[0][0]);", html)
        self.assertIn("pick('Order', 'autoPick', 'strategy', ORDERS, orderOf(au().strategy))", html)
        self.assertIn('p.strategy = orderOf(a.strategy);', html)
        self.assertNotIn("au().strategy ||", html)


if __name__ == '__main__':
    unittest.main()
