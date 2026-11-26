import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# What `readings` and `import` say. A wrong word after `give` is named, not `give`. `readings take`, `import -` and
# `export --check -` at a terminal with nothing piped in say what they are waiting for before they wait. --for 0 says
# what it needs. A reading not taken because this Mac's own is newer says so. The Keychain's answer when `import`
# could not keep an account is a sentence, the same one `leftovers` says.
import io
import json
import sys
import unittest
from unittest import mock

from switchboard import backup, leftovers, readings
from switchboard.accounts import vault
from test_backup_import import A, Base as ImportBase
from test_readings_carry import B, Base as ReadingsBase


class AtATerminal(io.BytesIO):
    """Standard input that is a terminal; what a person would paste, then Control-D."""

    def isatty(self):
        return True


def stdin_of(data, tty):
    fake = mock.Mock()
    fake.buffer = AtATerminal(data) if tty else io.BytesIO(data)
    fake.isatty = lambda: tty
    return fake


class Readings(ReadingsBase):
    def test_a_wrong_word_after_give_is_named(self):
        for args, word in ((['give', '--json'], '--json'), (['give', 'now'], 'now'), (['take', '--bogus'], '--bogus'),
                           (['bogus'], 'bogus')):
            with self.subTest(args=args):
                out, err = io.StringIO(), io.StringIO()
                self.assertEqual(readings.main(args, out=out, err=err), 2)
                self.assertEqual(err.getvalue(), 'Unknown word: %s.\n%s\n' % (word, readings.USAGE))
                self.assertEqual(out.getvalue(), '')

    def test_take_at_a_terminal_says_what_it_waits_for(self):
        doc = json.dumps(self.given(self.item(B, self.now - 60))).encode()
        for args in (['take'], ['take', '-']):
            with self.subTest(args=args):
                out, err = io.StringIO(), io.StringIO()
                with mock.patch.object(sys, 'stdin', stdin_of(doc, True)):
                    self.assertEqual(readings.main(args, out=out, err=err), 0)
                self.assertEqual(err.getvalue(), readings.WAITING + '\n')
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, 'stdin', stdin_of(doc, False)):              # piped in: nothing more is said
            self.assertEqual(readings.main(['take', '-'], out=out, err=err), 0)
        self.assertEqual(err.getvalue(), '')
        self.assertIn('Control-D', readings.WAITING)
        self.assertIn('switchboard readings give', readings.WAITING)

    def test_for_zero_says_what_it_needs(self):
        out, err = io.StringIO(), io.StringIO()
        self.assertEqual(readings.main(['take', '-', '--for', '0'], out=out, err=err), 2)
        self.assertTrue(err.getvalue().startswith('--for needs a time above zero, like --for 10m.\n'), err.getvalue())

    def test_a_reading_not_newer_than_the_one_here_says_so(self):
        self.own(B, self.now - 30, five=10, week=20)
        said = dict(readings.take(self.given(self.item(B, self.now - 600))))
        self.assertEqual(said[B], 'not taken: its own numbers here are newer')


class Import(ImportBase):
    def test_import_and_check_at_a_terminal_say_what_they_wait_for(self):
        dest = self.export('--account', A)
        with open(dest, 'rb') as f:
            data = f.read()
        with mock.patch.object(sys, 'stdin', stdin_of(data, True)):
            code, out, err = self.take('-')
        self.assertEqual(code, 0, err)
        self.assertEqual(err, backup.WAITING + '\n')
        with mock.patch.object(sys, 'stdin', stdin_of(data, True)):
            code, out, _, err = self.two.run('export', '--check', '-', '--passphrase-file', self.pf_two)
        self.assertEqual(code, 0, err)
        self.assertEqual(err, backup.WAITING + '\n')
        with mock.patch.object(sys, 'stdin', stdin_of(data, False)):
            code, out, err = self.take('-')
        self.assertEqual(err, '')
        self.assertIn('Control-D', backup.WAITING)

    def test_the_keychains_answer_is_a_sentence(self):
        dest = self.export('--account', A)
        self.two.rig.kc.rule('write', service='Switchboard login', why='locked', times=-1)
        code, out, err = self.take(dest)
        self.assertEqual(code, 1, err)
        self.assertIn('Could not keep %s: the Keychain is locked. Unlock the Mac, then try again.\n' % A, out)
        self.assertNotIn(self.ids[A], vault.load(self.two.rig.ctx)['accounts'])
        for why in ('locked', 'timeout', 'busy', 'dialog', 'denied'):
            self.assertEqual(leftovers.keychain_said(why), leftovers._KEYCHAIN[why])
        self.assertEqual(leftovers.keychain_said('verify'), "the Keychain didn't keep it (it answered verify)")


if __name__ == '__main__':
    unittest.main()
