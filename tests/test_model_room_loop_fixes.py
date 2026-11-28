import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# The second read of the model-room lane (REVIEW-DEEP-a.md), the loop's half: a refusal counts only for the account
# it was seen on and only after the switch to it; a newer reading or reply clears it; the plan after a switch is the
# new account's; a refused family counts as asked; the calm-wait clock survives a pass without a target and a restart;
# the signed-out note is said once per event; a fault in the chats' files never voids a pick by hand; a missing sign_lines
# key reads as the policy's default. Everything outside is faked.
import dataclasses
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import core, room  # noqa: E402
from switchboard.accounts import api, autoloop, policy, usage  # noqa: E402
from fakes_autoswitch import (FakeClock, FakeEngine, FakeNet, FakeTokens, Home, T0, account, make_loop,  # noqa: E402
                            set_policy, usage_body)

H, D = 3600.0, 86400.0


def ok(week, fable=None, five=None, week_reset=None):
    models = {'Fable': (fable, T0 + 3 * D)} if fable is not None else {}
    return (200, {}, usage_body(five=five, week=week, five_reset=T0 + 4 * H, week_reset=week_reset or T0 + 4 * D,
                                models=models))


class FakeScanner(object):
    """The transcripts' activity, as told: refused {family: when}, asked {family: when} (None: not read yet)."""

    def __init__(self, refused=None, asked=None, limited_at=None, fail=False):
        self.refused, self.asked, self.limited_at, self.fail = refused or {}, asked, limited_at, fail

    def look(self, timeline=(), now=None, light=False):
        if self.fail:
            raise RuntimeError('a look that fails')
        return {'busy': False, 'idle_for_s': 3600.0, 'sessions': [], 'processes': 1, 'obligations': [],
                'partial': False, 'limited_at': self.limited_at, 'refused': dict(self.refused),
                'asked': None if self.asked is None else dict(self.asked)}


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

    def loop(self, scanner=None, **kw):
        loop = make_loop(self.engine, self.clock, self.net, FakeTokens(), **kw)
        loop.d.notify_signed_out = lambda email, at: self.signed_out.append((email, at))
        if scanner is not None:
            loop.scanner = scanner
        return loop

    def want(self, family, session='s1'):
        d = core.data_path(os.path.join('running', 'wants'))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, session + '.json'), 'w') as f:
            json.dump({'v': 1, 'at': self.clock.wall(), 'session': session, 'asked': family, 'sent': family,
                       'agent': False}, f)

    def switched_to_a1_at(self, t, by='auto'):
        with open(core.data_path('history.jsonl'), 'a') as f:
            f.write(json.dumps({'t': t, 'kind': 'switch', 'by': by, 'to': 'a1@acct.example',
                                'account': 'a1@acct.example', 'text': 'Switched Claude Code to a1@acct.example'})
                    + '\n')

    def refused_file(self, by_account):
        with open(core.data_path(os.path.join('running', 'refused.json')), 'w') as f:
            json.dump({'v': 1, 'by_account': by_account}, f)

    def hb(self):
        return usage.read_json(core.data_path(autoloop.HEARTBEAT)) or {}

    def moves(self):
        return [(s['to'], s['kind']) for s in self.engine.switches]


# ---------- 1 and 6: a refusal counts only where and when it was seen ----------

class RefusalCountsOnlyAfterTheSwitch(Case):
    def test_a_notice_from_just_before_the_switch_to_the_account_in_use_does_not_count(self):
        self.want('fable')
        self.switched_to_a1_at(T0 - 100)
        self.net.set('tok-a1', ok(30))                           # no Fable window of its own: only a refusal blocks it
        self.net.set('tok-a2', ok(20, fable=20))
        self.loop(FakeScanner(refused={'fable': T0 - 115})).run_pass()
        self.assertEqual(self.moves(), [])

    def test_a_notice_after_the_switch_still_counts(self):
        # It counts: the policy wants the model move. With only a refusal to go by, a move 100 s after the last switch
        # waits an hour (policy.MODEL_REFUSED_DWELL, review S1: two accounts that both refuse would trade places), so
        # here it is the same move, held; the finisher's decision of Nov 27 (FINISH-NOTE.md, step 3).
        self.want('fable')
        self.switched_to_a1_at(T0 - 100)
        self.net.set('tok-a1', ok(30))
        self.net.set('tok-a2', ok(20, fable=20))
        self.loop(FakeScanner(refused={'fable': T0 - 90})).run_pass()
        self.assertEqual(self.moves(), [])
        d = self.hb()['decision']
        self.assertEqual((d.get('target'), d.get('kind')), ('a2', 'model'))
        self.assertIn('with only a refusal to go by it waits an hour', d.get('hold') or '')

    def test_a_notice_after_a_switch_an_hour_ago_moves(self):
        self.want('fable')
        self.switched_to_a1_at(T0 - policy.MODEL_REFUSED_DWELL - 100)
        self.net.set('tok-a1', ok(30))
        self.net.set('tok-a2', ok(20, fable=20))
        self.loop(FakeScanner(refused={'fable': T0 - 90})).run_pass()
        self.assertEqual(self.moves(), [('a2', 'model')])

    def test_a_newer_reading_with_room_clears_it_so_no_second_move(self):
        self.want('fable')
        self.switched_to_a1_at(T0 - 100)
        self.net.set('tok-a1', ok(30, fable=55))                 # read this pass, after the notice: Fable has room
        self.net.set('tok-a2', ok(20, fable=20))
        self.loop(FakeScanner(refused={'fable': T0 - 95})).run_pass()
        self.assertEqual(self.moves(), [])

    def test_a_newer_fable_reply_on_the_account_clears_it(self):
        self.want('fable')
        self.switched_to_a1_at(T0 - 100)
        self.net.set('tok-a1', ok(30))
        self.net.set('tok-a2', ok(20, fable=20))
        self.loop(FakeScanner(refused={'fable': T0 - 95}, asked={'fable': T0 - 40})).run_pass()
        self.assertEqual(self.moves(), [])

    def test_the_re_read_before_the_switch_clears_a_refusal_no_reading_had_answered(self):
        self.want('fable')
        self.switched_to_a1_at(T0 - 100)
        self.net.set('tok-a1', ok(30, fable=55))
        self.net.set('tok-a2', ok(20, fable=20))
        loop = self.loop(FakeScanner(refused={'fable': T0 - 95}))
        real = loop.poller.poll

        def poll(ctx, only=None, **kw):
            if only is None:
                return usage.Report()                            # the pass's own reads read nothing
            return real(ctx, only=only, **kw)
        loop.poller.poll = poll
        loop.run_pass()
        self.assertEqual(self.moves(), [])

    def test_a_refusal_with_no_known_account_counts_for_nothing(self):
        self.want('fable')
        self.refused_file({'?': {'fable': {'at': T0 - 30, 'until': None, 'kind': 'limit_model'}}})
        self.net.set('tok-a1', ok(30))
        self.net.set('tok-a2', ok(20, fable=20))
        self.loop().run_pass()
        self.assertEqual(self.moves(), [])

    def test_a_refusal_file_entry_from_before_the_switch_to_the_account_does_not_count(self):
        self.want('fable')
        self.switched_to_a1_at(T0 - 100)
        self.refused_file({'a1': {'fable': {'at': T0 - 200, 'until': T0 + 3 * D, 'kind': 'limit_model'}}})
        self.net.set('tok-a1', ok(30))
        self.net.set('tok-a2', ok(20, fable=20))
        self.loop().run_pass()
        self.assertEqual(self.moves(), [])
        self.assertTrue(room.read_plan()['models']['fable']['room'])

    def test_an_old_notice_kept_by_a_failing_look_lapses_after_ten_minutes(self):
        self.want('fable')
        self.switched_to_a1_at(T0 - 3 * H)
        self.net.set('tok-a1', ok(30))
        self.net.set('tok-a2', ok(20, fable=20))
        loop = self.loop(FakeScanner(fail=True))
        loop.activity = FakeScanner(refused={'fable': T0 - 700}, asked={}).look()
        loop.run_pass()
        self.assertEqual(self.moves(), [])


# ---------- 2: the plan written right after a switch is the new account's ----------

class PlanAfterASwitch(Case):
    def test_a_switch_for_a_refused_account_writes_a_plan_with_room_on_the_new_one(self):
        self.net.set('tok-a1', ok(100))
        self.net.set('tok-a2', ok(10, fable=10))
        loop = self.loop(FakeScanner(limited_at=T0 - 10, asked={}))
        res = loop.run_pass()
        self.assertIsNotNone(res.switched)
        p = room.read_plan()
        self.assertEqual(p['account']['id'], 'a2')
        self.assertFalse(p['out'])
        self.assertTrue(p['models']['fable']['room'])
        self.assertTrue(p['signin']['ok'])


# ---------- 3: a refused family counts as asked ----------

class RefusalIsAnAsk(Case):
    def test_a_fable_refusal_with_no_fable_reply_in_six_hours_still_moves(self):
        self.switched_to_a1_at(T0 - 8 * H)
        self.net.set('tok-a1', ok(30))
        self.net.set('tok-a2', ok(20, fable=20))
        self.loop(FakeScanner(refused={'fable': T0 - 30}, asked={})).run_pass()
        self.assertEqual(self.moves(), [('a2', 'model')])


# ---------- 4: a limit sentence naming a window we do not know changes nothing ----------

class UnknownWindow(Home):
    def test_an_unknown_window_is_ignored_and_logged_once_in_at_most_twelve_words(self):
        from switchboard.accounts import sessions
        text = ("You've hit your Mythos limit · resets Nov 30 at 1pm (America/Los_Angeles). Switch to another model, "
                "or manage usage credits to continue.")
        with mock.patch.object(core, 'log') as log:
            self.assertIsNone(sessions.limit_scope(text))
            self.assertIsNone(sessions.limit_scope(text))
        self.assertEqual(log.call_count, 1)
        line = log.call_args[0][0]
        self.assertIn('Mythos', line)
        self.assertNotIn('America', line)
        self.assertLessEqual(len(line.split(':', 1)[1].split()), 12)
        for known, want in (("You've hit your weekly limit · resets Mon 9am", (sessions.ALL, None)),
                            ("You've hit your limit · resets 4pm", (sessions.ALL, None)),
                            ("You've hit your session limit · resets 4pm", (sessions.ALL, None)),
                            ("You've hit your 5-hour limit · resets 12:20pm", (sessions.ALL, None))):
            self.assertEqual(sessions.limit_scope(known), want, known)


# ---------- 5: a held or failed read counts as a failed read ----------

class ReadCandidates(Case):
    def snap(self, loop):
        return policy.Snapshot(now=self.clock.wall(), accounts=tuple(self.accts), readings={},
                               settings=autoloop.load_settings(), active='a1')

    def ctx(self):
        return usage.Context(active='a1', order=['a1', 'a2'], lines={}, busy=False)

    def test_a_held_read_is_said_failed(self):
        loop = self.loop()
        rep = usage.Report()
        rep.held.append('a2')
        loop.poller.poll = lambda ctx, only=None, **kw: rep
        with mock.patch.object(policy, 'unread_candidates', lambda s: ['a2']):
            _, snap = loop._read_candidates(self.snap(loop), self.ctx())
        self.assertEqual(snap.read_failed, frozenset(['a2']))

    def test_a_stage_that_fails_says_every_account_failed(self):
        loop = self.loop()

        def boom(ctx, only=None, **kw):
            raise RuntimeError('no answer')
        loop.poller.poll = boom
        with mock.patch.object(policy, 'unread_candidates', lambda s: ['a2']):
            _, snap = loop._read_candidates(self.snap(loop), self.ctx())
        self.assertEqual(snap.read_failed, frozenset(['a2']))


# ---------- 7: the calm-wait clock survives a pass without a target and a restart ----------

class WaitClock(Case):
    def test_a_pass_without_a_target_keeps_the_clock(self):
        loop = self.loop()
        loop._note_wanted(api.Decision('stay', target='a2', kind='forced', hold='waits'), None)
        self.clock.advance(60)
        loop._note_wanted(api.Decision('stay', kind='forced', hold='no fresh numbers'), None)
        self.clock.advance(30)
        loop._note_wanted(api.Decision('stay', reason='nothing to do'), None)
        self.clock.advance(30)
        self.assertAlmostEqual(loop._waited().get('forced', 0.0), 120.0, delta=1)

    def test_the_need_gone_for_ten_minutes_or_a_switch_forgets_it(self):
        loop = self.loop()
        loop._note_wanted(api.Decision('stay', target='a2', kind='forced', hold='waits'), None)
        for _ in range(11):
            self.clock.advance(60)
            loop._note_wanted(api.Decision('stay', reason='nothing to do'), None)
        self.assertNotIn('forced', loop._waited())
        loop._note_wanted(api.Decision('stay', target='a2', kind='model', hold='waits'), None)
        loop._note_wanted(api.Decision('switch', target='a2', kind='model'), object())
        self.assertEqual(loop._waited(), {})

    def test_a_clock_last_wanted_long_ago_is_not_used_by_a_restarted_loop(self):
        loop = self.loop()
        loop._note_wanted(api.Decision('stay', target='a2', kind='forced', hold='waits'), None)
        self.clock.advance(3 * H)                               # the loop was stopped for three hours
        loop2 = self.loop()
        self.assertNotIn('forced', loop2._waited())
        loop2._note_wanted(api.Decision('stay', target='a2', kind='forced', hold='waits'), None)
        self.assertEqual(loop2._waited().get('forced'), 0.0)    # wanted again: from now, not from three hours ago

    def test_a_restarted_loop_resumes_the_clock_and_the_forced_switch_goes_at_its_cap(self):
        self.net.set('tok-a1', ok(96, five=40))                 # over its line, not at 100 (that never waits)
        self.net.set('tok-a2', ok(10, five=5))
        loop = self.loop(load=lambda: 6.0)
        self.assertIsNone(loop.run_pass().switched)
        self.clock.advance(61)
        self.assertIsNone(loop.run_pass().switched)
        self.clock.advance(61)
        loop2 = self.loop(load=lambda: 6.0)                     # restarted: 122 s wanted in all
        self.assertIsNotNone(loop2.run_pass().switched)


# ---------- 8: the signed-out note is said once per event ----------

class SignedOutOnce(Case):
    def test_a_pass_that_does_not_see_the_loss_does_not_make_it_said_again(self):
        self.engine.accts = [account('a1', 1)]
        self.engine.live_state = api.ABSENT
        look = api.PutBack('leave', account='a1@acct.example', reason='its saved login was refused by Anthropic',
                           key='refused', account_id='a1')
        answers = [look, None, look, None, look]
        loop = self.loop(put_back=lambda: answers.pop(0) if answers else look)
        for _ in range(5):
            loop.run_pass()
            self.clock.advance(60)
        said = [n for n in loop.notes if 'signed out of a1@acct.example' in n[0]]
        self.assertEqual(len(said), 1, said)
        self.assertEqual(len(self.signed_out), 1)

    def test_signed_in_again_then_lost_again_is_a_new_event(self):
        self.engine.accts = [account('a1', 1)]
        look = api.PutBack('leave', account='a1@acct.example', reason='its saved login was refused by Anthropic',
                           key='refused', account_id='a1')
        loop = self.loop(put_back=lambda: look)
        for state in (api.ABSENT, api.PRESENT, api.ABSENT):
            self.engine.live_state = state
            loop.run_pass()
            self.clock.advance(60)
        self.assertEqual(len(self.signed_out), 2)


# ---------- 9: a fault in the chats' files never voids a pick by hand ----------

class PickHoldsThroughAFault(Case):
    def test_her_pick_holds_when_the_wants_files_cannot_be_read(self):
        self.want('fable')
        self.switched_to_a1_at(T0 - 600, by='you')
        self.net.set('tok-a1', ok(30, fable=100))
        self.net.set('tok-a2', ok(20, fable=20))

        def boom(now, where=None):
            raise RuntimeError('a reader that fails')
        with mock.patch.object(room, 'read_wants', boom):
            self.assertIsNone(self.loop().run_pass().switched)
        self.assertEqual(self.moves(), [])


    def test_a_fault_in_reading_the_chats_models_still_lets_a_forced_switch_go(self):
        self.net.set('tok-a1', ok(100))
        self.net.set('tok-a2', ok(10))

        def boom(snap):
            raise RuntimeError('a reader that fails')
        with mock.patch.object(autoloop, 'with_refused_asked', boom):
            res = self.loop().run_pass()
        self.assertEqual(self.moves(), [('a2', 'forced')])


# ---------- the guards fixer's caps: sign and model moves are counted for policy (when its Snapshot has the fields) ----

@dataclasses.dataclass(frozen=True)
class _WithCaps(policy.Snapshot):
    sign_recent: int = 0
    model_recent: int = 0


class CountsForTheCaps(Case):
    def test_model_and_sign_moves_are_counted_when_the_snapshot_has_the_fields(self):
        def change(st):
            for kind in ('model', 'model', 'sign', 'optional'):
                autoloop.record_switch(st, self.clock, kind, 'auto', 'a1', 'a2')
        autoloop.update_state(change)
        st = autoloop.load_state()
        self.assertEqual([autoloop.optional_recent(st, self.clock, kind=k) for k in ('optional', 'sign', 'model')],
                         [1, 1, 2])
        with mock.patch.object(policy, 'Snapshot', _WithCaps):
            snap = autoloop._snapshot(self.clock, autoloop.load_settings(), self.accts, 'a1', True, None, False, None,
                                      False, False, 0.5, (True, 100), st, None)
        self.assertEqual((snap.optional_recent, snap.sign_recent, snap.model_recent), (1, 1, 2))
        snap = autoloop._snapshot(self.clock, autoloop.load_settings(), self.accts, 'a1', True, None, False, None,
                                  False, False, 0.5, (True, 100), st, None)       # without the fields: as before
        self.assertEqual(snap.optional_recent, 1)


# ---------- 10: a missing sign_lines key reads as the policy's default ----------

@dataclasses.dataclass(frozen=True)
class _OffByDefault(policy.Settings):
    sign_lines: bool = False


class SignLinesDefault(Case):
    def test_a_missing_key_takes_the_policy_default(self):
        core.update_setting(autoloop.POLICY_KEY, lambda old: dict((k, v) for k, v in (old or {}).items()
                                                                  if k != 'sign_lines'))
        with mock.patch.object(policy, 'Settings', _OffByDefault):
            self.assertFalse(autoloop.load_settings().sign_lines)
        self.assertEqual(autoloop.load_settings().sign_lines, policy.Settings().sign_lines)

    def test_a_stored_value_is_kept(self):
        for v in (True, False):
            autoloop.save_settings(sign_lines=v)
            with mock.patch.object(policy, 'Settings', _OffByDefault):
                self.assertIs(autoloop.load_settings().sign_lines, v)


if __name__ == '__main__':
    unittest.main()
