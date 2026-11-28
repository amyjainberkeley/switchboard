import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# History keeps the whole sentence of a move that keeps Claude Code signed in. The sentence is the switch line's
# `reason`; cut at 160 characters like the other facts, it would lose "Sign in to <a> again when you like." with two
# 15-character emails, and cut an email in the middle with longer ones. `reason` has room for 300
# (history.REASON_MAX), and the sentence (policy.stay_note) is built to fit that room as the notification's body is:
# why, where Claude Code is now and the sign-in-again sentence always, each email whole; then the optional parts, each
# whole or not at all: here that automatic switching is off first (a move made with it off always says so when that
# fits), then that the numbers are old. Read back from history.jsonl as the page's History and `switchboard log` read
# it. Made-up *.example accounts.
import dataclasses
import json
import os
import random
import shutil
import tempfile
import time
import unittest
from unittest import mock

from fakes_autoswitch import T0, FakeClock, FakeNet, account, set_policy
from fakes_accounts import Rig
from switchboard import history
from switchboard.accounts import api, autoloop, context, policy, putback, roster, sessions, tokens

H, D = 3600.0, 86400.0
NOW = T0
LONG_A = 'a' * 27 + '@work.example'                     # 40 characters each
LONG_B = 'b' * 27 + '@home.example'
LOST_WORDS = dict(putback.MARKS)
LIMIT_WORDS = ('was put back again and again within minutes and was lost again each time',
               'was put back %d times within an hour and was lost again each time' % putback.PUT_BACK_MAX)
OFF = ', although automatic switching is off'


def win(name, pct, resets=None, age=0.0):
    return api.Window(name, pct, resets, NOW - age, 'endpoint')


def reading(aid, five=10.0, week=20.0, age=0.0):
    return api.Reading(aid, (win('5-hour', five, NOW + 3 * H, age), win('weekly', week, NOW + 4 * D, age)),
                       NOW - age, 'endpoint', False, None)


def accounts(a=LONG_A, b=LONG_B):
    return tuple(dataclasses.replace(account(aid, n), email=e, refresh_expires_at=NOW + 20 * D, fingerprint='fp-' + aid)
                 for aid, n, e in (('a', 1, a), ('b', 2, b)))


def snap(a=LONG_A, b=LONG_B, readings=None, enabled=True, **kw):
    return policy.Snapshot(now=NOW, accounts=accounts(a, b), readings=readings or {'a': reading('a'),
                                                                                   'b': reading('b')},
                           settings=policy.Settings(enabled=enabled), active='a', idle_for_s=1800.0,
                           since_switch_s=4 * H, candidates_read=True, **kw)


def lost(words, **kw):
    return snap(lost=words, live_ok=False, live_why='Claude Code is not signed in', **kw)


def every_move(a=LONG_A, b=LONG_B):
    """(kind, snapshot, the old-numbers words or None) for every kind of move: lost for each mark and the put-back's
    two limits, ends, ended; automatic switching on and off; fresh, old and unknown numbers."""
    numbers = (({'a': reading('a'), 'b': reading('b')}, None),
               ({'a': reading('a'), 'b': reading('b', age=2 * H + 300)}, '2 h 5 min old (5-hour 10%, weekly 20%)'),
               ({'a': reading('a')}, 'not known yet'))
    for readings, old in numbers:
        for enabled in (True, False):
            base = dict(a=a, b=b, readings=readings, enabled=enabled)
            for words in [LOST_WORDS[k] for k in autoloop.LOST_MARKS] + list(LIMIT_WORDS):
                yield 'lost', lost(words, **base), old
            if old is None:             # a sign-in that still works for 10 minutes never moves on old numbers
                yield 'ends', snap(ends_at=NOW + 600, **base), old
            yield 'ended', snap(ends_at=NOW - 1, **base), old


def whole(why, a, b, old=None, off=False):
    """The history's sentence from its parts, in the order it reads."""
    return '%s. Switchboard moved Claude Code to %s%s.%s Sign in to %s again when you like.' % (
        why, b, OFF if off else '', (" %s's numbers are %s." % (b, old)) if old else '', a)


def why_of(kind, s, a):
    if kind == 'lost':
        return '%s lost its sign-in and its saved login %s' % (a, s.lost)
    return "%s's sign-in ends in 10 minutes" % a if kind == 'ends' else "%s's sign-in has ended" % a


# when even what must stay does not fit with the account named in the why, the why is the notification's one clause
# (the account left is still named, whole, in the sign-in-again sentence)
WHY_SHORT = {'lost': 'Claude Code lost its sign-in', 'ends': "Claude Code's sign-in ends in 10 minutes",
             'ended': "Claude Code's sign-in has ended"}


class Folder(unittest.TestCase):
    def setUp(self):
        self._old_home = os.environ.get('SWITCHBOARD_HOME')
        self.tmp = tempfile.mkdtemp(prefix='history-move-')
        os.environ['SWITCHBOARD_HOME'] = os.path.join(self.tmp, 'data')
        os.makedirs(os.environ['SWITCHBOARD_HOME'])

    def tearDown(self):
        if self._old_home is None:
            os.environ.pop('SWITCHBOARD_HOME', None)
        else:
            os.environ['SWITCHBOARD_HOME'] = self._old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def lines(self):
        with open(history.path(), encoding='utf-8') as f:
            return [json.loads(x) for x in f.read().splitlines() if x.strip()]


class TheMovesSentence(Folder):
    def note(self, s):
        d = policy.decide(s)
        self.assertEqual((d.action, d.target), ('switch', 'b'), d.reason)
        return policy.switch_note(d, s)

    def test_with_two_40_character_emails_every_move_is_kept_whole_with_both_emails(self):
        n = 0
        for kind, s, old in every_move():
            note = self.note(s)
            with self.subTest(kind=kind, lost=s.lost, enabled=s.settings.enabled, old=old):
                # as the switch records itself (switcher._critical's line, through core.event)
                history.from_event('Switched Claude Code to %s' % LONG_B, 'switch',
                                   {'h': 'switch', 'account': LONG_B, 'to': LONG_B, 'frm': LONG_A, 'reason': note,
                                    'by': 'auto'})
                rec = self.lines()[-1]
                self.assertEqual(rec['reason'], note)                       # nothing cut
                self.assertLessEqual(len(note), history.REASON_MAX)
                self.assertNotIn('…', rec['reason'])
                self.assertIn(LONG_A, rec['reason'])
                self.assertIn(LONG_B, rec['reason'])
                self.assertTrue(rec['reason'].startswith(why_of(kind, s, LONG_A) + '. Switchboard moved Claude '
                                                         'Code to %s' % LONG_B), rec['reason'])
                self.assertTrue(rec['reason'].endswith(' Sign in to %s again when you like.' % LONG_A), rec['reason'])
                self.assertIn(rec['reason'], [whole(why_of(kind, s, LONG_A), LONG_A, LONG_B, o, f)
                                              for o in (None, old) for f in (False, True)])
                self.assertEqual(history.parts(rec), ('%s → %s' % (LONG_A, LONG_B), 'auto: ' + note))
                n += 1
        self.assertEqual(n, 3 * 2 * (len(autoloop.LOST_MARKS) + 2 + 1) + 2)

    def test_short_emails_keep_every_part(self):
        s = lost(LOST_WORDS['refused'], a='a@acct.example', b='b@acct.example', enabled=False,
                 readings={'a': reading('a'), 'b': reading('b', age=2 * H + 300)})
        self.assertEqual(self.note(s), whole('a@acct.example lost its sign-in and its saved login was refused by '
                                             'Anthropic', 'a@acct.example', 'b@acct.example',
                                             '2 h 5 min old (5-hour 10%, weekly 20%)', True))

    def test_with_switching_off_it_says_so_before_the_old_numbers_when_both_do_not_fit(self):
        a, b = 'p' * 8 + '.one@mail2.example', 'p' * 8 + '.two@mail1.example'           # 26 characters each
        s = lost(LOST_WORDS['refused'], a=a, b=b, enabled=False,
                 readings={'a': reading('a'), 'b': reading('b', five=100.0, week=31.6, age=4 * H + 21 * 60)})
        why = why_of('lost', s, a)
        both = whole(why, a, b, '4 h 21 min old (5-hour 100%, weekly 31.6%)', True)
        self.assertGreater(len(both), history.REASON_MAX)
        self.assertEqual(self.note(s), whole(why, a, b, None, True))

    def test_the_rule_holds_over_many_made_up_emails(self):
        rnd = random.Random(5309)
        kept = short = skipped = 0
        for n in range(3000):
            la, lb = rnd.randint(12, 64), rnd.randint(12, 80)
            a = 'x' * (la - len('@a.example')) + '@a.example'
            b = 'y' * (lb - len('@b.example')) + '@b.example'
            kind = rnd.choice(('lost', 'ends', 'ended'))
            enabled = rnd.random() < 0.5
            age = rnd.choice((0.0, 2 * H + 300, None))
            readings = {'a': reading('a')}
            if age is not None:
                readings['b'] = reading('b', age=age)
            old = None if age == 0.0 else '2 h 5 min old (5-hour 10%, weekly 20%)' if age else 'not known yet'
            base = dict(a=a, b=b, readings=readings, enabled=enabled)
            s = lost(rnd.choice([LOST_WORDS[k] for k in autoloop.LOST_MARKS] + list(LIMIT_WORDS)), **base) \
                if kind == 'lost' else snap(ends_at=NOW + 600 if kind == 'ends' else NOW - 1, **base)
            if kind == 'ends' and old is not None:
                # it still works for 10 minutes: no move on old numbers (it reads them first)
                self.assertEqual(policy.decide(s).hold, 'no fresh numbers')
                skipped += 1
                continue
            note = self.note(s)
            why = why_of(kind, s, a)
            where = (n, la, lb, kind, enabled, old, note)
            if len(whole(why, a, b)) > history.REASON_MAX:
                why = WHY_SHORT[kind]
                short += 1
            kept += 1
            self.assertLessEqual(len(note), history.REASON_MAX, where)       # two emails of 144 characters fit
            self.assertIn(a, note)
            self.assertIn(b, note)
            fits = [(o, f) for o in ((None, old) if old else (None,)) for f in ((False, True) if not enabled else
                                                                                 (False,))
                    if len(whole(why, a, b, o, f)) <= history.REASON_MAX]
            # that it is off first, then the old numbers: the first that fits of the orders below is the sentence
            best = [(o, f) for o, f in ((old, not enabled), (None, not enabled), (old, False), (None, False))
                    if (o, f) in fits][0]
            self.assertEqual(note, whole(why, a, b, *best), where)
        self.assertEqual(kept + skipped, 3000)
        self.assertGreater(kept, 2000)
        self.assertGreater(short, 100)                          # the one-clause why was needed, and fit

    def test_emails_too_long_to_name_the_account_twice_keep_both_whole_with_the_one_clause_why(self):
        a, b = 'x' * 50 + '@a.example', 'y' * 70 + '@b.example'
        s = lost(LIMIT_WORDS[0], a=a, b=b, enabled=False)
        self.assertGreater(len(whole(why_of('lost', s, a), a, b)), history.REASON_MAX)
        self.assertEqual(self.note(s), whole('Claude Code lost its sign-in', a, b, off=True))
        self.assertLessEqual(len(self.note(s)), history.REASON_MAX)

    def test_the_room_is_historys_and_the_notification_keeps_its_own(self):
        self.assertEqual(policy.HISTORY_ROOM, history.REASON_MAX)
        s = lost(LOST_WORDS['refused'], enabled=False)
        d = policy.decide(s)
        self.assertLessEqual(len(policy.stay_note(d, s, short=True)), policy.NOTE_ROOM)
        self.assertTrue(policy.stay_note(d, s, short=True).startswith('Claude Code lost its sign-in. '))


class OtherFacts(Folder):
    def test_a_reason_of_another_kind_longer_than_300_is_still_cut_and_other_facts_at_160(self):
        long_reason = ' '.join('word%d' % i for i in range(80))                 # about 470 characters
        history.record('limit', 'made-up line', account='x' * 200 + '@acct.example', reason=long_reason)
        rec = self.lines()[-1]
        self.assertEqual(len(rec['reason']), history.REASON_MAX)
        self.assertTrue(rec['reason'].endswith('…'))
        self.assertEqual(len(rec['account']), history.FACT_MAX)
        self.assertTrue(rec['account'].endswith('…'))
        self.assertEqual((history.REASON_MAX, history.FACT_MAX, history.TEXT_MAX), (300, 160, 300))


class ThroughTheRealSwitch(unittest.TestCase):
    """The move itself: a sign-in Anthropic refused, two 40-character emails, automatic switching off; the real
    account engine on its stand-ins records the switch line through core.event into history.jsonl."""

    def setUp(self):
        self._old_home = os.environ.get('SWITCHBOARD_HOME')
        self.tmp = tempfile.mkdtemp(prefix='history-move-')
        os.environ['SWITCHBOARD_HOME'] = os.path.join(self.tmp, 'data')
        os.makedirs(os.environ['SWITCHBOARD_HOME'])
        sessions._reset_for_tests()
        self.clock = FakeClock(wall=time.time(), mono=time.monotonic())
        self.rig = Rig(wall=self.clock.wall, mono=self.clock.mono, sleep=self.clock.sleep)
        self.use = context.use(self.rig.ctx)
        self.use.__enter__()
        putback.reset()
        roster.forget_live()
        self.rig.keep('uuid-a', LONG_A, 'org-a')
        self.clock.advance(1.0)
        self.rig.keep('uuid-b', LONG_B, 'org-b')                    # Claude Code is on B
        self.rig.ctx.events = context._core_event                   # the history as the installed program writes it
        self.moved = []
        patcher = mock.patch.object(autoloop, 'notify_put_back', lambda res: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        deps = autoloop.Deps(net=FakeNet(self.clock), net_ready=lambda: True, load=lambda: 0.5,
                             power=lambda: (True, 100), procs=lambda: 1, disk_free=lambda: 10 ** 12,
                             history_note=lambda text, kind='info', **f: None, notify=lambda res: None,
                             notify_move=self.moved.append)
        self.loop = autoloop.Loop(deps=deps, clock=self.clock, rand=lambda: 0.5)
        set_policy(enabled=False)

    def tearDown(self):
        self.use.__exit__(None, None, None)
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        putback.reset()
        roster.forget_live()
        self.rig.close()
        sessions._reset_for_tests()
        if self._old_home is None:
            os.environ.pop('SWITCHBOARD_HOME', None)
        else:
            os.environ['SWITCHBOARD_HOME'] = self._old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_line_read_back_holds_both_emails_and_the_sign_in_again_sentence(self):
        blob = json.loads(self.rig.live_text())                     # Anthropic refused the renewal
        blob['claudeAiOauth'].update({'accessToken': '', 'refreshToken': ''})
        self.rig.set_live(json.dumps(blob, separators=(',', ':')))
        roster.forget_live()
        for _ in range(3):
            self.loop.run_pass()
            self.clock.advance(61.0)
        with open(history.path(), encoding='utf-8') as f:
            recs = [json.loads(x) for x in f.read().splitlines() if x.strip()]
        switches = [r for r in recs if r['kind'] == 'switch']
        self.assertEqual(len(switches), 1, recs)
        self.assertEqual(switches[0]['reason'],
                         "%s's sign-in was refused by Anthropic. Switchboard moved Claude "
                         'Code to %s, although automatic switching is off. Sign in to %s again when you like.'
                         % (LONG_B, LONG_A, LONG_B))
        self.assertEqual(len(self.moved), 1)


if __name__ == '__main__':
    unittest.main()
