import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""The page's start brings the session hooks up to date.

Hooks put in by an older version listen only to the artifact and Claude Docs tools. At the page's start they are
brought up to date, so they hear every claude.ai connector, without the person running a command; hooks that are not
in are never put in; and nothing in this can stop the page from starting."""
import unittest
from unittest import mock

from switchboard import server, session_hooks


class TheStart(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.logged = []
        for target, name, fn in ((session_hooks, 'precompile', lambda *a, **k: self.calls.append('precompile')),
                                 (server, 'log', lambda text, *a, **k: self.logged.append(text))):
            p = mock.patch.object(target, name, fn)
            p.start()
            self.addCleanup(p.stop)

    def hooks(self, there, upgraded=False, boom=None):
        def upgrade(*a, **k):
            self.calls.append('upgrade')
            if boom:
                raise boom
            return upgraded
        for name, fn in (('status', lambda *a, **k: {'in': there}), ('upgrade', upgrade)):
            p = mock.patch.object(session_hooks, name, fn)
            p.start()
            self.addCleanup(p.stop)

    def test_hooks_that_are_in_are_brought_up_to_date_then_compiled(self):
        self.hooks(True, upgraded=True)
        server.start_session_hooks()
        self.assertEqual(self.calls, ['upgrade', 'precompile'])
        self.assertEqual(self.logged, ['the session hooks were brought up to date'])

    def test_hooks_already_current_are_only_compiled_and_nothing_is_said(self):
        self.hooks(True, upgraded=False)
        server.start_session_hooks()
        self.assertEqual(self.calls, ['upgrade', 'precompile'])
        self.assertEqual(self.logged, [])

    def test_hooks_that_are_not_in_are_left_alone(self):
        for there in (False, None):                      # not in; or the settings file can't be read
            self.calls[:] = []
            self.hooks(there)
            server.start_session_hooks()
            self.assertEqual(self.calls, [], there)

    def test_a_failure_is_noted_and_the_page_still_starts(self):
        self.hooks(True, boom=OSError('settings went away'))
        server.start_session_hooks()                      # does not raise
        self.assertEqual(self.calls, ['upgrade'])
        self.assertEqual(len(self.logged), 1)
        self.assertIn('not made ready', self.logged[0])


if __name__ == '__main__':
    unittest.main()
