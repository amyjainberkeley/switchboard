import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# `switchboard auto why` and `switchboard auto check --dry-run`: what auto-switch would do now, and why, in plain
# words. It never switches and never writes a file. Made-up accounts only (*.example); the engine, the network and the
# clock are stand-ins (tests/fakes_autoswitch.py).
import dataclasses
import hashlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import autocheck, core
from switchboard.accounts import autoloop, policy, usage
from fakes_autoswitch import account, set_policy
from test_auto_check import CheckCase, D, ok
from test_autoswitch_account_rules import COUNT, SEED, generated
from test_autoswitch_policy import NOW, reading, snap

H = 3600.0


def judged(s, word='why', stored=None):
    return autocheck.judge(autocheck.parse([word]), s, stored or s.settings)


def folder_state(root):
    """Every file under `root`: its size, change time and a hash of its bytes."""
    out = {}
    for base, _, files in os.walk(root):
        for name in files:
            p = os.path.join(base, name)
            with open(p, 'rb') as f:
                data = f.read()
            st = os.stat(p)
            out[p] = (st.st_size, st.st_mtime_ns, hashlib.sha256(data).hexdigest())
    return out


class TheDecisionLine(unittest.TestCase):
    def test_a_move_it_would_make_is_said_as_history_says_it_and_exits_0(self):
        s = snap({'a': reading('a', five=92.0, week=30.0), 'b': reading('b', five=10.0, week=20.0),
                  'c': reading('c', five=10.0, week=20.0, week_reset=NOW + 6 * D)})
        code, doc = judged(s)
        self.assertEqual(code, autoloop.EXIT_SWITCHED)
        self.assertEqual((doc['outcome'], doc['action'], doc['from'], doc['to'], doc['forced']),
                         ('would_switch', 'switch', 'a@acct.example', 'b@acct.example', True))
        self.assertTrue(doc['said'].startswith('Would switch Claude Code to b@acct.example: a@acct.example 5-hour at '
                                               '92% (line 90%); b@acct.example has room'), doc['said'])
        self.assertEqual(doc['reasons'][0], 'In use: a@acct.example, 5-hour 92% (2 points over its 90% line), weekly '
                                            '30% (65 points under its 95% line); numbers 0 s old.')
        self.assertIn('b@acct.example is the one it picks: 5-hour 10%, weekly 20%; numbers 0 s old.', doc['reasons'])
        self.assertIn('c@acct.example could take it: 5-hour 10%, weekly 20%; numbers 0 s old.', doc['reasons'])

    def test_a_stay_says_where_it_stays(self):
        s = snap({'a': reading('a', five=20.0, week=30.0), 'b': reading('b', five=10.0, week=20.0)},
                 settings=policy.Settings(enabled=True, strategy='most-room'))
        code, doc = judged(s)
        self.assertEqual(code, autoloop.EXIT_NOTHING)
        self.assertEqual(doc['said'], 'Stays on a@acct.example: a@acct.example is under its lines.')
        self.assertEqual((doc['outcome'], doc['to'], doc['forced']), ('stays', None, None))


class EachOtherAccount(unittest.TestCase):
    def test_each_account_that_cannot_take_it_gives_one_reason(self):
        accts = (account('a', 1), account('b', 2, status='needs_login'), account('c', 3), account('d', 4),
                 account('e', 5), account('f', 6), account('g', 7), account('h', 8, status='conflict'))
        r = {'a': reading('a', five=20.0, week=30.0, week_reset=NOW + 2 * D),
             'b': reading('b', five=1.0, week=1.0), 'c': reading('c', five=1.0, week=1.0),
             'd': reading('d', five=93.0, week=10.0), 'e': reading('e', five=1.0, week=1.0, age=1200.0),
             'g': reading('g', five=10.0, week=10.0, week_reset=NOW + 2 * D + 1800),
             'h': reading('h', five=1.0, week=1.0)}
        s = snap(r, accounts=accts, settings=policy.Settings(enabled=True, rules=(('c', 'out'),)))
        code, doc = judged(s)
        self.assertEqual(code, autoloop.EXIT_NOTHING)
        said = doc['reasons']
        self.assertIn('b@acct.example: needs a sign-in.', said)
        self.assertIn('c@acct.example: kept out of auto-switch.', said)
        self.assertIn('d@acct.example: at its line: 5-hour 93% (3 points over its 90% line).', said)
        self.assertIn('e@acct.example: numbers too old: 20 min (it moves only onto numbers under 15 min).', said)
        self.assertIn('f@acct.example: no numbers yet.', said)
        self.assertIn("g@acct.example has room, but no gain: its week refills %s, not an hour before a@acct.example's "
                      "(%s)." % (usage.clock_text(NOW + 2 * D + 1800, NOW), usage.clock_text(NOW + 2 * D, NOW)), said)
        self.assertIn("h@acct.example: can't be used now: Anthropic said its saved login belongs to another account.",
                      said)
        why = dict((c['email'], (c['can_take_it'], c['why_not'])) for c in doc['candidates'])
        self.assertEqual(why, {'b@acct.example': (False, 'needs_sign_in'), 'c@acct.example': (False, 'kept_out'),
                               'd@acct.example': (False, 'at_line'), 'e@acct.example': (False, 'numbers_too_old'),
                               'f@acct.example': (False, 'no_numbers'), 'g@acct.example': (True, 'no_gain'),
                               'h@acct.example': (False, 'not_usable')})
        self.assertEqual(doc['oldest_numbers_s'], 1200)


class WhatAMoveThatCanWaitWaitsFor(unittest.TestCase):
    def held(self, **kw):
        r = {'a': reading('a', five=20.0, week=40.0, week_reset=NOW + 5 * D),
             'b': reading('b', five=10.0, week=50.0, week_reset=NOW + 2 * D)}
        return judged(snap(r, accounts=(account('a', 1), account('b', 2)), **kw))

    def test_idle_minutes(self):
        code, doc = self.held(idle_for_s=130.0)
        self.assertEqual(code, autoloop.EXIT_NOTHING)
        self.assertEqual((doc['outcome'], doc['to'], doc['forced']), ('waits', 'b@acct.example', False))
        self.assertIn('Waiting: Claude Code has been idle 2 of 5 minutes.', doc['reasons'])
        self.assertEqual(dict((k, doc['waiting_for'][k]) for k in ('what', 'now', 'needed')), {
            'what': 'idle', 'now': 130, 'needed': 300})

    def test_minutes_since_the_last_switch(self):
        code, doc = self.held(since_switch_s=42 * 60.0)
        self.assertIn('Waiting: 42 of 45 minutes since the last switch.', doc['reasons'])
        self.assertEqual(doc['waiting_for']['what'], 'since_switch')

    def test_optional_moves_in_six_hours(self):
        code, doc = self.held(optional_recent=2)
        self.assertIn('Waiting: 2 optional moves in the last 6 hours (it makes at most 2).', doc['reasons'])
        self.assertEqual(doc['waiting_for']['what'], 'optional_moves')

    def test_working_now_and_the_other_holds_in_policys_own_words(self):
        code, doc = self.held(busy=True)
        self.assertIn('Waiting: Claude Code is working now.', doc['reasons'])
        code, doc = self.held(load_per_core=6.0)
        self.assertIn('Waiting: the Mac is very busy (load 6 per core).', doc['reasons'])
        self.assertEqual(doc['waiting_for']['what'], 'load')


class NothingHasRoom(unittest.TestCase):
    def test_it_says_when_the_first_account_refills(self):
        r = {'a': reading('a', five=100.0, week=40.0, five_reset=NOW + 2 * H),
             'b': reading('b', five=100.0, week=40.0, five_reset=NOW + 1 * H)}
        code, doc = judged(snap(r, accounts=(account('a', 1), account('b', 2))))
        self.assertEqual(code, autoloop.EXIT_BLOCKED)
        self.assertEqual(doc['outcome'], 'blocked')
        self.assertIn('Nothing has room. First refill: %s (b@acct.example, 5-hour).' % usage.clock_text(NOW + H, NOW),
                      doc['reasons'])
        self.assertEqual(doc['first_refill'], {'at': autocheck.iso(NOW + H), 'email': 'b@acct.example',
                                               'limit': '5-hour'})


class TheNearestThingThatWouldChangeIt(unittest.TestCase):
    def said(self, s):
        code, doc = judged(s)
        if doc['would_change']:
            self.assertEqual(doc['reasons'][-1], doc['would_change'])
        return doc['would_change']

    def test_a_stay_under_its_lines_names_the_nearest_line(self):
        r = {'a': reading('a', five=71.0, week=40.0), 'b': reading('b', five=10.0, week=20.0)}
        s = snap(r, accounts=(account('a', 1), account('b', 2)),
                 settings=policy.Settings(enabled=True, strategy='most-room'))
        self.assertEqual(self.said(s), 'It would move at 90% of the 5-hour limit (now 71%), and not before: in this '
                                       'order a move waits for a line.')
        r['a'] = reading('a', five=10.0, week=80.0)
        self.assertEqual(self.said(dataclasses.replace(s, readings=r)),
                         'It would move at 95% of the weekly limit (now 80%), and not before: in this order a move '
                         'waits for a line.')

    def test_a_move_that_waits_says_when_it_would_go(self):
        r = {'a': reading('a', five=20.0, week=40.0, week_reset=NOW + 5 * D),
             'b': reading('b', five=10.0, week=50.0, week_reset=NOW + 2 * D)}
        two = (account('a', 1), account('b', 2))
        self.assertEqual(self.said(snap(r, accounts=two, idle_for_s=130.0)),
                         'It would move when Claude Code has been idle 5 minutes (%s).'
                         % usage.clock_text(NOW + 170, NOW))
        self.assertEqual(self.said(snap(r, accounts=two, since_switch_s=42 * 60.0)),
                         'It would move 45 minutes after the last switch, at %s.' % usage.clock_text(NOW + 180, NOW))
        self.assertEqual(self.said(snap(r, accounts=two, busy=True)),
                         'It would move when Claude Code has been idle 5 minutes.')

    def test_a_move_says_what_tipped_it(self):
        r = {'a': reading('a', five=92.0, week=30.0), 'b': reading('b', five=10.0, week=20.0)}
        two = (account('a', 1), account('b', 2))
        self.assertEqual(self.said(snap(r, accounts=two)), 'What tipped it: a@acct.example 5-hour at 92% (line 90%).')
        r = {'a': reading('a', five=20.0, week=40.0, week_reset=NOW + 5 * D),
             'b': reading('b', five=10.0, week=50.0, week_reset=NOW + 2 * D)}
        self.assertEqual(self.said(snap(r, accounts=two)),
                         "What tipped it: b@acct.example's week refills 3 days sooner than a@acct.example's.")

    def test_nothing_has_room_says_when_the_first_refill_lets_it_move(self):
        r = {'a': reading('a', five=100.0, week=40.0, five_reset=NOW + 2 * H),
             'b': reading('b', five=100.0, week=40.0, five_reset=NOW + 1 * H)}
        self.assertEqual(self.said(snap(r, accounts=(account('a', 1), account('b', 2)))),
                         "It would move at %s, when b@acct.example's 5-hour limit refills."
                         % usage.clock_text(NOW + H, NOW))


class ManySnapshots(unittest.TestCase):
    def test_the_dry_run_decides_as_policy_does_on_every_made_up_snapshot(self):
        seen = set()
        for s in generated(SEED, COUNT):
            used = dataclasses.replace(s, settings=autocheck.check_settings(autocheck.parse(['why']), s.settings))
            d = policy.decide(used)
            code, doc = judged(used, stored=s.settings)
            self.assertEqual(doc['action'], d.action)
            to = next((a.email for a in s.accounts if a.id == d.target), d.target) if d.target else None
            self.assertEqual(doc['to'], to)
            could_not_look = d.action == 'stay' and d.kind is None and (
                not used.live_ok or (used.active in [a.id for a in used.accounts] and (used.settling or used.offline)))
            self.assertEqual(code, autoloop.EXIT_SWITCHED if d.action == 'switch' else
                             autoloop.EXIT_BLOCKED if d.kind == 'forced' else
                             autoloop.EXIT_ERROR if could_not_look else autoloop.EXIT_NOTHING)
            if could_not_look:
                seen.add('could not look')
            self.assertTrue(doc['said'] and all(x.endswith('.') for x in doc['reasons']), doc)
            if s.settings.enabled:                           # nothing asked of this check: the very same decision
                self.assertEqual(d, policy.decide(s))
            seen.add((d.action, d.kind))
        self.assertGreaterEqual(len(seen), 5, seen)


class EndToEnd(CheckCase):
    def why(self, *args):
        out = io.StringIO()
        code = autocheck.main(list(args), deps=self.deps(), clock=self.clock, out=out)
        return code, out.getvalue()

    def test_a_dry_run_never_switches_never_reads_and_never_writes(self):
        self.answers(ok(92.0, 30.0), ok(10.0, 20.0), ok(10.0, 20.0))
        set_policy(strategy='most-room')
        loop = autoloop.Loop(deps=self.deps(), clock=self.clock)
        loop.poller.poll(usage.Context(active='a1', order=('a1', 'a2', 'a3'), lines={'5-hour': 90.0, 'weekly': 95.0}))
        switched = []
        self.engine.switch = lambda *a, **k: switched.append(a)
        reads = self.net.count()
        self.assertEqual(reads, 3)
        root = os.environ['SWITCHBOARD_HOME']
        before = folder_state(root)
        for args in (['why'], ['check', '--dry-run']):
            code, out = self.why(*args)
            self.assertEqual(code, autoloop.EXIT_SWITCHED)
            self.assertTrue(out.startswith('Would switch Claude Code to a2@acct.example: '), out)
        self.assertEqual(folder_state(root), before)
        self.assertEqual(self.net.count(), reads)
        self.assertEqual(switched, [])

    def test_why_is_check_dry_run(self):
        self.answers(ok(20.0, 40.0), ok(30.0, 40.0), ok(30.0, 40.0))
        self.check()
        self.assertEqual(self.why('why'), self.why('check', '--dry-run'))

    def test_a_dry_run_answers_while_the_loop_holds_the_lease(self):
        self.answers(ok(20.0, 40.0), ok(30.0, 40.0), ok(30.0, 40.0))
        set_policy(strategy='most-room')
        self.check()
        lease = autoloop.Lease()
        self.assertTrue(lease.acquire(self.clock))
        self.addCleanup(lease.release)
        code, out = self.why('why')
        self.assertEqual(code, autoloop.EXIT_NOTHING)
        self.assertTrue(out.startswith('Stays on a1@acct.example: '), out)

    def test_the_demo_refuses_it(self):
        core.DEMO = True
        self.addCleanup(setattr, core, 'DEMO', False)
        with self.assertRaises(core.SwitchError):
            self.why('why')


if __name__ == '__main__':
    unittest.main()
