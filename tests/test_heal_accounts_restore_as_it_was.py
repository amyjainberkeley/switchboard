import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# A damaged list of accounts comes back as it was at its last save: every save also writes the same bytes to
# accounts.json.last-good, and the restore takes that copy first. Only when that copy can't be used either does an
# older numbered copy come back, and then the sentence says that accounts added, removed or changed after it are not
# in it, and the rules kept for an account missing from it outlive any other settings change for a day. A list
# written by a newer Switchboard is never set aside or replaced: nothing is written or moved, and the answer is a
# refusal that says to update. A save whose copy can't be written still saves the list. Made-up *.example accounts on
# the engine's stand-ins (tests/fakes_accounts.py).
import glob
import json
import os
import stat
import unittest
from unittest import mock

from fakes_accounts import Rig
from switchboard.accounts import autoloop, ccfiles, context, vault

LAST_GOOD = '.last-good'


class Case(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        self.addCleanup(self.close)
        self.rig.keep('uuid-a', 'a@work.example', 'org-a')
        self.rig.keep('uuid-b', 'b@home.example', 'org-b')
        self.rig.keep('uuid-c', 'c@third.example', 'org-c')          # added last: the list's last save
        self.path = vault.store_path(self.ctx)
        autoloop.save_settings(rule=('uuid-c', 'out'))               # and the person keeps it out of auto-switch

    def close(self):
        self.use.__exit__(None, None, None)
        self.rig.close()

    def write(self, path, text):
        with open(path, 'w') as f:
            f.write(text)

    def raw(self, path):
        with open(path, 'rb') as f:
            return f.read()

    def rules(self):
        return autoloop.settings_dict().get('rules') or {}

    def files(self):
        """Every file of the list beside it, with its bytes."""
        return dict((p, self.raw(p)) for p in sorted(glob.glob(self.path + '*')) if os.path.isfile(p))

    def said(self):
        return [e[0] for e in self.rig.events if e[0].startswith("Switchboard's list of accounts")]


class TheListAsItWas(Case):
    def test_a_damaged_list_comes_back_with_the_account_added_last_and_its_rule_stays(self):
        self.write(self.path, '{"schema": 1, "accoun')
        doc = vault.load(self.ctx)
        self.assertEqual(sorted(doc['accounts']), ['uuid-a', 'uuid-b', 'uuid-c'])
        self.assertEqual(self.rules(), {'uuid-c': 'out'})
        autoloop.save_settings(strategy='most-room')                 # an unrelated policy change
        self.assertEqual(self.rules(), {'uuid-c': 'out'})
        [text] = self.said()
        self.assertRegex(text, r"^Switchboard's list of accounts was damaged\. It was restored from a copy made ")
        self.assertNotIn('not in it', text)

    def test_the_record_of_what_was_tried_once_survives(self):
        with vault.changing(self.ctx) as doc:
            doc['put_live'] = {'uuid-c': {'tried': 'made-up-fp', 'at': [1.0]}}
        self.write(self.path, 'not json')
        self.assertEqual(vault.load(self.ctx).get('put_live'), {'uuid-c': {'tried': 'made-up-fp', 'at': [1.0]}})

    def test_every_save_writes_the_copy_whole_and_private(self):
        with vault.changing(self.ctx) as doc:
            doc['accounts']['uuid-a']['alias'] = 'work'
        self.assertEqual(self.raw(self.path + LAST_GOOD), self.raw(self.path))
        self.assertEqual(stat.S_IMODE(os.stat(self.path + LAST_GOOD).st_mode), 0o600)


class AnOlderCopyWhenThereIsNoOther(Case):
    def test_it_says_what_is_not_in_it_and_no_rule_is_lost(self):
        self.write(self.path + LAST_GOOD, 'damaged too')
        self.write(self.path, '{"half')
        doc = vault.load(self.ctx)
        self.assertEqual(sorted(doc['accounts']), ['uuid-a', 'uuid-b'])     # the copy before the last save
        [text] = self.said()
        self.assertRegex(text, r"^Switchboard's list of accounts was damaged\. It was restored from an older copy, "
                               r"made .*: accounts added, removed or changed after that are not in it\. Add an "
                               r"account that is missing again\.$")
        autoloop.save_settings(strategy='most-room')
        self.assertEqual(self.rules(), {'uuid-c': 'out'})                    # kept while the restore is recent

    def test_a_removal_still_takes_the_rule_with_it(self):
        self.write(self.path + LAST_GOOD, 'damaged too')
        self.write(self.path, '{"half')
        vault.load(self.ctx)
        autoloop.forget_rules(['uuid-c'])
        self.assertEqual(self.rules(), {})


class AListFromANewerSwitchboard(Case):
    def test_doctor_says_it_is_a_newer_list_and_to_update_and_promises_no_copy(self):
        from switchboard import selfcheck
        self.write(self.path, json.dumps({'schema': vault.SCHEMA + 1, 'accounts': {}, 'order': []}))
        before = self.files()
        for fix in (False, True):                      # plain doctor (a look), then doctor --fix
            with self.subTest(fix=fix):
                (r,) = selfcheck.check_accounts(fix, self.ctx.wall())
                self.assertEqual((r.state, r.found, r.fix),
                                 (selfcheck.PROBLEM, 'was written by a newer Switchboard than this one',
                                  'Update this one: switchboard update'))
                self.assertEqual(self.files(), before)

    def test_nothing_is_written_or_moved_and_the_answer_is_a_refusal(self):
        newer = json.dumps({'schema': vault.SCHEMA + 1, 'accounts': {}, 'order': []})
        self.write(self.path, newer)
        before = self.files()
        with self.assertRaises(vault.VaultError) as e:
            vault.load(self.ctx)
        self.assertEqual(e.exception.kind, 'newer')
        self.assertTrue(str(e.exception).endswith('Update this one: switchboard update'), str(e.exception))
        with self.assertRaises(vault.VaultError):
            with vault.changing(self.ctx) as doc:
                doc['accounts'] = {}
        doc = {'schema': vault.SCHEMA, 'accounts': {}, 'order': []}       # a save that read the list before it
        with self.assertRaises(vault.VaultError):
            with vault.account_lock(self.ctx):
                vault.save(self.ctx, doc)
        self.assertEqual(self.files(), before)
        self.assertEqual(self.said(), [])


class ACopyThatCannotBeWritten(Case):
    def test_the_list_is_saved_all_the_same(self):
        real = ccfiles.atomic_write

        def fails_for_the_copy(path, data, *a, **k):
            if path.endswith(LAST_GOOD):
                raise OSError('made-up disk error')
            return real(path, data, *a, **k)
        with mock.patch.object(vault.ccfiles, 'atomic_write', side_effect=fails_for_the_copy):
            with vault.changing(self.ctx) as doc:
                doc['accounts']['uuid-a']['alias'] = 'work'
        self.assertEqual(vault.load(self.ctx)['accounts']['uuid-a']['alias'], 'work')
        self.assertTrue([x for x in self.rig.logs if 'copy of the list of accounts' in x], self.rig.logs)


if __name__ == '__main__':
    unittest.main()
