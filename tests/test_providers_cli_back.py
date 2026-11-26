import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# switchboard back [--chat NAME] [--show] (DESIGN 12.4, 13, 16), every row word for word with its exit code, on a Kit
# of stand-ins.
#
# - Which move: the newest open continue in this folder, or the one that led to the chat named with --chat. None:
#   "Nothing in this folder was carried on elsewhere." The chat it led to: the ledger's, else the adapter's own finder,
#   else the newest chat of that provider here begun after the move whose first message is that note's mark. None:
#   "No Codex chat in this folder began from the note of ...".
# - It asks no yes (the person typed back) but honours a stay and a never for the provider the note goes to.
# - Delivery, by where the chat that was left is: open in a window with the session hooks able to deliver (the note's
#   pointer goes to the bridge: fixed words, times, the hop, the path, no chat words); open but they cannot (the paste
#   line, and the hooks-not-on line when the bridge says they are off); not open at a terminal (Open ... again? [y/N]:
#   y resumes it with the note in one recorded exec, n is the paste line); not open, not at a terminal (the line to
#   run); a Codex chat the same way; a chat whose file is gone (a new chat of that provider on the note).
# - --show prints the note's path and the paste line and starts nothing. The continue becomes returned, with one
#   History line.
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))

from fakes_provider_kit import (CHAT_A, CHAT_B, CHAT_C, NOW, OTHER, FakeBridge, KitCase, chat,  # noqa: E402
                                claude_found)
from switchboard.providers import contract, handoff as real_handoff, run as provider_run, words  # noqa: E402
from switchboard.providers.contract import Event, Hop, ReadInfo, Transcript  # noqa: E402

HOP = 'h-0000000a'
LEFT_AT = NOW - 3600
WHEN = words.when(LEFT_AT, NOW)


def left(**kw):
    return lambda folder: chat('claude', folder, cid=CHAT_A, name='Fix the login page', ago=4000.0, **kw)


def away(cid=CHAT_B, name='Ship it', started_ago=3500.0):
    return lambda folder: chat('codex', folder, cid=cid, name=name, ago=60.0, started_ago=started_ago)


def began_on(folder, hop, cid=CHAT_B):
    """A Codex chat whose first message is the note's (a reader turns it into a NOTICE with the mark)."""
    return Transcript(chat('codex', folder, cid=cid), (
        Event(contract.NOTICE, words.NOTE_MARK_TEXT.format(hop=hop), origin='switchboard-note'),
        Event(contract.ASSISTANT, 'Changed the footer and ran the tests.')), ReadInfo(reader='fake'))


class Base(KitCase):
    def rig(self, claude_chats=(left(),), codex_chats=(away(),), to_chat=None, hop_state='started', **kw):
        r = super().rig(claude_chats=claude_chats, codex_chats=codex_chats, **kw)
        r.tasks.add_hop(Hop(id=HOP, task='t-0000000a', kind='continue', at=LEFT_AT, folder=r.folder,
                            from_provider='claude', from_chat=CHAT_A, to_provider='codex', to_chat=to_chat,
                            state=hop_state))
        r.codex.transcripts[CHAT_B] = began_on(r.folder, HOP)
        return r

    def note(self, r):
        return r.handoff.notes[-1]

    def paste(self, r):
        n = self.note(r)
        return words.BACK_WORDS.format(hop=n.hop, path=n.path, other_name='Codex')

    def returned(self, r):
        self.assertEqual(r.tasks.get(HOP).state, 'returned')
        n = self.note(r)
        self.assertEqual(r.said[-1], ('Came back to Claude from Codex in site (note %s)' % words.size(n.bytes),
                                      'info', {'frm': 'Codex', 'to': 'Claude', 'reason': 'come back'}))


class WhichMove(Base, unittest.TestCase):
    def test_nothing_carried_on(self):
        r = KitCase.rig(self, claude_chats=(left(),), codex_chats=(away(),))
        self.assertEqual(r('back'), 1)
        self.assertEqual(r.text(), 'Nothing in this folder was carried on elsewhere. switchboard continue does that.\n')

    def test_a_returned_or_failed_move_is_not_open(self):
        for state in ('returned', 'failed'):
            with self.subTest(state=state):
                r = self.rig(hop_state=state)
                self.assertEqual(r('back'), 1)
                self.assertTrue(r.text().startswith('Nothing in this folder was carried on elsewhere.'))

    def test_no_chat_began_from_the_note(self):
        r = self.rig(codex_chats=(away(started_ago=7200.0),))         # begun before the move: not the one
        self.assertEqual(r('back'), 1)
        self.assertEqual(r.text(), 'No Codex chat in this folder began from the note of %s. If you carried on in '
                                   'another folder, run switchboard back there.\n' % WHEN)
        r = self.rig()
        r.codex.transcripts[CHAT_B] = began_on(r.folder, 'h-0000000f')   # another note's chat
        self.assertEqual(r('back'), 1)
        self.assertTrue(r.text().startswith('No Codex chat in this folder began from the note of'))

    def test_the_chat_found_by_its_first_message_goes_into_the_ledger(self):
        r = self.rig()
        self.assertEqual(r('back'), 0)
        self.assertEqual(r.tasks.get(HOP).to_chat, CHAT_B)
        made = r.handoff.made[-1]
        self.assertEqual(made['transcript'].chat.id, CHAT_B)
        self.assertEqual(made['why'], 'The person came back to Claude.')
        self.assertEqual((made['to_name'], made['from_name'], made['budget']), ('Claude', 'Codex', 48000))

    def test_the_adapters_own_finder_comes_first(self):
        r = self.rig()

        class Files:
            asked = []

            @staticmethod
            def started_from(reach, folder, hop_id, after):
                Files.asked.append((folder, hop_id, after))
                return chat('codex', folder, cid=CHAT_C, name='Found by the reader')

        r.codex.chatfiles = Files
        r.codex.chats_.append(chat('codex', r.folder, cid=CHAT_C, name='Found by the reader'))
        self.assertEqual(r('back'), 0)
        self.assertEqual(Files.asked, [(r.folder, HOP, LEFT_AT)])
        self.assertEqual(r.tasks.get(HOP).to_chat, CHAT_C)
        self.assertNotIn('read %s' % CHAT_B, r.codex.calls)

    def test_chat_names_one_of_several_moves(self):
        r = self.rig(to_chat=CHAT_B, codex_chats=(away(), away(cid=CHAT_C, name='Second try')))
        r.tasks.add_hop(Hop(id='h-0000000b', task='t-0000000b', kind='continue', at=NOW - 1800, folder=r.folder,
                            from_provider='claude', from_chat=CHAT_A, to_provider='codex', to_chat=CHAT_C,
                            state='started'))
        self.assertEqual(r('back', '--chat', 'ship it'), 0)
        self.assertEqual(r.tasks.get(HOP).state, 'returned')
        self.assertEqual(r.tasks.get('h-0000000b').state, 'started')
        r2 = self.rig(to_chat=CHAT_B)
        self.assertEqual(r2('back', '--chat', 'nothing like it'), 1)
        self.assertTrue(r2.text().startswith('Nothing in this folder was carried on elsewhere.'))

    def test_chat_finds_a_move_just_made(self):
        # continue records no to_chat: each open move's chat is found by its note's mark, then matched by name
        for word, picked, other in (('second try', 'h-0000000b', HOP), ('ship it', HOP, 'h-0000000b')):
            with self.subTest(word=word):
                r = self.rig(codex_chats=(away(), away(cid=CHAT_C, name='Second try', started_ago=1700.0)))
                r.tasks.add_hop(Hop(id='h-0000000b', task='t-0000000b', kind='continue', at=NOW - 1800,
                                    folder=r.folder, from_provider='claude', from_chat=CHAT_A, to_provider='codex',
                                    state='started'))
                r.codex.transcripts[CHAT_C] = began_on(r.folder, 'h-0000000b', cid=CHAT_C)
                self.assertEqual(r('back', '--chat', word), 0)
                self.assertEqual(r.tasks.get(picked).state, 'returned')
                self.assertEqual(r.tasks.get(other).state, 'started')
                self.assertEqual(r.tasks.get(picked).to_chat, CHAT_C if picked != HOP else CHAT_B)
        r = self.rig()
        self.assertEqual(r('back', '--chat', 'nothing like it'), 1)
        self.assertTrue(r.text().startswith('Nothing in this folder was carried on elsewhere.'))

    def test_chat_finds_a_move_in_the_work_folder(self):
        w = os.path.join(self.root, 'site', 'api')
        os.makedirs(w)
        r = self.rig(claude_chats=(left(work_folder=w),), codex_chats=())
        r.codex.chats_ = [chat('codex', w, cid=CHAT_B, name='Ship it', ago=60.0, started_ago=3500.0)]
        self.assertEqual(r('back', '--chat', 'ship'), 0)
        self.assertEqual(r.tasks.get(HOP).state, 'returned')

    def test_wrong_words(self):
        for args in (['--chat'], ['--now'], ['codex']):
            with self.subTest(args=args):
                r = self.rig()
                self.assertEqual(r('back', *args), 2)
                self.assertEqual(r.text(), words.HELP['back'] + '\n')


class HonoursTheFolder(Base, unittest.TestCase):
    def test_a_stay_on_another_provider(self):
        r = self.rig(tty=True)
        r.choices.set_stay(r.folder, 'codex', NOW)
        self.assertEqual(r('back'), 1)
        self.assertEqual(r.text(), 'This folder stays on Codex. switchboard stay off lifts that.\n')
        self.assertEqual((r.asked, r.handoff.notes), ([], []))

    def test_a_never(self):
        r = self.rig(tty=True)
        r.choices.set_consent(r.folder, 'claude', 'never', NOW)
        self.assertEqual(r('back'), 1)
        self.assertEqual(r.text(), "You said this folder's chats never go to Claude. switchboard allow claude lifts "
                                   "that.\n")
        self.assertEqual(r.tasks.get(HOP).state, 'started')

    def test_no_yes_is_asked(self):
        r = self.rig()
        self.assertEqual(r('back'), 0)
        self.assertEqual(r.asked, [])


class Delivery(Base, unittest.TestCase):
    def test_open_with_the_hooks_on(self):
        r = self.rig(claude_chats=(left(live=True),))
        r.claude.bridge = FakeBridge(can=True)
        self.assertEqual(r('back'), 0)
        n = self.note(r)
        self.assertEqual(r.lines(), [
            'Back to Claude. A note of what Codex did: %s (%s; 2 places that looked like a secret taken out, best '
            'effort). It goes to the chat Fix the login page with your next message there.'
            % (n.path, words.size(n.bytes))])
        text = real_handoff.back_note(n, other_name='Codex', start=LEFT_AT, end=NOW)     # the times as numbers
        self.assertEqual(r.claude.bridge.notes, [(CHAT_A, text, n.path, n.hop)])
        self.assertNotIn('a time not known', text)
        self.assertNotIn('footer', text)                       # the other assistant's words stay in the note only
        self.assertEqual((r.calls.execs, r.asked), ([], []))
        back = r.tasks.get(n.hop)
        self.assertEqual((back.kind, back.from_provider, back.from_chat, back.to_provider, back.to_chat),
                         ('back', 'codex', CHAT_B, 'claude', CHAT_A))
        self.returned(r)

    def test_open_without_the_hooks(self):
        for can, hooks_line in ((False, True), (None, False)):
            with self.subTest(can=can):
                r = self.rig(claude_chats=(left(live=True),), tty=True)
                r.claude.bridge = FakeBridge(can=can)
                self.assertEqual(r('back'), 0)
                n = self.note(r)
                want = ['Back to Claude. A note of what Codex did: %s (%s).' % (n.path, words.size(n.bytes)),
                        'Paste this into the chat Fix the login page:',
                        '  ' + self.paste(r)]
                if hooks_line:
                    want.append("Switchboard's session hooks would deliver this with your next message; they are "
                                "not turned on here (switchboard hooks).")
                self.assertEqual(r.lines()[-len(want):], want)
                self.assertEqual((r.claude.bridge.notes, r.calls.execs, r.asked), ([], [], []))
                self.returned(r)

    def test_open_and_the_bridge_could_not_write(self):
        r = self.rig(claude_chats=(left(live=True),))
        r.claude.bridge = FakeBridge(can=True, put_ok=False)
        self.assertEqual(r('back'), 0)
        self.assertIn('Paste this into the chat Fix the login page:', r.lines())

    def test_not_open_at_a_terminal_yes_resumes_it(self):
        seen = {}

        def execve(path, argv, env, cwd):
            seen['exec'] = (path, tuple(argv), dict(env), cwd)
            seen['state'] = r.tasks.get(HOP).state

        r = self.rig(tty=True, answers=['y'], execve=execve)
        self.assertEqual(r('back'), 0)
        n = self.note(r)
        self.assertEqual(r.asked, ['Open Fix the login page again in this terminal now? [y/N] '])
        self.assertEqual(r.lines()[-1], 'Opening Fix the login page again in Claude Code 2.1.330, with the note.')
        prog = r.claude.found_.program
        back = words.BACK_WORDS.format(hop=n.hop, path=n.path, other_name=OTHER)
        self.assertEqual(seen['exec'], (prog.path, (prog.path, '--resume', CHAT_A, back),
                                        provider_run.child_env(r.reach.env), r.folder))
        self.assertEqual(seen['state'], 'returned')             # closed, and said, before the exec
        self.returned(r)

    def test_not_open_at_a_terminal_no_is_the_paste_line(self):
        r = self.rig(tty=True, answers=['n'])
        self.assertEqual(r('back'), 0)
        self.assertEqual(r.lines()[-2:], ['Paste this into the chat Fix the login page:', '  ' + self.paste(r)])
        self.assertEqual(r.calls.execs, [])
        self.returned(r)

    def test_not_open_not_at_a_terminal(self):
        r = self.rig()
        self.assertEqual(r('back'), 0)
        n = self.note(r)
        launch = r.claude.resume(chat('claude', r.folder, cid=CHAT_A), n, contract.PRINT)
        self.assertEqual(r.lines(), [
            'Back to Claude. A note of what Codex did: %s (%s).' % (n.path, words.size(n.bytes)),
            'To open it again with the note, run this in a terminal:',
            '  ' + launch.line])
        self.assertEqual((r.calls.execs, r.asked), ([], []))
        self.returned(r)

    def test_a_codex_chat_comes_back_the_same_way(self):
        r = KitCase.rig(self, claude_chats=(lambda f: chat('claude', f, cid=CHAT_B, name='Write the docs', ago=60.0,
                                                     started_ago=3500.0),),
                        codex_chats=(lambda f: chat('codex', f, cid=CHAT_A, name='Ship it', ago=4000.0),),
                        tty=True, answers=['y'])
        r.tasks.add_hop(Hop(id=HOP, task='t-0000000a', kind='continue', at=LEFT_AT, folder=r.folder,
                            from_provider='codex', from_chat=CHAT_A, to_provider='claude', to_chat=CHAT_B,
                            state='started'))
        self.assertEqual(r('back'), 0)
        n = self.note(r)
        prog = r.codex.found_.program
        back = words.BACK_WORDS.format(hop=n.hop, path=n.path, other_name=OTHER)
        self.assertEqual(r.calls.execs[0][1], (prog.path, 'resume', '-C', r.folder, CHAT_A, back))
        self.assertEqual(r.lines()[-1], 'Opening Ship it again in Codex 0.169.1, with the note.')

    def test_the_chat_that_was_left_is_gone(self):
        r = self.rig(claude_chats=(), tty=True)
        self.assertEqual(r('back'), 0)
        n = self.note(r)
        self.assertIn('The chat you left is not on this Mac any more; starting a new Claude chat on the note.',
                      r.lines())
        prog = r.claude.found_.program
        first = words.FIRST_WORDS.format(hop=n.hop, path=n.path, from_name=OTHER)
        self.assertEqual(r.calls.execs[0][1], (prog.path, first))
        self.returned(r)

    def test_show_starts_nothing(self):
        r = self.rig(tty=True)
        self.assertEqual(r('back', '--show'), 0)
        n = self.note(r)
        self.assertEqual(r.lines()[-3:], ['The note: %s' % n.path, 'Paste this into the chat Fix the login page:',
                                          '  ' + self.paste(r)])
        self.assertEqual((r.calls.execs, r.asked, r.claude.calls.count('detect')), ([], [], 0))
        self.assertEqual(r.tasks.get(HOP).state, 'started')

    def test_the_came_back_line_is_written_just_before_the_exec(self):
        seen = {}

        def execve(path, argv, env, cwd):
            seen['said'] = [s[0] for s in r.said]
            seen['states'] = (r.tasks.get(HOP).state, r.tasks.all[-1].state)

        r = self.rig(tty=True, answers=['y'], execve=execve)
        self.assertEqual(r('back'), 0)
        self.assertTrue(seen['said'][-1].startswith('Came back to Claude from Codex in site'), seen['said'])
        self.assertEqual(seen['states'], ('returned', 'started'))

    def test_a_start_that_is_refused_writes_no_came_back_line(self):
        for refuse in (lambda *a: contract.Unsupported('claude', contract.RESUME, 'Not here.'),
                       lambda *a: (_ for _ in ()).throw(contract.ProviderError('refused', 'Not here.'))):
            with self.subTest(refuse=refuse):
                r = self.rig(tty=True, answers=['y'])
                r.claude.resume = refuse
                self.assertEqual(r('back'), 1)
                self.assertEqual(r.lines()[-1], 'Not here.')
                self.assertFalse(any(s[0].startswith('Came back') for s in r.said), r.said)
                self.assertEqual(r.tasks.get(HOP).state, 'started')
                self.assertEqual(r.tasks.all[-1].state, 'failed')

    def test_an_unnamed_chat_is_shown_short(self):
        r = self.rig(codex_chats=(away(name=None),), tty=True)
        self.assertEqual(r('back', '--show'), 0)
        self.assertIn('Making the note from `Codex chat bbbbbbbb` ...', r.lines())
        self.assertNotIn(CHAT_B, r.text())

    def test_a_resume_that_cannot_start(self):
        def execve(path, argv, env, cwd):
            raise PermissionError(13, 'Permission denied')

        r = self.rig(tty=True, answers=['y'], execve=execve)
        self.assertEqual(r('back'), 1)
        self.assertTrue(r.lines()[-2].startswith('Claude could not be started from here (Permission denied). To '
                                                 'carry on, run this in a terminal:'), r.lines())
        n = self.note(r)
        self.assertEqual(r.tasks.get(n.hop).state, 'failed')
        self.assertEqual(r.said[-1], ('Could not start Claude for a note in site', 'problem',
                                      {'to': 'Claude', 'reason': 'could not start'}))
        self.assertEqual(r.tasks.get(HOP).state, 'started')

    def test_a_resume_refused_while_the_chat_is_open(self):
        r = self.rig(tty=True, answers=['y'])
        r.claude.busy = True
        self.assertEqual(r('back'), 1)
        self.assertEqual(r.lines()[-1], 'That chat is open in a window; close it first.')
        self.assertEqual(r.calls.execs, [])

    def test_too_old_a_claude_program_never_resumes(self):
        r = self.rig(tty=True, answers=['y'])
        r.claude.found_ = claude_found(self.root, version='2.1.300')

        def refuse(chat_ref, handoff, how):
            raise contract.ProviderError('too_old', words.TOO_OLD.format(name='Claude Code', version='2.1.300',
                                                                         seen='2.1.330'))
        r.claude.resume = refuse
        self.assertEqual(r('back'), 1)
        self.assertEqual(r.lines()[-1], 'The newest Claude Code here (2.1.300) is older than the one that wrote your '
                                        'Claude Code chats (2.1.330). Update Claude Code, then run this again.')


if __name__ == '__main__':
    unittest.main()
