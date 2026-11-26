import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# The api.py functions the rest of Switchboard calls (readings, feed_status_line, activity, decide,
# auto_settings, set_auto, auto_status, request_need_switch), with the account engine's functions faked.
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import core
from switchboard.accounts import api, usage
from fakes_autoswitch import FakeEngine, Home, account

import time


class TheAutoSwitchFunctions(Home):
    def setUp(self):
        super().setUp()
        self.now = time.time()
        self.engine = FakeEngine([account('a1', 1), account('a2', 2)], 'a1')
        patches = [mock.patch.object(api, 'accounts', self.engine.accounts),
                   mock.patch.object(api, 'live', self.engine.live),
                   mock.patch.object(api, 'switch', self.engine.switch)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def feed(self, aid, five, week, resets=0):
        usage.feed_status_line(aid, {'five_hour': {'used_percentage': five, 'resets_at': self.now + 3600 + resets},
                                     'seven_day': {'used_percentage': week,
                                                   'resets_at': self.now + 3 * 86400 + resets}},
                               self.now, now=self.now)

    def test_settings_default_and_change_through_api(self):
        self.assertEqual(api.auto_settings(), {'enabled': False, 'session_at': 90.0, 'weekly_at': 95.0,
                                               'model_at': 95.0, 'model': None, 'strategy': 'refills-first'})
        got = api.set_auto(enabled=True, weekly_at=97.5, model='Fable')
        self.assertEqual((got['enabled'], got['weekly_at'], got['model']), (True, 97.5, 'Fable'))
        self.assertIs(core.settings()['auto'], True)
        with self.assertRaises(api.AccountsError) as e:
            api.set_auto(strategy='round-robin')
        self.assertEqual(e.exception.kind, 'other')
        self.assertIn('refills-first, most-room or stay-then-refills', str(e.exception))

    def test_readings_and_the_status_line_through_api(self):
        api.feed_status_line('a1', {'five_hour': {'used_percentage': 12.0, 'resets_at': self.now + 3600}},
                             self.now)
        r = api.readings()['a1']
        self.assertIsInstance(r, api.Reading)
        self.assertEqual((r.windows[0].name, r.windows[0].pct, r.source), ('5-hour', 12.0, 'status_line'))

    def test_decide_gathers_from_disk_and_one_bounded_live_read(self):
        api.set_auto(enabled=True)
        self.feed('a1', 92.0, 30.0)
        self.feed('a2', 10.0, 20.0, resets=1000)
        d = api.decide()
        self.assertEqual((d.action, d.kind, d.target), ('switch', 'forced', 'a2'))
        self.assertEqual(self.engine.live_calls, 1)

    def test_decide_says_plainly_when_the_live_login_cannot_be_read(self):
        api.set_auto(enabled=True)
        self.engine.live_state = api.UNREADABLE
        d = api.decide()
        self.assertEqual(d.action, 'stay')
        self.assertIn("your Mac's login Keychain is locked", d.reason)      # plain words, never the bare code

    def test_activity_has_its_shape(self):
        a = api.activity()
        self.assertEqual(set(a) >= {'busy', 'idle_for_s', 'sessions', 'obligations'}, True)
        self.assertFalse(a['busy'])

    def test_auto_status_with_no_loop(self):
        st = api.auto_status()
        self.assertEqual((st['pid'], st['stage'], st['running'], st['restart']), (None, None, False, False))
        self.assertIn('lease', st)
        self.assertIn('waiting_for', st)
        self.assertIn('stuck_for_s', st)
        self.assertIn('last_pass_at', st)

    def test_a_need_switch_through_api(self):
        api.set_auto(enabled=True)
        self.feed('a1', 20.0, 30.0)
        self.feed('a2', 10.0, 20.0, resets=1000)
        res = api.request_need_switch('a2', 'Claude in Chrome follows account 2')
        self.assertEqual((res.to_id, res.kind, res.source), ('a2', 'need', 'hook'))
        self.assertEqual(self.engine.switches[0]['kind'], 'need')
        self.assertIsNone(api.request_need_switch('a2', 'Claude in Chrome follows account 2'))

    def test_a_need_switch_is_off_while_automatic_switching_is_off(self):
        self.feed('a1', 20.0, 30.0)
        self.feed('a2', 10.0, 20.0, resets=1000)
        self.assertIsNone(api.request_need_switch('a2', 'chrome'))
        self.assertEqual(self.engine.switches, [])

    def test_with_the_account_engine_built_its_own_functions_answer_through_its_fakes(self):
        # (With no engine, access_token would raise NotImplementedError. The real engine answers here; it is
        # pointed at a made-up Mac, so nothing reaches the real Keychain.)
        from fakes_accounts import Rig
        from switchboard.accounts import context
        mock.patch.stopall()
        rig = Rig()
        self.addCleanup(rig.close)
        with context.use(rig.ctx):
            api.set_auto(enabled=True)
            self.assertEqual(api.decide().action, 'stay')             # Claude Code is not signed in there
            self.assertIsNone(api.request_need_switch('a2', 'x'))
            got = api.access_token('a1')
            self.assertEqual((got.kind, got.why), (api.UNREADABLE, 'not_found'))


if __name__ == '__main__':
    unittest.main()
