import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# The rule every router adapter applies (switchboard/router/resolve.py), held to tests/router_fixtures.json: the one
# file every other way of reading a plan (the Claude Code plugin's) is held to as well. The file's answers are
# written by hand from the plan's contract, not made by this code.
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))

from switchboard.providers import contract, job, kinds  # noqa: E402
from switchboard.router import resolve  # noqa: E402

FIXTURES = os.path.join(HERE, 'router_fixtures.json')


def load():
    with open(FIXTURES, encoding='utf-8') as f:
        return json.load(f)


def resolve_cases():
    return [x for x in load() if 'plan' in x]


def effort_cases():
    return [x for x in load() if 'current' in x]


def kind_cases():
    return [x for x in load() if 'want_kind' in x]


class TheFile(unittest.TestCase):
    def test_enough_cases_each_named_once(self):
        cases = load()
        self.assertGreaterEqual(len(resolve_cases()), 45)
        self.assertGreaterEqual(len(kind_cases()), 25)
        self.assertGreaterEqual(len([x for x in resolve_cases() if 'where' in x]), 20)
        self.assertGreaterEqual(len(effort_cases()), 15)
        names = [x['name'] for x in cases]
        self.assertEqual(len(names), len(set(names)))
        for x in cases:
            with self.subTest(name=x['name']):
                self.assertTrue(isinstance(x['name'], str) and len(x['name'].split()) >= 3)
                keys = set(x)
                self.assertIn(keys, ({'name', 'plan', 'kind', 'here', 'now', 'want'},
                                     {'name', 'plan', 'kind', 'here', 'now', 'where', 'want'},
                                     {'name', 'kinds', 'types', 'helper_type', 'description', 'task', 'want_kind'},
                                     {'name', 'current', 'answer', 'reason', 'want'}))

    def test_every_code_is_proved(self):
        reasons = {x['want'].get('reason') for x in resolve_cases()}
        self.assertLessEqual(set(resolve.REASONS), reasons)
        passed = {code for x in resolve_cases() for _, code in x['want']['passed']}
        self.assertLessEqual(set(resolve.PASS_CODES), passed)
        dos = {x['want']['do'] for x in resolve_cases()}
        self.assertEqual(dos, {'nothing', 'model', 'hand'})
        self.assertIn('codex', {x['here'] for x in resolve_cases()})
        self.assertEqual({x.get('where') for x in resolve_cases()}, {None, 'workflow', 'helper', 'chat', 'teammate'})
        self.assertIn(None, {x['kind'] for x in resolve_cases() if x['want'].get('reason') == 'default'})
        got = {(x['reason'], x['want'] is None) for x in effort_cases()}
        self.assertLessEqual({('route', True), ('route', False), ('default', True), ('default', False)}, got)

    def test_made_up_data_only(self):
        with open(FIXTURES, encoding='utf-8') as f:
            text = f.read()
        self.assertNotIn('@', text)
        self.assertNotIn('/Users', text)
        text.encode('ascii')                    # one spelling of every byte, so a copy compares byte for byte


class TheRule(unittest.TestCase):
    def test_every_resolve_case(self):
        for x in resolve_cases():
            with self.subTest(name=x['name']):
                got = resolve.resolve(x['plan'], x['kind'], x['here'], x['now'], x.get('where'))
                self.assertEqual(got, x['want'])

    def test_a_missing_where_is_null(self):
        for x in resolve_cases():
            with self.subTest(name=x['name']):
                self.assertEqual(resolve.resolve(x['plan'], x['kind'], x['here'], x['now']),
                                 resolve.resolve(x['plan'], x['kind'], x['here'], x['now'], None))

    def test_every_apply_effort_case(self):
        for x in effort_cases():
            with self.subTest(name=x['name']):
                self.assertEqual(resolve.apply_effort(x['current'], x['answer'], x['reason']), x['want'])

    def test_apply_effort_never_raises(self):
        for odd in (None, 0, [], {}, 'high', float('nan')):
            self.assertIsNone(resolve.apply_effort(odd, odd, odd))

    # A plan whose route for the kind asked cannot be read answers no_plan for that kind, yet the plan as a whole
    # may be used (another kind's route is fine).
    BROKEN_ROUTE = ('a route for this kind that is not a list: no_plan', 'a choice that is not an object: no_plan',
                    'a choice whose agent is not text: no_plan', 'a choice whose model is a number: no_plan',
                    'a route for the kind that cannot be read is no_plan, never the default')

    def test_usable_says_the_same(self):
        for x in resolve_cases():
            with self.subTest(name=x['name']):
                bad = x['want'].get('reason') in ('no_plan', 'old_plan', 'off') and x['name'] not in self.BROKEN_ROUTE
                self.assertEqual(resolve.usable(x['plan'], x['now']), not bad)

    def test_every_kind_case(self):
        for x in kind_cases():
            with self.subTest(name=x['name']):
                got = resolve.kind_of_work(x['kinds'], x['types'], x['helper_type'], x['description'], x['task'])
                self.assertEqual(got, x['want_kind'])

    def test_never_raises(self):
        for plan in (None, 0, 'x', [], {'v': 1}, {'v': 1, 'made_at': float('nan'), 'good_for_s': 1, 'on': True,
                                                 'agents': {}, 'routes': {}, 'hand': {'on': True}}):
            got = resolve.resolve(plan, 'find', 'claude', 0.0)
            self.assertEqual(got['do'], 'nothing')
        for k in (None, 0, 'x', [], {'order': 5}):
            self.assertIsNone(resolve.kind_of_work(k, None, None, None, 'fix it'))

    def test_the_answer_is_new_each_time(self):
        x = next(c for c in resolve_cases() if c['want']['do'] == 'hand')
        a = resolve.resolve(x['plan'], x['kind'], x['here'], x['now'])
        a['passed'].append(['x', 'y'])
        self.assertEqual(resolve.resolve(x['plan'], x['kind'], x['here'], x['now']), x['want'])


class SameWordRuleAsKinds(unittest.TestCase):
    """The kind of a helper's work is read by providers/kinds.py's own matcher: with the shipped table the answer is
    kinds.kind_of's, word for word."""

    def test_the_shipped_table_gives_kind_of(self):
        for x in kind_cases():
            k = x['kinds']
            if not (isinstance(k, dict) and k.get('cut') == kinds.CUT and k.get('order') == list(kinds.KINDS)
                    and k.get('light') == kinds.LIGHT and k.get('table') == {n: list(v) for n, v in kinds.TABLE.items()}):
                continue
            if x['helper_type'] in (x['types'] or {}) or isinstance(x['description'], str) and x['description']:
                continue
            with self.subTest(name=x['name']):
                self.assertEqual(x['want_kind'], kinds.kind_of(x['task'])[0])

    def test_more_tasks_against_kind_of(self):
        k = {'order': list(kinds.KINDS), 'light': kinds.LIGHT, 'cut': kinds.CUT,
             'table': {n: list(v) for n, v in kinds.TABLE.items()}}
        for task in ('explain how to add a new provider', 'update the docs, then review them', 'where is the port set',
                     'find the summary and draft a reply', 'Thanks! Now review this', 'who wrote this', '',
                     '2) translate the readme', 'why does the build fail', 'propose a design for caching'):
            with self.subTest(task=task):
                self.assertEqual(resolve.kind_of_work(k, {}, None, None, task), kinds.kind_of(task)[0])

    def test_no_second_matcher(self):
        with open(resolve.__file__, encoding='utf-8') as f:
            src = f.read()
        self.assertIn('kind_of(', src)
        self.assertNotIn('re.compile', src)


class SameAsChoose(unittest.TestCase):
    """resolve() and providers/job.py choose() agree wherever both apply: a plan with the hand switch on (choose has
    no `here`), every agent of the route can be looked at by both (no too_old: choose has no such state), and no
    stay, no never, no provider named. choose() also tries the person's order after the route; an empty order keeps
    it to the route."""

    STATE = {'room': contract.ROOM, 'out': contract.OUT, 'unknown': contract.UNKNOWN}
    CANNOT = {'not_added': contract.NOT_ADDED, 'not_installed': contract.NOT_INSTALLED,
              'signed_out': contract.SIGNED_OUT}

    def rooms(self, agents):
        out = {}
        for pid, a in agents.items():
            if not isinstance(a, dict) or a.get('can') is not True:
                why = a.get('why_not') if isinstance(a, dict) else None
                out[pid] = contract.Room(pid, self.CANNOT.get(why, contract.NOT_ADDED))
            else:
                state = self.STATE.get(a.get('room'), contract.UNKNOWN)
                back = a.get('back_at') if isinstance(a.get('back_at'), (int, float)) else None
                out[pid] = contract.Room(pid, state, back_at=back if state == contract.OUT else None)
        return out

    def test_agree(self):
        n = 0
        for x in resolve_cases():
            plan, want = x['plan'], x['want']
            if want.get('reason') in ('no_plan', 'old_plan', 'off', 'no_route', 'default') or not plan['hand']['on']:
                continue
            agents = plan['agents']
            route = plan['routes'][x['kind']]
            if any(isinstance(agents.get(c['agent']), dict) and agents[c['agent']].get('why_not') in ('too_old',
                                                                                                    'never')
                   for c in route):
                continue
            got = job.choose(x['kind'], [(c['agent'], c['model'], c['effort']) for c in route], [],
                             self.rooms(agents))
            with self.subTest(name=x['name']):
                n += 1
                if want.get('reason') == 'stays':
                    self.assertEqual((got.provider, got.model, got.effort), (x['here'], None, None))
                elif want['do'] == 'nothing':
                    self.assertIsNone(got.provider)
                else:
                    self.assertEqual((got.provider, got.model, got.effort),
                                     (want.get('to', x['here']), want['model'], want['effort']))
        self.assertGreater(n, 20)


if __name__ == '__main__':
    unittest.main()
