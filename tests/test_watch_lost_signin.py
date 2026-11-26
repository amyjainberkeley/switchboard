import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Claude Code without a sign-in, untold for minutes: the case of a night when Claude Code had no sign-in for hours and
# nothing said so. When its login is lost and the put-back can put it back, it does within seconds and its own note
# says so: nothing more is said. When the put-back can't (its rules forbid it, nothing usable is kept for that account,
# or it has stopped for now after repeated losses), one note says so after five awake minutes, with the one thing to
# do, and "signed in again" when a sign-in is back. Never for a sign-out the person chose (the put-back's own record
# never holds one), never while a sign-in from the page is under way, never together with the put-back's own note for
# the same loss. Clocks are stand-ins; no notification is shown.
import os
import shutil
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from fakes_accounts import Rig
from switchboard import core, notify, server, watch
from switchboard.accounts import api, context, putback
from test_server_robust import Sandbox
from test_watch_notes import BEATING, NOW, ON, Clock, account

A, B = 'ana@one.example', 'ben@two.example'
STAY = api.Decision('stay', reason='No switch now: Claude Code is not signed in.')


def page(active=None):
    return {'active': active, 'accounts': {A: account(), B: account()}}


def lost(hold=False, since=NOW, who=A):
    return {'account': who, 'since': since, 'hold': hold}


class Base(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(core.data_dir(), ignore_errors=True)
        os.makedirs(core.data_dir(), exist_ok=True)
        self.clock = Clock()
        self.spells = watch.Spells(mono=lambda: self.clock.mono, wall=lambda: self.clock.wall)

    def find(self, gone=None, active=None, auto=ON):
        return watch.troubles(auto, BEATING, page(active), STAY, self.clock.wall, lost=gone)

    def run_for(self, seconds, gone, active=None, back_at=None, step=20):
        notes = []
        for _ in range(int(seconds // step)):
            notes += self.spells.look(self.find(gone, active), active=active, back_at=back_at)
            self.clock.tick(step)
        return notes


class WhatCounts(Base):
    def test_a_loss_the_put_back_could_not_mend_is_a_trouble_naming_the_account(self):
        got = self.find(lost())
        self.assertEqual(got['signin']['key'], 'signin:%s' % A)
        self.assertEqual(got['signin']['who'], A)

    def test_nothing_without_a_loss_or_with_auto_switch_off(self):
        self.assertNotIn('signin', self.find(None))
        self.assertNotIn('signin', self.find(lost(), auto={'enabled': False, 'on': False}))

    def test_a_dead_loops_record_is_not_news(self):
        # The put-back's record is written by the loop. With the loop down longer than the page lets a loop go without
        # a beat (engine.AutoHost.STALE), or not running at all, a loss in it may be long over (the person signed in
        # again meanwhile): not said, beside the true "auto-switch is not working".
        from switchboard import engine
        down = {'enabled': True, 'on': False, 'problem': 'Automatic switching is stuck.'}
        for s in (dict(BEATING, stuck_for_s=engine.AutoHost.STALE + 60), dict(BEATING, running=False)):
            with self.subTest(status=s):
                got = watch.troubles(down, s, page(None), STAY, self.clock.wall, lost=lost())
                self.assertNotIn('signin', got)
                self.assertIn('auto', got)
        slow = dict(BEATING, stuck_for_s=engine.AutoHost.STALE - 30)          # late, not dead: still said
        self.assertIn('signin', watch.troubles(ON, slow, page(None), STAY, self.clock.wall, lost=lost()))


class OneNoteForTheSpell(Base):
    def test_told_once_after_five_awake_minutes_with_the_one_thing_to_do(self):
        notes = self.run_for(15 * 60, lost(since=self.clock.wall))
        self.assertEqual([n.key for n in notes], ['signin-lost'])
        n = notes[0]
        self.assertEqual(n.title, 'Claude Code is not signed in')
        # the one press on the page that writes Claude Code's sign-in in every state the note is sent in: Use on an
        # account whose kept sign-in works (a switch writes it whole, from no login too), or, when none works,
        # Sign in on the account the settings name (its new sign-in is put in use at once: roster._dead_live)
        self.assertEqual(n.body, 'Claude Code has had no sign-in for 5 min, and Switchboard could not put %s back. '
                                 'Open Switchboard and press Use on an account, or Sign in on %s.' % (A, A))
        self.assertNotIn('Sign in to all', n.body)      # not on the page in every one of these states

    def test_within_five_awake_minutes_of_the_loss_the_put_back_first_saw(self):
        self.spells.look(self.find(None))               # the page looks every tick, all well
        self.clock.tick(20)
        since = self.clock.wall - 15                    # the loop saw it missing 15 s before this page's look
        notes, at = [], None
        for _ in range(80):
            got = self.spells.look(self.find(lost(since=since)))
            if got and at is None:
                at = self.clock.wall
            notes += got
            self.clock.tick(5)
        self.assertEqual(len(notes), 1)
        self.assertLessEqual(at - since, 300 + 5)

    def test_a_sleep_between_two_looks_never_counts(self):
        self.spells.look(self.find(None))
        self.clock.tick(20, wall=3600)                  # the Mac slept an hour; the loss was seen just before
        notes = self.run_for(280, lost(since=self.clock.wall - 1800), step=20)
        self.assertEqual(notes, [])                     # under five awake minutes since this page saw it

    def test_never_while_the_put_back_still_waits_and_never_when_it_lands(self):
        # the fourth loss in an hour: the put-back waits five minutes before it writes, then puts it back
        notes = self.run_for(330, lost(hold=True, since=self.clock.wall), step=10)
        notes += self.run_for(10 * 60, None, active=A, back_at=self.clock.wall)
        self.assertEqual(notes, [])

    def test_once_its_wait_is_over_it_is_said(self):
        notes = self.run_for(60, lost(hold=True, since=self.clock.wall), step=10)
        notes += self.run_for(10 * 60, lost(hold=False, since=NOW), step=10)
        self.assertEqual([n.key for n in notes], ['signin-lost'])


class TheEnd(Base):
    def said(self):
        notes = self.run_for(6 * 60, lost(since=self.clock.wall))
        self.assertEqual([n.key for n in notes], ['signin-lost'])

    def test_a_sign_in_back_by_the_person_is_said_once(self):
        self.said()
        notes = self.run_for(5 * 60, None, active=B)
        self.assertEqual([(n.key, n.body) for n in notes], [('signin-back', 'Claude Code is signed in to %s.' % B)])

    def test_a_put_back_that_lands_later_has_its_own_note_and_nothing_more(self):
        self.said()
        self.assertEqual(self.run_for(5 * 60, None, active=A, back_at=self.clock.wall), [])

    def test_a_sign_out_chosen_meanwhile_is_never_called_signed_in(self):
        self.said()
        self.assertEqual(self.run_for(10 * 60, None, active=None), [])


class TheWordsFollowTheRules(unittest.TestCase):
    def test_whole_email_no_forbidden_word(self):
        for n in (notify.signin_lost(A, 5, 1), notify.signin_back(A, 1)):
            self.assertIn(A, n.body)
            text = (n.title + ' ' + n.body).lower()
            for bad in ('unlimited', 'never run out', 'rotate', 'pool', 'bypass', 'avoid', 'dodge', 'where it went',
                        ' her ', ' days'):
                self.assertNotIn(bad, text)


# ---------- the page reads the put-back's own record ----------

class ThePutBacksRecord(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.addCleanup(self.rig.close)
        self.use = context.use(self.rig.ctx)
        self.use.__enter__()
        self.addCleanup(self.use.__exit__, None, None, None)
        self.rig.keep('uuid-a', 'ana@work.example', 'org-a')
        self.ctx = self.rig.ctx
        self.now = time.time()

    def record(self, **rec):
        base = {'missing': None, 'done': [], 'failed': []}
        base.update(rec)
        putback._save_record(self.ctx, base, self.now)

    def test_a_loss_it_holds_names_the_whole_email_and_holds_for_its_first_wait(self):
        self.record(missing=('uuid-a', self.now - 2))
        got, back = server.lost_signin(self.now)
        self.assertEqual((got['account'], got['since'], got['hold'], back), ('ana@work.example', self.now - 2, True,
                                                                             None))
        got, _ = server.lost_signin(self.now + putback.PUT_BACK_WAITS[0] + server.LOST_SLACK + 1)
        self.assertFalse(got['hold'])

    def test_past_its_limit_nothing_waits(self):
        done = [self.now - 60 * (i + 1) for i in range(putback.PUT_BACK_MAX)]
        self.record(missing=('uuid-a', self.now - 1), done=done)
        self.assertFalse(server.lost_signin(self.now)[0]['hold'])

    def test_a_chosen_sign_out_is_never_in_it(self):
        self.record(missing=None, last={'at': self.now - 30, 'account': 'ana@work.example'})
        self.assertEqual(server.lost_signin(self.now), (None, self.now - 30))

    def test_nothing_while_a_sign_in_from_the_page_is_under_way(self):
        self.record(missing=('uuid-a', self.now - 400))
        folder = os.path.join(self.rig.data, putback.SIGNINS, 'made-up-sign-in')
        os.makedirs(folder)
        self.assertEqual(server.lost_signin(self.now)[0], None)


class ThePagesTick(Sandbox):
    def setUp(self):
        Sandbox.setUp(self)
        self.clock = Clock()
        self.patch(server, 'SPELLS', watch.Spells(mono=lambda: self.clock.mono, wall=lambda: self.clock.wall))
        self.patch(server.engine, 'auto_settings', lambda: dict(ON))
        self.patch(server.api, 'auto_status', lambda: dict(BEATING))
        self.patch(server, 'load', lambda: page(None))

    def test_one_note_from_the_record_after_five_minutes(self):
        self.patch(server, 'lost_signin', lambda now=None: (lost(since=self.clock.wall), None))
        sent = []
        for _ in range(8):
            sent += server.watch_tick(now=self.clock.wall)
            self.clock.tick(60)
        self.assertEqual([n.key for n in sent], ['signin-lost'])

    def test_a_record_that_cannot_be_read_costs_the_note_never_the_tick(self):
        def broken(now=None):
            raise OSError('made up')
        self.patch(server, 'lost_signin', broken)
        self.assertEqual(server.watch_tick(now=self.clock.wall), [])


if __name__ == '__main__':
    unittest.main()
