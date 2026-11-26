import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# --debug cannot be left on for the background. SWITCHBOARD_DEBUG is honoured only for a command a person runs at a
# terminal: the page (serve), the demo, the menu bar, the status line and hooks, `switchboard claude` and a run ignore
# it, and starting the page from the menu bar does not hand it on. (--debug typed on a command still counts: a person
# asked for it.) And the masking hides a secret glued to its flag (-wVALUE, --password=VALUE) and NAME_TOKEN=VALUE of
# any length, made-up secrets each.
import os
import unittest
from unittest import mock

from switchboard import debug, menubar

ON = {debug.VARIABLE: '1'}


class TheVariable(unittest.TestCase):
    def take(self, words, terminal=True, env=ON):
        with mock.patch.dict(os.environ, env), mock.patch.object(debug, 'at_terminal', lambda: terminal):
            return debug.take(list(words))[1]

    def test_a_command_a_person_runs_at_a_terminal(self):
        for words in (['status'], ['switch', 'work'], ['doctor'], ['config', 'get', 'awake']):
            self.assertTrue(self.take(words), words)

    def test_not_without_a_terminal(self):
        self.assertFalse(self.take(['status'], terminal=False))

    def test_never_for_what_runs_in_the_background_or_hands_the_terminal_on(self):
        for words in (['serve'], ['demo'], ['menubar'], ['statusline'], ['notify'], ['hooks'], ['claude', '-p', 'x'],
                      ['run', 'work'], ['resume', 'work'], ['awake', '--once'], []):
            with self.subTest(words=words):
                self.assertFalse(self.take(words), words)

    def test_typed_on_the_command_it_still_counts(self):
        self.assertTrue(self.take(['serve', '--debug'], terminal=False, env={debug.VARIABLE: '0'}))
        self.assertTrue(self.take(['status', '--debug'], terminal=False, env={debug.VARIABLE: '0'}))

    def test_starting_the_page_from_the_menu_bar_does_not_hand_it_on(self):
        seen = []
        with mock.patch.dict(os.environ, ON), mock.patch.object(menubar, 'agent_plist', lambda: '/nonexistent.plist'), \
                mock.patch.object(menubar.subprocess, 'Popen', lambda *a, **kw: seen.append(kw.get('env'))):
            self.assertEqual(menubar.start_page(), 'serve')
        self.assertEqual(len(seen), 1)
        self.assertNotIn(debug.VARIABLE, seen[0])
        self.assertIn('PATH', seen[0])


class TheMasking(unittest.TestCase):
    def said(self, text):
        out = []
        with mock.patch.object(debug.sys, 'stderr') as err:
            err.write = out.append
            debug.say(text)
        return ''.join(out)

    def test_a_secret_glued_to_its_flag(self):
        for args, secret in ((['security', 'add-generic-password', '-wMadeUp7'], 'MadeUp7'),
                             (['security', '-XAbCd01'], 'AbCd01'),
                             (['tool', '--password=hunter22'], 'hunter22'),
                             (['tool', '--token=tok3n'], 'tok3n'),
                             (['tool', '--api-key=k3y'], 'k3y')):
            with self.subTest(args=args):
                line = debug.program(args)
                self.assertNotIn(secret, line)
                self.assertIn(debug.HIDDEN, line)

    def test_flags_that_take_no_secret_are_left_as_they_are(self):
        line = debug.program(['/usr/bin/openssl', 'enc', '-pbkdf2', '-pass', 'fd:5', '-iter', '1'])
        self.assertEqual(line, '/usr/bin/openssl enc -pbkdf2 -pass fd:5 -iter 1')

    def test_a_named_secret_of_any_length(self):
        for text, secret in (('env ANTHROPIC_AUTH_TOKEN=ab1', 'ab1'),
                             ('CLAUDE_CODE_OAUTH_TOKEN=short9', 'short9'),
                             ('{"ANTHROPIC_AUTH_TOKEN":"x7y"}', 'x7y'),
                             ("MY_API_KEY: 'q1'", 'q1'),
                             ('SOME_SECRET=s3', 's3'),
                             ('db_password=pw1', 'pw1')):
            with self.subTest(text=text):
                line = self.said(text)
                self.assertNotIn(secret, line)
                self.assertIn(debug.HIDDEN, line)

    def test_words_that_only_end_like_a_name_stay(self):
        self.assertEqual(self.said('monkey=banana turkey: ok'), 'debug: monkey=banana turkey: ok\n')


if __name__ == '__main__':
    unittest.main()
