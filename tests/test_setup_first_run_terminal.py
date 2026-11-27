import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The first run in the terminal (switchboard/firstrun.py), end to end on stand-ins:
#
# - `switchboard setup` numbers its steps "N of M", M counting only the steps this run shows.
# - Step 1 keeps the account Claude Code is on without a question.
# - Step 2 adds each other account through the browser, started here through the running page (POST /api/connect,
#   followed with /api/connect-look): kept, the same account again, a sign-in that did not finish, Ctrl-C while it
#   waits (the page's Cancel), the page not running. Codex is offered once when it is on this Mac, and not at all when
#   it is not.
# - Step 3 explains the link app before macOS asks, asks only after Enter, and says what the seat holds after:
#   "Use" or "Keep". It is left out when the link app is missing or already the default.
# - Step 4 names only what is on. Without a terminal nothing is asked and at most three lines are said.
# - `switchboard add` and `switchboard links on` at a terminal use the same steps; `switchboard help` fits on one
#   screen and names only commands that exist; no product message sends a person to /login to add an account.
#
# Made-up accounts only (*.example). The engine is tests/fakes_accounts_api.py; the page is a small HTTP server of this
# file on a port the system picks; the link app is a shell script in a throwaway home that answers --status and
# --make-default from files; macOS, the browser, launchd and the Keychain are never reached.
import io
import json
import os
import plistlib
import re
import sys
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
sys.path.insert(0, ROOT)

from switchboard import __main__ as cli
from switchboard import connect, core, firstrun, linkapp, server, term
from test_keep import A, B, C, FakeTty, Sandbox

CHROME_ID = 'com.google.Chrome'


class SigninPage(ThreadingHTTPServer):
    """The running page, as the first run talks to it. GET /api/state: the engine's accounts (with a plan word each)
    and this data folder's mark. POST /api/engine keeps the account Claude Code is on; /api/connect starts a sign-in
    (key 'new' or the email, a start time); /api/connect-look answers from `looks`, one per call, each a dict, the last
    one repeated; /api/cancel is noted. Each POST is noted in `posts`."""

    def __init__(self, test):
        ThreadingHTTPServer.__init__(self, ('127.0.0.1', 0), SigninHandler)
        self.test, self.posts, self.looks, self.signins, self.plans = test, [], [], [], {}
        self.needs = set()
        self.refuse = None
        self.started = 1000.25
        threading.Thread(target=self.serve_forever, daemon=True).start()

    def stop(self):
        self.shutdown()
        self.server_close()

    def state(self):
        accounts = []
        for i, r in enumerate(self.test.fake.saved):
            a = {'email': r['email'], 'slot': i + 1, 'unmanaged': False, 'needs_login': r['email'] in self.needs}
            if r['email'] in self.plans:
                a['plan_line'] = self.plans[r['email']]
            accounts.append(a)
        return {'accounts': accounts, 'folder': core.folder_mark(), 'demo': False,
                'browser': {'name': 'Google Chrome', 'present': True}}

    def sign_in(self, *looks, got=None):
        """The next sign-in: these looks while it is under way, then over; `got` the account it comes back as (None:
        not finished), kept by the engine when it ends."""
        self.signins.append((list(looks), got))


class SigninHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.send(self.server.state())

    def do_POST(self):
        page = self.server
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
        page.posts.append((self.path, body))
        if self.path == '/api/engine':
            page.test.fake.add_current()
            return self.send({'ok': True})
        if self.path == '/api/connect':
            page.started += 1
            looks, got = page.signins.pop(0) if page.signins else ([], None)
            page.looks = [dict(x, started=page.started) for x in looks]
            page.got = got
            return self.send({'ok': True, 'key': body.get('email') or 'new', 'started': page.started})
        if self.path == '/api/connect-look':
            if page.looks:
                look = page.looks.pop(0)
                if look.get('state') == 'over' and page.got:
                    page.test.fake.hold(page.got)       # the engine kept it (connect.collect)
                return self.send(dict(look, ok=True))
            return self.send({'ok': True, 'state': None})
        if self.path == '/api/switch':          # the page's switch_to: the engine moves Claude Code, or refuses
            if page.refuse:
                data = json.dumps({'error': page.refuse}).encode()
                self.send_response(409)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            page.test.fake.switch(body.get('email'))
            return self.send({'ok': True, 'note': None})
        if self.path == '/api/cancel':
            page.looks = [x for x in page.looks if x.get('state') in ('keeping', 'over')] \
                if page.looks[:1] and page.looks[0].get('state') == 'keeping' else []
            return self.send({'ok': True})
        self.send({'ok': True})


WAIT = {'state': 'waiting', 'default_browser': False}
WAIT_DEFAULT = {'state': 'waiting', 'default_browser': True}
KEEP = {'state': 'keeping'}


def over(got=None, ok=True, why=None):
    r = {'ok': ok}
    if got:
        r['got'] = got
    if why:
        r['why'] = why
    return {'state': 'over', 'result': r}


class Setup(Sandbox):
    """A first run on this Mac: Claude Code signed in as A, the page running on its own port, the link app
    installed with Google Chrome as the default browser, and no Codex."""

    def setUp(self):
        super().setUp()
        self.signed_in(A)
        self.page = SigninPage(self)
        self.addCleanup(self.page.stop)
        os.environ['SWITCHBOARD_PORT'] = str(self.page.server_address[1])
        self.apps = os.path.join(self.home, 'Applications')
        os.makedirs(self.apps)
        self.chrome = self.app('Google Chrome.app', CHROME_ID)
        self.seat_file = os.path.join(self.tmp, 'seat.txt')
        self.answer_file = os.path.join(self.tmp, 'answer.txt')
        self.made_default = os.path.join(self.tmp, 'asked.txt')
        self.links_app = None
        linkapp.CACHE.update(at=0, value=None, path=None)
        self.addCleanup(linkapp.CACHE.update, at=0, value=None, path=None)
        self.codex = None
        self.patch(firstrun, 'board', self.board)
        from switchboard.providers import cli as providers_cli
        self.patch(providers_cli, 'main', self.add_codex)
        self.link_app()

    def patch(self, target, name, value):
        p = mock.patch.object(target, name, value)
        p.start()
        self.addCleanup(p.stop)

    # ---- stand-ins ----

    def app(self, name, bundle_id, exe=None):
        path = os.path.join(self.apps, name)
        os.makedirs(os.path.join(path, 'Contents', 'MacOS'))
        with open(os.path.join(path, 'Contents', 'Info.plist'), 'wb') as f:
            plistlib.dump({'CFBundleIdentifier': bundle_id, 'CFBundleExecutable': 'SwitchboardLinks'}, f)
        if exe:
            p = os.path.join(path, 'Contents', 'MacOS', 'SwitchboardLinks')
            with open(p, 'w') as f:
                f.write(exe)
            os.chmod(p, 0o755)
        return path

    def link_app(self, answer='use', holder=None):
        """The link app: --status prints the seat's holder; --make-default notes that macOS was asked, and on "use"
        gives it the seat (and notes Chrome as where plain links go, as the real one does), else exits 1."""
        path = os.path.join(self.apps, 'Switchboard Links.app')
        with open(self.seat_file, 'w') as f:
            f.write((holder or self.chrome) + '\n')
        with open(self.answer_file, 'w') as f:
            f.write(answer)
        script = ('#!/bin/sh\n'
                  'case "$1" in\n'
                  '  --status) cat "%(seat)s" ;;\n'
                  '  --make-default)\n'
                  '    echo asked >> "%(asked)s"\n'
                  '    [ "$(cat "%(answer)s")" = crash ] && exit 137\n'
                  '    [ "$(cat "%(answer)s")" = slow ] && exit 2\n'
                  '    if [ "$(cat "%(answer)s")" = use ]; then\n'
                  '      echo "%(app)s" > "%(seat)s"; mkdir -p "%(data)s"; printf %(chrome)s > "%(data)s/previous-browser.txt"\n'
                  '      exit 0\n'
                  '    fi\n'
                  '    exit 1 ;;\n'
                  'esac\n') % {'seat': self.seat_file, 'asked': self.made_default, 'answer': self.answer_file,
                               'app': path, 'data': self.data, 'chrome': CHROME_ID}
        if not os.path.exists(path):
            self.links_app = self.app('Switchboard Links.app', linkapp.BUNDLE_ID, exe=script)
        return path

    def no_link_app(self):
        import shutil
        shutil.rmtree(os.path.join(self.apps, 'Switchboard Links.app'))

    def asked_macos(self):
        try:
            with open(self.made_default) as f:
                return len(f.read().splitlines())
        except OSError:
            return 0

    def with_codex(self):
        self.codex = 'here'

    def board(self):
        b = {'providers': [{'id': 'claude', 'name': 'Claude', 'account_list': True}], 'add': []}
        if self.codex == 'here':
            b['add'] = [{'id': 'codex', 'name': 'Codex', 'app': 'Codex', 'version': '0.169.1'}]
        elif self.codex == 'added':
            b['providers'].append({'id': 'codex', 'name': 'Codex', 'account_list': False})
        return b

    def add_codex(self, args, kit=None):
        """`switchboard add codex`, in the words the providers code prints (providers/cli.py, _add)."""
        from switchboard.providers import words
        self.assertEqual(args, ['add', 'codex'])
        self.codex = 'added'
        for line in (words.ADD_DONE.format(name='Codex', order='Claude, Codex'),
                     words.ADD_WILL_RUN.format(app='Codex', version='0.169.1', where='/opt/homebrew/bin/codex',
                                               name='Codex'),
                     words.ADD_SIGNED_IN):
            kit.out.write(line + '\n')
        return 0

    def setup(self, *answers, args=('setup',)):
        tty = FakeTty(list(answers))
        code, out = self.run_cli(*args, tty=tty)
        return code, out

    def interrupt_on(self, *calls):
        """cli._sleep raises KeyboardInterrupt on these calls (1 is the first), as Ctrl-C at that moment would."""
        n = {'i': 0}

        def sleep(seconds):
            n['i'] += 1
            if n['i'] in calls:
                raise KeyboardInterrupt
        self.patch(cli, '_sleep', sleep)

    def steps(self, out):
        return re.findall(r'^(\d+) of (\d+)  (.+)$', out, re.M)

    def posts(self, path):
        return [b for p, b in self.page.posts if p == path]

    def assertSaid(self, words, out):
        """`words` are in `out`, wherever the terminal's width broke its lines."""
        self.assertIn(' '.join(words.split()), ' '.join(out.split()))


FULL = '''Switchboard setup.

1 of 5  Your account
  Claude Code is signed in as maya@northwind.example. Kept.

2 of 5  Your other accounts
  Each signs in through your browser, on Anthropic's own page. Nothing is signed out by this step.
  Add a Claude account?  Enter opens your browser, s skips.  
  Waiting for your browser. Ctrl-C cancels.
  Your browser approved it. Keeping the login. Ctrl-C cancels.
  maya@ivy.example (Max 5x). Kept.
  Add another?  Enter opens your browser, s skips.  s
  Codex is on this Mac too. Add it to your list?  Enter adds it, s skips.  
  Added Codex. Your order: Claude, Codex.
  Switchboard will run Codex 0.169.1 (/opt/homebrew/bin/codex). Codex keeps its own sign-in;
  Switchboard never reads or keeps it.
  It looks signed in.

3 of 5  See it move
  Claude Code is on maya@northwind.example. Switchboard moves it at your lines, and you can move it
  any time.
  Move Claude Code to maya@ivy.example now?  Enter moves it, s skips.  
  Moved. Open chats follow within about 30 seconds. Back: switchboard switch maya@northwind.example

4 of 5  Links that open in the right account
  A claude.ai link opens only for the account that made it. Switchboard can open each claude.ai link
  in the browser profile of its owner. Every other link opens in Google Chrome exactly as it does
  now. For this, macOS asks one question about your "default web browser": choose
  "Use Switchboard Links".
  Undo at any time: switchboard links off
  Turn this on?  Enter shows macOS's question, s skips.  
  macOS is asking. Choose "Use Switchboard Links". If you chose Keep, press Ctrl-C.
  Done. claude.ai links open as their owner. Everything else still opens in Google Chrome.

5 of 5  Where to find it
  Claude Code   a status line with the account and its limits, and a notification when a long turn
                is done or it needs you
  The page      switchboard page   (every account, chat and artifact, at 127.0.0.1:%d)
  Auto-switch is on: at 90%% of the 5-hour limit or 95%% of the week, Claude Code moves to the next
  account.
  Changed in Claude Code's settings: status line (undo: switchboard statusline off), notifications
  (undo: switchboard notify off), session hooks (undo: switchboard hooks off).

2 Claude accounts and Codex. Type switchboard to see them.
'''


class TheTwoSentences(Setup):
    def test_step_two_promises_nothing_about_staying_signed_in(self):
        self.no_link_app()
        code, out = self.setup('s')
        self.assertIn("  Each signs in through your browser, on Anthropic's own page. Nothing is signed out by this "
                      "step.\n  Add a Claude account?", out)
        self.assertNotIn('stays signed in', out)


class TheWholeFirstRun(Setup):
    def test_one_account_one_added_codex_and_links(self):
        self.with_codex()
        self.page.plans[B] = 'Max 5x'
        self.page.sign_in(WAIT, WAIT, KEEP, over(B), got=B)
        code, out = self.setup('', 's', '', '', '')
        self.assertEqual(code, 0, out)
        self.assertEqual(out, FULL % self.page.server_address[1])
        self.assertEqual(cli.identity()[0], B)                      # "See it move" moved it
        self.assertEqual(self.fake.emails(), [A, B])
        self.assertEqual(self.posts('/api/connect'), [{'email': '', 'profile': '+new'}])   # a new browser profile
        self.assertEqual(self.asked_macos(), 1)
        self.assertTrue(linkapp.status(fresh=True)['on'])
        self.assertIs(core.settings().get(server.LINKS_WANTED), True)
        self.assertEqual(self.codex, 'added')

    def test_the_counter_counts_only_the_steps_shown(self):
        self.no_link_app()
        code, out = self.setup('s')
        self.assertEqual(self.steps(out), [('1', '4', 'Your account'), ('2', '4', 'Your other accounts'),
                                           ('3', '4', 'See it move'), ('4', '4', 'Where to find it')])
        self.assertNotIn('Links', out)

    def test_left_out_when_links_already_open_as_their_owner(self):
        self.link_app(holder=os.path.join(self.apps, 'Switchboard Links.app'))
        code, out = self.setup('s')
        self.assertEqual([s[2] for s in self.steps(out)], ['Your account', 'Your other accounts', 'See it move',
                                                           'Where to find it'])
        self.assertEqual(self.asked_macos(), 0)

    def test_left_out_when_turned_off_on_purpose(self):
        core.set_setting(server.LINKS_WANTED, False)
        code, out = self.setup('s')
        self.assertEqual([s[2] for s in self.steps(out)], ['Your account', 'Your other accounts', 'See it move',
                                                           'Where to find it'])

    def test_each_step_has_at_most_six_lines_before_its_question_at_80_columns(self):
        self.patch(cli, 'width', lambda: 80)
        self.with_codex()
        self.page.sign_in(WAIT, over(B), got=B)
        code, out = self.setup('', 's', 's', 's', 's')
        blocks = re.split(r'^\d+ of \d+  .*$', out, flags=re.M)[1:]
        for block in blocks:
            lines = block.strip('\n').splitlines()
            asked = [i for i, x in enumerate(lines) if '  Enter ' in x]
            if asked:
                self.assertLessEqual(asked[0], 6, block)
        for line in out.splitlines():
            self.assertLessEqual(len(line), 80 + len('  s'), line)


class Wrapping(unittest.TestCase):
    def test_a_command_or_a_button_name_is_never_broken_across_lines(self):
        class Narrow(object):
            def width(self):
                return 60

            def colour(self):
                return False
        out = firstrun.Out(Narrow())
        for words in (firstrun.LINKS_KEPT % 'Google Chrome', firstrun.LINKS_WHY % 'Google Chrome',
                      firstrun.PAGE_DOWN):
            with self.subTest(words=words[:30]), redirect_stdout(io.StringIO()) as said:
                out.para(words)
            for line in said.getvalue().splitlines():
                self.assertFalse(line.rstrip().endswith(('switchboard', 'switchboard links', '"Use', '"Use Switchboard')),
                                 line)
            self.assertEqual(' '.join(said.getvalue().split()), ' '.join(words.split()))


class SeeItMove(Setup):
    """One real switch during the first run, through the code `switchboard switch` runs (the page's /api/switch)."""

    def setUp(self):
        super().setUp()
        self.no_link_app()

    def counted_right(self, out):
        steps = self.steps(out)
        self.assertEqual([int(n) for n, m, t in steps], list(range(1, len(steps) + 1)), out)
        self.assertEqual(set(int(m) for n, m, t in steps), {len(steps)}, out)
        return [t for n, m, t in steps]

    def test_enter_moves_claude_code_and_says_how_to_move_back(self):
        self.page.sign_in(WAIT, over(B), got=B)
        code, out = self.setup('', 's', '')
        self.assertEqual(code, 0, out)
        self.assertEqual(self.counted_right(out), ['Your account', 'Your other accounts', 'See it move',
                                                   'Where to find it'])
        self.assertSaid('3 of 4  See it move\n'
                        '  Claude Code is on maya@northwind.example. Switchboard moves it at your lines, and you can '
                        'move it any time.\n'
                        '  Move Claude Code to maya@ivy.example now?  Enter moves it, s skips.  \n'
                        '  Moved. Open chats follow within about 30 seconds. Back: switchboard switch '
                        'maya@northwind.example\n', out)
        self.assertIn('  Moved. Open chats follow within about 30 seconds. Back: switchboard switch '
                      'maya@northwind.example\n', out)            # the command to type back stays on one line
        self.assertEqual(self.posts('/api/switch'), [{'email': B}])
        self.assertEqual(cli.identity()[0], B)

    def test_the_back_line_really_moves_claude_code_back(self):
        self.page.sign_in(WAIT, over(B), got=B)
        code, out = self.setup('', 's', '')
        back = re.search(r'Back: switchboard (switch \S+)\n', out).group(1).split()
        self.assertEqual(self.run_cli(*back)[0], 0)
        self.assertEqual(cli.identity()[0], A)

    def test_s_changes_nothing(self):
        self.page.sign_in(WAIT, over(B), got=B)
        code, out = self.setup('', 's', 's')
        self.assertIn('  Move Claude Code to maya@ivy.example now?  Enter moves it, s skips.  s\n'
                      '  Nothing moved. Move it any time with: switchboard switch\n', out)
        self.assertEqual(self.posts('/api/switch'), [])
        self.assertEqual(cli.identity()[0], A)
        self.counted_right(out)

    def test_a_refused_switch_says_why_and_the_first_run_goes_on(self):
        self.link_app()
        self.page.refuse = 'maya@ivy.example needs a fresh sign-in'
        self.page.sign_in(WAIT, over(B), got=B)
        code, out = self.setup('', 's', '', 's')
        self.assertEqual(code, 0, out)
        self.assertIn('  Not moved: maya@ivy.example needs a fresh sign-in. Claude Code stays on '
                      'maya@northwind.example.\n', out)
        self.assertNotIn('Moved.', out)
        self.assertEqual(self.counted_right(out), ['Your account', 'Your other accounts', 'See it move',
                                                   'Links that open in the right account', 'Where to find it'])
        self.assertEqual(cli.identity()[0], A)

    def test_two_accounts_kept_already_and_no_page_still_move(self):
        self.fake.hold(A)
        self.fake.hold(B)
        self.page.stop()
        os.environ['SWITCHBOARD_PORT'] = str(cli_free_port())
        code, out = self.setup('')
        self.assertIn('See it move', self.counted_right(out))
        self.assertIn('  Moved. Open chats follow', out)        # the page is down: the engine itself, as switch does
        self.assertEqual(cli.identity()[0], B)

    def test_no_account_under_its_lines_no_move(self):
        import time
        self.fake.hold(A)
        self.fake.hold(B)
        self.fake.reading(B, five=(97, time.time() + 3600))
        code, out = self.setup('s')
        self.assertNotIn('Move Claude Code to', out)
        self.assertEqual(self.posts('/api/switch'), [])
        self.counted_right(out)

    def test_with_the_page_down_and_one_account_the_step_does_not_exist(self):
        self.page.stop()
        os.environ['SWITCHBOARD_PORT'] = str(cli_free_port())
        code, out = self.setup('s')
        self.assertEqual(self.counted_right(out), ['Your account', 'Your other accounts', 'Where to find it'])

    def test_one_account_after_step_two_says_there_is_nothing_to_move_to(self):
        code, out = self.setup('s')
        self.assertEqual(self.counted_right(out), ['Your account', 'Your other accounts', 'See it move',
                                                   'Where to find it'])
        self.assertSaid('There is no other account to move to yet. Add one any time with switchboard add, and Claude '
                        'Code moves at your lines.', out)
        self.assertNotIn('Move Claude Code to', out)

    def test_never_with_yes_or_without_a_terminal(self):
        self.fake.hold(A)
        self.fake.hold(B)
        for args in (('setup', '--yes'), ('setup',)):
            with self.subTest(args=args):
                code, out = self.run_cli(*args, tty=FakeTty(['', '', '']) if '--yes' in args else None)
                self.assertNotIn('See it move', out)
                self.assertNotIn('Move Claude Code', out)
        self.assertEqual(self.posts('/api/switch'), [])
        self.assertEqual(cli.identity()[0], A)


class RoutingSeam(Setup):
    """The routing step: a seam for a `switchboard.router` package this copy does not have."""

    def setUp(self):
        super().setUp()
        self.no_link_app()

    def router(self, turn_on):
        import types
        pkg, wire, words = (types.ModuleType('switchboard.router'), types.ModuleType('switchboard.router.wire'),
                            types.ModuleType('switchboard.router.hand_words'))
        words.FIRST_RUN = ['Made-up router line one.', 'Made-up router line two.']
        wire.turn_on = turn_on
        pkg.wire, pkg.hand_words = wire, words
        p = mock.patch.dict(sys.modules, {'switchboard.router': pkg, 'switchboard.router.wire': wire,
                                          'switchboard.router.hand_words': words})
        p.start()
        self.addCleanup(p.stop)

    def test_without_a_router_package_nothing_changes(self):
        self.assertIsNone(firstrun.router_parts())
        with redirect_stdout(io.StringIO()) as said:
            self.assertFalse(firstrun.routing_step(cli, None, firstrun.Out(cli)))
        self.assertEqual(said.getvalue(), '')
        code, out = self.setup('s')
        self.assertNotIn('Routing', out)
        self.assertEqual([t for n, m, t in self.steps(out)], ['Your account', 'Your other accounts', 'See it move',
                                                             'Where to find it'])

    def test_with_one_it_shows_in_its_place_and_is_counted(self):
        self.router(lambda: ['Made-up: turned on.'])
        code, out = self.setup('s')
        self.assertEqual(code, 0, out)
        self.assertEqual(self.steps(out), [('1', '5', 'Your account'), ('2', '5', 'Your other accounts'),
                                           ('3', '5', 'See it move'), ('4', '5', 'Routing'),
                                           ('5', '5', 'Where to find it')])
        self.assertIn('4 of 5  Routing\n  Made-up router line one.\n  Made-up router line two.\n'
                      '  Made-up: turned on.\n\n5 of 5', out)

    def test_a_failure_on_its_side_is_one_line_and_the_run_goes_on(self):
        def turn_on():
            raise RuntimeError('the made-up router is broken')
        self.router(turn_on)
        code, out = self.setup('s')
        self.assertEqual(code, 0, out)
        self.assertSaid('  Routing could not be turned on: the made-up router is broken. Turn it on later with: '
                        'switchboard router on\n\n5 of 5  Where to find it', out)

    def test_it_runs_with_yes_and_never_in_the_one_line_form(self):
        self.router(lambda: ['Made-up: turned on.'])
        code, out = self.run_cli('setup', '--yes')
        self.assertIn('Routing', [t for n, m, t in self.steps(out)])
        code, out = self.run_cli('setup')
        self.assertNotIn('Made-up', out)


class EveryChangeWithItsUndo(Setup):
    """Step "Where to find it" names each thing this first run put into Claude Code's settings, with its undo."""

    def setUp(self):
        super().setUp()
        self.no_link_app()

    def changed_line(self, out):
        said = ' '.join(out.split())
        m = re.search(r"Changed in Claude Code's settings: (.*?)\.(?: |$)", said)
        return m.group(1) if m else None

    def test_what_went_in_is_named_with_the_command_that_takes_it_out(self):
        from switchboard import session_hooks
        code, out = self.setup('s')
        line = self.changed_line(out)
        self.assertIsNotNone(line, out)
        named = re.findall(r'([a-z ]+) \(undo: (switchboard [a-z]+ off)\)', line)
        really = {'status line': cli.statusline_on(), 'notifications': cli.notifications_on(),
                  'session hooks': bool(session_hooks.status()['in'])}
        self.assertEqual(sorted(w.strip() for w, u in named), sorted(w for w, on in really.items() if on), out)
        self.assertEqual(sorted(u for w, u in named), sorted(
            {'status line': 'switchboard statusline off', 'notifications': 'switchboard notify off',
             'session hooks': 'switchboard hooks off'}[w.strip()] for w, u in named))
        for w, undo in named:
            self.assertTrue(cli.command_help(undo.split()[1]), undo)
        self.assertIn('session hooks', line)                        # the hooks went in, so they are said

    def test_a_second_run_changes_nothing_and_says_nothing_changed(self):
        self.setup('s')
        code, out = self.setup('s')
        self.assertIsNone(self.changed_line(out), out)

    def test_only_what_went_in(self):
        self.assertEqual(firstrun.changes({'lined': 'theirs', 'notified': False, 'hooked': True}),
                         "Changed in Claude Code's settings: session hooks (undo: switchboard hooks off).")
        self.assertIsNone(firstrun.changes({'lined': None, 'notified': False, 'hooked': False}))


class CtrlCAnywhere(Setup):
    """Ctrl-C outside the wait for a sign-in: never a sign-in left running, never setup ended half done."""

    def setUp(self):
        super().setUp()
        self.no_link_app()

    def test_while_the_page_starts_the_sign_in_it_is_cancelled_and_asked_again(self):
        real, hit = cli.ask_page, []

        def ask_page(path, body, **kw):
            got = real(path, body, **kw)
            if path == '/api/connect' and not hit:
                hit.append(path)
                raise KeyboardInterrupt         # Ctrl-C while the page was still answering
            return got
        self.patch(cli, 'ask_page', ask_page)
        code, out = self.setup('', 's')
        self.assertEqual(code, 0, out)
        self.assertEqual(self.posts('/api/cancel'), [{'key': 'new'}])
        self.assertIn('  Cancelled. Nothing was added.\n  Add a Claude account?  Enter opens your browser, s skips.  s\n',
                      out)
        self.assertIn('Where to find it', out)

    def test_during_the_wait_for_the_page_it_goes_on(self):
        self.job()
        self.patch(cli, 'page_running', lambda: False)
        self.interrupt_on(3)
        code, out = self.setup()
        self.assertEqual(code, 0, out)
        self.assertSaid('Run switchboard doctor', out)
        self.assertIn('Where to find it', out)

    def test_while_the_plan_is_read_the_account_is_still_said_as_kept(self):
        self.page.sign_in(WAIT, over(B), got=B)
        self.interrupt_on(2)                    # the first sleep is the wait's, the second the plan's
        code, out = self.setup('', 's', 's')
        self.assertIn('  maya@ivy.example. Kept.\n', out)
        self.assertIn('Where to find it', out)


class TheLinkAppDoesNotAnswerAsItShould(Setup):
    def test_a_link_app_that_crashes_is_said_once_as_no_change_and_never_waited_on(self):
        self.link_app(answer='crash')
        code, out = self.setup('s', '')
        self.assertEqual(code, 0, out)
        self.assertSaid('No change. Google Chrome is still your default browser. Turn this on later with: switchboard '
                        'links on', out)
        self.assertNotIn('No answer from macOS', out)
        self.assertIsNone(core.settings().get(server.LINKS_WANTED))       # a crash is not an answer

    def test_after_its_own_wait_ends_it_says_no_answer_yet_and_ctrl_c_moves_on(self):
        self.link_app(answer='slow')

        class EndedItsOwnWait(object):          # the link app's --make-default, exited 2 after its own 180 s
            returncode = 2

            def poll(self):
                return 2
        self.patch(firstrun, 'make_default', EndedItsOwnWait)
        self.interrupt_on(3)
        code, out = self.setup('s', '')
        self.assertEqual(code, 0, out)
        self.assertIn('  No answer from macOS yet. Ctrl-C moves on.\n', out)
        self.assertIn('Where to find it', out)
        self.assertIsNone(core.settings().get(server.LINKS_WANTED))       # Ctrl-C is not an answer either

    def test_the_wait_line_says_what_to_do_after_keep(self):
        self.assertIn('If you chose Keep, press Ctrl-C.', firstrun.LINKS_WAIT)


class OneAccountOnly(Setup):
    def test_skip_everything(self):
        self.no_link_app()
        code, out = self.setup('s')
        self.assertEqual(code, 0)
        self.assertIn('  Add a Claude account?  Enter opens your browser, s skips.  s\n', out)
        self.assertNotIn('Codex', out)                                  # not on this Mac: not a word
        self.assertTrue(out.endswith('1 Claude account. Type switchboard to see it.\n'), out)
        self.assertEqual(self.posts('/api/connect'), [])
        self.assertNotIn('Auto-switch is on', out)                      # one account: nothing to switch to

    def test_codex_present_and_skipped(self):
        self.no_link_app()
        self.with_codex()
        code, out = self.setup('s', 's')
        self.assertIn('  Codex is on this Mac too. Add it to your list?  Enter adds it, s skips.  s\n', out)
        self.assertEqual(self.codex, 'here')
        self.assertTrue(out.endswith('1 Claude account. Type switchboard to see it.\n'), out)


class TheSameAccount(Setup):
    def test_said_with_what_to_do_then_tried_again(self):
        self.no_link_app()
        self.fake.hold(A)
        self.page.sign_in(WAIT, over(A), got=A)
        self.page.sign_in(WAIT, over(C), got=C)
        code, out = self.setup('', '', 's')
        self.assertSaid('  Your browser signed in as maya@northwind.example, which is already here. Press Enter to try '
                        'again: a new browser profile opens, and you sign in there as the other account.\n'
                        '  Try again?  Enter opens your browser, s skips.  \n', out)
        self.assertIn('  ana@harbor.example. Kept.\n  Add another?', out)
        self.assertEqual(self.fake.emails(), [A, C])

    def test_in_the_default_browser_it_says_to_sign_out_there(self):
        self.no_link_app()
        self.fake.hold(A)
        self.page.sign_in(WAIT_DEFAULT, over(A), got=A)
        code, out = self.setup('', 's')
        self.assertSaid('  Your browser opens the sign-in as it is: if it shows an account already here, sign out of '
                        'claude.ai there first.\n  Waiting for your browser.', out)
        self.assertSaid('  Your browser signed in as maya@northwind.example, which is already here. Sign out of '
                        'claude.ai in that window, then press Enter to try again.\n  Try again?', out)

    def test_one_that_needed_a_sign_in_is_signed_in_again(self):
        self.no_link_app()
        self.fake.hold(A)
        self.fake.hold(B)
        self.page.needs.add(B)
        self.page.sign_in(WAIT, over(B), got=B)
        code, out = self.setup('', 's')
        self.assertIn('  Signed in again as maya@ivy.example. Kept.\n  Add another?', out)


class NotFinished(Setup):
    def test_a_sign_in_that_timed_out_says_why_and_asks_again(self):
        self.no_link_app()
        self.page.sign_in(WAIT, over(ok=False, why='it was not approved in time'))
        code, out = self.setup('', 's')
        self.assertIn('  The sign-in did not finish: it was not approved in time.\n'
                      '  Try again?  Enter opens your browser, s skips.  s\n', out)
        self.assertEqual(self.fake.emails(), [A])

    def test_one_the_page_could_not_start_says_why(self):
        self.no_link_app()

        def refuse(path, body):
            raise cli.engine.Refused('Claude Code did not hand over a sign-in link. Try again in a minute.')
        real = cli.ask_page
        self.patch(cli, 'ask_page', lambda path, body, **k: refuse(path, body) if path == '/api/connect'
                   else real(path, body, **k))
        code, out = self.setup('', 's')
        self.assertIn('  The sign-in did not start: Claude Code did not hand over a sign-in link. Try again in a '
                      'minute.\n  Try again?', out)

    def test_cancelled_on_the_page(self):
        self.no_link_app()
        self.page.sign_in(WAIT, over(ok=False, why='stopped'))
        code, out = self.setup('', 's')
        self.assertIn('  The sign-in was cancelled on the page. Nothing was added.\n  Add a Claude account?', out)

    def test_a_page_that_forgot_it(self):
        self.no_link_app()
        self.page.sign_in(WAIT)
        code, out = self.setup('', 's')
        self.assertIn('  The sign-in is no longer under way: the page may have restarted. Nothing was added.\n', out)


class CtrlCWhileItWaits(Setup):
    def test_it_cancels_that_sign_in_and_asks_again(self):
        self.no_link_app()
        self.page.sign_in(WAIT, WAIT, WAIT, over(B), got=B)
        self.interrupt_on(2)
        code, out = self.setup('', 's')
        self.assertEqual(code, 0)
        self.assertEqual(self.posts('/api/cancel'), [{'key': 'new'}])
        self.assertIn('  Cancelled. Nothing was added.\n  Add a Claude account?  Enter opens your browser, s skips.  s\n',
                      out)
        self.assertEqual(self.fake.emails(), [A])
        self.assertTrue(out.endswith('1 Claude account. Type switchboard to see it.\n'), out)

    def test_one_the_browser_had_just_approved_is_kept(self):
        self.no_link_app()
        self.page.sign_in(WAIT, KEEP, KEEP, over(B), got=B)
        self.interrupt_on(2)
        code, out = self.setup('', 's')
        self.assertIn('  Your browser had already approved it, so its login is being kept. Ctrl-C leaves that to the '
                      'page.\n', out)
        self.assertIn('  maya@ivy.example. Kept.\n', out)

    def test_one_kept_just_before_the_cancel_reached_the_page_is_said_as_kept(self):
        self.no_link_app()
        self.page.sign_in(WAIT, WAIT, over(B), got=B)
        self.interrupt_on(1)
        real = cli.ask_page

        def ask(path, body, **k):           # the page kept it as the Cancel arrived: its next look is over
            left = [x for x in self.page.looks if x.get('state') == 'over']
            got = real(path, body, **k)
            if path == '/api/cancel':
                self.page.looks = left
            return got
        self.patch(cli, 'ask_page', ask)
        code, out = self.setup('', 's')
        self.assertIn('  maya@ivy.example. Kept.\n', out)
        self.assertNotIn('Cancelled', out)

    def test_at_a_question_it_skips_that_step(self):
        self.no_link_app()

        class Interrupting(FakeTty):
            def ask(self, text):
                sys.stdout.write(text)
                raise KeyboardInterrupt
        code, out = self.run_cli('setup', tty=Interrupting([]))
        self.assertEqual(code, 0)
        self.assertEqual(self.posts('/api/connect'), [])
        self.assertIn('Where to find it', out)


class TheLinkStep(Setup):
    def test_keep_at_macos_question(self):
        self.link_app(answer='keep')
        code, out = self.setup('s', '')
        self.assertSaid('  No change. Google Chrome is still your default browser. Turn this on later with: switchboard '
                        'links on\n\n5 of 5', out)
        self.assertEqual(self.asked_macos(), 1)
        self.assertFalse(linkapp.status(fresh=True)['on'])
        self.assertIs(core.settings().get(server.LINKS_WANTED), False)    # nothing offers it again by itself
        self.assertEqual(out.count("macOS's question"), 1)                # never a second question

    def test_skipped_it_asks_macos_nothing(self):
        code, out = self.setup('s', 's')
        self.assertIn('  Turn this on?  Enter shows macOS\'s question, s skips.  s\n'
                      '  Turn this on later with: switchboard links on\n', out)
        self.assertEqual(self.asked_macos(), 0)

    def test_the_explanation_comes_before_macos_is_asked(self):
        seen = []
        real = firstrun.ask_macos

        def ask(cli_, out, before):
            seen.append(sys.stdout.getvalue())
            return real(cli_, out, before)
        self.patch(firstrun, 'ask_macos', ask)
        self.setup('s', '')
        self.assertSaid('choose "Use Switchboard Links"', seen[0])
        self.assertSaid('Undo at any time: switchboard links off', seen[0])

    def test_another_default_browser_is_named_and_an_unknown_one_is_not(self):
        safari = self.app('Safari.app', 'com.apple.Safari')
        self.link_app(holder=safari)
        with open(self.seat_file, 'w') as f:
            f.write(safari + '\n')
        code, out = self.setup('s', 's')
        self.assertSaid('Every other link opens in Safari exactly as it does now.', out)
        other = self.app('Mail.app', 'com.apple.mail')
        with open(self.seat_file, 'w') as f:
            f.write(other + '\n')
        code, out = self.setup('s', 's')
        self.assertSaid('Every other link opens in your browser exactly as it does now.', out)
        self.assertNotIn('Google Chrome', out)

    def test_links_on_at_a_terminal_explains_first(self):
        tty = FakeTty([''])
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(cli, 'open_tty', lambda: tty), \
                mock.patch.object(cli, 'terminal', lambda: True):
            code = cli.main(['links', 'on'])
        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertLess(text.index('Undo at any time'), text.index("Turn this on?  Enter shows macOS's question"))
        self.assertTrue(text.endswith('Done. claude.ai links open as their owner. Everything else still opens in '
                                      'Google Chrome.\n'), text)

    def test_links_on_without_a_terminal_is_as_before(self):
        code, out = self.run_cli('links', 'on')
        self.assertNotIn('Undo at any time', out)


class WhereToFindIt(Setup):
    def test_the_menu_bar_only_when_it_runs(self):
        from switchboard import menubar
        self.no_link_app()
        self.patch(menubar, 'running_pid', lambda: 4242)
        code, out = self.setup('s')
        self.assertIn('  Menu bar      the S, next to the clock.\n', out)
        plist = menubar.agent_plist(menubar.AGENT)
        os.makedirs(os.path.dirname(plist), exist_ok=True)
        open(plist, 'w').close()
        code, out = self.setup('s')
        self.assertIn('  Menu bar      the S, next to the clock. It starts when you log in.\n', out)
        self.patch(menubar, 'running_pid', lambda: None)
        code, out = self.setup('s')
        self.assertNotIn('Menu bar', out)

    def test_the_page_line_is_always_there(self):
        self.no_link_app()
        code, out = self.setup('s')
        self.assertIn('  The page      switchboard page   (every account, chat and artifact, at 127.0.0.1:%d)\n'
                      % self.page.server_address[1], out)


class ThePageNotRunning(Setup):
    def test_without_its_job_one_line_names_doctor_and_never_serve(self):
        self.no_link_app()
        self.page.stop()
        os.environ['SWITCHBOARD_PORT'] = str(cli_free_port())
        code, out = self.setup('s')
        self.assertEqual(code, 0)
        self.assertSaid('  Accounts are added through the page, which is not running and did not start. Run '
                        'switchboard doctor to see why, then add them with switchboard add.\n', out)
        self.assertNotIn('switchboard serve', out)
        self.assertNotIn('Press Enter once it runs', out)
        self.assertNotIn('/login', out)
        self.assertSaid('not running now: switchboard doctor says why', out)
        self.assertEqual(self.page_starts, [])          # no job to ask: nothing is started

    def test_its_job_is_asked_to_start_it_and_waited_for(self):
        self.no_link_app()
        self.job()
        looks = {'n': 0}

        def page_running():                 # the job's page answers once launchd was asked, from the third look on
            looks['n'] += 1
            return bool(self.page_starts) and looks['n'] > 3
        self.patch(cli, 'page_running', page_running)
        code, out = self.setup('s')
        self.assertEqual(self.page_starts, ['job'])
        self.assertNotIn('which is not running', out)
        self.assertNotIn('switchboard serve', out)
        self.assertIn('Add a Claude account?', out)

    def test_one_waiting_line_then_doctor_when_the_job_does_not_start_it(self):
        self.no_link_app()
        self.job()
        self.page.stop()
        os.environ['SWITCHBOARD_PORT'] = str(cli_free_port())
        slept = []
        self.patch(cli, '_sleep', slept.append)
        code, out = self.setup('s')
        self.assertEqual(self.page_starts, ['job'])
        self.assertEqual(out.count('Starting the page…'), 1)
        self.assertLessEqual(sum(slept), 20)                       # about 20 s at most
        self.assertGreaterEqual(sum(slept), 19)
        flat = ' '.join(out.split())
        self.assertLess(flat.index('Starting the page…'), flat.index('Run switchboard doctor'))
        self.assertNotIn('switchboard serve', out)

    def test_add_email_asks_the_job_too(self):
        self.job()
        self.page.stop()
        os.environ['SWITCHBOARD_PORT'] = str(cli_free_port())
        code, out = self.run_cli('add', B, tty=FakeTty([]))
        self.assertEqual(code, 1)
        self.assertEqual(self.page_starts, ['job'])
        self.assertSaid('Run switchboard doctor', out)
        self.assertNotIn('switchboard serve', out)


def cli_free_port():
    from test_keep import free_port
    return free_port()


class NoTerminal(Setup):
    def test_three_lines_at_most_and_the_command_that_does_the_rest(self):
        code, out = self.run_cli('setup')
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertLessEqual(len(lines), 3, out)
        self.assertEqual(lines[0], 'Claude Code is signed in as maya@northwind.example. Kept.')
        self.assertEqual(lines[-1], 'Add your other accounts at a terminal with switchboard setup.')
        self.assertEqual(self.posts('/api/connect'), [])
        self.assertEqual(self.asked_macos(), 0)

    def test_yes_says_every_step_and_asks_nothing(self):
        code, out = self.run_cli('setup', '--yes')
        self.assertEqual([s[2] for s in self.steps(out)], ['Your account', 'Your other accounts', 'Where to find it'])
        self.assertIn('  Add each one at a terminal with: switchboard add\n', out)
        self.assertEqual(self.asked_macos(), 0)

    def test_not_signed_in(self):
        self.signed_out()
        self.assertEqual(self.run_cli('setup'), (0, 'Sign in to Claude Code first, then run switchboard again.\n'))
        code, out = self.setup()
        self.assertEqual(out, 'Switchboard setup.\n\n1 of 1  Your account\n'
                              '  Sign in to Claude Code first, then run switchboard again.\n')


class OneWord(Setup):
    """`switchboard` alone, at a terminal, with nothing kept: the whole first run, then the accounts."""

    def bare(self, *answers):
        with mock.patch.object(cli, 'read_one_key', lambda: 'q'), \
                mock.patch.object(sys, 'stdin', io.StringIO(''.join(a + '\n' for a in answers))):
            return self.run_cli(terminal=True)

    def test_signed_in_it_is_the_whole_first_run_then_the_list(self):
        self.no_link_app()
        self.page.sign_in(WAIT, over(B), got=B)
        code, out = self.bare('', 's', 's')
        self.assertEqual(code, 0, out)
        self.assertEqual([t for n, m, t in self.steps(out)], ['Your account', 'Your other accounts', 'See it move',
                                                             'Where to find it'])
        self.assertNotIn('Type switchboard to see', out)           # the accounts follow at once
        self.assertIn('maya@ivy.example', out.split('Where to find it')[1])
        self.assertEqual(self.fake.emails(), [A, B])

    def test_not_signed_in_it_is_the_first_runs_own_words(self):
        self.signed_out()
        code, out = self.bare()
        self.assertEqual((code, out), (0, 'Switchboard setup.\n\n1 of 1  Your account\n'
                                          '  Sign in to Claude Code first, then run switchboard again.\n'))

    def test_without_claude_code_it_is_the_first_runs_own_words(self):
        self.signed_out()
        self.patch(connect, 'claude_bin', lambda: None)
        code, out = self.bare()
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith('Switchboard setup.\n\n1 of 1  Your account\n'), out)
        self.assertNotIn('switchboard setup', out)

    def test_no_message_says_then_run_switchboard_setup(self):
        for claude in ('/made/up/claude', None):
            with mock.patch.object(connect, 'claude_bin', lambda: claude):
                self.assertNotIn('switchboard setup', cli.first_step())
                self.assertNotIn('switchboard setup', cli._closing({}, {'accounts': [], 'head': {'email': None}})[0])


class NoDeadEndWithoutClaudeCode(Setup):
    """Codex on this Mac, Claude Code not installed or not signed in: Codex is offered, and the last line says what is
    true and the one next step, never only "Install Claude Code first"."""

    def setUp(self):
        super().setUp()
        self.signed_out()
        self.with_codex()

    def test_not_installed_codex_is_offered_and_added(self):
        self.patch(connect, 'claude_bin', lambda: None)
        code, out = self.setup('')
        self.assertEqual(code, 0, out)
        self.assertEqual(self.steps(out), [('1', '2', 'Your account'), ('2', '2', 'Codex')])
        self.assertIn('  Codex is on this Mac too. Add it to your list?  Enter adds it, s skips.  \n'
                      '  Added Codex. Your order: Claude, Codex.\n', out)
        self.assertEqual(self.codex, 'added')

    def test_not_signed_in_codex_is_offered_and_skipped(self):
        code, out = self.setup('s')
        self.assertEqual(self.steps(out), [('1', '2', 'Your account'), ('2', '2', 'Codex')])
        self.assertEqual(self.codex, 'here')

    def test_without_codex_it_is_the_one_step(self):
        self.codex = None
        code, out = self.setup()
        self.assertEqual(self.steps(out), [('1', '1', 'Your account')])

    def test_the_list_with_codex_on_it_ends_on_what_is_true_and_the_next_step(self):
        board = {'providers': [{'id': 'claude', 'name': 'Claude', 'account_list': True},
                               {'id': 'codex', 'name': 'Codex', 'account_list': False}], 'add': []}
        for claude, words in ((None, 'Your Claude accounts join this list once Claude Code is installed '
                                     '(https://claude.com/claude-code). Then run switchboard.'),
                              ('/made/up/claude', 'Your Claude accounts join this list once Claude Code is signed '
                                                  'in. Then run switchboard.')):
            with self.subTest(claude=claude), mock.patch.object(connect, 'claude_bin', lambda: claude):
                line = cli._closing({'providers': board}, {'accounts': [], 'head': {'email': None}})[0]
                self.assertEqual(line, words)
                self.assertFalse(line.startswith('Install Claude Code first'))

    def test_no_terminal_and_yes_ask_nothing(self):
        for args in (('setup',), ('setup', '--yes')):
            with self.subTest(args=args):
                code, out = self.run_cli(*args)
                self.assertNotIn('Add it to your list?', out)
        self.assertEqual(self.codex, 'here')


class SwitchboardAdd(Setup):
    def test_add_at_a_terminal_signs_in_through_the_browser(self):
        self.fake.hold(A)
        self.page.plans[B] = 'Max 20x'
        self.page.sign_in(WAIT, over(B), got=B)
        code, out = self.setup('s', args=('add',))
        self.assertEqual(code, 0)
        self.assertEqual(out, 'Waiting for your browser. Ctrl-C cancels.\n'      # a command of its own: not indented
                              'maya@ivy.example (Max 20x). Kept.\n'
                              'Add another?  Enter opens your browser, s skips.  s\n'
                              '%s\n'
                              '2 accounts. Type switchboard to see them.\n' % term.AUTO_ON)
        self.assertEqual(self.fake.emails(), [A, B])

    def test_add_without_the_page_says_why_it_uses_login_and_hints_at_the_same_account(self):
        self.page.stop()
        os.environ['SWITCHBOARD_PORT'] = str(cli_free_port())
        self.fake.hold(A)
        state = {'n': 0}

        def sleep(seconds):                 # the person runs /login as the same account, then as another
            state['n'] += 1
            if state['n'] == 1:
                with open(core.claude_json(), 'w') as f:
                    json.dump({'oauthAccount': {'emailAddress': A, 'organizationUuid': 'org-northwind'},
                               'again': 1}, f)
            elif state['n'] == 3:
                self.signed_in(B)
        self.patch(cli, '_sleep', sleep)
        code, out = self.setup('n', args=('add',))
        self.assertIn(cli.ADD_WITHOUT_PAGE + '\n', out)
        self.assertIn('Claude Code signed in as maya@northwind.example, which is already here. Run /login again and '
                      'sign in as another account.\n', out)
        self.assertIn('✓ Saved maya@ivy.example\n', out)


class SignInAgainFromTheList(Setup):
    def test_an_account_that_needs_it_signs_in_through_the_page(self):
        self.fake.hold(A)
        self.fake.hold(B)
        self.page.needs.add(B)
        self.page.sign_in(WAIT, over(B), got=B)
        with redirect_stdout(io.StringIO()) as out:
            code = firstrun.sign_in_again(cli, B)
        self.assertEqual(code, 0)
        self.assertEqual(self.posts('/api/connect'), [{'email': B, 'profile': '+new'}])
        self.assertTrue(out.getvalue().endswith('\nSigned in again as maya@ivy.example. Kept.\n'), out.getvalue())

    def test_without_a_terminal_it_names_the_command(self):
        self.fake.hold(A)
        self.fake.hold(B, status='needs_login')
        with mock.patch.object(cli.term, 'read', lambda view, live=None: {'accounts': [
                {'email': B, 'needs_login': True, 'active': False, 'slot': 2, 'unmanaged': False}]}):
            with self.assertRaises(core.SwitchError) as e:
                self.run_cli('2')
        self.assertEqual(str(e.exception), 'maya@ivy.example needs a fresh sign-in: switchboard add maya@ivy.example')


class AddOneAccount(Setup):
    def test_add_email_signs_in_as_that_account(self):
        self.fake.hold(A)
        self.page.sign_in(WAIT, over(B), got=B)
        code, out = self.run_cli('add', B)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.posts('/api/connect'), [{'email': B, 'profile': '+new'}])
        self.assertIn('\nmaya@ivy.example. Kept.\n', out)
        self.assertEqual(self.fake.emails(), [A, B])

    def test_another_account_came_back(self):
        self.fake.hold(A)
        self.page.sign_in(WAIT, over(C, ok=False, why='your browser signed in as ana@harbor.example'), got=C)
        code, out = self.run_cli('add', B)
        self.assertEqual(code, 1)
        self.assertSaid('Added ana@harbor.example, not maya@ivy.example: your browser was signed in as '
                        'ana@harbor.example.', out)
        self.page.sign_in(WAIT, over(A, ok=False, why='your browser signed in as maya@northwind.example'))
        code, out = self.run_cli('add', B)
        self.assertSaid('Your browser signed in as maya@northwind.example, which is already here, not as '
                        'maya@ivy.example. Sign out of claude.ai in that window, then try again.', out)

    def test_page_down(self):
        self.page.stop()
        os.environ['SWITCHBOARD_PORT'] = str(cli_free_port())
        code, out = self.run_cli('add', B)
        self.assertEqual((code, ' '.join(out.split())), (1, firstrun.PAGE_DOWN))

    def test_add_codex_is_still_the_providers_own(self):
        from switchboard.providers import cli as providers_cli
        seen = []
        with mock.patch.object(providers_cli, 'main', lambda args, kit=None: seen.append(args) or 0):
            self.run_cli('add', 'codex')
        self.assertEqual(seen, [['add', 'codex']])


# ---------- the short help ----------

class TheHelp(Setup):
    def test_it_fits_on_one_screen_and_names_only_commands_that_exist(self):
        code, out = self.run_cli('help')
        self.assertEqual(code, 0)
        self.assertEqual(out, firstrun.short_help() + '\n')
        lines = out.splitlines()
        self.assertLessEqual(len(lines), 10)
        self.assertEqual(' '.join(lines[:2]), 'Switchboard routes Claude Code between the Claude accounts you pay '
                                              'for, and puts Codex and your own API keys one command away.')
        named = set(re.findall(r'switchboard ([a-z][a-z-]*)', out))
        self.assertEqual(named, {'add', 'page', 'doctor', 'help', 'log'})
        for word in named - {'help'}:
            self.assertTrue(cli.command_help(word), word)
        for line in lines:
            self.assertLessEqual(len(line), 78, line)            # a terminal of 80 columns: no line wraps
        for flag in ('-h', '--help'):
            self.assertEqual(self.run_cli(flag), (0, out))

    def test_help_all_is_every_command(self):
        self.assertEqual(self.run_cli('help', 'all'), (0, cli.__doc__.strip() + '\n'))
        self.assertEqual(self.run_cli('help', 'auto')[1], cli.command_help('auto') + '\n')


# ---------- /login only where it is the true answer ----------

# Every product line that names /login, by file, and why it stays (NOTE.md lists the same). A new one fails here
# until it is looked at: adding an account goes through `switchboard add` or the page's Add account.
LOGIN_KEPT = {
    'switchboard/__main__.py': 9,       # the help's add line (a /login is kept by itself); add and a sign-in again
                                        # while the page is not running, each said with why; comments
    'switchboard/scripted.py': 1,       # `current`: Claude Code itself is signed out
    'switchboard/gateway.py': 2,        # Claude Code's own words, quoted
    'switchboard/server.py': 1,         # a comment
    'switchboard/places.py': 1,         # a comment
    'switchboard/keep.py': 3,           # comments: a /login is kept by itself
    'switchboard/providers/claude.py': 1,   # how Claude Code itself signs in, when it is signed out
    'switchboard/browser.py': 1,        # a claude.ai path, not a message
    'switchboard/engine.py': 1,         # an engine without browser sign-ins
    'switchboard/session_hooks.py': 12,     # "do not run /login" to a chat
    'switchboard/history.py': 2,        # comments
    'switchboard/accounts/putback.py': 3,   # renewing the account in use after its login was lost four times
    'switchboard/accounts/capture.py': 2,   # comments
    'switchboard/accounts/roster.py': 3,    # the engine's add_current: Claude Code itself is signed out
}


class LoginOnlyWhereTrue(unittest.TestCase):
    def test_no_new_message_sends_a_person_to_login(self):
        found = {}
        top = os.path.join(ROOT, 'switchboard')
        for folder, _, files in os.walk(top):
            for name in files:
                if not name.endswith(('.py', '.html', '.js')):
                    continue
                path = os.path.join(folder, name)
                with open(path, encoding='utf-8') as f:
                    n = len(re.findall(r'(?<![\w.])/login\b', f.read()))
                if n:
                    found[os.path.relpath(path, os.path.join(ROOT)).replace(os.sep, '/')] = n
        self.assertEqual(found, LOGIN_KEPT)

    def test_the_first_run_and_add_never_say_it(self):
        for words in (firstrun.OTHERS_HOW, firstrun.ASK_FIRST, firstrun.PAGE_DOWN, firstrun.NO_TERMINAL_ADD,
                      term.another_account()):
            self.assertNotIn('/login', words)


# ---------- the page's half: one sign-in, followed from another process ----------

class FakeProc(object):
    def __init__(self, rc=None):
        self.rc = rc
        self.pid = 0

    def poll(self):
        return self.rc


class ConnectLook(unittest.TestCase):
    def setUp(self):
        connect.RUNNING.clear()
        connect.ENDED.clear()
        self.addCleanup(connect.RUNNING.clear)
        self.addCleanup(connect.ENDED.clear)

    def test_waiting_keeping_over_and_none(self):
        c = {'proc': FakeProc(None), 'dir': '/nonexistent', 'started': 5.5, 'email': '', 'profile': None,
             'opened': 'default'}
        connect.RUNNING['new'] = c
        self.assertEqual(connect.look('new', 5.5), {'state': 'waiting', 'started': 5.5, 'default_browser': True})
        self.assertEqual(connect.look('new', 4.0), {'state': None})           # another sign-in under the same key
        c['proc'].rc = 0
        self.assertEqual(connect.look('new')['state'], 'keeping')
        connect.RUNNING.pop('new')
        c['result'] = {'ok': True, 'got': B}
        connect._ended('new', c)
        self.assertEqual(connect.look('new', 5.5), {'state': 'over', 'started': 5.5, 'result': {'ok': True, 'got': B}})
        self.assertEqual(connect.look('other'), {'state': None})

    def test_a_failed_sign_in_is_over_at_once_never_keeping(self):
        connect.RUNNING['new'] = {'proc': FakeProc(1), 'dir': '/nonexistent', 'started': 6.0, 'email': '',
                                  'profile': None}
        self.assertEqual(connect.look('new', 6.0), {'state': 'over', 'started': 6.0,
                                                    'result': {'ok': False, 'why': 'the sign-in did not finish'}})

    def test_a_cancel_is_noted_as_stopped(self):
        connect.RUNNING['new'] = {'proc': FakeProc(None), 'dir': '/nonexistent', 'started': 7.0, 'email': '',
                                  'profile': None}
        with mock.patch.object(connect, '_end', lambda c: None):
            connect.stop('new')
        self.assertEqual(connect.look('new', 7.0)['result'], {'ok': False, 'why': 'stopped'})

    def test_the_page_answers_it(self):
        connect.RUNNING['new'] = {'proc': FakeProc(None), 'dir': '/nonexistent', 'started': 9.25, 'email': '',
                                  'profile': 'P', 'opened': 'profile'}
        self.assertEqual(server.act('/api/connect-look', {'key': 'new', 'started': 9.25}),
                         {'state': 'waiting', 'started': 9.25, 'default_browser': False})
        self.assertEqual(server.act('/api/connect-look', {'key': 'new', 'started': 'x'})['state'], 'waiting')


if __name__ == '__main__':
    unittest.main()
