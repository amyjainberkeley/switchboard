import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# Where a chat's work is (ADDENDA-4 D4, D5): a chat is listed under the folder it was started in (ChatRef.folder), and
# its newest lines often ran in another one (ChatRef.work_folder). On a Kit of stand-ins.
#
# - continue: where things stand is read in the work folder and the next assistant starts there (terminal, print,
#   app); the note's stand says where the chat was started (stand['started_in']); one plain line says so before
#   anything starts, both folders through words.safe; the yes is kept for the chat's own folder. Without a work
#   folder, or with one that is no longer a folder, nothing of this changes.
# - back: opening the chat that was left again keeps its own folder; a fresh start (its file is gone) starts where
#   the other side's work is; the chat the move led to is also looked for in the work folder of the chat that was left
#   (the next assistant was started there).
# - No chat here: the sentence also says that a chat is listed under the folder it was started in, and names up to
#   three folders above this one that have a chat of the last day.
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))

from fakes_provider_kit import CHAT_A, CHAT_B, NOW, OTHER, KitCase, chat  # noqa: E402
from switchboard.providers import contract, words  # noqa: E402
from switchboard.providers.contract import Event, Hop, ReadInfo, Transcript  # noqa: E402

HOP = 'h-0000000a'
LEFT_AT = NOW - 3600


def moved_line(started, work, to='Codex'):
    return ('This chat was started in %s and last worked in %s; %s starts in %s.'
            % (words.one_line(started, 200), words.one_line(work, 200), to, words.one_line(work, 200)))


class Base(KitCase):
    def work(self, r, name='api'):
        w = os.path.join(r.folder, name)
        os.makedirs(w, exist_ok=True)
        return w

    def moved_rig(self, work_name='api', **kw):
        """Claude's chat started in the rig's folder, its newest lines in <folder>/api; a yes for Codex given."""
        holder = {}

        def made(folder):
            holder['w'] = os.path.join(folder, work_name)
            os.makedirs(holder['w'], exist_ok=True)
            return chat('claude', folder, work_folder=holder['w'])

        r = self.rig(claude_chats=(made,), **kw)
        r.choices.set_consent(r.folder, 'codex', 'yes', NOW - 3600)
        return r, holder['w']


class Continue(Base, unittest.TestCase):
    def test_the_start_and_the_stand_are_where_the_work_is(self):
        r, w = self.moved_rig(tty=True)
        self.assertEqual(r('continue'), 0)
        note = r.handoff.notes[0]
        prog = r.codex.found_.program
        first = words.FIRST_WORDS.format(hop=note.hop, path=note.path, from_name=OTHER)
        self.assertEqual(len(r.calls.execs), 1)
        path, argv, env, cwd = r.calls.execs[0]
        self.assertEqual((argv, cwd), ((prog.path, '-C', w, first), w))
        self.assertEqual(r.handoff.stood, [w])
        stand = r.handoff.made[0]['stand']
        self.assertEqual((stand['folder'], stand['started_in']), (w, r.folder))
        self.assertIn('Carrying on in Codex 0.169.1 (ChatGPT.app), in %s.' % w, r.lines())

    def test_one_line_says_so_before_anything_starts(self):
        r, w = self.moved_rig(tty=True)
        r('continue')
        lines = r.lines()
        self.assertEqual(lines.count(moved_line(r.folder, w)), 1)
        self.assertLess(lines.index(moved_line(r.folder, w)), lines.index('Making the note from `Fix the login page` ...'))

    def test_the_line_and_the_start_when_printed(self):
        r, w = self.moved_rig()
        self.assertEqual(r('continue'), 0)
        note = r.handoff.notes[0]
        launch = r.codex.start(w, note, contract.PRINT)
        self.assertEqual(r.lines()[2], moved_line(r.folder, w))
        self.assertEqual(r.lines()[-1], '  ' + launch.line)
        self.assertIn('cd %s' % w, launch.line)
        self.assertEqual(r.tasks.all[0].folder, r.folder)          # the move is kept with the chat's own folder

    def test_the_app_opens_on_the_work_folder(self):
        opened = []
        r, w = self.moved_rig(tty=True, open_url=lambda url, app=None: opened.append(url) or contract.Ran(code=0))
        self.assertEqual(r('continue', '--in', 'app'), 0)
        self.assertEqual(len(opened), 1)
        self.assertIn('path=' + w.replace('/', '%2F'), opened[0])
        self.assertIn(moved_line(r.folder, w), r.lines())
        self.assertEqual(r.lines()[-1], 'Codex is open on %s with the note in its message box. Press send to carry '
                                        'on.' % w)

    def test_the_yes_is_kept_for_the_chats_own_folder(self):
        holder = {}

        def made(folder):
            holder['w'] = os.path.join(folder, 'api')
            os.makedirs(holder['w'], exist_ok=True)
            return chat('claude', folder, work_folder=holder['w'])

        r = self.rig(claude_chats=(made,), tty=True, answers=['y'])
        self.assertEqual(r('continue'), 0)
        self.assertEqual(r.choices.consent(r.folder, 'codex'), 'yes')
        self.assertIsNone(r.choices.consent(holder['w'], 'codex'))
        self.assertIn('You are asked once for this folder (site).', r.text())

    def test_the_same_folder_changes_nothing(self):
        r = self.rig(claude_chats=(lambda f: chat('claude', f),), tty=True)
        r.choices.set_consent(r.folder, 'codex', 'yes', NOW - 3600)
        self.assertEqual(r('continue'), 0)
        self.assertEqual(r.calls.execs[0][3], r.folder)
        self.assertEqual(r.handoff.stood, [r.folder])
        self.assertNotIn('started_in', r.handoff.made[0]['stand'])
        self.assertNotIn('last worked in', r.text())
        self.assertIn('Carrying on in Codex 0.169.1 (ChatGPT.app), in this folder.', r.lines())

    def test_a_work_folder_that_is_gone_is_not_used(self):
        r = self.rig(claude_chats=(lambda f: chat('claude', f, work_folder=os.path.join(f, 'gone')),), tty=True)
        r.choices.set_consent(r.folder, 'codex', 'yes', NOW - 3600)
        self.assertEqual(r('continue'), 0)
        self.assertEqual(r.calls.execs[0][3], r.folder)
        self.assertEqual(r.handoff.stood, [r.folder])
        self.assertNotIn('last worked in', r.text())

    def test_both_folders_go_through_safe(self):
        r, w = self.moved_rig(work_name='api\x1b[31mred\x07')
        self.assertEqual(r('continue'), 0)
        self.assertNotIn('\x1b', r.text())
        self.assertNotIn('\x07', r.text())
        self.assertIn('last worked in %s' % words.one_line(w, 200), r.text())
        note = r.handoff.notes[0]                           # a start line with a control character is never printed
        self.assertEqual(r.lines()[-1], 'Open Codex in %s and give it the file %s.' % (words.one_line(w, 200),
                                                                                       note.path))

    def test_the_question_names_the_folder_the_app_will_work_in(self):
        r, w = self.moved_rig(tty=True, answers=['n'])
        r.choices.folders.clear()                           # no yes yet: the question comes
        self.assertEqual(r('continue'), 1)
        self.assertIn('Codex will then work in %s and can read its files.' % w, r.text())
        self.assertNotIn('Codex will then work in this folder', r.text())
        r = self.rig(claude_chats=(lambda f: chat('claude', f),), tty=True, answers=['n'])
        self.assertEqual(r('continue'), 1)
        self.assertIn('Codex will then work in this folder and can read its files.', r.text())


def asked(started, work, to='Codex', starts=True):
    return ('This chat was started in %s and last worked in %s, outside it. Carry on in %s? Switchboard reads git '
            'there%s. [y/N] ' % (started, work, work, ' and %s starts there' % to if starts else ''))


def not_allowed(started, work, to_id='codex'):
    return ('This chat last worked in %s, outside %s, where it was started; %s is used. To carry on in %s: cd there '
            'and run switchboard allow %s in a terminal.' % (work, started, started, work, to_id))


def too_wide(started, work):
    return ('This chat last worked in %s, a folder too wide to carry on in; %s, where it was started, is used.'
            % (work, started))


class Outside(Base, unittest.TestCase):
    """A work folder outside the chat's own folder (FIX-PLAN X1): used only with a yes for that folder, asked at a
    terminal before git runs there; never /, the home folder or a folder that holds it. Git runs only in the folder
    that is used."""

    def out_rig(self, work, own=None, yes_own=True, **kw):
        r = self.rig(claude_chats=(), **kw)
        own = os.path.realpath(own or r.folder)
        for d in (own, work):
            if not os.path.isdir(d):                    # never a mkdir of / (the test guard counts it)
                os.makedirs(d)
        r.folder = own
        r.claude.chats_ = [chat('claude', own, work_folder=work)]
        if yes_own:
            r.choices.set_consent(own, 'codex', 'yes', NOW - 3600)
        return r

    def sibling(self):
        return os.path.join(self.root, 'notes')

    def test_a_sibling_is_asked_and_yes_is_kept(self):
        sib = self.sibling()
        r = self.out_rig(sib, tty=True, answers=['y'])
        self.assertEqual(r('continue'), 0)
        self.assertEqual(r.asked, [asked(r.folder, sib)])
        self.assertEqual(r.handoff.stood, [sib])
        self.assertEqual(r.calls.execs[0][3], sib)
        self.assertEqual(r.choices.consent(sib, 'codex'), 'yes')
        self.assertIn(('Chats in notes may go to Codex', 'setting', {'to': 'Codex'}), r.said)
        self.assertIn(moved_line(r.folder, sib), r.lines())
        self.assertEqual(r('continue', '--again'), 0)          # the next time: no question
        self.assertEqual(len(r.asked), 1)
        self.assertEqual(r.calls.execs[1][3], sib)

    def test_a_sibling_answered_no_uses_the_chats_own_folder(self):
        sib = self.sibling()
        for answer in ('n', '', 'never'):
            with self.subTest(answer=answer):
                r = self.out_rig(sib, tty=True, answers=[answer])
                self.assertEqual(r('continue'), 0)
                self.assertEqual(r.handoff.stood, [r.folder])
                self.assertEqual(r.calls.execs[0][3], r.folder)
                self.assertIsNone(r.choices.consent(sib, 'codex'))
                self.assertNotIn('last worked in %s;' % sib, r.text())
                self.assertIn('Carrying on in Codex 0.169.1 (ChatGPT.app), in this folder.', r.lines())

    def test_the_question_comes_before_git_runs_there(self):
        sib = self.sibling()
        seen = {}

        def look(prompt):
            seen['stood'] = list(r.handoff.stood)
            return 'y'
        r = self.out_rig(sib, tty=True, answers=[look])
        r('continue')
        self.assertEqual(seen['stood'], [])

    def test_not_at_a_terminal_the_chats_own_folder_and_the_allow_line(self):
        sib = self.sibling()
        r = self.out_rig(sib)
        self.assertEqual(r('continue'), 0)
        self.assertEqual(r.asked, [])
        self.assertEqual(r.handoff.stood, [r.folder])
        self.assertIn(not_allowed(r.folder, sib), r.lines())
        self.assertIn('cd %s' % r.folder, r.lines()[-1])
        self.assertIsNone(r.choices.consent(sib, 'codex'))

    def test_a_yes_given_before_for_that_folder_is_used(self):
        sib = self.sibling()
        r = self.out_rig(sib)
        r.choices.set_consent(sib, 'codex', 'yes', NOW - 60)
        self.assertEqual(r('continue'), 0)
        self.assertEqual(r.handoff.stood, [sib])
        self.assertIn('cd %s' % sib, r.lines()[-1])

    def test_a_parent_that_holds_the_home_folder_is_never_used(self):
        r = self.out_rig(self.root, tty=True)               # root holds root/home, the made-up home
        self.assertEqual(r('continue'), 0)
        self.assertEqual(r.asked, [])
        self.assertEqual(r.handoff.stood, [r.folder])
        self.assertIn(too_wide(r.folder, self.root), r.lines())

    def test_the_root_and_the_home_folder_are_never_used(self):
        for work in ('/', os.path.join(self.root, 'home')):
            with self.subTest(work=work):
                r = self.out_rig(work, tty=True)
                self.assertEqual(r('continue'), 0)
                self.assertEqual((r.asked, r.handoff.stood), ([], [r.folder]))
                self.assertEqual(r.calls.execs[0][3], r.folder)
                self.assertIn(too_wide(r.folder, work), r.lines())

    def test_a_parent_below_the_home_is_asked(self):
        site = os.path.join(self.root, 'site')
        r = self.out_rig(site, own=os.path.join(site, 'app'), tty=True, answers=['y'])
        self.assertEqual(r('continue'), 0)
        self.assertEqual(r.asked, [asked(r.folder, site)])
        self.assertEqual(r.handoff.stood, [site])

    def test_never_on_either_folder_refuses(self):
        sib = self.sibling()
        r = self.out_rig(sib, tty=True)
        r.choices.set_consent(sib, 'codex', 'never', NOW)
        self.assertEqual(r('continue'), 1)
        self.assertEqual(r.lines()[-1], "You said this folder's chats never go to Codex. switchboard allow codex "
                                        "lifts that.")
        self.assertEqual((r.asked, r.handoff.stood, r.calls.execs), ([], [], []))
        r = self.out_rig(sib, tty=True, yes_own=False)
        r.choices.set_consent(r.folder, 'codex', 'never', NOW)
        self.assertEqual(r('continue'), 1)
        self.assertEqual((r.asked, r.handoff.stood, r.calls.execs), ([], [], []))

    def test_a_stay_on_the_work_folder_refuses(self):
        sib = self.sibling()
        r = self.out_rig(sib, tty=True)
        r.choices.set_stay(sib, 'claude', NOW)
        self.assertEqual(r('continue'), 1)
        self.assertEqual(r.lines()[-1], 'This folder stays on Claude. switchboard stay off lifts that.')
        self.assertEqual((r.asked, r.handoff.stood), ([], []))

    def test_a_never_on_a_folder_below_is_honoured(self):
        r, w = self.moved_rig(tty=True)
        r.choices.set_consent(w, 'codex', 'never', NOW)
        self.assertEqual(r('continue'), 1)
        self.assertEqual((r.handoff.stood, r.calls.execs), ([], []))


class Back(Base, unittest.TestCase):
    def back_rig(self, claude_chats, codex_chats, to_chat=None, **kw):
        r = self.rig(claude_chats=claude_chats, codex_chats=(), **kw)
        r.codex.chats_ = [c(r.folder) for c in codex_chats]
        r.tasks.add_hop(Hop(id=HOP, task='t-0000000a', kind='continue', at=LEFT_AT, folder=r.folder,
                            from_provider='claude', from_chat=CHAT_A, to_provider='codex', to_chat=to_chat,
                            state='started'))
        return r

    def test_opening_the_chat_again_keeps_its_own_folder(self):
        w = {}

        def left(folder):
            w['w'] = os.path.join(folder, 'api')
            os.makedirs(w['w'], exist_ok=True)
            return chat('claude', folder, cid=CHAT_A, ago=4000.0, work_folder=w['w'])

        r = self.back_rig((left,), (lambda f: chat('codex', os.path.join(f, 'api'), cid=CHAT_B, ago=60.0),),
                          to_chat=CHAT_B, tty=True, answers=['y'])
        self.assertEqual(r('back'), 0)
        self.assertEqual(r.calls.execs[0][3], r.folder)
        self.assertNotIn('last worked in', r.text())

    def test_the_chat_the_move_led_to_is_found_in_the_work_folder(self):
        w = {}

        def left(folder):
            w['w'] = os.path.join(folder, 'api')
            os.makedirs(w['w'], exist_ok=True)
            return chat('claude', folder, cid=CHAT_A, ago=4000.0, work_folder=w['w'])

        r = self.back_rig((left,), (lambda f: chat('codex', os.path.join(f, 'api'), cid=CHAT_B, ago=60.0,
                                                   started_ago=3500.0),))
        r.codex.transcripts[CHAT_B] = Transcript(chat('codex', w['w'], cid=CHAT_B), (
            Event(contract.NOTICE, words.NOTE_MARK_TEXT.format(hop=HOP), origin='switchboard-note'),),
            ReadInfo(reader='fake'))
        self.assertEqual(r('back'), 0)
        self.assertEqual(r.tasks.get(HOP).to_chat, CHAT_B)
        self.assertEqual(r.handoff.stood, [w['w']])

    def test_a_fresh_start_goes_where_the_other_sides_work_is(self):
        w = {}

        def away(folder):
            w['w'] = os.path.join(folder, 'docs')
            os.makedirs(w['w'], exist_ok=True)
            return chat('codex', folder, cid=CHAT_B, ago=60.0, work_folder=w['w'])

        r = self.back_rig((), (away,), to_chat=CHAT_B, tty=True)
        self.assertEqual(r('back'), 0)
        n = r.handoff.notes[-1]
        prog = r.claude.found_.program
        first = words.FIRST_WORDS.format(hop=n.hop, path=n.path, from_name=OTHER)
        self.assertEqual(r.calls.execs[0][1:], ((prog.path, first), r.calls.execs[0][2], w['w']))
        self.assertEqual(r.handoff.stood, [w['w']])
        self.assertEqual(r.handoff.made[0]['stand']['started_in'], r.folder)
        lines = r.lines()
        self.assertEqual(lines.count(moved_line(r.folder, w['w'], to='Claude')), 1)

    def gone_rig(self, work, **kw):
        """The chat that was left is gone; the Codex chat the move led to last worked in work."""
        if not os.path.isdir(work):
            os.makedirs(work)
        return self.back_rig((), (lambda f: chat('codex', f, cid=CHAT_B, ago=60.0, work_folder=work),),
                             to_chat=CHAT_B, **kw)

    def test_a_fresh_start_outside_asks_first(self):
        sib = os.path.join(self.root, 'notes')
        r = self.gone_rig(sib, tty=True, answers=['y'])
        self.assertEqual(r('back'), 0)
        self.assertEqual(r.asked, [asked(r.folder, sib, to='Claude')])
        self.assertEqual(r.handoff.stood, [sib])
        self.assertEqual(r.calls.execs[0][3], sib)
        self.assertEqual(r.choices.consent(sib, 'claude'), 'yes')

    def test_a_fresh_start_outside_answered_no(self):
        sib = os.path.join(self.root, 'notes')
        r = self.gone_rig(sib, tty=True, answers=['n'])
        self.assertEqual(r('back'), 0)
        self.assertEqual(r.handoff.stood, [r.folder])
        self.assertEqual(r.calls.execs[0][3], r.folder)

    def test_a_fresh_start_outside_not_at_a_terminal(self):
        sib = os.path.join(self.root, 'notes')
        r = self.gone_rig(sib)
        self.assertEqual(r('back'), 0)
        self.assertEqual((r.asked, r.handoff.stood), ([], [r.folder]))
        self.assertIn(not_allowed(r.folder, sib, to_id='claude'), r.lines())

    def test_a_fresh_start_never_in_a_folder_too_wide(self):
        for work in ('/', self.root):
            with self.subTest(work=work):
                r = self.gone_rig(work, tty=True)
                self.assertEqual(r('back'), 0)
                self.assertEqual((r.asked, r.handoff.stood), ([], [r.folder]))
                self.assertIn(too_wide(r.folder, work), r.lines())

    def test_a_never_on_the_work_folder_refuses_back(self):
        sib = os.path.join(self.root, 'notes')
        r = self.gone_rig(sib, tty=True)
        r.choices.set_consent(sib, 'claude', 'never', NOW)
        self.assertEqual(r('back'), 1)
        self.assertEqual((r.asked, r.handoff.stood, r.calls.execs), ([], [], []))

    def test_reopening_the_chat_left_reads_git_only_where_allowed(self):
        sib = os.path.join(self.root, 'notes')
        os.makedirs(sib)
        r = self.back_rig((lambda f: chat('claude', f, cid=CHAT_A, ago=4000.0),),
                          (lambda f: chat('codex', f, cid=CHAT_B, ago=60.0, work_folder=sib),), to_chat=CHAT_B,
                          tty=True, answers=['n', 'n'])
        self.assertEqual(r('back'), 0)
        self.assertEqual(r.asked[0], asked(r.folder, sib, to='Claude', starts=False))
        self.assertEqual(r.handoff.stood, [r.folder])


class NoChatHere(Base, unittest.TestCase):
    def test_it_says_where_chats_are_listed(self):
        r = self.rig(claude_chats=())
        r.choices.set_consent(r.folder, 'codex', 'yes', NOW - 3600)
        self.assertEqual(r('continue'), 1)
        self.assertEqual(r.lines(), [
            "No chat of Claude's or Codex's ran in this folder in the last 7 days. Name one with switchboard "
            "continue --chat NAME.",
            'A chat is listed under the folder it was started in: run switchboard continue there.'])

    def test_it_names_the_folders_above_with_a_chat_of_the_last_day(self):
        r = self.rig(claude_chats=(lambda f: chat('claude', f),
                                   lambda f: chat('claude', os.path.dirname(f), cid=CHAT_B, ago=3 * 86400.0)))
        site = r.folder
        r.folder = self.work(r, 'api')                      # typed in a folder below the one the chat began in
        self.assertEqual(r('continue'), 1)
        self.assertEqual(r.lines()[1], 'A chat is listed under the folder it was started in. Chats of the last day '
                                       'were started in %s; run switchboard continue in one of them.'
                         % words.one_line(site, 200))

    def test_up_to_three_folders_nearest_first(self):
        deep = os.path.join('a', 'b', 'c', 'd')
        r = self.rig(claude_chats=())
        top = os.path.join(r.folder, deep)
        os.makedirs(top)
        here = top
        ups = []
        for _ in range(4):
            here = os.path.dirname(here)
            ups.append(here)
        r.claude.chats_ = [chat('claude', f, name='n%d' % i, cid=CHAT_A[:-1] + str(i)) for i, f in enumerate(ups)]
        r.folder = top
        self.assertEqual(r('continue'), 1)
        self.assertEqual(r.lines()[1], 'A chat is listed under the folder it was started in. Chats of the last day '
                                       'were started in %s; run switchboard continue in one of them.'
                         % ', '.join(words.one_line(f, 200) for f in ups[:3]))

    def test_a_folder_name_goes_through_safe(self):
        r = self.rig(claude_chats=())
        odd = os.path.join(r.folder, 'x\x1b]0;title\x07y')
        below = os.path.join(odd, 'z')
        os.makedirs(below)
        r.claude.chats_ = [chat('claude', odd)]
        r.folder = below
        self.assertEqual(r('continue'), 1)
        self.assertNotIn('\x1b', r.text())
        self.assertIn(words.one_line(odd, 200), r.lines()[1])


if __name__ == '__main__':
    unittest.main()
