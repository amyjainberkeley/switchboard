import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# `switchboard signin`: the page's Sign in to all, from a terminal. It reuses the page's own queue through the running
# page (POST /api/signins), never a second one of its own: it starts it, says where it is in the page's words until it
# ends, then the page's closing line; Ctrl-C asks the page to stop. With no page running it starts nothing and says so.
#
# The page is a stand-in (ask_page and page_state); nothing runs. Made-up accounts only.
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import __main__ as cli
import test_cli_commands  # its Cli: a throwaway home and the fake accounts (its own tests are not run from here)

A, B = 'ana@work.example', 'bo@home.example'


def running(current, done, of=2, waiting=False):
    return {'state': 'running', 'current': current, 'done': done, 'of': of, 'waiting': waiting}


class SignInToAll(test_cli_commands.Cli):
    def page(self, answers, states):
        """The page: each POST answered from `answers` (by its 'do'), each look at its state from `states` in turn."""
        self.posts = []

        def ask(path, body, timeout=120, here=True):      # here: ask_page's own (False: nothing is made here)
            self.assertFalse(here)
            self.posts.append((path, body))
            return answers[body['do']]
        looks = iter(states)
        return (mock.patch.object(cli, 'ask_page', ask),
                mock.patch.object(cli, 'page_state', lambda timeout=5: next(looks)),
                mock.patch.object(cli, '_sleep', lambda s: None))

    def run_with(self, answers, states, *args):
        a, b, c = self.page(answers, states)
        with a, b, c:
            return self.run_cli('signin', *args)

    def test_it_starts_the_pages_queue_and_says_where_it_is_until_it_ends(self):
        line = 'Signed in again to all 2 accounts.'
        code, out, err = self.run_with(
            {'start': {'signins': running(A, 0), 'line': None}},
            [{'accounts': [], 'signins': running(A, 0)}, {'accounts': [], 'signins': running(A, 0, waiting=True)},
             {'accounts': [], 'signins': running(B, 1)},
             {'accounts': [], 'signins': {'state': 'ended', 'line': line, 'skipped': []}}])
        self.assertEqual(self.posts, [('/api/signins', {'do': 'start'})])          # the page's own queue, once
        self.assertEqual(out, 'Signing in to %s: approve it in the tab that opened. 0 of 2 done.\n'
                              'Keeping the new sign-in of %s. 0 of 2 done.\n'
                              'Signing in to %s: approve it in the tab that opened. 1 of 2 done.\n'
                              '%s\n' % (A, A, B, line))
        self.assertEqual((code, err), (0, ''))

    def test_one_not_signed_in_is_named_and_the_exit_says_so(self):
        line = 'Signed in again to 1 of 2. Not signed in: %s (it was not approved in time).' % B
        code, out, _ = self.run_with({'start': {'signins': running(A, 0)}},
                                     [{'accounts': [], 'signins': {'state': 'ended', 'line': line,
                                                                   'skipped': [{'email': B, 'why': 'x'}]}}])
        self.assertTrue(out.endswith(line + '\n'))
        self.assertEqual(code, 1)

    def test_with_nothing_to_do_it_says_so(self):
        code, out, _ = self.run_with({'start': {'signins': {'state': 'idle', 'due': []},
                                                'line': 'No account needs a sign-in now.'}}, [])
        self.assertEqual((code, out), (0, 'No account needs a sign-in now.\n'))

    def test_ctrl_c_asks_the_page_to_stop(self):
        a, _, c = self.page({'start': {'signins': running(A, 0)}, 'stop': {'signins': running(A, 0)}}, [])
        looks = mock.Mock(side_effect=[{'accounts': [], 'signins': running(A, 0)}, KeyboardInterrupt])
        with a, mock.patch.object(cli, 'page_state', looks), c:
            code, out, _ = self.run_cli('signin')
        self.assertEqual(self.posts, [('/api/signins', {'do': 'start'}), ('/api/signins', {'do': 'stop'})])
        self.assertTrue(out.endswith('Stopped. What was signed in stays signed in.\n'), out)
        self.assertEqual(code, 1)

    def test_without_the_page_it_starts_nothing(self):
        def refused(path, body, timeout=120, here=True):
            raise ConnectionRefusedError(61, 'refused')
        with mock.patch.object(cli, 'ask_page', refused):
            code, out, _ = self.run_cli('signin')
        self.assertEqual(code, 1)
        self.assertIn('The page is not running, and Sign in to all runs there.', out)
        self.assertEqual(self.fake.calls, [c for c in self.fake.calls if c[0] not in ('add_login', 'add_current')])

    def test_its_help_names_the_page(self):
        self.assertIn('Sign in to all', cli.command_help('signin'))
        self.assertIn('the page must be running', cli.command_help('signin'))


if __name__ == '__main__':
    unittest.main()
