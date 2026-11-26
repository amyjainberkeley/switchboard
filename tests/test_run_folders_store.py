import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# Where a folder's account is kept (accounts/folders.py): <data>/folders.json, owner-only, by the folder's real path.
# The nearest folder above wins; two spellings of one folder are one folder; an account removed from Switchboard takes
# its folders with it. Made-up accounts and folders.
import os
import random
import shutil
import stat
import sys
import tempfile
import time
import unicodedata
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import core
from switchboard.accounts import api, context, folders, tokens
from fakes_accounts import Rig


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix='sbfolders-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for p in (core.data_path(folders.FILE),):
            if os.path.exists(p):
                os.remove(p)
        self.addCleanup(lambda: os.path.exists(core.data_path(folders.FILE)) and
                        os.remove(core.data_path(folders.FILE)))

    def mk(self, *parts):
        p = os.path.join(self.tmp, *parts)
        os.makedirs(p, exist_ok=True)
        return p


class SetAndFind(Store):
    def test_the_folder_and_every_folder_inside_it_find_it(self):
        work = self.mk('work')
        deep = self.mk('work', 'client', 'src', 'lib')
        folders.set_account(work, 'uuid-a', 'maya@fernhill.example', 100.0)
        st = os.stat(work)
        for where in (work, deep):
            self.assertEqual(folders.nearest(where), (work, {'account': 'uuid-a', 'email': 'maya@fernhill.example',
                                                             'at': 100.0, 'id': [st.st_dev, st.st_ino]}))
        self.assertIsNone(folders.nearest(self.mk('elsewhere')))
        self.assertIsNone(folders.nearest(self.tmp))                     # a folder above is not inside

    def test_the_nearest_folder_above_wins(self):
        work, client = self.mk('work'), self.mk('work', 'client')
        src = self.mk('work', 'client', 'src')
        folders.set_account(work, 'uuid-a', 'maya@fernhill.example', 1.0)
        folders.set_account(client, 'uuid-b', 'maya@northwind.example', 2.0)
        self.assertEqual(folders.nearest(src)[0], client)
        self.assertEqual(folders.nearest(client)[0], client)
        self.assertEqual(folders.nearest(self.mk('work', 'other'))[0], work)

    def test_the_file_is_owner_only_and_names_the_real_folder(self):
        work = self.mk('work')
        folders.set_account(work + os.sep, 'uuid-a', 'maya@fernhill.example', 1.0)
        st = os.stat(core.data_path(folders.FILE))
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o600)
        ident = [os.stat(work).st_dev, os.stat(work).st_ino]
        self.assertEqual(core.read_json(core.data_path(folders.FILE)),
                         {'v': 1, 'folders': {work: {'account': 'uuid-a', 'email': 'maya@fernhill.example', 'at': 1.0,
                                                     'id': ident}}})

    def test_setting_again_replaces_and_off_takes_only_its_own_entry(self):
        work, client = self.mk('work'), self.mk('work', 'client')
        folders.set_account(work, 'uuid-a', 'maya@fernhill.example', 1.0)
        folders.set_account(client, 'uuid-a', 'maya@fernhill.example', 1.0)
        folders.set_account(client, 'uuid-b', 'maya@northwind.example', 2.0)
        self.assertEqual([(k, e['account']) for k, e in folders.entries()], [(work, 'uuid-a'), (client, 'uuid-b')])
        self.assertEqual(folders.clear(client)['account'], 'uuid-b')
        self.assertIsNone(folders.clear(client))                         # twice: nothing more to take out
        self.assertEqual(folders.nearest(client)[0], work)               # the folder above still applies
        self.assertIsNone(folders.own(client))

    def test_a_file_that_cannot_be_read_is_never_written_over(self):
        work = self.mk('work')
        with open(core.data_path(folders.FILE), 'w') as f:
            f.write('{not json')
        self.assertEqual(folders.entries(), [])
        self.assertIsNone(folders.nearest(work))
        with self.assertRaises(folders.Unreadable):
            folders.set_account(work, 'uuid-a', 'maya@fernhill.example', 1.0)
        with self.assertRaises(folders.Unreadable):
            folders.clear(work)
        folders.forget_account(['uuid-a'])                               # never raises
        with open(core.data_path(folders.FILE)) as f:
            self.assertEqual(f.read(), '{not json')


class Spellings(Store):
    def test_links_dots_and_a_trailing_slash_are_one_folder(self):
        work = self.mk('real', 'work')
        self.mk('real', 'work', 'src')
        os.symlink(os.path.join(self.tmp, 'real'), os.path.join(self.tmp, 'linked'))
        via_link = os.path.join(self.tmp, 'linked', 'work')
        folders.set_account(via_link + os.sep, 'uuid-a', 'maya@fernhill.example', 1.0)
        self.assertEqual([k for k, _ in folders.entries()], [work])
        for where in (work, work + os.sep, via_link, os.path.join(via_link, 'src'),
                      os.path.join(self.tmp, 'real', 'work', 'src', '..'), os.path.join(via_link, 'src', '.')):
            with self.subTest(where=where):
                self.assertEqual(folders.nearest(where)[0], work)
        folders.set_account(work, 'uuid-b', 'maya@northwind.example', 2.0)    # one folder: replaced, not added
        self.assertEqual([(k, e['account']) for k, e in folders.entries()], [(work, 'uuid-b')])
        self.assertEqual(folders.clear(via_link)['account'], 'uuid-b')
        self.assertEqual(folders.entries(), [])

    def test_another_unicode_form_is_the_same_folder(self):
        nfc = unicodedata.normalize('NFC', 'café')
        nfd = unicodedata.normalize('NFD', nfc)
        made = self.mk(nfd)
        folders.set_account(made, 'uuid-a', 'maya@fernhill.example', 1.0)
        self.assertEqual(folders.nearest(os.path.join(self.tmp, nfc))[0], unicodedata.normalize('NFC', made))
        self.assertEqual(folders.nearest(os.path.join(self.tmp, nfd))[0], unicodedata.normalize('NFC', made))

    def test_another_case_is_the_same_folder_only_when_the_disk_says_so(self):
        made = self.mk('Client')
        folders.set_account(made, 'uuid-a', 'maya@fernhill.example', 1.0)
        other = os.path.join(self.tmp, 'client')
        found = folders.nearest(other)
        if os.path.exists(other):                                       # a disk that ignores case (macOS's default)
            self.assertEqual(found[0], made)
        else:
            self.assertIsNone(found)
        shutil.rmtree(made)
        os.makedirs(os.path.join(self.tmp, 'cLIENT'))
        self.assertIsNone(folders.nearest(os.path.join(self.tmp, 'cLIENT')))   # a gone folder's entry never passes on


class ManyCases(Store):
    """Over many made-up trees: the entry found is always the deepest folder at or above, and with no entries there is
    never one (a run without NAME prints its usage line, as before)."""

    def test_the_nearest_folder_always_wins(self):
        rnd = random.Random(20261125)
        names = ['a', 'b', 'c']
        tree = ['']
        for _ in range(3):
            tree += [p + '/' + n if p else n for p in tree for n in names if (p + '/' + n if p else n) not in tree]
        for rel in tree:
            if rel:
                self.mk(*rel.split('/'))
        paths = [os.path.join(self.tmp, *rel.split('/')) if rel else self.tmp for rel in tree]
        for case in range(3000):
            chosen = rnd.sample(paths, rnd.randint(0, 6))
            found = dict((folders.key(p), {'account': 'acct-%d' % i, 'email': 'x%d@example.com' % i, 'at': 0})
                         for i, p in enumerate(chosen))
            where = rnd.choice(paths)
            got = folders.nearest(where, found=found)
            above = [k for k in found if where == k or where.startswith(k + os.sep)]
            want = max(above, key=len) if above else None
            self.assertEqual(got[0] if got else None, want, (case, where, sorted(found)))
            self.assertIsNone(folders.nearest(where, found={}))
            if case % 300 == 0:                                         # now and then through the file itself
                for k, e in found.items():
                    folders.set_account(k, e['account'], e['email'], 0)
                got = folders.nearest(where)
                self.assertEqual(got[0] if got else None, want)
                folders.remove([k for k, _ in folders.entries()])
                self.assertEqual(folders.entries(), [])


class Removal(unittest.TestCase):
    """api.remove takes the account's folders with it; a removal that is refused keeps them."""

    def setUp(self):
        self.rig = Rig()
        self.use = context.use(self.rig.ctx)
        self.use.__enter__()
        if os.path.exists(core.data_path(folders.FILE)):
            os.remove(core.data_path(folders.FILE))
        self.rig.two_accounts()                       # a (uuid-a) and b (uuid-b); Claude Code on b
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix='sbfolders-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def tearDown(self):
        self.use.__exit__(None, None, None)
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        self.rig.close()
        if os.path.exists(core.data_path(folders.FILE)):
            os.remove(core.data_path(folders.FILE))

    def test_removing_an_account_takes_its_folders(self):
        one, two, three = (os.path.join(self.tmp, n) for n in ('one', 'two', 'three'))
        for p in (one, two, three):
            os.makedirs(p)
        folders.set_account(one, 'uuid-a', 'a@example.com', 1.0)
        folders.set_account(two, 'uuid-b', 'b@example.com', 1.0)
        folders.set_account(three, 'uuid-a', 'a@example.com', 1.0)
        with self.assertRaises(api.AccountsError):
            api.remove('nobody@example.com')
        self.assertEqual(len(folders.entries()), 3)
        api.remove('a@example.com')
        self.assertEqual([(k, e['account']) for k, e in folders.entries()], [(two, 'uuid-b')])


if __name__ == '__main__':
    unittest.main()
