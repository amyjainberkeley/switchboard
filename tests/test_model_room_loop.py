import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# The automatic loop follows the models the chats ask for: it reads what they ask for and what was refused, waits for
# a calm Mac, holds the person's own pick, writes the plan (room.json) every pass, and says once when a lost sign-in
# has no account to move to. Everything outside is faked.
import json
import os
import stat
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import core, room  # noqa: E402
from switchboard.accounts import api, autoloop, usage  # noqa: E402
from fakes_autoswitch import (FakeClock, FakeEngine, FakeNet, FakeTokens, Home, T0, account, make_loop,  # noqa: E402
                            set_policy, usage_body)

H, D = 3600.0, 86400.0


def ok(week, fable=None, five=None, week_reset=None):
    models = {'Fable': (fable, T0 + 3 * D)} if fable is not None else {}
    return (200, {}, usage_body(five=five, week=week, five_reset=T0 + 4 * H, week_reset=week_reset or T0 + 4 * D,
                                models=models))


class Case(Home):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock()
        self.accts = [account('a1', 1), account('a2', 2)]
        self.engine = FakeEngine(self.accts, 'a1', self.clock)
        self.net = FakeNet(self.clock)
        self.signed_out = []
        set_policy()
        os.makedirs(core.data_path('running'))
        with open(core.data_path(os.path.join('running', 'on.json')), 'w') as f:
            json.dump({'v': 1, 'on': True, 'since': T0, 'plugin': '0.1.0'}, f)

    def loop(self, **kw):
        loop = make_loop(self.engine, self.clock, self.net, FakeTokens(), **kw)
        loop.d.notify_signed_out = lambda email, at: self.signed_out.append((email, at))
        return loop

    def want(self, family, session='s1'):
        d = core.data_path(os.path.join('running', 'wants'))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, session + '.json'), 'w') as f:
            json.dump({'v': 1, 'at': self.clock.wall(), 'session': session, 'asked': family, 'sent': family,
                       'agent': False}, f)

    def plan(self):
        return room.read_plan()

    def hb(self):
        return usage.read_json(core.data_path(autoloop.HEARTBEAT)) or {}


class FollowsTheChats(Case):
    def test_a_fable_chat_moves_to_the_account_with_fable_room_and_the_plan_says_so(self):
        self.want('fable')
        self.net.set('tok-a1', ok(76, fable=100))
        self.net.set('tok-a2', ok(0, fable=0, week_reset=T0 + 6 * D))
        res = self.loop().run_pass()
        self.assertIsNotNone(res.switched)
        sw = self.engine.switches[0]
        self.assertEqual((sw['to'], sw['kind']), ('a2', 'model'))
        self.assertIn('Fable is used up on a1@acct.example (100%).', sw['reason'])
        p = self.plan()
        self.assertEqual(p['account']['id'], 'a2')                  # written at once for the account switched to
        self.assertTrue(p['models']['fable']['room'])
        path = core.data_path(os.path.join('running', 'room.json'))
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_nothing_asked_of_fable_means_no_move_for_it(self):
        self.want('opus')
        self.net.set('tok-a1', ok(50, fable=100))
        self.net.set('tok-a2', ok(10, fable=0))
        res = self.loop().run_pass()
        self.assertIsNone(res.switched)
        self.assertIsNone(self.plan()['words'])                     # opus runs as asked

    def test_a_refusal_a_chat_wrote_counts(self):
        self.want('fable')
        with open(core.data_path(os.path.join('running', 'refused.json')), 'w') as f:
            json.dump({'v': 1, 'by_account': {'a1': {'fable': {'at': self.clock.wall() - 30, 'until': None,
                                                               'kind': 'limit_model'}}}}, f)
        self.net.set('tok-a1', ok(30))      # no Fable number of its own: a newer reading with room would clear it
        self.net.set('tok-a2', ok(20, fable=20))
        self.loop().run_pass()
        self.assertEqual([(s['to'], s['kind']) for s in self.engine.switches], [('a2', 'model')])

    def test_with_no_room_for_fable_anywhere_it_stays_and_steps_down(self):
        self.want('fable')
        self.net.set('tok-a1', ok(76, fable=100))
        self.net.set('tok-a2', ok(10, fable=100))
        res = self.loop().run_pass()
        self.assertIsNone(res.switched)
        p = self.plan()
        self.assertEqual((p['models']['fable']['room'], p['models']['fable']['use']), (False, 'opus'))
        self.assertTrue(p['words'].endswith('Chats run on Opus 5.5 until then.'), p['words'])
        self.assertNotIn('Every account is out', self.hb()['decision']['reason'])


class CalmFirst(Case):
    def test_a_forced_switch_waits_for_a_calm_mac_two_minutes_at_most_over_a_line(self):
        self.net.set('tok-a1', ok(96, five=40))                     # over its weekly line, not at 100
        self.net.set('tok-a2', ok(10, five=5))
        loop = self.loop(load=lambda: 6.0)
        res = loop.run_pass()
        self.assertIsNone(res.switched)
        self.assertIn('waits for the Mac to calm down (load 6 per core)', self.hb()['decision']['hold'])
        self.assertEqual(self.plan()['next']['when'], 'calm')
        self.clock.advance(60)
        self.assertIsNone(loop.run_pass().switched)
        self.clock.advance(61)
        self.assertIsNotNone(loop.run_pass().switched)              # 121 s waited: it goes

    def test_a_blocked_account_is_left_on_the_first_pass_on_a_busy_mac(self):
        self.net.set('tok-a1', ok(100, five=40))
        self.net.set('tok-a2', ok(10, five=5))
        self.assertIsNotNone(self.loop(load=lambda: 6.0).run_pass().switched)


class HerPick(Case):
    def test_her_pick_holds_a_model_switch_two_hours(self):
        self.want('fable')
        with open(core.data_path('history.jsonl'), 'w') as f:
            f.write(json.dumps({'t': self.clock.wall() - 600, 'kind': 'switch', 'by': 'you', 'to': 'a1@acct.example',
                                'account': 'a1@acct.example', 'text': 'Switched Claude Code to a1@acct.example'}) +
                    '\n')
        self.net.set('tok-a1', ok(30, fable=100))
        self.net.set('tok-a2', ok(20, fable=20))
        loop = self.loop()
        self.assertIsNone(loop.run_pass().switched)
        d = self.hb()['decision']
        self.assertTrue(d['reason'].startswith('You picked a1@acct.example at '), d['reason'])
        self.assertAlmostEqual(autoloop.pick_age('a1@acct.example', self.clock.wall()), 600.0, delta=5)
        self.assertIsNone(autoloop.pick_age('a2@acct.example', self.clock.wall()))


class SignedOutWithNowhereToGo(Case):
    def test_said_once_in_history_and_one_notification(self):
        self.engine.accts = [account('a1', 1)]
        self.engine.live_state = api.ABSENT
        look = api.PutBack('leave', account='a1@acct.example', reason='its saved login was refused by Anthropic',
                           key='refused', account_id='a1')
        loop = self.loop(put_back=lambda: look)
        for _ in range(3):
            loop.run_pass()
            self.clock.advance(60)
        said = [n for n in loop.notes if 'signed out of a1@acct.example' in n[0]]
        self.assertEqual(said, [('Claude Code is signed out of a1@acct.example and no other account is signed in. '
                                 'Sign in on the Switchboard page.', 'problem')])
        self.assertEqual([e for e, _ in self.signed_out], ['a1@acct.example'])
        self.assertFalse(self.plan()['signin']['ok'])


class Settings(Case):
    def test_pick_hold_and_sign_lines_are_stored_and_read_back(self):
        autoloop.save_settings(pick_hold='until_out', sign_lines=False)
        st = autoloop.load_settings()
        self.assertEqual((st.pick_hold, st.sign_lines), ('until_out', False))
        for bad in (dict(pick_hold='always'), dict(sign_lines='yes')):
            with self.assertRaises(api.AccountsError):
                autoloop.save_settings(**bad)
        self.assertEqual(autoloop.load_settings().pick_hold, 'until_out')


class OffUntilTurnedOn(Case):
    def test_no_plan_while_the_plugin_is_off_and_the_moves_are_the_same(self):
        os.unlink(core.data_path(os.path.join('running', 'on.json')))
        self.want('fable')
        self.net.set('tok-a1', ok(76, fable=100))
        self.net.set('tok-a2', ok(0, fable=0))
        self.assertEqual(self.loop().run_pass().switched.to_id, 'a2')
        self.assertFalse(os.path.exists(core.data_path(os.path.join('running', 'room.json'))))


class FailOpen(Case):
    def test_unreadable_chat_files_change_nothing(self):
        os.makedirs(core.data_path(os.path.join('running', 'wants', 'x.json')))     # a folder where a file goes
        with open(core.data_path(os.path.join('running', 'refused.json')), 'w') as f:
            f.write('{not json')
        self.net.set('tok-a1', ok(96))
        self.net.set('tok-a2', ok(10))
        self.assertEqual(self.loop().run_pass().switched.to_id, 'a2')


if __name__ == '__main__':
    unittest.main()
