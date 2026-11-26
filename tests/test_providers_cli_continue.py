import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# switchboard continue (DESIGN 12.3, 9.1, 13), every row word for word with its exit code, on a Kit of stand-ins.
#
# - The yes for a folder is asked only at a terminal (a stand-in tty), never given by a flag, and only after the note
#   is made, with its size and how many places that looked like a secret were taken out. s shows the note (escapes
#   out) and asks again; n removes the note and its sidecar; never does the same and records never.
# - At a terminal it ends in one recorded exec with the Launch's exact argument list, environment and folder, after
#   the hop is `started` and the History line is written. An exec that raises OSError leaves the hop `failed`, a
#   problem line, the line to run, exit 1. --in app opens one link at a terminal. Not at a terminal it prints the
#   line, --in app or not: nothing is started without a person at a terminal.
# - Which chat: one is taken; several are listed (30 at most, then "and N more"), stopped at the limit first, and at a
#   terminal picked by number; --latest; the chat this shell runs in (CLAUDE_CODE_SESSION_ID); --chat by name, by
#   the start of an id, several (ambiguous), none. A headless run is listed only by name.
# - The refusals: no other provider added (and the offer to add one at a terminal), --to not added, the target is
#   the source, no chat, a stay, a never, not asked yet away from a terminal, every provider out, too old, a full
#   disk, already carried on (and --again).
# - The room line with its age and the --ask hint past an hour; the line saying a key in this shell is not used.
# - Every outside value printed (a chat's name) goes through words.safe.
import os
import sys
import unittest
from dataclasses import replace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))

from fakes_provider_kit import (CHAT_A, CHAT_B, NOW, OTHER, KitCase, chat, claude_limits, codex_found,  # noqa: E402
                                codex_limits)
from switchboard.providers.contract import Found  # noqa: E402
from switchboard.providers import contract, run as provider_run, words  # noqa: E402
from switchboard.providers.contract import Event, Hop, ReadInfo, Transcript  # noqa: E402

ROOM = ['Claude: has room.', 'Codex: has room (ChatGPT Pro, weekly 42%, read 12 min ago).']


def a_chat(**kw):
    return lambda folder: chat('claude', folder, **kw)


def question(size, k=2, to='Codex', vendor='OpenAI', frm='Claude', work='', show=True, jobs=True):
    return ("Carry this chat on in {to}? A note of it goes to {vendor}: what you asked, what {frm} answered, tool "
            "results and\nfile names, with anything that looks like a secret taken out as well as Switchboard can "
            "({size}, {k} taken out).\n{to} will then work in this folder and can read its files. What {vendor} does "
            "with it follows {vendor}'s terms and\nthe settings in your {to} account. Switchboard uses the note for "
            "nothing else.\n{jobs}{work}You are asked once for this folder (site).\n"
            "  y      yes, for this folder from now on\n  n      not now\n{s}  never  never from this folder\n"
            ).format(to=to, vendor=vendor, frm=frm, size=size, k=k, work=work,
                     s='  s      show the note first\n' if show else '',
                     jobs='The same yes lets switchboard hand give {to} jobs in this folder. '.format(to=to)
                     if jobs else '')


class Base(KitCase):
    def rig(self, chats=(a_chat(),), **kw):
        return super().rig(claude_chats=chats, **kw)

    def allowed(self, r):
        r.choices.set_consent(r.folder, 'codex', 'yes', NOW - 3600)
        return r


class AtATerminal(Base, unittest.TestCase):
    def test_the_yes_then_one_exec_with_the_launchs_exact_words(self):
        seen = {}

        def execve(path, argv, env, cwd):
            seen['exec'] = (path, tuple(argv), dict(env), cwd)
            seen['hop'] = r.tasks.all[-1].state
            seen['said'] = list(r.said)
            seen['note_there'] = os.path.exists(r.handoff.notes[0].path)

        r = self.rig(tty=True, answers=['y'], execve=execve)
        self.assertEqual(r('continue'), 0)
        note = r.handoff.notes[0]
        size = words.size(note.bytes)
        self.assertEqual(r.text(), '\n'.join(ROOM + [
            'Making the note from `Fix the login page` ...',
            question(size).rstrip('\n'),
            'Carrying on in Codex 0.169.1 (ChatGPT.app), in this folder.',
            'The note: %s (%s; 2 places that looked like a secret taken out, best effort; from Fix the login page).'
            % (note.path, size)]) + '\n')
        self.assertEqual(r.asked, ['> '])
        prog = r.codex.found_.program
        first = words.FIRST_WORDS.format(hop=note.hop, path=note.path, from_name=OTHER)
        self.assertEqual(seen['exec'], (prog.path, (prog.path, '-C', r.folder, first),
                                        provider_run.child_env(r.reach.env), r.folder))
        self.assertEqual(seen['hop'], 'started')
        self.assertTrue(seen['note_there'])
        self.assertEqual(seen['said'], [
            ('Chats in site may go to Codex', 'setting', {'to': 'Codex'}),
            ('Started Codex on a note of a chat from Claude in site (note %s, 2 places that looked like a secret '
             'taken out)' % size, 'info', {'frm': 'Claude', 'to': 'Codex', 'reason': 'carry on'})])
        hop = r.tasks.all[0]
        self.assertEqual((hop.kind, hop.from_provider, hop.from_chat, hop.to_provider, hop.handoff, hop.removed),
                         ('continue', 'claude', CHAT_A, 'codex', note.path, 2))
        self.assertEqual(r.choices.consent(r.folder, 'codex'), 'yes')

    def test_the_question_comes_after_the_note_is_made(self):
        state = {}

        def look(prompt):
            state['notes'] = list(r.handoff.notes)
            state['exists'] = os.path.exists(r.handoff.notes[0].path)
            state['hops'] = list(r.tasks.all)
            return 'n'

        r = self.rig(tty=True, answers=[look])
        self.assertEqual(r('continue'), 1)
        self.assertEqual(len(state['notes']), 1)
        self.assertTrue(state['exists'])
        self.assertEqual(state['hops'], [])             # nothing is in the ledger before the yes
        self.assertIn('(%s, 2 taken out)' % words.size(state['notes'][0].bytes), r.text())

    def test_s_shows_the_note_then_asks_again(self):
        r = self.rig(tty=True, answers=['s', 'y'])
        r.claude.transcripts[CHAT_A] = Transcript(
            chat('claude', r.folder), (Event(contract.PERSON, 'make the page \x1b[31mred\x1b[0m'),), ReadInfo())
        self.assertEqual(r('continue'), 0)
        self.assertEqual(r.asked, ['> ', '> '])
        self.assertIn('> make the page red\n', r.text())
        self.assertNotIn('\x1b', r.text())
        self.assertEqual(r.text().count('Carry this chat on in Codex?'), 2)
        self.assertEqual(len(r.calls.execs), 1)

    def test_n_removes_the_note_and_its_sidecar(self):
        for answer in ('n', '', 'later'):
            with self.subTest(answer=answer):
                r = self.rig(tty=True, answers=[answer])
                self.assertEqual(r('continue'), 1)
                note = r.handoff.notes[0]
                self.assertTrue(r.text().endswith('Nothing was sent; the note was removed.\n'))
                self.assertFalse(os.path.exists(note.path))
                self.assertFalse(os.path.exists(note.meta_path))
                self.assertEqual((r.calls.execs, r.calls.links, r.tasks.all, r.said), ([], [], [], []))
                self.assertIsNone(r.choices.consent(r.folder, 'codex'))

    def test_never_removes_it_and_is_kept(self):
        r = self.rig(tty=True, answers=['never'])
        self.assertEqual(r('continue'), 1)
        self.assertFalse(os.path.exists(r.handoff.notes[0].path))
        self.assertEqual(r.choices.consent(r.folder, 'codex'), 'never')
        self.assertEqual(r.said, [('Chats in site never go to Codex', 'setting', {'to': 'Codex'})])
        self.assertTrue(r.text().endswith('Nothing was sent; the note was removed.\n'))

    def test_the_question_says_the_yes_covers_jobs_only_for_a_provider_that_takes_them(self):
        r = self.rig(chats=(), codex_chats=(lambda f: chat('codex', f, cid=CHAT_B, name='Ship it'),), tty=True,
                     answers=['n'])
        self.assertEqual(r('continue'), 1)
        size = words.size(r.handoff.notes[0].bytes)
        self.assertIn(question(size, to='Claude', vendor='Anthropic', frm='Codex', jobs=False), r.text())

    def test_a_work_plan_is_named_in_the_question(self):
        r = self.rig(tty=True, answers=['n'])
        r.codex.limits_ = codex_limits(plan='enterprise')
        r('continue')
        size = words.size(r.handoff.notes[0].bytes)
        self.assertIn(question(size, work="This is a work plan: your employer's terms apply. "), r.text())

    def test_no_flag_gives_the_yes(self):
        for extra in (['--yes'], ['-y'], ['--allow'], ['--consent', 'yes']):
            with self.subTest(extra=extra):
                r = self.rig()
                self.assertEqual(r('continue', *extra), 2)
                self.assertEqual(r.text(), words.HELP['continue'] + '\n')
                self.assertEqual((r.handoff.notes, r.calls.execs), ([], []))

    def test_an_exec_that_cannot_start(self):
        def execve(path, argv, env, cwd):
            raise FileNotFoundError(2, 'No such file or directory')

        r = self.allowed(self.rig(tty=True, execve=execve))
        self.assertEqual(r('continue'), 1)
        note = r.handoff.notes[0]
        launch = r.codex.start(r.folder, note, contract.IN_TERMINAL)
        self.assertEqual(r.lines()[-2:], [
            'Codex could not be started from here (No such file or directory). To carry on, run this in a terminal:',
            '  ' + launch.line])
        self.assertEqual(r.tasks.all[0].state, 'failed')
        self.assertEqual(r.said[-1], ('Could not start Codex for a note in site', 'problem',
                                      {'to': 'Codex', 'reason': 'could not start'}))

    def test_the_key_in_this_shell_is_not_used(self):
        r = self.allowed(self.rig(tty=True, env={'CODEX_API_KEY': 'sk-made-up-0000'}))
        self.assertEqual(r('continue'), 0)
        self.assertIn('Codex runs on your OpenAI sign-in here, not on the API key set in this shell.', r.lines())
        path, argv, env, cwd = r.calls.execs[0]
        self.assertNotIn('CODEX_API_KEY', env)
        self.assertNotIn('sk-made-up-0000', ' '.join(argv))

    def test_a_key_for_the_other_vendor_says_nothing(self):
        r = self.allowed(self.rig(tty=True, env={'ANTHROPIC_API_KEY': 'sk-ant-made-up'}))
        r('continue')
        self.assertNotIn('not on the API key', r.text())
        self.assertNotIn('ANTHROPIC_API_KEY', r.calls.execs[0][2])

    def test_show_then_yes(self):
        r = self.allowed(self.rig(tty=True, answers=['y']))
        self.assertEqual(r('continue', '--show'), 0)
        note = r.handoff.notes[0]
        self.assertIn('The note: %s\n# Switchboard note %s' % (note.path, note.hop), r.text())
        self.assertEqual(r.asked, ['Carry on in Codex with this note? [y/N] '])
        self.assertEqual(len(r.calls.execs), 1)

    def test_show_then_no_starts_nothing(self):
        r = self.allowed(self.rig(tty=True, answers=['']))
        self.assertEqual(r('continue', '--show'), 0)
        self.assertEqual(r.calls.execs, [])
        self.assertEqual(r.tasks.all[0].state, 'failed')            # nothing started: the move is closed

    def test_a_note_not_used_does_not_block_the_next_continue(self):
        r = self.allowed(self.rig(tty=True, answers=['', 'y']))
        self.assertEqual(r('continue', '--show'), 0)
        self.assertEqual(r('continue', '--show'), 0)
        self.assertNotIn('already carried on', r.text())
        self.assertEqual(len(r.calls.execs), 1)

    def test_unnamed_chats_are_shown_short(self):
        r = self.allowed(self.rig(chats=(a_chat(name=None),), tty=True))
        self.assertEqual(r('continue'), 0)
        self.assertIn('Making the note from `Claude Code chat aaaaaaaa` ...', r.lines())
        self.assertIn('from Claude Code chat aaaaaaaa).', r.text())
        self.assertNotIn(CHAT_A, r.text())
        r = self.allowed(self.rig(chats=(a_chat(name=None), a_chat(cid=CHAT_B, name=None, ago=60.0))))
        self.assertEqual(r('continue'), 1)
        self.assertEqual(r.lines()[1:], ['  1. Claude Code chat bbbbbbbb  (Claude Code, not open, 1 min ago)',
                                         '  2. Claude Code chat aaaaaaaa  (Claude Code, not open, 10 min ago)'])

    def test_ctrl_c_at_the_question(self):
        def stop(prompt):
            raise KeyboardInterrupt

        r = self.rig(tty=True, answers=[stop])
        self.assertEqual(r('continue'), 130)
        self.assertEqual(r.calls.execs, [])

    def test_from_a_codex_chat_to_claude(self):
        r = self.rig(chats=(), codex_chats=(lambda f: chat('codex', f, cid=CHAT_B, name='Ship it'),), tty=True)
        r.choices.set_consent(r.folder, 'claude', 'yes', NOW)
        self.assertEqual(r('continue'), 0)
        note = r.handoff.notes[0]
        prog = r.claude.found_.program
        self.assertEqual(r.calls.execs[0][:2], (prog.path, (prog.path, words.FIRST_WORDS.format(
            hop=note.hop, path=note.path, from_name=OTHER))))
        self.assertIn('Carrying on in Claude Code 2.1.330 (your PATH), in this folder.', r.lines())

    def test_claude_has_no_app_link(self):
        r = self.rig(chats=(), codex_chats=(lambda f: chat('codex', f, cid=CHAT_B),), tty=True)
        r.choices.set_consent(r.folder, 'claude', 'yes', NOW)
        self.assertEqual(r('continue', '--in', 'app'), 1)
        self.assertEqual(r.lines()[-1], words.NO_CLAUDE_LINK)
        self.assertEqual(r.calls.links, [])
        self.assertEqual(r.tasks.all[0].state, 'failed')


class NotAtATerminal(Base, unittest.TestCase):
    def test_never_asked_is_refused_before_a_note_is_made(self):
        r = self.rig()
        self.assertEqual(r('continue'), 1)
        self.assertEqual(r.lines()[-1], "Carrying this folder's chat on in Codex sends a note of it to OpenAI. Run "
                                        "switchboard allow codex in a terminal once to say yes for this folder.")
        self.assertEqual((r.asked, r.handoff.notes, r.tasks.all), ([], [], []))

    def test_a_yes_given_before_prints_the_line(self):
        r = self.allowed(self.rig())
        self.assertEqual(r('continue'), 0)
        note = r.handoff.notes[0]
        launch = r.codex.start(r.folder, note, contract.PRINT)
        self.assertEqual(r.lines(), ROOM + [
            'The note is ready: %s (%s; 2 places that looked like a secret taken out, best effort).'
            % (note.path, words.size(note.bytes)),
            'To carry on in Codex, run this in a terminal:',
            '  ' + launch.line])
        self.assertEqual((r.calls.execs, r.asked), ([], []))
        self.assertEqual(r.tasks.all[0].state, 'printed')
        self.assertEqual(r.said, [('Made a note for Codex of a chat from Claude in site (note %s, 2 places that '
                                   'looked like a secret taken out)' % words.size(note.bytes), 'info',
                                   {'frm': 'Claude', 'to': 'Codex', 'reason': 'carry on'})])

    def test_the_printed_line_names_each_key_it_leaves_out(self):
        r = self.allowed(self.rig(env={'OPENAI_API_KEY': 'sk-made-up-1111'}))
        r('continue')
        self.assertIn('env -u OPENAI_API_KEY', r.text())
        self.assertNotIn('sk-made-up-1111', r.text())

    def test_a_line_that_cannot_be_printed_safely_names_the_file(self):
        r = self.allowed(self.rig())
        r.codex.found_ = replace(r.codex.found_, program=replace(r.codex.found_.program,
                                                                  path=r.codex.found_.program.path + '\x07'))
        r('continue')
        note = r.handoff.notes[0]
        self.assertEqual(r.lines()[-1], 'Open Codex in this folder and give it the file %s.' % note.path)

    def test_in_app_is_the_printed_line(self):
        for how in ('--in', 'app'), ():
            with self.subTest(how=how):
                r = self.allowed(self.rig())
                r.codex.app = '/Applications/ChatGPT.app'
                if not how:
                    r.choices.set_open_with('codex', contract.IN_APP)
                self.assertEqual(r('continue', *how), 0)
                note = r.handoff.notes[0]
                launch = r.codex.start(r.folder, note, contract.PRINT)
                self.assertEqual((r.calls.links, r.calls.execs), ([], []))
                self.assertEqual(r.lines()[-2:], ['To carry on in Codex, run this in a terminal:', '  ' + launch.line])
                self.assertEqual(r.tasks.all[0].state, 'printed')

    def test_in_app_opens_one_link_at_a_terminal(self):
        r = self.allowed(self.rig(tty=True))
        r.codex.app = '/Applications/ChatGPT.app'
        self.assertEqual(r('continue', '--in', 'app'), 0)
        note = r.handoff.notes[0]
        launch = r.codex.start(r.folder, note, contract.IN_APP)
        self.assertEqual(r.calls.links, [(launch.url, '/Applications/ChatGPT.app')])
        self.assertTrue(launch.url.startswith('codex://new?prompt=Switchboard%20note%20'))
        self.assertEqual(r.lines()[-1], 'Codex is open on this folder with the note in its message box. Press send '
                                        'to carry on.')
        self.assertEqual(r.tasks.all[0].state, 'in_app')
        self.assertEqual(r.said[0][0][:41], 'Opened Codex on a note of a chat from Cla')


class WhichChat(Base, unittest.TestCase):
    def test_no_chat(self):
        r = self.allowed(self.rig(chats=()))
        self.assertEqual(r('continue'), 1)
        self.assertEqual(r.text(), "No chat of Claude's or Codex's ran in this folder in the last 7 days. Name one "
                                   "with switchboard continue --chat NAME.\nA chat is listed under the folder it was "
                                   "started in: run switchboard continue there.\n")     # D5

    def test_a_chat_of_another_folder_is_not_one(self):
        r = self.allowed(self.rig(chats=(lambda f: chat('claude', f + '-other'),)))
        self.assertEqual(r('continue'), 1)
        self.assertTrue(r.text().startswith("No chat of Claude's"))

    def test_several_not_at_a_terminal(self):
        r = self.allowed(self.rig(chats=(a_chat(ago=600.0), a_chat(cid=CHAT_B, name='Write the docs', ago=60.0))))
        self.assertEqual(r('continue'), 1)
        self.assertEqual(r.lines(), [
            '2 chats ran in this folder. Name one with --chat, or use --latest:',
            '  1. Write the docs  (Claude Code, not open, 1 min ago)',
            '  2. Fix the login page  (Claude Code, not open, 10 min ago)'])
        self.assertEqual((r.handoff.notes, r.asked), ([], []))

    def test_several_at_a_terminal_by_number(self):
        r = self.allowed(self.rig(chats=(a_chat(ago=600.0), a_chat(cid=CHAT_B, name='Write the docs', ago=60.0)),
                                  tty=True, answers=['2']))
        self.assertEqual(r('continue'), 0)
        self.assertEqual(r.asked, ['Which chat? (a number) '])
        self.assertEqual(r.tasks.all[0].from_chat, CHAT_A)
        for bad in ('0', '3', 'x', '', '\u00b2', '\u0663'):
            with self.subTest(bad=bad):
                r = self.allowed(self.rig(chats=(a_chat(), a_chat(cid=CHAT_B)), tty=True, answers=[bad]))
                self.assertEqual(r('continue'), 1)
                self.assertEqual(r.lines()[-1], 'That is not a number from the list. Nothing was sent.')
                self.assertEqual(r.handoff.notes, [])

    def test_thirty_one_shown_as_thirty_and_one_more_stopped_first(self):
        chats = [a_chat(cid='%08x-0000-4000-8000-%012x' % (i, i), name='Chat %02d' % i, ago=100.0 + i)
                 for i in range(1, 31)]
        chats.append(a_chat(cid='ffffffff-0000-4000-8000-00000000001f', name='Stopped one', ago=5000.0,
                            stopped='rate_limit', live=True))
        r = self.allowed(self.rig(chats=chats))
        self.assertEqual(r('continue'), 1)
        lines = r.lines()
        self.assertEqual(lines[0], '31 chats ran in this folder. Name one with --chat, or use --latest:')
        self.assertEqual(lines[1], '  1. Stopped one  (Claude Code, stopped at the limit · open in a window, '
                                   '1 hour ago)')
        self.assertEqual(lines[2], '  2. Chat 01  (Claude Code, not open, 1 min ago)')
        self.assertEqual(len(lines), 1 + 30 + 1)
        self.assertEqual(lines[-1], '  and 1 more; name one with --chat')
        self.assertNotIn('Chat 30', r.text())

    def test_the_chat_list_shows_names_through_safe(self):
        r = self.allowed(self.rig(chats=(a_chat(name='evil\x1b]0;x\x07\x1b[2J name\x0d\x0aline'),
                                         a_chat(cid=CHAT_B))))
        r('continue')
        self.assertNotIn('\x1b', r.text())
        self.assertNotIn('\r', r.text())
        self.assertIn('  1. evil name line  (Claude Code', r.text())

    def test_latest(self):
        r = self.allowed(self.rig(chats=(a_chat(ago=600.0), a_chat(cid=CHAT_B, name='Write the docs', ago=60.0))))
        self.assertEqual(r('continue', '--latest'), 0)
        self.assertEqual(r.lines()[0], 'Taking the newest chat: Write the docs (Claude Code, 1 min ago).')
        self.assertEqual(r.tasks.all[0].from_chat, CHAT_B)

    def test_the_chat_this_shell_runs_in(self):
        r = self.allowed(self.rig(chats=(a_chat(ago=60.0), a_chat(cid=CHAT_B, name='Write the docs', ago=600.0)),
                                  env={'CLAUDE_CODE_SESSION_ID': CHAT_B}))
        self.assertEqual(r('continue'), 0)
        self.assertEqual(r.lines()[0], 'Taking the chat this shell runs in: Write the docs (Claude Code).')
        self.assertEqual(r.tasks.all[0].from_chat, CHAT_B)
        self.assertEqual(r.asked, [])

    def test_chat_by_name_by_id_and_ambiguous(self):
        chats = (a_chat(name='Fix the login page'), a_chat(cid=CHAT_B, name='Fix the footer'))
        for word, picked in (('fix the login page', CHAT_A), ('Fix the l', CHAT_A), ('bbbbbbbb', CHAT_B),
                             ('BBBB', CHAT_B)):
            with self.subTest(word=word):
                r = self.allowed(self.rig(chats=chats))
                self.assertEqual(r('continue', '--chat', word), 0)
                self.assertEqual(r.tasks.all[0].from_chat, picked)
        r = self.allowed(self.rig(chats=chats))
        self.assertEqual(r('continue', '--chat', 'fix'), 1)
        self.assertEqual(r.lines(), ['More than one chat matches "fix":',
                                     '  1. Fix the login page  (Claude Code, not open, 10 min ago)',
                                     '  2. Fix the footer  (Claude Code, not open, 10 min ago)'])
        r = self.allowed(self.rig(chats=chats))
        self.assertEqual(r('continue', '--chat', 'deploy'), 1)
        self.assertEqual(r.text(), 'No chat here is called "deploy" or has an id that starts with it.\n')

    def test_a_headless_run_only_by_name(self):
        chats = (a_chat(entrypoint='sdk-cli', name='Night job'),)
        r = self.allowed(self.rig(chats=chats))
        self.assertEqual(r('continue'), 1)
        self.assertTrue(r.text().startswith("No chat of Claude's"))
        r = self.allowed(self.rig(chats=chats))
        self.assertEqual(r('continue', '--chat', 'night job'), 0)

    def test_older_chats_only_when_nothing_is_recent(self):
        r = self.allowed(self.rig(chats=(a_chat(ago=3 * 86400.0),)))
        self.assertEqual(r('continue'), 0)
        r = self.allowed(self.rig(chats=(a_chat(ago=3 * 86400.0), a_chat(cid=CHAT_B, ago=60.0))))
        self.assertEqual(r('continue'), 0)                # the recent one alone is offered, so it is taken
        self.assertEqual(r.tasks.all[0].from_chat, CHAT_B)

    def test_wrong_words(self):
        for args in (['--chat', 'x', '--latest'], ['--in', 'window'], ['--to'], ['--what']):
            with self.subTest(args=args):
                r = self.allowed(self.rig())
                self.assertEqual(r('continue', *args), 2)
                self.assertTrue(r.text().endswith(words.HELP['continue'] + '\n'))
        r = self.allowed(self.rig())
        self.assertEqual(r('continue', '--to', 'gemini'), 2)
        self.assertTrue(r.text().startswith('Switchboard does not know an assistant called "gemini".'))


class Refusals(Base, unittest.TestCase):
    def test_no_other_provider_added(self):
        r = self.rig(codex_added=False)
        self.assertEqual(r('continue'), 1)
        self.assertEqual(r.text(), 'No other provider is added. switchboard add codex adds Codex.\n')

    def test_a_mac_with_only_claude(self):
        for tty in (False, True):
            with self.subTest(tty=tty):
                r = self.rig(codex_added=False, codex_found_=Found('codex', installed=False), tty=tty)
                self.assertEqual(r('continue'), 1)
                self.assertEqual(r.text(), "No other provider is on this Mac yet. Install OpenAI's Codex (its app or "
                                           "its command line), then run switchboard add codex.\n")
                self.assertEqual((r.asked, r.handoff.notes), ([], []))
        r = self.rig(codex=False)                       # this build has no Codex adapter at all
        self.assertEqual(r('continue'), 1)
        self.assertEqual(r.text(), 'No other provider is on this Mac yet.\n')

    def test_the_offer_to_add_at_a_terminal(self):
        r = self.rig(codex_added=False, tty=True, answers=['', 'y'])
        self.assertEqual(r('continue'), 0)
        self.assertEqual(r.asked[0], 'Codex 0.169.1 is on this Mac but not in your order. Add it and go on? [Y/n] ')
        self.assertIn('Added Codex. Your order: Claude, then Codex.', r.lines())
        self.assertEqual(len(r.calls.execs), 1)
        r = self.rig(codex_added=False, tty=True, answers=['n'])
        self.assertEqual(r('continue'), 1)
        self.assertEqual(r.lines()[-1], 'No other provider is added. switchboard add codex adds Codex.')
        self.assertNotIn('codex', r.choices.added())

    def test_to_not_added(self):
        r = self.rig(codex_added=False)
        self.assertEqual(r('continue', '--to', 'codex'), 1)
        self.assertEqual(r.text(), 'Codex is not added. switchboard add codex adds it.\n')

    def test_the_target_is_the_source(self):
        r = self.allowed(self.rig())
        self.assertEqual(r('continue', '--to', 'claude'), 1)
        self.assertEqual(r.text(), 'This chat is already in Claude.\n')

    def test_a_stay_and_a_never(self):
        r = self.rig()
        r.choices.set_stay(r.folder, 'claude', NOW)
        self.assertEqual(r('continue'), 1)
        self.assertEqual(r.lines()[-1], 'This folder stays on Claude. switchboard stay off lifts that.')
        r = self.rig(tty=True)
        r.choices.set_consent(r.folder, 'codex', 'never', NOW)
        self.assertEqual(r('continue'), 1)
        self.assertEqual(r.lines()[-1], "You said this folder's chats never go to Codex. switchboard allow codex "
                                        "lifts that.")
        self.assertEqual((r.asked, r.handoff.notes), ([], []))

    def test_everyone_out(self):
        r = self.allowed(self.rig())
        r.codex.limits_ = codex_limits(pct=99.0)
        r.claude.limits_ = claude_limits(under=0)
        self.assertEqual(r('continue'), 1)
        self.assertEqual(r.text(), 'Every provider you added is out: Codex until %s. Nothing was sent.\n'
                         % words.when(NOW + 2 * 86400, NOW))

    def test_the_target_unknown_is_not_a_refusal(self):
        r = self.allowed(self.rig())
        r.codex.limits_ = ()
        self.assertEqual(r('continue'), 0)
        self.assertEqual(r.lines()[1:4], ['Codex: numbers not known (ChatGPT Pro).',
                                          'switchboard providers --ask reads them now.',
                                          "Codex's numbers are not known; it is next in your order."])

    def test_the_room_line_says_its_age(self):
        r = self.allowed(self.rig())
        r.codex.limits_ = codex_limits(read_ago=2 * 3600.0)
        self.assertEqual(r('continue'), 0)
        self.assertEqual(r.lines()[1:3], [
            'Codex: had room when last read 2 hours ago (ChatGPT Pro, weekly 42%, read 2 hours ago).',
            'switchboard providers --ask reads them now.'])

    def test_too_old(self):
        r = self.allowed(self.rig(codex_found_=codex_found(self.root, version='0.164.0', seen='0.169.1')))
        self.assertEqual(r('continue'), 1)
        self.assertEqual(r.lines()[-1], 'The newest Codex here (0.164.0) is older than the one that wrote your Codex '
                                        'chats (0.169.1). Update Codex, then run this again.')
        self.assertEqual(r.handoff.notes, [])

    def test_a_full_disk(self):
        r = self.allowed(self.rig())
        r.handoff.fail = 'disk'
        self.assertEqual(r('continue'), 1)
        self.assertEqual(r.lines()[-1], 'The note could not be written: the disk is full, or its folder cannot be '
                                        'written to.')

    def test_already_carried_and_again(self):
        for state in ('started', 'in_app', 'printed'):
            with self.subTest(state=state):
                r = self.allowed(self.rig())
                r.tasks.add_hop(Hop(id='h-00000001', task='t-00000001', kind='continue', at=NOW - 300,
                                    folder=r.folder, from_provider='claude', from_chat=CHAT_A, to_provider='codex',
                                    state=state))
                self.assertEqual(r('continue'), 1)
                self.assertTrue(r.lines()[-1].startswith('This chat was already carried on to Codex'))
        r = self.allowed(self.rig())
        r.tasks.add_hop(Hop(id='h-00000001', task='t-00000001', kind='continue', at=NOW - 300, folder=r.folder,
                            from_provider='claude', from_chat=CHAT_A, to_provider='codex', state='started'))
        self.assertEqual(r('continue'), 1)
        self.assertEqual(r.lines()[-1], 'This chat was already carried on to Codex at %s. Open it in Codex, or run '
                                        'switchboard back to return. To make a new note anyway: switchboard continue '
                                        '--again.' % words.when(NOW - 300, NOW))
        self.assertEqual(r('continue', '--again'), 0)
        self.assertEqual(len(r.handoff.notes), 1)

    def test_a_note_made_and_not_used_is_not_already_carried(self):
        r = self.allowed(self.rig())
        r.tasks.add_hop(Hop(id='h-00000001', task='t-00000001', kind='continue', at=NOW - 300, folder=r.folder,
                            from_provider='claude', from_chat=CHAT_A, to_provider='codex', state='made'))
        self.assertEqual(r('continue'), 0)
        self.assertEqual(len(r.handoff.notes), 1)

    def test_a_chat_written_since_is_not_already_carried(self):
        r = self.allowed(self.rig(chats=(a_chat(ago=10.0),)))
        r.tasks.add_hop(Hop(id='h-00000001', task='t-00000001', kind='continue', at=NOW - 300, folder=r.folder,
                            from_provider='claude', from_chat=CHAT_A, to_provider='codex', state='started'))
        self.assertEqual(r('continue'), 0)
        made = r.handoff.made[0]
        self.assertEqual(made['task'], 't-00000001')

    def test_a_working_chat_and_a_waiting_window(self):
        r = self.allowed(self.rig(chats=(a_chat(working=True),)))
        r('continue')
        self.assertIn('That chat is still working: its newest part may not be in the note.', r.lines())
        r = self.allowed(self.rig(chats=(a_chat(live=True, stopped='rate_limit'),)))
        r('continue')
        self.assertIn('That Claude window may carry on by itself when its limit resets. Press Esc in it to cancel the '
                      'wait, or close it.', r.lines())


if __name__ == '__main__':
    unittest.main()
