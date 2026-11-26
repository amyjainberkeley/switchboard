"""The Keychain wrapper against a fake /usr/bin/security: every answer kept apart, one call at a time, deadlines, a
helper that may be showing a dialog never killed."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import os
import threading
import time
import unittest

from fakes_accounts import Rig
from switchboard.accounts import keychain
from switchboard.accounts.api import ABSENT, PRESENT, UNREADABLE

SVC, ACCT = 'Claude Code-credentials', 'tester'


class KeychainCase(unittest.TestCase):
    def setUp(self):
        self.rig = Rig(real_keychain=True)
        self.sec = self.rig.sec
        # generous deadlines for answers that must come (a process start at background priority on a loaded Mac
        # can take seconds); a short one only where a test wants "no answer in time"
        self.kc = self.make(120.0)

    def make(self, deadline):
        return keychain.Keychain(tool=self.sec.tool, read_deadline=deadline, write_deadline=deadline, backoff_s=30.0,
                                 gate_path=os.path.join(self.rig.data, 'keychain.gate'))

    def settle(self, kc, limit=90.0):
        end = time.time() + limit
        while kc.state()['needs_approval_or_slow'] and time.time() < end:
            time.sleep(0.2)
        self.assertEqual(kc.state()['needs_approval_or_slow'], [])

    def tearDown(self):
        self.rig.close()


class EachAnswerKeepsItsOwnMeaning(KeychainCase):
    def test_each_keychain_answer_maps_to_its_own_state_and_only_44_in_a_searchable_keychain_is_absent(self):
        self.assertEqual(self.kc.read(SVC, ACCT, fresh=True).kind, ABSENT)
        self.sec.put(SVC, ACCT, '{"x":1}')
        self.sec.rule(verb='find', exit=36, stderr='security: User interaction is not allowed.\n', times=1)
        r = self.kc.read(SVC, ACCT, fresh=True, interactive=True)
        self.assertEqual((r.kind, r.why), (UNREADABLE, 'locked'))
        self.sec.rule(verb='find', exit=51, times=1)
        r = self.kc.read(SVC, ACCT, fresh=True, interactive=True)
        self.assertEqual((r.kind, r.why), (UNREADABLE, 'denied'))
        self.sec.rule(verb='find', exit=128, times=1)
        self.assertEqual(self.kc.read(SVC, ACCT, fresh=True, interactive=True).why, 'denied')
        self.sec.rule(verb='find', exit=7, stderr='something odd\n', times=1)
        self.assertEqual(self.kc.read(SVC, ACCT, fresh=True, interactive=True).why, 'unknown')
        r = self.kc.read(SVC, ACCT, fresh=True, interactive=True)
        self.assertEqual((r.kind, r.value, r.provenance), (PRESENT, '{"x":1}', 'keychain'))

    def test_a_hang_is_timeout_never_absent_and_success_after_three_failures_is_present(self):
        self.sec.put(SVC, ACCT, '{"x":2}')
        self.sec.rule(verb='find', delay=6.0, times=1)
        quick = self.make(1.0)
        r = quick.read(SVC, ACCT, fresh=True)
        self.assertEqual((r.kind, r.why), (UNREADABLE, 'timeout'))
        self.settle(quick)                                  # the left-alone helper finishes by itself
        self.sec.rule(verb='find', exit=1, times=3)
        for _ in range(3):
            r = self.kc.read(SVC, ACCT, fresh=True, interactive=True)
            self.assertEqual((r.kind, r.why), (UNREADABLE, 'unknown'), r)
        r = self.kc.read(SVC, ACCT, fresh=True, interactive=True)
        self.assertEqual((r.kind, r.value), (PRESENT, '{"x":2}'), (r, self.kc.state()))

    def test_exit_44_outside_the_login_session_is_unreadable_not_logged_out(self):
        self.sec.no_login_keychain()
        r = self.kc.read(SVC, ACCT, fresh=True)
        self.assertEqual((r.kind, r.why), (UNREADABLE, 'no_keychain'))
        self.assertEqual(self.kc.exists(SVC, ACCT).kind, UNREADABLE)

    def test_a_missing_tool_is_unreadable_not_absent(self):
        kc = keychain.Keychain(tool=os.path.join(self.rig.root, 'nothing-here'), read_deadline=5.0)
        r = kc.read(SVC, ACCT, fresh=True)
        self.assertEqual((r.kind, r.why), (UNREADABLE, 'no_tool'))
        self.assertFalse(kc.write(SVC, ACCT, 'x').ok)


class OneAtATime(KeychainCase):
    def test_concurrent_callers_are_serialised_so_the_fake_never_sees_two_at_once(self):
        self.sec.put(SVC, ACCT, '{"x":3}')
        self.sec.rule(verb='find', delay=0.15, times=-1)
        other = self.make(60.0)
        self.kc.read_deadline = 60.0
        results = []

        def go(kc):
            results.append(kc.read(SVC, ACCT, fresh=True, interactive=True).kind)

        threads = [threading.Thread(target=go, args=(self.kc if i % 2 else other,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results, [PRESENT] * 6)
        self.assertEqual(self.sec.max_concurrent(), 1)


class NeverKillADialog(KeychainCase):
    def test_a_read_that_times_out_is_left_running_marked_needs_approval_and_its_late_answer_is_kept(self):
        self.sec.put(SVC, ACCT, '{"late":true}')
        self.sec.rule(verb='find', delay=8.0, times=1)
        kc = self.make(1.0)
        r = kc.read(SVC, ACCT, fresh=True)
        self.assertEqual(r.why, 'timeout')
        self.assertIn(SVC, kc.state()['needs_approval_or_slow'])
        again = kc.read(SVC, ACCT, fresh=True, interactive=True)            # no second helper while one waits
        self.assertEqual((again.kind, again.why), (UNREADABLE, 'timeout'))
        self.assertEqual(kc.write(SVC, ACCT, '{"other":1}').why, 'busy')     # never races the waiting helper
        self.settle(kc)
        self.assertEqual(len(self.sec.calls('find')), 1)
        cached = kc.read(SVC, ACCT)                          # display read: the late answer, labelled as cached
        self.assertEqual((cached.kind, cached.value, cached.provenance, cached.degraded),
                         (PRESENT, '{"late":true}', 'cache', True))
        self.assertEqual(self.sec.get(SVC, ACCT), '{"late":true}')

    def test_too_many_helpers_waiting_on_dialogs_start_no_new_ones(self):
        kc = self.make(1.0)
        for acct in ('a', 'b'):
            self.sec.put(SVC, acct, '{}')
            self.sec.rule(verb='find', account=acct, delay=8.0, times=1)
            self.assertEqual(kc.read(SVC, acct, fresh=True, interactive=True).why, 'timeout')
        self.sec.put(SVC, 'c', '{}')
        self.assertEqual(kc.read(SVC, 'c', fresh=True, interactive=True).why, 'busy')
        self.settle(kc)
        self.assertEqual(sorted(c['account'] for c in self.sec.calls('find')), ['a', 'b'])


class RestAfterFailure(KeychainCase):
    def test_a_failed_read_rests_serves_the_last_good_value_as_degraded_and_never_spawns_in_a_loop(self):
        self.sec.put(SVC, ACCT, '{"good":1}')
        self.assertEqual(self.kc.read(SVC, ACCT).provenance, 'keychain')
        self.sec.rule(verb='find', exit=36, times=1)
        r = self.kc.read(SVC, ACCT)
        self.assertEqual((r.kind, r.provenance, r.degraded), (PRESENT, 'cache', True))
        n = len(self.sec.calls('find'))
        for _ in range(5):
            self.assertTrue(self.kc.read(SVC, ACCT).degraded)
        self.assertEqual(len(self.sec.calls('find')), n)          # resting: no helper started
        fresh = self.kc.read(SVC, ACCT, fresh=True)
        self.assertEqual((fresh.kind, fresh.why), (UNREADABLE, 'locked'))   # bytes to spend are never a cache
        self.assertEqual(self.kc.read(SVC, ACCT, fresh=True, interactive=True).value, '{"good":1}')

    def test_a_person_pressing_try_again_is_not_held_by_the_rest(self):
        self.sec.put(SVC, ACCT, '{}')
        self.sec.rule(verb='find', exit=36, times=1)
        self.kc.read(SVC, ACCT, fresh=True)
        self.assertEqual(self.kc.read(SVC, ACCT, fresh=True, interactive=True).kind, PRESENT)


class WritesAndShapes(KeychainCase):
    def test_writes_go_as_hex_on_standard_input_update_in_place_and_never_touch_the_access_list(self):
        value = '{"claudeAiOauth":{"refreshToken":"r"},"note":"café"}'
        self.assertTrue(self.kc.write(SVC, ACCT, value).ok)
        self.assertTrue(self.kc.write(SVC, ACCT, value + ' ').ok)
        add = self.sec.calls('add')
        self.assertEqual(len(add), 2)
        for c in add:
            self.assertIn('-U', c['flags'])
            self.assertNotIn('-T', c['flags'])
            self.assertEqual((c['via'], c['argv_value']), ('stdin', False))   # never in `ps`
        self.assertEqual(self.sec.get(SVC, ACCT), value + ' ')

    def test_a_value_security_prints_as_hex_reads_back_as_text(self):
        value = '{"name":"Amélie — personal"}'
        self.sec.put(SVC, ACCT, value)
        self.assertEqual(self.kc.read(SVC, ACCT, fresh=True).value, value)

    def test_deleting_a_missing_item_counts_as_done_and_exists_never_asks_for_the_secret(self):
        self.assertTrue(self.kc.delete(SVC, ACCT).ok)
        self.sec.put(SVC, ACCT, '{}')
        r = self.kc.exists(SVC, ACCT)
        self.assertEqual(r.kind, PRESENT)
        self.assertTrue(r.value)
        self.assertEqual(self.sec.calls('find'), [])
        self.assertEqual(self.kc.exists('nothing', ACCT).kind, ABSENT)


if __name__ == '__main__':
    unittest.main()
