import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# One key press: pressing another provider's row in the list (its number at the prompt, `switchboard N`, or
# `switchboard switch` when that row is next) does what `switchboard continue --to PROVIDER` does, carrying this
# folder's newest chat over and starting that provider's app here, and prints ONE line. No question: at a terminal,
# pressing the row the person added is the yes, for that move only. A folder that stays on Claude, or never goes
# to that provider, is refused in one line. What continue prints along the way is said with --why and goes to the
# log. A real problem is said in one line, with what to do. `continue` typed by hand keeps its own question.
#
# Made-up chats, programs and numbers only: the adapters are the stand-ins of tests/fakes_provider_kit.py, and an
# exec is only recorded.
import copy
import dataclasses
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from fakes_provider_kit import NOW, KitCase, chat, codex_found, codex_limits  # noqa: E402
from switchboard import __main__ as main  # noqa: E402
from switchboard import connect, core, term  # noqa: E402
from switchboard.providers import cli, contract, words  # noqa: E402
from switchboard.providers.contract import Event, Found, ReadInfo, Transcript  # noqa: E402
from test_one_list import codex_row  # noqa: E402
from test_term import VIEW  # noqa: E402

CARRIED = 'Codex is opening here, with your chat "Upload retries" carried over.'


def upload(folder):
    return chat('claude', folder, name='Upload retries', ago=600.0)


class OneKeyPress(KitCase, unittest.TestCase):
    def press(self, r, why=False):
        code = cli.move('codex', why=why, kit=r.kit)
        return code, r.lines()

    def test_one_line_and_the_chat_carried_over(self):
        r = self.rig(tty=True, claude_chats=[upload])
        self.assertEqual(self.press(r), (0, [CARRIED]))
        self.assertEqual(r.asked, [])                                   # no question
        self.assertIsNone(r.choices.consent(r.folder, 'codex'))         # the yes was for this move only
        (path, argv, env, cwd), = r.calls.execs
        self.assertEqual((argv[1:3], cwd), (('-C', r.folder), r.folder))
        self.assertTrue(argv[3].startswith('Switchboard note h-'))
        self.assertEqual([h.state for h in r.tasks.all], ['started'])
        self.assertEqual([k for _, k, _ in r.said], ['info'])
        self.assertTrue(r.said[0][0].startswith('Started Codex on a note of a chat from Claude in site'))

    def test_no_chat_in_this_folder_opens_it_plain(self):
        r = self.rig(tty=True)
        self.assertEqual(self.press(r), (0, ['Codex is opening here.']))
        (path, argv, env, cwd), = r.calls.execs
        self.assertEqual((argv, cwd), ((r.codex.found_.program.path, '-C', r.folder), r.folder))
        self.assertEqual((r.handoff.made, r.tasks.all, r.asked), ([], [], []))

    def test_the_newest_chat_is_the_one_carried(self):
        def older(f):
            return chat('claude', f, cid='bbbbbbbb-0000-4000-8000-00000000000b', name='Old', ago=5000.0)
        r = self.rig(tty=True, claude_chats=[older, upload])
        self.assertEqual(self.press(r), (0, [CARRIED]))
        # Codex's own chat is the newest here: nothing to carry over, so Codex opens as it is
        def mine(f):
            return chat('codex', f, cid='cccccccc-0000-4000-8000-00000000000c', name='Mine', ago=60.0)
        r = self.rig(tty=True, claude_chats=[upload], codex_chats=[mine])
        self.assertEqual(self.press(r), (0, ['Codex is opening here.']))

    def test_a_chat_with_no_name_is_named_by_its_app(self):
        r = self.rig(tty=True, claude_chats=[lambda f: chat('claude', f, name=None)])
        self.assertEqual(self.press(r), (0, ['Codex is opening here, with your Claude Code chat carried over.']))

    def test_why_says_what_continue_says_along_the_way(self):
        r = self.rig(tty=True, claude_chats=[upload])
        code, lines = self.press(r, why=True)
        note = r.handoff.notes[0]
        self.assertEqual(code, 0)
        self.assertEqual(lines[-1], CARRIED)
        for said in ('Taking the newest chat: Upload retries (Claude Code, 10 min ago).',
                     'Carrying on in Codex 0.169.1 (ChatGPT.app), in this folder.',
                     'The note: %s (%s; 2 places that looked like a secret taken out, best effort; from Upload '
                     'retries).' % (note.path, words.size(note.bytes))):
            self.assertIn(said, lines)

    def logged(self, r, why=False):
        got = []
        with mock.patch.object(core, 'log', got.append):
            code = cli.move('codex', why=why, kit=r.kit)
        return code, got

    def test_the_log_gets_fixed_words_only(self):
        # Never a chat's name in any form (a span with backticks, a secret the scrubber changes, one common word that a
        # replace would find everywhere), never a folder's path, never the note's path: lines made of fixed words.
        for name in ('fix `parse()` in the sync job', 'key sk-ant-api03-' + 'x' * 40, 'the', 'Upload retries'):
            with self.subTest(name=name):
                r = self.rig(tty=True, claude_chats=[lambda f, n=name: chat('claude', f, name=n)])
                code, got = self.logged(r)
                note = r.handoff.notes[0]
                self.assertEqual(code, 0)
                self.assertEqual(got, ['key press: Codex started on a note of a chat from Claude (hop %s, note %s, '
                                       '2 places that looked like a secret taken out); Codex 0.169.1'
                                       % (note.hop, words.size(note.bytes))])

    def test_the_log_holds_no_folder_when_the_work_moved(self):
        r = self.rig(tty=True, claude_chats=[lambda f: chat('claude', f, name='Upload retries',
                                                              work_folder=os.path.join(f, 'api'))])
        os.makedirs(os.path.join(r.folder, 'api'))
        code, got = self.logged(r, why=True)
        self.assertEqual(code, 0)
        self.assertIn('Codex is opening in %s, with your chat "Upload retries" carried over.'
                      % os.path.join(r.folder, 'api'), r.lines())
        self.assertEqual(len(got), 1)
        self.assertNotIn('/', got[0])

    def test_the_log_for_each_other_end(self):
        r = self.rig(tty=True)                                          # no chat: opened as it is
        self.assertEqual(self.logged(r)[1], ['key press: Codex opened with no chat to carry over; Codex 0.169.1'])
        r = self.rig(tty=True, claude_chats=[upload])
        r.choices.set_consent(r.folder, 'codex', 'never', NOW)
        self.assertEqual(self.logged(r)[1], ['key press: nothing was started for Codex (it was refused)'])
        r = self.rig(claude_chats=[upload])                             # no terminal, a yes given before: printed
        r.choices.set_consent(r.folder, 'codex', 'yes', NOW)
        code, got = self.logged(r)
        self.assertEqual(got, ['key press: Codex given as a line to run on a note of a chat from Claude (hop %s, note '
                               '%s, 2 places that looked like a secret taken out); Codex 0.169.1'
                               % (r.handoff.notes[0].hop, words.size(r.handoff.notes[0].bytes))])

    def test_a_folder_that_stays_or_never_goes_there_is_refused_in_one_line(self):
        r = self.rig(tty=True, claude_chats=[upload])
        r.choices.set_consent(r.folder, 'codex', 'never', NOW)
        self.assertEqual(self.press(r), (1, ["You said this folder's chats never go to Codex. switchboard allow codex "
                                             'lifts that.']))
        r = self.rig(tty=True, claude_chats=[upload])
        r.choices.set_stay(r.folder, 'claude', NOW)
        self.assertEqual(self.press(r), (1, ['This folder stays on Claude. switchboard stay off lifts that.']))
        r = self.rig(tty=True)                                          # no chat: refused all the same
        r.choices.set_stay(r.folder, 'claude', NOW)
        self.assertEqual(self.press(r)[0], 1)
        self.assertEqual((r.calls.execs, r.handoff.made), ([], []))

    def test_without_a_terminal_a_press_is_no_yes(self):
        r = self.rig(claude_chats=[upload])
        self.assertEqual(self.press(r), (1, [words.NOT_ASKED.format(to='Codex', vendor='OpenAI', to_id='codex')]))
        self.assertEqual((r.calls.execs, r.handoff.made), ([], []))
        # a yes given before for this folder is used: the note is made and the line to run is printed
        r = self.rig(claude_chats=[upload])
        r.choices.set_consent(r.folder, 'codex', 'yes', NOW)
        code, lines = self.press(r)
        self.assertEqual((code, r.calls.execs), (0, []))
        self.assertEqual(lines[1:2], ['To carry on in Codex, run this in a terminal:'])

    def test_a_real_problem_is_one_line_with_what_to_do(self):
        r = self.rig(tty=True, claude_chats=[upload], codex_found_=Found('codex', installed=False))
        self.assertEqual(self.press(r), (1, ["Codex is not on this Mac. Install OpenAI's Codex (its app or its "
                                             'command line), then try again.']))
        r = self.rig(tty=True, claude_chats=[upload], codex_found_=codex_found(self.root, version='0.155.1'))
        self.assertEqual(self.press(r), (1, ['The newest Codex here (0.155.1) is older than the one that wrote your '
                                             'Codex chats (0.169.1). Update Codex, then run this again.']))
        r = self.rig(tty=True, codex_found_=Found('codex', installed=False))     # no chat: the same line
        self.assertEqual(self.press(r)[0], 1)
        self.assertEqual(r.calls.execs, [])

    def read_as(self, r, **info):
        c = r.claude.chats_[0]
        r.claude.transcripts[c.id] = Transcript(c, (Event(contract.PERSON, 'made-up request about the site'),),
                                                ReadInfo(reader='fake', **info))

    def test_a_chat_still_working_is_said_without_why(self):
        r = self.rig(tty=True, claude_chats=[lambda f: chat('claude', f, name='Upload retries', working=True)])
        self.assertEqual(self.press(r), (0, [words.STILL_WORKING, CARRIED]))

    def test_a_window_that_may_carry_on_by_itself_is_said_without_why(self):
        r = self.rig(tty=True, claude_chats=[lambda f: chat('claude', f, name='Upload retries', live=True,
                                                              stopped='rate_limit')])
        self.assertEqual(self.press(r), (0, [words.MAY_CARRY_ON.format(source='Claude'), CARRIED]))

    def test_a_chat_read_in_part_is_said_and_not_called_whole(self):
        shape = words.SHAPE_UNKNOWN.format(app='Claude Code', version='9.9.9')
        part = 'Codex is opening here, with part of your chat "Upload retries" carried over.'
        r = self.rig(tty=True, claude_chats=[upload])
        self.read_as(r, degraded=shape)
        self.assertEqual(self.press(r), (0, [shape, part]))
        for stopped in ('bytes', 'time'):
            with self.subTest(stopped=stopped):
                r = self.rig(tty=True, claude_chats=[upload])
                self.read_as(r, stopped=stopped, from_end=True)
                self.assertEqual(self.press(r), (0, ['Only part of that chat could be read, so the note holds less '
                                                     'of it.', part]))
        r = self.rig(tty=True, claude_chats=[upload])
        self.read_as(r, stopped='boundary', from_end=True)              # read back to its own summary: whole
        self.assertEqual(self.press(r), (0, [CARRIED]))
        r = self.rig(tty=True, claude_chats=[upload])
        self.read_as(r, degraded=shape)
        r.choices.set_open_with('codex', contract.IN_APP)
        self.assertEqual(self.press(r)[1][-1], 'Codex is open here with part of your chat "Upload retries" in its '
                                               'message box. Press send to carry on.')

    def test_with_why_a_problem_is_said_once(self):
        shape = words.SHAPE_UNKNOWN.format(app='Claude Code', version='9.9.9')
        r = self.rig(tty=True, claude_chats=[upload])
        self.read_as(r, degraded=shape)
        code, lines = self.press(r, why=True)
        self.assertEqual(lines.count(shape), 1)

    def test_out_is_said_and_it_opens_all_the_same(self):
        r = self.rig(tty=True, claude_chats=[upload])
        r.codex.limits_ = codex_limits(pct=97.0)
        code, lines = self.press(r)
        self.assertEqual((code, lines[1:]), (0, [CARRIED]))
        self.assertEqual(lines[0], 'Codex is out until %s; it opens all the same, since you picked it.'
                         % words.when(NOW + 2 * 86400.0, NOW))

    def test_a_work_folder_outside_the_chats_is_not_asked_about(self):
        other = os.path.join(self.root, 'elsewhere')
        os.makedirs(other)
        r = self.rig(tty=True, claude_chats=[lambda f: chat('claude', f, name='Upload retries', work_folder=other)])
        self.assertEqual(self.press(r), (0, [CARRIED]))
        self.assertEqual(r.asked, [])
        self.assertEqual(r.calls.execs[0][3], r.folder)                 # the chat's own folder

    def test_in_its_app(self):
        r = self.rig(tty=True, claude_chats=[upload])
        r.choices.set_open_with('codex', contract.IN_APP)
        self.assertEqual(self.press(r), (0, ['Codex is open here with your chat "Upload retries" in its message '
                                             'box. Press send to carry on.']))
        self.assertEqual(len(r.calls.links), 1)

    def test_a_start_that_fails_says_so_with_the_line_to_run(self):
        def refused(*a):
            raise OSError(13, 'Permission denied')
        r = self.rig(tty=True, claude_chats=[upload], execve=refused)
        code, lines = self.press(r)
        self.assertEqual((code, lines[0]), (1, CARRIED))
        self.assertEqual(lines[1], 'Codex could not be started from here (Permission denied). To carry on, run this '
                                   'in a terminal:')
        self.assertEqual([h.state for h in r.tasks.all], ['failed'])

    def test_the_one_line_only_when_the_program_can_still_be_run(self):
        # The one line comes before the program replaces this one, so it is said only after a check that the program
        # is still there and can be run; when not, the problem and the line to run, and no "opening".
        for chats in ([upload], []):
            with self.subTest(chat=bool(chats)):
                r = self.rig(tty=True, claude_chats=chats)
                os.chmod(r.codex.found_.program.path, 0o644)
                code, lines = self.press(r)
                self.assertEqual(code, 1)
                self.assertEqual(lines[0], 'Codex could not be started from here (its program is not there any more, '
                                           'or cannot be run). To carry on, run this in a terminal:')
                self.assertEqual(len(lines), 2)
                self.assertTrue(lines[1].startswith('  '))
                self.assertEqual(r.calls.execs, [])
                self.assertEqual([h.state for h in r.tasks.all], ['failed'] if chats else [])

    def test_no_chat_and_no_terminal_starts_nothing(self):
        r = self.rig()
        code, lines = self.press(r)
        prog = r.codex.found_.program.path
        self.assertEqual((code, r.calls.execs, r.calls.links), (0, [], []))
        self.assertEqual(lines, ['To open Codex here, run this in a terminal:',
                                 '  cd %s && %s -C %s' % (r.folder, prog, r.folder)])

    def test_continue_typed_by_hand_still_asks(self):
        r = self.rig(tty=True, claude_chats=[upload], answers=['n'])
        self.assertEqual(r('continue', '--to', 'codex'), 1)
        self.assertEqual(len(r.asked), 1)
        self.assertTrue(r.text().rstrip().endswith('Nothing was sent; the note was removed.'))


# ---------- the list's prompt, switchboard N and switchboard switch ----------

class WithoutATerminal(KitCase, unittest.TestCase):
    # switchboard N and use N with standard input and output that are not terminals, through main() down to the real
    # key press: its gate is the real one (run._tty), and nothing starts.
    def run_main(self, r, *args):
        from switchboard.providers import run as provider_run
        kit = dataclasses.replace(r.kit, reach=dataclasses.replace(r.reach, tty=provider_run._tty))
        out = io.StringIO()
        with mock.patch.object(main, 'current_view',
                               lambda **k: dict(copy.deepcopy(VIEW), provider_rows=[codex_row()])), \
                mock.patch.object(cli, 'default_kit', lambda reach=None: dataclasses.replace(kit, out=sys.stdout)), \
                mock.patch.object(sys, 'stdin', io.StringIO('6\n')), redirect_stdout(out):
            code = main.main(list(args))
        return code, out.getvalue().splitlines()

    def test_a_chat_is_not_sent_without_a_yes_given_before(self):
        for args in (('6',), ('use', '6'), ('6', '--why')):
            with self.subTest(args=args):
                r = self.rig(claude_chats=[upload])
                code, printed = self.run_main(r, *args)
                self.assertEqual(code, 1)
                self.assertEqual(printed[-1], words.NOT_ASKED.format(to='Codex', vendor='OpenAI', to_id='codex'))
                self.assertEqual((r.calls.execs, r.calls.links, r.handoff.made), ([], [], []))

    def test_no_chat_prints_the_line_to_run(self):
        for args in (('6',), ('use', '6')):
            with self.subTest(args=args):
                r = self.rig()
                code, printed = self.run_main(r, *args)
                self.assertEqual((code, printed[0]), (0, 'To open Codex here, run this in a terminal:'))
                self.assertEqual((r.calls.execs, r.calls.links), ([], []))


class TheKeys(unittest.TestCase):
    def setUp(self):
        self.moved = []
        for target, name, value in ((main, 'identity', lambda: None), (main, 'width', lambda: None),
                                    (connect, 'claude_bin', lambda: '/made/up/claude'),
                                    (main, 'auth_said', lambda after_switch=False: None),
                                    (cli, 'move', lambda pid, why=False, kit=None:
                                     self.moved.append((pid, why)) or print(CARRIED) or 0)):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)

    def view(self, **row):
        return dict(copy.deepcopy(VIEW), provider_rows=[codex_row(**row)])

    def run_main(self, *args, view=None):
        out = io.StringIO()
        with mock.patch.object(main, 'current_view', lambda **k: view or self.view()), redirect_stdout(out), \
                mock.patch.object(main, 'switch', lambda email, v: self.moved.append(('claude', email))):
            code = main.main(list(args))
        return code, out.getvalue()

    def test_the_row_pressed_at_the_prompt(self):
        out = io.StringIO()
        with mock.patch.object(main, 'read_one_key', lambda: '6'), redirect_stdout(out):
            self.assertEqual(main.show(self.view(), ask=True), 0)
        self.assertEqual(self.moved, [('codex', False)])
        self.assertTrue(out.getvalue().endswith('\n\nSwitch to 1–6, or Enter to stay › 6\n' + CARRIED + '\n'),
                        out.getvalue())

    def test_switchboard_and_its_number(self):
        self.assertEqual(self.run_main('6'), (0, CARRIED + '\n'))
        self.assertEqual(self.run_main('6', '--why')[0], 0)
        self.assertEqual(self.run_main('use', '6')[0], 0)
        self.assertEqual(self.moved, [('codex', False), ('codex', True), ('codex', False)])

    def test_switch_goes_there_when_that_row_is_next(self):
        self.assertEqual(self.run_main('switch', view=self.view(next=True)), (0, CARRIED + '\n'))
        self.assertEqual(self.run_main('switch', '--why', view=self.view(next=True))[0], 0)
        self.assertEqual(self.moved, [('codex', False), ('codex', True)])

    def test_switch_stays_among_the_claude_accounts_otherwise(self):
        code, out = self.run_main('switch', view=self.view(email=None))
        self.assertEqual(code, 0)
        self.assertEqual(self.moved, [('claude', 'ana@northwind.example')])     # after the last, the first
        self.assertEqual(out, 'Claude Code is on ana@northwind.example.\n')

    def test_the_bare_command_takes_why(self):
        with mock.patch.object(main, 'terminal', lambda: True), \
                mock.patch.object(main, 'saved_identities', lambda: [('x', '')]), \
                mock.patch.object(main, 'keep_now', lambda: False), \
                mock.patch.object(main, 'read_one_key', lambda: '6'):
            self.assertEqual(self.run_main('--why')[0], 0)
        self.assertEqual(self.moved, [('codex', True)])

    def test_the_rows_printed_name_moves_there_as_its_number_does(self):
        # What the row prints (its email when its app told one, else its app's name) or its provider's id, any case.
        own = self.view(email='maya@openai-side.example')
        for args in (('codex',), ('Codex', '--why'), ('maya@openai-side.example',), ('switch', 'codex'),
                     ('use', 'MAYA@openai-side.example')):
            with self.subTest(args=args):
                self.assertEqual(self.run_main(*args, view=own), (0, CARRIED + '\n'))
        self.assertEqual(self.run_main('switch', 'Codex', view=self.view(email=None)), (0, CARRIED + '\n'))
        self.assertEqual(self.moved, [('codex', False), ('codex', True)] + [('codex', False)] * 4)
        with mock.patch.object(main, 'current_view', lambda **k: own), self.assertRaises(core.SwitchError) as e:
            main.use('codex', script=True)                           # a script's switch moves Claude Code only
        self.assertEqual(str(e.exception), main.SCRIPT_CLAUDE_ONLY.format(number=6, name='Codex'))
        self.assertEqual(len(self.moved), 6)
        code, out = self.run_main('Claude', view=own)                # the account list's own provider: as before
        self.assertEqual((code, out.splitlines()[0]), (2, 'Unknown command: Claude. Did you mean claude?'))

    def test_a_claude_account_wins_a_tie(self):
        # The row's email is a Claude account's too (VIEW's ana@northwind.example), or a Claude account's short name
        # is the provider's id: the Claude account is the one meant.
        for args in (('ana@northwind.example',), ('switch', 'ana@northwind.example')):
            with self.subTest(args=args):
                self.assertEqual(self.run_main(*args)[0], 0)
        aliased = self.view(email=None)
        other = next(a for a in aliased['accounts'] if a['email'] not in (VIEW['active'], 'ana@northwind.example'))
        other['alias'] = 'codex'
        for args in (('codex',), ('switch', 'Codex')):
            with self.subTest(args=args):
                self.assertEqual(self.run_main(*args, view=aliased)[0], 0)
        self.assertEqual(self.moved, [('claude', 'ana@northwind.example')] * 2 + [('claude', other['email'])] * 2)

    def test_in_the_demo_nothing_opens(self):
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = True
        with self.assertRaises(core.SwitchError) as e:
            main.move_to('codex')
        self.assertEqual(str(e.exception), "The demo's OpenAI row is made up, so nothing opens.")
        self.assertEqual(self.moved, [])

    def test_the_prompt_reads_the_same_rows_the_list_shows(self):
        state = term.read(self.view(), tz=None)
        self.assertEqual([a['slot'] for a in state['accounts'] if a.get('provider')], [6])


if __name__ == '__main__':
    unittest.main()
