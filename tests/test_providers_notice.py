import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The notice of what a press on another provider's row sends (the folder's newest chat, the person's words and files),
# to whom (its maker) and under whose terms, said once per provider on every road, and kept in the choices file.
#
# - Every way of adding says it and keeps that it was said: `switchboard add codex`, and bare `switchboard add`.
# - A press at a terminal on a provider whose notice was never said (added on an older build, or by hand), in a folder
#   with no yes of its own, says it as one question [y/N] BEFORE any chat is read into a note. No: nothing is read,
#   nothing is kept, the log says not now. A yes is kept: the next press asks nothing.
# - The yes for a folder that `continue` asks (an older build's) counts as said in that folder.
# - Without a terminal the press is refused as before. Continue here (the menu, the page) makes no note on such a
#   provider and answers one line that sends the person to the list.
#
# Made-up accounts and chats only: the adapters are the stand-ins of tests/fakes_provider_kit.py.
import dataclasses
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from fakes_provider_kit import CHAT_A, NOW, KitCase, chat, codex_found  # noqa: E402
from switchboard import core  # noqa: E402
from switchboard.providers import cli, press, words  # noqa: E402

NOTICE = ["Pressing the Codex row asks no more: it sends your folder's newest chat,",
          "your words and files, to OpenAI under your ChatGPT plan and OpenAI's terms."]
PRESS_NOTICE = ["Pressing the Codex row sends your folder's newest chat, your words and files,",
                "to OpenAI under your ChatGPT plan and OpenAI's terms. It asks this once."]
GO_ON = 'Go on? [y/N] '
FIRST = 'Type switchboard and press the Codex row once first: it says what goes to OpenAI.'


def upload(folder):
    return chat('claude', folder, cid=CHAT_A, name='Upload retries')


class TheWords(unittest.TestCase):
    def test_each_says_what_goes_to_whom_under_whose_terms(self):
        names = dict(name='Codex', vendor='OpenAI', plan='ChatGPT plan')
        self.assertEqual(words.NOTICE.format(**names).split('\n'), NOTICE)
        self.assertEqual(words.PRESS_NOTICE.format(**names).split('\n'), PRESS_NOTICE)
        self.assertEqual(words.PRESS_NOTICE_ASK, GO_ON)
        self.assertEqual(words.PRESS_NOTICE_FIRST.format(**names), FIRST)
        for line in NOTICE + PRESS_NOTICE:                              # a terminal's lines; FIRST is a face's
            self.assertLessEqual(len(line), 80, line)
        self.assertNotIn('\n', FIRST)
        self.assertFalse(hasattr(words, 'ADDED_NOTICE'))                # one notice for every add: NOTICE


class PressAtATerminal(KitCase, unittest.TestCase):
    def press(self, r, why=False):
        start = len(r.lines())
        logged = []
        with mock.patch.object(core, 'log', logged.append):
            code = cli.move('codex', why=why, kit=r.kit)
        return code, r.lines()[start:], logged

    def test_add_codex_says_it_and_the_press_then_asks_nothing(self):
        r = self.rig(codex_added=False, codex_found_=codex_found(self.root, signed_in=True), tty=True,
                     claude_chats=[upload])
        self.assertEqual(r('add', 'codex'), 0)
        self.assertEqual(r.lines()[-2:], NOTICE)
        self.assertTrue(r.choices.notice_said('codex'))
        code, _, _ = self.press(r)
        self.assertEqual((code, r.asked), (0, []))
        self.assertEqual(len(r.calls.execs), 1)

    def test_add_codex_with_no_terminal_says_it_and_the_first_press_still_asks(self):
        # Typed by a script or an assistant's own shell, nobody may read it: it is said, not kept.
        r = self.rig(codex_added=False, codex_found_=codex_found(self.root, signed_in=True), answers=['y'],
                     claude_chats=[upload])
        self.assertEqual(r('add', 'codex'), 0)
        self.assertEqual(r.lines()[-2:], NOTICE)
        self.assertFalse(r.choices.notice_said('codex'))
        r.reach = dataclasses.replace(r.reach, tty=lambda: True)
        code, printed, _ = self.press(r)
        self.assertEqual((code, r.asked, printed[:2]), (0, [GO_ON], PRESS_NOTICE))

    def test_add_codex_again_on_an_older_add_says_it_once(self):
        r = self.rig(noticed=(), tty=True, claude_chats=[upload])
        self.assertEqual(r('add', 'codex'), 0)
        self.assertEqual(r.lines()[1:], NOTICE)
        self.assertTrue(r.choices.notice_said('codex'))
        r('add', 'codex')
        self.assertEqual(len(r.lines()), 4)                             # the second time: only that it is added

    def test_added_on_an_older_build_the_first_press_asks_once(self):
        r = self.rig(noticed=(), tty=True, answers=['y'], claude_chats=[upload])
        code, printed, _ = self.press(r)
        self.assertEqual(code, 0)
        self.assertEqual(r.asked, [GO_ON])
        self.assertEqual(printed[:2], PRESS_NOTICE)
        self.assertEqual(len(r.calls.execs), 1)
        self.assertTrue(r.choices.notice_said('codex'))
        self.assertIsNone(r.choices.consent(r.folder, 'codex'))         # the yes is for the row, not kept per folder
        code, printed, _ = self.press(r)
        self.assertEqual((code, r.asked), (0, [GO_ON]))                 # asked once
        self.assertNotIn(PRESS_NOTICE[0], printed)

    def test_no_is_not_now_and_nothing_is_read(self):
        for answer in ('', 'n', 'never', 'x'):
            with self.subTest(answer=answer):
                r = self.rig(noticed=(), tty=True, answers=[answer], claude_chats=[upload])
                code, printed, logged = self.press(r)
                self.assertEqual(code, 1)
                self.assertEqual(printed, PRESS_NOTICE + ['Nothing was sent.'])
                self.assertFalse([c for c in r.claude.calls if str(c).startswith('read')])
                self.assertEqual((r.handoff.made, r.tasks.hops(r.folder), r.calls.execs), ([], [], []))
                self.assertFalse(r.choices.notice_said('codex'))
                self.assertIsNone(r.choices.consent(r.folder, 'codex'))
                self.assertEqual(logged, ['key press: nothing was started for Codex (not now)'])

    def test_the_question_comes_with_why_too_and_once(self):
        r = self.rig(noticed=(), tty=True, answers=['n'], claude_chats=[upload])
        code, printed, _ = self.press(r, why=True)
        self.assertEqual(code, 1)
        self.assertEqual(printed.count(PRESS_NOTICE[0]), 1)

    def test_a_folder_with_an_older_builds_yes_asks_nothing_new(self):
        r = self.rig(noticed=(), tty=True, claude_chats=[upload])
        r.choices.set_consent(r.folder, 'codex', 'yes', NOW - 86400)
        code, printed, _ = self.press(r)
        self.assertEqual((code, r.asked), (0, []))
        self.assertNotIn(PRESS_NOTICE[0], printed)
        self.assertEqual(len(r.calls.execs), 1)
        self.assertFalse(r.choices.notice_said('codex'))                # said for that folder only

    def test_without_a_terminal_it_is_refused_as_before(self):
        r = self.rig(noticed=(), claude_chats=[upload])
        code, printed, _ = self.press(r)
        self.assertEqual(code, 1)
        self.assertEqual(printed, [words.NOT_ASKED.format(to='Codex', vendor='OpenAI', to_id='codex')])
        self.assertEqual((r.asked, r.handoff.made, r.calls.execs), ([], [], []))

    def test_no_chat_to_carry_needs_no_notice(self):
        r = self.rig(noticed=(), tty=True)                              # Codex opened as it is: nothing of a chat
        code, _, _ = self.press(r)
        self.assertEqual((code, r.asked), (0, []))
        self.assertEqual(len(r.calls.execs), 1)


class ContinueHere(KitCase, unittest.TestCase):
    def test_before_any_notice_it_sends_the_person_to_the_list(self):
        r = self.rig(noticed=(), claude_chats=[upload])
        with self.assertRaises(cli.Refused) as e:
            press.press('codex', {'id': CHAT_A, 'folder': r.folder}, number=6, kit=r.kit)
        self.assertEqual(str(e.exception), FIRST)
        self.assertEqual((r.handoff.made, r.calls.links, r.calls.execs, r.asked), ([], [], [], []))
        self.assertFalse([c for c in r.claude.calls if str(c).startswith('read')])

    def test_once_said_or_with_a_folders_yes_it_opens_as_before(self):
        r = self.rig(claude_chats=[upload])                             # added with its notice
        self.assertIn('said', press.press('codex', {'id': CHAT_A, 'folder': r.folder}, kit=r.kit))
        r = self.rig(noticed=(), claude_chats=[upload])
        r.choices.set_consent(r.folder, 'codex', 'yes', NOW - 86400)
        self.assertIn('said', press.press('codex', {'id': CHAT_A, 'folder': r.folder}, kit=r.kit))
        self.assertEqual(len(r.calls.links), 1)


if __name__ == '__main__':
    unittest.main()
