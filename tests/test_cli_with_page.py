import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The command line wired to the page and the hooks. `check` waits for the page's own check and says trouble
# and no_profile in plain words; a busy page is running, never "not running"; `switchboard hooks on|off`; tidy takes
# the session hooks out with the rest and puts Claude Code's settings back exactly; doctor names the session hooks and
# never reads the browser's tabs from a terminal; the help lists hooks; a switch through the page prints its note.
import json
import os
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import __main__ as cli
from switchboard import artifacts, browser, core, keep, notify_hooks, server, session_hooks, statusline
from test_cli_commands import Cli, A, B

BUSY = {'error': 'Switchboard is busy. It answers again in a moment.', 'busy': True, 'retry': 2,
        'version': core.VERSION}


class FakePage(object):
    """Something answering on the page's port: canned answers per (method, path), each a (code, body) or a function
    returning one; `delay` seconds before any answer. Records what was posted."""

    def __init__(self, test, answers, delay=0.0):
        self.answers, self.posted, self.delay = answers, [], delay
        page = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def answer(self, method):
                if method == 'POST':
                    n = int(self.headers.get('Content-Length') or 0)
                    page.posted.append((self.path, json.loads(self.rfile.read(n) or b'{}')))
                time.sleep(page.delay)
                route = self.path.split('?')[0]
                # A page says which data folder it serves in its state answer, busy or not (core.folder_mark), and
                # a command checks it before any change. This stand-in serves the test's folder unless an answer
                # names another ('folder': None is a page from before the check); with no state answer of its own
                # it answers an empty one.
                got = page.answers.get((method, route), (200, state()) if (method, route) == ('GET', '/api/state')
                                       else (404, {'error': 'not found'}))
                code, body = got() if callable(got) else got
                if (method, route) == ('GET', '/api/state') and isinstance(body, dict) and 'folder' not in body:
                    body = dict(body, folder=core.folder_mark())
                data = json.dumps(body).encode()
                try:
                    self.send_response(code)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except OSError:
                    pass

            def do_GET(self):
                self.answer('GET')

            def do_POST(self):
                self.answer('POST')
        self.httpd = ThreadingHTTPServer(('127.0.0.1', int(os.environ['SWITCHBOARD_PORT'])), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        test.addCleanup(self.httpd.server_close)
        test.addCleanup(self.httpd.shutdown)


def state(**more):
    return dict({'accounts': [], 'version': core.VERSION, 'demo': False}, **more)


# ---------- check: the page's own check, waited for; its words ----------

class CheckCommand(Cli):
    def setUp(self):
        super().setUp()
        self.patch = lambda *a: self.addCleanup(mock.patch.object(*a).stop) or mock.patch.object(*a).start()
        p = mock.patch.object(cli, '_sleep', lambda s: None)
        p.start()
        self.addCleanup(p.stop)

    def test_the_page_checks_on_its_own_and_the_command_waits_for_its_answer_in_plain_words(self):
        looks = []

        def look():
            looks.append(1)
            if len(looks) < 3:
                return 200, state(check={'running': True, 'started': time.time(), 'at': None})
            return 200, state(check={'running': False, 'at': time.time(), 'tabs': True, 'line': '...',
                                     'results': [{'email': A, 'result': 'trouble'}, {'email': B, 'result': 'no_profile'}]})
        page = FakePage(self, {('POST', '/api/check'): (200, {'started': True, 'ok': True}), ('GET', '/api/state'): look})
        code, out, _ = self.run_cli('check')
        self.assertEqual(code, 0, out)
        self.assertEqual([p for p, _ in page.posted], ['/api/check'])
        self.assertGreaterEqual(len(looks), 3)
        self.assertIn('claude.ai is having trouble right now', out)
        self.assertIn('no Google Chrome profile is signed in as it: press Set up for it on the page', out)
        self.assertNotIn(' trouble\n', out)
        self.assertNotIn('no_profile', out)

    def test_an_older_answer_is_not_taken_for_this_one(self):
        old = {'running': False, 'at': time.time() - 600, 'tabs': True, 'results': [{'email': A, 'result': 'ok'}]}
        FakePage(self, {('POST', '/api/check'): (200, {'started': True}), ('GET', '/api/state'): (200, state(check=old))})
        with mock.patch.object(cli, 'CHECK_WAIT', 0.2):
            code, out, _ = self.run_cli('check')
        self.assertEqual(code, 1)
        self.assertIn('The page is still checking', out)

    def test_macos_not_allowing_it_says_where_to_press(self):
        done = {'running': False, 'at': time.time() + 1, 'tabs': False, 'results': []}
        FakePage(self, {('POST', '/api/check'): (200, {'started': True}), ('GET', '/api/state'): (200, state(check=done))})
        code, out, _ = self.run_cli('check')
        self.assertEqual(code, 1)
        self.assertIn('Let Switchboard check links', out)

    def test_without_the_page_it_checks_here_with_the_same_words(self):
        with mock.patch.object(browser, 'can_see_tabs', lambda ask=False: True), \
                mock.patch.object(artifacts, 'check_profiles', lambda: [{'email': A, 'result': 'trouble'}]):
            code, out, _ = self.run_cli('check')
        self.assertEqual(code, 0)
        self.assertIn('claude.ai is having trouble right now', out)


# ---------- a busy page is running ----------

class BusyPage(Cli):
    def test_a_busy_page_is_running_and_says_so(self):
        FakePage(self, {('GET', '/api/state'): (503, BUSY)})
        self.assertEqual(cli.page_status(), 'busy')
        self.assertTrue(cli.page_running())
        self.assertIsNone(cli.page_state())                 # no numbers from it: the terminal works them out itself
        self.fake.auto['enabled'] = True
        code, out, _ = self.run_cli('auto')
        self.assertEqual(code, 0)
        self.assertNotIn(cli.NOT_RUNNING_HERE, out)
        with mock.patch.object(cli, 'open_page', lambda wait=True: True):
            self.assertEqual(self.run_cli('page')[0], 0)

    def test_a_page_too_slow_to_answer_is_busy_and_nothing_there_is_down(self):
        self.assertEqual(cli.page_status(), 'down')
        FakePage(self, {('GET', '/api/state'): (200, state())}, delay=2.0)
        self.assertEqual(cli.page_status(timeout=0.3), 'busy')

    def test_another_program_on_the_port_is_not_the_page(self):
        FakePage(self, {('GET', '/api/state'): (503, {'error': 'nope'})})
        self.assertEqual(cli.page_status(), 'down')
        self.assertFalse(cli.page_running())


# ---------- the session hooks: switchboard hooks, tidy, doctor, the help ----------

MINE = (b'{\n  "model": "opus",\n  "hooks": {\n    "SessionStart": [\n      {"hooks": [{"type": "command", '
        b'"command": "echo mine"}]}\n    ]\n  }\n}\n')


class SessionHooks(Cli):
    def setUp(self):
        super().setUp()
        self.settings = os.path.join(os.environ['CLAUDE_CONFIG_DIR'], 'settings.json')
        with open(self.settings, 'wb') as f:
            f.write(MINE)
        p = mock.patch.object(session_hooks, 'precompile', lambda: [])
        p.start()
        self.addCleanup(p.stop)

    def bytes(self):
        with open(self.settings, 'rb') as f:
            return f.read()

    def test_hooks_on_and_off_change_the_file_exactly_and_the_choice_is_yours(self):
        code, out, _ = self.run_cli('hooks', 'on')
        self.assertEqual(code, 0, out)
        self.assertIn('Session hooks in Claude Code: on', out)
        self.assertIs(session_hooks.status()['in'], True)
        self.assertIn(b'echo mine', self.bytes())                       # never over the person's own hook
        s = core.settings()
        self.assertEqual((s.get('session_hooks'), s.get('session_hooks_set_by_you')), (True, True))
        code, out, _ = self.run_cli('hooks', 'off')
        self.assertEqual(code, 0, out)
        self.assertIn('Session hooks in Claude Code: off', out)
        self.assertEqual(self.bytes(), MINE)                            # exactly as it was
        self.assertIs(core.settings().get('session_hooks'), False)      # quiet, even where one could not be taken out
        self.assertFalse(keep.session_hooks_by_itself())                # the person set them: never on by themselves
        self.assertEqual(self.run_cli('hooks', 'maybe')[0], 2)

    def test_tidy_takes_them_out_with_the_status_line_and_the_notification_hooks_and_says_so(self):
        server.statusline_turn(True)
        notify_hooks.turn(True)
        session_hooks.turn(True)
        core.set_setting('session_hooks_by_itself', 1.0)
        self.assertNotEqual(self.bytes(), MINE)
        code, out, _ = self.run_cli('tidy')
        self.assertEqual(code, 0, out)
        self.assertIn('Session hooks: taken out.', out)
        self.assertIn('Status line: put back as it was.', out)
        self.assertIn('Notification hooks: taken out.', out)
        self.assertEqual(self.bytes(), MINE)
        self.assertIsNone(core.settings().get('session_hooks_by_itself'))    # an install again puts them back
        self.assertFalse(os.path.exists(statusline.backup_path(self.settings)))  # nothing left: the copy goes too

    def test_doctor_names_the_session_hooks_and_never_reads_tabs_from_a_terminal(self):
        self.two()
        with mock.patch.object(cli, 'page_running', lambda: False), \
                mock.patch.object(cli.linkapp, 'status', lambda fresh=False: {'installed': False, 'on': False}), \
                mock.patch.object(cli.browser, 'present', lambda: True), \
                mock.patch.object(cli.browser, 'profiles', lambda: {'P1': 'Work'}), \
                mock.patch.object(cli.browser, 'can_see_tabs', side_effect=AssertionError('osascript from a terminal')):
            _, out, _ = self.run_cli('doctor')
            self.assertIn(' --  Session hooks in Claude Code: off', out)
            self.assertIn('Run: switchboard hooks on', out)
            self.assertIn(' --  Allowed to read Google Chrome tab titles: not known while the page is not running', out)
            session_hooks.turn(True)
            _, out, _ = self.run_cli('doctor')
            self.assertIn(' ok  Session hooks in Claude Code: on', out)

    def test_doctor_reads_tab_access_from_the_running_page(self):
        tabs = {'state': 'would_ask', 'text': "Switchboard needs your OK to read Google Chrome's tab titles."}
        FakePage(self, {('GET', '/api/state'): (200, state(browser={'tabs': tabs}))})
        page = cli.page_status()
        lines = []
        cli._doctor_tabs(lambda good, text, fix='': lines.append((good, text, fix)), page)
        self.assertEqual(lines[0][0], False)
        self.assertIn('needs your OK', lines[0][1])
        self.assertIn('Let Switchboard check links', lines[0][2])

    def test_the_help_lists_hooks(self):
        _, out, _ = self.run_cli('help')
        self.assertIn('switchboard hooks [on|off]', out)
        for line in ('switchboard switch [NAME]', 'switchboard remove NAME', 'switchboard alias NAME', 'switchboard run [NAME]',
                     'switchboard owner LINK'):
            self.assertIn(line, out)
        code, out, _ = self.run_cli('hooks', '--help')
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith('switchboard hooks [on|off]'), out)


# ---------- a switch through the page prints what the page says about it ----------

class SwitchNote(Cli):
    def test_the_pages_note_is_printed(self):
        page = FakePage(self, {('POST', '/api/switch'): (200, {'ok': True, 'note': artifacts.DESKTOP_NOTE})})
        view = {'accounts': [{'email': B, 'slot': 2, 'unmanaged': False, 'active': False}]}
        import io
        from contextlib import redirect_stdout
        out = io.StringIO()
        with redirect_stdout(out):
            cli.switch(B, view)
        self.assertEqual(page.posted, [('/api/switch', {'email': B})])
        self.assertEqual(out.getvalue(), artifacts.DESKTOP_NOTE + '\n')

    def test_with_the_page_down_auto_on_here_reaches_the_owner_map(self):
        self.two()
        with mock.patch.object(cli.engine, 'foreign_switcher', lambda folder=None: None):
            self.assertEqual(self.run_cli('auto', 'on')[0], 0)
        self.assertIs(artifacts.read_owner_map().get('auto'), True)

    def test_with_the_page_down_a_switch_here_brings_the_owner_map_along(self):
        self.two()
        code, out, _ = self.run_cli('switch', B)
        self.assertEqual(code, 0, out)
        self.assertEqual(artifacts.read_owner_map().get('active'), B)


if __name__ == '__main__':
    unittest.main()
