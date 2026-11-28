import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# Takeaways on the week's numbers (switchboard/optimize.py): each one hangs on one number the week section already
# shows, measured here from this Mac's own files, from 2 percent of the week, at most three, the largest first. Its
# state is read from the router's own switches, never kept here, and reading them writes nothing.
import os
import shutil
import sys
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))
sys.path.insert(0, HERE)

from switchboard import core, insights, optimize  # noqa: E402
from switchboard.router import settings as rsettings  # noqa: E402
from test_providers_words import problems  # noqa: E402  (the one guard every package is held to)

# Weighted (usage.WEIGHTS): re-reading 500,000, storing 100,000, replies 100,000, new input 0.
READING = {'input': 0, 'output': 20000, 'cache_write': 80000, 'cache_read': 10000000, 'cache_write_1h': 0}
REPLYING = {'input': 0, 'output': 200000, 'cache_write': 80000, 'cache_read': 1000000, 'cache_write_1h': 0}
SHAPE = {'id', 'on', 'measured', 'line', 'change', 'state', 'can', 'why_not'}
TRYIT = 'switchboard.router.tryit'         # the router's own trial-run module; not in this tree, so stand-ins here


def who(workflows, helpers, chat):
    return [{'label': 'Workflows', 'share': workflows}, {'label': 'Helpers', 'share': helpers},
            {'label': 'You, in chat', 'share': chat}]


def page(tokens=None, rows=None):
    return {'tokens': dict(tokens or {}), 'cuts': {'who': rows or []}}


def state(**on):
    s = rsettings.defaults()
    s.update(on)
    return {'switches': s}


def trial(**k):
    """A state read where the router's trial-run module imports: what its state() said."""
    s = state()
    s['trial'] = dict({'trying': False, 'was_on': False, 'was_trial': False}, **k)
    return s


class TheShape(unittest.TestCase):
    def test_rereading_when_it_is_the_largest_part(self):
        got = optimize.takeaways(page(READING), state())
        self.assertEqual([t['id'] for t in got], ['rereading'])
        t = got[0]
        self.assertEqual(set(t), SHAPE)
        figure = insights.find(page(READING))[0]['numbers']['cache_read']      # the number the week shows
        self.assertEqual(t['measured'], {'value': figure, 'unit': 'percent_of_week', 'basis': 'your last 7 days'})
        self.assertEqual(t['on'], 'tokens.cache_read')
        self.assertIsNone(t['change'])                                          # advice only
        self.assertEqual((t['state'], t['can']), ('off', False))
        self.assertLessEqual(len(t['line']), 140)

    def test_no_rereading_when_another_part_is_larger(self):
        self.assertEqual(optimize.takeaways(page(REPLYING), state()), [])

    def test_helpers_largest_is_advice_only(self):
        # the router's trial-run switch is read by nothing that routes yet, so no press is offered for it
        got = optimize.takeaways(page(rows=who(20, 50, 30)), state())
        self.assertEqual([t['id'] for t in got], ['who'])
        t = got[0]
        self.assertEqual((t['on'], t['measured']['value']), ('who.helpers', 50))
        self.assertEqual(t['line'], 'Helpers did the largest part of your week.')
        self.assertIsNone(t['change'])
        self.assertEqual((t['state'], t['can'], t['why_not']), ('off', False, None))

    def test_no_sentence_claims_what_this_build_does_not_do(self):
        # nothing records where routes would send work, and nothing ends a trial run after a day
        texts = [optimize.LINE_REREADING, optimize.LABEL_TRY, optimize.DID_TRY, optimize.DID_STOP, optimize.ONLY_TRY,
                 optimize.NO_TRY, optimize.TRY_FAILED, optimize.STOP_FAILED] + list(optimize.LINE_WHO.values()) + \
            list(optimize.LINE_WHO_TRY.values())
        for text in texts:
            with self.subTest(text=text):
                self.assertNotIn('for a day', text)
                self.assertNotRegex(text, r'records|\broutes\b')          # a person may have no routes at all
                self.assertNotRegex(text, r'(?i)codex|every chat|all (of )?your')   # only Claude Code chats opened next
                self.assertLessEqual(len(text), 140)
                self.assertEqual(problems(text), [])

    def test_with_the_trial_module_the_press_is_offered(self):
        for rows, top in ((who(20, 50, 30), 'helpers'), (who(61, 9, 30), 'workflows')):
            with self.subTest(top=top):
                t = optimize.takeaways(page(rows=rows), trial())[0]
                self.assertEqual(t['change'], {'key': 'dry', 'label': 'Try it', 'estimate': None})
                self.assertEqual((t['state'], t['can'], t['why_not']), ('off', True, None))
                self.assertEqual(t['line'], optimize.LINE_WHO_TRY[top])
                self.assertIn('Claude Code chats', t['line'])

    def test_the_trial_state_follows_the_modules_own_state(self):
        rows = who(20, 50, 30)
        self.assertEqual(optimize.takeaways(page(rows=rows), trial(trying=True))[0]['state'], 'trying')
        self.assertEqual(optimize.takeaways(page(rows=rows), trial(trying=False))[0]['state'], 'off')
        # the module's own state wins over the bare switch: setting `dry` alone is not a trial run
        s = trial(trying=False)
        s['switches']['dry'] = True
        self.assertEqual(optimize.takeaways(page(rows=rows), s)[0]['state'], 'off')
        self.assertEqual(optimize.takeaways(page(rows=rows), dict(trial(), trial={}))[0]['state'], 'off')

    def test_workflows_largest_hang_on_workflows(self):
        t = optimize.takeaways(page(rows=who(61, 9, 30)), state())[0]
        self.assertEqual((t['id'], t['on'], t['measured']['value']), ('who', 'who.workflows', 61))

    def test_your_own_chat_largest_says_nothing(self):
        self.assertEqual(optimize.takeaways(page(rows=who(20, 25, 55)), state()), [])

    def test_state_is_the_routers_own_switch(self):
        self.assertEqual(optimize.takeaways(page(rows=who(20, 50, 30)), state(dry=True))[0]['state'], 'trying')
        self.assertEqual(optimize.takeaways(page(rows=who(20, 50, 30)), state(dry=False))[0]['state'], 'off')
        self.assertEqual(optimize.takeaways(page(rows=who(20, 50, 30)), {})[0]['state'], 'off')


class HowMany(unittest.TestCase):
    def test_largest_first_and_at_most_three(self):
        got = optimize.takeaways(page(READING, who(20, 50, 30)), state())
        self.assertEqual([t['id'] for t in got], ['rereading', 'who'])          # 71 then 50
        self.assertLessEqual(len(got), 3)
        values = [t['measured']['value'] for t in got]
        self.assertEqual(values, sorted(values, reverse=True))

    def test_nothing_under_two_percent(self):
        tiny = who(1.5, 1.9, 1.0)                                               # helpers largest, but under the floor
        self.assertEqual(optimize.takeaways(page(rows=tiny), state()), [])

    def test_a_week_with_nothing_to_say_is_empty(self):
        for p in ({}, None, page(), page({'cache_read': 0}), {'tokens': 'x', 'cuts': {'who': 'y'}},
                  {'tokens': {'cache_read': float('nan')}}, page(rows=[{'label': 'Helpers', 'share': True}])):
            with self.subTest(page=p):
                self.assertEqual(optimize.takeaways(p, state()), [])


class OnlyItsOwnNumbers(unittest.TestCase):
    def test_no_estimate_and_no_number_from_elsewhere(self):
        for t in optimize.takeaways(page(READING, who(20, 50, 30)), state()):
            if t['change']:
                self.assertIsNone(t['change']['estimate'])
            self.assertEqual(t['measured']['basis'], 'your last 7 days')
            self.assertNotRegex(t['line'], r'\d')                               # the number is the measured one alone

    def test_words(self):
        for t in optimize.takeaways(page(READING, who(20, 50, 30)), state()):
            for text in (t['line'], (t['change'] or {}).get('label') or ''):
                with self.subTest(text=text):
                    self.assertEqual(problems(text), [])
                    self.assertNotRegex(text.lower(), r'best model|sav(e|ing)|cheaper|unlimited')
                    self.assertLessEqual(len(t['change']['label']) if t['change'] else 0, 40)


class ReadingWritesNothing(unittest.TestCase):
    def setUp(self):
        p = core.data_path('router')
        if os.path.isdir(p):
            shutil.rmtree(p)

    def test_router_state_reads_only(self):
        with mock.patch.dict(sys.modules, {TRYIT: None}):
            got = optimize.router_state()
        self.assertEqual(got['switches'], rsettings.defaults())
        self.assertFalse(os.path.exists(core.data_path('router')))

    def test_the_trial_is_read_only_when_its_module_imports(self):
        with mock.patch.dict(sys.modules, {TRYIT: None}):                      # not there: import fails
            self.assertIsNone(optimize.router_state()['trial'])
        m = types.ModuleType(TRYIT)
        m.state = lambda: {'trying': True, 'was_on': False, 'was_trial': False}
        m.on = m.off = lambda: self.fail('reading the state presses nothing')
        with mock.patch.dict(sys.modules, {TRYIT: m}):
            self.assertIs(optimize.router_state()['trial']['trying'], True)

        def boom():
            raise RuntimeError('stand-in failure')
        m.state = boom
        with mock.patch.dict(sys.modules, {TRYIT: m}):
            self.assertEqual(optimize.router_state()['trial'], {})            # there, but its state not known: off
        self.assertFalse(os.path.exists(core.data_path('router')))

    def test_a_switch_turned_on_reads_back(self):
        rsettings.set_switch('dry', True)
        self.addCleanup(rsettings.reset)
        self.assertIs(optimize.router_state()['switches']['dry'], True)


# The takeaway on storing: only with the router's own count (switchboard.router.cache_count, asked for by the page's
# background work) of at least 20 restores, and only where the cache setting's own status says the key can be set.
COUNT = {'records_read': 900, 'requests': 120, 'restores': 25, 'restore_tokens': 400000, 'cache_writes': 60,
         'by_family': {}, 'says': ['Helpers stored their whole context again 25 times after a wait.',
                                   'That is from 120 requests read.']}
UNSET = {'state': 'unset', 'value': None, 'set_by': None, 'new_enough': True, 'problem': None, 'notes': [],
         'lines': []}


def counted(count=None, cache=None):
    s = state()
    s['count'] = dict(COUNT) if count is None else count
    s['cache'] = dict(UNSET) if cache is None else cache
    return s


class TheStoringTakeaway(unittest.TestCase):
    def test_made_from_the_count_where_the_key_can_be_set(self):
        got = optimize.takeaways(page(), counted())
        self.assertEqual([t['id'] for t in got], ['storing'])
        t = got[0]
        self.assertEqual(set(t), SHAPE)
        self.assertEqual(t['on'], 'tokens.cache_write')
        self.assertEqual(t['measured'], {'value': 25, 'unit': 'times', 'basis': None})   # the count names no window
        self.assertEqual(t['line'], 'Helpers stored their whole context again after a wait.')
        self.assertEqual(t['change'], {'key': 'cache_hour', 'label': "Keep helpers' cache for an hour",
                                       'estimate': None})
        self.assertEqual((t['state'], t['can'], t['why_not']), ('off', True, None))
        self.assertEqual(problems(t['change']['label']), [])

    def test_the_line_is_our_own_never_the_counts_words(self):
        # The count's sentences come from another module; the line is a fixed sentence held to the word rules, and
        # the number is the measured one alone.
        for says in ([], ['You will save a lot.'], ['x' * 141], None):
            with self.subTest(says=says):
                got = optimize.takeaways(page(), counted(dict(COUNT, says=says)))
                self.assertEqual([t['id'] for t in got], ['storing'])
                line = got[0]['line']
                self.assertEqual(line, optimize.LINE_STORING)
                self.assertEqual(problems(line), [])
                self.assertNotRegex(line, r'\d')
                self.assertNotRegex(line.lower(), r'best model|sav(e|ing)|cheaper|unlimited')
                self.assertIsNone(got[0]['measured']['basis'])

    def test_the_window_only_when_the_counts_own_words_say_it(self):
        says = ['Helpers stored their whole context again 25 times after a wait in your last 7 days.']
        t = optimize.takeaways(page(), counted(dict(COUNT, says=says)))[0]
        self.assertEqual(t['measured']['basis'], 'your last 7 days')

    def test_not_made_without_a_count_or_under_twenty(self):
        for s in (state(), dict(counted(), count=None), counted(dict(COUNT, restores=19)),
                  counted(dict(COUNT, restores=True)), dict(counted(), cache=None)):
            with self.subTest(state=s):
                self.assertEqual(optimize.takeaways(page(), s), [])

    def test_not_made_where_the_key_is_set_or_cannot_be(self):
        for cache in (dict(UNSET, state='5m', value='5m', set_by='you'), dict(UNSET, state='1h', set_by='you'),
                      dict(UNSET, state='other', value='9m'), dict(UNSET, state='unknown'),
                      dict(UNSET, problem='That file cannot be read.'), dict(UNSET, new_enough=False),
                      dict(UNSET, notes=['A managed setting decides it.'])):
            with self.subTest(cache=cache):
                self.assertEqual(optimize.takeaways(page(), counted(cache=cache)), [])

    def test_switchboards_own_value_shows_undo(self):
        t = optimize.takeaways(page(), counted(cache=dict(UNSET, state='1h', value='1h', set_by='switchboard')))[0]
        self.assertEqual((t['id'], t['state'], t['can']), ('storing', 'on', True))

    def test_the_shares_first_then_the_count(self):
        got = optimize.takeaways(page(READING, who(20, 50, 30)), counted())
        self.assertEqual([t['id'] for t in got], ['rereading', 'who', 'storing'])

    def test_the_status_is_read_only_with_a_count(self):
        calls = []
        with mock.patch.object(optimize.cache_setting, 'status', lambda **k: calls.append(k) or dict(UNSET)), \
                mock.patch.dict(sys.modules, {TRYIT: None}):
            self.assertNotIn('cache', optimize.router_state())
            self.assertEqual(calls, [])
            got = optimize.router_state(count=COUNT, cache={'claude_dir': '/nowhere'})
        self.assertEqual((got['count'], got['cache']), (COUNT, UNSET))
        self.assertEqual(calls, [{'claude_dir': '/nowhere'}])


if __name__ == '__main__':
    unittest.main()
