"""Sign in to all: one press signs in again to every account that needs it, one after another.

- which accounts it takes: ended (or refused) first, then the soonest to end; every one ending within the week or more
  than a day before the latest-ending account's date; one in step with the newest sign-in, one whose date is not known,
  and one Switchboard does not keep are left alone;
- one at a time, in that order, each one the row's own Sign in (POST /api/connect, its browser profile): the next one
  starts only once the one before is kept;
- one that fails, is cancelled, comes back as another account or is not approved in time is skipped, and the closing
  line names it by its whole email;
- Stop ends it, cancels the one waiting, and leaves what was kept as kept;
- a second press while it runs starts nothing more;
- a restart of the page's job mid-queue leaves no half state: a sign-in the browser approved is kept at the next start,
  the rest is not started, and the queue is gone;
- the Mac sleeping while a sign-in waits: after a short sleep it carries on with that same sign-in; after one longer
  than a sign-in waits, that one is skipped cleanly and the next starts, never two at once;
- every tab sees the same queue in /api/state.

Sign-ins start the real way (server -> connect.start) with a stand-in for Claude Code's own sign-in command, a fake
engine and a fake browser (test_signin_pressed_twice's sandbox).
"""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import connect, core, server, signins
import test_signin_pressed_twice as twice

D = 86400
A, B, C, E = 'ana@work.example', 'bo@home.example', 'cy@lab.example', 'ed@shop.example'


class Pick(unittest.TestCase):
    def acc(self, email, at, slot, need=False, **more):
        return dict({'email': email, 'slot': slot, 'signin_ends_at': at, 'needs_login': need, 'connected': True}, **more)

    def test_ended_first_then_the_soonest_and_the_ones_out_of_step(self):
        now = 1.8e9
        accounts = [self.acc('late@x.example', now + 29 * D, 1),
                    self.acc('in-step@x.example', now + 28.5 * D, 2),        # within a day of the latest: alone
                    self.acc('behind@x.example', now + 20 * D, 3),           # more than a day before it: taken
                    self.acc('soon@x.example', now + 2 * D, 4),
                    self.acc('week@x.example', now + 6 * D, 5),
                    self.acc('ended@x.example', now - D, 6, need=True),
                    self.acc('refused@x.example', now + 25 * D, 7, need=True),   # refused before its date
                    self.acc('unknown@x.example', None, 8),                   # no date known: alone
                    self.acc('not-kept@x.example', now + D, 9, unmanaged=True),
                    self.acc('gone@x.example', now + D, 10, connected=False)]
        self.assertEqual(signins.pick(accounts, now), ['ended@x.example', 'refused@x.example', 'soon@x.example',
                                                       'week@x.example', 'behind@x.example'])

    def test_after_one_sitting_nothing_is_taken(self):
        now = 1.8e9
        accounts = [self.acc(e, now + 29 * D + i * 60, i + 1) for i, e in enumerate((A, B, C))]
        self.assertEqual(signins.pick(accounts, now), [])

    def test_the_note_counts_from_the_same_list_what_has_ended_and_what_ends_this_week(self):
        # One source for the note above the accounts, the button beside it, the menu bar and the palette: pick's own
        # list (every account a press opens, in its order), and of those, the ones whose sign-in has ended (a sign-in
        # needed, its date passed) and the ones that end within the week. One out of step is opened but not counted
        # (it does not end this week); one refused before its date needs a sign-in as one that ended does (pick's own
        # rule), so it counts as ended: the button is there for it, the account in use's too.
        now = 1.8e9
        accounts = [self.acc('late@x.example', now + 29 * D, 1),
                    self.acc('behind@x.example', now + 20 * D, 3),
                    self.acc('soon@x.example', now + 2 * D, 4),
                    self.acc('week@x.example', now + 6.5 * D, 5),
                    self.acc('ended@x.example', now - D, 6, need=True),
                    self.acc('refused@x.example', now + 25 * D, 7, need=True)]
        v = signins.view(accounts, now)
        self.assertEqual(v['due'], ['ended@x.example', 'refused@x.example', 'soon@x.example', 'week@x.example',
                                    'behind@x.example'])
        self.assertEqual((v['ended'], v['week']), (['ended@x.example', 'refused@x.example'],
                                                   ['soon@x.example', 'week@x.example']))
        self.assertEqual(v['due'], signins.pick(accounts, now))
        only_out_of_step = [self.acc('late@x.example', now + 29 * D, 1), self.acc('behind@x.example', now + 20 * D, 3)]
        v = signins.view(only_out_of_step, now)
        self.assertEqual((v['due'], v['ended'], v['week']), (['behind@x.example'], [], []))   # no note to show

    def test_never_an_assumed_length_of_a_sign_in(self):
        now = 1.8e9
        # the same spread of dates, much shorter or longer than any sign-in: the same choice
        for span in (2 * D, 90 * D):
            accounts = [self.acc(A, now + span, 1), self.acc(B, now + span - 1.5 * D, 2)]
            self.assertEqual(signins.pick(accounts, now), [B] if span - 1.5 * D > 7 * D else [B, A])


class Queue(twice.SignIns):
    def setUp(self):
        super().setUp()
        self.patch(signins, 'LOOK_EVERY', 0.01)
        self.patch(signins, 'RESULT_GRACE', 0.3)
        self.patch(connect, 'AGAIN_AFTER', 0)
        signins.reset()
        self.addCleanup(self.end_queue)
        now = time.time()
        st = core.blank_state()
        for i, (email, at, need, profile) in enumerate(((A, now + 6 * D, False, 'P1'), (B, now - D, True, 'P2'),
                                                        (C, now + 2 * D, False, None), (E, now + 29 * D, False, None))):
            core.ensure_account(st, email).update(connected=True, slot=i + 1, needs_login=need, signin_ends_at=at,
                                                  profile=profile)
        core.save(st)
        self.st = core.load()

    def end_queue(self):
        signins.stop()
        for x in self.made:
            if x.rc is None:
                x.fail()
        for _ in range(500):
            connect.finish(self.st)
            if not [t for t in threading.enumerate() if t.name == 'sign-in-to-all']:
                break
            time.sleep(0.01)
        signins.reset()

    def until(self, cond, what):
        for _ in range(1500):
            if cond():
                return
            time.sleep(0.01)
        self.fail('waited for: %s' % what)

    def started(self, n):
        self.until(lambda: len(self.made) >= n, '%d sign-ins started' % n)
        time.sleep(0.05)

    def tick(self):
        """The page's loop: what has ended goes to the engine."""
        connect.finish(self.st)

    def view(self):
        return server.signins_view(core.load(), time.time())

    def ended(self):
        self.until(lambda: not signins.running(), 'the queue to end')
        return signins.RUN

    def email_of(self, login):
        return login.args[login.args.index('--email') + 1]

    def test_one_at_a_time_in_order_each_the_rows_own_sign_in(self):
        got = server.signins_act('start')
        self.assertEqual(got['signins']['emails'], [B, C, A])                # ended, then the soonest; E is in step
        self.started(1)
        self.assertEqual([self.email_of(x) for x in self.made], [B])
        self.assertEqual(self.opened, [('P2', twice.LINK % 1)])              # in its own browser profile
        time.sleep(0.2)
        self.assertEqual(len(self.made), 1)                                  # never two at once
        v = self.view()
        self.assertEqual((v['state'], v['current'], v['done'], v['of']), ('running', B, 0, 3))
        self.made[0].approve(B)
        self.tick()                                                          # kept: the next one starts
        self.started(2)
        self.assertEqual(self.email_of(self.made[1]), C)
        self.assertEqual(self.view()['done'], 1)
        self.made[1].approve(C)
        self.tick()
        self.started(3)
        self.assertEqual(self.email_of(self.made[2]), A)
        self.assertEqual(self.opened[2], ('P1', twice.LINK % 3))
        self.made[2].approve(A)
        self.tick()
        q = self.ended()
        self.assertEqual(q.kept, [B, C, A])
        self.assertEqual(q.line, 'Signed in again to all 3 accounts.')
        v = self.view()
        self.assertEqual((v['state'], v['line']), ('ended', 'Signed in again to all 3 accounts.'))
        self.assertIn('Signed in again to all 3 accounts.', [e['text'] for e in core.load()['events']])
        self.assertEqual(len([e for e in core.load()['events'] if e['text'].startswith('Opened the sign-in for')]), 3)

    def test_one_that_fails_is_skipped_and_named_and_the_queue_goes_on(self):
        server.signins_act('start')
        self.started(1)
        self.made[0].fail()
        self.tick()
        self.started(2)
        self.made[1].approve(C)
        self.tick()
        self.started(3)
        self.made[2].approve('someone@else.example')                         # the browser was signed in as another
        self.tick()
        q = self.ended()
        self.assertEqual(q.kept, [C])
        self.assertEqual(q.line, 'Signed in again to 1 of 3. Not signed in: %s (the sign-in did not finish); %s (your '
                                 'browser signed in as someone@else.example).' % (B, A))
        self.assertEqual(self.view()['skipped'], [{'email': B, 'why': 'the sign-in did not finish'},
                                                  {'email': A, 'why': 'your browser signed in as someone@else.example'}])

    def test_one_cancelled_on_its_row_or_not_approved_in_time_is_skipped(self):
        self.patch(connect, 'TIMES_OUT', 0.3)
        server.signins_act('start')
        self.started(1)
        server.act('/api/cancel', {'key': B})                                # its own Cancel
        self.started(2)
        time.sleep(0.4)
        self.tick()                                                          # 15 minutes (here 0.3 s) with no yes
        self.started(3)
        self.made[2].approve(A)
        self.tick()
        q = self.ended()
        self.assertEqual(q.skipped, [(B, 'stopped'), (C, 'it was not approved in time')])
        self.assertEqual(q.kept, [A])

    def test_stop_ends_it_cancels_the_one_waiting_and_keeps_what_was_kept(self):
        server.signins_act('start')
        self.started(1)
        self.made[0].approve(B)
        self.tick()
        self.started(2)
        got = server.signins_act('stop')
        q = self.ended()
        self.assertEqual(q.kept, [B])
        self.assertIn(self.made[1].pid, self.killed)                         # the one waiting is cancelled
        self.assertEqual(len(self.made), 2)                                  # A never started
        self.assertEqual(q.line, 'Stopped. Signed in again to 1 of 3.')
        self.assertIn(got['signins']['state'], ('running', 'ended'))

    def test_stop_a_moment_after_the_browser_approved_it_still_keeps_that_one(self):
        server.signins_act('start')
        self.started(1)
        self.made[0].approve(B)                                              # approved, not yet kept
        server.signins_act('stop')
        time.sleep(0.1)
        self.tick()
        q = self.ended()
        self.assertEqual(q.kept, [B])
        self.assertEqual(len(self.made), 1)

    def test_a_second_press_while_it_runs_starts_nothing_more(self):
        server.signins_act('start')
        self.started(1)
        again = server.signins_act('start')
        self.assertEqual((again['signins']['state'], again['signins']['current']), ('running', B))
        time.sleep(0.2)
        self.assertEqual(len(self.made), 1)
        self.assertEqual(len([t for t in threading.enumerate() if t.name == 'sign-in-to-all']), 1)

    def test_with_nothing_to_do_it_says_so(self):
        now = time.time()
        with core.locked() as st:
            for i, e in enumerate((A, B, C, E)):
                st['accounts'][e].update(needs_login=False, signin_ends_at=now + 29 * D + i)
        got = server.signins_act('start')
        self.assertEqual(got['line'], 'No account needs a sign-in now.')
        self.assertEqual(got['signins']['state'], 'idle')
        self.assertEqual(self.made, [])

    def test_a_restart_mid_queue_leaves_no_half_state(self):
        server.signins_act('start')
        self.started(1)
        self.made[0].approve(B)
        self.tick()
        self.started(2)
        self.made[1].approve(C)                                              # approved; the page's job ends now
        signins.RUN.stopped = True                                           # the process ends, its thread with it
        connect.RUNNING[C]['over'] = True
        self.until(lambda: not signins.running(), 'the old queue to end')
        signins.reset()                                                      # a new process has no queue
        connect.RUNNING.clear()
        self.patch(connect, 'run', lambda args, **kw: type('Ran', (), {'stdout': ''})())     # no sign-in still going
        connect.sweep()                                                      # its next start
        self.tick()
        self.assertIn(('add_login', self.made[1].dir), self.fake.calls)      # what the browser approved is kept
        self.assertEqual(len(self.made), 2)                                  # A is simply not started
        v = self.view()
        self.assertEqual(v['state'], 'idle')
        self.assertIn(A, v['due'])                                           # its note comes back by itself

    def asleep(self, key, seconds):
        """The Mac slept `seconds` while this sign-in waited: the wall clock moved past its start and its last press."""
        c = connect.RUNNING[key]
        c['started'] -= seconds
        c['pressed'] = c.get('pressed', c['started']) - seconds

    def test_after_a_short_sleep_it_carries_on_with_the_same_sign_in(self):
        server.signins_act('start')
        self.started(1)
        self.asleep(B, 120)
        self.tick()
        time.sleep(0.2)
        self.assertEqual(len(self.made), 1)                                  # the same one, still waiting
        self.assertEqual(self.view()['current'], B)
        self.made[0].approve(B)
        self.tick()
        self.started(2)
        self.assertEqual([self.email_of(x) for x in self.made], [B, C])

    def test_after_a_long_sleep_the_one_waiting_is_skipped_cleanly_and_never_two_start(self):
        server.signins_act('start')
        self.started(1)
        self.asleep(B, connect.TIMES_OUT + 60)
        self.tick()                                                          # the first look after waking
        self.started(2)
        time.sleep(0.2)
        self.assertEqual([self.email_of(x) for x in self.made], [B, C])     # one at a time, never B twice
        self.assertIn(self.made[0].pid, self.killed)                         # the old listener is stopped
        self.assertEqual(signins.RUN.skipped, [(B, 'it was not approved in time')])
        self.assertEqual(self.view()['current'], C)

    def test_every_tab_sees_the_same_queue(self):
        server.signins_act('start')
        self.started(1)
        one, two = self.view(), self.view()
        self.assertEqual(one, two)
        self.assertEqual(one['current'], B)
        self.made[0].approve(B)
        self.tick()
        self.started(2)
        self.assertEqual(self.view()['current'], C)
        self.assertEqual(self.view(), self.view())

    def test_hide_takes_the_closing_line_away(self):
        server.signins_act('start')
        self.started(1)
        server.signins_act('stop')
        self.ended()
        self.assertEqual(self.view()['state'], 'ended')
        server.signins_act('hide')
        self.assertEqual(self.view()['state'], 'idle')


class TheHardRuleIsSaidWhereTheQueueLives(unittest.TestCase):
    def test_the_module_says_it_never_approves_for_the_person(self):
        doc = ' '.join(signins.__doc__.split())
        self.assertIn("every sign-in completes on Anthropic's own page, by the person's own click", doc)
        self.assertIn('never presses, fills or scripts anything on that page', doc)
        self.assertIn('never keeps a password or a browser cookie', doc)


if __name__ == '__main__':
    unittest.main()
