import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# `--five-hour N`, `--week N` and `--order WORD` on `switchboard auto check` and `switchboard auto why`: the lines and
# the order for this one check, checked as the page checks them, and never stored.
import dataclasses
import io
import json
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import autocheck
from switchboard.accounts import autoloop, policy, usage
from fakes_autoswitch import set_policy
from test_auto_check import CheckCase, ok
from test_autoswitch_account_rules import SEED, generated


class ForThisCheckOnly(CheckCase):
    def setUp(self):
        super().setUp()
        self.answers(ok(87.0, 70.0), ok(10.0, 20.0), ok(10.0, 20.0))
        loop = autoloop.Loop(deps=self.deps(), clock=self.clock)
        loop.poller.poll(usage.Context(active='a1', order=('a1', 'a2', 'a3'), lines={'5-hour': 90.0, 'weekly': 95.0}))
        set_policy(strategy='most-room')
        self.stored = self.settings_bytes()

    def run_cli(self, *args):
        out = io.StringIO()
        code = autocheck.main(list(args), deps=self.deps(), clock=self.clock, out=out)
        return code, out.getvalue()

    def test_a_lower_5_hour_line_for_one_look(self):
        self.assertEqual(self.run_cli('why')[0], autoloop.EXIT_NOTHING)
        for args in (('why', '--five-hour', '85'), ('why', '--five-hour=85'),
                     ('check', '--dry-run', '--five-hour', '85')):
            code, out = self.run_cli(*args)
            self.assertEqual(code, autoloop.EXIT_SWITCHED, args)
            self.assertIn('a1@acct.example 5-hour at 87% (line 85%)', out)
            self.assertIn('For this check only: 5-hour line 85%.', out)
        self.assertEqual(self.settings_bytes(), self.stored)
        self.assertEqual(autoloop.load_settings().session_at, 90.0)

    def test_a_week_line_and_an_order_for_one_look_as_json(self):
        out = io.StringIO()
        code = autocheck.main(['why', '--week', '60', '--order', 'refills-first', '--json'], deps=self.deps(),
                              clock=self.clock, out=out)
        doc = json.loads(out.getvalue())
        self.assertEqual(code, autoloop.EXIT_SWITCHED)
        self.assertEqual(doc['settings'], {'auto_switch_on': True, 'order': 'refills-first', 'five_hour_line': 90,
                                           'week_line': 60, 'model_line': 95, 'model': None,
                                           'this_check_only': ['week_line', 'order'], 'own_lines': [], 'ranks': []})
        self.assertIn('weekly at 70% (line 60%)', doc['said'])
        self.assertEqual(self.settings_bytes(), self.stored)

    def test_a_real_check_with_a_line_of_its_own_moves_and_stores_nothing(self):
        code, out = self.run_cli('check', '--five-hour', '85')
        self.assertEqual(code, autoloop.EXIT_SWITCHED)
        self.assertEqual(self.engine.switches[0]['to'], 'a2')
        self.assertEqual(self.settings_bytes(), self.stored)
        self.assertEqual(autoloop.ONE_CHECK, [])
        self.assertEqual(policy.lines_for(autoloop.load_settings()), {'5-hour': 90.0, 'weekly': 95.0})

    def test_a_value_out_of_bounds_or_not_a_number_exits_2(self):
        forms = '\n'.join(autocheck.FORMS) + '\n'
        for args, said in ((('--five-hour', '49'), '--five-hour takes a number from 50 to 99.9.'),
                           (('--week', '100'), '--week takes a number from 50 to 99.9.'),
                           (('--week', 'nan'), '--week takes a number from 50 to 99.9.'),
                           (('--five-hour', 'ninety'), '--five-hour takes a number from 50 to 99.9.'),
                           (('--five-hour=',), '--five-hour takes a number from 50 to 99.9.'),
                           (('--five-hour',), '--five-hour needs a number.'),
                           (('--order',), '--order needs refills-first, most-room or stay-then-refills.'),
                           (('--order', 'consume'), '--order takes refills-first, most-room or stay-then-refills.')):
            for word in ('why', 'check'):
                with self.subTest(args=args, word=word):
                    self.assertEqual(self.run_cli(word, *args), (2, '%s\n%s' % (said, forms)))
        self.assertEqual(self.engine.switches, [])
        self.assertEqual(self.settings_bytes(), self.stored)


class ManySnapshots(unittest.TestCase):
    def test_with_lines_and_an_order_of_its_own_it_decides_as_policy_does_with_them(self):
        rng = random.Random(SEED + 1)
        for s in generated(SEED + 1, 2000):
            args = ['why']
            change = {'enabled': True}
            if rng.random() < 0.5:
                change['session_at'] = rng.choice([50.0, 70.5, 85.0, 99.9])
                args += ['--five-hour', str(change['session_at'])]
            if rng.random() < 0.5:
                change['weekly_at'] = rng.choice([50.0, 80.0, 99.0])
                args += ['--week=%s' % change['weekly_at']]
            if rng.random() < 0.5:
                change['strategy'] = rng.choice(policy.STRATEGIES)
                args += ['--order', change['strategy']]
            opts = autocheck.parse(args)
            used = autocheck.check_settings(opts, s.settings)
            self.assertEqual(used, dataclasses.replace(s.settings, **change))
            d = policy.decide(dataclasses.replace(s, settings=used))
            code, doc = autocheck.judge(opts, dataclasses.replace(s, settings=used), s.settings)
            self.assertEqual(doc['action'], d.action)
            self.assertEqual(doc['to'], next((a.email for a in s.accounts if a.id == d.target), d.target)
                             if d.target else None)


if __name__ == '__main__':
    unittest.main()
