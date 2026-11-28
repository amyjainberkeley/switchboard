import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# "Keep Claude Code signed in" can be found and turned off: `switchboard auto signed-in [on|off]` sets and clears it
# (settings.json, where the loop reads it at every pass) and notes the change in the history; a word it does not
# know is refused with its usage line and changes nothing; `switchboard doctor` says how to turn it off, or on.
# A throwaway home and data folder; nothing answers on the port; no engine is asked.
import io
import os
import shutil
import socket
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import __main__ as cli  # noqa: E402
from switchboard import core, engine, history  # noqa: E402
from switchboard.accounts import autoloop  # noqa: E402

ON = ('Keep Claude Code signed in: on (moves it to a signed-in account when its sign-in is lost or ends, also with '
      'automatic switching off)')
TURN_OFF = 'Turn it off with `switchboard auto signed-in off`.'
TURN_ON = 'Turn it on with `switchboard auto signed-in on`.'


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-stay-command-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        home = os.path.join(self.tmp, 'home')
        os.makedirs(os.path.join(home, '.claude'))
        env = mock.patch.dict(os.environ, {'HOME': home, 'SWITCHBOARD_HOME': os.path.join(self.tmp, 'data'),
                                           'CLAUDE_CONFIG_DIR': os.path.join(home, '.claude'),
                                           'SWITCHBOARD_PORT': str(free_port()), 'NO_COLOR': '1'})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = False

    def run_cli(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(list(args))
        return code, out.getvalue()


class TheCommand(Case):
    def test_off_then_on_and_each_is_in_the_history_as_yours(self):
        self.assertTrue(autoloop.load_settings().stay_signed_in)            # on until it is turned off
        self.assertEqual(self.run_cli('auto', 'signed-in', 'off'),
                         (0, 'Keep Claude Code signed in: off\n%s\n' % TURN_ON))
        self.assertFalse(autoloop.load_settings().stay_signed_in)
        self.assertIs(core.settings()[autoloop.POLICY_KEY]['stay_signed_in'], False)
        self.assertEqual(self.run_cli('auto', 'signed-in'), (0, 'Keep Claude Code signed in: off\n%s\n' % TURN_ON))
        self.assertEqual(self.run_cli('auto', 'signed-in', 'on'), (0, '%s\n%s\n' % (ON, TURN_OFF)))
        self.assertTrue(autoloop.load_settings().stay_signed_in)
        said = [(r['kind'], r['text'], r.get('by')) for r in history.read()]
        self.assertEqual(said, [('setting', 'Keep Claude Code signed in: off', 'you'),
                                ('setting', 'Keep Claude Code signed in: on', 'you')])

    def test_automatic_switching_is_left_as_it_is(self):
        autoloop.save_settings(enabled=True)
        self.run_cli('auto', 'signed-in', 'off')
        self.assertTrue(autoloop.load_settings().enabled)
        self.assertFalse(autoloop.load_settings().stay_signed_in)

    def test_a_word_it_does_not_know_is_refused_with_the_usage_line(self):
        autoloop.save_settings(stay_signed_in=False)
        for words in (['maybe'], ['On'], ['off', 'now'], ['--yes']):
            with self.subTest(words=words):
                self.assertEqual(self.run_cli('auto', 'signed-in', *words),
                                 (2, 'switchboard auto signed-in [on|off]\n'))
                self.assertFalse(autoloop.load_settings().stay_signed_in)
        self.assertEqual(history.read(), [])

    def test_the_help_names_it(self):
        code, out = self.run_cli('auto', '--help')
        self.assertEqual(code, 0)
        self.assertIn('switchboard auto signed-in [on|off]\n', out)
        with open(os.path.join(HERE, '..', 'docs', 'commands.md'), encoding='utf-8') as f:
            self.assertIn('| `switchboard auto signed-in` |', f.read())


class TheDoctorLine(Case):
    def doctor(self):
        """_doctor_auto's lines as `switchboard doctor` prints them (its `line`, word for word)."""
        out = io.StringIO()

        def line(good, text, fix=''):
            print(' %s  %s%s' % ('ok' if good else '--' if good is None else 'NO', text,
                                 '\n       ' + fix if fix and not good else ''))
        with mock.patch.object(engine, 'auto_settings', lambda: {'enabled': False}), redirect_stdout(out):
            cli._doctor_auto(line, 0)
        return out.getvalue().splitlines()[-2:]

    def test_on_says_how_to_turn_it_off(self):
        self.assertEqual(self.doctor(), [' ok  ' + ON, '       ' + TURN_OFF])

    def test_off_says_how_to_turn_it_on(self):
        autoloop.save_settings(stay_signed_in=False)
        self.assertEqual(self.doctor(), [' --  Keep Claude Code signed in: off', '       ' + TURN_ON])


if __name__ == '__main__':
    unittest.main()
