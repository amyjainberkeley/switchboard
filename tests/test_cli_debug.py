import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# `--debug` on any command (before or after the command word) and SWITCHBOARD_DEBUG=1: on standard error, what the
# command did: each file it read or wrote, each program it started (secrets masked), each call to the running page
# or the network (method, host, path, status, how long), the Keychain's calls, and which side did the work. Every
# line goes through core.redact, emails too. Off, a command's output is what it is without it, byte for byte, and
# nothing is hooked. Words after `--` (run, resume, hand) and after `switchboard claude` are not Switchboard's.
import builtins
import os
import re
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import __main__ as cli
from switchboard import core, debug
from switchboard.accounts import keychain, net
from test_cli_commands import Cli, A, B
from test_cli_with_page import FakePage

TOKEN = 'sk-ant-oat01-' + 'Zq7' * 12                     # made up, shaped like a login
KEY = 'sk-ant-api03-' + 'Kx9' * 12                       # made up, shaped like an API key
JWT = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4eXoifQ.c2lnbmF0dXJlc2lnbg'
PASSWORD = 'correct horse battery'                        # what follows -w for `security`: never shown, whatever it is
LONG = 'a1B2c3D4' * 6                                     # 48 key-like characters
SECRETS = (TOKEN, KEY, JWT, 'correct horse', LONG, 'refresh_token=', A, B, '@')


class Debug(Cli):
    def lines(self, err):
        got = [x for x in err.splitlines() if x]
        self.assertTrue(got, 'nothing on standard error')
        for x in got:
            self.assertTrue(x.startswith('debug: '), x)
        return got

    def clean(self, err):
        for s in SECRETS:
            self.assertNotIn(s, err)


class OnAndOff(Debug):
    def test_before_or_after_the_command_word_and_by_the_variable_the_output_is_the_same(self):
        self.two()
        plain = self.run_cli('status')
        self.assertEqual(plain[2], '')
        for args, env in ((('status', '--debug'), {}), (('--debug', 'status'), {}),
                          (('status',), {'SWITCHBOARD_DEBUG': '1'})):
            with self.subTest(args=args, env=env), mock.patch.dict(os.environ, env), \
                    mock.patch.object(debug, 'at_terminal', lambda: True):      # the variable needs a terminal
                code, out, err = self.run_cli(*args)
                self.assertEqual((code, out), plain[:2])
                lines = self.lines(err)
                self.assertTrue(any(x.startswith('debug: read ') for x in lines), lines)
                self.assertIn('debug: this command did the work.', lines)
                self.clean(err)

    def test_off_nothing_is_said_and_nothing_is_hooked(self):
        self.two()
        before = (builtins.open, os.open, subprocess.Popen, core.write_bytes, cli._page_open, keychain.Keychain._run,
                  net.Http.request, urllib.request.OpenerDirector.open)
        with mock.patch.object(debug, 'say', lambda *a: self.fail('a debug line while it is off')), \
                mock.patch.dict(os.environ, {'SWITCHBOARD_DEBUG': '0'}):
            for args in (('status',), ('status', '--json'), ('config',), ('config', 'get', 'awake'), ('switch', B),
                         ('switch', A), ('current',)):
                with self.subTest(args=args):
                    self.assertEqual(self.run_cli(*args)[2], '')
                    self.assertEqual((builtins.open, os.open, subprocess.Popen, core.write_bytes, cli._page_open,
                                      keychain.Keychain._run, net.Http.request, urllib.request.OpenerDirector.open),
                                     before)

    def test_on_only_standard_error_changes_and_every_hook_is_taken_out_after(self):
        self.two()
        before = (builtins.open, os.open, subprocess.Popen, core.write_bytes, cli._page_open, keychain.Keychain._run,
                  net.Http.request, urllib.request.OpenerDirector.open)
        for args in (('status',), ('config',), ('config', 'get', 'awake'), ('current',)):
            with self.subTest(args=args):
                plain = self.run_cli(*args)
                code, out, err = self.run_cli(*args + ('--debug',))
                self.assertEqual((code, out), plain[:2])
                self.lines(err)
        for to in (B, A):
            plain_code, plain_out, _ = self.run_cli('switch', to, '--debug')
            self.assertEqual((plain_code, plain_out), (0, 'Claude Code is on %s.\n' % to))
        self.assertEqual((builtins.open, os.open, subprocess.Popen, core.write_bytes, cli._page_open,
                          keychain.Keychain._run, net.Http.request, urllib.request.OpenerDirector.open), before)
        self.assertFalse(debug.STATE['on'])

    def test_words_after_the_double_dash_and_after_claude_are_not_ours(self):
        seen = []
        with mock.patch.object(cli, 'run_command', lambda args: seen.append(args) or 0), \
                mock.patch.object(cli, 'claude_on_keys', lambda args: seen.append(args) or 0):
            self.run_cli('run', 'work', '--', '--debug')
            self.run_cli('claude', '--debug')
            self.run_cli('--debug', 'run', 'work', '--debug', '--', '-p', '--debug')
        self.assertEqual(seen, [['work', '--', '--debug'], ['--debug'], ['work', '--', '-p', '--debug']])


class WhatItSays(Debug):
    def test_a_switch_here_names_the_page_that_did_not_answer_and_the_files_it_wrote(self):
        self.two()
        code, out, err = self.run_cli('switch', B, '--debug')
        self.assertEqual(code, 0)
        lines = self.lines(err)
        self.assertTrue(any(x.startswith('debug: page: POST /api/switch: no answer') for x in lines), lines)
        self.assertTrue(any(x.startswith('debug: wrote ') and 'history' in x for x in lines), lines)
        self.assertIn('debug: the page did not answer, so this command did the work.', lines)
        self.clean(err)

    def test_through_the_running_page(self):
        self.two()
        view = cli.local_view()
        FakePage(self, {('GET', '/api/state'): (200, view), ('POST', '/api/switch'): (200, {'note': None})})
        code, out, err = self.run_cli('--debug', 'switch', B)
        self.assertEqual(code, 0)
        lines = self.lines(err)
        self.assertTrue(any(x.startswith('debug: page: GET /api/state -> 200 (') for x in lines), lines)
        self.assertTrue(any(x.startswith('debug: page: POST /api/switch -> 200 (') for x in lines), lines)
        self.assertIn('debug: the running page did the work.', lines)

    def test_a_refusal_still_ends_with_what_was_done(self):
        self.two()
        code, out, err = self.run_cli('switch', 'nobody', '--debug')
        self.assertTrue(str(code).startswith('refused: '), code)
        self.assertTrue(self.lines(err))


class NoSecretGetsOut(Debug):
    """Made-up secrets through every hook: a program's arguments, a file's name, a page path and a network address
    with a token in them, the Keychain's own call. None of them, and no email, reaches standard error."""

    def test_every_hook(self):
        self.two()
        folder = tempfile.mkdtemp(dir=os.environ['SWITCHBOARD_HOME'] if os.path.isdir(os.environ['SWITCHBOARD_HOME'])
                                  else None)
        page = FakePage(self, {('POST', '/api/x'): (200, {})})
        seen = {}
        real_request = net.Http.request
        real_run = keychain.Keychain._run

        def network(self_, method, url, headers=None, body=None, timeout_s=10.0):
            seen['net'] = url
            return 200, {}, b'{}'

        def keychain_run(self_, key, args, deadline, verb, data=None):
            seen['keychain'] = args
            return 0, TOKEN.encode(), b''
        with mock.patch.object(net.Http, 'request', network), \
                mock.patch.object(keychain.Keychain, '_run', keychain_run):
            def work():
                subprocess.run(['/usr/bin/true', '--token', TOKEN, '-w', PASSWORD, 'Bearer ' + KEY, JWT, LONG],
                               capture_output=True)
                with open(os.path.join(folder, 'token-%s.json' % LONG), 'w') as f:
                    f.write(TOKEN)
                with open(os.path.join(folder, '%s.txt' % A), 'w') as f:
                    f.write('x')
                core.write_json(os.path.join(folder, 'k-%s.json' % KEY), {'t': TOKEN})
                cli.ask_page('/api/x?refresh_token=%s&who=%s' % (TOKEN, A), {'token': TOKEN})
                net.Http().request('POST', 'https://auth.example/v1/oauth/token?refresh_token=%s' % TOKEN,
                                   headers={'Authorization': 'Bearer ' + TOKEN}, body=TOKEN.encode())
                keychain.Keychain._run(object(), 'k', ['add-generic-password', '-a', A, '-s', 'svc', '-w', PASSWORD],
                                       1.0, 'write', data=TOKEN.encode())
                return 0
            with mock.patch.object(cli, 'status', work):
                code, out, err = self.run_cli('status', '--debug')
        self.assertEqual(code, 0)
        self.assertEqual(page.posted, [('/api/x?refresh_token=%s&who=%s' % (TOKEN, A), {'token': TOKEN})])
        self.assertIn('auth.example', seen['net'])
        self.assertTrue(seen['keychain'])
        lines = self.lines(err)
        self.clean(err)
        self.assertTrue(any(x.startswith('debug: started /usr/bin/true') for x in lines), lines)
        self.assertTrue(any(x.startswith('debug: page: POST /api/x -> 200') for x in lines), lines)
        self.assertTrue(any(x.startswith('debug: network: POST auth.example/v1/oauth/token -> 200') for x in lines),
                        lines)
        self.assertTrue(any(x.startswith('debug: keychain: add-generic-password -> exit 0') for x in lines), lines)
        self.assertTrue(any(x.startswith('debug: wrote ') and x.endswith('.json') for x in lines), lines)
        self.assertIs(net.Http.request, real_request)
        self.assertIs(keychain.Keychain._run, real_run)



class Timings(Debug):
    """The last line: the total time and how it was spent: this command, the page, the Keychain, the network, and other
    programs. The parts add up to the total."""
    LINE = re.compile(r'^debug: (\d+) ms in all: this command (\d+) ms, the page (\d+) ms, the Keychain (\d+) ms, '
                      r'the network (\d+) ms, other programs (\d+) ms$')

    def test_the_last_line_says_where_the_time_went(self):
        self.two()
        view = cli.local_view()
        FakePage(self, {('GET', '/api/state'): (200, view), ('POST', '/api/switch'): (200, {'note': None})},
                 delay=0.25)
        code, out, err = self.run_cli('switch', B, '--debug')
        self.assertEqual(code, 0)
        last = self.lines(err)[-1]
        m = self.LINE.match(last)
        self.assertTrue(m, last)
        total, here, page, keychain_ms, network, programs = (int(x) for x in m.groups())
        self.assertGreaterEqual(page, 500)                          # two answers, each a quarter of a second late
        self.assertLessEqual(abs(here + page + keychain_ms + network + programs - total), 3)

    def test_a_refusal_has_it_too(self):
        self.two()
        _, _, err = self.run_cli('switch', 'nobody', '--debug')
        self.assertRegex(self.lines(err)[-1], self.LINE)


if __name__ == '__main__':
    unittest.main()
