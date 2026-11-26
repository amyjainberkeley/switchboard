import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# `switchboard doctor` says when sign-ins end, in the page's words (term.signin_words): one line for each account whose
# sign-in ends within the week (ok, with why once: Anthropic ends each sign-in on a set date, and renewing does not
# move it), and one for each whose sign-in has ended (NO, with how to sign in again: Sign in to all, or `switchboard
# signin`). The account Claude Code is on is said like any other, with no "/login" line. Nothing for a date far off,
# not known, or for a login refused before its date.
#
# The engine is tests/fakes_accounts_api.py (test_cli_commands' Cli); the page is not running. Made-up accounts only.
import os
import sys
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import __main__ as cli
from switchboard import term
import test_cli_commands  # its Cli: a throwaway home and the fake accounts (its own tests are not run from here)

D = 86400
A, B, C, E, F, G = ('ana@work.example', 'ana@home.example', 'bo@lab.example', 'cy@old.example', 'di@far.example',
                    'ed@struck.example')
WHY = '       ' + term.SIGNIN_WHY + '\n'


class DoctorSignIns(test_cli_commands.Cli):
    def doctor(self):
        with mock.patch.object(cli, 'page_running', lambda: False), \
                mock.patch.object(cli.linkapp, 'status', lambda fresh=False: {'installed': False, 'on': False}), \
                mock.patch.object(cli.browser, 'present', lambda: False), \
                mock.patch.object(cli.connect, 'claude_bin', lambda: None):
            return self.run_cli('doctor')

    def words(self, at, need=False, active=False):
        return term.signin_line({'signin_ends_at': at, 'needs_login': need, 'active': active}, time.time())

    def test_each_sign_in_about_to_end_is_ok_with_why_once(self):
        now = time.time()
        self.fake.hold(A, 'org-w', refresh_expires_at=now + 5 * D)
        self.fake.hold(B, status='expiring', refresh_expires_at=now + 3 * D)
        self.fake.hold(C, status='expiring', refresh_expires_at=now + 2 * D + 600)
        self.fake.hold(F, refresh_expires_at=now + 20 * D)
        self.fake.sign_in(A, 'org-w')
        code, out, _ = self.doctor()
        # in the accounts' order, why after the first
        self.assertIn(' ok  %s: %s\n%s ok  %s: %s\n ok  %s: %s\n'
                      % (A, self.words(now + 5 * D), WHY, B, self.words(now + 3 * D), C, self.words(now + 2 * D + 600)),
                      out)
        self.assertEqual(out.count(term.SIGNIN_WHY), 1)
        self.assertNotIn('%s: Sign-in' % F, out)                          # far off: nothing
        self.assertNotIn('/login', out)

    def test_one_that_ended_needs_a_look_and_says_how_to_sign_in_again(self):
        now = time.time()
        self.fake.hold(A, 'org-w')
        self.fake.hold(E, status='needs_login', refresh_expires_at=now - D - 600)
        self.fake.hold(G, status='needs_login', refresh_expires_at=now + 2 * D)        # refused before its date
        self.fake.sign_in(A, 'org-w')
        code, out, _ = self.doctor()
        self.assertIn(' NO  %s: %s\n       Press Sign in to all on the page, or run switchboard signin.\n'
                      % (E, self.words(now - D - 600, True)), out)
        self.assertTrue(self.words(now - D - 600, True).startswith('Sign-in ended by yesterday '))
        self.assertNotIn('%s: Sign-in' % G, out)
        self.assertNotIn(term.SIGNIN_WHY, out)
        self.assertEqual(code, 1)
        self.assertTrue(out.endswith('Something above needs a look.\n'))

    def test_a_date_that_is_not_a_sane_time_says_nothing_and_breaks_nothing(self):
        self.fake.hold(A, 'org-w', refresh_expires_at=-9e10)
        self.fake.hold(E, status='needs_login', refresh_expires_at=-9e10)
        self.fake.hold(F, refresh_expires_at=5e11)
        self.fake.sign_in(A, 'org-w')
        code, out, err = self.doctor()
        self.assertEqual(err, '')
        self.assertIn('Accounts: 3 kept', out)
        self.assertNotIn('Sign-in', out)

    def test_nothing_when_no_sign_in_is_near_its_date(self):
        now = time.time()
        self.fake.hold(A, 'org-w', refresh_expires_at=now + 20 * D)
        self.fake.hold(B)
        self.fake.sign_in(A, 'org-w')
        _, out, _ = self.doctor()
        self.assertNotIn('Sign-in', out)


if __name__ == '__main__':
    unittest.main()
