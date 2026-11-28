import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# room.build turns a snapshot and its decision into the plan the chats run by (room.json): which families have room
# on the account in use, the one to use in their place, and words for the page. No flapping at 99 and 100.
import dataclasses
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import room  # noqa: E402
from switchboard.accounts import api, policy  # noqa: E402
from fakes_autoswitch import T0, account  # noqa: E402

H, D = 3600.0, 86400.0
NOW = T0


def reading(aid, week=None, fable=None, five=None, age=0.0, now=NOW, week_reset=None, fable_reset=None):
    ws = []
    if five is not None:
        ws.append(api.Window('5-hour', five, now + 3 * H, now - age, 'endpoint'))
    if week is not None:
        ws.append(api.Window('weekly', week, week_reset or now + 4 * D, now - age, 'endpoint'))
    if fable is not None:
        ws.append(api.Window('model:Fable', fable, fable_reset or now + 3 * D, now - age, 'endpoint'))
    return api.Reading(aid, tuple(ws), now - age, 'endpoint', False, None)


ACCTS = (account('a', 1), account('b', 2))


def snap(readings, now=NOW, **kw):
    kw.setdefault('asked', frozenset(['fable']))
    kw.setdefault('settings', policy.Settings(enabled=True))
    kw.setdefault('accounts', ACCTS)
    return policy.Snapshot(now=now, readings=readings, active='a', load_per_core=0.5, idle_for_s=1800.0,
                           since_switch_s=4 * H, **kw)


def plan(s, prev=None, refused=None, **kw):
    return room.build(s, policy.decide(s), refused=refused, prev=prev, **kw)


class ThePlan(unittest.TestCase):
    def test_fable_used_up_steps_down_to_opus_and_says_where_it_moves(self):
        s = snap({'a': reading('a', week=76, fable=100), 'b': reading('b', week=0, fable=0)})
        p = plan(s)
        self.assertEqual(p['v'], 1)
        self.assertEqual(p['account'], {'id': 'a', 'email': 'a@acct.example'})
        self.assertEqual(p['models']['fable'], {'room': False, 'pct': 100.0, 'until': NOW + 3 * D, 'use': 'opus'})
        self.assertEqual(p['models']['opus'], {'room': True, 'pct': None, 'until': None, 'use': None})
        self.assertFalse(p['out'])
        self.assertEqual(p['next']['to'], 'b@acct.example')
        self.assertEqual((p['next']['kind'], p['next']['when']), ('model', 'now'))
        self.assertTrue(p['words'].startswith('Fable is used up on a@acct.example until '), p['words'])
        self.assertTrue(p['words'].endswith('Chats run on Opus 5.5 until Switchboard moves to b@acct.example.'))

    def test_everything_as_asked_says_nothing(self):
        p = plan(snap({'a': reading('a', week=20, fable=20), 'b': reading('b', week=0, fable=0)}))
        self.assertIsNone(p['words'])
        self.assertTrue(all(m['room'] for m in p['models'].values()))
        self.assertIsNone(p['next'])

    def test_old_or_unknown_numbers_count_as_room(self):
        p = plan(snap({'a': reading('a', week=20, fable=100, age=901)}))
        self.assertTrue(p['models']['fable']['room'])

    def test_a_refusal_in_a_chat_counts_ten_minutes_or_until_its_reset(self):
        refused = {'a@acct.example': {'fable': {'at': NOW - 60, 'until': None, 'kind': 'limit_model'}}}
        s = snap({'a': reading('a', week=20, fable=50)})
        self.assertFalse(plan(s, refused=refused)['models']['fable']['room'])
        refused['a@acct.example']['fable']['at'] = NOW - 601
        self.assertTrue(plan(s, refused=refused)['models']['fable']['room'])
        refused['a@acct.example']['fable']['until'] = NOW + H
        self.assertFalse(plan(s, refused=refused)['models']['fable']['room'])
        self.assertEqual(plan(s, refused=refused)['models']['fable']['until'], NOW + H)
        # another account's refusal is not this one's
        self.assertTrue(plan(s, refused={'b': {'fable': {'at': NOW - 60}}})['models']['fable']['room'])

    def test_a_five_hour_or_weekly_window_at_100_blocks_every_family(self):
        p = plan(snap({'a': reading('a', five=100, week=20, fable=10)}))
        self.assertTrue(p['out'])
        self.assertEqual(p['out_until'], NOW + 3 * H)
        self.assertTrue(all(m['use'] is None for m in p['models'].values()))

    def test_signed_out_and_put_back(self):
        s = snap({'a': reading('a', week=20, fable=20)})
        self.assertEqual(plan(s, put_back=(True, NOW - 30))['signin'], {'ok': True, 'back_at': NOW - 30})
        p = plan(s, put_back=(False, None))
        self.assertEqual(p['signin'], {'ok': False, 'back_at': None})
        self.assertEqual(p['words'], 'Claude Code is signed out of a@acct.example.')

    def test_rule_4_words(self):
        s = snap({'a': reading('a', week=50, fable=100), 'b': reading('b', week=10, fable=100)})
        p = plan(s)
        self.assertIsNone(p['next'])
        self.assertTrue(p['words'].endswith('Chats run on Opus 5.5 until then.'), p['words'])

    def test_a_sign_move_is_kind_sign(self):
        # lead's A2: the contract's five words; the page tells a sign move from a refill move
        s = snap({'a': reading('a', week=66), 'b': reading('b', week=16)}, asked=frozenset(),
                 settings=policy.Settings(enabled=True, sign_lines=True))
        self.assertEqual(policy.decide(s).kind, 'sign')
        self.assertEqual(plan(s)['next']['kind'], 'sign')

    def test_a_question_mark_refusal_from_before_the_switch_is_not_this_accounts(self):
        # deep read: a '?' entry written for the account before this one would keep Fable refused here
        refused = {'?': {'fable': {'at': NOW - 60, 'until': None, 'kind': 'limit_model'}}}
        s = snap({'a': reading('a', week=20, fable=50)})
        self.assertTrue(plan(s, refused=refused, since=NOW - 30)['models']['fable']['room'])
        self.assertFalse(plan(s, refused=refused, since=NOW - 120)['models']['fable']['room'])
        self.assertFalse(plan(s, refused=refused)['models']['fable']['room'])       # since not known: as before

    def test_an_account_with_no_email_is_named_by_its_label(self):
        accts = (dataclasses.replace(account('a', 1), email=None, alias='work'), account('b', 2))
        s = snap({'a': reading('a', week=76, fable=100)}, accounts=accts)
        p = plan(s)
        self.assertNotIn('None', p['words'])
        self.assertTrue(p['words'].startswith('Fable is used up on work until '), p['words'])

    def test_her_pick_promises_no_move(self):
        # deep read: under a pick by hand the plan said "until Switchboard moves to b" and next.when 'calm'
        s = snap({'a': reading('a', week=76, fable=100), 'b': reading('b', week=0, fable=0)}, pick_age_s=600.0)
        p = plan(s)
        self.assertIsNone(p['next'])
        self.assertNotIn('moves to', p['words'])
        self.assertIn('Chats run on Opus 5.5: you picked a@acct.example, so Claude Code stays there until ', p['words'])
        s = dataclasses.replace(s, settings=policy.Settings(enabled=True, pick_hold='until_out'))
        self.assertTrue(plan(s)['words'].endswith('so Claude Code stays there until a limit makes it move.'))


class NoFlapping(unittest.TestCase):
    def test_99_100_99_100_steps_down_once_and_back_up_only_at_95(self):
        seq = [99, 100, 99, 100, 99, 97, 95, 96]
        want = [True, False, False, False, False, False, True, True]
        prev, got = None, []
        for i, pct in enumerate(seq):
            now = NOW + 60 * i
            s = snap({'a': reading('a', week=20, fable=pct, now=now)}, now=now)
            prev = plan(s, prev=prev)
            got.append(prev['models']['fable']['room'])
        self.assertEqual(got, want)

    def test_held_at_96_to_99_reads_at_not_used_up(self):
        prev = plan(snap({'a': reading('a', week=20, fable=100)}))
        p = plan(snap({'a': reading('a', week=20, fable=99)}), prev=prev)
        self.assertFalse(p['models']['fable']['room'])
        self.assertTrue(p['words'].startswith('Fable is at 99% on a@acct.example. Chats run on Opus 5.5'), p['words'])
        self.assertNotIn('used up', p['words'])

    def test_old_numbers_do_not_give_room_back_before_the_reset(self):
        # review S2: after a late read the plan gave Fable its room back while its window was at 100
        def stale(fable_age, other_age, now):
            ws = (api.Window('5-hour', 30, now + 3 * H, now - other_age, 'endpoint'),
                  api.Window('weekly', 80, now + 4 * D, now - other_age, 'endpoint'),
                  api.Window('model:Fable', 100 if fable_age > 900 else 95, NOW + 3 * D, now - fable_age, 'endpoint'))
            return api.Reading('a', ws, now - other_age, 'endpoint', False, None)
        prev = plan(snap({'a': reading('a', week=80, fable=100)}))
        self.assertFalse(prev['models']['fable']['room'])
        later = NOW + 1200
        p = plan(snap({'a': stale(901, 0, later)}, now=later), prev=prev)            # Fable's own number is old
        self.assertFalse(p['models']['fable']['room'])
        self.assertEqual(p['models']['fable']['until'], NOW + 3 * D)
        p = plan(snap({'a': stale(901, 901, later)}, now=later), prev=p)              # every number is old
        self.assertFalse(p['models']['fable']['room'])
        self.assertTrue(plan(snap({'a': stale(0, 0, later)}, now=later), prev=p)['models']['fable']['room'])  # 95
        gone = NOW + 3 * D + 60
        self.assertTrue(plan(snap({'a': stale(901, 901, gone)}, now=gone), prev=p)['models']['fable']['room'])

    def test_back_up_when_the_account_changes_or_the_reset_passes(self):
        s = snap({'a': reading('a', week=20, fable=100)})
        prev = plan(s)
        self.assertFalse(prev['models']['fable']['room'])
        later = NOW + 3 * D + 60
        s2 = snap({'a': reading('a', week=20, fable=99, now=later)}, now=later)
        self.assertTrue(plan(s2, prev=prev)['models']['fable']['room'])        # its reset has passed
        s3 = dataclasses.replace(snap({'a': reading('a', week=20, fable=99), 'b': reading('b', week=20, fable=99)}),
                                 active='b')
        self.assertTrue(plan(s3, prev=prev)['models']['fable']['room'])


class TheFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='room-')
        self.d = os.path.join(self.tmp, 'running')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_written_owner_only_and_whole(self):
        p = plan(snap({'a': reading('a', week=20, fable=100), 'b': reading('b', week=0, fable=0)}))
        self.assertTrue(room.write(p, self.d))
        self.assertEqual(stat.S_IMODE(os.stat(self.d).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.d, 'room.json')).st_mode), 0o600)
        with open(os.path.join(self.d, 'room.json')) as f:
            self.assertEqual(json.load(f), p)
        self.assertEqual(room.read_plan(self.d), p)
        self.assertEqual([n for n in os.listdir(self.d) if n.endswith('.tmp')], [])

    def test_a_write_that_fails_says_false_and_never_raises(self):
        with open(self.d, 'w') as f:
            f.write('a file where the folder goes')
        self.assertFalse(room.write({'v': 1}, self.d))

    def test_wants_count_six_hours_and_old_files_go(self):
        w = os.path.join(self.d, 'wants')
        os.makedirs(w)

        def put(name, rec, age):
            p = os.path.join(w, name)
            with open(p, 'w') as f:
                f.write(rec if isinstance(rec, str) else json.dumps(rec))
            os.utime(p, (NOW - age, NOW - age))
        put('s1.json', {'v': 1, 'at': NOW - 60, 'session': 's1', 'asked': 'fable', 'sent': 'opus', 'agent': False}, 60)
        put('s2.json', {'v': 1, 'at': NOW - 7 * H, 'session': 's2', 'asked': 'sonnet', 'sent': 'sonnet',
                        'agent': False}, 7 * H)
        put('s3.json', {'v': 2, 'at': NOW, 'asked': 'haiku'}, 0)
        put('s4.json', 'not json', 0)
        put('s5.json', {'v': 1, 'at': NOW - 8 * D, 'asked': 'opus'}, 8 * D)
        put('s6.json', {'v': 1, 'at': NOW - 30, 'asked': 'gpt'}, 30)
        self.assertEqual(room.read_wants(NOW, self.d), {'fable': NOW - 60})
        self.assertFalse(os.path.exists(os.path.join(w, 's5.json')))
        self.assertEqual(room.read_wants(NOW, os.path.join(self.tmp, 'nothing')), {})

    def test_refused_and_put_back_files_are_read_forgivingly(self):
        os.makedirs(self.d)
        self.assertEqual(room.read_refused(self.d), {})
        with open(os.path.join(self.d, 'refused.json'), 'w') as f:
            json.dump({'v': 1, 'by_account': {'a': {'fable': {'at': NOW}}}}, f)
        self.assertEqual(room.read_refused(self.d), {'a': {'fable': {'at': NOW}}})
        self.assertEqual(room.read_put_back(self.tmp), (True, None))
        with open(os.path.join(self.tmp, 'put-back.json'), 'w') as f:
            json.dump({'missing': None, 'wrote_at': NOW - 5}, f)
        self.assertEqual(room.read_put_back(self.tmp), (True, NOW - 5))
        with open(os.path.join(self.tmp, 'put-back.json'), 'w') as f:
            json.dump({'missing': ['a@acct.example', NOW - 5], 'wrote_at': NOW - 500}, f)
        self.assertEqual(room.read_put_back(self.tmp), (False, None))


if __name__ == '__main__':
    unittest.main()
