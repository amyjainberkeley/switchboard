import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The page and the browser: which browsers count, the first run's steps, the page's port, the status line's model,
# the live numbers, and that nothing leaves the Mac.
#
# Made-up accounts only. Every folder is a throwaway one; /Applications is stood in for by a temp folder.
# Nothing here opens a browser, runs claude, the engine or the installer.
import contextlib
import io
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer as HTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import browser, connect, core, demo, engine, server, statusline
from fakes_accounts_api import install
import test_app  # its headless-Chrome helper

A, B, C = 'maya@northwind.example', 'maya@ivy.example', 'maya@fernhill.example'
URL = 'https://claude.ai/oauth/authorize?code=true'


class Sandbox(unittest.TestCase):
    """A throwaway home, data folder and Applications folder."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-server-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, 'home')
        self.data = os.path.join(self.tmp, 'data')
        os.makedirs(os.path.join(self.home, '.claude'))
        env = mock.patch.dict(os.environ, {'HOME': self.home, 'SWITCHBOARD_HOME': self.data,
                                           'CLAUDE_CONFIG_DIR': os.path.join(self.home, '.claude')})
        env.start()
        self.addCleanup(env.stop)
        for k in ('SWITCHBOARD_PORT', 'SWITCHBOARD_STATE'):
            os.environ.pop(k, None)
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = False
        self.apps = os.path.join(self.tmp, 'Applications')         # stands in for /Applications
        os.makedirs(self.apps)
        self.patch(browser, 'app_dirs', lambda: [self.apps, os.path.join(self.home, 'Applications')])
        self.forget()
        self.addCleanup(self.forget)

    def patch(self, target, name, value):
        p = mock.patch.object(target, name, value, create=True)
        p.start()
        self.addCleanup(p.stop)

    def forget(self):
        """Clear what the browser module remembers between calls."""
        getattr(browser, 'APPS', {}).clear()
        browser.HINTS.update(at=0, key=None, value={})
        browser.BLIND['until'] = 0

    def browser_data(self, k='chrome', profiles=None):
        """The browser's data folder, as it is once the browser has been opened: Local State and its profiles."""
        d = os.path.join(browser.support(), browser.BROWSERS[k]['dir'])
        os.makedirs(d, exist_ok=True)
        cache = dict((folder, {'name': name}) for folder, name in (profiles or {'Default': 'Person 1'}).items())
        with open(os.path.join(d, 'Local State'), 'w') as f:
            json.dump({'profile': {'info_cache': cache}}, f)
        return d

    def app(self, k='chrome', mine=False):
        """The browser's app, in /Applications (or in ~/Applications with mine=True)."""
        where = os.path.join(self.home, 'Applications') if mine else self.apps
        path = os.path.join(where, browser.BROWSERS[k]['app'] + '.app')
        os.makedirs(os.path.join(path, 'Contents', 'MacOS'))
        return path


# ---------- a browser is there only when its data folder AND its app are ----------

class BrowserNeedsItsApp(Sandbox):
    def test_a_data_folder_left_behind_is_no_browser(self):
        self.browser_data('chrome')                    # Chrome was opened once, then moved to the Trash
        self.assertFalse(browser.present())
        self.app('chrome')
        self.forget()
        self.assertTrue(browser.present())

    def test_the_app_in_your_own_applications_folder_counts(self):
        self.browser_data('chrome')
        self.app('chrome', mine=True)
        self.assertTrue(browser.present())

    def test_an_app_never_opened_is_no_browser_either(self):
        self.app('chrome')                             # no data folder: no profile to open a link in
        self.assertFalse(browser.present())

    def test_the_app_is_looked_up_by_path_and_remembered(self):
        self.browser_data('chrome')
        app = self.app('chrome')
        looked = []
        real_isdir = os.path.isdir
        self.patch(browser.os.path, 'isdir', lambda p: looked.append(p) or real_isdir(p))
        self.assertTrue(browser.present())
        self.assertTrue(browser.present())
        self.assertEqual(looked.count(app), 1)         # one look, then remembered
        shutil.rmtree(app)
        self.assertTrue(browser.present())             # still remembered...
        self.patch(browser, 'APP_FRESH', 0)
        self.assertFalse(browser.present())            # ...until it is looked up again

    def test_a_sign_in_without_the_app_opens_in_the_default_browser(self):
        self.browser_data('chrome', {'P1': 'Work'})
        opened = []
        self.patch(browser, 'open_url', lambda p, u, background=False: opened.append(('profile', p, u)))
        self.patch(connect.subprocess, 'Popen', lambda args, **k: opened.append(('default', args)))
        connect.open_sign_in('P1', URL)
        self.assertEqual(opened, [('default', ['/usr/bin/open', URL])])

    def test_the_page_starts_no_profile_sign_in_without_the_app(self):
        self.browser_data('chrome', {'P1': 'Work'})
        started = []
        self.patch(connect, 'start', lambda email, profile: started.append((email, profile)) or 'new')
        self.assertEqual(server.act('/api/connect', {'email': B, 'profile': 'P1'})['key'], 'new')
        self.assertEqual(started, [(B, None)])
        self.assertIn('Opened the sign-in for %s in your browser' % B, [e['text'] for e in core.load()['events']])


# ---------- a browser chosen in settings that is gone falls back to the first one found ----------

class ChosenBrowserGone(Sandbox):
    def test_a_chosen_browser_whose_folder_is_gone_falls_back(self):
        core.set_setting('browser', 'brave')           # chosen once, Brave since removed with its folder
        self.browser_data('chrome', {'P1': 'Work'})
        self.app('chrome')
        self.assertEqual(browser.kind(), 'chrome')
        self.assertTrue(browser.present())
        self.assertEqual(browser.profiles(), {'P1': 'Work'})
        self.assertEqual(browser.app(), 'Google Chrome')

    def test_a_chosen_browser_whose_app_is_gone_falls_back_too(self):
        core.set_setting('browser', 'brave')
        self.browser_data('brave')                     # its folder is still there, its app is not
        self.browser_data('chrome')
        self.app('chrome')
        self.assertEqual(browser.kind(), 'chrome')

    def test_a_chosen_browser_that_is_here_is_kept(self):
        core.set_setting('browser', 'brave')
        for k in ('chrome', 'brave'):
            self.browser_data(k)
            self.app(k)
        self.assertEqual(browser.kind(), 'brave')

    def test_with_no_browser_at_all_the_choice_stays_and_nothing_is_present(self):
        core.set_setting('browser', 'edge')
        self.assertEqual(browser.kind(), 'edge')
        self.assertFalse(browser.present())
        core.set_setting('browser', None)
        self.assertEqual(browser.kind(), 'chrome')
        self.assertFalse(browser.present())


# ---------- no "New profile" where no browser can make one ----------

class NoNewProfileWithoutABrowser(Sandbox):
    def setUp(self):
        super().setUp()
        with core.locked() as st:
            core.ensure_account(st, A).update(connected=True, slot=1)
        self.opened = []
        self.patch(browser, 'open_url', lambda p, u, background=False: self.opened.append((p, u)))

    def test_the_server_refuses_it_in_one_plain_line(self):
        with self.assertRaises(core.SwitchError) as said:
            server.act('/api/profile', {'email': A, 'profile': '+new'})
        self.assertEqual(str(said.exception), 'A new profile needs Google Chrome. Install it and open it once.')
        st = core.load()
        self.assertIsNone(st['accounts'][A].get('profile'))          # no profile that no browser could open
        self.assertEqual(self.opened, [])
        self.assertFalse([e for e in st['events'] if e['text'].startswith('Made the')])

    def test_with_a_browser_a_new_profile_is_made_and_opened_at_sign_in(self):
        self.browser_data('chrome', {'P1': 'Work'})
        self.app('chrome')
        made = 'Claude northwind'                                     # its short name on the page
        self.assertEqual(server.act('/api/profile', {'email': A, 'profile': '+new'}),
                         {'opened': True, 'profile_name': made})
        self.assertEqual(self.opened, [(made, 'https://claude.ai/login')])

    def test_choosing_none_or_a_profile_that_is_there_still_works_without_the_app(self):
        self.browser_data('chrome', {'P1': 'Work'})   # the folder left behind: its profiles can still be named
        self.assertEqual(server.act('/api/profile', {'email': A, 'profile': 'P1'}), {})
        self.assertEqual(core.load()['accounts'][A]['profile'], 'P1')
        self.assertEqual(server.act('/api/profile', {'email': A, 'profile': ''}), {})
        self.assertIsNone(core.load()['accounts'][A]['profile'])


# ---------- the first run counts the browser-profile step only while links are in use ----------

class SetupSteps(Sandbox):
    LINKS_OFF = {'installed': False, 'on': False, 'browser': None, 'home': ''}

    def facts(self, links=None, profiles=None):
        return {'profiles': profiles or {}, 'browser': {'name': 'Google Chrome', 'present': False, 'tabs_blocked': False},
                'engine': {'installed': True, 'claude': True}, 'links': dict(links or self.LINKS_OFF),
                'auto': {'available': True, 'enabled': False, 'session_at': 90, 'weekly_at': 95},
                'setup_hidden': False, 'now': time.time(), 'demo': False}

    def kept(self, **more):
        """One account Switchboard holds, kept with no browser profile (signed in in the default browser, or kept by
        itself from Claude Code's /login)."""
        st = core.blank_state()
        core.ensure_account(st, A).update(dict(connected=True, slot=1, profile=None), **more)
        st['active'] = A
        return st

    def test_an_account_with_no_profile_finishes_the_first_run_when_links_are_not_used(self):
        setup = server.render(self.kept(), self.facts())['setup']
        self.assertTrue(setup['profiles'])
        self.assertTrue(setup['done'])

    def test_the_link_app_installed_but_off_does_not_count_profiles_either(self):
        setup = server.render(self.kept(), self.facts(links=dict(self.LINKS_OFF, installed=True)))['setup']
        self.assertTrue(setup['profiles'])
        self.assertFalse(setup['links'])               # it still waits for the link app to be turned on
        self.assertFalse(setup['done'])

    def test_with_links_on_every_account_needs_its_profile(self):
        on = dict(self.LINKS_OFF, installed=True, on=True)
        setup = server.render(self.kept(), self.facts(links=on))['setup']
        self.assertEqual((setup['profiles'], setup['done']), (False, False))
        setup = server.render(self.kept(profile='P1'), self.facts(links=on, profiles={'P1': 'Work'}))['setup']
        self.assertEqual((setup['profiles'], setup['done']), (True, True))
        setup = server.render(self.kept(profile='P1', signed_out=time.time()), self.facts(links=on))['setup']
        self.assertFalse(setup['profiles'])            # a profile signed out of claude.ai cannot open its links

    def test_no_account_is_still_not_done(self):
        st = core.blank_state()
        setup = server.render(st, self.facts())['setup']
        self.assertEqual((setup['accounts'], setup['done']), (False, False))


# ---------- the page's port, said plainly; the demo opens a tab only on a page that started ----------

class Stop(Exception):
    """Ends serve()'s endless wait in a test."""


class PortLines(Sandbox):
    def setUp(self):
        super().setUp()
        self.patch(server, 'loop', lambda: self.fail('the page ran on a port it does not hold'))
        made = []

        class Recorded(server.ThreadingHTTPServer):     # every server serve() starts is closed after the test
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                made.append(self)
        self.patch(server, 'ThreadingHTTPServer', Recorded)
        self.addCleanup(lambda: [(s.shutdown(), s.server_close()) for s in made])

    def holder(self, handler=None):
        """Something else on a port: a plain socket that never answers, or an HTTP server with `handler`."""
        if handler is None:
            s = socket.socket()
            s.bind(('127.0.0.1', 0))
            s.listen(4)
            self.addCleanup(s.close)
            return s.getsockname()[1]
        httpd = HTTPServer(('127.0.0.1', 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return httpd.server_address[1]

    def serve_on(self, n, demo_on=False):
        with mock.patch.dict(os.environ, {'SWITCHBOARD_PORT': str(n)}), mock.patch.object(core, 'DEMO', demo_on):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                server.serve()
            return out.getvalue()

    def test_another_program_on_the_port_names_the_command_that_moves_the_page(self):
        n = self.holder()
        with self.assertRaises(core.SwitchError) as said:
            self.serve_on(n)
        self.assertEqual(str(said.exception), "Port %d is in use by another program, so the page can't start. "
                                              "Pick a free one: switchboard update --port 7801" % n)

    def test_a_web_page_that_is_not_switchboard_is_another_program(self):
        from http.server import BaseHTTPRequestHandler

        class Other(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b'{"hello": "world"}'
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        n = self.holder(Other)
        with self.assertRaises(core.SwitchError) as said:
            self.serve_on(n)
        self.assertIn('is in use by another program', str(said.exception))

    def test_switchboards_own_page_on_the_port_is_said_to_be_running_already(self):
        n = self.holder(server.Handler)                # the real page's handler, as the background job runs it
        with mock.patch.dict(os.environ, {'SWITCHBOARD_PORT': str(n)}):
            self.assertTrue(server.page_answers(n))
        out = self.serve_on(n)
        self.assertEqual(out, "Switchboard's page is already running at http://localhost:%d.\n" % n)

    def test_the_demo_on_the_port_is_not_switchboards_page(self):
        self.addCleanup(demo.STATE.clear)
        self.addCleanup(demo.FACTS.clear)
        n = self.holder(server.Handler)
        with mock.patch.dict(os.environ, {'SWITCHBOARD_PORT': str(n)}), mock.patch.object(core, 'DEMO', True):
            self.assertFalse(server.page_answers(n))
        self.assertFalse(server.page_answers(self.holder()))

    def test_the_suggested_port_is_never_the_one_in_use(self):
        self.assertIn('--port 7801', server.port_taken(7799))
        self.assertIn('--port 7802', server.port_taken(7801))
        with mock.patch.object(core, 'DEMO', True):
            self.assertIn('SWITCHBOARD_PORT=7802 switchboard demo', server.port_taken(7798))
            self.assertIn('SWITCHBOARD_PORT=7803 switchboard demo', server.port_taken(7802))

    def test_the_demo_on_a_taken_port_keeps_its_line_and_opens_no_tab(self):
        from switchboard import __main__ as cli
        n = self.holder()
        opened = []
        self.patch(cli.subprocess, 'Popen', lambda args, **k: opened.append(args))
        with mock.patch.dict(os.environ, {'SWITCHBOARD_PORT': str(n)}):
            with self.assertRaises(core.SwitchError) as said:
                cli.main(['demo'])
        self.assertEqual(str(said.exception), "Port %d is in use, so the demo can't start. Is another demo open? Or run: "
                                              "SWITCHBOARD_PORT=7802 switchboard demo" % n)
        self.assertEqual(opened, [])                   # no tab on a page that never started

    def test_the_demo_opens_its_tab_once_its_port_answers(self):
        from switchboard import __main__ as cli
        free = socket.socket()
        free.bind(('127.0.0.1', 0))
        n = free.getsockname()[1]
        free.close()
        seen = []

        def opened(args, **k):
            with socket.create_connection(('127.0.0.1', n), timeout=2):
                seen.append(args)                      # the page was listening when the tab was asked for

        def wait(_):
            raise Stop()
        self.patch(cli.subprocess, 'Popen', opened)
        self.patch(server, 'time', type('T', (), {'time': staticmethod(time.time), 'sleep': staticmethod(wait)}))
        with mock.patch.dict(os.environ, {'SWITCHBOARD_PORT': str(n)}), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(Stop):
                cli.main(['demo'])
        self.assertEqual(seen, [['/usr/bin/open', 'http://localhost:%d' % n]])


# ---------- a sign-in in the default browser says what to watch for; another account is a warning ----------

class FakeLogin(object):
    """Stands in for `claude auth login`: hands over the sign-in link at once, as Claude Code's `open` stand-in does."""

    def __init__(self, args, env=None, **kw):
        self.args, self.pid = args, None
        with open(env['SWITCHBOARD_URL_FILE'], 'w') as f:
            f.write(URL)

    def poll(self):
        return 0


class DefaultBrowserSignIn(Sandbox):
    def setUp(self):
        super().setUp()
        self.addCleanup(connect.RUNNING.clear)
        self.addCleanup(getattr(connect, 'NOTES', {}).clear)
        self.opened = []
        self.cleaned = []
        self.patch(connect, 'claude_bin', lambda: '/made-up/claude')
        self.fake = install(self)                       # the engine: it keeps what a sign-in folder holds
        self.patch(connect, '_clean', lambda d: self.cleaned.append(d))     # it would ask the Keychain
        self.patch(browser, 'open_url', lambda p, u, background=False: self.opened.append(('profile', p)))

        def popen(args, **kw):
            if args[0] == '/usr/bin/open':
                self.opened.append(('default', args[1]))
                return None
            return FakeLogin(args, **kw)
        self.patch(connect.subprocess, 'Popen', popen)

    def test_how_each_sign_in_opened_is_kept_for_the_page(self):
        self.assertEqual(connect.open_sign_in(None, URL), 'default')
        key = connect.start(B, None)
        st = core.load()
        self.assertEqual([(c['email'], c['default_browser']) for c in connect.view(st)], [(B, True)])
        connect.RUNNING.clear()
        self.browser_data('chrome', {'P1': 'Work'})
        self.app('chrome')
        self.assertEqual(connect.open_sign_in('P1', URL), 'profile')
        connect.start(B, 'P1')
        self.assertEqual([(c['email'], c['default_browser']) for c in connect.view(st)], [(B, False)])
        self.assertEqual(self.opened, [('default', URL), ('default', URL), ('profile', 'P1'), ('profile', 'P1')])
        self.assertTrue(key)

    def finished(self, asked, got, here=()):
        """A sign-in for `asked` that came back as `got`, finished by the page's tick. Returns the state after."""
        st = core.blank_state()
        for i, email in enumerate(here):
            core.ensure_account(st, email).update(connected=True, slot=i + 1)
        d = tempfile.mkdtemp(dir=self.tmp)
        with open(os.path.join(d, '.claude.json'), 'w') as f:
            json.dump({'oauthAccount': {'emailAddress': got}}, f)
        connect.RUNNING[asked or 'new'] = {'proc': FakeLogin.__new__(FakeLogin), 'dir': d, 'started': time.time(),
                                           'email': asked, 'profile': None, 'opened': 'default'}
        connect.finish(st)
        self.assertIn(('add_login', d), self.fake.calls)          # the engine read the folder's login itself
        self.assertEqual(self.cleaned[-1], d)
        return st

    def test_a_sign_in_as_an_account_already_here_is_a_warning_and_says_what_to_do(self):
        st = self.finished(B, A, here=(A,))
        ev = st['events'][-1]
        self.assertEqual((ev['kind'], ev['text']),
                         ('warn', 'Signed in as %s, not %s. It was already here, so nothing new was added.' % (A, B)))
        notes = [c for c in connect.view(st) if c.get('done')]
        self.assertEqual([c['line'] for c in notes], ['Your browser signed in as %s, which is already here. To add %s, '
                                                      'sign out of claude.ai there first.' % (A, B)])
        connect.stop(notes[0]['key'])                  # the page's Hide
        self.assertEqual(connect.view(st), [])

    def test_signing_in_again_as_one_account_but_getting_another_says_sign_in_again(self):
        st = self.finished(B, A, here=(A, B))         # B's "Sign in again", approved as A by the browser
        self.assertEqual([c['line'] for c in connect.view(st) if c.get('done')],
                         ['Your browser signed in as %s, which is already here. To sign in as %s again, sign out of '
                          'claude.ai there first.' % (A, B)])

    def test_a_sign_in_as_another_new_account_adds_it_with_a_warning(self):
        st = self.finished(B, C, here=(A,))
        self.assertTrue(st['accounts'][C]['connected'])
        ev = st['events'][-1]
        self.assertEqual((ev['kind'], ev['text']),
                         ('warn', 'Added %s, not %s: the browser was signed in as %s.' % (C, B, C)))
        self.assertEqual([c['line'] for c in connect.view(st) if c.get('done')],
                         ['Added %s, not %s: your browser was signed in as %s.' % (C, B, C)])

    def test_the_account_asked_for_is_a_plain_added_with_no_note(self):
        st = self.finished(B, B, here=(A,))
        self.assertEqual((st['events'][-1]['kind'], st['events'][-1]['text']), ('switch', 'Added %s' % B))
        self.assertEqual(connect.view(st), [])
        st = self.finished('', C, here=(A,))           # no email typed: any account is the one asked for
        self.assertEqual(st['events'][-1]['text'], 'Added %s' % C)
        self.assertEqual(connect.view(st), [])

    def test_a_note_goes_after_ten_minutes(self):
        st = self.finished(B, A, here=(A,))
        self.assertEqual(len(connect.view(st)), 1)
        later = time.time() + 601
        with mock.patch.object(connect.time, 'time', lambda: later):
            self.assertEqual(connect.view(st), [])


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class WaitingRowInThePage(test_app.ChromePage):
    ROWS = """
      await wait(300);
      done(all('#below .nl').map((p) => [p.querySelector('.tx').textContent, (p.querySelector('button') || {}).textContent || null]));
"""

    def connecting(self, *entries):
        demo._ready()
        demo.FACTS['connecting'] = list(entries)
        return self.run_page(self.ROWS)

    def test_the_default_browser_row_says_to_sign_out_of_another_account(self):
        rows = self.connecting({'key': B, 'email': B, 'name': B, 'profile_name': '', 'started': time.time(),
                                'default_browser': True})
        self.assertEqual(rows, [['Waiting for sign-in as %s. If your browser shows another account, sign out of '
                                 'claude.ai there first.' % B, 'Cancel']])

    def test_with_no_email_it_names_an_account_already_here(self):
        rows = self.connecting({'key': 'new', 'email': '', 'name': '', 'profile_name': '', 'started': time.time(),
                                'default_browser': True})
        self.assertEqual(rows, [['Waiting for sign-in. If your browser shows an account already here, sign out of '
                                 'claude.ai there first.', 'Cancel']])

    def test_a_sign_in_in_a_profile_keeps_its_short_row(self):
        rows = self.connecting({'key': B, 'email': B, 'name': B, 'profile_name': 'Work', 'started': time.time(),
                                'default_browser': False})
        self.assertEqual(rows, [['Waiting for sign-in as %s' % B, 'Cancel']])

    def test_a_sign_in_as_another_account_stays_said_until_hidden(self):
        line = 'Your browser signed in as %s, which is already here. To add %s, sign out of claude.ai there first.' % (A, B)
        rows = self.connecting({'key': B, 'email': B, 'name': B, 'profile_name': '', 'started': time.time(),
                                'done': True, 'got': A, 'line': line})
        self.assertEqual(rows, [[line, 'Hide']])
        sent = self.run_page("""
      $('#below .nl button').click(); await wait(400);
      done(sent);
""")
        self.assertEqual(sent, [['/api/cancel', {'key': B}]])


# ---------- Chrome setup says the link app needs Apple's command line tools ----------

@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class LinksRowInThePage(test_app.ChromePage):
    def test_without_the_link_app_chrome_setup_names_the_command_and_what_it_needs(self):
        # Links have no switch on the page (always on); their rows are Settings › Links
        demo._ready()
        demo.FACTS['links'].update(installed=False, on=False)
        v = self.run_page("""
      location.hash = '#settings'; await wait(900);
      const row = $('#sec-settings [data-k="links-needs"]');
      done({aside: row ? row.textContent : null, code: row ? (row.querySelector('code') || {}).textContent || null : null,
            sw: all('#sec-settings .sw').some((b) => /links/i.test(b.getAttribute('aria-label') || ''))});
""")
        self.assertEqual(v['aside'], 'Needs the link app: switchboard update --link-app (it needs Apple’s command line tools)')
        self.assertEqual(v['code'], 'switchboard update --link-app')
        self.assertFalse(v['sw'])


# ---------- the status line shows the model in use ----------

class StatusLineModel(unittest.TestCase):
    ST = {'active': A, 'next': B}

    def data(self, model=None, used=49):
        d = {'session_id': 's1', 'rate_limits': {'seven_day': {'used_percentage': used}}}   # no reset time: no "refills"
        if model is not None:
            d['model'] = model
        return d

    def line(self, data, st=None, columns=None):
        return statusline.build(data, self.ST if st is None else st, time.time(), columns=columns, colour=False)

    def test_the_model_claude_code_names_is_shown_with_the_account(self):
        self.assertEqual(self.line(self.data({'id': 'claude-opus-5-5', 'display_name': 'Opus'})),
                         '● %s on Opus · 51%% left · next: %s' % (A, B))

    def test_without_a_model_the_line_names_none(self):
        self.assertEqual(self.line(self.data()), '● %s · 51%% left · next: %s' % (A, B))
        for odd in ({'id': 'claude-opus-5-5'}, {'display_name': 5}, 'Opus', None, ['Opus'], {'display_name': '  '}):
            self.assertEqual(self.line(self.data(odd)), '● %s · 51%% left · next: %s' % (A, B), odd)

    def test_the_name_is_kept_short_and_plain(self):
        self.assertEqual(self.line(self.data({'display_name': 'Claude Fable 2'}), st={}), '● Fable 2 · 51% left')
        self.assertEqual(self.line(self.data({'display_name': 'Op\x1b[2Jus\x07'}), st={}), '● Op[2Jus · 51% left')
        long_name = self.line(self.data({'display_name': 'Sonnet with a very long name indeed'}), st={})
        self.assertEqual(long_name, '● Sonnet with a very long… · 51% left')       # 24 at most, as a name is cut

    def test_a_narrow_terminal_drops_next_then_the_model_then_the_numbers(self):
        d = self.data({'display_name': 'Opus'})
        full = '● %s on Opus · 51%% left · next: %s' % (A, B)
        self.assertEqual(self.line(d, columns=len(full) + statusline.MARGIN), full)
        self.assertEqual(self.line(d, columns=len(full) + 1), '● %s on Opus · 51%% left' % A)
        self.assertEqual(self.line(d, columns=len('● %s on Opus · 51%% left' % A) + 1), '● %s · 51%% left' % A)
        self.assertEqual(self.line(d, columns=len('● %s · 51%% left' % A) + 1), '● %s' % A)
        self.assertEqual(self.line(d, st={}, columns=20), '● Opus · 51% left')
        self.assertEqual(self.line(d, st={}, columns=14), '● 51% left')

    def test_the_live_record_still_saves_only_numbers(self):
        rec = statusline.live_record(self.data({'display_name': 'Opus'}), {}, time.time())
        self.assertNotIn('Opus', json.dumps(rec))


# ---------- the account in use takes Claude Code's own numbers when the engine has none fresh ----------

H, D = 3600, 86400


class LiveNumbers(Sandbox):
    """server.apply_live_numbers(st, now): the status line's live records (statusline.py writes them) stand in for the
    engine's reading of the account in use when that reading is missing, failed or older than the record (the newer
    reading wins; test_statusline_live_numbers has the rest)."""

    def setUp(self):
        super().setUp()
        self.now = time.time()
        self.wa = self.now + 3 * D                     # A's weekly window resets then; B's a day later
        self.wb = self.now + 4 * D
        self.fa = self.now + 2 * H                     # A's 5-hour window

    def state(self, age=1200, status='ok', error=None, usage=True, switched=3 * H):
        """A in use (its engine reading `age` seconds old), B beside it with a fresh reading."""
        st = core.blank_state()
        a = core.ensure_account(st, A)
        a.update(connected=True, slot=1, usage_status=status, usage_error=error, note=None,
                 usage={'session': {'pct': 10.0, 'resets': server.iso(self.fa), 'pace': None, 'lasts': None},
                        'weekly': {'pct': 30.0, 'resets': server.iso(self.wa), 'pace': 31.0, 'lasts': True},
                        'scoped': [{'name': 'Fable', 'pct': 40.0, 'resets': server.iso(self.wa)}], 'spend': None}
                 if usage else None,
                 usage_at=self.now - age if usage else None)
        b = core.ensure_account(st, B)
        b.update(connected=True, slot=2, usage_status='ok', usage_at=self.now - 60,
                 usage={'session': None, 'weekly': {'pct': 5.0, 'resets': server.iso(self.wb), 'pace': None,
                                                    'lasts': None}, 'scoped': [], 'spend': None})
        st['active'] = A
        st['timeline'] = [[B, self.now - D], [A, self.now - switched]]
        return st

    def live(self, sid='s1', age=60, week=55.0, five=20.0, wreset=None, freset=None, account=A, env=None):
        """One live record, made by statusline.live_record as Claude Code's status line run would make it."""
        rl = {}
        if week is not None:
            rl['seven_day'] = {'used_percentage': week, 'resets_at': self.wa if wreset is None else wreset}
        if five is not None:
            rl['five_hour'] = {'used_percentage': five, 'resets_at': self.fa if freset is None else freset}
        rec = statusline.live_record({'session_id': sid, 'rate_limits': rl}, {'active': account}, self.now - age,
                                     env={} if env is None else env)
        folder = statusline.live_dir()
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, sid + '.json')
        with open(path, 'w') as f:
            json.dump(rec, f)
        return rec

    def shown(self, st):
        """A's numbers as the page, the menu bar and the terminal get them: render()'s account, trusted or not."""
        facts = {'profiles': {}, 'browser': {'name': 'Google Chrome', 'present': False, 'tabs_blocked': False},
                 'engine': {'installed': True, 'claude': True}, 'links': {'installed': False, 'on': False},
                 'auto': {'available': True, 'enabled': False, 'session_at': 90, 'weekly_at': 95},
                 'setup_hidden': True, 'now': self.now, 'demo': False}
        a = next(x for x in server.render(st, facts)['accounts'] if x['email'] == A)
        u = a['usage'] if a['trusted'] else None
        return None if u is None else ((u.get('session') or {}).get('pct'), (u.get('weekly') or {}).get('pct'))

    def test_an_old_engine_reading_gives_way_to_claude_codes_fresh_numbers(self):
        st = self.state(age=1200)
        rec = self.live(age=60)
        server.apply_live_numbers(st, self.now)
        a = st['accounts'][A]
        self.assertEqual(a['usage_at'], rec['at'])
        self.assertEqual(self.shown(st), (20.0, 55.0))
        self.assertEqual(a['usage']['weekly']['resets'], server.iso(self.wa))
        self.assertEqual(a['usage']['scoped'], [{'name': 'Fable', 'pct': 40.0, 'resets': server.iso(self.wa)}])
        self.assertEqual(st['accounts'][B]['usage']['weekly']['pct'], 5.0)      # only the account in use

    def test_a_missing_or_failed_reading_gives_way_too(self):
        for kw in ({'usage': False}, {'age': 60, 'status': 'error', 'error': 'The usage page did not answer.'},
                   {'age': 60, 'status': 'frozen'}):
            with self.subTest(**kw):
                st = self.state(**kw)
                self.live(age=420)                     # seven minutes old: still shown, as current
                server.apply_live_numbers(st, self.now)
                self.assertEqual(self.shown(st), (20.0, 55.0))

    def test_an_engine_reading_newer_than_the_record_stays(self):
        st = self.state(age=10)                        # newer than the record's numbers: the newer wins
        self.live(age=300)
        server.apply_live_numbers(st, self.now)
        self.assertEqual(self.shown(st), (10.0, 30.0))
        self.assertNotIn('live', st['accounts'][A])

    def test_only_a_fresh_record_for_this_account_after_the_last_switch_counts(self):
        cases = {'ten minutes old': dict(age=601), 'dated ahead of the clock': dict(age=-60),
                 'from before the switch': dict(age=3 * H + 5), 'within the switch settling time': dict(age=3 * H - 20),
                 'labelled with another account': dict(account=B),
                 'from a Claude Code on a profile of its own': dict(env={'CLAUDE_CONFIG_DIR': os.path.join(self.tmp, 'other')}),
                 "in another account's weekly window": dict(wreset=self.wb + 30, freset=self.now + 4 * H),
                 "not this account's open weekly window": dict(wreset=self.wa - 2 * D),
                 "not this account's open 5-hour window": dict(freset=self.fa + H)}
        for why, kw in cases.items():
            with self.subTest(why):
                shutil.rmtree(statusline.live_dir(), True)
                st = self.state(age=1200)
                self.live(**kw)
                server.apply_live_numbers(st, self.now)
                self.assertEqual(self.shown(st), (10.0, 30.0), why)       # the engine's own (old) reading, as before
                self.assertNotIn('live', st['accounts'][A])

    def test_the_newest_record_that_fits_wins(self):
        st = self.state(age=1200)
        self.live(sid='older', age=300, week=50.0)
        self.live(sid='newer', age=30, week=56.0)
        self.live(sid='other', age=5, week=99.0, account=B)
        server.apply_live_numbers(st, self.now)
        self.assertEqual(self.shown(st), (20.0, 56.0))

    def test_a_window_that_refilled_since_is_not_started(self):
        st = self.state(age=1200)
        self.live(age=120, freset=self.now - 30)       # the 5-hour window reset half a minute ago
        st['accounts'][A]['usage']['session']['resets'] = server.iso(self.now - 30)
        server.apply_live_numbers(st, self.now)
        # refilled: 0%, its reset passed, drawn as not started; never left out (no window means no such limit)
        self.assertEqual(st['accounts'][A]['usage']['session'],
                         {'pct': 0.0, 'resets': server.iso(self.now - 30), 'pace': None, 'lasts': None})
        self.assertEqual(self.shown(st), (0.0, 55.0))

    def test_a_login_proven_to_be_another_accounts_is_never_given_its_numbers(self):
        st = self.state(age=60)
        st['disagree'] = True                          # Claude Code's config names A; its login is another account's
        self.live(age=30)
        server.apply_live_numbers(st, self.now)
        self.assertNotIn('live', st['accounts'][A])

    def test_each_tick_starts_again_from_the_engines_own_reading(self):
        st = self.state(age=1200)
        engine_reading = dict(st['accounts'][A])
        self.live(age=60)
        server.apply_live_numbers(st, self.now)
        server.apply_live_numbers(st, self.now)        # twice, with no engine look between: the same
        self.assertEqual(self.shown(st), (20.0, 55.0))
        shutil.rmtree(statusline.live_dir())           # Claude Code closed: nothing fresh any more
        server.apply_live_numbers(st, self.now + 5)
        a = st['accounts'][A]
        for k in ('usage', 'usage_at', 'usage_status', 'usage_error', 'note'):
            self.assertEqual(a.get(k), engine_reading.get(k), k)       # the engine's own reading, exactly
        self.assertNotIn('live', a)

    def test_with_the_engines_rows_on_every_tick(self):
        """As the page's tick runs it: apply_rows (here the engine has no reading for A), then apply_live_numbers."""
        st = self.state(age=1200)
        rows = [engine._row(id='acct-1', number=1, email=A, active=True, usage_error='The usage page did not answer.'),
                engine._row(id='acct-2', number=2, email=B, source='endpoint', usage_at=self.now - 60,
                            usage={'session': None, 'weekly': {'pct': 5.0, 'resets': server.iso(self.wb), 'pace': None,
                                                               'lasts': None}, 'scoped': [], 'spend': None})]
        self.live(age=60, week=55.0)
        for tick in range(2):
            server.apply_rows(st, rows)
            server.apply_live_numbers(st, self.now)
            self.assertEqual(self.shown(st), (20.0, 55.0), tick)
        self.live(age=20, week=57.0)
        server.apply_rows(st, rows)
        server.apply_live_numbers(st, self.now)
        self.assertEqual(self.shown(st), (20.0, 57.0))
        shutil.rmtree(statusline.live_dir())
        server.apply_rows(st, rows)
        server.apply_live_numbers(st, self.now)
        self.assertEqual(st['accounts'][A]['usage_at'], self.now - 1200)     # back to the engine's last reading
        self.assertEqual(st['accounts'][A]['usage_status'], 'error')


class LiveRecordProfile(unittest.TestCase):
    """statusline.py marks a record from a Claude Code on a profile of its own (CLAUDE_CONFIG_DIR away from ~/.claude:
    `switchboard run NAME`, a folder tied to an account, a profile you made), which may be another account."""

    DATA = {'session_id': 's', 'rate_limits': {'seven_day': {'used_percentage': 5, 'resets_at': 2e9}}}

    def test_only_a_profile_of_its_own_is_marked(self):
        home = os.path.expanduser('~')
        rec = lambda env: statusline.live_record(self.DATA, {}, 1.0, env=env)   # noqa: E731
        self.assertNotIn('own_profile', rec({}))
        self.assertNotIn('own_profile', rec({'CLAUDE_CONFIG_DIR': ''}))
        self.assertNotIn('own_profile', rec({'CLAUDE_CONFIG_DIR': os.path.join(home, '.claude')}))
        self.assertNotIn('own_profile', rec({'CLAUDE_CONFIG_DIR': os.path.join(home, '.claude', '')}))
        self.assertTrue(rec({'CLAUDE_CONFIG_DIR': os.path.join(home, '.switchboard', 'run', 'acct-2')})['own_profile'])
        self.assertTrue(rec({'CLAUDE_CONFIG_DIR': os.path.join(home, '.claude-work')})['own_profile'])

    def test_the_command_marks_it_from_its_own_environment(self):
        tmp = tempfile.mkdtemp(prefix='switchboard-server-')
        self.addCleanup(shutil.rmtree, tmp, True)
        with mock.patch.dict(os.environ, {'SWITCHBOARD_HOME': tmp}):
            statusline.main(stdin=io.BytesIO(json.dumps(self.DATA).encode()), stdout=io.StringIO(),
                            env={'CLAUDE_CONFIG_DIR': os.path.join(tmp, 'elsewhere')}, now=time.time())
            with open(os.path.join(tmp, 'live', 's.json')) as f:
                self.assertTrue(json.load(f)['own_profile'])


# ---------- nothing leaves the Mac ----------

SOURCE = os.path.realpath(os.path.join(HERE, '..', 'switchboard'))
URL_RE = re.compile(r'https?://([A-Za-z0-9._%-]+)')
ANTHROPIC = ('anthropic.com', 'claude.ai', 'claude.com')                    # and every address under them
GITHUB_AND_PYPI = ('github.com', 'raw.githubusercontent.com', 'objects.githubusercontent.com', 'codeload.github.com',
                   'pypi.org', 'files.pythonhosted.org')                    # the installer's and update's downloads
KEY_PROVIDERS = ('openrouter.ai',)                                          # the gateway's, besides Anthropic's API
THIS_MAC = ('localhost', '127.0.0.1')
# Written as text, never fetched: the SVG namespace in the tab icon (the page's and the go page's), the font
# license's own address, a comment that names the NO_COLOR convention. Each only in its own files.
# chromewebstore.google.com: the Claude extension's page, opened in the person's own browser profile from Chrome
# setup; Switchboard never fetches it.
NAMED_ONLY = {'www.w3.org': ('web/index.html', 'web/go.html'), 'openfontlicense.org': ('web/Geist-OFL.txt',),
              'no-color.org': ('statusline.py',), 'chromewebstore.google.com': ('browser.py',),
              'openai.com': ('skill/reference/codex.md', 'skill/reference/what-it-does-not-do.md')}   # its terms, a link to read
# Not addresses: the gateway's own loopback address filled in at run time, and a refusal that shows the form.
PLACEHOLDERS = {('gateway.py', '%s'): 'LOOPBACK', ('keys.py', 'host'): 'SwitchError'}
# Fetched only when a pack is turned on and its plan agreed to: Node's own release, pinned with its checksum;
# and claude-mem's npm package, checked against npm's pinned integrity before it runs.
PACK_DOWNLOADS = {'nodejs.org': 'packs/pins.json', 'registry.npmjs.org': 'packs/pins.json'}


def allowed(host):
    return host in THIS_MAC or host in GITHUB_AND_PYPI or host in KEY_PROVIDERS or \
        any(host == d or host.endswith('.' + d) for d in ANTHROPIC)


def addresses():
    """[(file relative to switchboard/, line number, host, line)] for every http(s) address written in the code and
    the page."""
    out = []
    for folder, dirs, names in os.walk(SOURCE):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for name in sorted(names):
            path = os.path.join(folder, name)
            try:
                with open(path, encoding='utf-8') as f:
                    text = f.read()
            except (OSError, UnicodeDecodeError):
                continue                               # no text, no address
            rel = os.path.relpath(path, SOURCE)
            for n, line in enumerate(text.replace('\\.', '.').splitlines(), 1):   # a pattern's escaped dots
                out.extend((rel, n, host.lower(), line) for host in URL_RE.findall(line))
    return out


# The socket guard: while on, a connection, a send or a name lookup to anything but this Mac is refused (and noted),
# before it happens. Audit hooks see every socket this process opens, whichever module opens it.
GUARD = {'on': False, 'refused': [], 'ran': []}


def _this_mac(host):
    if isinstance(host, bytes):
        host = host.decode('ascii', 'replace')
    return host is None or (isinstance(host, str) and (host in ('localhost', '::1') or host.startswith('127.')))


def _net_guard(event, args):
    if not GUARD['on']:
        return
    if event in ('socket.connect', 'socket.sendto', 'socket.sendmsg'):
        address = args[1] if len(args) > 1 else None
        if isinstance(address, (str, bytes)) or address is None:
            return                                     # a Unix socket: a file on this Mac
        if not _this_mac(address[0] if isinstance(address, tuple) and address else '?'):
            GUARD['refused'].append((event, repr(address)))
            raise ConnectionRefusedError('Refused in tests: %s %r' % (event, address))
    elif event in ('socket.getaddrinfo', 'socket.gethostbyname', 'socket.gethostbyaddr'):
        if args and not _this_mac(args[0]):
            GUARD['refused'].append((event, repr(args[0])))
            raise OSError('Refused in tests: %s %r' % (event, args[0]))
    elif event == 'subprocess.Popen':
        GUARD['ran'].append(args[1] if len(args) > 1 else args)


sys.addaudithook(_net_guard)


@contextlib.contextmanager
def guarded():
    GUARD.update(on=True, refused=[], ran=[])
    try:
        yield GUARD
    finally:
        GUARD['on'] = False


class NothingLeavesTheMac(Sandbox):
    def test_every_address_in_the_code_and_the_page_is_on_the_allow_list(self):
        found = addresses()
        hosts = set(h for _, _, h, _ in found)
        self.assertTrue({'claude.ai', 'api.anthropic.com', 'openrouter.ai', 'localhost'} <= hosts, hosts)   # it looked
        wrong = ['%s:%d %s' % (rel, n, host) for rel, n, host, line in found
                 if not (allowed(host) or rel in NAMED_ONLY.get(host, ()) or PACK_DOWNLOADS.get(host) == rel
                         or ((rel, host) in PLACEHOLDERS and PLACEHOLDERS[(rel, host)] in line))]
        self.assertEqual(wrong, [])

    def test_the_gateways_key_providers_are_the_allowed_ones(self):
        from switchboard import keys
        from urllib.parse import urlsplit
        hosts = set(urlsplit(base).hostname for base, _ in keys.KINDS.values() if base)
        self.assertEqual(hosts, {'api.anthropic.com', 'openrouter.ai'})
        self.assertTrue(all(allowed(h) for h in hosts))

    def test_the_guard_refuses_before_anything_is_sent(self):
        s = socket.socket()
        self.addCleanup(s.close)
        with guarded() as g:
            with self.assertRaises(OSError):
                s.connect(('203.0.113.7', 443))        # a documentation-only address: refused before the call
            with self.assertRaises(OSError):
                socket.getaddrinfo('example.com', 443)
            socket.getaddrinfo('localhost', 443)       # this Mac is fine
        self.assertEqual([e for e, _ in g['refused']], ['socket.connect', 'socket.getaddrinfo'])

    def test_a_tick_a_render_and_the_usage_reader_reach_nothing_but_this_mac(self):
        now = time.time()
        iso = test_app.iso

        def usage(five, five_at, week, week_at):
            return {'session': {'pct': five, 'resets': iso(five_at), 'pace': None, 'lasts': None},
                    'weekly': {'pct': week, 'resets': iso(week_at), 'pace': None, 'lasts': None}, 'scoped': [],
                    'spend': None}
        rows = [engine._row(id='acct-1', number=1, email=A, active=True, source='endpoint', usage_at=now - 5,
                            usage=usage(20, now + H, 40, now + D)),
                engine._row(id='acct-2', number=2, email=B, source='endpoint', usage_at=now - 5,
                            usage=usage(0, now + 2 * H, 10, now + 2 * D))]
        install(self)                                  # the engine itself (accounts.api) is a fake: nothing real
        self.patch(server.engine, 'listing', lambda: {'rows': [dict(r) for r in rows], 'live': None, 'disagree': False})
        self.patch(server.engine, 'foreign_switcher', lambda folder=None: None)
        self.patch(server.engine, 'auto_settings', lambda: {'available': True, 'service': True, 'enabled': True,
                                                            'session_at': 90, 'weekly_at': 95, 'model_at': 95,
                                                            'models': []})
        self.patch(server, 'NOTIFY_RUN', lambda cmd: 0)          # a notification would be a child process
        shutil.copytree(test_app.FIXTURE, os.path.join(self.home, '.claude', 'projects'))   # history to read
        # The made-up history ends Nov 11 2026, so the reader looks at the week before test_app.FIXTURE_NOW instead
        # of the clock's week (which would leave the history behind). The folder it reads and its cache stay real.
        self.patch(server.usage, 'summarize', lambda root, since, until, **k: test_app.REAL(
            root, test_app.FIXTURE_NOW - 7 * D, test_app.FIXTURE_NOW, **k))
        with mock.patch.dict(server.USAGE, {'page': None, 'at': 0, 'running': False}), guarded() as g:
            server.tick()
            view = server.view()
            server.read_usage()
            page = server.USAGE['page']
            with mock.patch.dict(os.environ, {'SWITCHBOARD_PORT': '0'}):
                httpd = HTTPServer(('127.0.0.1', 0), server.Handler)   # and the page itself, asked over loopback
                threading.Thread(target=httpd.serve_forever, daemon=True).start()
                try:
                    os.environ['SWITCHBOARD_PORT'] = str(httpd.server_address[1])
                    opener = __import__('urllib.request').request.build_opener(
                        __import__('urllib.request').request.ProxyHandler({}))
                    with opener.open('http://127.0.0.1:%d/api/state' % httpd.server_address[1], timeout=10) as r:
                        state = json.load(r)
                finally:
                    httpd.shutdown()
                    httpd.server_close()
        self.assertEqual(g['refused'], [])
        self.assertEqual(g['ran'], [])                 # no child process either: nothing that could reach out for it
        self.assertEqual([a['email'] for a in view['accounts']], [A, B])           # each step really ran
        self.assertEqual(state['active'], A)
        self.assertFalse(page.get('failed'), page)
        self.assertTrue(page['cuts']['work'])


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class ProfileMenusInThePage(test_app.ChromePage):
    OPTIONS = """
      location.hash = '#settings'; await wait(900);
      done(all('#sec-settings .pairs select[data-change="profile"]').map((s) => [].slice.call(s.options).map((o) => o.textContent)));
"""

    def test_new_profile_is_offered_only_where_a_browser_can_make_one(self):
        demo._ready()
        menus = self.run_page(self.OPTIONS)
        self.assertEqual(len(menus), 5)
        self.assertTrue(all('New profile' in m for m in menus), menus)
        demo.FACTS['browser'] = dict(demo.FACTS['browser'], present=False)
        menus = self.run_page(self.OPTIONS)
        self.assertEqual(len(menus), 5)                               # each account's menu is still there...
        self.assertFalse([m for m in menus if 'New profile' in m], menus)   # ...with no New profile in it
        self.assertTrue(all('None' in m for m in menus), menus)


if __name__ == '__main__':
    unittest.main()
