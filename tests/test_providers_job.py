import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""The pick for a new task (switchboard/providers/job.py): pure, one rule per test, then a property test.

- `to` (named with --on) decides as place.pick(to=...) does: that provider unless it cannot run, even when out.
- `stay` means only that provider is looked at.
- Otherwise the candidates are the route's choices in order, then every other provider of the order, in order. A
  candidate that cannot run is passed with its code; the first with room is picked; else the first whose numbers are
  not known; else nobody: all_out, with the earliest time one is back when that is known.
- The reason codes say which: route, route_next, order, order_next, named, stay, unknown, all_out, and the codes for
  a provider that cannot run. The model and effort come from the route's choice; a provider from the order has none.
- The property test (seeded, 6,000 cases) holds the never-rules over made-up orders, routes and rooms, and a copy of
  choose() broken on purpose (made inside this test from the module's own source) is caught by it.
"""
import inspect
import os
import random
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))

from switchboard.providers import contract, job  # noqa: E402
from switchboard.providers.contract import NOT_ADDED, NOT_INSTALLED, OUT, ROOM, SIGNED_OUT, UNKNOWN, Room  # noqa: E402
from switchboard.providers.job import RouteChoice as C  # noqa: E402

T = 1795600000.0
CANNOT = (NOT_ADDED, NOT_INSTALLED, SIGNED_OUT)


def rooms(**states):
    """rooms(claude=ROOM, codex=(OUT, T + 60)) -> {pid: Room}."""
    out = {}
    for pid, s in states.items():
        state, back = s if isinstance(s, tuple) else (s, None)
        out[pid] = Room(pid, state, back_at=back)
    return out


class OneRuleEach(unittest.TestCase):
    def test_no_route_the_first_of_the_order_with_room(self):
        got = job.choose('review', (), ['claude', 'codex'], rooms(claude=ROOM, codex=ROOM))
        self.assertEqual((got.provider, got.model, got.effort, got.reason, got.passed), ('claude', None, None,
                                                                                         'order', ()))
        self.assertFalse(got.from_route)

    def test_no_route_a_later_one_of_the_order(self):
        got = job.choose('review', None, ['claude', 'codex'], rooms(claude=(OUT, T), codex=ROOM))
        self.assertEqual((got.provider, got.reason, got.passed), ('codex', 'order_next', (('claude', 'out'),)))

    def test_the_first_choice_of_the_route(self):
        route = (C('codex', 'gpt-6.2-astra', 'high'), C('claude', 'sonnet'))
        got = job.choose('review', route, ['claude', 'codex'], rooms(claude=ROOM, codex=ROOM))
        self.assertEqual((got.provider, got.model, got.effort, got.reason, got.passed),
                         ('codex', 'gpt-6.2-astra', 'high', 'route', ()))
        self.assertTrue(got.from_route)
        self.assertEqual(got.kind, 'review')

    def test_a_later_choice_of_the_route_says_what_was_passed(self):
        route = (C('codex', 'gpt-6.2-astra', 'high'), C('claude', 'sonnet'))
        got = job.choose('review', route, ['claude', 'codex'], rooms(claude=ROOM, codex=(OUT, T + 3600)))
        self.assertEqual((got.provider, got.model, got.effort, got.reason, got.passed),
                         ('claude', 'sonnet', None, 'route_next', (('codex', 'out'),)))

    def test_after_the_route_the_rest_of_the_order(self):
        route = (C('codex', 'gpt-6.2-astra'),)
        got = job.choose('review', route, ['claude', 'codex'], rooms(claude=ROOM, codex=SIGNED_OUT))
        self.assertEqual((got.provider, got.model, got.reason, got.passed),
                         ('claude', None, 'order_next', (('codex', 'signed_out'),)))

    def test_a_candidate_that_cannot_run_is_passed_with_its_code(self):
        for state in CANNOT:
            with self.subTest(state=state):
                got = job.choose('fix', (), ['codex', 'claude'], rooms(codex=state, claude=ROOM))
                self.assertEqual((got.provider, got.passed), ('claude', (('codex', state),)))

    def test_a_provider_with_no_room_entry_is_not_added(self):
        got = job.choose('fix', (C('codex'),), ['claude'], rooms(claude=ROOM))
        self.assertEqual((got.provider, got.passed), ('claude', (('codex', NOT_ADDED),)))

    def test_room_comes_before_numbers_not_known(self):
        got = job.choose('fix', (), ['claude', 'codex'], rooms(claude=UNKNOWN, codex=ROOM))
        self.assertEqual((got.provider, got.reason, got.passed), ('codex', 'order_next', (('claude', 'unknown'),)))

    def test_with_no_room_anywhere_the_first_not_known(self):
        route = (C('codex', 'gpt-6-luna'), C('claude', 'haiku', 'medium'))
        got = job.choose('find', route, ['claude', 'codex'], rooms(claude=UNKNOWN, codex=(OUT, T)))
        self.assertEqual((got.provider, got.model, got.effort, got.reason, got.passed),
                         ('claude', 'haiku', 'medium', 'unknown', (('codex', 'out'),)))
        self.assertTrue(got.from_route)

    def test_all_out_with_the_earliest_time_one_is_back(self):
        got = job.choose('fix', (), ['claude', 'codex'], rooms(claude=(OUT, T + 7200), codex=(OUT, T + 3600)))
        self.assertEqual((got.provider, got.reason, got.back_at, got.passed),
                         (None, 'all_out', T + 3600, (('claude', 'out'), ('codex', 'out'))))
        self.assertEqual((got.model, got.effort), (None, None))

    def test_all_out_when_a_time_is_not_known(self):
        got = job.choose('fix', (), ['claude', 'codex'], rooms(claude=(OUT, T + 7200), codex=(OUT, None)))
        self.assertEqual((got.provider, got.reason, got.back_at), (None, 'all_out', None))

    def test_all_out_beside_ones_that_cannot_run(self):
        got = job.choose('fix', (), ['claude', 'codex'], rooms(claude=(OUT, T + 60), codex=NOT_INSTALLED))
        self.assertEqual((got.provider, got.reason, got.back_at, got.passed),
                         (None, 'all_out', T + 60, (('claude', 'out'), ('codex', NOT_INSTALLED))))

    def test_none_can_run(self):
        got = job.choose('fix', (), ['claude', 'codex'], rooms(claude=SIGNED_OUT, codex=NOT_INSTALLED))
        self.assertEqual((got.provider, got.reason), (None, SIGNED_OUT))

    def test_nobody_to_look_at(self):
        got = job.choose('fix', (), [], {})
        self.assertEqual((got.provider, got.reason, got.passed), (None, 'no_other', ()))

    def test_named_is_picked_even_when_out_with_the_routes_model_for_it(self):
        route = (C('claude', 'sonnet', 'high'), C('codex', 'gpt-6.2-astra', 'high'))
        got = job.choose('review', route, ['claude', 'codex'], rooms(claude=ROOM, codex=(OUT, T)), to='codex')
        self.assertEqual((got.provider, got.model, got.effort, got.reason, got.passed),
                         ('codex', 'gpt-6.2-astra', 'high', 'named', ()))

    def test_named_and_not_in_the_route_has_no_model(self):
        got = job.choose('review', (C('claude', 'sonnet'),), ['claude', 'codex'], rooms(claude=ROOM, codex=UNKNOWN),
                         to='codex')
        self.assertEqual((got.provider, got.model, got.reason), ('codex', None, 'named'))

    def test_named_that_cannot_run_is_refused_with_its_code(self):
        for state in CANNOT:
            with self.subTest(state=state):
                got = job.choose('fix', (), ['claude', 'codex'], rooms(claude=ROOM, codex=state), to='codex')
                self.assertEqual((got.provider, got.reason, got.passed), (None, state, (('codex', state),)))
        got = job.choose('fix', (), ['claude'], rooms(claude=ROOM), to='codex')
        self.assertEqual((got.provider, got.reason), (None, NOT_ADDED))

    def test_stay_looks_only_at_that_provider(self):
        route = (C('codex', 'gpt-6.2-astra'), C('claude', 'sonnet', 'high'))
        got = job.choose('review', route, ['claude', 'codex'], rooms(claude=ROOM, codex=ROOM), stay='claude')
        self.assertEqual((got.provider, got.model, got.effort, got.reason, got.passed),
                         ('claude', 'sonnet', 'high', 'stay', ()))

    def test_stay_on_one_that_is_out_is_nobody(self):
        got = job.choose('review', (), ['claude', 'codex'], rooms(claude=(OUT, T), codex=ROOM), stay='claude')
        self.assertEqual((got.provider, got.reason, got.back_at, got.passed), (None, 'all_out', T,
                                                                               (('claude', 'out'),)))

    def test_stay_on_one_whose_numbers_are_not_known(self):
        got = job.choose('review', (), ['claude', 'codex'], rooms(claude=UNKNOWN, codex=ROOM), stay='claude')
        self.assertEqual((got.provider, got.reason), ('claude', 'unknown'))

    def test_named_against_a_stay_elsewhere_is_nobody(self):
        got = job.choose('review', (), ['claude', 'codex'], rooms(claude=ROOM, codex=ROOM), stay='claude',
                         to='codex')
        self.assertEqual((got.provider, got.reason, got.passed), (None, 'stays_elsewhere', ()))
        got = job.choose('review', (), ['claude', 'codex'], rooms(claude=ROOM, codex=ROOM), stay='codex',
                         to='codex')
        self.assertEqual((got.provider, got.reason), ('codex', 'named'))

    def test_a_route_naming_one_provider_twice_keeps_the_first(self):
        route = (C('codex', 'gpt-6.2-astra'), C('codex', 'gpt-6-luna'))
        got = job.choose('find', route, ['claude', 'codex'], rooms(claude=ROOM, codex=(OUT, T)))
        self.assertEqual((got.provider, got.passed), ('claude', (('codex', 'out'),)))

    def test_rooms_as_a_list_and_route_words_as_tuples(self):
        got = job.choose('find', [('codex', 'gpt-6-luna', None)], ['claude', 'codex'],
                         [Room('claude', ROOM), Room('codex', ROOM)])
        self.assertEqual((got.provider, got.model), ('codex', 'gpt-6-luna'))

    def test_a_provider_this_folder_never_goes_to_is_passed(self):
        route = (C('codex', 'gpt-6.2-astra'), C('claude', 'sonnet'))
        got = job.choose('fix', route, ['claude', 'codex'], rooms(claude=ROOM, codex=ROOM), never={'codex'})
        self.assertEqual((got.provider, got.model, got.reason, got.passed),
                         ('claude', 'sonnet', 'route_next', (('codex', 'never'),)))
        got = job.choose('fix', (), ['codex', 'claude'], rooms(claude=UNKNOWN, codex=ROOM), never=('codex',))
        self.assertEqual((got.provider, got.reason, got.passed), ('claude', 'unknown', (('codex', 'never'),)))
        got = job.choose('fix', (), ['codex', 'claude'], rooms(claude=ROOM, codex=ROOM), never={'codex', 'claude'})
        self.assertEqual((got.provider, got.reason, got.passed), (None, 'never', (('codex', 'never'),
                                                                                  ('claude', 'never'))))

    def test_named_but_never_from_this_folder(self):
        got = job.choose('fix', (), ['claude', 'codex'], rooms(claude=ROOM, codex=ROOM), to='codex', never={'codex'})
        self.assertEqual((got.provider, got.reason, got.passed), (None, 'never', (('codex', 'never'),)))

    def test_every_code_it_gives_is_in_reasons(self):
        self.assertEqual(set(job.REASONS), {'route', 'route_next', 'order', 'order_next', 'named', 'stay', 'unknown',
                                            'all_out', NOT_ADDED, NOT_INSTALLED, SIGNED_OUT, 'never', 'no_other',
                                            'stays_elsewhere'})
        self.assertTrue({NOT_ADDED, NOT_INSTALLED, SIGNED_OUT} <= set(contract.ROOM_STATES))


# ---------- the property test ----------

POOL = ('claude', 'codex', 'zeta', 'omni')
STATES = (ROOM, OUT, UNKNOWN, NOT_ADDED, NOT_INSTALLED, SIGNED_OUT)


def candidates(route, order, stay, to):
    """The candidates as the brief says them, worked out here on their own: the route's providers (each once, first
    choice kept), then the order's others; with to (else stay), only that provider."""
    out = []
    for c in route:
        if c.provider not in out:
            out.append(c.provider)
    out += [p for p in order if p not in out]
    want = to if to is not None else stay
    if want is not None:
        return [want]
    return out


def case(rng):
    order = rng.sample(POOL, rng.randint(0, len(POOL)))
    route = tuple(C(p, rng.choice((None, 'm1', 'm2')), rng.choice((None, 'low', 'high')))
                  for p in rng.sample(POOL, rng.randint(0, 3)))
    if route and rng.random() < 0.1:
        route += (C(route[0].provider, 'other-model'),)       # a provider twice: the first is kept
    rs = {}
    for p in POOL:
        if rng.random() < 0.9:
            state = rng.choice(STATES)
            rs[p] = Room(p, state, back_at=(T + rng.randint(1, 9) * 600 if rng.random() < 0.7 else None)
                         if state == OUT else None)
    stay = rng.choice(POOL) if rng.random() < 0.15 else None
    to = rng.choice(POOL) if rng.random() < 0.15 else None
    never = frozenset(p for p in POOL if rng.random() < 0.15)
    return route, order, rs, stay, to, never


def violations(choose, n=6000, seed=20261124):
    """Every case where choose breaks a never-rule, as (rule, case) pairs."""
    rng = random.Random(seed)
    found = []
    for _ in range(n):
        route, order, rs, stay, to, never = case(rng)
        got = choose('review', route, order, rs, stay=stay, to=to, never=never)

        def state(p, rs=rs, never=never):
            return 'never' if p in never else rs[p].state if p in rs else NOT_ADDED
        cands = [] if (stay and to and stay != to) else candidates(route, order, stay, to)
        args = (route, order, rs, stay, to, never)
        if got.provider is not None and state(got.provider) in CANNOT:
            found.append(('a provider that cannot run', args))
        if got.provider is not None and got.provider in never:
            found.append(('one the folder never goes to', args))
        if got.provider is not None and state(got.provider) == OUT and to is None and \
                any(state(p) == ROOM for p in cands):
            found.append(('one that is out while another has room', args))
        if got.provider is not None and state(got.provider) == UNKNOWN and to is None and \
                any(state(p) == ROOM for p in cands):
            found.append(('numbers not known while another has room', args))
        if to is not None and got.provider not in (to, None):
            found.append(('to did not decide', args))
        if stay is not None and got.provider not in (stay, None):
            found.append(('stay did not decide', args))
        if got.provider is None and to is None and any(state(p) in (ROOM, UNKNOWN) for p in cands):
            found.append(('nobody while one could take it', args))
        again = choose('review', tuple(route), list(order), dict(reversed(list(rs.items()))), stay=stay, to=to,
                       never=set(never))
        if again != got:
            found.append(('not the same answer', args))
        upto = cands.index(got.provider) if got.provider is not None else len(cands)
        if [p for p, _ in got.passed] != cands[:upto]:
            found.append(('passed is not the candidates before the pick', args))
        if got.reason not in job.REASONS:
            found.append(('a reason code not in REASONS', args))
    return found


def broken_copy(old, new):
    """job.py's own source with one line changed, loaded as a module of its own (the product is not touched)."""
    src = inspect.getsource(job)
    if old not in src:
        raise AssertionError('the line to break is not in job.py: %r' % old)
    mod = types.ModuleType('job_broken')
    mod.__dict__['__package__'] = 'switchboard.providers'
    exec(compile(src.replace(old, new, 1), 'job_broken', 'exec'), mod.__dict__)
    mod.RouteChoice, mod.Choice = job.RouteChoice, job.Choice        # the cases are made of the module's own types
    return mod


class TheNeverRules(unittest.TestCase):
    def test_6000_made_up_cases(self):
        found = violations(job.choose)
        self.assertEqual(found[:3], [])

    def test_a_copy_broken_on_purpose_is_caught(self):
        # the copy picks the first candidate that is out when there is one, before any with room
        broken = broken_copy('at = _first(states, ROOM)', 'at = _first(states, OUT)')
        found = violations(broken.choose, n=2000)
        self.assertTrue(found, 'the property test passed a choose() that ignores room')
        self.assertIn('one that is out while another has room', {rule for rule, _ in found})


if __name__ == '__main__':
    unittest.main()
