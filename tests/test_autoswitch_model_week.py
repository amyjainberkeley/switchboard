import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Auto-switch also switches on a per-model weekly limit (Fable's). That is
# the week of one model, the one in use, against its own line (95 by default), or none: the engine's
# accounts.api.auto_settings() names it ('model'), and plan.py's order and Next say what the engine's policy will do.
# A counted model week holds an account back like its 5-hour or weekly window; one that is not counted never moves
# anyone; nothing is ever landed on over a line. The page's auto-switch line says so in a few words.
#
# Made-up accounts only. The engine is a fake of accounts.api (tests/fakes_accounts_api.py).
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

import test_app  # its headless-Chrome helper
from fakes_accounts_api import install
from switchboard import core, demo, engine, plan, server

NOW = 1_800_000_000
D, H = 86400, 3600


def acct(name, five, week, fable=None, opus=None, **more):
    scoped = [{'name': n, 'used_pct': p, 'resets_at': NOW + 3 * D} for n, p in (('Fable', fable), ('Opus', opus))
              if p is not None]
    a = {'name': name, 'weekly_used_pct': week, 'weekly_resets_at': NOW + 4 * D,
         'five_hour_used_pct': five, 'five_hour_resets_at': NOW + 3 * H if five is not None else None,
         'scoped': scoped, 'disabled': False, 'needs_login': False, 'active': False}
    a.update(more)
    return a


def lines(models=None):
    return {'weekly_line': 90, 'session_line': 90, 'model_line': 95, 'margin': 5, 'strategy': 'most-room',
            'models': list(models or [])}


def nxt(accounts, s):
    n = plan.next_account(accounts, s, NOW)
    return n['account']['name'] if n['account'] else None


class TheSetting(unittest.TestCase):
    """What the engine says it counts, read through engine.auto_settings (and changed through the page)."""

    def setUp(self):
        self.fake = install(self)
        p = mock.patch.object(engine, 'HOST', engine.AutoHost())
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(engine, 'foreign_switcher', lambda folder=None: None)
        p.start()
        self.addCleanup(p.stop)

    def test_unset_counts_no_model_week(self):
        self.fake.auto['model'] = None
        a = engine.auto_settings()
        self.assertEqual((a['model'], a['models'], a['model_at']), (None, [], 95.0))
        self.fake.auto['model'] = '  '                  # a blank name is no name
        self.assertEqual(engine.auto_settings()['models'], [])

    def test_a_name_is_kept_and_none_or_all_are_not_models(self):
        self.fake.auto.update(model='Fable', model_at=92)
        a = engine.auto_settings()
        self.assertEqual((a['model'], a['models'], a['model_at']), ('Fable', ['Fable'], 92))
        self.assertEqual(engine.model_names('Fable'), ['Fable'])
        self.assertEqual(engine.model_names(' None '), [])
        self.assertEqual(engine.model_names('all'), [])

    def test_the_plan_reads_it_the_same(self):
        self.assertEqual(server.plan_settings({})['models'], [])
        self.assertEqual(plan._settings(None)['models'], ())
        self.assertEqual(server.plan_settings({'models': ['Fable']})['models'], ['Fable'])
        self.assertEqual(plan._settings({'models': ['Fable', 'none']})['models'], ('fable',))

    def test_the_page_picker_writes_a_name_and_none(self):
        server.act('/api/auto', {'model': 'Fable'})
        self.assertEqual(self.fake.auto['model'], 'Fable')
        server.act('/api/auto', {'model': ''})
        self.assertIsNone(self.fake.auto['model'])
        self.assertEqual([c[1] for c in self.fake.calls if c[0] == 'set_auto'], [{'model': 'Fable'}, {'model': ''}])
        for bad in ('Fa ble!', 7):
            with self.assertRaises(core.SwitchError):
                server.act('/api/auto', {'model': bad})

    def test_models_seen_never_offers_all_or_none_as_a_model(self):
        st = core.blank_state()
        st['accounts']['a@made-up.example'] = {'connected': True, 'models_seen': ['Fable']}
        self.assertEqual(server.models_seen(st, {'models': ['all']}), ['Fable'])
        self.assertEqual(server.models_seen(st, {'models': ['none']}), ['Fable'])
        self.assertEqual(server.models_seen(st, {'models': ['Opus']}), ['Fable', 'Opus'])


class ThePlan(unittest.TestCase):
    """plan.py, the page's order and Next, by our own rule (the one the engine's policy keeps)."""

    def test_a_counted_fable_week_over_the_line_moves_it(self):
        given = [acct('in-use', 20, 30, fable=95, active=True), acct('room', 20, 30, fable=30),
                 acct('spent', 20, 30, fable=100)]
        self.assertEqual(nxt(given, lines(['Fable'])), 'room')
        ranked = dict((a['name'], a) for a in plan.order(given, lines(['Fable']), NOW))
        self.assertEqual((ranked['spent']['reason'], ranked['spent']['blocked_by']), ('at its limit', ['Fable']))
        self.assertFalse(ranked['in-use']['usable'])
        self.assertEqual(ranked['in-use']['reason'], 'over its line')

    def test_every_fable_week_spent_nothing_is_next_and_it_says_when(self):
        for five in (95, 100, 50):
            with self.subTest(five=five):
                given = [acct('a', five, 40, fable=100, active=True), acct('b', 10, 30, fable=100),
                         acct('c', 20, 30, fable=100)]
                n = plan.next_account(given, lines(['Fable']), NOW)
                self.assertEqual((n['account'], n['reason'], n['back_at']),
                                 (None, 'no other account has room', NOW + 3 * D))
                ranked = dict((x['name'], x) for x in plan.order(given, lines(['Fable']), NOW))
                self.assertEqual(ranked['b']['reason'], 'at its limit')
                self.assertFalse(ranked['a']['usable'])
                self.assertEqual(nxt(given, lines([])), 'b')                 # not counted: it never moves anyone

    def test_an_uncounted_model_week_changes_nothing(self):
        given = [acct('a', 95, 40, fable=100, active=True), acct('b', 10, 30, fable=100),
                 acct('c', 20, 30, fable=100)]
        strip = lambda xs: [(x['name'], x['rank'], x['usable'], x['reason'], x['left_pct']) for x in xs]  # noqa: E731
        self.assertEqual(strip(plan.order(given, lines(['Opus']), NOW)), strip(plan.order(given, lines([]), NOW)))

    def test_room_only_behind_a_limit_is_no_landing_place(self):
        given = [acct('a', 100, 40, fable=30, active=True), acct('b', 10, 30, fable=100), acct('c', 20, 30, fable=100)]
        self.assertIsNone(nxt(given, lines(['Fable'])))
        self.assertEqual(nxt(given, lines([])), 'b')

    def test_an_account_with_no_model_week_is_not_held_back_by_it(self):
        given = [acct('a', 50, 40, fable=100, active=True), acct('b', 5, 20, fable=100), acct('team', 30, 30)]
        self.assertEqual(nxt(given, lines(['Fable'])), 'team')
        given[2]['five_hour_used_pct'] = 100                              # ...until that account is at its own limit
        self.assertIsNone(nxt(given, lines(['Fable'])))

    def test_one_account_with_fable_room_is_next(self):
        given = [acct('a', 20, 40, fable=100, active=True), acct('b', 10, 30, fable=100), acct('c', 20, 30, fable=0)]
        self.assertEqual(nxt(given, lines(['Fable'])), 'c')

    def test_two_models_counted_each_holds_its_own(self):
        given = [acct('a', 95, 40, fable=100, opus=20, active=True), acct('b', 10, 30, fable=30, opus=100),
                 acct('c', 20, 45, fable=100, opus=100)]
        self.assertIsNone(nxt(given, lines(['Fable', 'Opus'])))
        self.assertEqual(nxt(given, lines(['Fable'])), 'b')
        ranked = dict((x['name'], x) for x in plan.order(given, lines(['Fable', 'Opus']), NOW))
        self.assertEqual(ranked['c']['blocked_by'], ['Fable', 'Opus'])

    def test_everyone_over_a_line_nothing_is_landed_on(self):
        # An engine could fall back to the account with the most room when every account was over a
        # line; ours never lands over a line ("All out: stay, say when the first refills").
        given = [acct('in-use', 90, 92, fable=89, opus=100, active=True), acct('fable-95', 0, 92, fable=95),
                 acct('five-95', 95, 70, fable=80)]
        n = plan.next_account(given, lines(['Fable', 'Opus']), NOW)
        self.assertEqual((n['account'], n['reason']), (None, 'no other account has room'))
        self.assertEqual(n['back_at'], NOW + 3 * H)                       # five-95's 5-hour window, the first back
        self.assertFalse(any(x['fallback'] for x in plan.order(given, lines(['Fable', 'Opus']), NOW)))

    def test_an_equal_room_tie_goes_to_the_sooner_refill(self):
        given = [acct('in-use', 100, 40, active=True), acct('later', 20, 50), acct('sooner', 20, 50,
                                                                                 weekly_resets_at=NOW + 1 * D)]
        self.assertEqual(nxt(given, lines([])), 'sooner')

    def test_accounts_auto_switch_cannot_use_are_never_next(self):
        for more in ({'disabled': True}, {'needs_login': True}, {'trusted': False}):
            with self.subTest(more=more):
                given = [acct('a', 95, 40, fable=100, active=True), acct('b', 10, 30, fable=100),
                         acct('off', 10, 10, fable=0, **more)]
                self.assertIsNone(nxt(given, lines(['Fable'])))
                self.assertEqual(nxt(given, lines([])), 'b')


class TheDemo(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = True
        demo.STATE.clear()
        demo.FACTS.clear()
        self.addCleanup(demo.STATE.clear)
        self.addCleanup(demo.FACTS.clear)

    def test_it_counts_fable_and_its_picker_follows(self):
        demo.view()
        self.assertEqual((demo.FACTS['auto']['model'], demo.FACTS['auto']['models']), ('Fable', ['Fable']))
        demo.act('/api/auto', {'model': ''})
        self.assertEqual((demo.FACTS['auto']['model'], demo.FACTS['auto']['models']), (None, []))
        demo.act('/api/auto', {'model': 'Fable'})
        self.assertEqual(demo.FACTS['auto']['models'], ['Fable'])


class ThePageText(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(HERE, '..', 'switchboard', 'web', 'index.html'), encoding='utf-8') as f:
            self.html = f.read()

    def test_the_picker_offers_none_and_the_models_seen(self):
        self.assertIn("const seen = (a.models_seen || []).map(String)", self.html)     # a tick for each model
        self.assertNotIn("['all', 'All']", self.html)


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class TheAutoSwitchLine(test_app.ChromePage):
    """The auto-switch line over the usage, in headless Chrome on the demo (its ivy account reports a Fable week).
    The line reads: "at [90 %] 5-hour [95 %] weekly": it names only what the account rows show, and a model's week has
    no line there, so its field is in Settings, Switch at (tests/test_page_rows_two_limits.py). (It read "... [95 %]
    Fable" while the rows had a line per model week.)"""

    def line(self):
        """The line as it reads on screen: each switch line is a field, its value and its % stand in for it."""
        return self.run_page("const words = [];\n"
                             "document.querySelectorAll('#auto label > span, #auto span.w, #auto input').forEach((x) => {\n"
                             "  if (getComputedStyle(x).display !== 'none') words.push(x.tagName === 'INPUT' ? x.value + '%' : x.textContent); });\n"
                             "done(words.join(' '));")

    def test_with_a_model_counted_it_says_nothing_more(self):
        demo.view()
        self.assertEqual(self.line(), 'Auto-switch at 90% 5-hour 95% weekly')

    def test_with_none_counted_it_says_nothing_more(self):
        demo.view()
        demo.FACTS['auto'].update(model=None, models=[])
        self.assertEqual(self.line(), 'Auto-switch at 90% 5-hour 95% weekly')


if __name__ == '__main__':
    unittest.main()
