import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# A sign-in that was kept shows on its row within a pass.
#
# Run /login in Claude Code as an account Switchboard already holds, one whose row says "Sign in again". The engine
# keeps the new login within seconds. The page read its rows just before that, so the row went on saying "Sign in
# again", with the old end date, until the next regular reading: a minute later, or four on a busy Mac. The login can
# be kept by the pass that follows the rows in the same tick, or by the loop that reads the accounts, which is another
# process; either way accounts.json changes, and that is what the page's loop now looks at on every pass.
#
# It must never make the page read more often than it always did for nothing: a file that is written again and again
# with nothing new for a row in it (a login the Keychain will not give up is noted at every pass) wakes it once, and
# then only at the regular gap.
#
# Made-up accounts only (*.example). The engine, the automatic-switching host and notifications are stand-ins; every
# folder is a throwaway one; nothing here reaches the network.
import json
import os
import sys
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
sys.path.insert(0, ROOT)

from switchboard import core, engine, keep, server
from switchboard.accounts import api
from fakes_accounts_api import FakeAccounts
from test_keep import A, B, ORG, Sandbox as KeepSandbox

D = 86400.0
EARLY = ('stamp', 'said', 'same', 'woke')       # what server.ROWS holds for the loop's early reading


class RowsAfterAKeep(KeepSandbox):
    def setUp(self):
        KeepSandbox.setUp(self)
        core.set_setting('auto_set_by_you', True)       # auto-switch going on by itself wakes the loop too: not here
        self.fake.hold(A)
        self.old_end, self.new_end = time.time() - 5 * D, time.time() + 29 * D
        self.b = FakeAccounts.hold(self.fake, B, ORG[B], status='needs_login', refresh_expires_at=self.old_end)
        self.signed_in(B)                               # /login as B in Claude Code
        self.store(0)
        self.ticks = 0
        self.forget()
        self.addCleanup(self.forget)
        real = server.tick

        def tick():
            self.ticks += 1
            return real()
        for target, name, value in ((server, 'tick', tick), (server, 'MAC_TICK', 0.01), (server, 'TICK', 3600),
                                    (server, 'mac_tick', lambda now=None: None)):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)

    def forget(self):
        for k in EARLY:
            server.ROWS.pop(k, None)

    def store(self, n):
        """accounts.json as the engine writes it: a new file put in the old one's place."""
        path = core.data_path('accounts.json')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + '.tmp', 'w') as f:
            json.dump({'schema': 1, 'write': n, 'pad': 'x' * n}, f)
        os.replace(path + '.tmp', path)

    def keep_the_login(self):
        """The engine keeps B's new sign-in: the account is fine again, with the new sign-in's end date."""
        self.fake.by_id[self.b].update(status='ok', refresh_expires_at=self.new_end)
        self.store(1)

    def row(self):
        return core.load()['accounts'][B]

    def passes(self, n, each, gap=240.0):
        """Run n passes of the loop on a clock of our own: 20 seconds go by between two passes, a regular reading is
        due every `gap` seconds, and `each(k)` runs at the end of pass k (1, 2, ...), where the other process would
        have written."""
        clock, k = [0.0], [0]

        def mac_tick(now=None):                         # the last step of a pass
            k[0] += 1
            each(k[0])

        def wait_for(default, now=None):                # between two passes
            clock[0] += 20.0
            return 0.001
        with mock.patch.object(server, 'mac_tick', mac_tick), mock.patch.object(keep, 'wait_for', wait_for), \
                mock.patch.object(server, 'monotonic', lambda: clock[0]), \
                mock.patch.object(server, 'ease_tick', lambda load=None: gap):
            server.loop(ticks=n)

    # ---------- the sign-in shows ----------

    def test_the_row_says_sign_in_again_before_the_login_is_kept(self):
        server.loop(ticks=1)
        self.assertIs(self.row()['needs_login'], True)
        self.assertEqual(self.row()['signin_ends_at'], self.old_end)
        self.assertEqual(self.ticks, 1)

    def test_kept_by_the_other_process_between_two_passes_it_shows_on_the_next_pass(self):
        passes = []

        def mac_tick(now=None):                         # the last step of a pass
            passes.append(1)
            if len(passes) == 2:
                self.keep_the_login()                   # the loop that reads the accounts kept it (its own process)
        with mock.patch.object(server, 'mac_tick', mac_tick):
            server.loop(ticks=3)
        self.assertEqual(self.ticks, 2)                 # the first pass, and the pass after the change: not a TICK later
        self.assertIs(self.row()['needs_login'], False)
        self.assertEqual(self.row()['signin_ends_at'], self.new_end)

    def test_kept_by_the_pass_that_follows_the_rows_the_next_pass_does_not_wait(self):
        waits = []
        real_pass = api.keep_pass

        def keep_pass():
            if not waits and self.fake.by_id[self.b]['status'] != 'ok':
                self.keep_the_login()                   # the engine's own pass of login keeping, after the rows
            return real_pass()

        def wait_for(default, now=None):                # the end of a pass: is the loop already woken?
            waits.append(server.WAKE.is_set())
            return 0.01
        with mock.patch.object(api, 'keep_pass', keep_pass), mock.patch.object(keep, 'wait_for', wait_for):
            server.loop(ticks=2)
        self.assertEqual(waits, [True])                 # no 20 seconds between the keep and the reading that shows it
        self.assertEqual(self.ticks, 2)
        self.assertIs(self.row()['needs_login'], False)

    def test_kept_while_the_rows_were_being_read_it_still_shows(self):
        real, did = engine.listing, []

        def listing():
            got = real()                                # the rows as they were ...
            if not did:
                did.append(1)
                self.keep_the_login()                   # ... and the login is kept before this tick is over
            return got
        with mock.patch.object(engine, 'listing', listing):
            server.loop(ticks=2)
        self.assertEqual(self.ticks, 2)                 # the file is looked at as it was BEFORE the rows were asked for
        self.assertIs(self.row()['needs_login'], False)

    def test_the_first_account_ever_kept_wakes_it(self):
        os.remove(core.data_path('accounts.json'))
        passes = []

        def mac_tick(now=None):
            passes.append(1)
            if len(passes) == 1:
                self.keep_the_login()                   # accounts.json is there for the first time
        with mock.patch.object(server, 'mac_tick', mac_tick):
            server.loop(ticks=3)
        self.assertEqual(self.ticks, 2)

    def test_a_loop_that_starts_is_held_back_by_nothing_an_earlier_one_left(self):
        server.ROWS.update(stamp=('an', 'old', 'one'), said=['old'], same=True, woke=server.monotonic())
        passes = []

        def mac_tick(now=None):
            passes.append(1)
            if len(passes) == 2:
                self.keep_the_login()
        with mock.patch.object(server, 'mac_tick', mac_tick):
            server.loop(ticks=3)
        self.assertEqual(self.ticks, 2)
        self.assertIs(self.row()['needs_login'], False)

    # ---------- and it never reads more often for nothing ----------

    def test_nothing_changed_so_the_accounts_are_read_at_the_regular_gap_only(self):
        server.loop(ticks=6)
        self.assertEqual(self.ticks, 1)

    def test_a_file_that_is_not_there_wakes_nothing(self):
        os.remove(core.data_path('accounts.json'))
        server.loop(ticks=4)
        self.assertEqual(self.ticks, 1)

    def test_a_file_written_at_every_pass_with_nothing_new_in_it_wakes_it_once(self):
        self.passes(13, lambda k: self.store(k + 1))    # 260 seconds: the regular reading (240) is not due again yet
        self.assertEqual(self.ticks, 2)                 # the first reading, and one for the first change
        self.forget()
        self.ticks = 0
        self.passes(14, lambda k: self.store(k + 1))    # ... and with the 14th pass it is due: read as it always was
        self.assertEqual(self.ticks, 3)

    def test_while_each_reading_says_something_new_it_is_woken_once_in_a_quarter_of_the_gap(self):
        def each(k):                                    # an account changes at every pass
            self.fake.by_id[self.b]['alias'] = 'name-%d' % k
            self.store(k + 1)
        self.passes(13, each)
        self.assertEqual(self.ticks, 5)                 # at 0, 20, 80, 140 and 200 seconds: 60 apart, not 20

    def test_a_real_change_after_a_reading_that_said_nothing_new_shows_by_the_regular_gap(self):
        def each(k):
            if k == 1:
                self.store(7)                           # written, with nothing new in it: woken once, for nothing
            if k == 2:
                self.keep_the_login()                   # 20 seconds later the sign-in is kept
        self.passes(13, each)
        self.assertEqual(self.ticks, 2)
        self.assertIs(self.row()['needs_login'], True)  # held for now, as before this look existed ...
        self.forget()
        self.ticks = 0
        self.fake.by_id[self.b].update(status='needs_login', refresh_expires_at=self.old_end)
        self.passes(14, each)
        self.assertEqual(self.ticks, 3)                 # ... and never lost: there by the regular gap
        self.assertIs(self.row()['needs_login'], False)

    def test_an_engine_that_does_not_answer_is_asked_again_at_the_regular_gap_only(self):
        def each(k):
            self.store(k + 1)                           # the file goes on changing
            self.fake.fail['accounts'] = core.SwitchError('The accounts could not be read.')
        self.passes(13, each)
        self.assertEqual(self.ticks, 2)                 # the first reading; one for the change, which failed; no more

    def test_the_look_failing_never_stops_the_loop(self):
        said, calls = [], []
        real = server.rows_stamp

        def rows_stamp():
            calls.append(1)
            if len(calls) > 1:
                raise RuntimeError('made up')
            return real()
        with mock.patch.object(server, 'rows_stamp', rows_stamp), mock.patch.object(server, 'log', said.append):
            server.loop(ticks=3)
        self.assertGreaterEqual(self.ticks, 1)
        self.assertTrue([s for s in said if s.startswith('looking at the accounts file failed')], said)

    # ---------- the two gaps, one call at a time ----------

    def test_after_a_quarter_of_the_gap_a_change_wakes_it_again(self):
        server.loop(ticks=1)                            # the tick read the accounts, and they said something new
        self.store(2)
        self.assertTrue(server.wake_if_behind(400.0, now=1000.0))       # changed since the tick read them
        self.assertTrue(server.WAKE.is_set())
        self.assertFalse(server.wake_if_behind(400.0, now=1050.0))      # still changed, and inside a quarter of 400 s
        self.assertTrue(server.wake_if_behind(400.0, now=1100.0))       # the quarter is over: once more
        self.assertFalse(server.wake_if_behind(400.0, now=1150.0))
        server.tick()                                   # read again: the rows are as new as the file,
        self.assertFalse(server.wake_if_behind(400.0, now=2000.0))      # and they said nothing new
        self.store(3)
        self.assertFalse(server.wake_if_behind(400.0, now=1300.0))      # so now the whole gap counts: 200 s is inside
        self.assertTrue(server.wake_if_behind(400.0, now=1500.0))

    def test_a_calmer_mac_shortens_the_wait_at_once(self):
        server.loop(ticks=1)
        self.store(2)
        self.assertTrue(server.wake_if_behind(240.0, now=1000.0))
        self.assertFalse(server.wake_if_behind(240.0, now=1030.0))      # a quarter of four minutes is not over
        self.assertTrue(server.wake_if_behind(60.0, now=1030.0))        # a quarter of one minute is

    def test_a_wake_dated_after_now_never_holds_a_change_back(self):
        server.loop(ticks=1)
        self.store(2)
        self.assertTrue(server.wake_if_behind(400.0, now=1000.0))
        self.assertTrue(server.wake_if_behind(400.0, now=10.0))         # "woken 990 s from now" is not a wait

    def test_what_the_rows_say_leaves_out_a_readings_numbers(self):
        one = engine._row(id='a', number=1, email=A, status='needs_login', refresh_expires_at=5.0, usage={'pct': 1},
                          usage_at=10.0, checked_at=10.0, source='endpoint')
        later = dict(one, usage={'pct': 9}, usage_at=70.0, checked_at=70.0, frozen=True, usage_error='x',
                     models_seen=['m'], source='statusline')
        self.assertEqual(server.rows_said([one]), server.rows_said([later]))
        for change in ({'status': 'ok'}, {'refresh_expires_at': 6.0}, {'active': True}, {'alias': 'w'}, {'number': 2},
                       {'unmanaged': True}, {'emptied': True}, {'plan': 'team'}):
            self.assertNotEqual(server.rows_said([one]), server.rows_said([dict(one, **change)]), change)
        self.assertNotEqual(server.rows_said([one]), server.rows_said([one, one]))


if __name__ == '__main__':
    unittest.main()
