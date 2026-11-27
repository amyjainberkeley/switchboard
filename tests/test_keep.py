import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# "Just log in". A login Claude Code signs in to is kept by itself; auto-switch turns on by itself
# once a second account is in; the order is what refills first; `switchboard auto [on|off]`.
#
# Made-up accounts only. The engine is a stand-in in this process (tests/fakes_accounts_api.py in accounts.api's place);
# automatic switching's loop, notifications and the page are stand-ins too. Every folder is a throwaway one.
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
sys.path.insert(0, ROOT)

from switchboard import __main__ as cli
from switchboard import connect, core, demo, engine, firstrun, notify_hooks, server, statusline
from switchboard.accounts import api
from fakes_accounts_api import FakeAccounts, install
import test_app  # its headless-Chrome helper; it has no tests of its own

A, B, C = 'maya@northwind.example', 'maya@ivy.example', 'ana@harbor.example'
ORG = {A: 'org-northwind', B: 'org-ivy', C: 'org-harbor'}
AUTO_ON = ('Auto-switch is on: Claude Code moves to another account before a limit. Turn it off with '
           'switchboard auto off.')


def keep_module():
    """switchboard.keep, imported when a test needs it, so that each test that needs
    it fails on its own instead of the whole file failing to load."""
    from switchboard import keep
    return keep


def iso_in(seconds):
    return datetime.fromtimestamp(time.time() + seconds, timezone.utc).isoformat()


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


class FakeEngine(FakeAccounts):
    """The account engine (accounts.api) in this process, with this file's own helpers: accounts in the person's
    order; `add` (api.add_current) keeps the account Claude Code's config names, or fails with `add_fails` (a
    sentence, as the engine says it). Each add is noted as ['add'] in calls."""

    def __init__(self):
        FakeAccounts.__init__(self)
        self.add_fails = None

    def hold(self, email, status='ok', org=None):
        return FakeAccounts.hold(self, email, ORG.get(email, '') if org is None else org, status=status)

    def emails(self):
        return [r['email'] for r in self.saved]

    def strategy(self):
        return self.auto.get('strategy')

    def add_current(self):
        self.calls.append(['add'])
        if self.add_fails:
            raise api.AccountsError('keychain_locked', self.add_fails)
        b = self.block()
        email = (b.get('emailAddress') or '').lower()
        if not email:
            raise api.AccountsError('unreadable_live', 'Claude Code is not signed in. Run /login in Claude Code first.')
        aid = FakeAccounts.hold(self, email, b.get('organizationUuid') or '')
        return [a for a in self.accounts() if a.id == aid][0]


class FakeHost(object):
    """Stands in for the automatic-switching loop's host: it never starts a process."""

    def __init__(self, fake):
        self.fake, self.ensured = fake, 0

    def wanted(self):
        return self.fake.auto.get('enabled') is True

    def ensure(self, now=None):
        self.ensured += 1

    def status(self, wanted=None):
        w = self.wanted()
        return {'wanted': w, 'running': w, 'on': w, 'starting': False, 'last_pass': 1.0 if w else None, 'problem': None,
                'stage': None, 'waiting_for': None, 'restarted': None}

    def running(self):
        return self.wanted()

    def stop(self, wait=30):
        pass

    def recorded_pid(self):
        return None


class Page(ThreadingHTTPServer):
    """A stand-in for the running page: GET /api/state answers a small state; each POST is noted, handed to
    `on_post`, and answered ok."""

    def __init__(self, on_post=None):
        ThreadingHTTPServer.__init__(self, ('127.0.0.1', 0), PageHandler)
        self.posts, self.on_post = [], on_post
        threading.Thread(target=self.serve_forever, daemon=True).start()

    def stop(self):
        self.shutdown()
        self.server_close()


class PageHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):              # a page of this test's data folder (core.folder_mark): a change may go to it
        self.send({'accounts': [], 'setup': {}, 'folder': core.folder_mark()})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
        self.server.posts.append((self.path, body))
        if self.server.on_post:
            self.server.on_post(self.path, body)
        self.send({'ok': True})


class FakeTty(object):
    """A terminal that answers from a list; what it was asked is kept."""

    def __init__(self, answers):
        self.answers, self.asked = list(answers), []
        self.writer = io.StringIO()

    def ask(self, text):
        sys.stdout.write(text)
        self.asked.append(text)
        if not self.answers:
            raise EOFError
        said = self.answers.pop(0)
        sys.stdout.write(said + '\n')
        return said.strip().lower()

    yes = cli.Tty.yes


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-keep-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, 'home')
        self.data = os.path.join(self.tmp, 'data')
        os.makedirs(os.path.join(self.home, '.claude'))
        env = mock.patch.dict(os.environ, {'HOME': self.home, 'SWITCHBOARD_HOME': self.data,
                                           'CLAUDE_CONFIG_DIR': os.path.join(self.home, '.claude'),
                                           'SWITCHBOARD_PORT': str(free_port()), 'TZ': 'America/Los_Angeles'})
        env.start()
        self.addCleanup(env.stop)
        for k in ('SWITCHBOARD_ENGINE', 'SWITCHBOARD_STATE', 'SWITCHBOARD_NOTIFY_STATE', 'NO_COLOR'):
            os.environ.pop(k, None)
        time.tzset()
        self.addCleanup(time.tzset)
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = False
        self.fake = install(self, FakeEngine())
        self.host = FakeHost(self.fake)
        self.shown = []
        self.page_starts = []                   # each time the first run asked launchd to start the page's job
        for target, name, value in ((engine, 'HOST', self.host), (server, 'NOTIFY_RUN', self.note),
                                    (firstrun, 'start_page', lambda: self.page_starts.append('job') or 'job'),
                                    (server, 'tell', lambda *a, **k: None),
                                    (server.artifacts, 'refresh', lambda: None),
                                    (connect, 'claude_bin', lambda: '/made/up/claude'),
                                    (cli, '_sleep', lambda s: None),
                                    (notify_hooks, 'MANAGED', os.path.join(self.tmp, 'managed')),   # none there
                                    (statusline, 'precompile', lambda *a, **k: None),    # no bytecode in the source
                                    (notify_hooks, 'precompile', lambda *a, **k: None),
                                    (engine, 'foreign_switcher', lambda folder=None: None)):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        server.WAKE.clear()
        self.addCleanup(server.WAKE.clear)
        try:
            k = keep_module()
            k.reset()
            self.addCleanup(k.reset)
        except ImportError:
            pass

    # ---- helpers ----

    def note(self, cmd):
        self.shown.append(cmd)
        return 0

    def notes(self):
        """(title, body) of each notification that would have been shown."""
        return [(c[-2], c[-1]) for c in self.shown]

    def signed_in(self, email):
        with open(core.claude_json(), 'w') as f:
            json.dump({'oauthAccount': {'emailAddress': email, 'organizationUuid': ORG.get(email, '')}}, f)

    def signed_out(self):
        try:
            os.remove(core.claude_json())
        except FileNotFoundError:
            pass

    def look(self, now):
        """One background tick, as the page's loop runs it: the engine's rows read, then what follows them."""
        looked = server.tick()
        server.keep_tick(looked, now=now)
        return looked

    def adds(self):
        return self.fake.calls.count(['add'])

    def events(self):
        return [e['text'] for e in core.load()['events']]

    def kept_events(self):
        return [e for e in self.events() if e.startswith('Kept ')]

    def run_cli(self, *args, tty=None, terminal=False):
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(cli, 'open_tty', lambda: tty), \
                mock.patch.object(cli, 'terminal', lambda: terminal):
            code = cli.main(list(args))
        return code, out.getvalue()

    def job(self):
        """Switchboard's background job is set up (it starts the page at login)."""
        agents = os.path.join(self.home, 'Library', 'LaunchAgents')
        os.makedirs(agents, exist_ok=True)
        open(os.path.join(agents, cli.AGENT + '.plist'), 'w').close()

    def start_page(self, on_post=None):
        page = Page(on_post=on_post)
        self.addCleanup(page.stop)
        os.environ['SWITCHBOARD_PORT'] = str(page.server_address[1])
        return page


# ---------- 1. every login is kept by itself ----------

class KeptByItself(Sandbox):
    def setUp(self):
        super().setUp()
        core.set_setting('auto_set_by_you', True)       # these tests are about keeping; auto-switch has its own

    def test_kept_after_two_looks_at_least_5_s_apart_and_said_once(self):
        self.fake.hold(A)
        self.signed_in(B)                                       # /login as another account in Claude Code
        t = 1000.0
        self.look(t)
        self.assertEqual(self.adds(), 0)                        # one look is not enough
        self.look(t + 2)
        self.assertEqual(self.adds(), 0)                        # two, but under 5 s apart
        self.look(t + 6)
        self.assertEqual(self.adds(), 1)
        self.assertEqual(self.fake.emails(), [A, B])
        self.assertEqual(self.kept_events(), ['Kept maya@ivy.example · 2 accounts'])
        self.assertEqual(self.notes(), [('Switchboard', 'Kept maya@ivy.example · 2 accounts')])
        self.assertTrue(server.WAKE.is_set())                   # the page reads the accounts again at once
        self.look(t + 70)
        self.look(t + 140)
        self.assertEqual((self.adds(), len(self.kept_events()), len(self.notes())), (1, 1, 1))

    def test_the_two_looks_must_be_in_a_row(self):
        self.fake.hold(A)
        self.signed_in(B)
        t = 1000.0
        self.look(t)
        self.signed_in(A)                                       # back on an account the engine holds
        self.look(t + 6)
        self.signed_in(B)
        self.look(t + 12)                                       # seen again, but not on the look before
        self.assertEqual(self.adds(), 0)
        self.look(t + 18)
        self.assertEqual(self.adds(), 1)

    def test_the_first_login_too_with_no_account_yet(self):
        self.signed_in(A)
        t = 1000.0
        self.look(t)
        self.look(t + 6)
        self.assertEqual(self.fake.emails(), [A])
        self.assertEqual(self.notes(), [('Switchboard', 'Kept maya@northwind.example · 1 account')])

    def test_the_same_email_in_another_organization_is_another_account(self):
        self.fake.hold(A)
        with open(core.claude_json(), 'w') as f:                # the same email, signed in to another organization
            json.dump({'oauthAccount': {'emailAddress': A, 'organizationUuid': 'org-team'}}, f)
        self.look(1000.0)
        self.look(1006.0)
        self.assertEqual(self.adds(), 1)
        self.fake.calls[:] = []
        self.signed_in(A)                                       # the one it holds: nothing to keep
        self.look(1100.0)
        self.look(1106.0)
        self.assertEqual(self.adds(), 0)

    def test_nothing_without_a_login_or_rows_or_in_the_demo(self):
        self.signed_out()
        self.look(1000.0)
        self.look(1006.0)
        self.assertEqual(self.adds(), 0)
        self.signed_in(B)
        server.keep_tick({'rows': None}, now=1100.0)            # the engine did not answer: nothing is kept
        server.keep_tick({'rows': None}, now=1106.0)
        self.assertEqual(self.adds(), 0)
        core.DEMO = True
        server.keep_tick({'rows': []}, now=1200.0)
        server.keep_tick({'rows': []}, now=1206.0)
        self.assertEqual(self.adds(), 0)

    def test_a_failure_waits_5_minutes_and_is_said_once(self):
        self.fake.hold(A)
        self.signed_in(B)
        self.fake.add_fails = "macOS's Keychain is locked. Unlock the Mac, then try again."
        t = 1000.0
        self.look(t)
        self.look(t + 6)
        self.assertEqual(self.adds(), 1)
        said = [e for e in self.events() if e.startswith('Could not keep')]
        self.assertEqual(said, ["Could not keep maya@ivy.example: macOS's Keychain is locked. Trying again in 5 "
                                'minutes.'])
        for later in (t + 66, t + 200, t + 305):                 # within 5 minutes of the failure: not tried
            self.look(later)
        self.assertEqual(self.adds(), 1)
        self.look(t + 307)
        self.assertEqual(self.adds(), 2)                        # tried again, failed again: not said again
        self.assertEqual(len([e for e in self.events() if e.startswith('Could not keep')]), 1)
        self.assertEqual(self.notes(), [])                      # a failure is a line on the page, not a notification
        self.fake.add_fails = None
        self.look(t + 620)
        self.assertEqual(self.adds(), 3)
        self.assertEqual(self.kept_events(), ['Kept maya@ivy.example · 2 accounts'])

    def test_an_unexpected_failure_also_waits_so_the_loop_never_spins(self):
        k = keep_module()
        self.fake.hold(A)
        self.signed_in(B)
        self.look(1000.0)
        with mock.patch.object(engine, 'add_current', mock.Mock(side_effect=RuntimeError('made-up'))):
            self.look(1006.0)
        self.assertFalse(k.due(1007.0))                         # no look again until RETRY has passed
        self.assertEqual(k.wait_for(20, 1007.0), 20)
        self.assertEqual([e for e in self.events() if e.startswith('Could not keep')],
                         ['Could not keep maya@ivy.example: Switchboard could not ask the engine. Trying again in 5 '
                          'minutes.'])
        self.assertTrue(k.due(1310.0))

    def test_with_notifications_off_only_the_line(self):
        core.set_setting('notify', False)
        self.fake.hold(A)
        self.signed_in(B)
        self.look(1000.0)
        self.look(1006.0)
        self.assertEqual((self.adds(), len(self.kept_events()), self.notes()), (1, 1, []))


# ---------- let go: an account removed in Switchboard ----------

class LetGo(Sandbox):
    def setUp(self):
        super().setUp()
        core.set_setting('auto_set_by_you', True)
        self.fake.hold(A)
        self.fake.hold(B)
        self.signed_in(B)
        self.look(900.0)                                        # the page knows both

    def remove(self, email):
        self.assertEqual(server.act('/api/remove', {'email': email})['state'], 'confirm')
        server.act('/api/remove', {'email': email, 'confirm': True})

    def let_go(self):
        return core.settings().get('let_go') or []

    def test_removed_here_it_is_never_kept_by_itself(self):
        self.remove(B)                                          # Claude Code stays signed in to it
        self.assertEqual(self.fake.emails(), [A])
        self.assertEqual(self.let_go(), [[B, ORG[B]]])          # the account: its email and organization
        for t in (1000.0, 1006.0, 1100.0, 1500.0):
            self.look(t)
        self.assertEqual(self.adds(), 0)

    def test_switchboard_add_keeps_it_again_and_forgets_the_let_go(self):
        self.remove(B)
        code, out = self.run_cli('add')
        self.assertEqual(code, 0)
        self.assertIn('Saved maya@ivy.example', out)
        self.assertEqual((self.fake.emails(), self.let_go()), ([A, B], []))

    def test_the_pages_add_keeps_it_again_and_forgets_the_let_go(self):
        self.remove(B)
        server.act('/api/engine', {'add': True})
        self.assertEqual((self.fake.emails(), self.let_go()), ([A, B], []))
        self.remove(B)
        self.look(2000.0)
        self.look(2006.0)
        self.assertEqual(self.fake.emails(), [A])               # let go again, so not kept again by itself

    def test_bare_switchboard_does_not_keep_one_you_removed(self):
        self.remove(B)
        code, out = self.run_cli('status')
        self.assertEqual(code, 0)
        self.assertNotIn('Kept', out)
        self.assertEqual(self.fake.emails(), [A])

    def test_removing_everything_lets_every_one_go(self):
        server.act('/api/purge', {'typed': 'remove everything'})
        self.assertEqual(self.fake.emails(), [])
        self.assertEqual(sorted(self.let_go()), sorted([[A, ORG[A]], [B, ORG[B]]]))
        for t in (1000.0, 1006.0, 1012.0):
            self.look(t)
        self.assertEqual(self.adds(), 0)

    def test_switchboard_remove_lets_it_go(self):
        with mock.patch.object(cli, 'terminal', lambda: True), mock.patch('builtins.input', return_value='n'):
            self.assertEqual(cli.main(['remove', B]), 1)        # the person said no: nothing removed
        self.assertEqual(self.let_go(), [])
        self.assertEqual(self.run_cli('remove', B, '--yes')[0], 0)
        self.assertEqual(self.fake.emails(), [A])
        self.assertEqual(self.let_go(), [[B, ORG[B]]])
        self.look(1000.0)
        self.look(1006.0)
        self.assertEqual(self.adds(), 0)


# ---------- 2. auto-switch on by itself ----------

class AutoOnByItself(Sandbox):
    def turned_on(self):
        return self.fake.auto.get('enabled') is True

    def test_on_with_two_accounts_that_can_be_switched_to_and_said_once(self):
        self.fake.hold(A)
        self.fake.hold(B)
        self.signed_in(A)
        self.look(1000.0)
        self.assertTrue(self.turned_on())
        self.assertGreaterEqual(self.host.ensured, 1)           # started the way the page's switch starts it
        self.assertEqual(self.fake.strategy(), 'refills-first')
        self.assertEqual([e for e in self.events() if e.startswith('Auto-switch is on')], [AUTO_ON])
        self.assertEqual(self.notes(), [('Switchboard', AUTO_ON)])
        self.look(1060.0)
        self.assertEqual(len([e for e in self.events() if e.startswith('Auto-switch is on')]), 1)
        self.assertEqual(len(self.notes()), 1)

    def test_not_with_one_account_that_can_be_switched_to(self):
        self.fake.hold(A)
        self.signed_in(A)
        self.look(1000.0)
        self.assertFalse(self.turned_on())
        b = self.fake.hold(B, status='needs_login')             # a second one that needs a fresh sign-in: not yet
        self.look(1060.0)
        self.assertFalse(self.turned_on())
        self.fake.remove(b)
        self.assertEqual(self.notes(), [])

    def test_not_while_another_program_switches_claude_codes_login(self):
        self.fake.hold(A)
        self.fake.hold(B)
        self.signed_in(A)
        with mock.patch.object(engine, 'foreign_switcher', lambda folder=None: 'com.example.claude-switch'):
            self.look(1000.0)
        self.assertFalse(self.turned_on())
        self.look(1060.0)                                       # once it is off, auto-switch goes on by itself
        self.assertTrue(self.turned_on())

    def test_when_the_second_account_is_kept_by_itself(self):
        self.fake.hold(A)
        self.signed_in(B)
        self.look(1000.0)
        self.assertFalse(self.turned_on())
        self.look(1006.0)                                       # B kept: two accounts now
        self.assertTrue(self.turned_on())
        self.assertEqual(self.notes(), [('Switchboard', 'Kept maya@ivy.example · 2 accounts'),
                                        ('Switchboard', AUTO_ON)])

    def test_never_again_once_you_turned_it_off_on_the_page_or_the_menu_bar(self):
        self.fake.hold(A)
        self.fake.hold(B)
        self.signed_in(A)
        self.look(1000.0)
        server.act('/api/auto', {'enabled': False})             # the page's switch, and the menu bar's (the same POST)
        self.assertIs(core.settings().get('auto_set_by_you'), True)
        self.fake.hold(C)
        self.look(1060.0)
        self.look(1120.0)
        self.assertFalse(self.turned_on())

    def test_never_once_you_set_it_before_a_second_account_came(self):
        self.fake.hold(A)
        self.signed_in(A)
        server.act('/api/auto', {'enabled': False})
        self.fake.hold(B)
        self.look(1000.0)
        self.assertFalse(self.turned_on())
        self.assertEqual(self.notes(), [])

    def test_never_once_you_turned_it_off_in_the_terminal(self):
        self.fake.hold(A)
        self.fake.hold(B)
        self.signed_in(A)
        code, out = self.run_cli('auto', 'off')
        self.assertEqual(code, 0)
        self.assertIs(core.settings().get('auto_set_by_you'), True)
        self.look(1000.0)
        self.assertFalse(self.turned_on())

    def test_changing_the_lines_or_the_order_is_not_turning_it_on_or_off(self):
        self.fake.hold(A)
        self.signed_in(A)
        server.act('/api/auto', {'session_at': 85, 'strategy': 'most-room'})
        self.assertIsNone(core.settings().get('auto_set_by_you'))
        self.fake.hold(B)
        self.look(1000.0)
        self.assertTrue(self.turned_on())
        self.assertEqual(self.fake.strategy(), 'most-room')      # the order you picked stays

# ---------- 3. what refills first, by default ----------

class RefillsFirst(Sandbox):
    def test_a_fresh_install_runs_on_what_refills_first(self):
        self.signed_in(A)
        code, out = self.run_cli('setup')                       # the installer's first run, without a terminal
        self.assertEqual(code, 0)
        self.assertEqual(self.fake.emails(), [A])
        self.assertEqual(engine.auto_settings()['strategy'], 'refills-first')    # what the page shows
        self.assertEqual(engine.DEFAULT_STRATEGY, 'refills-first')
        self.assertFalse([c for c in self.fake.calls if c[0] == 'set_auto' and 'strategy' in c[1]])   # nothing written

    def test_an_order_you_picked_on_the_page_is_never_replaced(self):
        self.signed_in(A)
        self.run_cli('setup')
        server.act('/api/auto', {'strategy': 'most-room'})
        self.fake.hold(B)
        self.look(1000.0)                                       # auto-switch on by itself
        self.assertIs(self.fake.auto['enabled'], True)
        self.assertEqual(self.fake.strategy(), 'most-room')


# ---------- 4. switchboard auto [on|off] ----------

class AutoCommand(Sandbox):
    def setUp(self):
        super().setUp()
        self.job()                                              # the page runs under the background job

    def test_bare_says_whether_it_is_on_in_one_line(self):
        self.assertEqual(self.run_cli('auto'), (0, 'Auto-switch is off. Turn it on: switchboard auto on\n'))
        self.fake.auto.update(enabled=True, strategy='refills-first', weekly_at=95)
        self.assertEqual(self.run_cli('auto'), (0, 'Auto-switch is on: at 90% 5-hour · 95% weekly, using what refills '
                                                   'first.\n'))
        self.assertIsNone(core.settings().get('auto_set_by_you'))      # a look changes nothing

    def test_each_order_in_plain_words(self):
        self.fake.auto['enabled'] = True
        for strategy, words in (('most-room', 'using the one with the most room'),
                                ('refills-first', 'using what refills first')):
            with self.subTest(strategy=strategy):
                self.fake.auto['strategy'] = strategy
                self.assertEqual(self.run_cli('auto')[1],
                                 'Auto-switch is on: at 90% 5-hour · 95% weekly, ' + words + '.\n')

    def test_on_and_off_without_the_page_change_it_here_and_mark_it_yours(self):
        code, out = self.run_cli('auto', 'on')
        self.assertEqual((code, out), (0, 'Auto-switch is on: at 90% 5-hour · 95% weekly, using what refills '
                                          'first.\n'))
        self.assertIs(self.fake.auto['enabled'], True)
        self.assertIs(core.settings().get('auto_set_by_you'), True)
        self.assertEqual(self.host.ensured, 0)                  # the loop runs only inside the page, never here
        code, out = self.run_cli('auto', 'off')                 # yours now, so it stays off
        self.assertEqual((code, out), (0, 'Auto-switch is off until you turn it on: switchboard auto on\n'))
        self.assertIs(self.fake.auto['enabled'], False)

    def test_on_and_off_go_through_the_running_page(self):
        page = self.start_page(on_post=lambda path, body: self.fake.auto.update(enabled=body.get('enabled') is True))
        code, out = self.run_cli('auto', 'on')
        self.assertEqual(page.posts, [('/api/auto', {'enabled': True})])
        self.assertTrue(out.startswith('Auto-switch is on: at 90% 5-hour'), out)
        self.run_cli('auto', 'off')
        self.assertEqual(page.posts[-1], ('/api/auto', {'enabled': False}))

    def test_without_the_page_or_its_job_it_says_where_it_runs(self):
        os.remove(os.path.join(self.home, 'Library', 'LaunchAgents', cli.AGENT + '.plist'))
        code, out = self.run_cli('auto', 'on')
        self.assertEqual(out.splitlines()[1],
                         'It runs in the page, which is not running. Start it with: switchboard serve')

    def test_wrong_words_and_another_switcher(self):
        self.assertEqual(self.run_cli('auto', 'maybe')[0], 2)
        with mock.patch.object(engine, 'foreign_switcher', lambda folder=None: 'com.example.claude-switch'):
            with self.assertRaises(core.SwitchError) as e:
                self.run_cli('auto', 'on')
        self.assertIn('Another program is also switching', str(e.exception))
        self.assertIs(self.fake.auto['enabled'], False)

    def test_the_help_and_the_readme_name_it(self):
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main(['help', 'all'])
        self.assertIn('switchboard auto [on|off]', out.getvalue())
        with open(os.path.join(ROOT, 'README.md'), encoding='utf-8') as f:
            readme = f.read()
        commands = readme[readme.index('## Usage'):readme.index('## How fresh the numbers are')]
        self.assertIn('switchboard auto', commands)
        with open(os.path.join(ROOT, 'docs', 'commands.md'), encoding='utf-8') as f:
            self.assertIn('| `switchboard auto` |', f.read())          # and the reference


# ---------- 5. words: bare switchboard and status keep at once; the first run ----------

class InTheTerminal(Sandbox):
    def test_bare_switchboard_keeps_at_once_and_says_so_above_the_view(self):
        core.set_setting('auto_set_by_you', True)
        self.fake.hold(A)
        self.signed_in(B)
        code, out = self.run_cli()
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith('Kept maya@ivy.example.\n\nClaude Code is on maya@ivy.example'), out)
        self.assertEqual((self.adds(), self.fake.emails()), (1, [A, B]))
        code, out = self.run_cli('status')                      # already kept: nothing more
        self.assertFalse(out.startswith('Kept'), out)
        self.assertEqual(self.adds(), 1)

    def test_status_keeps_too_and_a_second_account_turns_auto_switch_on(self):
        self.fake.hold(A)
        self.signed_in(B)
        code, out = self.run_cli('status')
        self.assertTrue(out.startswith('Kept maya@ivy.example.\n%s\n\nClaude Code is on maya@ivy.example · '
                                       'auto-switch on' % AUTO_ON), out)
        self.assertIs(self.fake.auto['enabled'], True)
        self.assertIsNone(core.settings().get('auto_set_by_you'))
        self.assertEqual(self.host.ensured, 0)                  # the page starts it, never the terminal
        self.assertEqual(self.notes(), [])                      # said here, in the terminal

    def test_through_the_running_page_when_it_answers(self):
        core.set_setting('auto_set_by_you', True)
        self.fake.hold(A)
        self.signed_in(B)
        page = self.start_page(on_post=lambda path, body: path == '/api/engine' and engine.add_current())
        code, out = self.run_cli('status')
        self.assertEqual(page.posts[0], ('/api/engine', {'add': True, 'by': 'auto'}))   # kept by itself, for the history
        self.assertTrue(out.startswith('Kept maya@ivy.example.\n'), out)
        self.assertIn('2  maya@ivy.example', out)               # the view is read afresh, with it

    def test_a_failure_is_one_line_and_the_view_still_shows(self):
        core.set_setting('auto_set_by_you', True)
        self.fake.hold(A)
        self.signed_in(B)
        self.fake.add_fails = "macOS's Keychain is locked. Unlock the Mac, then try again."
        code, out = self.run_cli('status')
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("Could not keep maya@ivy.example: macOS's Keychain is locked. Unlock the Mac, "
                                       'then try again.\n'), out)
        self.assertIn('Claude Code is on maya@ivy.example', out)

    def test_the_first_run(self):
        self.job()
        self.signed_in(A)
        code, out = self.run_cli('setup', tty=FakeTty(['s']))
        self.assertEqual(code, 0, out)
        self.assertTrue(out.startswith('Switchboard setup.\n\n1 of 4  Your account\n'      # "See it move" is planned:
                                       # the job may start the page, so a second account may follow
                                       '  Claude Code is signed in as maya@northwind.example. Kept.\n'), out)
        self.assertNotIn('/login', out)                 # the other accounts sign in through the browser
        self.assertEqual(self.fake.emails(), [A])

    def test_the_first_run_without_the_page_names_doctor(self):
        self.signed_in(A)
        code, out = self.run_cli('setup', tty=FakeTty(['s']))
        self.assertIn('Accounts are added through the page, which is not running and did not start. Run switchboard '
                      'doctor', ' '.join(out.split()))
        self.assertNotIn('switchboard serve', out)
        self.assertTrue(out.endswith('1 Claude account. Type switchboard to see it.\n'), out)

    def test_one_account_in_the_view_points_at_add(self):
        self.job()
        core.set_setting('auto_set_by_you', True)
        self.fake.hold(A)
        self.signed_in(A)
        code, out = self.run_cli('status')
        self.assertTrue(out.endswith('\n\nAdd another account with switchboard add.\n'), out)

    def test_switchboard_add_turns_auto_switch_on_with_the_second_account(self):
        self.fake.hold(A)
        self.signed_in(B)
        code, out = self.run_cli('add')
        self.assertEqual(code, 0)
        self.assertIn('✓ Saved maya@ivy.example\n%s\n' % AUTO_ON, out)
        self.assertIs(self.fake.auto['enabled'], True)


# ---------- the page's first run, and the loop that looks again sooner ----------

class PageWords(unittest.TestCase):
    def test_the_first_run_form_has_the_one_quiet_line(self):
        with open(os.path.join(ROOT, 'switchboard', 'web', 'index.html'), encoding='utf-8') as f:
            html = f.read()
        body = html[html.index('function firstHTML()'):html.index('// ---------- drawing ----------')]
        self.assertIn("addForm('first') + '<p class=\"fr-line\">It signs in through your browser, on Anthropic’s own "
                      "page.</p>'", body)


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class PageFirstRunInChrome(test_app.ChromePage):
    def test_no_login_yet_shows_the_form_and_the_line_under_it(self):
        demo._ready()
        for a in demo.STATE['accounts'].values():
            a['connected'] = False                          # nothing added, and Claude Code signed in to nothing
        demo.FACTS.update(setup_hidden=False)
        dom = self.dom()
        main = dom[dom.find('<main'):dom.find('</main>')]
        self.assertIn('Add your first account', main)
        self.assertRegex(main, r'</form><p class="fr-line">It signs in through your browser, on Anthropic’s own '
                               r'page\.</p>')


class TheLoop(Sandbox):
    def test_a_new_login_wakes_the_loop_and_the_second_look_comes_soon(self):
        k = keep_module()
        core.set_setting('auto_set_by_you', True)
        self.fake.hold(A)
        self.signed_in(A)
        logins = [B]

        def mac_tick(now=None):                             # between two passes, /login as B in Claude Code
            if logins:
                self.signed_in(logins.pop())
        with mock.patch.object(server, 'mac_tick', mac_tick), mock.patch.object(server, 'MAC_TICK', 0.05), \
                mock.patch.object(server, 'TICK', 3600), mock.patch.object(k, 'KEEP_GAP', 0.2):
            started = time.time()
            server.loop(ticks=8)
        self.assertEqual(self.fake.emails(), [A, B])
        self.assertLess(time.time() - started, 30)

    def test_due_and_wait(self):
        k = keep_module()
        self.assertFalse(k.due(1000.0))
        self.assertEqual(k.wait_for(20, 1000.0), 20)
        k.SEEN.update(who=(B, ORG[B]), at=1000.0)
        self.assertFalse(k.due(1003.0))
        self.assertTrue(k.due(1005.0))
        self.assertAlmostEqual(k.wait_for(20, 1001.0), 4.5, places=3)
        self.assertEqual(k.wait_for(20, 1010.0), 0.5)           # overdue: look again at once (never a busy loop)

    def test_login_changed_reads_claude_codes_file_only_when_it_changed(self):
        k = keep_module()
        self.signed_in(A)
        self.assertFalse(k.login_changed())                     # the first look only remembers
        self.assertFalse(k.login_changed())
        self.signed_in(B)
        self.assertTrue(k.login_changed())
        self.assertFalse(k.login_changed())
        self.signed_out()
        self.assertFalse(k.login_changed())                     # signed out: nothing to keep, no early look


if __name__ == '__main__':
    unittest.main()
