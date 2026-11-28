import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# One add: `switchboard add` with no other word keeps the Claude login as it does today and, when Codex is on this
# Mac, signed in and not yet added, asks ONE question that carries its notice (what pressing its row sends, to whom,
# under whose terms), default no. A yes adds it and keeps that the notice was said; Enter or no adds nothing. With no
# terminal nothing is said or changed. The same question is cli.offer, for a first run. `switchboard add codex` keeps
# its own lines, then says the notice. `switchboard remove codex` takes it off the list again.
#
# Made-up accounts and programs only: the adapters are the stand-ins of tests/fakes_provider_kit.py and
# tests/fakes_codex.py; Codex's login is only ever looked for with os.stat, never opened.
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from fakes_provider_kit import NOW, KitCase, codex_found, codex_limits  # noqa: E402
from switchboard import __main__ as main  # noqa: E402
from switchboard import core  # noqa: E402
from switchboard.providers import cli, contract, words  # noqa: E402
from switchboard.providers.contract import Found  # noqa: E402
from test_term import A, FakeTty, Sandbox  # noqa: E402

# The notice, said before the one question: what pressing its row will do, before it is ever pressed.
NOTICE = ["Pressing the Codex row asks no more: it sends your folder's newest chat,",
          "your words and files, to OpenAI under your ChatGPT plan and OpenAI's terms."]
OFFER = 'Add Codex too? [y/N] '
ADDED = '\n'.join(NOTICE)          # what the stand-in add_signed_in of TheAddCommand prints
QUESTION = "Show its account now? Codex's helper runs once and may renew its sign-in. [y/N] "


class AddWhatIsSignedIn(KitCase, unittest.TestCase):
    def signed_in(self, **kw):
        # at a terminal, answering the offer yes and the one question that follows no, unless a test says otherwise
        return self.rig(codex_added=False, codex_found_=codex_found(self.root, signed_in=True),
                        **dict({'tty': True, 'answers': ['y', 'n']}, **kw))

    def test_without_a_terminal_add_says_and_changes_nothing_about_codex(self):
        # Once added, the press on Codex's row needs no other yes, so it is offered only where a person reads the
        # question: standard input and output both a terminal, the same gate as the press itself.
        r = self.signed_in(tty=False)
        self.assertEqual(cli.add_signed_in(r.kit), 0)
        self.assertEqual((r.lines(), r.said, r.asked), ([], [], []))
        self.assertNotIn('codex', r.choices.added())
        self.assertNotIn('detect', r.codex.calls)

    def test_codex_signed_in_and_not_added_is_offered_with_its_notice(self):
        r = self.signed_in()
        self.assertEqual(cli.add_signed_in(r.kit), 1)
        self.assertEqual(r.lines(), NOTICE)
        self.assertEqual(r.asked[0], OFFER)
        self.assertIn('codex', r.choices.added())
        self.assertTrue(r.choices.notice_said('codex'))
        self.assertEqual(r.said, [('Added Codex to the providers you use', 'setting', {'to': 'Codex'})])
        # then its one question (answered no here), and nothing else is run or started
        self.assertEqual((r.asked, r.calls.runs, r.calls.execs), ([OFFER, QUESTION], [], []))

    def test_enter_or_no_adds_nothing_and_asks_nothing_more(self):
        for answer in ('', 'n', 'no', 'x'):
            with self.subTest(answer=answer):
                r = self.signed_in(answers=[answer])
                self.assertEqual(cli.add_signed_in(r.kit), 0)
                self.assertEqual((r.lines(), r.asked, r.said), (NOTICE, [OFFER], []))
                self.assertNotIn('codex', r.choices.added())
                self.assertFalse(r.choices.notice_said('codex'))

    def test_the_notice_says_what_the_press_will_do(self):
        # Two lines, each within 80 columns: pressing the row sends the folder's newest chat (words and files) to
        # OpenAI, under the person's ChatGPT plan and OpenAI's terms, and asks nothing more. Then one short question.
        names = dict(name='Codex', vendor='OpenAI', plan='ChatGPT plan')
        self.assertEqual(words.NOTICE.format(**names).split('\n'), NOTICE)
        self.assertEqual(words.ADD_OFFER.format(**names), OFFER)
        self.assertLessEqual(max(len(x) for x in NOTICE + [OFFER]), 80)
        for said in ('Pressing the Codex row', 'asks no more', "folder's newest chat", 'words and files', 'to OpenAI',
                     'ChatGPT plan', "OpenAI's terms"):
            self.assertIn(said, ADDED.replace('\n', ' '))

    def test_said_once(self):
        r = self.signed_in()
        cli.add_signed_in(r.kit)
        self.assertEqual(cli.add_signed_in(r.kit), 0)                   # added already: nothing more is said
        self.assertEqual(r.lines(), NOTICE)

    def test_nothing_when_codex_is_not_here_signed_in_and_usable(self):
        for found in (None, Found('codex', installed=False), codex_found(self.root, signed_in=None),
                      Found('codex', installed=True, signed_in=True)):     # here, but no copy of it can be used
            with self.subTest(found=found):
                r = self.rig(codex_added=False, codex_found_=found, tty=True)
                if found is None:
                    r.codex.found_ = None
                self.assertEqual(cli.add_signed_in(r.kit), 0)
                self.assertEqual((r.lines(), r.said, r.asked), ([], [], []))
                self.assertNotIn('codex', r.choices.added())

    def test_an_adapter_that_can_tell_it_is_not_signed_in_runs_nothing(self):
        r = self.signed_in()
        r.codex.signed_in = lambda: None                                # its login is not there
        self.assertEqual(cli.add_signed_in(r.kit), 0)
        self.assertNotIn('detect', r.codex.calls)

    def test_a_failure_is_logged_and_never_stops_add(self):
        r = self.signed_in()
        r.choices.add = lambda pid, now: 1 / 0
        logged = []
        with mock.patch.object(core, 'log', logged.append):
            self.assertEqual(cli.add_signed_in(r.kit), 0)
        self.assertEqual(logged, ['adding the providers signed in: ZeroDivisionError'])


class OfferForAFirstRun(KitCase, unittest.TestCase):
    """cli.offer(pid, kit): the one call a first run makes. It says the notice, asks, adds, and answers what
    happened, in one word."""

    def test_each_answer(self):
        signed_in = codex_found(self.root, signed_in=True)
        r = self.rig(codex_added=False, codex_found_=signed_in, tty=True, answers=['y', 'n'])
        self.assertEqual(cli.offer('codex', kit=r.kit), 'added')
        self.assertEqual(cli.offer('codex', kit=r.kit), 'added_before')
        r = self.rig(codex_added=False, codex_found_=signed_in, tty=True, answers=[''])
        self.assertEqual(cli.offer('codex', kit=r.kit), 'no')
        r = self.rig(codex_added=False, codex_found_=signed_in)
        self.assertEqual((cli.offer('codex', kit=r.kit), r.lines(), r.asked), ('not_asked', [], []))
        r = self.rig(codex_added=False, codex_found_=codex_found(self.root, signed_in=None), tty=True)
        self.assertEqual((cli.offer('codex', kit=r.kit), r.lines(), r.asked), ('not_here', [], []))
        self.assertEqual(cli.offer('claude', kit=r.kit), 'not_here')    # its seats are the account list
        self.assertEqual(set(cli.OFFERED), {'added', 'no', 'not_asked', 'added_before', 'not_here'})

    def test_offerable_is_whether_offer_would_ask_and_says_nothing(self):
        # A first run plans its steps with cli.offerable: the same checks as offer, nothing said, asked or kept.
        signed_in = codex_found(self.root, signed_in=True)
        for kw, would in (({'codex_added': False, 'codex_found_': signed_in, 'tty': True}, True),
                          ({'codex_added': True, 'tty': True}, False),
                          ({'codex_added': False, 'codex_found_': signed_in}, False),
                          ({'codex_added': False, 'codex_found_': codex_found(self.root, signed_in=None),
                            'tty': True}, False),
                          ({'codex': False, 'tty': True}, False)):
            with self.subTest(**{k: str(v) for k, v in kw.items()}):
                r = self.rig(**kw)
                self.assertIs(cli.offerable('codex', kit=r.kit), would)
                self.assertEqual((r.lines(), r.asked, r.said), ([], [], []))
                self.assertEqual('codex' in r.choices.added(), kw.get('codex_added', True) and kw.get('codex', True))
        self.assertIs(cli.offerable('claude', kit=r.kit), False)              # its seats are the account list

    def test_bare_add_then_a_press_asks_no_second_question(self):
        from fakes_provider_kit import CHAT_A, chat
        r = self.rig(codex_added=False, codex_found_=codex_found(self.root, signed_in=True), tty=True,
                     answers=['y', 'n'], claude_chats=[lambda f: chat('claude', f, cid=CHAT_A, name='Upload retries')])
        self.assertEqual(cli.add_signed_in(r.kit), 1)
        self.assertEqual(cli.move('codex', kit=r.kit), 0)
        self.assertEqual(r.asked, [OFFER, QUESTION])                    # the press asked nothing
        self.assertEqual(len(r.calls.execs), 1)


class OneQuestionAfterAdding(KitCase, unittest.TestCase):
    # Right after add has added Codex, at a terminal, ONE question on one line: the same yes as providers --ask
    # (kept as ask_ok, and every rule of that ask after it). y is yes; Enter or anything else is no.
    def added(self, answer, **kw):
        r = self.rig(codex_added=False, codex_found_=codex_found(self.root, signed_in=True), tty=True,
                     answers=['y'] + ([answer] if answer is not None else []), **kw)
        r.codex.ask_limits = codex_limits(source='ask', ask=contract.ASK_DONE, read_ago=0.0)
        return r

    def test_the_question(self):
        self.assertEqual(QUESTION, words.ADD_ASK.format(name='Codex'))
        self.assertLessEqual(len(QUESTION), 80)

    def test_yes_asks_codex_now_and_keeps_the_yes(self):
        r = self.added('y')
        self.assertEqual(cli.add_signed_in(r.kit), 1)
        self.assertEqual(r.asked, [OFFER, QUESTION])
        self.assertTrue(r.choices.ask_ok('codex'))
        self.assertIn('limits(ask)', r.codex.calls)
        self.assertEqual(r.lines(), NOTICE)                             # nothing more is said when it answered

    def test_enter_or_anything_else_is_no_and_nothing_more_is_said(self):
        for answer in ('', 'n', 'no', 'x', 'Y please'):
            with self.subTest(answer=answer):
                r = self.added(answer)
                self.assertEqual(cli.add_signed_in(r.kit), 1)
                self.assertEqual(r.asked, [OFFER, QUESTION])
                self.assertFalse(r.choices.ask_ok('codex'))
                self.assertNotIn('limits(ask)', r.codex.calls)
                self.assertEqual(r.lines(), NOTICE)
        r = self.added('yes')
        cli.add_signed_in(r.kit)
        self.assertTrue(r.choices.ask_ok('codex'))

    def test_asked_once_whichever_the_answer(self):
        for answer in ('y', ''):
            with self.subTest(answer=answer):
                r = self.added(answer)
                cli.add_signed_in(r.kit)
                self.assertEqual(cli.add_signed_in(r.kit), 0)             # added already: no question again
                self.assertEqual(r.asked, [OFFER, QUESTION])

    def test_a_yes_given_before_is_the_same_yes(self):
        r = self.added(None)
        r.choices.set_ask_ok(NOW, pid='codex')
        self.assertEqual(cli.add_signed_in(r.kit), 1)
        self.assertEqual(r.asked, [OFFER])
        self.assertIn('limits(ask)', r.codex.calls)

    def test_an_ask_that_did_not_answer_is_said_in_one_line(self):
        r = self.added('y')
        r.codex.ask_limits = codex_limits(source='file', ask=contract.ASK_REFUSED,
                                          error='Codex did not answer in time.')
        cli.add_signed_in(r.kit)
        self.assertEqual(r.lines(), NOTICE + ['Codex did not answer now (Codex did not answer in time.). '
                                              'switchboard providers --ask asks it again.'])

    def test_no_question_without_a_terminal(self):
        r = self.rig(codex_added=False, codex_found_=codex_found(self.root, signed_in=True), tty=False)
        self.assertEqual(cli.add_signed_in(r.kit), 0)
        self.assertEqual(r.asked, [])


class CodexSaysWhetherItIsSignedIn(unittest.TestCase):
    def test_by_its_login_files_presence_alone(self):
        from fakes_codex import Rig
        rig = Rig(self)
        rig.app('ChatGPT.app', '0.169.1')
        self.assertIsNone(rig.provider.signed_in())
        os.makedirs(os.path.join(rig.home, '.codex'))
        with open(os.path.join(rig.home, '.codex', 'auth.json'), 'w') as f:
            f.write('{"made-up": true}')
        opened = []
        real_open = open

        def watching(path, *a, **k):
            opened.append(str(path))
            return real_open(path, *a, **k)
        with mock.patch('builtins.open', watching):
            self.assertIs(rig.provider.signed_in(), True)
        self.assertFalse([p for p in opened if p.endswith('auth.json')])
        self.assertIs(rig.provider.detect().signed_in, True)


class TheAddCommand(Sandbox):
    def setUp(self):
        super().setUp()
        self.signed_in(A, 'org-a')
        self.kits = []
        p = mock.patch.object(cli, 'add_signed_in', lambda kit=None: self.kits.append(kit) or print(ADDED) or 1)
        p.start()
        self.addCleanup(p.stop)

    def test_without_a_terminal(self):
        # Codex is reached only at a terminal, through the first run's one place (firstrun.offer_codex).
        self.assertEqual(self.run_cli('add'), (0, '✓ Saved ana@work.example\n'
                                                  'To add another, run switchboard add at a terminal.\n'))
        self.assertEqual(self.kits, [])

    def test_at_a_terminal_before_the_next_sign_in(self):
        def stop(seconds):
            if seconds == main.POLL:
                raise KeyboardInterrupt
        with mock.patch.object(main, '_sleep', stop):
            code, out = self.run_cli('add', tty=FakeTty([]))
        self.assertEqual((code, out), (0, '✓ Saved ana@work.example\n' + ADDED + '\n' + main.ADD_WITHOUT_PAGE + '\n'
                                          'In Claude Code, run /login and sign in to the next account.\n\n'))
        self.assertEqual(self.kits, [None])

    def test_add_claude_is_add(self):
        self.assertEqual(self.run_cli('add', 'claude'), self.run_cli('add'))      # no terminal: the same, no Codex
        self.assertEqual(self.kits, [])

        def stop(seconds):
            if seconds == main.POLL:
                raise KeyboardInterrupt
        with mock.patch.object(main, '_sleep', stop):
            self.run_cli('add', 'claude', tty=FakeTty([]))
        self.assertEqual(self.kits, [None])                                     # at a terminal: Codex, once

    def test_add_codex_keeps_its_own_lines(self):
        from switchboard.providers import cli as providers_cli
        seen = []
        with mock.patch.object(providers_cli, 'main', lambda args: seen.append(args) or 0):
            self.assertEqual(self.run_cli('add', 'codex'), (0, ''))
        self.assertEqual((seen, self.kits), ([['add', 'codex']], []))

    def test_remove_codex_goes_to_the_providers_and_remove_name_stays_the_accounts(self):
        from switchboard.providers import cli as providers_cli
        seen, removed = [], []
        with mock.patch.object(providers_cli, 'main', lambda args: seen.append(args) or 0), \
                mock.patch.object(main, 'remove_command', lambda args: removed.append(args) or 0):
            self.assertEqual(self.run_cli('remove', 'codex'), (0, ''))
            self.assertEqual(self.run_cli('remove', A), (0, ''))
            self.assertEqual(self.run_cli('remove', 'claude'), (0, ''))     # the accounts' own: no account so named
            self.assertEqual(self.run_cli('remove', 'codex', '--yes'), (0, ''))
        self.assertEqual(seen, [['remove', 'codex']])
        self.assertEqual(removed, [[A], ['claude'], ['codex', '--yes']])

    def test_remove_codex_is_in_the_help(self):
        self.assertIn('switchboard remove PROVIDER', main.__doc__)
        self.assertIn('switchboard remove PROVIDER', words.HELP_TEXT)


if __name__ == '__main__':
    unittest.main()
