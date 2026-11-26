import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# `switchboard auto check --json` and `switchboard auto why --json`: one JSON document on standard output and nothing
# else, also when the command refuses. Every key is pinned here: removing or renaming one raises `schema`.
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import autocheck, core, engine
from switchboard.accounts import autoloop, usage
from fakes_autoswitch import set_policy
from test_auto_check import CheckCase, T0, ok

KEYS = ['schema', 'dry_run', 'checked_at', 'outcome', 'exit_code', 'said', 'action', 'from', 'to', 'from_number',
        'to_number', 'forced',
        'reasons', 'waiting_for', 'in_use', 'candidates', 'first_refill', 'would_change', 'settings',
        'oldest_numbers_s', 'loop', 'problem']
ACCOUNT = ['email', 'number', 'organization', 'limits', 'checked_at', 'age_s']
CANDIDATE = ACCOUNT + ['can_take_it', 'why_not', 'words']
LIMIT = ['limit', 'model', 'used_percent', 'line', 'points_left']
SETTINGS = ['auto_switch_on', 'order', 'five_hour_line', 'week_line', 'model_line', 'model', 'this_check_only',
            'own_lines', 'ranks']
WHEN = r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$'


class Json(CheckCase):
    def run_json(self, *args):
        out, err = io.StringIO(), io.StringIO()
        code = autocheck.main(list(args), deps=self.deps(), clock=self.clock, out=out, err=err)
        text = out.getvalue()
        doc = json.loads(text)
        self.assertEqual(text, json.dumps(doc, indent=1, ensure_ascii=False) + '\n')     # one document, nothing else
        self.assertEqual(list(doc), KEYS)
        self.assertEqual(doc['schema'], 1)
        self.assertEqual(doc['exit_code'], code)
        self.assertRegex(doc['checked_at'], WHEN)
        return code, doc, err.getvalue()

    def test_why_as_json(self):
        self.answers(ok(92.0, 30.0), ok(10.0, 20.0), ok(10.0, 20.0, week_reset=T0 + 6 * 86400))
        loop = autoloop.Loop(deps=self.deps(), clock=self.clock)
        loop.poller.poll(usage.Context(active='a1', order=('a1', 'a2', 'a3'), lines={'5-hour': 90.0, 'weekly': 95.0}))
        self.clock.advance(30)
        code, doc, err = self.run_json('why', '--json')
        self.assertEqual(code, autoloop.EXIT_SWITCHED)
        self.assertEqual(err, '')
        self.assertEqual((doc['dry_run'], doc['outcome'], doc['action'], doc['from'], doc['to'], doc['forced']),
                         (True, 'would_switch', 'switch', 'a1@acct.example', 'a2@acct.example', True))
        self.assertTrue(doc['said'].startswith('Would switch Claude Code to a2@acct.example: '))
        self.assertEqual(list(doc['in_use']), ACCOUNT)
        self.assertEqual(doc['in_use']['email'], 'a1@acct.example')
        self.assertRegex(doc['in_use']['checked_at'], WHEN)
        self.assertGreaterEqual(doc['in_use']['age_s'], 30)
        self.assertEqual([list(x) for x in doc['in_use']['limits']], [LIMIT, LIMIT])
        self.assertEqual(doc['in_use']['limits'][0], {'limit': 'five_hour', 'model': None, 'used_percent': 92,
                                                      'line': 90, 'points_left': -2})
        self.assertEqual([list(c) for c in doc['candidates']], [CANDIDATE, CANDIDATE])
        self.assertEqual([(c['email'], c['can_take_it'], c['why_not']) for c in doc['candidates']],
                         [('a2@acct.example', True, None), ('a3@acct.example', True, None)])
        self.assertEqual(list(doc['settings']), SETTINGS)
        self.assertEqual(doc['settings'], {'auto_switch_on': True, 'order': 'refills-first', 'five_hour_line': 90,
                                           'week_line': 95, 'model_line': 95, 'model': None, 'this_check_only': [],
                                           'own_lines': [], 'ranks': []})
        self.assertEqual(doc['reasons'][0][:8], 'In use: ')
        self.assertGreaterEqual(doc['oldest_numbers_s'], 30)
        self.assertIsNone(doc['loop'])
        self.assertIsNone(doc['problem'])

    def test_a_check_that_moves_as_json(self):
        set_policy(enabled=False)
        self.answers(ok(92.0, 30.0), ok(10.0, 20.0), ok(10.0, 20.0))
        code, doc, err = self.run_json('check', '--json')
        self.assertEqual(code, autoloop.EXIT_SWITCHED)
        self.assertEqual((doc['dry_run'], doc['outcome'], doc['to']), (False, 'switched', 'a2@acct.example'))
        self.assertIs(doc['settings']['auto_switch_on'], False)
        self.assertNotIn('Auto-switch is off', ' '.join(doc['reasons']))
        self.assertEqual(err, '')

    def test_a_check_while_the_loop_runs_as_json(self):
        lease = autoloop.Lease()
        self.assertTrue(lease.acquire(self.clock))
        self.addCleanup(lease.release)
        usage.write_atomic(core.data_path(autoloop.HEARTBEAT), {
            'v': 1, 'pid': os.getpid(), 'boot': self.clock.boot(), 'last_pass_at': T0 - 60,
            'decision': {'action': 'stay', 'reason': 'a1@acct.example is under its lines.'}})
        code, doc, _ = self.run_json('check', '--json')
        self.assertEqual(code, autoloop.EXIT_BUSY)
        self.assertEqual(doc['outcome'], 'loop_running')
        self.assertEqual(doc['loop'], {'running': True, 'pid': os.getpid(), 'decided_at': autocheck.iso(T0 - 60),
                                       'said': 'a1@acct.example is under its lines.'})

    def test_a_wrong_word_is_a_document_too_and_exits_2(self):
        for args in (['check', '--json', '--made-up'], ['why', '--made-up', '--json']):
            code, doc, _ = self.run_json(*args)
            self.assertEqual(code, 2)
            self.assertEqual((doc['outcome'], doc['problem']), ('refused', 'Unknown word: --made-up.'))

    def test_a_refusal_is_a_document_too(self):
        real = engine.foreign_switcher
        engine.foreign_switcher = lambda folder=None: 'com.example.swapper'
        self.addCleanup(setattr, engine, 'foreign_switcher', real)
        code, doc, _ = self.run_json('check', '--json')
        self.assertEqual(code, autoloop.EXIT_ERROR)
        self.assertIn('com.example.swapper', doc['problem'])
        core.DEMO = True
        self.addCleanup(setattr, core, 'DEMO', False)
        code, doc, _ = self.run_json('why', '--json')
        self.assertEqual(code, autoloop.EXIT_ERROR)
        self.assertEqual(doc['problem'], 'The demo has made-up accounts; try auto-switch on its page.')


if __name__ == '__main__':
    unittest.main()
