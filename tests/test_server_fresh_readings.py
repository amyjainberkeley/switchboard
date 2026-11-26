import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The page's process, between its ticks: "Read again" (POST /api/refresh) writes the request the loop that reads the
# accounts listens for (never in the demo), and so does a change of auto-switch or its lines; a reading the loop saves
# in usage.json reaches GET /api/state (the page, the menu bar, the terminal) at the next look, not at the next tick,
# however busy the Mac is, and costs one stat when nothing changed.
#
# The engine's listing, its auto view and the loop's host are stand-ins (test_app's Sandbox); the readings store is the
# real one, written in a throwaway data folder. Made-up *.example accounts only; nothing is read from the network.
import json
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import core, engine, server
from switchboard.accounts import api, usage
import test_app
from test_app import A, H, Sandbox

REQUEST = 'read-request.json'


def request():
    return usage.read_json(core.data_path(REQUEST))


class ReadAgain(Sandbox):
    def test_read_again_writes_the_request_every_account_by_default(self):
        before = time.time()
        got = server._act('/api/refresh', {})
        req = request()
        self.assertIsNotNone(req)
        self.assertTrue(req['all'])
        self.assertTrue(before - 1 <= req['at'] <= time.time() + 1)
        self.assertEqual(got, {'asked': req['at']})              # the page waits for the loop to answer this one
        self.assertTrue(server.WAKE.is_set())
        server.WAKE.clear()

    def test_all_false_asks_for_the_account_in_use_alone(self):
        server._act('/api/refresh', {'all': False})
        self.assertFalse(request()['all'])

    def test_the_demo_writes_none(self):
        core.DEMO = True
        server.act('/api/refresh', {})
        self.assertIsNone(request())

    def test_changing_auto_switch_or_a_line_asks_the_loop_to_take_it_at_once(self):
        self.patch(engine, 'auto_settings', test_app.REAL_AUTO)
        self.patch(engine.HOST, 'ensure', lambda: None)           # no loop is started here
        self.patch(engine, 'foreign_switcher', lambda folder=None: None)
        server._act('/api/auto', {'enabled': False})
        first = request()
        self.assertIsNotNone(first)
        self.assertFalse(first['all'])
        time.sleep(0.01)
        server._act('/api/auto', {'session_at': 85})
        self.assertNotEqual(request()['at'], first['at'])


class BetweenTicks(Sandbox):
    """The tick read the accounts a while ago; the loop has saved a newer reading of the account in use since."""

    def setUp(self):
        super().setUp()
        self.three()
        for r in self.rows:
            r['usage_at'] = time.time() - 600
        self.patch(server, 'facts', lambda: test_app.Plan.facts(self))
        server.tick()                                     # the state as the last tick left it: 40% weekly, 10 min old

    def store(self, week=63.0, age=5.0, aid='acct-1'):
        now = time.time()
        rec = usage.load_store()
        rec['accounts'][aid] = {
            'w': {'5-hour': {'pct': 12.0, 'resets_at': now + 2 * H, 'at': now - age, 'src': 'endpoint'},
                  'weekly': {'pct': week, 'resets_at': now + 3 * 86400, 'at': now - age, 'src': 'endpoint'}},
            'ep': {'ok_at': now - age}}
        usage.write_atomic(usage.store_path(), rec)
        return now - age

    def active(self, v):
        return next(a for a in v['accounts'] if a['email'] == A)

    def test_the_state_shows_a_new_reading_without_a_tick(self):
        at = self.store()
        a = self.active(server.view())
        self.assertEqual(a['usage']['weekly']['pct'], 63.0)
        self.assertAlmostEqual(a['usage_at'], at, places=2)
        self.assertAlmostEqual(a['checked_at'], at, places=2)

    def test_on_a_very_busy_mac_too(self):
        self.patch(server, 'EASE', {'aware': True, 'on': True, 'load': 6.0})
        self.store(week=71.0)
        self.assertEqual(self.active(server.view())['usage']['weekly']['pct'], 71.0)

    def test_the_tick_still_sees_the_change_it_tells_about(self):
        # The new numbers are drawn at once but not written into the state file: the tick's own before and after
        # (a notification, the history's limit lines) still see the reading cross a line.
        self.store(week=96.0)
        self.assertEqual(self.active(server.view())['usage']['weekly']['pct'], 96.0)
        self.assertEqual(core.load()['accounts'][A]['usage']['weekly']['pct'], 40)

    def test_a_look_costs_one_stat_when_nothing_changed(self):
        self.store()
        calls = []
        real = api.readings
        self.patch(api, 'readings', lambda: calls.append(1) or real())
        server.view()
        server.view()
        server.view()
        self.assertEqual(len(calls), 1)
        self.store(week=64.0)
        self.assertEqual(self.active(server.view())['usage']['weekly']['pct'], 64.0)
        self.assertEqual(len(calls), 2)

    def test_each_pass_of_the_pages_loop_takes_a_new_reading_in(self):
        self.store()
        server.fresh_readings()
        gen = server.SNAPSHOT.gen
        self.assertFalse(server.readings_pass())              # nothing new since
        self.assertEqual(server.SNAPSHOT.gen, gen)
        self.store(week=65.0)
        self.assertTrue(server.readings_pass())
        self.assertEqual(server.SNAPSHOT.gen, gen + 1)         # the next GET /api/state is built again

    def test_get_api_state_answers_with_the_new_reading(self):
        self.patch(server, 'SNAPSHOT', server.Snapshot())
        code, body = server.SNAPSHOT.answer()
        self.assertEqual(code, 200)
        self.store(week=66.0)
        time.sleep(server.Snapshot.SIGNAL_EVERY + 0.05)
        code, body = server.SNAPSHOT.answer()
        self.assertEqual(self.active(json.loads(body))['usage']['weekly']['pct'], 66.0)


class ClaudeCodesOwnNumbersUnderneath(Sandbox):
    """The state the tick left shows Claude Code's own numbers for the account in use (its 'live' mark, the engine's
    own reading kept under it), and the store's reading of that account has no numbers (none read since, a failed
    read). A look puts the engine's own reading back, as the tick does, and never keeps Claude Code's numbers, once
    their record is gone, as if they were the engine's."""

    def test_a_store_reading_with_no_numbers_puts_back_the_engines_own(self):
        now = time.time()
        theirs = {'session': {'pct': 55.0, 'resets': None}, 'weekly': {'pct': 61.0, 'resets': None}, 'scoped': [],
                  'spend': None}
        own = {'session': {'pct': 30.0, 'resets': None}, 'weekly': {'pct': 40.0, 'resets': None}, 'scoped': [],
               'spend': None}
        was = {'usage': own, 'usage_at': now - 900, 'checked_at': now - 900, 'usage_status': 'ok',
               'usage_error': None, 'note': None}
        a = {'id': 'acct-1', 'connected': True, 'usage': theirs, 'usage_at': now - 700, 'checked_at': now - 700,
             'usage_status': 'ok', 'usage_error': None, 'note': None,
             'live': {'at': now - 700, 'usage': theirs, 'was': was}}
        st = {'accounts': {A: a}, 'active': A}
        self.patch(server, 'ROWS', {'by_email': {A: engine._row(id='acct-1', email=A)}})
        failed = dict(engine.reading_fields(None), usage_error='Anthropic did not answer')
        server.apply_readings(st, {'acct-1': failed})
        server.apply_live_numbers(st, now)                # as view() does; no record of Claude Code's is left
        self.assertEqual((a['usage'], a['usage_at'], a['checked_at']), (own, now - 900, now - 900))
        self.assertNotIn('live', a)
        self.assertEqual(a['usage_status'], 'error')

if __name__ == '__main__':
    unittest.main()
