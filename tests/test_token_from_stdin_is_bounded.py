"""`switchboard add --token -` reads one line of standard input of a bounded length, and refuses a longer one plainly,
without repeating it and keeping nothing. A token or a key that fits is read as before. Made-up tokens only; the
engine is the real one on a made-up Mac (fakes_accounts.Rig)."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import contextlib
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from fakes_accounts import Rig
from switchboard import __main__ as cli
from switchboard import bearers
from switchboard.accounts import bearer, context, roster
from switchboard.core import SwitchError

TOKEN = 'sk-ant-oat01-madeUpOneYearToken-0501-aaaaaaaaaaaa'


class Endless(object):
    """Standard input that never ends a line: each read gives as much as it is asked for, and an unbounded read is a
    failure of the test (it would never return)."""

    def __init__(self):
        self.asked = []

    def readline(self, size=-1):
        self.asked.append(size)
        if size is None or size < 0:
            raise AssertionError('standard input was read with no length cap')
        return 'sk-ant-oat01-' + 'a' * size

    def isatty(self):
        return False


class FromStandardInput(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.rig.two_accounts()
        self.use = context.use(self.rig.ctx)
        self.use.__enter__()
        roster.forget_live()

    def tearDown(self):
        self.use.__exit__(None, None, None)
        self.rig.close()

    def run_cli(self, feed, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), mock.patch.object(sys, 'stdin', feed):
            try:
                code = cli.main(list(argv))
            except SwitchError as e:
                code = 'refused: %s' % e
        return code, out.getvalue(), err.getvalue()

    def test_a_line_longer_than_any_token_is_refused_plainly(self):
        feed = Endless()
        code, out, err = self.run_cli(feed, 'add', '--token', '-', '--label', 'night')
        self.assertEqual(code, 'refused: ' + bearers.TOO_LONG)
        self.assertEqual(feed.asked, [bearers.MOST + 2])
        self.assertNotIn('aaaa', out + err + code)
        self.assertEqual(bearer.records(self.rig.doc()), {})

    def test_a_long_piped_file_is_refused_the_same_way(self):
        feed = io.StringIO('sk-ant-api03-' + 'b' * 100000 + '\n')
        feed.isatty = lambda: False
        code, _, _ = self.run_cli(feed, 'add', '--token', '-', '--label', 'night')
        self.assertEqual(code, 'refused: ' + bearers.TOO_LONG)
        self.assertEqual(bearer.records(self.rig.doc()), {})

    def test_a_token_that_fits_is_kept_as_before(self):
        feed = io.StringIO(TOKEN + '\r\n')
        feed.isatty = lambda: False
        code, out, err = self.run_cli(feed, 'add', '--token', '-', '--label', 'night')
        self.assertEqual(code, 0, err)
        self.assertEqual([r['label'] for r in bearer.records(self.rig.doc()).values()], ['night'])
        self.assertGreater(bearers.MOST, len('sk-ant-oat') + 500)     # the longest shape bearer.kind_of takes


if __name__ == '__main__':
    unittest.main()
