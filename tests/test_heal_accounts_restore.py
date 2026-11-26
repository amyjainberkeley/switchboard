import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# Switchboard's list of kept accounts (accounts.json) comes back by itself from the newest of its five copies that
# can be used, when the list can't be read or used; the damaged file is kept; a list that reads fine is never touched;
# a bad file is never rotated into the copies; the logins in the Keychain are never touched by any of it.
import glob
import json
import os
import threading
import time
import unittest

from fakes_accounts import Rig
from switchboard import repairs
from switchboard.accounts import api, context, vault


class Case(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        self.rig.two_accounts()
        for alias in ('home', 'house', 'flat', 'den', 'home'):     # more saves: five copies, each with both accounts
            with vault.changing(self.ctx) as doc:
                doc['accounts']['uuid-a']['alias'] = alias
        self.path = vault.store_path(self.ctx)
        self.keychain = dict(self.rig.sec.items())

    def tearDown(self):
        self.use.__exit__(None, None, None)
        self.rig.close()

    def write(self, path, text):
        with open(path, 'w') as f:
            f.write(text)

    def raw(self, path):
        with open(path, 'rb') as f:
            return f.read()

    def aside(self, kind='damaged'):
        return sorted(glob.glob(self.path + '.%s-*' % kind))

    def assertBothAccounts(self, doc):
        self.assertEqual(sorted(doc['accounts']), ['uuid-a', 'uuid-b'])
        self.assertEqual(doc['accounts']['uuid-a']['email'], 'a@example.com')

    def assertKeychainUntouched(self):
        self.assertEqual(dict(self.rig.sec.items()), self.keychain)


class ItComesBack(Case):
    def test_a_half_written_list_comes_back_from_the_newest_good_copy_and_says_so_once(self):
        good = self.raw(self.path)                  # the list as of its last save (accounts.json.last-good)
        self.write(self.path, '{"schema": 1, "accounts": {"uuid-a": ')
        doc = vault.load(self.ctx)
        self.assertBothAccounts(doc)
        self.assertEqual(self.raw(self.path), good)                         # written back as the list
        [kept] = self.aside()
        self.assertEqual(self.raw(kept), b'{"schema": 1, "accounts": {"uuid-a": ')   # the damaged file is kept
        said = [e for e in self.rig.events if e[0].startswith("Switchboard's list of accounts was damaged.")]
        self.assertEqual(len(said), 1)
        self.assertRegex(said[0][0], r"^Switchboard's list of accounts was damaged\. It was restored from a copy made "
                                     r"\w{3} \w{3} \d+ at \d+:\d\d [AP]M\.$")
        self.assertEqual(said[0][2].get('h'), 'repair')
        self.assertEqual(len([x for x in self.rig.logs if 'Restored the list of accounts' in x]), 1)
        notes = repairs.recent(self.rig.data)
        self.assertEqual([(n['what'], n['kind']) for n in notes], [('accounts', 'restored')])
        self.assertBothAccounts(vault.load(self.ctx))                       # and the next look says nothing
        self.assertEqual(len([e for e in self.rig.events if 'was damaged' in e[0]]), 1)
        self.assertEqual(len(self.aside()), 1)
        self.assertEqual([a.email for a in api.accounts()], ['a@example.com', 'b@example.com'])
        self.assertKeychainUntouched()

    def test_the_wrong_shape_counts_as_damaged(self):
        for text in ('{"schema": 1, "accounts": []}', '[]', '{"schema": 1, "accounts": {"uuid-a": "x"}}',
                     '{"schema": 1, "accounts": {"uuid-a": {"email": "a@example.com"}}}', '{"schema": 1}',
                     '{"schema": "1", "accounts": {}}', '{"schema": 1, "accounts": {}, "order": "uuid-a"}'):
            with self.subTest(text=text):
                self.write(self.path, text)
                self.assertBothAccounts(vault.load(self.ctx))
                self.assertEqual(self.raw(self.aside()[-1]), text.encode())
                for p in self.aside():
                    os.unlink(p)

    def test_a_list_from_a_newer_switchboard_is_left_as_it_is_and_refused(self):
        # a newer Switchboard's list is never set aside nor replaced: an older copy still running would undo it
        newer = json.dumps({'schema': vault.SCHEMA + 1, 'accounts': {}})
        self.write(self.path, newer)
        with self.assertRaises(vault.VaultError) as e:
            vault.load(self.ctx)
        self.assertEqual(e.exception.kind, 'newer')
        self.assertEqual(self.raw(self.path), newer.encode())
        self.assertEqual(self.aside('newer'), [])
        self.assertFalse(any('written by a newer Switchboard' in e[0] for e in self.rig.events))

    def test_a_copy_that_is_damaged_or_incomplete_is_passed_over(self):
        self.write(self.path + '.last-good', 'not json')     # the copy of the last save is tried first: damaged too
        self.write(self.path + '.1', 'not json')
        doc = json.loads(self.raw(self.path + '.2'))
        doc['accounts']['uuid-a'].pop('uuid')                               # an account without its id
        self.write(self.path + '.2', json.dumps(doc))
        want = self.raw(self.path + '.3')
        self.write(self.path, '')
        self.assertBothAccounts(vault.load(self.ctx))
        self.assertEqual(self.raw(self.path), want)

    def test_a_change_under_the_account_lock_restores_too(self):
        self.write(self.path, '{"schema": 1, "acc')
        with vault.changing(self.ctx) as doc:
            self.assertBothAccounts(doc)
            doc['accounts']['uuid-b']['alias'] = 'work'
        self.assertEqual(vault.load(self.ctx)['accounts']['uuid-b']['alias'], 'work')


class ItDoesNotActWhenItShouldNot(Case):
    def test_a_list_that_reads_fine_is_never_restored_even_when_every_copy_is_bad(self):
        for n in range(1, vault.BACKUPS + 1):
            if os.path.exists('%s.%d' % (self.path, n)):
                self.write('%s.%d' % (self.path, n), 'garbage')
        before = self.raw(self.path)
        self.assertBothAccounts(vault.load(self.ctx))
        self.assertEqual(self.raw(self.path), before)
        self.assertEqual(self.aside(), [])
        self.assertEqual(repairs.recent(self.rig.data), [])
        self.assertFalse(any('damaged' in e[0] for e in self.rig.events))

    def test_an_empty_list_that_reads_fine_stays_empty(self):
        with vault.changing(self.ctx) as doc:
            doc['accounts'], doc['order'] = {}, []
        self.assertEqual(vault.load(self.ctx)['accounts'], {})
        self.assertEqual(self.aside(), [])

    def test_with_no_copy_that_can_be_used_nothing_moves_and_it_is_still_an_error(self):
        for name in ['%s.%d' % (self.path, n) for n in range(1, vault.BACKUPS + 1)] + [self.path + '.last-good']:
            try:
                os.unlink(name)
            except OSError:
                pass
        self.write(self.path, '{"half')
        with self.assertRaises(vault.VaultError) as e:
            vault.load(self.ctx)
        self.assertIn('none of its copies', str(e.exception))
        self.assertEqual(self.raw(self.path), b'{"half')                  # left where it was, as it was
        self.assertEqual(self.aside(), [])
        self.assertEqual(repairs.recent(self.rig.data), [])

    def test_a_restore_waits_for_whoever_holds_the_account_lock_and_never_runs_beside_it(self):
        self.write(self.path, '{"half')
        held, done = threading.Event(), threading.Event()

        def holder():
            with vault.account_lock(self.ctx):
                held.set()
                done.wait(10)
        t = threading.Thread(target=holder)
        t.start()
        held.wait(5)
        try:
            with self.assertRaises(vault.VaultError):
                vault.load(self.ctx)                                        # busy: told, not restored beside it
            self.assertEqual(self.raw(self.path), b'{"half')
        finally:
            done.set()
            t.join(10)
        self.assertBothAccounts(vault.load(self.ctx))                       # the next look restores it

    def test_doctor_s_look_reports_and_changes_nothing(self):
        self.write(self.path, '{"half')
        with vault.no_restore():
            with self.assertRaises(vault.VaultError):
                vault.load(self.ctx)
        self.assertEqual(self.raw(self.path), b'{"half')
        self.assertEqual(self.aside(), [])

    def test_a_file_that_cannot_be_read_for_a_moment_is_left_alone(self):
        real = vault.ccfiles.read_json

        def flaky(path):
            if path == self.path:
                return api.Read(api.UNREADABLE, why='io'), None
            return real(path)
        vault.ccfiles.read_json = flaky
        try:
            with self.assertRaises(vault.VaultError):
                vault.load(self.ctx)
        finally:
            vault.ccfiles.read_json = real
        self.assertEqual(self.aside(), [])
        self.assertBothAccounts(vault.load(self.ctx))


class ABadFileNeverPushesTheCopiesOut(Case):
    def test_a_save_sets_a_bad_file_aside_instead_of_rotating_it_in(self):
        copies = dict((n, self.raw('%s.%d' % (self.path, n))) for n in range(1, vault.BACKUPS + 1)
                      if os.path.exists('%s.%d' % (self.path, n)))
        doc = vault.load(self.ctx)
        self.write(self.path, 'damaged between the read and the write')
        with vault.account_lock(self.ctx):
            vault.save(self.ctx, doc)
        for n, b in copies.items():
            self.assertEqual(self.raw('%s.%d' % (self.path, n)), b, n)     # not one copy moved
        [kept] = self.aside()
        self.assertEqual(self.raw(kept), b'damaged between the read and the write')
        self.assertBothAccounts(vault.load(self.ctx))

    def test_a_good_file_still_rotates_as_before(self):
        before = self.raw(self.path)
        with vault.changing(self.ctx) as doc:
            doc['accounts']['uuid-a']['alias'] = 'house'
        self.assertEqual(self.raw(self.path + '.1'), before)


if __name__ == '__main__':
    unittest.main()
