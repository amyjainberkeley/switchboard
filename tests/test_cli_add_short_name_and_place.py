"""`switchboard add --as SHORT [--at N]`: the account kept in this run gets its short name and its place in one step.
When a run keeps several, the first one kept gets them, and the command says so. A name that is taken or looks like a
number or an email, and a place that does not exist, are refused before any sign-in starts. Made-up accounts only:
the engine is a stand-in (tests/fakes_accounts_api.py), and so are the sign-ins (Claude Code's own file, written as
/login writes it) and the running page (test_term.Page)."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import os
import unittest
from unittest import mock

from switchboard import __main__ as cli
from switchboard import core, more
from switchboard.accounts import api
from test_term import FakeTty, Page, Sandbox

A, B, C = 'ana@northwind.example', 'ana@fernhill.example', 'ana@harbor.example'
USAGE = ('switchboard add [--as SHORT] [--at N]\n'
         'switchboard add --token [-] [--for NAME | --label WORD] [--as SHORT]\n')


class Adding(Sandbox):
    def setUp(self):
        super().setUp()
        self.signed_in(A, 'org-a')
        check = mock.patch.object(api, 'check_alias', self.check_alias, create=True)
        check.start()
        self.addCleanup(check.stop)

    def check_alias(self, alias, keep_for=None):
        """The engine's rule (tests/test_accounts_short_name_check.py proves the real one), over the stand-in's list."""
        self.fake.calls.append(('check_alias', alias, keep_for))
        norm = (alias or '').strip().lower() or None
        if norm and (norm.isdigit() or '@' in norm or norm.startswith('-')):
            raise api.AccountsError('other', 'A short name is letters, digits, dots, dashes and underscores, not only '
                                             'digits, and does not start with a dash.')
        for r in self.fake.saved:
            if norm and r['id'] != keep_for and (r['alias'] or '') == norm:
                raise api.AccountsError('other', '"%s" is already the short name of %s.' % (norm, r['email']))
        return norm

    def logins(self, *who):
        """Each wait, the person signs in to the next account in Claude Code (/login), as it writes its own file."""
        queue = list(who)

        def sleep(seconds):
            if seconds == cli.POLL and queue:
                nxt = queue.pop(0)
                if isinstance(nxt, BaseException):
                    raise nxt
                self.signed_in(*nxt)
        p = mock.patch.object(cli, '_sleep', sleep)
        p.start()
        self.addCleanup(p.stop)

    def alias(self, email):
        return next(r['alias'] for r in self.fake.saved if r['email'] == email)

    def signs_in(self):
        return [c for c in self.fake.calls if c[0] == 'add_current']


class TheFirstAccountKept(Adding):
    def test_at_a_terminal_the_new_account_gets_its_name_and_place(self):
        self.fake.hold(B, 'org-b')
        self.fake.hold(C, 'org-c')
        self.logins(KeyboardInterrupt())                # the person stops at the next sign-in
        code, out = self.run_cli('add', '--as', 'work', '--at', '1', tty=FakeTty([]))
        self.assertEqual(code, 0, out)
        self.assertTrue(out.endswith('%s is work now, at 1 in the list.\n' % A), out)
        self.assertEqual(self.emails(), [A, B, C])
        self.assertEqual(self.alias(A), 'work')

    def test_when_a_run_keeps_several_the_first_gets_them_and_it_says_so(self):
        self.fake.hold(A, 'org-a')                      # Claude Code's account is kept already: not this run's
        self.logins((B, 'org-b'), (C, 'org-c'))
        code, out = self.run_cli('add', '--as', 'home', '--at', '1', tty=FakeTty(['y', 'n']))
        self.assertEqual(code, 0, out)
        self.assertTrue(out.endswith('%s, the first account kept, is home now, at 1 in the list.\n' % B), out)
        self.assertEqual(self.emails(), [B, A, C])
        self.assertEqual((self.alias(B), self.alias(C), self.alias(A)), ('home', None, None))

    def test_only_a_name_or_only_a_place(self):
        self.fake.hold(B, 'org-b')
        code, out = self.run_cli('add', '--at', '1')
        self.assertTrue(out.endswith('%s is at 1 in the list now.\n' % A), out)
        self.assertEqual(self.emails(), [A, B])
        self.signed_in(C, 'org-c')
        code, out = self.run_cli('add', '--as', 'lab')
        self.assertTrue(out.endswith('%s is lab now.\n' % C), out)
        self.assertEqual(self.emails(), [A, B, C])

    def test_without_a_terminal_the_account_claude_code_is_on_gets_them_kept_before_or_not(self):
        self.fake.hold(B, 'org-b')
        self.fake.hold(A, 'org-a')                      # the page kept it after /login, before the script ran
        code, out = self.run_cli('add', '--as', 'work', '--at', '1')
        self.assertEqual(code, 0, out)
        self.assertTrue(out.endswith('%s is work now, at 1 in the list.\n' % A), out)
        self.assertEqual((self.emails(), self.alias(A)), ([A, B], 'work'))
        changes = len(self.calls('set_alias', 'reorder'))
        code, again = self.run_cli('add', '--as', 'work', '--at', '1')                      # twice: the same
        self.assertEqual((code, again.splitlines()[-1]), (0, out.splitlines()[-1]))
        self.assertEqual(len(self.calls('set_alias', 'reorder')), changes)                   # and nothing changed

    def test_add_claude_takes_the_flags_too(self):
        code, out = self.run_cli('add', 'claude', '--as', 'work')
        self.assertTrue(out.endswith('%s is work now.\n' % A), out)

    def test_a_run_that_keeps_no_new_account_says_so(self):
        self.fake.hold(A, 'org-a')
        self.logins(KeyboardInterrupt())
        code, out = self.run_cli('add', '--as', 'home', tty=FakeTty([]))
        self.assertEqual(code, 0)
        self.assertTrue(out.endswith('No new account was kept, so --as and --at changed nothing.\n'), out)
        self.assertIsNone(self.alias(A))

    def test_through_the_running_page(self):
        ids = {}

        def on_post(path, body):
            if path == '/api/engine':
                ids[A] = self.fake.add_current().id
            elif path == '/api/move':
                more.move(ids[body['email']], body['to'])
        self.fake.hold(B, 'org-b')
        page = Page({'accounts': []}, on_post=on_post)
        self.addCleanup(page.stop)
        os.environ['SWITCHBOARD_PORT'] = str(page.server_address[1])
        code, out = self.run_cli('add', '--as', 'work', '--at', '1')
        self.assertEqual(code, 0, out)
        self.assertEqual([p[0] for p in page.posts], ['/api/engine', '/api/move'])
        self.assertEqual(page.posts[1][1], {'email': A, 'to': 1})
        self.assertEqual((self.emails(), self.alias(A)), ([A, B], 'work'))


class RefusedBeforeAnySignIn(Adding):
    def test_a_taken_name(self):
        self.fake.hold(B, 'org-b', alias='work')
        with self.assertRaises(core.SwitchError) as e:
            self.run_cli('add', '--as', 'work', tty=FakeTty([]))
        self.assertEqual(str(e.exception), '"work" is already the short name of %s.' % B)
        self.assertEqual(self.signs_in(), [])

    def test_a_name_held_by_the_account_claude_code_is_on_at_a_terminal(self):
        self.fake.hold(A, 'org-a', alias='work')        # at a terminal the next sign-in is the one named
        with self.assertRaises(core.SwitchError):
            self.run_cli('add', '--as', 'work', tty=FakeTty([]))
        self.assertEqual(self.signs_in(), [])

    def test_a_number_or_an_email(self):
        for word in ('12', 'ana@harbor.example'):
            with self.subTest(word=word), self.assertRaises(core.SwitchError) as e:
                self.run_cli('add', '--as', word, tty=FakeTty([]))
            self.assertIn('not only digits', str(e.exception))
        self.assertEqual(self.signs_in(), [])

    def test_a_place_that_does_not_exist(self):
        self.fake.hold(B, 'org-b')
        self.fake.hold(C, 'org-c')
        for n in ('0', '4'):
            with self.subTest(n=n):
                self.assertEqual(self.run_cli('add', '--at', n, tty=FakeTty([])),
                                 (1, 'There is no place %s: a new account can go at 1 to 3.\n  1  %s\n  2  %s\n'
                                  % (n, B, C)))
        self.fake.hold(A, 'org-a')                      # no terminal, and Claude Code's account is kept: 1 to 3
        self.assertEqual(self.run_cli('add', '--at', '4')[0], 1)
        self.assertEqual(self.signs_in(), [])

    def test_wrong_words_exit_2_with_the_form(self):
        for args in (('--as',), ('--at',), ('--at', 'first'), ('--as', 'a', '--as', 'b'), ('--yes',),
                     ('--as', 'a', 'extra'), ('--at', '-1')):
            with self.subTest(args=args):
                self.assertEqual(self.run_cli('add', *args), (2, USAGE))
        self.assertEqual(self.signs_in(), [])

    def test_the_demo_refuses_it(self):
        core.DEMO = True
        with self.assertRaises(core.SwitchError) as e:
            self.run_cli('add', '--as', 'work')
        self.assertIn('The demo has made-up accounts', str(e.exception))


if __name__ == '__main__':
    unittest.main()
