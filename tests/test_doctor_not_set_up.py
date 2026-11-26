"""doctor on a Mac where nothing is set up (no account kept) does not end "All good.": it ends with the first screen's
own sentence for running switchboard setup, and still exits 0."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import io
import os
import unittest
from contextlib import redirect_stdout
from unittest import mock

from fakes_accounts_api import install as fake_engine
from switchboard import __main__ as cli
from switchboard import browser, connect, engine, lanes, linkapp


class DoctorNotSetUp(unittest.TestCase):
    def setUp(self):
        self.fake = fake_engine(self)

    def doctor(self, claude):
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(linkapp, 'status', lambda fresh=False: {'installed': False, 'on': False}), \
                mock.patch.object(browser, 'present', lambda: False), mock.patch.object(cli, 'page_running', lambda: False), \
                mock.patch.object(cli, 'page_status', lambda: 'down'), mock.patch.object(engine, 'foreign_switcher', lambda: None), \
                mock.patch.object(connect, 'claude_bin', lambda: claude), mock.patch.object(lanes, 'answers', lambda *a, **k: False), \
                mock.patch.object(cli, 'auth_said', lambda after_switch=False: None, create=True), \
                mock.patch.dict(os.environ, {'PATH': '/usr/bin:/bin'}):
            code = cli.doctor()
        return code, out.getvalue()

    def test_nothing_kept_ends_with_the_first_screens_setup_line(self):
        code, text = self.doctor(None)
        self.assertNotIn('\n NO ', text, text)
        self.assertIn('Accounts: 0 kept', text)
        self.assertEqual(code, 0)
        self.assertNotIn('All good.', text)
        last = text.rstrip('\n').split('\n')[-1]
        with mock.patch.object(connect, 'claude_bin', lambda: None):
            first_screen = cli._closing({}, {'accounts': [], 'head': {'email': None}})[0]
        self.assertEqual(last, first_screen)
        self.assertEqual(last, 'Install Claude Code first (https://claude.com/claude-code), then run switchboard setup.')

    def test_the_line_follows_what_the_first_screen_would_say(self):
        with mock.patch.object(connect, 'claude_bin', lambda: '/made/up/claude'):
            with mock.patch.object(cli, 'identity', lambda: None):
                self.assertEqual(cli.not_set_up(), 'Sign in to Claude Code first, then run switchboard setup.')
            with mock.patch.object(cli, 'identity', lambda: ('ana@work.example', 'org')):
                self.assertEqual(cli.not_set_up(), 'Add it with switchboard setup.')

    def test_an_account_kept_still_ends_all_good(self):
        self.fake.hold('ana@work.example', org_id='org-work')
        code, text = self.doctor('/made/up/claude')
        self.assertNotIn('\n NO ', text, text)
        self.assertEqual((code, text.rstrip('\n').split('\n')[-1]), (0, 'All good.'))


if __name__ == '__main__':
    unittest.main()
