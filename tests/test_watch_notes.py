import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The person is told when Switchboard is not doing its job (watch.troubles, watch.Spells, the notes in notify.py, the
# page's watch_tick): auto-switch stopped, stuck or deciding nothing; the account in use blocked with no switch; a
# Keychain dialog unanswered; every number over an hour old. Each said once for each spell, after a few minutes of
# awake time, with the one thing to do; "works again" only for the first two. And NOT said in the normal slow cases: a
# loop that restarts once, a trouble that comes and goes, a wake, a page that restarts. Clocks are stand-ins; no
# notification is ever shown (the page's NOTIFY_RUN is a stand-in that records).
import os
import shutil
import sys
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import core, notify, server, watch
from switchboard.accounts import api, autoloop
from test_server_robust import Sandbox

NOW = 1.8e9
A, B = 'ana@one.example', 'ben@two.example'
DIALOG = autoloop.KEYCHAIN_WORDS['dialog']


def iso(t):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(t))


def account(pct_week=40.0, pct_five=10.0, at=NOW - 60, resets=NOW + 3600, **kw):
    a = {'connected': True, 'usage_at': at,
         'usage': {'session': {'pct': pct_five, 'resets': iso(resets)}, 'weekly': {'pct': pct_week, 'resets': iso(resets)}}}
    a.update(kw)
    return a


def state(active=A, **accounts):
    return {'active': active, 'accounts': accounts or {A: account(), B: account()}}


ON = {'enabled': True, 'on': True, 'problem': None}
BEATING = {'running': True, 'pass_age_s': 30.0, 'last_pass_at': NOW - 30, 'next_pass_at': NOW + 30}


class Clock(object):
    def __init__(self):
        self.mono, self.wall = 5000.0, NOW

    def tick(self, s, wall=None):
        self.mono += s
        self.wall += s if wall is None else wall


class Base(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(core.data_dir(), ignore_errors=True)
        os.makedirs(core.data_dir(), exist_ok=True)
        self.clock = Clock()
        p = mock.patch.object(watch, 'log', lambda *a: None)
        p.start()
        self.addCleanup(p.stop)

    def spells(self):
        return watch.Spells(mono=lambda: self.clock.mono, wall=lambda: self.clock.wall)


# ---------- what counts as a trouble ----------

class WhatIsWrong(unittest.TestCase):
    def find(self, auto=ON, s=BEATING, st=None, decision=None):
        return watch.troubles(auto, s, st or state(), decision, NOW, keychain_words=DIALOG)

    def test_all_well_is_nothing(self):
        self.assertEqual(self.find(), {})

    def test_auto_switch_on_and_its_loop_not_on_is_a_trouble_in_the_pages_own_words(self):
        said = 'Automatic switching stopped (exit 1). It starts again by itself.'
        got = self.find(auto={'enabled': True, 'on': False, 'problem': said})
        self.assertEqual(got['auto']['words'], said)

    def test_a_loop_that_beats_but_decided_nothing_for_minutes_is_a_trouble(self):
        late = dict(BEATING, pass_age_s=14 * 60.0, last_pass_at=NOW - 840, next_pass_at=NOW - 780)
        self.assertEqual(self.find(s=late)['auto']['words'], 'Auto-switch last decided 14 minutes ago.')
        n = notify.auto_stuck(self.find(s=late)['auto']['words'], 1)      # the note: the same fact, the same thing to do
        self.assertEqual(n.body, 'Auto-switch last decided 14 minutes ago. If it stays like this, run: switchboard doctor')

    def test_a_long_planned_wait_is_not_late(self):
        # The loop planned its next pass 10 minutes out (idle, far from any line): 12 minutes since the last is fine.
        idle = dict(BEATING, pass_age_s=12 * 60.0, last_pass_at=NOW - 720, next_pass_at=NOW - 120)
        self.assertEqual(self.find(s=idle), {})

    def test_auto_switch_off_is_never_a_trouble_of_these_kinds(self):
        off = {'enabled': False, 'on': False, 'problem': None}
        st = state(**{A: account(pct_week=100.0, at=NOW - 7200)})
        self.assertEqual(self.find(auto=off, st=st), {})

    def test_the_account_in_use_at_a_limit_with_no_switch_is_blocked_by_its_whole_email(self):
        d = api.Decision('stay', reason='No other account has fresh numbers with room yet.', kind='forced')
        got = self.find(st=state(**{A: account(pct_week=100.0), B: account()}), decision=d)['blocked']
        self.assertEqual(got['who'], A)
        self.assertEqual(got['words'], 'It is at its weekly limit')
        self.assertIn('fresh numbers', got['reason'])

    def test_a_refused_login_is_blocked_and_the_thing_to_do_is_a_sign_in(self):
        got = self.find(st=state(**{A: account(needs_login=True), B: account()}))['blocked']
        self.assertIn('refused', got['words'])
        self.assertIn('Sign in to all', got['todo'])

    def test_a_window_that_has_reset_since_is_not_blocked(self):
        self.assertNotIn('blocked', self.find(st=state(**{A: account(pct_five=100.0, resets=NOW - 60)})))

    def test_a_switch_under_way_is_not_blocked(self):
        d = api.Decision('switch', reason='', target='b')
        self.assertNotIn('blocked', self.find(st=state(**{A: account(pct_week=100.0)}), decision=d))

    def test_another_account_at_its_limit_is_not_the_one_in_use(self):
        self.assertNotIn('blocked', self.find(st=state(**{A: account(), B: account(pct_week=100.0)})))

    def test_the_loop_held_on_a_keychain_dialog_is_a_keychain_trouble(self):
        d = api.Decision('stay', reason='No switch now: %s.' % DIALOG)
        self.assertIn('keychain', self.find(decision=d))

    def test_a_beat_overdue_in_a_stage_that_waits_for_the_keychain_is_a_keychain_trouble(self):
        s = dict(BEATING, waiting_for='the Keychain', stuck_for_s=95.0)
        self.assertIn('keychain', self.find(s=s))
        self.assertNotIn('keychain', self.find(s=dict(s, stuck_for_s=0.0)))     # beating while it waits: in time

    def test_a_keychain_dialog_behind_a_stuck_auto_switch_is_one_note_the_one_that_says_what_to_do(self):
        s = dict(BEATING, waiting_for='the Keychain', stuck_for_s=400.0)
        got = self.find(auto={'enabled': True, 'on': False, 'problem': 'Automatic switching is stuck.'}, s=s)
        self.assertEqual(sorted(got), ['keychain'])

    def test_every_number_over_an_hour_old_is_a_trouble(self):
        st = state(**{A: account(at=NOW - 3700), B: account(at=None)})
        self.assertIn('old', self.find(st=st))

    def test_one_fresh_account_is_enough(self):
        self.assertNotIn('old', self.find(st=state(**{A: account(at=NOW - 3700), B: account(at=NOW - 60)})))

    def test_an_account_that_needs_a_sign_in_is_not_counted_for_old_numbers(self):
        st = state(active=B, **{A: account(at=NOW - 60), B: account(at=NOW - 9000, needs_login=True)})
        self.assertNotIn('old', self.find(st=st))

    def test_old_numbers_while_the_mac_is_offline_are_no_trouble(self):
        # The loop says it has had no network since then: the person can't mend that from Switchboard, and it mends
        # itself when the network is back (the reviewer's old_offline.py: a wake with no network, eight hours on).
        st = state(**{A: account(at=NOW - 8 * 3600), B: account(at=NOW - 8 * 3600)})
        self.assertNotIn('old', self.find(st=st, s=dict(BEATING, offline_since=NOW - 60)))
        self.assertIn('old', self.find(st=st, s=dict(BEATING, offline_since=None)))

    def test_the_old_numbers_note_says_where_to_see_why(self):
        # Doctor has no line about old numbers; the page's rows say when each account was last checked, and the
        # reason a read failed is on hover (engine.note_of).
        n = notify.numbers_old(1)
        self.assertEqual(n.body, "Every account's numbers are over an hour old, so auto-switch can't tell when to move "
                                 "Claude Code. Open Switchboard: each account says when it was last checked, and why on "
                                 "hover.")
        self.assertNotIn('switchboard doctor', n.body)


# ---------- once for each spell ----------

class AWakeWithNoNetwork(Base):
    def test_no_note_while_the_loop_says_it_is_offline(self):
        # The reviewer's old_offline.py: the Mac slept eight hours and wakes with no network (a plane, a hotel portal);
        # auto-switch on, the loop alive and saying it is offline. A look a minute for seven awake minutes: no note.
        sp, sent = self.spells(), []
        for _ in range(8):
            now = self.clock.wall
            st = state(**{A: account(at=now - 8 * 3600 - 10), B: account(at=now - 8 * 3600 - 10)})
            s = dict(BEATING, offline_since=now, last_pass_at=now - 30, next_pass_at=now + 60)
            sent += sp.look(watch.troubles(ON, s, st, None, now), active=A)
            self.clock.tick(60)
        self.assertEqual(sent, [])


class OneCauseOneNote(Base):
    def test_old_numbers_are_not_said_while_the_no_sign_in_spell_is_open(self):
        # After hours without a sign-in the numbers go old too: the first note already said what to do. Old numbers
        # count their five minutes only once that spell is over.
        sp, sent = self.spells(), []
        lost = {'key': 'signin:%s' % A, 'who': A, 'since': self.clock.wall, 'hold': False}
        for _ in range(4 * 60):                         # four hours without a sign-in, numbers old for most of them
            sent += sp.look({'signin': lost, 'old': {'key': 'old'}})
            self.clock.tick(60)
        self.assertEqual([n.key for n in sent], ['signin-lost'])
        for _ in range(4):                              # signed in again; the numbers still old for a few minutes
            sent += sp.look({'old': {'key': 'old'}}, active=A)
            self.clock.tick(60)
        self.assertEqual([n.key for n in sent], ['signin-lost', 'signin-back'])
        for _ in range(10):                             # old long after the sign-in came back: then it is its own cause
            sent += sp.look({'old': {'key': 'old'}}, active=A)
            self.clock.tick(60)
        self.assertEqual([n.key for n in sent][-1], 'numbers-old')


class OncePerSpell(Base):
    def test_a_trouble_is_said_after_five_minutes_of_awake_time_and_once(self):
        sp = self.spells()
        found = {'keychain': {'key': 'keychain'}}
        sent = []
        for _ in range(30):                             # 30 looks a minute apart: a long spell
            sent += sp.look(found)
            self.clock.tick(60)
        self.assertEqual([n.key for n in sent], ['keychain-waiting'])
        self.assertIn('Answer it', sent[0].body)

    def test_not_before_five_minutes(self):
        sp = self.spells()
        sent = []
        for _ in range(4):
            sent += sp.look({'old': {'key': 'old'}})
            self.clock.tick(60)
        self.assertEqual(sent, [])

    def test_a_trouble_that_comes_and_goes_is_one_spell(self):
        sp = self.spells()
        on, off = {'auto': {'key': 'auto', 'words': 'Automatic switching is stuck.'}}, {}
        sent = []
        for i in range(20):
            sent += sp.look(on if i % 3 else off)       # gone for a minute now and then: under GOOD_FOR
            self.clock.tick(60)
        self.assertEqual([n.key for n in sent], ['auto-stuck'])

    def test_a_loop_restarted_once_on_a_busy_mac_is_never_said(self):
        sp = self.spells()
        sent = sp.look({'auto': {'key': 'auto', 'words': 'Automatic switching stopped (exit 1).'}})
        for _ in range(10):
            self.clock.tick(30)
            sent += sp.look({})
        self.assertEqual(sent, [])

    def test_works_again_is_said_once_for_auto_switch(self):
        sp = self.spells()
        sent = []
        for _ in range(6):
            sent += sp.look({'auto': {'key': 'auto', 'words': 'Automatic switching is stuck.'}})
            self.clock.tick(60)
        for _ in range(6):
            sent += sp.look({})
            self.clock.tick(60)
        self.assertEqual([n.key for n in sent], ['auto-stuck', 'auto-fine'])

    def test_no_works_again_for_a_keychain_dialog_or_old_numbers(self):
        sp = self.spells()
        sent = []
        for _ in range(6):
            sent += sp.look({'keychain': {'key': 'keychain'}, 'old': {'key': 'old'}})
            self.clock.tick(60)
        for _ in range(6):
            sent += sp.look({})
            self.clock.tick(60)
        self.assertEqual(sorted(n.key for n in sent), ['keychain-waiting', 'numbers-old'])

    def blocked(self, who=A):
        return {'blocked': {'key': 'blocked:%s' % who, 'who': who, 'words': 'It is at its weekly limit',
                            'reason': 'No other account has room.', 'todo': 'Open Switchboard.'}}

    def test_an_account_that_can_work_again_while_still_in_use_is_said(self):
        sp = self.spells()
        sent = []
        for _ in range(6):
            sent += sp.look(self.blocked(), active=A)
            self.clock.tick(60)
        for _ in range(3):
            sent += sp.look({}, active=A)
            self.clock.tick(60)
        self.assertEqual([n.title for n in sent], ["Claude Code can't work on %s" % A, 'Claude Code can work again'])
        self.assertIn(A, sent[1].body)

    def test_a_switch_away_is_not_said_again_here(self):
        sp = self.spells()
        sent = []
        for _ in range(6):
            sent += sp.look(self.blocked(), active=A)
            self.clock.tick(60)
        for _ in range(3):
            sent += sp.look({}, active=B)               # moved: the switch's own note says so
            self.clock.tick(60)
        self.assertEqual(len(sent), 1)

    def test_nothing_from_before_a_wake_counts(self):
        sp = self.spells()
        sent = []
        for _ in range(4):
            sent += sp.look({'old': {'key': 'old'}})
            self.clock.tick(60)
        self.clock.tick(0, wall=8 * 3600)               # the Mac slept eight hours: every number is old at the wake
        for _ in range(5):                              # the first look sees the wake; the fifth is 4 min after it
            sent += sp.look({'old': {'key': 'old'}})
            self.clock.tick(60)
        self.assertEqual(sent, [])                      # four minutes of awake time after the wake: not yet
        sent += sp.look({'old': {'key': 'old'}})
        self.assertEqual([n.key for n in sent], ['numbers-old'])

    def test_a_page_that_starts_again_goes_on_with_its_spells(self):
        sp = self.spells()
        found = {'auto': {'key': 'auto', 'words': 'Automatic switching is stuck.'}, 'old': {'key': 'old'}}
        sent = []
        for _ in range(6):
            sent += sp.look({'auto': found['auto']})
            self.clock.tick(60)
        for _ in range(3):
            sent += sp.look(found)
            self.clock.tick(60)
        self.assertEqual([n.key for n in sent], ['auto-stuck'])
        self.clock.tick(30)
        again = self.spells()                           # a fresh page process
        later = []
        for _ in range(4):
            later += again.look(found)
            self.clock.tick(60)
        # The two minutes 'old' had went on (the time the page was down does not count); auto is not said twice.
        self.assertEqual([n.key for n in later], ['numbers-old'])

    def test_nothing_wrong_writes_nothing_minute_after_minute(self):
        sp = self.spells()
        sp.look({})
        path = core.data_path(watch.SPELLS)
        os.remove(path)
        for _ in range(5):
            self.clock.tick(60)
            sp.look({})
        self.assertFalse(os.path.exists(path))
        sp.look({'old': {'key': 'old'}})
        self.assertTrue(os.path.exists(path))

    def test_a_spell_seen_long_ago_is_a_new_one(self):
        sp = self.spells()
        for _ in range(6):
            sp.look({'keychain': {'key': 'keychain'}})
            self.clock.tick(60)
        self.clock.tick(watch.SAME_SPELL + 60)
        again = self.spells()
        sent = []
        for _ in range(6):
            sent += again.look({'keychain': {'key': 'keychain'}})
            self.clock.tick(60)
        self.assertEqual([n.key for n in sent], ['keychain-waiting'])


# ---------- the words ----------

class TheWords(unittest.TestCase):
    def test_auto_stuck_names_the_one_thing_to_do_once(self):
        n = notify.auto_stuck('Automatic switching stopped (exit 1). It starts again by itself.', 1)
        self.assertTrue(n.body.endswith(notify.DOCTOR))
        said = 'Automatic switching is stuck: it was restarted 3 times. Run: switchboard doctor'
        self.assertEqual(notify.auto_stuck(said, 1).body.count('switchboard doctor'), 1)

    def test_blocked_names_the_account_why_and_what_to_do(self):
        n = notify.blocked(A, 'It is at its weekly limit', 5, 'No other account has room.', 'Open Switchboard.', 1)
        self.assertIn(A, n.title)
        self.assertEqual(n.body, 'It is at its weekly limit. No switch was possible for 5 min: No other account has '
                                 'room. Open Switchboard.')

    def test_no_word_the_rules_forbid(self):
        notes = [notify.auto_stuck('x', 1), notify.auto_fine(1), notify.blocked(A, 'w', 5, 'r', 't', 1),
                 notify.blocked_fine(A, 1), notify.keychain_waiting(5, 1), notify.numbers_old(1)]
        for n in notes:
            text = (n.title + ' ' + n.body).lower()
            for bad in ('unlimited', 'never run out', 'rotate', 'pool', 'bypass', 'avoid', 'dodge', 'where it went',
                        ' her '):
                self.assertNotIn(bad, text, n)
            self.assertLessEqual(len(n.body), 240)


# ---------- the page sends them from its tick ----------

class ThePagesTick(Sandbox):
    def setUp(self):
        Sandbox.setUp(self)
        self.clock = Clock()
        self.patch(server, 'SPELLS', watch.Spells(mono=lambda: self.clock.mono, wall=lambda: self.clock.wall))
        self.patch(server.engine, 'auto_settings', lambda: {'enabled': True, 'on': False,
                                                           'problem': 'Automatic switching is stuck. Switchboard '
                                                                      'restarts it.'})
        self.patch(api, 'auto_status', lambda: {})
        self.patch(server, 'load', lambda: state())

    def test_a_stuck_auto_switch_is_told_once_after_five_minutes(self):
        sent = []
        for _ in range(8):
            sent += server.watch_tick(now=self.clock.wall)
            self.clock.tick(60)
        self.assertEqual([n.key for n in sent], ['auto-stuck'])
        self.assertEqual(len(self.shown), 1)            # through the page's stand-in, never a real notification
        self.assertIn('Auto-switch is not working', self.shown[0])

    def test_nothing_while_notifications_are_off(self):
        core.set_setting('notify', False)
        for _ in range(8):
            server.watch_tick(now=self.clock.wall)
            self.clock.tick(60)
        self.assertEqual(self.shown, [])

    def test_nothing_from_the_demo(self):
        core.DEMO = True
        self.assertEqual(server.watch_tick(), [])


if __name__ == '__main__':
    unittest.main()
