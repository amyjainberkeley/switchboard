"""`switchboard resume CHAT` opens a chat again as the account it belongs to (resume.py), and a
terminal file for the page (more.chat_terminal).

The chat files are made up in chatfile.py's shapes; the real `claude` is never run (fakes_run' stand-in records
its arguments, folder and environment, and whether the chat's file can be seen from where it runs)."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import io
import json
import os
import stat
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

from fakes_run import (ORG_A, ORG_B, KeepRig, org_line, owner_line, reply_line, user_line, write_chat)
from switchboard import __main__ as cli
from switchboard import chatfile, core, more, resume
from switchboard.accounts import context
from switchboard.core import SwitchError

ID = '0aa0aa0a-1111-4000-8000-000000000001'
ID2 = '0aa0bbbb-2222-4000-8000-000000000002'
ID3 = '7cc0cccc-3333-4000-8000-000000000003'
PROJECT = '-work-site'


class Case(unittest.TestCase):
    def setUp(self):
        self.k = KeepRig()
        self.rig, self.ctx, self.cc = self.k.rig, self.k.ctx, self.k.cc
        self.work = os.path.realpath(os.path.join(self.rig.root, 'work', 'site'))
        os.makedirs(self.work)
        patches = [mock.patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': self.cc, 'HOME': self.rig.home}),
                   context.use(self.ctx)]
        for p in patches:
            p.__enter__()
            self.addCleanup(p.__exit__, None, None, None)

    def tearDown(self):
        self.k.close()

    def chat(self, org=ORG_A, account='uuid-a', cid=ID, age=3600.0, stop='end_turn', cwd=None, project=PROJECT,
             owner=True, org_lines=True):
        cwd = self.work if cwd is None else cwd
        lines = []
        if owner:
            lines.append(owner_line(org, account, cid))
        if org_lines:
            lines.append(org_line(org, cid, cwd))
        lines += [user_line(cid, cwd), reply_line(cid, cwd, stop)]
        return write_chat(self.cc, project, cid, lines, age)

    def resume(self, *args):
        """(exit code, what was said on stderr)."""
        err = io.StringIO()
        code = resume.main(list(args), out=err)
        return code, err.getvalue()

    def refused(self, *args):
        with self.assertRaises(SwitchError) as e:
            self.resume(*args)
        self.assertFalse(os.path.exists(self.k.record), 'Claude Code started')
        return str(e.exception)


class Lookup(Case):
    def test_by_its_id_and_by_a_unique_start(self):
        path = self.chat()
        self.chat(cid=ID3)
        self.assertEqual(resume.find(ID), (ID, path))
        self.assertEqual(resume.find('0AA0aa0a'), (ID, path))
        self.assertEqual(resume.find('0aa0aa'), (ID, path))

    def test_two_chats_starting_alike_are_listed_by_id(self):
        self.chat()
        self.chat(cid=ID2)
        self.assertEqual(self.refused('0aa0'), 'More than one chat has an id that starts with "0aa0":\n  %s\n  %s\n'
                                               'Give more of the id.' % (ID, ID2))

    def test_none(self):
        self.chat()
        self.assertEqual(self.refused('ffff'), 'No chat on this Mac has an id that starts with "ffff". '
                                               '`switchboard chats` lists every chat with its id.')

    def test_something_that_is_no_chat_id(self):
        self.assertEqual(self.refused('../x'), '"../x" is not a chat id. `switchboard chats` lists every chat with '
                                               'its id.')

    def test_helpers_and_set_aside_files_are_not_chats(self):
        write_chat(self.cc, PROJECT, 'agent-0aa0aa0a', ['{}'])
        write_chat(self.cc, PROJECT, ID + '.orphaned-1-x', ['{}'])
        os.makedirs(os.path.join(self.cc, 'projects', PROJECT, ID, 'subagents'))
        self.assertIn('No chat on this Mac', self.refused('0aa0aa0a'))

    def test_the_same_chat_in_two_folders_is_its_newest_file(self):
        self.chat(project='-old-place', age=7200)
        newer = self.chat(age=3600)
        self.assertEqual(resume.find(ID), (ID, newer))

    def test_no_chat_named_says_what_to_give(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(resume.main([]), 2)
        self.assertEqual(out.getvalue(), 'Name the chat to open: switchboard resume CHAT, with its id or the start of '
                                         'it. `switchboard chats` lists every chat with its id.\n')

    def test_words_it_does_not_know(self):
        for args in (['x', '--bogus'], ['x', 'y'], ['x', '--as']):
            with self.subTest(args=args):
                out = io.StringIO()
                with redirect_stdout(out):
                    self.assertEqual(resume.main(args), 2)
                self.assertEqual(out.getvalue(), 'switchboard resume CHAT [--as NAME] [--fork] -- '
                                                 '[claude arguments]\n')


class Opening(Case):
    def test_on_another_account_it_runs_privately_in_the_chats_folder_and_finds_the_chat_through_the_link(self):
        self.chat()                                            # started under A; Claude Code is on B
        self.k.spec(do=[['found', 'projects/%s/%s.jsonl' % (PROJECT, ID)]])
        code, said = self.resume('0aa0aa0a')
        self.assertEqual(code, 0)
        self.assertEqual(said, 'Opening "0aa0aa0a" as a@example.com, the account it started under. Claude Code stays '
                               'on b@example.com everywhere else.\n')
        seen = self.k.seen()
        self.assertEqual(seen['argv'], ['--resume', ID])
        self.assertEqual(os.path.realpath(seen['cwd']), self.work)
        self.assertTrue(seen['env']['CLAUDE_CONFIG_DIR'].startswith(self.k.run_folder() + os.sep))
        self.assertEqual(seen['found'], {'projects/%s/%s.jsonl' % (PROJECT, ID): True})
        self.assertEqual(seen['claude_json']['oauthAccount']['accountUuid'], 'uuid-a')
        self.assertEqual(self.k.leftovers(), [])
        self.assertTrue(os.path.isfile(os.path.join(self.cc, 'projects', PROJECT, ID + '.jsonl')))

    def test_on_the_account_claude_code_is_on_it_is_claude_resume_plainly(self):
        self.chat(org=ORG_B, account='uuid-b')
        code, said = self.resume(ID)
        self.assertEqual(code, 0)
        self.assertEqual(said, 'Opening "0aa0aa0a" as b@example.com, the account it started under, which Claude Code '
                               'is on now.\n')
        seen = self.k.seen()
        self.assertEqual(seen['argv'], ['--resume', ID])
        self.assertIsNone(seen['env']['CLAUDE_CONFIG_DIR'])
        self.assertEqual(os.path.realpath(seen['cwd']), self.work)
        self.assertEqual(self.rig.doc()['claims'], {})

    def test_as_another_account(self):
        self.chat()
        code, said = self.resume(ID, '--as', 'b@example.com')
        self.assertEqual(said, 'Opening "0aa0aa0a" as b@example.com, which Claude Code is on now.\n')
        self.assertIsNone(self.k.seen()['env']['CLAUDE_CONFIG_DIR'])
        os.unlink(self.k.record)
        self.chat(org=ORG_B, account='uuid-b', cid=ID3)
        code, said = self.resume(ID3, '--as', 'a@example.com')
        self.assertEqual(said, 'Opening "7cc0cccc" as a@example.com. Claude Code stays on b@example.com everywhere '
                               'else.\n')
        self.assertIsNotNone(self.k.seen()['env']['CLAUDE_CONFIG_DIR'])

    def test_as_its_own_account_by_name_says_so(self):
        self.chat()
        code, said = self.resume(ID, '--as', 'a@example.com')
        self.assertTrue(said.startswith('Opening "0aa0aa0a" as a@example.com, the account it started under.'), said)

    def test_fork_and_claudes_own_arguments(self):
        self.chat(age=5)                                       # still being written: --fork makes a copy instead
        code, said = self.resume(ID, '--fork', '--', '--model', 'opus')
        self.assertEqual(self.k.seen()['argv'], ['--resume', ID, '--fork-session', '--model', 'opus'])
        self.assertTrue(said.startswith('Opening a copy of "0aa0aa0a" as a@example.com'), said)

    def test_the_exit_code_is_claude_codes(self):
        self.chat()
        self.k.spec(exit=7)
        self.assertEqual(self.resume(ID)[0], 7)


class NeverTwoWriters(Case):
    WORDS = ('That chat is open and working in another window. Two windows writing one chat can lose messages. Close '
             'it there, or add --fork to continue in a copy.')

    def test_written_in_the_last_minute(self):
        self.chat(age=20)
        self.assertEqual(self.refused(ID), self.WORDS)

    def test_a_helper_written_in_the_last_minute(self):
        self.chat(age=3600)
        helper = os.path.join(self.cc, 'projects', PROJECT, ID, 'subagents', 'agent-x.jsonl')
        os.makedirs(os.path.dirname(helper))
        with open(helper, 'w') as f:
            f.write('{}\n')
        self.assertEqual(self.refused(ID), self.WORDS)

    def test_a_turn_open_in_the_last_15_minutes(self):
        self.chat(age=300, stop='tool_use')
        self.assertEqual(self.refused(ID), self.WORDS)

    def test_a_turn_open_for_longer_is_stuck_not_working(self):
        self.chat(age=1200, stop='tool_use')
        self.assertEqual(self.resume(ID)[0], 0)

    def test_a_quiet_finished_chat_opens(self):
        self.chat(age=120)
        self.assertEqual(self.resume(ID)[0], 0)

    def window(self, folder, pid, cid=ID, status='idle'):
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, '%d.json' % pid), 'w') as f:
            json.dump({'pid': pid, 'sessionId': cid, 'cwd': self.work, 'startedAt': 1, 'kind': 'interactive',
                       'status': status, 'entrypoint': 'cli'}, f)

    def test_a_window_that_holds_the_chat_though_it_is_quiet(self):
        self.chat(age=3600)
        self.window(os.path.join(self.cc, 'sessions'), os.getpid())          # a live process holds it, idle
        self.assertEqual(self.refused(ID), self.WORDS)

    def test_a_window_that_ended_or_holds_another_chat_does_not_count(self):
        self.chat(age=3600)
        self.window(os.path.join(self.cc, 'sessions'), 99999)                # its process is gone (a crash)
        self.window(os.path.join(self.cc, 'sessions'), os.getpid(), cid=ID3)
        self.assertEqual(self.resume(ID)[0], 0)

    def test_a_run_that_holds_the_chat_counts_too(self):
        run = os.path.join(self.k.run_folder(), 'run-other', 'sessions')
        self.window(run, os.getpid())
        self.assertTrue(resume.open_in_window(ID, [os.path.join(self.cc, 'sessions'), run]))
        self.assertFalse(resume.open_in_window(ID3, [os.path.join(self.cc, 'sessions'), run]))
        with mock.patch.object(core, 'data_path', lambda name: os.path.join(self.rig.data, name)):
            self.assertTrue(resume.open_in_window(ID))                       # found among the runs by itself


class WhichAccount(Case):
    def test_an_organization_switchboard_does_not_keep(self):
        self.chat(org='org-ffff-0000-4000-8000-00000000000f', account='uuid-f')
        self.chat(cid=ID2)
        self.assertEqual(self.refused('0aa0aa'), "That chat started under an account Switchboard doesn't keep. Open it "
                                                 "as one it does: switchboard resume 0aa0aa --as NAME")

    def test_a_chat_whose_file_does_not_say(self):
        self.chat(owner=False)
        self.assertEqual(self.refused('0aa0aa0a'), "Which account that chat started under is not known: its file "
                                                   "doesn't say. Name one: switchboard resume 0aa0aa0a --as NAME")

    def test_two_kept_accounts_in_one_organization(self):
        self.rig.keep('uuid-c', 'c@example.com', ORG_A)
        self.rig.keep('uuid-b', 'b@example.com', ORG_B)       # Claude Code back on B
        self.chat(account=None)
        self.assertEqual(self.refused('0aa0aa0a'), 'More than one account Switchboard keeps is in the organization '
                                                   'that chat started under. Name one: switchboard resume 0aa0aa0a '
                                                   '--as NAME')
        os.unlink(os.path.join(self.cc, 'projects', PROJECT, ID + '.jsonl'))
        self.chat(account='uuid-c')
        code, said = self.resume(ID)
        self.assertTrue(said.startswith('Opening "0aa0aa0a" as c@example.com, the account it started under.'), said)

    def test_the_same_with_the_pages_look_at_the_chats_as_without_it(self):
        # The review's t5: once the page wrote chats.json, the account id of the chat's start was lost, and two kept
        # accounts in one organization made it "More than one account ...".
        from switchboard import chats
        self.rig.keep('uuid-c', 'c@example.com', ORG_A)
        self.rig.keep('uuid-b', 'b@example.com', ORG_B)
        path = self.chat(account='uuid-c')
        chats.look(st={'accounts': {}}, root=os.path.join(self.cc, 'projects'), folder=core.data_dir())
        entry = chatfile.load_index(chatfile.index_path(core.data_dir()))['chats'][ID]
        self.assertEqual((entry['owner'], entry['owner_account']), (ORG_A, 'uuid-c'))
        self.assertEqual(resume.look(ID, path).owner_account, 'uuid-c')
        code, said = self.resume(ID)
        self.assertTrue(said.startswith('Opening "0aa0aa0a" as c@example.com, the account it started under.'), said)
        doc = chatfile.load_index(chatfile.index_path(core.data_dir()))
        del doc['chats'][ID]['owner_account']                  # a saved look written before it kept the account id
        with open(chatfile.index_path(core.data_dir()), 'w') as f:
            json.dump(doc, f)
        self.assertEqual(resume.look(ID, path).owner_account, 'uuid-c')


class TheChatsFolder(Case):
    def test_a_folder_that_is_gone_is_named(self):
        gone = os.path.join(self.rig.root, 'deleted-project')
        self.chat(cwd=gone)
        self.assertEqual(self.refused(ID), "That chat ran in %s, which is gone, so it can't be opened there." % gone)

    def test_a_folder_the_file_does_not_name(self):
        write_chat(self.cc, PROJECT, ID, [owner_line(ORG_A, 'uuid-a', ID), '{"type":"user"}'])
        self.assertEqual(self.refused(ID), "Which folder that chat ran in is not known (its file doesn't say), so it "
                                           "can't be opened where it ran.")

    def test_an_older_chat_without_account_lines_names_its_folder_in_its_messages(self):
        self.chat(org_lines=False)
        self.assertEqual(self.resume(ID)[0], 0)
        self.assertEqual(os.path.realpath(self.k.seen()['cwd']), self.work)

    def test_a_chat_that_changed_folder_opens_in_the_folder_claude_code_files_it_under(self):
        # It began in work/site and went on in a worktree, where its login also changed: its last lines and its last
        # account line name the worktree. Claude Code keeps it under the folder it began in (its project folder's
        # name is that folder's path with every other character made '-'), so that is where it is found again.
        import re
        wt = os.path.join(self.work, 'wt')
        os.makedirs(wt)
        write_chat(self.cc, re.sub('[^a-zA-Z0-9]', '-', self.work), ID,
                   [owner_line(ORG_A, 'uuid-a', ID), user_line(ID, self.work), reply_line(ID, self.work),
                    org_line(ORG_A, ID, wt), user_line(ID, wt), reply_line(ID, wt)])
        self.assertEqual(self.resume(ID)[0], 0)
        self.assertEqual(os.path.realpath(self.k.seen()['cwd']), self.work)

    def test_its_last_lines_name_its_folder_over_an_older_account_line(self):
        # When the project folder's name settles nothing, the folder its last lines name, not an older account line's.
        newer = os.path.realpath(os.path.join(self.rig.root, 'work', 'newer'))
        os.makedirs(newer)
        write_chat(self.cc, PROJECT, ID, [owner_line(ORG_A, 'uuid-a', ID), org_line(ORG_A, ID, self.work),
                                          user_line(ID, self.work), user_line(ID, newer), reply_line(ID, newer)])
        self.assertEqual(self.resume(ID)[0], 0)
        self.assertEqual(os.path.realpath(self.k.seen()['cwd']), newer)

    def test_the_saved_look_is_carried_on_and_a_stale_one_is_not_used(self):
        path = self.chat(org=ORG_B, account='uuid-b')
        data = os.path.join(self.rig.root, 'look')
        os.makedirs(data)
        size = os.path.getsize(path)
        with open(path, 'a') as f:
            f.write(org_line(ORG_A, ID, self.work, '2026-11-18T05:00:00.000Z') + '\n')
        doc = {'v': 1, 'at': time.time(), 'orgs': {}, 'chats': {ID: {'file': path, 'end': size, 'owner': ORG_B,
                                                                     'steps': [[1.0, ORG_B]], 'cwd': self.work}}}
        with open(chatfile.index_path(data), 'w') as f:
            json.dump(doc, f)
        r = resume.look(ID, path, data_dir=data)
        self.assertEqual((r.owner, [o for _, o in r.steps], r.end), (ORG_B, [ORG_B, ORG_A], os.path.getsize(path)))
        doc['chats'][ID]['file'] = path + '.elsewhere'
        with open(chatfile.index_path(data), 'w') as f:
            json.dump(doc, f)
        r = resume.look(ID, path, data_dir=data)
        self.assertEqual(r.owner, ORG_B)                       # read from the chat's own file


class TheCommand(Case):
    def test_resume_is_a_command_and_claudes_arguments_after_the_double_dash_are_its_own(self):
        seen = []
        with mock.patch.object(cli, 'resume_command', lambda args: seen.append(args) or 0):
            self.assertEqual(cli.main(['resume', ID, '--', '--help']), 0)
        self.assertEqual(seen, [[ID, '--', '--help']])
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(['resume']), 2)
        self.assertIn('`switchboard chats` lists every chat with its id', out.getvalue())

    def test_run_takes_bare_before_the_double_dash(self):
        calls = []
        with mock.patch.object(cli.engine, 'run_as', lambda name, argv, **kw: calls.append((name, argv, kw)) or 3), \
                mock.patch.object(cli.connect, 'claude_bin', lambda: '/x/claude'):
            self.assertEqual(cli.main(['run', 'work', '--bare', '--', '-p', 'hi']), 3)
            self.assertEqual(cli.main(['run', 'work', '--', '-p', 'hi']), 3)
            self.assertEqual(cli.main(['run', 'work']), 3)
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(cli.main(['run', 'work', '--nope']), 2)
        self.assertEqual(out.getvalue(), 'switchboard run [NAME] [--bare] [--no-history|--history] [--strict] -- '
                                         '[claude arguments]\n')
        self.assertEqual([(n, a, kw['bare']) for n, a, kw in calls],
                         [('work', ['-p', 'hi'], True), ('work', ['-p', 'hi'], False), ('work', [], False)])


class ATerminalForThePage(unittest.TestCase):
    def setUp(self):
        self.command = core.data_path(os.path.join('bin', 'switchboard'))
        os.makedirs(os.path.dirname(self.command), exist_ok=True)
        with open(self.command, 'w') as f:
            f.write('#!/bin/sh\nexit 0\n')
        os.chmod(self.command, 0o700)
        self.addCleanup(os.unlink, self.command)
        self.opened = []

    def test_it_writes_a_file_that_opens_the_chat_and_hands_it_to_terminal(self):
        with mock.patch.object(more, 'OPEN', ['/bin/echo']), \
                mock.patch.object(more.subprocess, 'Popen', lambda cmd, **kw: self.opened.append(cmd)):
            path = more.chat_terminal(ID.upper())
        self.assertEqual(path, core.data_path(os.path.join('terminal', 'chat-%s.command' % ID)))
        self.assertEqual(self.opened, [['/bin/echo', path]])
        with open(path) as f:
            self.assertEqual(f.read(), "#!/bin/sh\n# Opened by Switchboard: chat %s, on the account it belongs to, in "
                                       "this window only.\nexec '%s' resume '%s'\n" % (ID, self.command, ID))
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o700)
        os.unlink(path)

    def test_only_a_chat_id(self):
        for bad in ('', 'x', ID + ';rm', '../' + ID, None):
            with self.subTest(bad=bad):
                with self.assertRaises(SwitchError) as e:
                    more.chat_terminal(bad)
                self.assertEqual(str(e.exception), 'That is not a chat id.')
        self.assertEqual(self.opened, [])

    def test_without_the_command_it_says_so(self):
        os.unlink(self.command)
        self.addCleanup(lambda: open(self.command, 'w').close())
        with self.assertRaises(SwitchError) as e:
            more.chat_terminal(ID)
        self.assertEqual(str(e.exception), 'The switchboard command is missing. Run the installer again.')


if __name__ == '__main__':
    unittest.main()
