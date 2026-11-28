import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# switchboard add PROVIDER, allow, stay and order (DESIGN 12.2, 12.6, 12.7, 12.8 and 13), word for word with their
# exit codes, on a Kit of stand-ins (tests/fakes_provider_kit.py): no real app, no real chat, nothing outside the
# test's own temp folder.
#
# - add: an unknown word (2), not on this Mac (1), already added (0), added with each of the three sign-in lines,
#   added with a program older than the one that wrote the chats, each ending with what a press on its row sends;
#   never a login: add only reads Found.signed_in.
# - allow: answered at a terminal only (1 otherwise, and no question is asked); the question is the continue one
#   without the note's size and without `s`; y lifts a typed never and leaves a stay alone; n changes nothing (1);
#   never records never; no flag gives a yes.
# - stay: with no word it prints its forms and exits 2; a stay sets never for every other added provider and marks
#   them; off lifts only those marks, so a never the person typed stays.
# - order: alone it prints the order; with ids each must be known (2) and added (1), no repeats (2).
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))

from fakes_provider_kit import NOW, KitCase, codex_found, codex_limits  # noqa: E402
from switchboard.providers import words  # noqa: E402

HELP = {k: v + '\n' for k, v in words.HELP.items()}
DAY_BEFORE = words.date(NOW - 86400, NOW)
NOTICE = ["Pressing the Codex row asks no more: it sends your folder's newest chat,",
          "your words and files, to OpenAI under your ChatGPT plan and OpenAI's terms."]
ALLOW_QUESTION = (
    "Carry this chat on in Codex? A note of it goes to OpenAI: what you asked, what Claude answered, tool results and\n"
    "file names, with anything that looks like a secret taken out as well as Switchboard can.\n"
    "Codex will then work in this folder and can read its files. What OpenAI does with it follows OpenAI's terms and\n"
    "the settings in your Codex account. Switchboard uses the note for nothing else.\n"
    "The same yes lets switchboard hand give Codex jobs in this folder. {work}You are asked once for this folder "
    "(site).\n"
    "  y      yes, for this folder from now on\n"
    "  n      not now\n"
    "  never  never from this folder\n")


class Add(KitCase, unittest.TestCase):
    def test_an_unknown_word_names_the_known_ones(self):
        r = self.rig(codex_added=False)
        self.assertEqual(r('add', 'gemini'), 2)
        self.assertEqual(r.text(), 'Switchboard does not know an assistant called "gemini". It knows: claude, '
                                   'codex.\n' + HELP['add'])

    def test_more_words_than_one_provider_are_wrong_words(self):
        r = self.rig(codex_added=False)
        self.assertEqual(r('add', 'codex', 'now'), 2)
        self.assertEqual(r.text(), HELP['add'])
        self.assertNotIn('codex', r.choices.added())

    def test_not_on_this_mac(self):
        r = self.rig(codex_added=False)
        r.codex.found_ = None
        self.assertEqual(r('add', 'codex'), 1)
        self.assertEqual(r.text(), "Codex is not on this Mac. Install OpenAI's Codex (its app or its command line), "
                                   "then run switchboard add codex again.\n")
        self.assertEqual(r.said, [])

    def test_a_build_without_the_adapter_is_not_on_this_mac_either(self):
        r = self.rig(codex=False)
        self.assertEqual(r('add', 'codex'), 1)
        self.assertTrue(r.text().startswith('Codex is not on this Mac.'), r.text())

    def test_already_added(self):
        r = self.rig()
        self.assertEqual(r('add', 'codex'), 0)
        self.assertEqual(r.text(), 'Codex is already in your order (added %s).\n' % DAY_BEFORE)

    def test_added_with_each_sign_in_line(self):
        cases = ((True, 'It looks signed in.'),
                 (False, 'Sign in once in Codex itself: run codex login, or open the ChatGPT app. Then switchboard '
                         'providers.'),
                 (None, 'Whether it is signed in shows when its numbers are first read (switchboard providers '
                        '--ask).'))
        for signed_in, line in cases:
            with self.subTest(signed_in=signed_in):
                r = self.rig(codex_added=False, codex_found_=codex_found(self.root, signed_in=signed_in), tty=True)
                self.assertEqual(r('add', 'codex'), 0)
                self.assertEqual(r.lines(), [
                    'Added Codex. Your order: Claude, then Codex.',
                    'Switchboard will run Codex 0.169.1 (ChatGPT.app). Codex keeps its own sign-in; Switchboard '
                    'never reads or keeps it.',
                    line] + NOTICE)                     # then what a press on its row sends, said this once
                self.assertTrue(r.choices.notice_said('codex'))             # kept: said at a terminal
                self.assertEqual(r.choices.added(), {'claude': 0, 'codex': NOW})
                self.assertEqual(r.said, [('Added Codex to the providers you use', 'setting', {'to': 'Codex'})])

    def test_added_with_a_program_older_than_the_chats(self):
        r = self.rig(codex_added=False, codex_found_=codex_found(self.root, version='0.164.0', seen='0.169.1'))
        self.assertEqual(r('add', 'codex'), 0)
        self.assertEqual(r.lines()[-3], 'The newest Codex here (0.164.0) is older than the one that wrote your Codex '
                                        'chats (0.169.1). Update Codex before carrying a chat on in it.')
        self.assertEqual(r.lines()[-2:], NOTICE)
        self.assertEqual(r.lines()[0], 'Added Codex. Your order: Claude, then Codex.')

    def test_no_usable_copy_is_not_added(self):
        found = codex_found(self.root)
        from dataclasses import replace
        refused = replace(found.program, refused='its folder can be changed by other users')
        r = self.rig(codex_added=False, codex_found_=replace(found, program=None, programs=(refused,)))
        self.assertEqual(r('add', 'codex'), 1)
        self.assertEqual(r.text(), 'Codex is on this Mac, but no copy of it can be used: its folder can be changed '
                                   'by other users.\n')
        self.assertNotIn('codex', r.choices.added())


class Remove(KitCase, unittest.TestCase):
    # switchboard remove PROVIDER: off the list (out of added and out of the order) and its notice record with it, so
    # adding it again says the notice again. What was said per folder stays. The accounts' remove NAME is another
    # command (switchboard/__main__.py sends only `remove <another provider's id>` here).
    def test_takes_it_off_the_list_and_says_so(self):
        r = self.rig(order=['codex', 'claude'])
        r.choices.set_consent(r.folder, 'codex', 'never', NOW)
        self.assertEqual(r('remove', 'codex'), 0)
        self.assertEqual(r.lines(), ['Took Codex off your order; Codex itself stays as it is. Your order: Claude.'])
        self.assertNotIn('codex', r.choices.added())
        self.assertNotIn('codex', r.choices.order())
        self.assertFalse(r.choices.notice_said('codex'))
        self.assertEqual(r.choices.consent(r.folder, 'codex'), 'never')    # a never typed in a folder stays
        self.assertEqual(r.said, [('Took Codex off the providers you use', 'setting', {'to': 'Codex'})])

    def test_added_again_it_says_the_notice_again(self):
        r = self.rig(codex_found_=codex_found(self.root, signed_in=True), tty=True)
        r('remove', 'codex')
        r('add', 'codex')
        self.assertEqual(r.lines()[-2:], NOTICE)
        self.assertTrue(r.choices.notice_said('codex'))

    def test_not_added_changes_nothing(self):
        r = self.rig(codex_added=False)
        self.assertEqual(r('remove', 'codex'), 0)
        self.assertEqual((r.lines(), r.said), (['Codex is not in your order.'], []))

    def test_the_first_provider_stays(self):
        r = self.rig()
        self.assertEqual(r('remove', 'claude'), 1)
        self.assertEqual(r.lines(), ['Claude is always in your order. switchboard remove NAME forgets one of its '
                                     'accounts.'])
        self.assertIn('codex', r.choices.added())

    def test_wrong_words(self):
        r = self.rig()
        self.assertEqual(r('remove'), 2)
        self.assertEqual(r('remove', 'codex', 'now'), 2)
        self.assertEqual(r('remove', 'gemini'), 2)
        self.assertIn('codex', r.choices.added())


class Allow(KitCase, unittest.TestCase):
    def test_not_at_a_terminal_it_asks_nothing(self):
        r = self.rig()
        self.assertEqual(r('allow', 'codex'), 1)
        self.assertEqual(r.text(), 'switchboard allow is answered in a terminal only.\n')
        self.assertEqual(r.asked, [])
        self.assertIsNone(r.choices.consent(r.folder, 'codex'))

    def test_unknown_and_not_added(self):
        r = self.rig(tty=True)
        self.assertEqual(r('allow', 'gemini'), 2)
        self.assertTrue(r.text().endswith(HELP['allow']))
        r = self.rig(tty=True, codex_added=False)
        self.assertEqual(r('allow', 'codex'), 1)
        self.assertEqual(r.text(), 'Codex is not added. switchboard add codex adds it.\n')
        self.assertEqual(r.asked, [])

    def test_no_flag_gives_a_yes(self):
        for extra in ('--yes', '-y', 'y', '--force'):
            with self.subTest(extra=extra):
                r = self.rig()
                self.assertEqual(r('allow', 'codex', extra), 2)
                self.assertIsNone(r.choices.consent(r.folder, 'codex'))

    def test_yes(self):
        r = self.rig(tty=True, answers=['y'])
        self.assertEqual(r('allow', 'codex'), 0)
        self.assertEqual(r.text(), ALLOW_QUESTION.format(work='') + 'Chats in this folder (site) may go to Codex.\n')
        self.assertEqual(r.asked, ['> '])
        self.assertEqual(r.choices.consent(r.folder, 'codex'), 'yes')
        self.assertEqual(r.said, [('Chats in site may go to Codex', 'setting', {'to': 'Codex'})])

    def test_a_work_plan_is_named(self):
        r = self.rig(tty=True, answers=['y'])
        r.codex.limits_ = codex_limits(plan='business')
        self.assertEqual(r('allow', 'codex'), 0)
        self.assertIn(ALLOW_QUESTION.format(work="This is a work plan: your employer's terms apply. "), r.text())

    def test_yes_lifts_a_typed_never_and_leaves_a_stay(self):
        r = self.rig(tty=True, answers=['y'])
        r.choices.set_stay(r.folder, 'claude', NOW)
        self.assertEqual(r.choices.consent(r.folder, 'codex'), 'never')
        self.assertEqual(r('allow', 'codex'), 0)
        self.assertEqual(r.choices.consent(r.folder, 'codex'), 'yes')
        self.assertEqual(r.choices.stays(r.folder), 'claude')

    def test_no_changes_nothing(self):
        for answer in ('n', '', 'maybe'):
            with self.subTest(answer=answer):
                r = self.rig(tty=True, answers=[answer])
                self.assertEqual(r('allow', 'codex'), 1)
                self.assertTrue(r.text().endswith('Nothing was changed.\n'))
                self.assertIsNone(r.choices.consent(r.folder, 'codex'))
                self.assertEqual(r.said, [])

    def test_never(self):
        r = self.rig(tty=True, answers=['never'])
        self.assertEqual(r('allow', 'codex'), 0)
        self.assertEqual(r.choices.consent(r.folder, 'codex'), 'never')
        self.assertTrue(r.text().endswith('Chats in this folder (site) never go to Codex. switchboard allow codex '
                                          'lifts that.\n'))
        self.assertEqual(r.said, [('Chats in site never go to Codex', 'setting', {'to': 'Codex'})])

    def test_claude_can_be_named_too(self):          # a Codex chat carried on to Claude asks the same for Claude
        r = self.rig(tty=True, answers=['y'])
        self.assertEqual(r('allow', 'claude'), 0)
        self.assertTrue(r.text().startswith('Carry this chat on in Claude? A note of it goes to Anthropic: what you '
                                            'asked, what Codex answered'), r.text())
        self.assertEqual(r.choices.consent(r.folder, 'claude'), 'yes')


class Stay(KitCase, unittest.TestCase):
    def test_no_word_is_never_a_default(self):
        r = self.rig()
        self.assertEqual(r('stay'), 2)
        self.assertEqual(r.text(), HELP['stay'])
        self.assertIn('switchboard stay claude', r.text())
        self.assertIn('off lifts it', r.text())
        self.assertIsNone(r.choices.stays(r.folder))

    def test_stay_and_off(self):
        r = self.rig()
        self.assertEqual(r('stay', 'claude'), 0)
        self.assertEqual(r.text(), 'This folder (site) stays on Claude: continue and hand never send its chats '
                                   'elsewhere.\n')
        self.assertEqual(r.choices.stays(r.folder), 'claude')
        self.assertEqual(r.choices.consent(r.folder, 'codex'), 'never')
        r.out.truncate(0), r.out.seek(0)
        self.assertEqual(r('stay', 'off'), 0)
        self.assertEqual(r.text(), "This folder's chats may go to other providers again; each one still asks once.\n")
        self.assertIsNone(r.choices.stays(r.folder))
        self.assertIsNone(r.choices.consent(r.folder, 'codex'))
        self.assertEqual(r.said, [('site stays on Claude', 'setting', {'to': 'Claude'}),
                                  ('site may carry on elsewhere again', 'setting', {})])

    def test_a_typed_never_survives_off(self):
        r = self.rig()
        r.choices.set_consent(r.folder, 'codex', 'never', NOW - 60)
        self.assertEqual(r('stay', 'claude'), 0)
        self.assertEqual(r('stay', 'off'), 0)
        self.assertEqual(r.choices.consent(r.folder, 'codex'), 'never')
        self.assertTrue(r.text().endswith("This folder's chats may go to other providers again; each one still asks "
                                          "once.\nChats here still never go to Codex: switchboard allow codex lifts "
                                          "that.\n"), r.text())

    def test_unknown_and_not_added(self):
        r = self.rig()
        self.assertEqual(r('stay', 'gemini'), 2)
        self.assertEqual(r('stay', 'claude', 'codex'), 2)
        r = self.rig(codex_added=False)
        self.assertEqual(r('stay', 'codex'), 1)
        self.assertEqual(r.text(), 'Codex is not added. switchboard add codex adds it.\n')


class Order(KitCase, unittest.TestCase):
    def test_alone_it_prints_the_order(self):
        r = self.rig()
        self.assertEqual(r('order'), 0)
        self.assertEqual(r.text(), 'Order: Claude, then Codex.\n')
        self.assertEqual(r.said, [])

    def test_named_ones_first(self):
        r = self.rig()
        self.assertEqual(r('order', 'codex'), 0)
        self.assertEqual(r.text(), 'Order: Codex, then Claude.\n')
        self.assertEqual(r.choices.order(), ['codex', 'claude'])
        self.assertEqual(r.said, [('Providers in order: Codex, then Claude', 'setting', {})])

    def test_wrong_words(self):
        r = self.rig()
        self.assertEqual(r('order', 'codex', 'codex'), 2)
        self.assertEqual(r.text(), 'Name each provider once.\n' + HELP['order'])
        r = self.rig()
        self.assertEqual(r('order', 'gemini'), 2)
        self.assertEqual(r.choices.order(), ['claude', 'codex'])

    def test_not_added(self):
        r = self.rig(codex_added=False)
        self.assertEqual(r('order', 'codex'), 1)
        self.assertEqual(r.text(), 'Codex is not added. switchboard add codex adds it.\n')
        self.assertEqual(r.choices.order(), ['claude'])


if __name__ == '__main__':
    unittest.main()
