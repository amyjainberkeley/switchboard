import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# The plan (switchboard/router/plan.py): built from what Switchboard already knows, written whole at 0600 and at most
# 16 KB, read back as None for anything it cannot use, and never holding an email, an account id, a name or a path.
import json
import os
import shutil
import stat
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))
sys.path.insert(0, HERE)

from switchboard import core  # noqa: E402
from switchboard.providers import contract, kinds  # noqa: E402
from switchboard.providers.contract import Room  # noqa: E402
from switchboard.providers.job import RouteChoice  # noqa: E402
from switchboard.providers.routes import RouteError, Table  # noqa: E402
from switchboard.router import plan, resolve, settings, words  # noqa: E402

import fakes_provider_kit as kit  # noqa: E402

NOW = kit.NOW
TABLE = Table({'find': (RouteChoice('claude', 'haiku', 'medium'), RouteChoice('codex', 'gpt-6-luna', 'medium')),
               'review': (RouteChoice('codex', 'gpt-6.2-astra', 'high'), RouteChoice('claude', 'sonnet'))})
ON = dict(settings.defaults(), on=True, hand={'on': True, 'write': False})


def clean():
    p = core.data_path('router')
    if os.path.isdir(p):
        shutil.rmtree(p)


def made(**kw):
    args = dict(now=NOW, table=TABLE, order=['claude', 'codex'],
                rooms={'claude': Room('claude', contract.ROOM), 'codex': Room('codex', contract.ROOM)},
                at_lines={}, lines={'claude': {'five_hour': 90.0, 'week': 95}}, in_use={'claude': 1},
                settings=ON, version='switchboard 0.2.0 (abc1234)')
    args.update(kw)
    return plan.build(**args)


class Build(unittest.TestCase):
    def test_the_whole_plan(self):
        got = made()
        self.assertEqual(got, {
            'v': 1, 'made_at': NOW, 'good_for_s': 180, 'on': True, 'dry': False, 'by': 'switchboard 0.2.0 (abc1234)',
            'order': ['claude', 'codex'],
            'agents': {'claude': {'can': True, 'why_not': None, 'room': 'room', 'back_at': None, 'at_lines': False,
                                  'line': {'five_hour': 90, 'week': 95}, 'in_use': '1'},
                       'codex': {'can': True, 'why_not': None, 'room': 'room', 'back_at': None, 'at_lines': False}},
            'routes': {'find': [{'agent': 'claude', 'model': 'haiku', 'effort': 'medium'},
                                {'agent': 'codex', 'model': 'gpt-6-luna', 'effort': 'medium'}],
                       'review': [{'agent': 'codex', 'model': 'gpt-6.2-astra', 'effort': 'high'},
                                  {'agent': 'claude', 'model': 'sonnet', 'effort': None}]},
            'why': {'find': 'your route for lookups', 'review': 'your route for reviews'},
            'defaults': {'workflow': {'model': None, 'effort': None}, 'helper': {'model': None, 'effort': None},
                         'chat': {'model': None, 'effort': None}},
            'helpers': {'force': False}, 'chat': {'effort': False, 'model_on_first_turn': False},
            'hand': {'on': True, 'write': False, 'max_s': 900}, 'carry_on': False,
            'kinds': {'order': list(kinds.KINDS), 'light': 'find', 'cut': 300,
                      'table': {k: list(v) for k, v in kinds.TABLE.items()}},
            'types': {'Explore': 'find', 'Plan': 'plan', 'explorer': 'find'}})

    def test_every_switch_off_when_none_given(self):
        got = made(settings=None)
        self.assertEqual((got['on'], got['dry'], got['helpers'], got['chat'], got['hand'], got['carry_on']),
                         (False, False, {'force': False}, {'effort': False, 'model_on_first_turn': False},
                          {'on': False, 'write': False, 'max_s': 900}, False))

    def test_each_switch_reaches_the_plan(self):
        for name in settings.NAMES:
            s = settings.defaults()
            settings._put(s, name, True)
            got = made(settings=s)
            with self.subTest(name=name):
                self.assertIs(settings.get(got, name), True)
                self.assertEqual(sum(settings.get(got, n) is True for n in settings.NAMES), 1)

    def test_each_default_reaches_the_plan(self):
        for name in settings.WORDS:
            s = settings.defaults()
            word = {'claude': 'xhigh'} if name.endswith('.model') else 'xhigh'
            settings._put(s, name, word)
            got = made(settings=s)
            with self.subTest(name=name):
                self.assertEqual(settings.get(got, name), word)
                self.assertEqual(sum(settings.get(got, n) is not None for n in settings.WORDS), 1)
        self.assertEqual(made(settings=None)['defaults'], settings.defaults()['defaults'])

    def test_a_default_reaches_work_with_no_route(self):
        s = dict(ON, defaults={'workflow': {'model': None, 'effort': 'xhigh'}, 'chat': {'model': {'claude': 'opus'}}})
        got = made(settings=s)
        self.assertEqual(got['defaults']['chat'], {'model': {'claude': 'opus'}, 'effort': None})
        self.assertEqual(got['defaults']['helper'], {'model': None, 'effort': None})
        self.assertEqual(resolve.resolve(got, 'build', 'claude', NOW, 'workflow'),
                         {'do': 'model', 'model': None, 'effort': 'xhigh', 'reason': 'default', 'kind': 'build',
                          'passed': [], 'why': None})
        self.assertEqual(resolve.resolve(got, 'find', 'claude', NOW, 'workflow')['reason'], 'route')

    def test_a_default_model_reaches_only_the_agent_it_was_set_for(self):
        s = dict(ON, defaults={'helper': {'model': {'codex': 'gpt-6-luna'}, 'effort': None}})
        got = made(settings=s)
        self.assertEqual(got['defaults']['helper'], {'model': {'codex': 'gpt-6-luna'}, 'effort': None})
        self.assertEqual(resolve.resolve(got, 'build', 'claude', NOW, 'helper')['reason'], 'no_route')
        self.assertEqual(resolve.resolve(got, 'build', 'codex', NOW, 'helper')['model'], 'gpt-6-luna')
        # A bare word, or an entry the plan cannot carry, is no model.
        for model in ('haiku', {'claude': 'me@x.example'}, {'a/b': 'haiku'}, {'claude': 5}, ['haiku']):
            with self.subTest(model=model):
                got = made(settings=dict(ON, defaults={'helper': {'model': model, 'effort': None}}))
                self.assertIsNone(got['defaults']['helper']['model'])

    def test_a_default_the_plan_cannot_carry_is_left_unset(self):
        s = dict(ON, defaults={'workflow': {'model': 'me@x.example', 'effort': 5}, 'helper': 'xhigh'})
        got = made(settings=s)
        self.assertEqual(got['defaults'], settings.defaults()['defaults'])
        plan.write(got)

    def test_rooms_become_can_room_and_back_at(self):
        got = made(order=['claude', 'codex'], rooms={
            'claude': Room('claude', contract.OUT, back_at=NOW + 600.0), 'codex': Room('codex', contract.SIGNED_OUT)},
            at_lines={'claude': True})['agents']
        self.assertEqual(got['claude'], {'can': True, 'why_not': None, 'room': 'out', 'back_at': int(NOW + 600),
                                         'at_lines': True, 'line': {'five_hour': 90, 'week': 95}, 'in_use': '1'})
        self.assertEqual(got['codex'], {'can': False, 'why_not': 'signed_out', 'room': 'unknown', 'back_at': None,
                                        'at_lines': False})
        for state, want in ((contract.NOT_INSTALLED, 'not_installed'), (contract.NOT_ADDED, 'not_added'),
                            ('too_old', 'too_old'), ('never', 'never')):
            a = made(rooms={'claude': Room('claude', contract.ROOM), 'codex': state if isinstance(state, str) and
                            state in ('too_old', 'never') else Room('codex', state)})['agents']['codex']
            self.assertEqual((a['can'], a['why_not']), (False, want), state)
        self.assertEqual(made(rooms={'claude': Room('claude', contract.UNKNOWN)})['agents']['claude']['room'],
                         'unknown')

    def test_a_provider_not_in_the_order_is_not_added(self):
        got = made(order=['claude'])
        self.assertEqual((got['agents']['codex']['can'], got['agents']['codex']['why_not']), (False, 'not_added'))
        self.assertEqual(got['order'], ['claude'])

    def test_a_preset_row_says_so_and_follows_the_order(self):
        table = Table({'find': (RouteChoice('claude', 'haiku', 'medium'), RouteChoice('codex', 'gpt-6-luna', 'medium'))},
                      'by-the-job', frozenset({'find'}))
        got = made(table=table, order=['codex', 'claude'])
        self.assertEqual([c['agent'] for c in got['routes']['find']], ['codex', 'claude'])
        self.assertEqual(got['why']['find'], words.PRESET_WHY['find'])

    def test_no_routes(self):
        got = made(table=None)
        self.assertEqual((got['routes'], got['why']), ({}, {}))
        self.assertEqual(resolve.resolve(got, 'find', 'claude', NOW)['reason'], 'no_route')

    def test_a_word_the_plan_cannot_carry_leaves_the_choice_out(self):
        table = Table({'find': (RouteChoice('claude', 'me@x.example'), RouteChoice('codex', 'gpt-6-luna', 'a/b'),
                                RouteChoice('Codex!'), RouteChoice('codex', 'gpt-6-luna'))})
        self.assertEqual(made(table=table)['routes'], {'find': [{'agent': 'codex', 'model': 'gpt-6-luna',
                                                                  'effort': None}]})

    def test_odd_lines_and_numbers_are_left_out(self):
        got = made(lines={'claude': {'five_hour': 'ninety', 'week': 101}, 'codex': {'week': 95.5}},
                   in_use={'claude': 'me@x.example', 'codex': True})['agents']
        self.assertNotIn('line', got['claude'])
        self.assertEqual(got['codex']['line'], {'week': 95.5})
        self.assertNotIn('in_use', got['claude'])
        self.assertNotIn('in_use', got['codex'])

    def test_the_plan_resolves_and_ages(self):
        got = made()
        self.assertEqual(resolve.resolve(got, 'review', 'claude', NOW + 10)['do'], 'hand')
        self.assertEqual(resolve.resolve(got, 'find', 'claude', NOW + 180)['model'], 'haiku')
        self.assertEqual(resolve.resolve(got, 'find', 'claude', NOW + 181)['reason'], 'old_plan')
        self.assertEqual(resolve.resolve(made(settings=None), 'find', 'claude', NOW)['reason'], 'off')
        self.assertEqual(resolve.kind_of_work(got['kinds'], got['types'], 'Explore', None, None), 'find')
        self.assertEqual(resolve.kind_of_work(got['kinds'], got['types'], None, 'Fix the crash', None), 'fix')

    def test_a_version_with_a_path_is_not_kept(self):
        self.assertEqual(made(version='switchboard 0.2.0 (/Users/someone)')['by'], 'switchboard')


class TheFile(unittest.TestCase):
    def setUp(self):
        clean()

    def test_written_whole_private_and_read_back(self):
        p = made()
        n = plan.write(p)
        self.assertEqual(stat.S_IMODE(os.stat(plan.path()).st_mode), 0o600)
        with open(plan.path(), 'rb') as f:
            data = f.read()
        self.assertEqual(len(data), n)
        self.assertEqual(data, plan.encode(p))
        self.assertLess(n, plan.MAX_BYTES)
        self.assertEqual(plan.read(), json.loads(data.decode('utf-8')))
        self.assertEqual(plan.read(), p)

    def test_too_big_is_refused_and_the_old_plan_stays(self):
        plan.write(made())
        with open(plan.path(), 'rb') as f:
            before = f.read()
        big = made()
        big['why'] = {'find': 'x' * plan.MAX_BYTES}
        with self.assertRaises(plan.PlanError):
            plan.write(big)
        with open(plan.path(), 'rb') as f:
            self.assertEqual(f.read(), before)

    def test_an_email_or_a_path_is_refused(self):
        for bad in ('maya1@northwind.example', '/Users/someone/site', 'C:\\x'):
            p = made()
            p['why'] = {'find': bad}
            with self.subTest(bad=bad):
                with self.assertRaises(plan.PlanError):
                    plan.write(p)
        self.assertFalse(os.path.exists(plan.path()))

    def test_a_write_that_fails_leaves_the_old_plan(self):
        plan.write(made())
        with open(plan.path(), 'rb') as f:
            before = f.read()
        with mock.patch('os.replace', side_effect=OSError(28, 'No space left on device')):
            with self.assertRaises(OSError):
                plan.write(made(now=NOW + 60))
        with open(plan.path(), 'rb') as f:
            self.assertEqual(f.read(), before)
        self.assertEqual([n for n in os.listdir(core.data_path('router')) if n.endswith('.tmp')], [])

    def test_read_gives_none_for_what_it_cannot_use(self):
        self.assertIsNone(plan.read())
        os.makedirs(core.data_path('router'), exist_ok=True)
        for data in (b'', b'not json', b'[1, 2]', b'"plan"', b'{"v": NaN}', b'\xff\xfe{}',
                     b'{"v": 1, "x": "' + b'y' * plan.MAX_BYTES + b'"}'):
            with open(plan.path(), 'wb') as f:
                f.write(data)
            with self.subTest(data=data[:20]):
                self.assertIsNone(plan.read())
        with open(plan.path(), 'wb') as f:
            f.write(b'{"v": 1}')
        self.assertEqual(plan.read(), {'v': 1})


class _Routes:
    """The routes module as gather() reaches it through the Kit."""

    def __init__(self, table=TABLE, error=None):
        self.table, self.error = table, error

    def load(self, catalog=None):
        if self.error is not None:
            raise self.error
        return self.table


class Gather(kit.KitCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        clean()

    def rigged(self, routes=None, **kw):
        r = self.rig(**kw)
        import dataclasses
        return r, dataclasses.replace(r.kit, routes=routes or _Routes())

    def test_from_the_stand_ins(self):
        settings.set_switch('on', True)
        r, k = self.rigged()
        got = plan.gather(NOW, kit=k)
        self.assertEqual(got['order'], ['claude', 'codex'])
        self.assertEqual(got['agents']['claude'], {'can': True, 'why_not': None, 'room': 'room', 'back_at': None,
                                                   'at_lines': False, 'line': {'five_hour': 90}, 'in_use': '1'})
        self.assertEqual(got['agents']['codex'], {'can': True, 'why_not': None, 'room': 'room', 'back_at': None,
                                                  'at_lines': False, 'line': {'week': 95}})
        self.assertEqual(set(got['routes']), {'find', 'review'})
        self.assertTrue(got['on'])
        self.assertTrue(got['by'].startswith('switchboard ' + core.VERSION))
        self.assertTrue(resolve.usable(got, NOW))

    def test_no_email_id_name_or_path_in_the_bytes(self):
        r, k = self.rigged()
        got = plan.gather(NOW, kit=k)
        plan.write(got)
        with open(plan.path(), 'rb') as f:
            data = f.read()
        text = data.decode('utf-8')
        seats = kit.claude_seats() + kit.codex_seat()
        secrets_ = [s.label for s in seats] + [s.key for s in seats if s.key != 'codex'] + \
            ['northwind', 'maya', self.root, os.path.basename(self.root), r.folder, '@', '/']
        for s in secrets_:
            with self.subTest(s=s):
                self.assertNotIn(s, text)
        self.assertNotIn('ChatGPT Pro', text)

    def test_every_claude_account_out_is_at_lines(self):
        r = self.rig()
        r.claude.limits_ = kit.claude_limits(under=0)
        import dataclasses
        got = plan.gather(NOW, kit=dataclasses.replace(r.kit, routes=_Routes()))
        self.assertEqual((got['agents']['claude']['room'], got['agents']['claude']['at_lines']), ('out', True))
        self.assertEqual(got['agents']['claude']['back_at'], int(NOW + 3600))

    def test_codex_not_added(self):
        r, k = self.rigged(codex_added=False)
        got = plan.gather(NOW, kit=k)
        self.assertEqual(got['order'], ['claude'])
        self.assertEqual((got['agents']['codex']['can'], got['agents']['codex']['why_not']), (False, 'not_added'))

    def test_codex_too_old(self):
        r, k = self.rigged(codex_found_=kit.codex_found(self.root, version='0.150.0', seen='0.169.1'))
        got = plan.gather(NOW, kit=k)
        self.assertEqual((got['agents']['codex']['can'], got['agents']['codex']['why_not']), (False, 'too_old'))

    def test_routes_that_cannot_be_read_are_no_routes(self):
        r, k = self.rigged(routes=_Routes(error=RouteError('Your routes file cannot be read.')))
        got = plan.gather(NOW, kit=k)
        self.assertEqual(got['routes'], {})
        self.assertEqual(resolve.resolve(got, 'find', 'claude', NOW)['do'], 'nothing')

    def test_a_provider_that_fails_is_left_unknown(self):
        r, k = self.rigged()

        def broken(ask=False):
            raise RuntimeError('made-up failure')
        r.codex.limits = broken
        got = plan.gather(NOW, kit=k)
        self.assertEqual((got['agents']['codex']['can'], got['agents']['codex']['room']), (True, 'unknown'))
        self.assertEqual(got['agents']['claude']['room'], 'room')

    def test_the_full_plan_fits(self):
        r, k = self.rigged()
        got = plan.gather(NOW, kit=k)
        self.assertLess(plan.write(got), plan.MAX_BYTES * 3 // 4)


if __name__ == '__main__':
    unittest.main()
