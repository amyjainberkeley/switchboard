import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# `switchboard export --check FILE`: it asks the passphrase and says whether the file is whole and what it holds, with
# the codes import uses for a file it can't use, and keeps nothing.
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard.accounts import vault
from test_backup_export import Mac, pass_file, read


class Check(unittest.TestCase):
    def setUp(self):
        self.one = Mac(self)
        self.one.rig.keep('uuid-a', 'maya@northwind.example', 'org-n')
        self.one.rig.keep('uuid-b', 'maya@fernhill.example', 'org-f')
        self.pf = pass_file(self.one.folder)
        self.dest = os.path.join(self.one.folder, 'accounts.sbx')
        code, _, _, err = self.one.run('export', self.dest, '--passphrase-file', self.pf)
        self.assertEqual(code, 0, err)
        self.other = Mac(self)

    def check(self, path, pf=None):
        return self.other.run('export', '--check', path, '--passphrase-file', pf or self.pf)

    def test_whole_with_what_it_holds_and_nothing_kept(self):
        writes = self.other.rig.kc.count('write')
        code, out, _, err = self.check(self.dest)
        self.assertEqual((code, err), (0, ''))
        self.assertRegex(out, r'^The file is whole and opens with that passphrase \(made \w+ \d+ \d+:\d\d [AP]M\)\. '
                              r'It holds 2 accounts:\n')
        self.assertIn('  maya@northwind.example, login saved ', out)
        self.assertIn('  maya@fernhill.example, login saved ', out)
        self.assertTrue(out.endswith('Nothing was kept.\n'), out)
        self.assertEqual(self.other.rig.kc.count('write'), writes)
        self.assertFalse(os.path.exists(vault.store_path(self.other.rig.ctx)))

    def test_each_file_it_cannot_use_has_its_code(self):
        data = read(self.dest)
        bad = os.path.join(self.other.folder, 'bad.sbx')

        def put(b):
            with open(bad, 'wb') as f:
                f.write(b)
        wrong = pass_file(self.other.folder, text='another passphrase')
        self.assertEqual(self.check(self.dest, wrong)[0], 3)
        i = data.index(b'\ntag ') - 5
        put(data[:i] + bytes([data[i] ^ 1]) + data[i + 1:])
        self.assertEqual(self.check(bad)[0], 4)
        put(data[:-10])
        code, _, _, err = self.check(bad)
        self.assertEqual(code, 5)
        self.assertTrue(err.startswith('This file is cut short'), err)

    def test_it_takes_no_other_words(self):
        code, _, _, err = self.other.run('export', '--check', self.dest, '--account', 'x')
        self.assertEqual(code, 2)
        self.assertIn('--check takes only the file', err)


if __name__ == '__main__':
    unittest.main()
