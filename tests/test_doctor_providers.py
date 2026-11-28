import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# switchboard doctor: one line per provider, from the board (providers/view.py, built from files only: nothing
# starts, no app is asked). The board is a made-up one here.
#
# - An added provider: ok, its version and its state, plan and windows (Claude: the accounts above).
# - On this Mac and not added: '--' and the line that adds it. Not on this Mac and not added: '--'.
# - Added and signed out, or added and gone from this Mac, or not read (its code failed): NO, with what to do.
# - A board that cannot be built: one '--' line, and doctor goes on.
import os
import sys
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))

from switchboard import __main__ as cli  # noqa: E402
from switchboard import demo  # noqa: E402
from switchboard.providers import view, words  # noqa: E402
import test_cli_commands  # noqa: E402  (its Cli: a throwaway home and the fake accounts; its own tests are not run here)


class DoctorProviders(test_cli_commands.Cli):
    def doctor(self, board):
        with mock.patch.object(cli, 'page_running', lambda: False), \
                mock.patch.object(cli.linkapp, 'status', lambda fresh=False: {'installed': False, 'on': False}), \
                mock.patch.object(cli.browser, 'present', lambda: False), \
                mock.patch.object(cli.connect, 'claude_bin', lambda: None), \
                mock.patch.object(view, 'view', board if callable(board) else (lambda *a, **k: board)):
            return self.run_cli('doctor')

    def test_claude_and_codex_with_codex_out(self):
        b = demo.providers(time.time(), out=True)
        code, out, _ = self.doctor(b)
        back = b['providers'][1]['back_at']
        self.assertIn(' ok  Claude 2.1.330: has room · the accounts above\n', out)
        self.assertIn(' ok  Codex 0.169.1: is out until %s · ChatGPT Pro · weekly 100%%\n'
                      % words.when(back, b['now']), out)
        # one line per provider, after the Claude Code line
        self.assertLess(out.index('Claude Code'), out.index(' ok  Claude 2.1.330:'))

    def test_on_this_mac_not_added_and_not_here(self):
        code, out, _ = self.doctor(demo.providers(time.time(), codex=False))
        self.assertIn(' --  Codex: on this Mac, not added\n       switchboard add codex adds it.\n', out)
        b = dict(demo.providers(time.time(), codex=False), add=[])
        code, out, _ = self.doctor(b)
        self.assertIn(' --  Codex: not on this Mac\n', out)

    def test_needs_a_look(self):
        b = demo.providers(time.time(), out=True)
        b['providers'][1].update(state='signed_out', seats=[], plan=None)
        code, out, _ = self.doctor(b)
        self.assertIn(' NO  Codex 0.169.1: not signed in\n       Sign in once in Codex itself: run codex login, or '
                      'open the ChatGPT app.\n', out)
        b['providers'][1].update(state='not_installed', version=None)
        code, out, _ = self.doctor(b)
        self.assertIn(" NO  Codex: not on this Mac\n       Install OpenAI's Codex again (its app or its command "
                      "line).\n", out)
        b['providers'][1].update(state='unknown', error='SyntaxError')
        code, out, _ = self.doctor(b)
        self.assertIn(' NO  Codex: Switchboard could not read it here (SyntaxError)\n       Run the installer again.\n',
                      out)

    def test_a_provider_the_list_has_no_row_for_is_never_ok(self):
        # The list, the menu and the page give it no row and one line that says why (view.under_list): doctor says
        # the same words, with the one step.
        b = demo.providers(time.time())
        codex = b['providers'][1]
        cases = (
            (dict(version='0.100.0', fit_why=words.STATE_TOO_OLD),
             ' --  Codex 0.100.0: older than the one that wrote your chats\n       The newest Codex here (0.100.0) '
             'is older than the one that wrote your Codex chats (0.169.1). Update Codex before carrying a chat on in '
             'it.\n'),
            (dict(version=None, fit_why=words.STATE_NO_COPY),
             " --  Codex: no copy here can be run\n       Install OpenAI's Codex again (its app or its command "
             "line).\n"),
            (dict(version='0.169.1', state='unknown', seats=[], plan=None, signed_in=None,
                  fit_why=words.STATE_SIGN_IN_NOT_SEEN),
             ' --  Codex 0.169.1: sign-in not seen here\n       Whether it is signed in shows when its numbers are '
             'first read (switchboard providers --ask).\n'),
            (dict(state='room', signed_in=False, fit_why=words.STATE_SIGNED_OUT),
             ' NO  Codex 0.169.1: not signed in\n       Sign in once in Codex itself: run codex login, or open the '
             'ChatGPT app.\n'))
        for change, said in cases:
            with self.subTest(fit_why=change['fit_why']):
                codex.update(change)
                self.assertEqual(view.under_list(b, b['now'])[0][2], change['fit_why'])    # what the faces say
                code, out, _ = self.doctor(b)
                self.assertIn(said, out)
                self.assertNotIn(' ok  Codex', out)

    def test_the_accounts_own_provider_keeps_its_line(self):
        # Its sign-in and its copy are its accounts', said on their rows and on the Claude Code line.
        b = demo.providers(time.time())
        b['providers'][0]['fit_why'] = words.STATE_TOO_OLD
        code, out, _ = self.doctor(b)
        self.assertIn(' ok  Claude 2.1.330: has room · the accounts above\n', out)

    def test_the_provider_the_accounts_serve_never_fails_doctor_on_its_own(self):
        b = demo.providers(time.time(), out=True)
        b['providers'][0].update(state='not_installed', version=None)
        code, out, _ = self.doctor(b)
        self.assertIn(' --  Claude: not on this Mac · the accounts above\n', out)

    def test_a_board_that_cannot_be_built(self):
        def broken(*a, **k):
            raise RuntimeError('made-up')
        code, out, _ = self.doctor(broken)
        self.assertIn(' --  Providers: not known just now\n', out)
        self.assertIn('Page at http://127.0.0.1:', out)              # doctor went on


if __name__ == '__main__':
    unittest.main()
