"""A renewal that Anthropic answered but whose new login could not be saved: the new login waits in recovery, and the
saved one is spent. Until the waiting login is saved, the spent one is never posted again (Anthropic would refuse it,
and the account would be marked as needing a sign-in while Switchboard holds its working login). That holds when the
save fails again, and when the waiting login can't be read just now. An account already marked that way gets its
waiting login back at the next attempt. A waiting login that is truly gone changes nothing: the old path stays.

A renewal goes out about two hours before the saved login's time is up, so for those two hours the spent login still
looks usable. A switch or a run to the account in that time takes the waiting login, never the spent one; when the
waiting one can't be saved or read just now it is refused in words that name the cause, and nothing is changed. A
switch to an account with nothing waiting asks nothing of the network or of the recovery item."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import os
import time
import unittest
from unittest import mock

from fakes_accounts import Rig
from switchboard.accounts import live, runas, switcher, tokens, vault
from switchboard.accounts.api import AccountsError

A = 'uuid-a'
SAVED, RECOVERY, WAITING = 'Switchboard login', 'Switchboard recovery', 'successor:' + A


class Clock(object):
    def __init__(self):
        self.w, self.m = time.time(), 50000.0

    def wall(self):
        return self.w

    def mono(self):
        return self.m

    def sleep(self, s):
        self.advance(s)

    def advance(self, s):
        self.w += s
        self.m += s


class Case(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.rig = Rig(wall=self.clock.wall, mono=self.clock.mono, sleep=self.clock.sleep)
        self.addCleanup(self.rig.close)
        self.rig.keep(A, 'a@example.com', 'org-a')
        self.clock.advance(10)                  # (a sign-in is told apart by its refresh expiry, set from the clock)
        self.rig.keep('uuid-b', 'b@example.com', 'org-b')   # Claude Code is on B; A is an idle saved login
        self.rig.set_saved_expiry(A, self.clock.wall() - 60)
        self.ctx, self.kc = self.rig.ctx, self.rig.kc
        self.old_fp = live.parse(self.rig.vault(A)).fingerprint

    def tearDown(self):
        self.settle()

    def settle(self):
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)

    def refresh(self):
        self.settle()
        res = tokens.refresh(self.ctx, A)
        self.settle()
        return res

    def posts(self):
        return len(self.rig.server.token_posts())

    def account(self):
        return vault.load(self.ctx)['accounts'][A]

    def renewed_but_not_saved(self):
        """The first renewal: Anthropic answers, the Keychain refuses the save, and the new login goes to recovery."""
        self.kc.rule('write', service=SAVED, account=A, why='locked', times=1)
        res = self.refresh()
        self.assertEqual(res.outcome, 'stashed')
        self.assertEqual(self.posts(), 1)
        self.assertIsNotNone(self.kc.get(RECOVERY, WAITING))
        self.assertEqual(live.parse(self.rig.vault(A)).fingerprint, self.old_fp)      # the saved one is the spent one
        return res

    def assert_saved_and_working(self):
        self.assertIn(self.rig.server.valid_refresh(A), self.rig.vault(A))
        self.assertIsNone(self.kc.get(RECOVERY, WAITING))
        self.assertIsNone(self.account().get('strike'))
        self.assertNotIn(A, vault.load(self.ctx)['refreshing'])



class WaitingLogin(Case):
    def test_a_save_that_fails_again_posts_nothing_and_the_login_is_saved_once_the_keychain_takes_it(self):
        self.renewed_but_not_saved()
        self.kc.rule('write', service=SAVED, account=A, why='locked', times=1)
        res = self.refresh()
        self.assertEqual(self.posts(), 1)                               # the spent login was not posted
        self.assertEqual((res.outcome, res.spent_maybe), ('stashed', True))     # a switch or a run waits, as before
        self.assertIsNone(self.account().get('strike'))
        self.assertIsNotNone(self.kc.get(RECOVERY, WAITING))
        res = self.refresh()
        self.assertEqual((res.outcome, self.posts()), ('adopted', 1))
        self.assert_saved_and_working()

    def test_a_waiting_login_that_cannot_be_read_now_is_waited_for(self):
        self.renewed_but_not_saved()
        self.kc.rule('read', service=RECOVERY, account=WAITING, why='timeout', times=1)
        res = self.refresh()
        self.assertEqual(self.posts(), 1)
        self.assertEqual((res.outcome, res.spent_maybe), ('transient', True))
        self.assertIsNone(self.account().get('strike'))
        res = self.refresh()
        self.assertEqual((res.outcome, self.posts()), ('adopted', 1))
        self.assert_saved_and_working()

    def test_a_cut_off_renewal_whose_own_item_cannot_be_read_now_is_waited_for(self):
        self.renewed_but_not_saved()
        with open(os.path.join(self.rig.data, 'recovery', 'manifest.json'), 'w') as f:
            f.write('{"entries": []}\n')                        # the list lost its line; the mark and the item stay
        self.kc.rule('read', service=RECOVERY, account=WAITING, why='timeout', times=1)
        res = self.refresh()
        self.assertEqual(self.posts(), 1)
        self.assertEqual((res.outcome, res.spent_maybe), ('transient', True))
        self.assertIsNone(self.account().get('strike'))
        res = self.refresh()
        self.assertEqual((res.outcome, self.posts()), ('adopted', 1))
        self.assertIn(self.rig.server.valid_refresh(A), self.rig.vault(A))
        self.assertIsNone(self.account().get('strike'))

    def test_an_account_already_marked_gets_its_waiting_login_back(self):
        self.renewed_but_not_saved()
        with vault.changing(self.ctx) as doc:                       # as an earlier build left it: refused, mark gone
            doc['accounts'][A]['strike'] = {'fp': self.old_fp, 'at': self.clock.wall(), 'why': 'invalid_grant',
                                            'after_cut_off': True}
            doc['refreshing'].pop(A, None)
        res = self.refresh()
        self.assertEqual((res.outcome, self.posts()), ('adopted', 1))
        self.assert_saved_and_working()

    def test_a_listed_login_that_is_gone_does_not_hold_the_account_back(self):
        self.renewed_but_not_saved()
        self.kc.drop(RECOVERY, WAITING)                             # listed, but neither the item nor a file is there
        res = self.refresh()
        self.assertEqual((res.outcome, self.posts()), ('permanent', 2))     # nothing to wait for: Anthropic is asked
        self.assertEqual((self.account().get('strike') or {}).get('fp'), self.old_fp)


class BeforeItIsUsed(Case):
    """The saved login has not run out yet (the renewal went out early), so nothing would renew it at a switch."""

    def setUp(self):
        Case.setUp(self)
        self.rig.set_saved_expiry(A, self.clock.wall() + 1800)
        self.old_fp = live.parse(self.rig.vault(A)).fingerprint
        self.b_fp = live.parse(self.rig.live_text()).fingerprint

    def switch(self):
        self.settle()
        return switcher.switch(self.ctx, A, kind='manual', source='page')

    def refused(self, kind, *words):
        with self.assertRaises(AccountsError) as caught:
            self.switch()
        self.assertEqual(caught.exception.kind, kind, str(caught.exception))
        for word in words:
            self.assertIn(word, str(caught.exception))
        self.assertEqual(live.parse(self.rig.live_text()).fingerprint, self.b_fp)      # Claude Code is where it was
        self.assertEqual(self.posts(), 1)

    def claimed(self):
        """What a `switchboard run` as A would be given as its login."""
        self.settle()
        with vault.switch_lock(self.ctx, timeout=30.0):
            consume = vault.consume_lock(self.ctx, A, timeout=10.0)
            try:
                return runas._claim(self.ctx, A, 'a@example.com', consume)[2]
            finally:
                consume.release()
                runas.finish(self.ctx, A)

    def test_a_switch_takes_the_waiting_login_never_the_spent_one(self):
        self.renewed_but_not_saved()
        self.assertTrue(self.switch().ok)
        self.assertIn(self.rig.server.valid_refresh(A), self.rig.live_text())       # the login Anthropic will take
        self.assertEqual(self.posts(), 1)                                           # and nothing was posted for it
        self.assert_saved_and_working()

    def test_a_switch_is_refused_while_the_waiting_login_cannot_be_saved(self):
        self.renewed_but_not_saved()
        self.kc.rule('write', service=SAVED, account=A, why='locked', times=1)
        self.refused('keychain_locked', "a@example.com's renewed login", 'nothing was changed')
        self.assertIsNotNone(self.kc.get(RECOVERY, WAITING))
        self.assertTrue(self.switch().ok)                                           # the Keychain takes it now
        self.assertIn(self.rig.server.valid_refresh(A), self.rig.live_text())
        self.assert_saved_and_working()

    def test_a_switch_is_refused_while_the_waiting_login_cannot_be_read(self):
        self.renewed_but_not_saved()
        self.kc.rule('read', service=RECOVERY, account=WAITING, why='timeout', times=1)
        self.refused('keychain_slow', 'nothing was changed')
        self.assertTrue(self.switch().ok)
        self.assertIn(self.rig.server.valid_refresh(A), self.rig.live_text())

    def test_a_run_takes_the_waiting_login_and_is_refused_while_it_cannot_be_saved(self):
        self.renewed_but_not_saved()
        self.kc.rule('write', service=SAVED, account=A, why='locked', times=1)
        with self.assertRaises(AccountsError) as caught:
            self.claimed()
        self.assertEqual(caught.exception.kind, 'keychain_locked', str(caught.exception))
        self.assertNotIn(A, vault.load(self.ctx)['claims'])
        self.assertEqual(self.claimed().fingerprint, live.parse(self.rig.vault(A)).fingerprint)
        self.assertIn(self.rig.server.valid_refresh(A), self.rig.vault(A))
        self.assertEqual(self.posts(), 1)

    def test_a_switch_with_nothing_waiting_asks_nothing_of_the_network_or_the_recovery_item(self):
        self.rig.server.dns_ok = False                                  # offline,
        tokens.WAKE.woke = self.clock.mono()                            # and the Mac just woke
        before, start = self.rig.vault(A), len(self.kc.calls)
        self.assertTrue(self.switch().ok)
        self.assertEqual(live.parse(self.rig.live_text()).fingerprint, self.old_fp)     # the saved login, as it was
        self.assertEqual(self.rig.vault(A), before)
        self.assertEqual(self.posts(), 0)
        during = self.kc.calls[start:]
        self.assertEqual([c for c in during if c[1] == RECOVERY and str(c[2]).startswith('successor:')], [])
        self.assertEqual([c for c in during if c[:2] == ('write', SAVED) and c[2] == A], [])

    def test_a_waiting_login_kept_in_a_damaged_file_is_said_with_a_way_out(self):
        self.kc.rule('write', service=RECOVERY, account=WAITING, why='locked', times=1)    # so it is kept in a file
        self.renewed_in_a_file()
        folder = os.path.join(self.rig.data, 'recovery')
        kept = [n for n in os.listdir(folder) if n.endswith('.json') and n != 'manifest.json']
        self.assertEqual(len(kept), 1)
        with open(os.path.join(folder, kept[0]), 'w') as f:
            f.write('{"key": ')                                         # cut off part-way
        with self.assertRaises(AccountsError) as caught:
            self.switch()
        self.assertEqual(str(caught.exception),
                         "A renewed login that may be waiting for a@example.com can't be read (unparseable), so "
                         "nothing was changed. If this keeps happening, sign in to a@example.com in Claude Code, and "
                         "Switchboard keeps it again.")
        self.assertEqual(live.parse(self.rig.live_text()).fingerprint, self.b_fp)
        self.assertEqual(self.posts(), 1)

    def renewed_in_a_file(self):
        self.kc.rule('write', service=SAVED, account=A, why='locked', times=1)
        self.assertEqual(self.refresh().outcome, 'stashed')
        self.assertIsNone(self.kc.get(RECOVERY, WAITING))

    def test_a_switch_whose_own_renewal_cannot_be_saved_names_the_keychain_and_the_next_one_works(self):
        self.rig.set_saved_expiry(A, self.clock.wall() - 60)            # run out: the switch renews it itself
        self.kc.rule('write', service=SAVED, account=A, why='locked', times=1)
        with self.assertRaises(AccountsError) as caught:
            self.switch()
        self.assertEqual(caught.exception.kind, 'keychain_locked', str(caught.exception))
        self.assertNotIn('Anthropic', str(caught.exception))            # Anthropic answered: the save is what failed
        self.assertEqual(live.parse(self.rig.live_text()).fingerprint, self.b_fp)
        self.assertEqual(self.posts(), 1)
        self.assertTrue(self.switch().ok)
        self.assertIn(self.rig.server.valid_refresh(A), self.rig.live_text())
        self.assertEqual(self.posts(), 1)                               # the waiting one was taken: nothing posted
        self.assert_saved_and_working()

    def test_a_look_that_breaks_never_stops_the_switch(self):
        with mock.patch.object(vault, 'stashed', side_effect=RuntimeError('made up')):
            res = tokens.adopt_waiting(self.ctx, A)
        self.assertEqual((res.outcome, res.spent_maybe), ('skipped', False))
        self.assertIn('RuntimeError', self.rig.logs[-1])                # and the log says the look failed
        real = tokens.adopt_waiting
        with mock.patch.object(tokens, 'adopt_waiting', lambda ctx, aid: real(ctx, aid)), \
                mock.patch.object(tokens, '_waiting', side_effect=RuntimeError('made up')):
            self.assertTrue(self.switch().ok)                           # nothing waits: _waiting is not even asked

    def test_a_lapsed_saved_login_whose_waiting_one_cannot_be_saved_names_the_keychain(self):
        self.rig.set_saved_expiry(A, self.clock.wall() + 1800)
        self.renewed_but_not_saved()
        self.clock.advance(3600)                                        # the spent login's time is up now
        self.kc.rule('write', service=SAVED, account=A, why='locked', times=1)
        with self.assertRaises(AccountsError) as caught:
            self.switch()
        self.assertEqual(caught.exception.kind, 'keychain_locked', str(caught.exception))
        self.assertNotIn('Anthropic', str(caught.exception))
        self.assertEqual(self.posts(), 1)
        self.kc.rule('write', service=SAVED, account=A, why='locked', times=1)
        with self.assertRaises(AccountsError) as caught:
            self.claimed()
        self.assertEqual(caught.exception.kind, 'keychain_locked', str(caught.exception))
        self.assertEqual(self.posts(), 1)


if __name__ == '__main__':
    unittest.main()
