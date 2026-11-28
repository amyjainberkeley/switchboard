import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The main road, counted. For the bare command, a number, `switchboard switch` and `switchboard add`, each on its good
# path, the lines printed beyond the list itself are a fixed number, and this test fails when one is added. On that
# road no line holds a path, a version number or "runs on your ... sign-in".
#
# Each command runs whole, as main() runs it: the Claude side on the list's own state with the switch standing in,
# and the other provider's side through the real key press and the real add on the stand-in adapters of
# tests/fakes_provider_kit.py (a made-up key in the shell, so the line about it would show if it were said). An exec
# is only recorded.
import copy
import dataclasses
import io
import os
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from fakes_provider_kit import KitCase, chat, codex_found  # noqa: E402
from switchboard import __main__ as main  # noqa: E402
from switchboard import connect, demo, models, term  # noqa: E402
from switchboard.providers import cli  # noqa: E402
from switchboard.router import wire  # noqa: E402
from test_one_list import codex_row  # noqa: E402
from test_router_wire import NEW, SETTINGS_TEXT, _Routes  # noqa: E402
from test_term import A, B, VIEW, FakeTty, Sandbox, hold, iso_in  # noqa: E402

# Lines beyond the list, on the good path. Change one only with a reason a person would agree with.
BARE = 3            # the blank line, the question with the key pressed, the one line that says what happened
NUMBER = 1          # switchboard N: the one line
SWITCH = 1          # switchboard switch: the one line
ADD = 6             # at a terminal, the page not running: the Claude login kept, Codex's notice (two lines), the
                    # question to add it (answered no), why the next sign-in is /login (the page is not running, and
                    # how to sign in through the browser instead), and how to add the next Claude account
ADD_NO_TERMINAL = 2     # no terminal: the Claude login kept, and how to add the next one (Codex is not added)
ROUTING_ON = 5      # once a person turns routing on: a blank line and routing's four lines (on, the plan's age,
                    # nothing routed yet, the helpers' cache); while it is off, not one line (the counts above)
UNDER = 2           # under the list, when there is something to say: a blank line and one line (why a provider added
                    # has no row, or the offer of one on this Mac that is not added); nothing when there is not

VERSION = re.compile(r'\b\d+\.\d+\.\d+')
SIGN_IN = re.compile(r'(?i)runs on your .*sign-in')


def upload(folder):
    return chat('claude', folder, name='Upload retries', ago=600.0)


class Road(KitCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.rig_ = self.rig(tty=True, claude_chats=[upload], env={'OPENAI_API_KEY': 'made-up-key-0123456789'})
        for target, name, value in ((main, 'identity', lambda: None), (main, 'width', lambda: None),
                                    (connect, 'claude_bin', lambda: '/made/up/claude'),
                                    (main, 'terminal', lambda: True), (main, 'keep_now', lambda: False),
                                    (main, 'saved_identities', lambda: [(VIEW['active'], '')]),
                                    (cli, 'default_kit', lambda reach=None: dataclasses.replace(self.rig_.kit,
                                                                                                out=sys.stdout))):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)

    def view(self, **row):
        return dict(copy.deepcopy(VIEW), provider_rows=[codex_row(**row)])

    def run_main(self, args, view, keys=()):
        keys = list(keys)
        out = io.StringIO()
        with mock.patch.object(main, 'current_view', lambda **k: view), \
                mock.patch.object(main, 'read_one_key', lambda: keys.pop(0)), redirect_stdout(out):
            code = main.main(list(args))
        return code, out.getvalue().splitlines()

    def beyond_the_list(self, view, printed):
        shown = term.view(term.read(view), main.width(), main.colour())
        self.assertEqual(printed[:len(shown)], shown)
        return printed[len(shown):]

    def check(self, extra):
        for line in extra:
            self.assertNotIn('/', line)
            self.assertIsNone(VERSION.search(line), line)
            self.assertIsNone(SIGN_IN.search(line), line)

    def test_the_bare_command_and_the_openai_row(self):
        view = self.view()
        code, printed = self.run_main([], view, keys='6')
        extra = self.beyond_the_list(view, printed)
        self.assertEqual((code, len(extra)), (0, BARE), extra)
        self.assertEqual(extra[1:], ['Switch to 1–6, or Enter to stay › 6',
                                     'Codex is opening here, with your chat "Upload retries" carried over.'])
        self.assertEqual(len(self.rig_.calls.execs), 1)
        self.check(extra)

    def test_a_number(self):
        code, printed = self.run_main(['6'], self.view())
        self.assertEqual((code, len(printed)), (0, NUMBER), printed)
        self.assertEqual(printed, ['Codex is opening here, with your chat "Upload retries" carried over.'])
        self.check(printed)

    def test_switch(self):
        code, printed = self.run_main(['switch'], self.view(email=None, next=True))
        self.assertEqual((code, len(printed)), (0, SWITCH), printed)
        self.assertEqual(printed, ['Codex is opening here, with your chat "Upload retries" carried over.'])
        self.check(printed)

    def with_the_page(self, board, keys='\r'):
        """The bare command with a page running that sends `board`: (exit code, the lines printed beyond the list)."""
        page = dict(copy.deepcopy(VIEW), providers=board)
        keys = list(keys)
        out = io.StringIO()
        with mock.patch.object(main, 'page_state', lambda timeout=5: copy.deepcopy(page)):
            view = main.current_view()
            with mock.patch.object(main, 'read_one_key', lambda: keys.pop(0)), redirect_stdout(out):
                code = main.main([])
        printed = out.getvalue().splitlines()
        extra = self.beyond_the_list(view, printed)
        self.check(extra)
        return code, view, printed, extra

    def test_with_the_page_codex_is_printed_once(self):
        code, view, printed, extra = self.with_the_page(demo.providers(VIEW['now']))
        self.assertEqual((code, extra), (0, ['', 'Switch to 1–6, or Enter to stay › ']))
        self.assertEqual([x for x in printed if 'OpenAI' in x or 'Codex' in x],
                         ['6  maya@northwind.example  OpenAI · Plus'])

    def test_codex_added_and_signed_out_has_no_number_and_one_dim_line(self):
        board = demo.providers(VIEW['now'])
        board['providers'][1].update(state='signed_out', seats=[], plan=None, fit_why='not signed in')
        board['carry'] = False                  # as the board says it: a signed-out Codex is nowhere to carry to
        code, view, printed, extra = self.with_the_page(board)
        self.assertEqual((code, len(extra)), (0, UNDER + 2), extra)
        self.assertEqual(extra, ['', '  Codex  not signed in', '', 'Switch to 1–5, or Enter to stay › '])
        self.assertFalse(any(x.startswith('6  ') for x in printed), printed)

    def test_codex_on_the_mac_and_not_added_is_one_offer_line(self):
        code, view, printed, extra = self.with_the_page(demo.providers(VIEW['now'], codex=False))
        self.assertEqual((code, len(extra)), (0, UNDER + 2), extra)
        self.assertEqual(extra, ['', 'Codex is on this Mac too: switchboard add codex adds it.', '',
                                 'Switch to 1–5, or Enter to stay › '])

    def test_without_the_key_press_the_rest_shows_with_why(self):
        code, printed = self.run_main(['6', '--why'], self.view())
        self.assertGreater(len(printed), NUMBER)
        self.assertTrue(any(VERSION.search(line) for line in printed))
        self.assertTrue(any(SIGN_IN.search(line) for line in printed))

    def test_the_bare_command_once_routing_is_on(self):
        # Routing turned on in a throwaway home (Claude Code's and Codex's folders and the data folder all made up):
        # the bare command then adds a blank line and routing's lines under the list, at most four of them.
        root = tempfile.mkdtemp(prefix='sb-road-routing-')
        self.addCleanup(shutil.rmtree, root, True)
        claude, codex = os.path.join(root, '.claude'), os.path.join(root, '.codex')
        os.makedirs(claude)
        os.makedirs(codex)
        with open(os.path.join(claude, 'settings.json'), 'w', encoding='utf-8') as f:
            f.write(SETTINGS_TEXT)
        env = mock.patch.dict(os.environ, {'SWITCHBOARD_HOME': os.path.join(root, 'data'),
                                           'CLAUDE_CONFIG_DIR': claude, 'CODEX_HOME': codex})
        env.start()
        self.addCleanup(env.stop)
        wire.turn_on(kit=dataclasses.replace(self.rig_.kit, routes=_Routes()), claude_dir=claude, codex_home=codex,
                     versions=NEW)
        view = self.view()
        with mock.patch.object(models, '_versions', lambda *a, **k: list(NEW)):
            code, printed = self.run_main([], view, keys='6')
        extra = self.beyond_the_list(view, printed)
        self.assertEqual((code, len(extra)), (0, ROUTING_ON + BARE), extra)
        self.assertLessEqual(ROUTING_ON, 5)
        self.assertEqual(extra[:2], ['', 'Routing is on.'])
        self.assertEqual(extra[ROUTING_ON + 1:], ['Switch to 1–6, or Enter to stay › 6',
                                                  'Codex is opening here, with your chat "Upload retries" carried over.'])
        self.check(extra)


class ClaudeRoad(Sandbox):
    # The Claude rows' road, counted through the real switch and everything it prints (the stand-in engine of
    # tests/fakes_accounts_api.py, the page not running), with Codex's row in the list. Not seen here: the line the
    # running page sends back after a switch (an account's browser profile, Claude in Chrome, or the desktop app's line
    # the first time): it is said only when the page answers, and no page runs in these tests.
    def setUp(self):
        super().setUp()
        hold(self.fake, A, active=True, week=(20, iso_in(3 * 86400)))
        hold(self.fake, B, week=(30, iso_in(4 * 86400)))
        rig = KitCase()
        rig.root = self.tmp
        self.r = KitCase.rig(rig, tty=True, claude_chats=[upload], codex_found_=codex_found(self.tmp, signed_in=True))
        for target, name, value in ((main, 'terminal', lambda: True),
                                    (cli, 'default_kit', lambda reach=None: dataclasses.replace(self.r.kit,
                                                                                                out=sys.stdout))):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)

    def run_main(self, args, keys=()):
        keys = list(keys)
        out = io.StringIO()
        with mock.patch.object(main, 'read_one_key', lambda: keys.pop(0)), redirect_stdout(out):
            code = main.main(list(args))
        return code, out.getvalue().splitlines()

    def test_the_bare_command_and_a_claude_row(self):
        code, printed = self.run_main([], keys='2')
        self.assertEqual(code, 0)
        self.assertEqual(printed[-BARE:], ['', 'Switch to 1–3, or Enter to stay › 2',
                                           'Claude Code is on ana@home.example.'])
        self.assertTrue(printed[-BARE - 1].startswith('   weekly'), printed)     # the list's last line, then those
        self.assertEqual(self.calls('switch')[-1][0], 'switch')

    def test_a_number(self):
        self.assertEqual(self.run_main(['2']), (0, ['Claude Code is on ana@home.example.']))

    def test_switch(self):
        code, printed = self.run_main(['switch'])
        self.assertEqual((code, len(printed)), (0, SWITCH), printed)
        self.assertEqual(printed, ['Claude Code is on ana@home.example.'])


class AddRoad(Sandbox):
    def setUp(self):
        super().setUp()
        self.signed_in(A, 'org-a')

    def kit(self, tty, answer='n'):
        rig = KitCase()
        rig.root = self.tmp
        self.r = KitCase.rig(rig, codex_added=False, codex_found_=codex_found(self.tmp, signed_in=True), tty=tty)

        def asked(prompt):                              # a terminal shows the question and what was typed
            sys.stdout.write(prompt + answer + '\n')
            return answer
        p = mock.patch.object(cli, 'default_kit', lambda reach=None: dataclasses.replace(self.r.kit, out=sys.stdout,
                                                                                         ask=asked))
        p.start()
        self.addCleanup(p.stop)

    def test_add_without_a_terminal(self):
        # No terminal, no Codex: it is added only where a person reads what its row will do.
        self.kit(tty=False)
        code, out = self.run_cli('add')
        printed = out.splitlines()
        self.assertEqual((code, len(printed)), (0, ADD_NO_TERMINAL), printed)
        self.assertEqual(printed, ['✓ Saved ana@work.example', 'To add another, run switchboard add at a terminal.'])
        self.assertNotIn('codex', self.r.choices.added())

    def test_add_at_a_terminal_up_to_the_next_sign_in(self):
        # Codex's question is answered no (Enter's answer too): the road goes on to the next Claude sign-in, the
        # account add is for. A yes adds Codex and asks one more question (test_one_add, test_codex_asked_once).
        self.kit(tty=True, answer='n')

        def stop(seconds):
            if seconds == main.POLL:
                raise KeyboardInterrupt
        with mock.patch.object(main, '_sleep', stop):
            code, out = self.run_cli('add', tty=FakeTty([]))
        printed = [line for line in out.splitlines() if line]
        self.assertEqual((code, len(printed)), (0, ADD), printed)
        self.assertEqual(printed[0], '✓ Saved ana@work.example')
        self.assertEqual(printed[1:4],
                         ["Pressing the Codex row asks no more: it sends your folder's newest chat,",
                          "your words and files, to OpenAI under your ChatGPT plan and OpenAI's terms.",
                          'Add Codex too? [y/N] n'])
        self.assertEqual(printed[4], main.ADD_WITHOUT_PAGE)
        self.assertEqual(printed[-1], 'In Claude Code, run /login and sign in to the next account.')
        self.assertNotIn('codex', self.r.choices.added())
        for line in printed:
            self.assertNotIn('/', line.replace('/login', '').replace('[y/N]', ''))
            self.assertIsNone(VERSION.search(line), line)


if __name__ == '__main__':
    unittest.main()
