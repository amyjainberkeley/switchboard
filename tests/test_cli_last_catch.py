"""The command line's last catch: anything that is not Switchboard's own refusal ends in one sentence, with the
traceback in the log (redacted, as every log line is), and a pipe closed by the reader (`switchboard status | head`)
ends quietly with a closed pipe's usual status. KeyboardInterrupt and SystemExit go on as before."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import os
import runpy
import signal
import sys
import tempfile
import unittest
import warnings
from unittest import mock

from switchboard import core

SENTENCE = ('Something went wrong: %s. `switchboard doctor` checks every part; the details are in '
            '~/.switchboard/switchboard.log.')


class LastCatch(unittest.TestCase):
    def setUp(self):
        self.data = os.path.join(tempfile.mkdtemp(prefix='switchboard-catch-'), 'data')
        env = mock.patch.dict(os.environ, {'SWITCHBOARD_HOME': self.data})
        env.start()
        self.addCleanup(env.stop)
        out = tempfile.TemporaryFile('w+')
        self.addCleanup(out.close)
        self.out = out

    def run_entry(self, raised):
        """`switchboard version`, run through the module's own entry point, with version_text raising `raised`."""
        def boom():
            raise raised
        with mock.patch.object(sys, 'argv', ['switchboard', 'version']), mock.patch.object(core, 'version_text', boom), \
                mock.patch.object(sys, 'stdout', self.out), warnings.catch_warnings():
            warnings.simplefilter('ignore', RuntimeWarning)          # runpy: the module is already imported
            with self.assertRaises(SystemExit) as e:
                runpy.run_module('switchboard.__main__', run_name='__main__')
        return e.exception.code

    def log(self):
        try:
            with open(os.path.join(self.data, 'switchboard.log'), encoding='utf-8') as f:
                return f.read()
        except OSError:
            return ''

    def test_anything_else_is_one_sentence_and_the_traceback_goes_to_the_log(self):
        key = 'sk-ant-api03-' + 'A1b2C3d4' * 6
        code = self.run_entry(OSError(30, 'Read-only file system: %s' % key))
        self.assertEqual(code, SENTENCE % 'OSError')
        log = self.log()
        self.assertIn('Traceback', log)
        self.assertIn('OSError', log)
        self.assertNotIn(key, log)
        self.assertIn('[hidden]', log)

    def test_in_the_demo_the_sentence_names_no_log_since_none_is_written(self):
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = True
        code = self.run_entry(OSError(30, 'Read-only file system'))
        self.assertEqual(code, 'Something went wrong: OSError. `switchboard doctor` checks every part.')
        self.assertEqual(self.log(), '')

    def test_a_closed_pipe_ends_quietly(self):
        code = self.run_entry(BrokenPipeError(32, 'Broken pipe'))
        self.assertEqual(code, 128 + signal.SIGPIPE)
        self.assertEqual(self.log(), '')

    def test_switchboards_own_refusal_and_ctrl_c_are_as_before(self):
        self.assertEqual(self.run_entry(core.SwitchError('No such account.')), 'No such account.')
        self.assertEqual(self.run_entry(KeyboardInterrupt()), 0)
        self.assertEqual(self.run_entry(SystemExit(3)), 3)
        self.assertEqual(self.log(), '')


if __name__ == '__main__':
    unittest.main()
