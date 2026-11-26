import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# `switchboard run` with --no-history, --history and --strict, and the folders that run as one account:
# `switchboard folder [NAME|off]`, `switchboard folders [--json]`, and `switchboard run` without NAME in such a folder.
# The engine is tests/fakes_accounts_api.py; Claude Code is never started. Made-up accounts and folders.
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import __main__ as cli
from switchboard import core, foldercmd, keep
from switchboard.accounts import api, folders
from fakes_accounts_api import account_id, install

A, B = 'ana@work.example', 'ana@home.example'
USAGE = 'switchboard run [NAME] [--bare] [--no-history|--history] [--strict] -- [claude arguments]\n'


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix='switchboard-run-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, 'home')
        os.makedirs(os.path.join(self.home, '.claude', 'projects'))
        env = mock.patch.dict(os.environ, {'HOME': self.home, 'SWITCHBOARD_HOME': os.path.join(self.tmp, 'data'),
                                           'CLAUDE_CONFIG_DIR': os.path.join(self.home, '.claude'),
                                           'SWITCHBOARD_PORT': str(free_port()), 'NO_COLOR': '1'})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = False
        self.fake = install(self)
        keep.reset()
        self.addCleanup(keep.reset)
        for target, name, value in ((cli, 'terminal', lambda: False),
                                    (cli.connect, 'claude_bin', lambda: '/usr/local/bin/claude')):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(os.chdir, os.getcwd())
        self.fake.hold(A, 'org-w')
        self.fake.hold(B)
        self.fake.sign_in(A, 'org-w')               # Claude Code is on A
        self.client = self.mk('work', 'client')
        self.src = self.mk('work', 'client', 'src')

    def mk(self, *parts):
        p = os.path.join(self.home, *parts)
        os.makedirs(p, exist_ok=True)
        return p

    def cli(self, *args, where=None):
        if where is not None:
            os.chdir(where)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = cli.main(list(args))
            except core.SwitchError as e:
                code = 'refused: %s' % e
        return code, out.getvalue(), err.getvalue()

    def runs(self):
        return [c for c in self.fake.calls if c[0] == 'run_as']


class NoHistory(Cli):
    def test_it_asks_for_an_apart_run_and_says_the_chat_is_not_kept(self):
        self.fake.run_plain = False
        self.fake.run_code = 3
        code, out, err = self.cli('run', B, '--no-history', '--', '-p', 'hi')
        self.assertEqual((code, out), (3, ''))
        self.assertEqual(self.runs(), [('run_as', B, ['-p', 'hi'])])
        self.assertEqual(self.fake.run_kw['apart'], True)
        self.assertEqual(self.fake.run_kw['bare'], False)
        self.assertEqual(err, 'This chat is not kept: it goes when Claude Code ends.\n')

    def test_named_it_never_runs_on_your_own_folder(self):
        self.cli('run', A, '--no-history', '--')
        self.assertEqual(self.fake.run_kw['strict'], True)

    def test_the_setting_makes_it_the_usual_way_and_history_asks_for_the_shared_way(self):
        core.set_setting(foldercmd.SETTING, foldercmd.APART)
        self.fake.run_plain = False
        code, out, err = self.cli('run', B, '--')
        self.assertEqual((self.fake.run_kw['apart'], self.fake.run_kw['strict']), (True, False))
        self.assertIn('This chat is not kept', err)
        code, out, err = self.cli('run', B, '--history', '--')
        self.assertEqual((self.fake.run_kw['apart'], err), (False, ''))
        self.cli('run', B, '--bare', '--')
        self.assertEqual((self.fake.run_kw['apart'], self.fake.run_kw['bare']), (False, True))

    def test_with_the_setting_the_account_in_use_says_its_chat_is_kept(self):
        core.set_setting(foldercmd.SETTING, foldercmd.APART)
        self.fake.run_plain = True
        code, out, err = self.cli('run', A, '--')
        self.assertEqual((code, out), (0, ''))
        self.assertEqual(err, 'This chat is kept: %s is the account Claude Code is on, so the run uses your own Claude '
                              'Code folder.\n' % A)

    def test_unset_every_run_is_as_before(self):
        for value in (None, 'shared', 'anything else'):
            with self.subTest(setting=value):
                if value is not None:
                    core.set_setting(foldercmd.SETTING, value)
                self.fake.run_plain = False
                code, out, err = self.cli('run', B, '--', '-p', 'x')
                self.assertEqual((code, out, err), (0, '', ''))
                self.assertEqual(self.fake.run_kw, {'cwd': os.getcwd(), 'bare': False, 'apart': False,
                                                    'strict': False})

    def test_history_and_no_history_together_or_an_unknown_word_is_the_usage(self):
        for args in (('run', B, '--history', '--no-history', '--'), ('run', B, '--nope', '--'),
                     ('run', B, '-p', 'x'), ('run', '--', '-p', 'x')):
            with self.subTest(args=args):
                self.assertEqual(self.cli(*args), (2, USAGE, ''))
        self.assertEqual(self.runs(), [])


class Strict(Cli):
    def test_on_the_account_in_use_it_says_why_and_exits_6(self):
        line = '%s is the account Claude Code is on, so a run as it would start on your own Claude Code folder. ' \
               'Nothing was started.' % A
        self.fake.fail['run_as'] = api.AccountsError('in_use', line)
        code, out, err = self.cli('run', A, '--strict', '--', '-p', 'hi')
        self.assertEqual((code, out, err), (6, '', line + '\n'))
        self.assertEqual(self.fake.run_kw, None)            # the fake refused before it ran

    def test_on_another_account_it_runs_as_usual(self):
        self.fake.run_code = 0
        self.assertEqual(self.cli('run', B, '--strict', '--', '-p', 'hi'), (0, '', ''))
        self.assertEqual(self.fake.run_kw['strict'], True)

    def test_other_refusals_stay_as_they_were(self):
        self.fake.fail['run_as'] = api.AccountsError('dead_login', '%s needs sign-in.' % B)
        self.assertEqual(self.cli('run', B, '--strict', '--')[0], 'refused: %s needs sign-in.' % B)


class AFolderThatRunsAsOneAccount(Cli):
    def test_set_it_then_run_without_a_name_inside_it(self):
        code, out, err = self.cli('folder', B, where=self.client)
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(out, 'This folder (~/work/client) runs as %s, with every folder in it: switchboard run starts '
                              'Claude Code as it here.\n' % B)
        self.fake.run_plain = False
        self.fake.run_code = 4
        code, out, err = self.cli('run', '--', '-p', 'hi', where=self.src)
        self.assertEqual((code, out), (4, ''))
        self.assertEqual(self.runs(), [('run_as', account_id(B), ['-p', 'hi'])])
        self.assertEqual(err, 'Running as %s: set for ~/work/client.\n' % B)
        self.cli('run', where=self.src)                     # alone: claude with nothing after it
        self.assertEqual(self.runs()[-1], ('run_as', account_id(B), []))
        self.cli('run', '--no-history', where=self.src)
        self.assertEqual(self.fake.run_kw['apart'], True)

    def test_a_name_wins_over_the_folder(self):
        self.cli('folder', B, where=self.client)
        self.cli('run', A, '--', where=self.src)
        self.assertEqual(self.runs(), [('run_as', A, [])])

    def test_the_nearest_folder_above_wins(self):
        self.cli('folder', A, where=os.path.join(self.home, 'work'))
        self.cli('folder', B, where=self.client)
        self.cli('run', '--', where=self.src)
        self.cli('run', '--', where=self.mk('work', 'other'))
        self.assertEqual([c[1] for c in self.runs()], [account_id(B), account_id(A, 'org-w')])

    def test_through_a_link_to_the_folder(self):
        os.symlink(self.client, os.path.join(self.home, 'client-link'))
        self.cli('folder', B, where=os.path.join(self.home, 'client-link'))
        self.assertEqual([k for k, _ in folders.entries()], [self.client])
        self.cli('run', '--', where=self.src)
        self.assertEqual(self.runs()[-1][1], account_id(B))

    def test_where_none_applies_the_usage_is_as_before(self):
        elsewhere = self.mk('elsewhere')
        self.assertEqual(self.cli('run', '--', '-p', 'hi', where=elsewhere), (2, USAGE, ''))
        code, out, err = self.cli('run', where=elsewhere)
        self.assertEqual((code, out, err), (2, cli.command_help('run') + '\n', ''))
        self.cli('folder', B, where=self.client)
        self.assertEqual(self.cli('run', '--', where=elsewhere), (2, USAGE, ''))
        self.assertEqual(self.runs(), [])

    def test_alone_it_says_what_this_folder_runs_as_and_which_folder_set_it(self):
        self.assertEqual(self.cli('folder', where=self.src),
                         (0, 'No account is set for this folder. switchboard folder NAME sets one.\n', ''))
        self.cli('folder', B, where=self.client)
        self.assertEqual(self.cli('folder', where=self.src),
                         (0, 'This folder runs as %s (set for ~/work/client).\n' % B, ''))

    def test_off_takes_only_this_folders_own_and_says_when_one_above_applies(self):
        self.cli('folder', A, where=os.path.join(self.home, 'work'))
        self.cli('folder', B, where=self.client)
        code, out, _ = self.cli('folder', 'off', where=self.client)
        self.assertEqual((code, out), (0, 'This folder no longer runs as %s.\nA folder above still applies. This '
                                          'folder runs as %s (set for ~/work).\n' % (B, A)))
        code, out, _ = self.cli('folder', 'off', where=self.client)             # twice: safe
        self.assertEqual((code, out), (0, 'This folder has no account of its own.\nA folder above still applies. '
                                          'This folder runs as %s (set for ~/work).\n' % A))
        code, out, _ = self.cli('folder', 'off', where=self.src)
        self.assertEqual(code, 0)
        self.assertEqual([k for k, _ in folders.entries()], [os.path.join(self.home, 'work')])

    def test_an_unknown_account_or_a_gone_folder_is_refused_and_sets_nothing(self):
        self.assertEqual(self.cli('folder', 'nobody@nowhere.example', where=self.client)[0],
                         'refused: No account called nobody@nowhere.example. Run switchboard to see them.')
        gone = self.mk('gone')
        os.chdir(gone)
        os.rmdir(gone)
        self.assertEqual(self.cli('folder', B)[0], 'refused: This folder is gone, so nothing was set.')
        self.assertEqual(folders.entries(), [])

    def test_wrong_words_are_the_usage(self):
        for args, usage in ((('folder', B, 'extra'), 'switchboard folder [NAME|off]\n'),
                            (('folder', '--json'), 'switchboard folder [NAME|off]\n'),
                            (('folders', 'extra'), foldercmd.FOLDERS_USAGE + '\n')):
            with self.subTest(args=args):
                self.assertEqual(self.cli(*args, where=self.client), (2, usage, ''))

    def test_a_removed_account_is_said_when_a_run_finds_it(self):
        self.cli('folder', B, where=self.client)
        self.fake.fail['run_as'] = api.AccountsError('not_found', 'No saved account matches that.')
        code = self.cli('run', '--', where=self.src)[0]
        self.assertTrue(code.startswith('refused: This folder runs as %s (set for ~/work/client), which Switchboard '
                                        'no longer has.' % B), code)


class EveryFolder(Cli):
    def test_folders_lists_each_with_its_account_and_as_json(self):
        self.assertEqual(self.cli('folders'), (0, 'No folder runs as an account. switchboard folder NAME sets the one '
                                                  'you are in.\n', ''))
        self.assertEqual(json.loads(self.cli('folders', '--json')[1]), {'schema': 1, 'folders': []})
        self.cli('folder', B, where=self.client)
        self.cli('folder', A, where=os.path.join(self.home, 'work'))
        code, out, err = self.cli('folders')
        self.assertEqual((code, out, err), (0, '~/work  %s\n~/work/client  %s\n' % (A, B), ''))
        code, out, err = self.cli('folders', '--json')
        doc = json.loads(out)
        self.assertEqual((code, err, sorted(doc)), (0, '', ['folders', 'schema']))
        self.assertEqual([(f['folder'], f['account'], f['folder_found'], f['account_found']) for f in doc['folders']],
                         [(os.path.join(self.home, 'work'), A, True, True), (self.client, B, True, True)])
        for f in doc['folders']:
            self.assertRegex(f['set_at'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')



class EntriesThatNoLongerFit(Cli):
    def setUp(self):
        super().setUp()
        self.gone_folder = self.mk('old')
        self.cli('folder', A, where=self.gone_folder)
        self.cli('folder', B, where=self.client)
        self.cli('folder', A, where=os.path.join(self.home, 'work'))
        os.chdir(self.home)
        shutil.rmtree(self.gone_folder)
        self.fake.remove(B)                         # (the fake's remove leaves the folders: an entry left behind)

    def test_check_lists_each_with_what_is_gone_and_exits_1(self):
        code, out, err = self.cli('folders', '--check')
        self.assertEqual((code, err), (1, ''))
        self.assertEqual(out, '~/old  %s (folder gone)\n~/work/client  %s (account gone)\n'
                              'switchboard folders --prune takes them out.\n' % (A, B))
        doc = json.loads(self.cli('folders', '--check', '--json')[1])
        self.assertEqual([(f['folder'], f['folder_found'], f['account_found']) for f in doc['folders']],
                         [(self.gone_folder, False, True), (self.client, True, False)])
        self.assertEqual(len(folders.entries()), 3)             # check only looks

    def test_prune_takes_them_out_and_says_which_and_twice_is_safe(self):
        code, out, err = self.cli('folders', '--prune')
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(out, 'Took out ~/old  %s (folder gone)\nTook out ~/work/client  %s (account gone)\n' % (A, B))
        self.assertEqual([k for k, _ in folders.entries()], [os.path.join(self.home, 'work')])
        self.assertEqual(self.cli('folders', '--prune'),
                         (0, 'Nothing to take out: every folder here is there, and so is its account.\n', ''))
        self.assertEqual(self.cli('folders', '--check'),
                         (0, 'Every folder here is there, and so is its account.\n', ''))
        self.assertEqual(json.loads(self.cli('folders', '--prune', '--json')[1]), {'schema': 1, 'removed': []})

    def test_prune_as_json_names_what_it_took_out(self):
        code, out, err = self.cli('folders', '--prune', '--json')
        doc = json.loads(out)
        self.assertEqual((code, err, sorted(doc)), (0, '', ['removed', 'schema']))
        self.assertEqual([f['folder'] for f in doc['removed']], [self.gone_folder, self.client])

    def test_accounts_that_cannot_be_read_are_never_pruned(self):
        self.fake.fail['accounts'] = api.AccountsError('keychain_locked', 'Unlock your Mac.')
        self.cli('folders', '--prune')                              # only the folder known to be gone goes
        self.assertEqual([k for k, _ in folders.entries()], [os.path.join(self.home, 'work'), self.client])
        self.assertEqual(json.loads(self.cli('folders', '--json')[1])['folders'][1]['account_found'], None)

    def test_a_refused_prune_as_json_is_still_only_json(self):
        core.DEMO = True
        code, out, err = self.cli('folders', '--prune', '--json')
        self.assertEqual((code, err), (1, ''))
        self.assertEqual(json.loads(out), {'schema': 1, 'removed': [],
                                           'refused': 'The demo has made-up accounts; nothing is taken out.'})
        self.assertEqual(len(folders.entries()), 3)

    def test_check_and_prune_together_are_the_usage(self):
        self.assertEqual(self.cli('folders', '--check', '--prune'), (2, foldercmd.FOLDERS_USAGE + '\n', ''))
        self.assertEqual(foldercmd.FOLDERS_USAGE, 'switchboard folders [--check|--prune] [--json]')


class TheDocs(unittest.TestCase):
    def test_they_say_resume_is_not_changed_by_any_of_this(self):
        line = ('`switchboard resume` is not changed by any of this: it opens a chat as the account it started under '
                '(or `--as NAME`), whatever folder you are in and whatever `run_history` says.')
        for name in ('how-it-works.md', 'commands.md'):
            with open(os.path.join(HERE, '..', 'docs', name), encoding='utf-8') as f:
                self.assertTrue(line in f.read(), name)


class TheHelpSaysItAsTheDocsDo(unittest.TestCase):
    def test_history_names_the_settings_value(self):
        # The docs: "The `run_history` setting set to `apart` ... and `--history` asks for the shared way for one run."
        said = ' '.join(cli.command_help('run').split())
        self.assertIn('--history: the chat is kept for this run when the run_history setting is set to apart', said)
        self.assertNotIn('setting is apart', said)


if __name__ == '__main__':
    unittest.main()
