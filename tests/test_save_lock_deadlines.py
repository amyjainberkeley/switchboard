import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Claude Code's own tries for its save lock end after about 5 s. So while Switchboard holds that lock, every Keychain
# call gets at most keychain.UNDER_LOCK_S, and all of them together at most keychain.UNDER_LOCK_TOTAL_S; a call past
# its deadline has its helper ended (never left to land after the lock is let go), and a write ended that way is read
# back before anything else. A call cut that way makes nothing rest (the limit is the lock's own, not a sign the
# Keychain is unwell), "ended" is said only of a helper that has exited, and a write that never started says so
# (Done.untouched), so a switch leaves no record behind for a write that is not coming. Calls made outside the lock
# keep their old deadlines (and a helper past one is still left
# to finish). The real Keychain wrapper over the stand-in `security` (FakeSecurity, which sleeps when told), real
# clocks (these tests take seconds by design), made-up *.example accounts.
import os
import threading
import time
import unittest
from contextlib import contextmanager
from unittest import mock

from fakes_autoswitch import FakeClock
from fakes_accounts import Rig
from switchboard.accounts import ccfiles, context, facts, keychain, live, putback, switcher
from switchboard.accounts.api import ABSENT, PRESENT, UNREADABLE, AccountsError

OLD = '{"claudeAiOauth":{"accessToken":"sk-ant-oat01-old","refreshToken":"sk-ant-ort01-old"}}'
NEW = '{"claudeAiOauth":{"accessToken":"sk-ant-oat01-new","refreshToken":"sk-ant-ort01-new"}}'
OTHER = '{"claudeAiOauth":{"accessToken":"sk-ant-oat01-other","refreshToken":"sk-ant-ort01-other"}}'


def gone(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


class Base(unittest.TestCase):
    def setUp(self):
        self.rig = Rig(real_keychain=True)
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        putback.reset()
        self.sec, self.kc = self.rig.sec, self.rig.kc
        self.svc, self.acct = self.rig.service, self.rig.account
        self.lock_path = facts.storage_lock(self.rig.env, self.rig.home)
        self.cred = facts.credentials_file(self.rig.env, self.rig.home)

    def tearDown(self):
        self.use.__exit__(None, None, None)
        putback.reset()
        self.rig.close()

    def hold(self, **kw):
        return ccfiles.storage_lock(self.rig.env, self.rig.home, **kw)

    def assert_helper_gone(self, verb):
        calls = [c for c in self.sec.calls(verb) if c['service'] == self.svc]
        for c in calls:                                     # a helper that had started is not running any more
            self.assertTrue(gone(c['pid']), 'the %s helper %s still runs' % (verb, c['pid']))

    def wait_until(self, t):
        while time.monotonic() < t:
            time.sleep(min(0.2, max(0.0, t - time.monotonic())))


class UnderTheLock(Base):
    def test_a_read_of_6_s_under_the_lock_is_cut_at_5_s_its_helper_ended_and_the_lock_let_go(self):
        self.sec.put(self.svc, self.acct, OLD)
        self.sec.rule(verb='find', service=self.svc, delay=6.0, times=1)
        t0 = time.monotonic()
        with self.hold():
            r = self.kc.read(self.svc, self.acct, fresh=True, interactive=True)
            took = time.monotonic() - t0
        self.assertEqual((r.kind, r.why), (UNREADABLE, 'timeout'))
        self.assertGreaterEqual(took, keychain.UNDER_LOCK_S - 0.3)
        self.assertLess(took, 6.0)                          # not the stand-in's 6 s
        self.assertFalse(os.path.exists(self.lock_path))
        self.assert_helper_gone('find')
        self.assertTrue(any('was ended' in x for x in self.rig.logs), self.rig.logs)
        self.assertEqual(self.kc.state()['needs_approval_or_slow'], [])     # nothing left waiting for an answer

    def test_a_write_that_would_land_at_7_s_is_cut_at_5_s_and_never_lands_later(self):
        self.sec.put(self.svc, self.acct, OLD)
        self.sec.rule(verb='add', service=self.svc, delay=7.0, times=1)
        t0 = time.monotonic()
        with self.hold():
            done = self.kc.write(self.svc, self.acct, NEW, interactive=True)
            took = time.monotonic() - t0
        self.assertEqual((done.ok, done.why, done.ended), (False, 'timeout', True))
        self.assertLess(took, 6.5)
        self.assert_helper_gone('add')
        self.wait_until(t0 + 8.5)                           # past the moment it would have landed
        self.assertEqual(self.sec.get(self.svc, self.acct), OLD)
        self.assertEqual(self.sec.landed(self.svc), [])     # the stand-in records every landing: none
        self.assertEqual(self.kc.state()['needs_approval_or_slow'], [])

    def test_a_write_past_its_deadline_while_a_keychain_dialog_shows_is_left_and_never_ended(self):
        # ending a helper that draws a dialog tears the dialog down, so under the lock too it is left to finish; the
        # lock is let go all the same, and nothing asks for that item again until the helper has answered
        self.sec.put(self.svc, self.acct, OLD)
        self.sec.rule(verb='add', service=self.svc, delay=7.0, times=1)
        self.kc._dialog = lambda: True                      # macOS's SecurityAgent is running
        t0 = time.monotonic()
        with self.hold():
            done = self.kc.write(self.svc, self.acct, NEW, interactive=True)
            took = time.monotonic() - t0
        self.assertEqual((done.ok, done.why, done.ended), (False, 'dialog', False))
        self.assertLess(took, 6.5)
        self.assertFalse(os.path.exists(self.lock_path))
        helper = [c for c in self.sec.calls('add') if c['service'] == self.svc][0]['pid']
        self.assertFalse(gone(helper), 'the helper was ended while a dialog was showing')
        self.assertFalse(any('was ended' in x for x in self.rig.logs), self.rig.logs)
        self.assertEqual(self.kc.state()['waiting_on_dialog'], [self.svc])
        with self.hold():
            again = self.kc.write(self.svc, self.acct, OTHER, interactive=True)
        self.assertEqual((again.ok, again.why), (False, 'busy'))   # no second helper while the first is out
        self.assertEqual(len([c for c in self.sec.calls('add') if c['service'] == self.svc]), 1)
        self.wait_until(t0 + 8.5)                           # the dialog answered: the helper finishes by itself
        self.assertEqual(self.sec.get(self.svc, self.acct), NEW)
        self.assertTrue(gone(helper))
        self.assertEqual(self.kc.state()['needs_approval_or_slow'], [])

    def test_a_read_past_its_deadline_while_a_dialog_shows_is_left_too(self):
        self.sec.put(self.svc, self.acct, OLD)
        self.sec.rule(verb='find', service=self.svc, delay=7.0, times=1)
        self.kc._dialog = lambda: True
        with self.hold():
            r = self.kc.read(self.svc, self.acct, fresh=True, interactive=True)
        self.assertEqual((r.kind, r.why), (UNREADABLE, 'dialog'))
        helper = [c for c in self.sec.calls('find') if c['service'] == self.svc][0]['pid']
        self.assertFalse(gone(helper), 'the helper was ended while a dialog was showing')
        self.assertFalse(any('was ended' in x for x in self.rig.logs), self.rig.logs)

    def test_when_it_cannot_be_told_whether_a_dialog_shows_the_helper_is_ended(self):
        self.sec.put(self.svc, self.acct, OLD)
        self.sec.rule(verb='add', service=self.svc, delay=7.0, times=1)
        self.kc._dialog = lambda: None                      # the process list could not be had
        t0 = time.monotonic()
        with self.hold():
            done = self.kc.write(self.svc, self.acct, NEW, interactive=True)
        self.assertEqual((done.ok, done.why, done.ended), (False, 'timeout', True))
        self.assert_helper_gone('add')
        self.wait_until(t0 + 8.5)
        self.assertEqual(self.sec.get(self.svc, self.acct), OLD)

    def test_a_read_cut_under_the_lock_makes_nothing_rest(self):
        # the 5 s are the lock's own limit, not a sign the Keychain is unwell: the next read that is not a person's own
        # (every look of the watch and the loop starts with one) is asked of the Keychain, not refused for 30 s
        self.sec.put(self.svc, self.acct, OLD)
        self.sec.rule(verb='find', service=self.svc, delay=6.0, times=1)
        with self.hold():
            r = self.kc.read(self.svc, self.acct, fresh=True, interactive=True)
        self.assertEqual((r.kind, r.why), (UNREADABLE, 'timeout'))
        state = self.kc.state()
        self.assertEqual((state['resting'], state['resting_all']), ([], None))
        self.assertEqual(self.kc.counts['timeout'], 1)      # counted all the same
        started = len(self.sec.calls('find'))
        again = self.kc.read(self.svc, self.acct, fresh=True)       # not interactive
        self.assertEqual((again.kind, again.value), (PRESENT, OLD))
        self.assertEqual(len(self.sec.calls('find')), started + 1)

    def test_a_write_cut_under_the_lock_makes_nothing_rest_either(self):
        self.sec.put(self.svc, self.acct, OLD)
        self.sec.rule(verb='add', service=self.svc, delay=7.0, times=1)
        with self.hold():
            done = self.kc.write(self.svc, self.acct, NEW, interactive=True)
        self.assertEqual((done.ok, done.why, done.ended), (False, 'timeout', True))
        state = self.kc.state()
        self.assertEqual((state['resting'], state['resting_all']), ([], None))
        again = self.kc.read(self.svc, self.acct, fresh=True)       # not interactive
        self.assertEqual((again.kind, again.value), (PRESENT, OLD))

    def test_a_call_past_its_deadline_outside_the_lock_still_makes_the_keychain_rest(self):
        kc = keychain.Keychain(tool=self.sec.tool, read_deadline=1.0, write_deadline=1.0, log=self.rig.logs.append)
        self.sec.put(self.svc, self.acct, OLD)
        self.sec.rule(verb='find', service=self.svc, delay=3.0, times=1)
        r = kc.read(self.svc, self.acct, fresh=True, interactive=True)
        self.assertEqual((r.kind, r.why), (UNREADABLE, 'timeout'))
        self.assertEqual(kc.state()['resting_all'], 'timeout')
        deadline = time.monotonic() + 60
        while kc.state()['needs_approval_or_slow'] and time.monotonic() < deadline:
            time.sleep(0.2)

    def test_a_helper_still_there_after_it_was_ended_is_not_called_ended(self):
        # "ended" promises that the write can no longer land. A helper the system has not let go of after SIGKILL is
        # left like any late one: the answer is a plain 'timeout', and the item stays busy until the helper is gone
        self.sec.put(self.svc, self.acct, OLD)
        self.sec.rule(verb='add', service=self.svc, delay=7.0, times=1)
        with mock.patch.object(keychain, 'end_helper', lambda proc, grace=keychain.END_GRACE_S: False):
            with self.hold():
                done = self.kc.write(self.svc, self.acct, NEW, interactive=True)
        self.assertEqual((done.ok, done.why, done.ended, done.untouched), (False, 'timeout', False, False))
        self.assertFalse(os.path.exists(self.lock_path))
        self.assertEqual(self.kc.state()['needs_approval_or_slow'], [self.svc])
        again = self.kc.write(self.svc, self.acct, OTHER, interactive=True)
        self.assertEqual((again.ok, again.why, again.untouched), (False, 'busy', False))    # a write is still out
        deadline = time.monotonic() + 60
        while self.kc.state()['needs_approval_or_slow'] and time.monotonic() < deadline:
            time.sleep(0.2)
        self.assertEqual(self.sec.get(self.svc, self.acct), NEW)    # this stand-in was never signalled: it landed

    def test_a_write_whose_helper_a_signal_ended_is_answered_as_an_ended_write(self):
        # a process on its way out ends its helpers; the write is then not "failed, nothing changed" but ended:
        # whether it landed first only a read can tell (write_live reads it back, and a switch keeps its record)
        self.sec.put(self.svc, self.acct, OLD)
        self.sec.rule(verb='add', service=self.svc, delay=4.0, times=1)
        timer = threading.Timer(1.0, lambda: keychain.end_children(dialog=lambda: False))
        timer.start()
        t0 = time.monotonic()
        done = self.kc.write(self.svc, self.acct, NEW, interactive=True)
        timer.join()
        self.assertLess(time.monotonic() - t0, 3.5)
        self.assertEqual((done.ok, done.why, done.ended, done.untouched), (False, 'timeout', True, False))
        self.assertEqual(self.kc.state()['resting_all'], None)
        self.wait_until(t0 + 5.0)
        self.assertEqual(self.sec.get(self.svc, self.acct), OLD)
        self.assertEqual(self.sec.landed(self.svc), [])

    def test_a_write_that_never_started_says_the_item_is_untouched(self):
        clock = FakeClock(wall=time.time(), mono=1000.0)
        self.sec.put(self.svc, self.acct, OLD)
        self.kc._gate.acquire()                             # another Keychain call of this process keeps the gate
        try:
            with self.hold(mono=clock.mono, sleep=clock.sleep):
                clock.advance(keychain.UNDER_LOCK_TOTAL_S - 1.0)        # one second is left for this call
                done = self.kc.write(self.svc, self.acct, NEW, interactive=True)
        finally:
            self.kc._gate.release()
        self.assertEqual((done.ok, done.why, done.ended, done.untouched), (False, 'busy', False, True))
        self.assertEqual([c for c in self.sec.calls('add') if c['service'] == self.svc], [])
        with self.hold(mono=clock.mono, sleep=clock.sleep):
            clock.advance(keychain.UNDER_LOCK_TOTAL_S + 0.5)            # the hold's time is spent: nothing starts
            done = self.kc.write(self.svc, self.acct, NEW, interactive=True)
            gone_ = self.kc.delete(self.svc, self.acct)
        self.assertEqual((done.why, done.untouched, gone_.why, gone_.untouched), ('busy', True, 'busy', True))
        self.assertEqual(self.sec.get(self.svc, self.acct), OLD)
        self.assertEqual(self.kc.state()['resting_all'], None)

    def test_busy_behind_an_earlier_read_is_untouched_and_behind_an_earlier_write_is_not(self):
        kc = keychain.Keychain(tool=self.sec.tool, read_deadline=1.0, write_deadline=1.0, log=self.rig.logs.append)
        self.sec.put(self.svc, self.acct, OLD)
        self.sec.rule(verb='find', service=self.svc, delay=3.0, times=1)
        self.assertEqual(kc.read(self.svc, self.acct, fresh=True, interactive=True).why, 'timeout')
        done = kc.write(self.svc, self.acct, NEW, interactive=True)          # the read is still out
        self.assertEqual((done.ok, done.why, done.untouched), (False, 'busy', True))
        deadline = time.monotonic() + 60
        while kc.state()['needs_approval_or_slow'] and time.monotonic() < deadline:
            time.sleep(0.2)
        self.sec.rule(verb='add', service=self.svc, delay=3.0, times=1)
        first = kc.write(self.svc, self.acct, NEW, interactive=True)        # left to finish (outside the lock)
        self.assertEqual((first.ok, first.why, first.ended, first.untouched), (False, 'timeout', False, False))
        done = kc.write(self.svc, self.acct, OTHER, interactive=True)        # the first write is still out
        self.assertEqual((done.ok, done.why, done.untouched), (False, 'busy', False))
        gone_ = kc.delete(self.svc, self.acct)
        self.assertEqual((gone_.ok, gone_.why, gone_.untouched), (False, 'busy', False))
        deadline = time.monotonic() + 60
        while kc.state()['needs_approval_or_slow'] and time.monotonic() < deadline:
            time.sleep(0.2)
        self.assertEqual(self.sec.get(self.svc, self.acct), NEW)

    def test_a_write_ended_at_its_deadline_is_read_back_and_the_earlier_text_means_it_did_not_land(self):
        self.sec.put(self.svc, self.acct, OLD)
        with open(self.cred, 'w') as f:
            f.write(OLD)
        self.sec.rule(verb='add', service=self.svc, delay=7.0, times=1)
        with live.storage_lock(self.ctx) as held:
            done = live.write_live(self.ctx, NEW, held=held, before=OLD)
        self.assertEqual((done.ok, done.why), (False, 'unchanged'))
        self.assertEqual(self.sec.get(self.svc, self.acct), OLD)
        with open(self.cred) as f:
            self.assertEqual(f.read(), OLD)                 # not touched for a write that did not land
        self.assertEqual(len([c for c in self.sec.calls('add') if c['service'] == self.svc]), 1)

    def test_an_absent_item_that_is_still_absent_is_the_earlier_state_too(self):
        self.sec.rule(verb='add', service=self.svc, delay=7.0, times=1)
        with live.storage_lock(self.ctx) as held:
            done = live.write_live(self.ctx, NEW, held=held, before=None)
        self.assertEqual((done.ok, done.why), (False, 'unchanged'))
        self.assertIsNone(self.sec.get(self.svc, self.acct))

    def test_a_write_that_landed_before_its_helper_was_ended_reads_back_as_landed(self):
        self.sec.put(self.svc, self.acct, OLD)
        with open(self.cred, 'w') as f:
            f.write(OLD)
        self.sec.rule(verb='add', service=self.svc, after=7.0, times=1)     # lands at once, then hangs
        with live.storage_lock(self.ctx) as held:
            done = live.write_live(self.ctx, NEW, held=held, before=OLD)
        self.assertTrue(done.ok, done)
        self.assertEqual(self.sec.get(self.svc, self.acct), NEW)
        with open(self.cred) as f:
            self.assertEqual(f.read(), NEW)                 # kept in step, as for any write that lands

    def test_anything_else_read_back_is_failed_and_nothing_more_is_written(self):
        self.sec.put(self.svc, self.acct, OLD)
        self.sec.rule(verb='add', service=self.svc, delay=7.0, times=1)

        def another_writer():                               # one that does not take the save lock
            time.sleep(1.0)
            self.sec.put(self.svc, self.acct, OTHER)
        t = threading.Thread(target=another_writer)
        t.start()
        try:
            with live.storage_lock(self.ctx) as held:
                done = live.write_live(self.ctx, NEW, held=held, before=OLD)
        finally:
            t.join()
        self.assertFalse(done.ok)
        self.assertEqual(done.why, 'timeout')
        self.assertEqual(self.sec.get(self.svc, self.acct), OTHER)
        self.assertEqual(len([c for c in self.sec.calls('add') if c['service'] == self.svc]), 1)

    def test_no_call_starts_once_the_hold_has_used_its_total(self):
        clock = FakeClock(wall=time.time(), mono=1000.0)
        self.sec.put(self.svc, self.acct, OLD)
        with self.hold(mono=clock.mono, sleep=clock.sleep):
            clock.advance(keychain.UNDER_LOCK_TOTAL_S + 0.5)
            r = self.kc.read(self.svc, self.acct, fresh=True, interactive=True)
            done = self.kc.write(self.svc, self.acct, NEW, interactive=True)
        self.assertEqual(r.kind, UNREADABLE)
        self.assertFalse(done.ok)
        self.assertEqual([c for c in self.sec.calls() if c['service'] == self.svc], [])    # nothing was started
        self.assertEqual(self.sec.get(self.svc, self.acct), OLD)

    def test_a_call_gets_only_what_is_left_of_the_total(self):
        clock = FakeClock(wall=time.time(), mono=1000.0)
        self.sec.put(self.svc, self.acct, OLD)
        self.sec.rule(verb='find', service=self.svc, delay=6.0, times=1)
        with self.hold(mono=clock.mono, sleep=clock.sleep):
            clock.advance(keychain.UNDER_LOCK_TOTAL_S - 2.0)
            t0 = time.monotonic()
            r = self.kc.read(self.svc, self.acct, fresh=True, interactive=True)
            took = time.monotonic() - t0
        self.assertEqual((r.kind, r.why), (UNREADABLE, 'timeout'))
        self.assertGreaterEqual(took, 1.7)
        self.assertLess(took, keychain.UNDER_LOCK_S - 0.5)     # cut at the 2 s left, not at 5 s
        self.assert_helper_gone('find')

    def test_the_hold_of_another_thread_does_not_shorten_this_ones_calls(self):
        self.sec.put(self.svc, self.acct, OLD)
        self.sec.rule(verb='find', service=self.svc, delay=6.0, times=1)
        took, got = {}, {}
        holding, done = threading.Event(), threading.Event()

        def holder():
            with self.hold():
                holding.set()
                done.wait(30)
        t = threading.Thread(target=holder)
        t.start()
        holding.wait(30)
        try:
            t0 = time.monotonic()
            got['r'] = self.kc.read(self.svc, self.acct, fresh=True, interactive=True)
            took['s'] = time.monotonic() - t0
        finally:
            done.set()
            t.join()
        self.assertEqual((got['r'].kind, got['r'].value), (PRESENT, OLD))
        self.assertGreaterEqual(took['s'], 6.0)


class OutsideTheLock(Base):
    def test_calls_outside_the_lock_keep_their_deadlines(self):
        self.assertEqual(self.kc.deadlines(), (60.0, 60.0))
        self.sec.put(self.svc, self.acct, OLD)
        self.sec.rule(verb='find', service=self.svc, delay=6.0, times=1)
        self.sec.rule(verb='add', service=self.svc, delay=6.0, times=1)
        t0 = time.monotonic()
        r = self.kc.read(self.svc, self.acct, fresh=True, interactive=True)
        self.assertEqual((r.kind, r.value), (PRESENT, OLD))
        self.assertGreaterEqual(time.monotonic() - t0, 6.0)
        self.assertTrue(self.kc.write(self.svc, self.acct, NEW, interactive=True).ok)
        self.assertEqual(self.sec.get(self.svc, self.acct), NEW)

    def test_a_helper_past_its_deadline_outside_the_lock_is_still_left_to_finish(self):
        kc = keychain.Keychain(tool=self.sec.tool, read_deadline=2.0, write_deadline=2.0, log=self.rig.logs.append)
        self.sec.put(self.svc, self.acct, OLD)
        self.sec.rule(verb='find', service=self.svc, delay=4.0, times=1)
        r = kc.read(self.svc, self.acct, fresh=True, interactive=True)
        self.assertEqual((r.kind, r.why), (UNREADABLE, 'timeout'))
        self.assertEqual(kc.state()['needs_approval_or_slow'], [self.svc])
        deadline = time.monotonic() + 60
        while kc.state()['needs_approval_or_slow'] and time.monotonic() < deadline:
            time.sleep(0.2)
        self.assertEqual(kc.counts['late'], 1)              # it answered by itself; nothing ended it
        self.assertFalse(any('was ended' in x for x in self.rig.logs))


class TheSwitch(Base):
    def setUp(self):
        super().setUp()
        self.a_text = self.rig.keep('uuid-a', 'ana@work.example', 'org-a')
        time.sleep(0.01)
        self.b_text = self.rig.keep('uuid-b', 'ana@home.example', 'org-b')          # Claude Code is on B
        self.holds = []                 # (taken at, let go at) of each hold of the save lock, real seconds

    def label(self):
        return (self.rig.config().get('oauthAccount') or {}).get('accountUuid')

    def live_adds(self):
        return [c for c in self.sec.calls('add') if c['service'] == self.svc]

    def at_hold(self, n, then):
        """Time every hold of the save lock; at the n-th (1 = the switch's write), run then() once it is taken."""
        real = live.storage_lock
        count = {'n': 0}

        @contextmanager
        def timed(ctx, env=None, budget_s=facts.STORAGE_LOCK_BUDGET_S):
            count['n'] += 1
            with real(ctx, env, budget_s) as lock:
                start = time.monotonic()
                if count['n'] == n:
                    then()
                try:
                    yield lock
                finally:
                    self.holds.append((start, time.monotonic()))
        return mock.patch.object(live, 'storage_lock', side_effect=timed)

    def test_a_read_under_the_lock_past_5_s_writes_nothing_and_the_lock_is_let_go_within_its_time(self):
        rule = dict(verb='find', service=self.svc, delay=6.0, times=1)
        with self.at_hold(1, lambda: self.sec.rule(**rule)):
            with self.assertRaises(AccountsError) as e:
                switcher.switch(self.ctx, 'uuid-a')
        self.assertEqual(e.exception.kind, 'keychain_slow')
        self.assertEqual(self.live_adds(), [])
        self.assertEqual(self.sec.get(self.svc, self.acct), self.b_text)
        self.assertEqual(self.label(), 'uuid-b')
        self.assertFalse(os.path.exists(switcher.journal_path(self.ctx)))
        self.assertFalse(os.path.exists(self.lock_path))
        start, end = self.holds[0]
        self.assertLess(end - start, 6.5)

    def test_a_write_past_5_s_is_cut_read_back_and_never_lands_later(self):
        self.sec.rule(verb='add', service=self.svc, delay=7.0, times=1)
        t0 = time.monotonic()
        with self.at_hold(0, lambda: None):
            with self.assertRaises(AccountsError) as e:
                switcher.switch(self.ctx, 'uuid-a')
        self.assertEqual(e.exception.kind, 'keychain_slow')
        self.assertIn('nothing was changed', str(e.exception))
        self.assertFalse(os.path.exists(switcher.journal_path(self.ctx)))
        self.assertFalse(os.path.exists(self.lock_path))
        self.assertEqual(len(self.holds), 1)
        start, end = self.holds[0]
        self.assertLess(end - start, keychain.UNDER_LOCK_TOTAL_S + keychain.END_GRACE_S)
        self.wait_until(max(t0, start) + 8.5)
        self.assertEqual(self.sec.get(self.svc, self.acct), self.b_text)
        self.assertEqual(self.sec.landed(self.svc), [])
        self.assertEqual(self.label(), 'uuid-b')
        res = switcher.switch(self.ctx, 'uuid-a')               # the next switch goes through
        self.assertTrue(res.ok, res.note)

    def gate_kept_through_the_write(self, seconds):
        """write_live, with Switchboard's own Keychain gate kept by another call for `seconds` from the write on."""
        real = live.write_live

        def write(*a, **k):
            self.kc._gate.acquire()
            timer = threading.Timer(seconds, self.kc._gate.release)
            timer.start()
            self.addCleanup(timer.join)
            return real(*a, **k)
        return mock.patch.object(live, 'write_live', side_effect=write)

    def test_a_write_that_never_started_leaves_no_record_behind(self):
        # Switchboard's other Keychain calls kept the gate for the whole short time a call has under the lock, so the
        # switch's write was never started. Nothing can still land: the switch says so, and no record of a switch
        # part-way stays behind (the put-back waits for as long as there is one)
        with self.gate_kept_through_the_write(keychain.UNDER_LOCK_S + 1.5):
            with self.assertRaises(AccountsError) as e:
                switcher.switch(self.ctx, 'uuid-a')
        self.assertEqual(e.exception.kind, 'keychain_slow')
        self.assertIn('nothing was changed', str(e.exception))
        self.assertEqual(self.live_adds(), [])
        self.assertFalse(os.path.exists(switcher.journal_path(self.ctx)))
        self.assertFalse(os.path.exists(self.lock_path))
        self.assertEqual((self.sec.get(self.svc, self.acct), self.label()), (self.b_text, 'uuid-b'))
        time.sleep(1.6)                                         # the other call is done
        res = switcher.switch(self.ctx, 'uuid-a')               # the next switch goes through
        self.assertTrue(res.ok, res.note)
        self.assertEqual(self.label(), 'uuid-a')

    def test_a_write_behind_an_earlier_write_still_out_keeps_its_record(self):
        # 'busy' with a write of this process still out for the item is not "nothing can land": the record stays
        real = self.kc.write

        def write(service, account, value, **k):
            if service == self.svc:
                return keychain.Done(False, 'busy')             # untouched False
            return real(service, account, value, **k)
        with mock.patch.object(self.kc, 'write', side_effect=write):
            res = switcher.switch(self.ctx, 'uuid-a')
        self.assertFalse(res.ok)
        self.assertEqual(switcher.read_journal(self.ctx)[1].get('step'), 'write_unknown')
        self.assertEqual((self.sec.get(self.svc, self.acct), self.label()), (self.b_text, 'uuid-b'))

    def test_a_check_read_cut_under_the_lock_is_made_again_without_it_and_the_switch_stands(self):
        rule = dict(verb='find', service=self.svc, delay=6.0, times=1)
        with self.at_hold(2, lambda: self.sec.rule(**rule)):
            res = switcher.switch(self.ctx, 'uuid-a')
        self.assertTrue(res.ok, res.note)
        self.assertEqual(self.label(), 'uuid-a')
        self.assertEqual(live.parse(self.sec.get(self.svc, self.acct)).fingerprint,
                         live.parse(self.a_text).fingerprint)
        self.assertEqual(len(self.live_adds()), 1)              # no roll back
        for start, end in self.holds:
            self.assertLess(end - start, 6.5)


class TheReadBackRule(unittest.TestCase):
    """write_live's answer after a write ended at its deadline, for every state the item can be read back in."""

    def test_every_state(self):
        unknown = live.NOT_KNOWN
        cases = [  # (read back, the item before the write, what the write did: 'landed', 'unchanged' or None)
            ((PRESENT, NEW), OLD, 'landed'), ((PRESENT, NEW), None, 'landed'), ((PRESENT, NEW), unknown, 'landed'),
            ((PRESENT, OLD), OLD, 'unchanged'), ((ABSENT, None), None, 'unchanged'),
            ((PRESENT, OLD), unknown, None), ((ABSENT, None), unknown, None),
            ((PRESENT, OTHER), OLD, None), ((ABSENT, None), OLD, None), ((PRESENT, OLD), None, None),
            ((UNREADABLE, None), OLD, None), ((UNREADABLE, None), None, None), ((UNREADABLE, None), unknown, None),
        ]
        for (kind, value), before, want in cases:
            back = mock.Mock(kind=kind, value=value)
            self.assertEqual(live.ended_write(back, NEW, before), want, (kind, value, before))


if __name__ == '__main__':
    unittest.main()
