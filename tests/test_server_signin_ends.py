import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# When each account's sign-in ends, on its way from the engine to every surface: the engine's rows carry
# refresh_expires_at (the date Anthropic set at the sign-in, which a renewal keeps); the tick copies it into the
# page's state as signin_ends_at (a new sign-in's date, or none, replaces an old one); GET /api/state carries it, and
# the look between ticks leaves it as the tick set it. (The demo's own dates: test_page_signin_ends.)
#
# The engine is a stand-in (test_app's Sandbox); made-up *.example accounts only.
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import core, engine, server
import test_app  # its Sandbox, its rows and the page's facts (its own tests are not run from here)
from test_app import A, B, D, Sandbox, row


class Rows(Sandbox):
    def state(self, rows, st=None):
        st = st or core.blank_state()
        server.apply_rows(st, rows)
        return st

    def test_each_account_takes_its_rows_date(self):
        now = time.time()
        st = self.state([row(1, A, True, 40, refresh_expires_at=now + 3 * D), row(2, B, False, 20)])
        self.assertEqual(st['accounts'][A]['signin_ends_at'], now + 3 * D)
        self.assertIsNone(st['accounts'][B]['signin_ends_at'])

    def test_a_new_date_replaces_the_old_one_and_none_clears_it(self):
        now = time.time()
        st = self.state([row(1, A, True, 40, refresh_expires_at=now + 3 * D)])
        self.state([row(1, A, True, 40, refresh_expires_at=now + 30 * D)], st)     # signed in again
        self.assertEqual(st['accounts'][A]['signin_ends_at'], now + 30 * D)
        self.state([row(1, A, True, 40)], st)                                       # a row that knows none
        self.assertIsNone(st['accounts'][A]['signin_ends_at'])

    def test_only_a_number_of_seconds(self):
        for odd in ('soon', True, float('nan'), float('inf'), [1], -9e10, -1, 0, 5e11):
            with self.subTest(value=odd):
                st = self.state([row(1, A, True, 40, refresh_expires_at=odd)])
                self.assertIsNone(st['accounts'][A]['signin_ends_at'])

    def test_get_api_state_carries_it(self):
        now = time.time()
        st = self.state([row(1, A, True, 40, refresh_expires_at=now + 3 * D), row(2, B, False, 20)])
        v = server.render(st, test_app.Plan.facts(self))
        got = dict((a['email'], a['signin_ends_at']) for a in v['accounts'])
        self.assertEqual(got, {A: now + 3 * D, B: None})

    def test_the_tick_puts_it_in_the_state(self):
        now = time.time()
        self.rows = [row(1, A, True, 40, refresh_expires_at=now + 2 * D),
                     row(2, B, False, 20, status='needs_login', refresh_expires_at=now - D)]
        server.tick()
        st = core.load()
        self.assertEqual((st['accounts'][A]['signin_ends_at'], st['accounts'][B]['signin_ends_at']),
                         (now + 2 * D, now - D))
        self.assertTrue(st['accounts'][B]['needs_login'])


class BetweenTicks(Sandbox):
    def test_a_look_between_ticks_leaves_it_as_the_tick_set_it(self):
        now = time.time()
        st = {'accounts': {A: {'id': 'acct-1', 'connected': True, 'signin_ends_at': now + 3 * D, 'usage': None,
                               'usage_at': now - 900, 'checked_at': now - 900}}, 'active': A}
        # the tick's row says another date (the engine changed it since): the look still leaves the state's own
        self.patch(server, 'ROWS', {'by_email': {A: engine._row(id='acct-1', email=A, refresh_expires_at=now + 30 * D)}})
        fields = dict(engine.reading_fields(None), usage={'session': None, 'weekly': {'pct': 40.0, 'resets': None},
                                                           'scoped': [], 'spend': None}, usage_at=now - 5)
        server.apply_readings(st, {'acct-1': fields})
        self.assertEqual(st['accounts'][A]['usage']['weekly']['pct'], 40.0)         # the look did take the reading
        self.assertEqual(st['accounts'][A]['signin_ends_at'], now + 3 * D)


if __name__ == '__main__':
    unittest.main()
