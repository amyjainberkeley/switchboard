import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The command line on the account engine: switchboard (the list), switch, add, remove, alias,
# run <account> -- ..., auto [on|off], open <link>, owner <link>, doctor, and their help.
# The page is not running (nothing answers on the port this test picks); the engine is tests/fakes_accounts_api.py.
import io
import os
import plistlib
import shutil
import socket
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import __main__ as cli
from switchboard import artifacts, core, engine, history, keep, server
from switchboard.accounts import api
from fakes_accounts_api import install

A, B, C = 'ana@work.example', 'ana@home.example', 'bo@lab.example'


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-cli-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        home = os.path.join(self.tmp, 'home')
        os.makedirs(os.path.join(home, '.claude', 'projects'))
        env = mock.patch.dict(os.environ, {'HOME': home, 'SWITCHBOARD_HOME': os.path.join(self.tmp, 'data'),
                                           'CLAUDE_CONFIG_DIR': os.path.join(home, '.claude'),
                                           'SWITCHBOARD_PORT': str(free_port()), 'NO_COLOR': '1'})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = False
        self.fake = install(self)
        keep.reset()
        self.addCleanup(keep.reset)
        from switchboard import notify_hooks, statusline
        for target, name, value in ((cli, 'terminal', lambda: False), (artifacts, 'refresh', lambda: None),
                                    (server, 'NOTIFY_RUN', lambda cmd: 0),
                                    (statusline, 'precompile', lambda *a, **k: None),      # no bytecode in the source
                                    (notify_hooks, 'precompile', lambda *a, **k: None)):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.dict(server.GUARD, {'at': -1e9, 'label': None}, clear=True)
        p.start()
        self.addCleanup(p.stop)

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = cli.main(list(args))
            except core.SwitchError as e:
                code = 'refused: %s' % e
        return code, out.getvalue(), err.getvalue()

    def two(self):
        a = self.fake.hold(A, 'org-w')
        b = self.fake.hold(B)
        self.fake.sign_in(A, 'org-w')
        return a, b


class Lists(Cli):
    def test_the_list_shows_the_engines_accounts(self):
        self.two()
        code, out, _ = self.run_cli('status')
        self.assertEqual(code, 0)
        self.assertIn('Claude Code is on %s' % A, out)
        self.assertIn(B, out)

    def test_a_login_not_kept_yet_is_kept_by_the_list(self):
        self.fake.hold(A, 'org-w')
        self.fake.sign_in(C, 'org-ivy')
        code, out, _ = self.run_cli('status')
        self.assertEqual(code, 0)
        self.assertIn((C, 'org-ivy'), engine.saved_identities())
        self.assertIn('Kept %s' % C, out)

    def test_a_removed_account_is_not_kept_again_by_the_list(self):
        self.fake.hold(A, 'org-w')
        self.fake.sign_in(C, 'org-ivy')
        keep.let_go((C, 'org-ivy'))
        self.run_cli('status')
        self.assertNotIn((C, 'org-ivy'), engine.saved_identities())

    def test_another_switcher_is_the_lists_last_line(self):
        self.two()
        folder = os.path.join(core.home(), 'Library', 'LaunchAgents')
        os.makedirs(folder)
        with open(os.path.join(folder, 'com.example.claude-switch.plist'), 'wb') as f:
            plistlib.dump({'Label': 'com.example.claude-switch', 'ProgramArguments': ['/opt/switch']}, f)
        code, out, _ = self.run_cli('status')
        self.assertIn("Another program is also switching Claude Code's login (com.example.claude-switch). "
                      "Turn it off first.", out)


class Switching(Cli):
    def test_switch_name_goes_through_the_engine_and_is_said_once(self):
        a, b = self.two()
        code, out, _ = self.run_cli('switch', B)
        self.assertEqual(code, 0)
        self.assertIn(('switch', b, 'manual', 'cli', 'Picked in a terminal.'), self.fake.calls)
        self.assertIn('Claude Code is on %s.' % B, out)
        self.assertEqual([r['text'] for r in history.read() if r['kind'] == 'switch'],
                         ['Switched Claude Code to %s' % B])
        self.assertEqual(core.load()['active'], B)

    def test_switch_by_number_and_alias(self):
        a, b = self.two()
        self.fake.by_id[b]['alias'] = 'home'
        self.assertEqual(self.run_cli('2')[0], 0)
        self.assertEqual(self.fake.block()['emailAddress'], B)
        self.assertEqual(self.run_cli('switch', '1')[0], 0)
        self.assertEqual(self.fake.block()['emailAddress'], A)
        self.assertEqual(self.run_cli('use', 'home')[0], 0)
        self.assertEqual(self.fake.block()['emailAddress'], B)

    def test_switch_alone_goes_to_the_next_one_passing_over_a_dead_login(self):
        a, b = self.two()
        self.fake.hold(C, status='needs_login')
        self.run_cli('switch')
        self.assertEqual(self.fake.block()['emailAddress'], B)
        self.run_cli('switch')
        self.assertEqual(self.fake.block()['emailAddress'], A)      # C needs a fresh sign-in: passed over

    def test_switch_alone_passes_over_an_account_kept_out_of_auto_switch_and_naming_it_goes_there(self):
        a, b = self.two()
        self.fake.hold(C)
        self.fake.auto['rules'] = {b: 'out'}
        self.run_cli('switch')
        self.assertEqual(self.fake.block()['emailAddress'], C)      # B is kept out of auto-switch: passed over
        self.run_cli('switch')
        self.assertEqual(self.fake.block()['emailAddress'], A)
        self.assertEqual(self.run_cli('switch', B)[0], 0)           # named, it goes there
        self.assertEqual(self.fake.block()['emailAddress'], B)
        self.assertEqual(self.run_cli('1')[0], 0)
        self.assertEqual(self.run_cli('use', B)[0], 0)
        self.assertEqual(self.fake.block()['emailAddress'], B)
        self.run_cli('switch')                                      # from it, alone, to the next as usual
        self.assertEqual(self.fake.block()['emailAddress'], C)

    def test_switch_alone_goes_to_an_account_used_last(self):
        a, b = self.two()
        self.fake.auto['rules'] = {b: 'last'}
        self.run_cli('switch')
        self.assertEqual(self.fake.block()['emailAddress'], B)

    def test_switch_alone_with_every_other_account_kept_out_says_why_and_changes_nothing(self):
        a, b = self.two()
        self.fake.auto['rules'] = {b: 'out'}
        before = len([x for x in self.fake.calls if x[0] == 'switch'])
        code, _, _ = self.run_cli('switch')
        self.assertEqual(code, 'refused: Every other account is kept out of auto-switch; name one to move there: '
                               'switchboard switch NAME')
        c = self.fake.hold(C, status='needs_login')
        code, _, _ = self.run_cli('switch')
        self.assertEqual(code, 'refused: Every other account needs a fresh sign-in or is kept out of auto-switch; '
                               'name a kept-out one to move there: switchboard switch NAME')
        self.fake.auto['rules'] = {b: 'out', c: 'out'}
        code, _, _ = self.run_cli('switch')
        self.assertTrue(code.startswith('refused: Every other account is kept out of auto-switch'), code)
        self.assertEqual(len([x for x in self.fake.calls if x[0] == 'switch']), before)     # nothing changed
        self.assertEqual(self.fake.block()['emailAddress'], A)

    def test_switch_alone_with_one_account_says_so(self):
        self.fake.hold(A, 'org-w')
        self.fake.sign_in(A, 'org-w')
        code, _, _ = self.run_cli('switch')
        self.assertEqual(code, 'refused: There is no other account to switch to. Add one with: switchboard add')

    def test_a_refusal_is_the_engines_sentence(self):
        self.two()
        self.fake.fail['switch'] = api.AccountsError('keychain_locked', "macOS's Keychain is locked: unlock the Mac.")
        code, _, _ = self.run_cli('switch', B)
        self.assertEqual(code, "refused: macOS's Keychain is locked: unlock the Mac.")
        self.assertEqual(self.fake.block()['emailAddress'], A)

    def test_an_unknown_account(self):
        self.two()
        self.assertEqual(self.run_cli('switch', 'nobody')[0],
                         'refused: No account called "nobody". Run `switchboard` to see them.')


class Accounts(Cli):
    def test_remove_with_yes_forgets_it_and_lets_it_go(self):
        a, b = self.two()
        code, out, _ = self.run_cli('remove', B, '--yes')
        self.assertEqual(code, 0)
        self.assertIn(('remove', b), self.fake.calls)
        self.assertTrue(keep.is_let_go((B, '')))
        self.assertEqual(out.strip(), 'Removed %s.' % B)
        self.assertTrue(any(r['kind'] == 'removed' and r.get('account') == B for r in history.read()))

    def test_remove_the_account_in_use_says_claude_code_stays_on_it(self):
        self.two()
        _, out, _ = self.run_cli('remove', A, '--yes')
        self.assertIn('Claude Code stays on it until you switch.', out)

    def test_remove_without_yes_and_no_terminal_asks_for_it(self):
        self.two()
        self.assertEqual(self.run_cli('remove', B)[0], 'refused: Add --yes to remove an account without being asked.')
        self.assertFalse([c for c in self.fake.calls if c[0] == 'remove'])

    def test_remove_at_a_terminal_asks_first(self):
        self.two()
        with mock.patch.object(cli, 'terminal', lambda: True), mock.patch('builtins.input', return_value='n'):
            code, out, _ = self.run_cli('remove', B)
        self.assertEqual(code, 1)
        self.assertFalse([c for c in self.fake.calls if c[0] == 'remove'])
        with mock.patch.object(cli, 'terminal', lambda: True), mock.patch('builtins.input', return_value='y'):
            self.assertEqual(self.run_cli('remove', B)[0], 0)

    def test_alias_sets_and_clears(self):
        a, b = self.two()
        code, out, _ = self.run_cli('alias', B, 'home')
        self.assertEqual((code, out.strip()), (0, '%s is home now.' % B))
        self.assertEqual(self.fake.by_id[b]['alias'], 'home')
        code, out, _ = self.run_cli('alias', 'home')
        self.assertEqual((code, out.strip()), (0, '%s has no short name now.' % B))
        self.assertIsNone(self.fake.by_id[b]['alias'])

    def test_add_keeps_the_login_claude_code_is_on(self):
        self.fake.sign_in(C, 'org-ivy')
        with mock.patch.object(cli, 'open_tty', lambda: None):
            code, out, _ = self.run_cli('add')
        self.assertEqual(code, 0)
        self.assertIn((C, 'org-ivy'), engine.saved_identities())

    def test_run_hands_claude_its_arguments_and_its_exit_code(self):
        self.two()
        self.fake.run_code = 3
        with mock.patch.object(cli.connect, 'claude_bin', lambda: '/usr/local/bin/claude'):
            code, _, _ = self.run_cli('run', B, '--', '-p', 'hello', '--help')
        self.assertEqual(code, 3)
        self.assertIn(('run_as', B, ['-p', 'hello', '--help']), self.fake.calls)

    def test_run_alone_or_without_the_dashes_shows_how(self):
        self.two()
        self.assertEqual(self.run_cli('run')[0], 2)
        self.assertEqual(self.run_cli('run', B, '-p', 'x')[0], 2)
        self.assertFalse([c for c in self.fake.calls if c[0] == 'run_as'])

    def test_run_without_claude_code_says_to_install_it(self):
        self.two()
        with mock.patch.object(cli.connect, 'claude_bin', lambda: None):
            code, _, _ = self.run_cli('run', B, '--')
        self.assertIn('Claude Code is not installed', code)


class Auto(Cli):
    def test_auto_on_and_off_with_the_page_down(self):
        self.two()
        with mock.patch.object(engine.HOST, 'ensure'):
            code, out, _ = self.run_cli('auto', 'on')
        self.assertEqual(code, 0)
        self.assertTrue(self.fake.auto['enabled'])
        self.assertTrue(core.settings().get('auto_set_by_you'))
        self.assertIn('Auto-switch is on: at 90% 5-hour · 95% weekly, using what refills first.', out)
        with mock.patch.object(engine.HOST, 'ensure'):
            self.run_cli('auto', 'off')
        self.assertFalse(self.fake.auto['enabled'])

    def test_auto_on_is_refused_while_another_program_switches(self):
        self.two()
        folder = os.path.join(core.home(), 'Library', 'LaunchAgents')
        os.makedirs(folder)
        with open(os.path.join(folder, 'com.example.claude-switch.plist'), 'wb') as f:
            plistlib.dump({'Label': 'com.example.claude-switch', 'ProgramArguments': ['/opt/switch']}, f)
        code, _, _ = self.run_cli('auto', 'on')
        self.assertEqual(code, "refused: Another program is also switching Claude Code's login "
                               "(com.example.claude-switch). Turn it off first.")
        self.assertFalse(self.fake.auto['enabled'])


class Links(Cli):
    URL = 'https://claude.ai/artifact/0123456789abcdef0123'

    def test_owner_known_and_not_known(self):
        aid = artifacts.art_id(self.URL)
        with core.locked() as st:
            st['artifacts'].append(dict(artifacts.blank_item(aid, time.time()), owner=A, src='seen'))
        code, out, _ = self.run_cli('owner', self.URL)          # artifacts.owner_of's one line
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith('%s (it loaded in its profile).' % A), out)
        self.assertIn('Open it as its owner: switchboard open %s' % self.URL, out)
        code, out, _ = self.run_cli('owner', 'https://claude.ai/artifact/ffffffffffffffffffff')
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith('Not known yet. switchboard open https://claude.ai/artifact/ffffffffffffffffffff'),
                        out)

    def test_owner_refuses_what_is_not_an_artifact_link(self):
        self.assertEqual(self.run_cli('owner', 'https://example.com/x')[0],
                         'refused: That is not a claude.ai artifact link.')

    def test_open_with_the_page_down_uses_open_link(self):
        said = 'Opened as %s: Plan.' % A                         # artifacts.open_link says it in one line
        with mock.patch.object(artifacts, 'open_link', return_value={'email': A, 'checked': True, 'title': 'Plan',
                                                                     'said': said}) as go:
            code, out, _ = self.run_cli('open', self.URL)
        go.assert_called_once_with(self.URL)
        self.assertEqual((code, out.strip()), (0, said))


class Doctor(Cli):
    def test_doctor_reads_the_engines_health(self):
        self.two()
        self.fake.health_extra = {'keychain': {'ok': False, 'line': "macOS's Keychain is locked: unlock the Mac."}}
        with mock.patch.object(cli, 'page_running', lambda: False), \
                mock.patch.object(cli.linkapp, 'status', lambda fresh=False: {'installed': False, 'on': False}), \
                mock.patch.object(cli.browser, 'present', lambda: False), \
                mock.patch.object(cli.browser, 'profiles', lambda: {}):
            code, out, _ = self.run_cli('doctor')
        self.assertEqual(code, 1)
        self.assertIn(' ok  Accounts: 2 kept', out)
        self.assertIn(" NO  macOS's Keychain is locked: unlock the Mac.", out)
        self.assertIn(' ok  No other program switches Claude Code\'s login', out)
        self.assertIn(' --  Automatic switching: off', out)

    def test_doctor_on_a_skeleton_engine_says_not_ready(self):
        with mock.patch.object(api, 'accounts', side_effect=NotImplementedError('not built yet')), \
                mock.patch.object(cli, 'page_running', lambda: False), \
                mock.patch.object(cli.linkapp, 'status', lambda fresh=False: {'installed': False, 'on': False}), \
                mock.patch.object(cli.browser, 'present', lambda: False), \
                mock.patch.object(cli.browser, 'profiles', lambda: {}):
            _, out, _ = self.run_cli('doctor')
        self.assertIn(' NO  Account engine: not ready in this copy', out)


class CommandHelp(Cli):
    def test_the_help_names_each_account_command(self):
        code, out, _ = self.run_cli('help', 'all')
        for line in ('switchboard switch [NAME]', 'switchboard remove NAME [--yes]', 'switchboard alias NAME [SHORT]',
                     'switchboard run [NAME] [--bare]', 'switchboard owner LINK', 'switchboard open LINK',
                     'switchboard doctor', 'switchboard auto [on|off]'):
            self.assertIn(line, out)

    def test_each_account_command_has_its_own_help(self):
        for cmd in ('switch', 'remove', 'alias', 'run', 'owner'):
            code, out, _ = self.run_cli(cmd, '--help')
            self.assertEqual(code, 0, cmd)
            self.assertTrue(out.startswith('switchboard %s' % cmd), out)


if __name__ == '__main__':
    unittest.main()
