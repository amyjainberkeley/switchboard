import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# switchboard providers [--ask] [--json] (DESIGN 12.1, 8.3, 8.4): the board, word for word, on a Kit of stand-ins.
#
# - One block per provider in the person's order, then the ones not added: its state words (has room only for
#   numbers under an hour old, had room when last read ..., is out until ..., numbers not known, not signed in), its
#   accounts or its plan and windows, the program Switchboard runs and where, the PATH copy when it is another one and
#   the line saying typing it runs an older program, every refused copy, when it was added and what Switchboard can
#   do with it. Then the folder's line (offered / yes on a date / never / stays) and the order.
# - Not added, not on this Mac, and an adapter whose code did not load each have their own line.
# - --ask: the yes to start the app's helper is asked only at a terminal, once; without it the saved numbers are
#   shown and say so. Each Limits.ask outcome has its own line; an ask that failed is said, exit 0.
# - --json: the shape DESIGN 12.1 gives. Outside values never print a terminal escape.
import json
import os
import sys
import unittest
from dataclasses import replace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))

from fakes_provider_kit import (NOW, KitCase, claude_limits, claude_seats, codex_found, codex_limits,  # noqa: E402
                                program)
from switchboard.providers import contract, words  # noqa: E402
from switchboard.providers.contract import Program, Seat  # noqa: E402

PAD = ' ' * 9
REFILL = words.when(NOW + 2 * 86400, NOW)
ADDED = words.date(NOW - 86400, NOW)
CLAUDE = 'Claude   has room · 5 accounts, 2 under their lines · in use: maya1@northwind.example'
CODEX = 'Codex    has room · ChatGPT Pro · weekly 42%%, refills %s · read 12 min ago from its own files' % REFILL
CAN = PAD + 'added %s · Switchboard can: read its numbers, carry a chat on in it, hand it a job' % ADDED


class Board(KitCase, unittest.TestCase):
    def test_the_usual_board(self):
        r = self.rig()
        self.assertEqual(r('providers'), 0)
        self.assertEqual(r.lines(), [
            CLAUDE,
            PAD + 'Claude Code 2.1.330 (your PATH)',
            CODEX,
            PAD + 'Codex 0.169.1 (ChatGPT.app)',
            CAN,
            '',
            'This folder (site): chats here have not been offered to Codex yet.',
            'Order: Claude, then Codex.'])
        self.assertNotIn('limits(ask)', r.codex.calls)          # no ask without --ask

    def test_the_path_copy_older_than_the_chats(self):
        app = program(self.root, 'codex', '0.169.1', where='ChatGPT.app', source='app')
        on_path = Program(os.path.join(self.root, 'bin', 'codex'), '0.155.1', 'path', 'your PATH')
        found = replace(codex_found(self.root), program=app, programs=(app, on_path))
        r = self.rig(codex_found_=found)
        self.assertEqual(r('providers'), 0)
        self.assertEqual(r.lines()[3:7], [
            PAD + 'Codex 0.169.1 (ChatGPT.app; the newest of 2 here; codex on your PATH is 0.155.1)',
            PAD + 'Typing codex here runs 0.155.1, which is older than the Codex that wrote your chats (0.169.1).',
            PAD + 'The one Switchboard uses is %s.' % app.path,
            CAN])

    def test_the_path_copy_is_called_by_the_word_a_person_types(self):
        # The program is kept by the path of the file itself (a link on PATH leads to codex.js); the board still
        # names the word a person types.
        app = program(self.root, 'codex', '0.169.1', where='ChatGPT.app', source='app')
        on_path = Program(os.path.join(self.root, 'lib', 'codex.js'), '0.155.1', 'path', 'your PATH')
        found = replace(codex_found(self.root), program=app, programs=(app, on_path))
        r = self.rig(codex_found_=found)
        self.assertEqual(r('providers'), 0)
        self.assertEqual(r.lines()[3:5], [
            PAD + 'Codex 0.169.1 (ChatGPT.app; the newest of 2 here; codex on your PATH is 0.155.1)',
            PAD + 'Typing codex here runs 0.155.1, which is older than the Codex that wrote your chats (0.169.1).'])

    def test_a_path_copy_that_is_not_older_says_only_its_version(self):
        app = program(self.root, 'codex', '0.169.1', where='ChatGPT.app', source='app')
        on_path = Program(os.path.join(self.root, 'bin', 'codex'), '0.169.1', 'path', 'your PATH')
        r = self.rig(codex_found_=replace(codex_found(self.root), program=app, programs=(app, on_path)))
        r('providers')
        self.assertEqual(r.lines()[3:5], [
            PAD + 'Codex 0.169.1 (ChatGPT.app; the newest of 2 here; codex on your PATH is 0.169.1)', CAN])

    def test_a_refused_copy_is_listed_and_never_chosen(self):
        app = program(self.root, 'codex', '0.169.1', where='ChatGPT.app', source='app')
        plugin = Program(os.path.join(self.root, 'plugin', 'codex'), '0.167.0-alpha.4.1', 'plugin',
                         "Codex's own plugin folder", refused='a copy the app keeps for itself')
        r = self.rig(codex_found_=replace(codex_found(self.root), program=app, programs=(app, plugin)))
        r('providers')
        self.assertEqual(r.lines()[3:6], [
            PAD + 'Codex 0.169.1 (ChatGPT.app)',
            PAD + "Codex's own plugin folder 0.167.0-alpha.4.1: not used, a copy the app keeps for itself",
            CAN])

    def test_the_state_words(self):
        cases = (
            (dict(read_ago=7200.0), 'Codex    had room when last read 2 hours ago · ChatGPT Pro · weekly 42%%, '
                                    'refills %s · read 2 hours ago from its own files' % REFILL),
            (dict(pct=96.0), 'Codex    is out until %s · ChatGPT Pro · weekly 96%%, refills %s · read 12 min ago '
                             'from its own files' % (REFILL, REFILL)),
            (dict(read_ago=9 * 86400.0), 'Codex    numbers not known · ChatGPT Pro · weekly 42%%, refills %s · '
                                         'read 9 days ago from its own files' % REFILL),
            (dict(source='ask', read_ago=60.0), 'Codex    has room · ChatGPT Pro · weekly 42%%, refills %s · asked '
                                                '1 min ago' % REFILL),
        )
        for kw, line in cases:
            with self.subTest(kw=kw):
                r = self.rig()
                r.codex.limits_ = codex_limits(**kw)
                self.assertEqual(r('providers'), 0)
                self.assertEqual(r.lines()[2], line)

    def test_no_numbers_at_all(self):
        r = self.rig()
        r.codex.limits_ = ()
        r('providers')
        self.assertEqual(r.lines()[2], 'Codex    numbers not known · ChatGPT Pro · never read')

    def test_every_claude_account_out(self):
        r = self.rig()
        r.claude.limits_ = claude_limits(under=0)
        r('providers')
        self.assertEqual(r.lines()[0], 'Claude   every account is out until %s · 5 accounts, 0 under their lines · '
                                       'in use: maya1@northwind.example' % words.when(NOW + 3600, NOW))

    def test_not_signed_in(self):
        r = self.rig(codex_found_=codex_found(self.root, signed_in=False))
        r('providers')
        self.assertEqual(r.lines()[2], 'Codex    not signed in')

    def test_not_added_not_here_and_not_loaded(self):
        r = self.rig(codex_added=False)
        self.assertEqual(r('providers'), 0)
        self.assertEqual(r.lines()[2:], ['Codex    not added · Codex 0.169.1 is here. switchboard add codex adds it.',
                                         '', 'This folder (site): chats here have not been offered to another '
                                         'provider yet.', 'Order: Claude.'])
        r = self.rig(codex_added=False)
        r.codex.found_ = None
        r('providers')
        self.assertEqual(r.lines()[2], 'Codex    not on this Mac')
        r = self.rig(codex=False)
        r.load_errors['codex'] = 'SyntaxError'
        r('providers')
        self.assertEqual(r.lines()[2], 'Codex    its code did not load here (SyntaxError).')

    def test_the_folder_lines(self):
        r = self.rig()                  # choices.consent_at says when the yes was given: its date shows
        r.choices.set_consent(r.folder, 'codex', 'yes', NOW - 86400)
        r('providers')
        self.assertEqual(r.lines()[-2], 'This folder (site): chats may go to Codex (you said yes on %s).' % ADDED)
        r = self.rig()                  # a yes kept with no time
        r.choices.set_consent(r.folder, 'codex', 'yes', NOW - 86400)
        r.choices.folders[r.folder]['consent']['codex'].pop('at')
        r('providers')
        self.assertEqual(r.lines()[-2], 'This folder (site): chats may go to Codex (you said yes).')
        r = self.rig()
        r.choices.set_consent(r.folder, 'codex', 'never', NOW)
        r('providers')
        self.assertEqual(r.lines()[-2], 'This folder (site): chats here never go to Codex (switchboard allow codex '
                                        'lifts that).')
        r = self.rig()
        r.choices.set_stay(r.folder, 'claude', NOW)
        r('providers')
        self.assertEqual(r.lines()[-2], 'This folder (site): this folder stays on Claude (switchboard stay off lifts '
                                        'that).')

    def test_the_order_comes_from_the_person(self):
        r = self.rig(order=['codex', 'claude'])
        r('providers')
        self.assertTrue(r.lines()[0].startswith('Codex    has room'))
        self.assertEqual(r.lines()[-1], 'Order: Codex, then Claude.')

    def test_wrong_words(self):
        r = self.rig()
        self.assertEqual(r('providers', '--all'), 2)
        self.assertEqual(r.text(), words.HELP['providers'] + '\n')

    def test_outside_values_never_print_an_escape(self):
        r = self.rig()
        seats = list(claude_seats())
        seats[0] = Seat('claude', 'acct-01', 'maya\x1b]0;owned\x07\x1b[2J@northwind.example', in_use=True)
        r.claude.seats_ = tuple(seats)
        r.codex.found_ = replace(r.codex.found_, program=replace(r.codex.found_.program,
                                                                 where='Chat\x1b[31mGPT.app\x07'))
        r('providers')
        self.assertNotIn('\x1b', r.text())
        self.assertNotIn('\x07', r.text())
        self.assertIn('in use: maya@northwind.example', r.text())          # the title sequence goes whole
        self.assertIn('Codex 0.169.1 (ChatGPT.app)', r.text())


class Ask(KitCase, unittest.TestCase):
    OFF = ('Codex was not asked: starting its own helper needs you at a terminal, and your yes the first time '
           '(switchboard providers --ask). These are its saved numbers.')
    QUESTION = ("This starts Codex's own helper once, in your Codex folder, to read its numbers. Codex may renew its "
                "sign-in while it runs. Go on? [y/N] ")

    def test_not_at_a_terminal_without_the_yes_nothing_is_started(self):
        r = self.rig()
        self.assertEqual(r('providers', '--ask'), 0)
        self.assertEqual(r.lines()[0], self.OFF)
        self.assertEqual(r.lines()[1], CLAUDE)
        self.assertEqual(r.asked, [])
        self.assertNotIn('limits(ask)', r.codex.calls)
        self.assertFalse(r.choices.ask_ok('codex'))

    def test_at_a_terminal_the_yes_is_asked_once(self):
        r = self.rig(tty=True, answers=['y'])
        r.codex.ask_limits = codex_limits(pct=50.0, read_ago=0.0, source='ask', ask=contract.ASK_DONE)
        self.assertEqual(r('providers', '--ask'), 0)
        self.assertEqual(r.asked, [self.QUESTION])
        self.assertTrue(r.choices.ask_ok('codex'))
        self.assertEqual(r.choices._ask_ok, {'codex': NOW})          # the yes is kept for that provider
        self.assertEqual(r.lines()[0], 'Asked Codex for its numbers.')
        self.assertIn('weekly 50%', r.lines()[3])
        self.assertIn('asked just now', r.lines()[3])
        self.assertEqual(r('providers', '--ask'), 0)          # the second time: no question
        self.assertEqual(r.asked, [self.QUESTION])

    def test_at_a_terminal_no_is_the_saved_numbers(self):
        r = self.rig(tty=True, answers=['n'])
        self.assertEqual(r('providers', '--ask'), 0)
        self.assertEqual(r.lines()[0], self.OFF)
        self.assertNotIn('limits(ask)', r.codex.calls)
        self.assertFalse(r.choices.ask_ok('codex'))

    def test_each_outcome(self):
        cases = ((contract.ASK_BUSY, '', 'Codex is being asked by another Switchboard command now.'),
                 (contract.ASK_REFUSED, 'server overloaded',
                  'Codex did not answer for its numbers (server overloaded). These are its saved numbers.'),
                 (contract.ASK_API_KEY, '', 'Codex is on an API key here, not a plan: its plan limits do not apply.'),
                 (contract.ASK_PACED, '', 'Codex was asked a short while ago. These are its saved numbers.'))
        for outcome, why, line in cases:
            with self.subTest(outcome=outcome):
                r = self.rig(tty=True)
                r.choices.set_ask_ok(NOW - 86400, 'codex')
                r.codex.ask_limits = codex_limits(ask=outcome, error=why or None)
                self.assertEqual(r('providers', '--ask'), 0)
                self.assertEqual(r.lines()[0], line)
                problems = [s for s in r.said if s[1] == 'problem']
                if outcome == contract.ASK_REFUSED:
                    self.assertEqual(problems, [('Codex did not answer for its numbers: server overloaded',
                                                 'problem', {'reason': 'ask refused'})])
                else:
                    self.assertEqual(problems, [])

    def test_not_at_a_terminal_nothing_is_started_even_after_the_yes(self):
        r = self.rig()
        r.choices.set_ask_ok(NOW - 86400, 'codex')
        r.codex.ask_limits = codex_limits(pct=50.0, read_ago=0.0, source='ask', ask=contract.ASK_DONE)
        self.assertEqual(r('providers', '--ask'), 0)
        self.assertNotIn('limits(ask)', r.codex.calls)
        self.assertEqual((r.lines()[0], r.asked), (self.OFF, []))
        r('providers', '--ask', '--json')
        self.assertNotIn('limits(ask)', r.codex.calls)
        self.assertEqual(json.loads(r.lines()[-1])['ask'], {'outcome': contract.ASK_OFF, 'why': ''})

    def test_paced_says_when_from_the_asks_own_file(self):
        r = self.rig(tty=True)
        r.choices.set_ask_ok(NOW - 86400, 'codex')
        path = os.path.join(r.reach.data_dir, contract.ASK_FILE)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.addCleanup(os.remove, path)
        with open(path, 'w') as f:
            json.dump({'codex': {'at': NOW - 120, 'ok': True, 'backoff_until': NOW + 600}}, f)
        r.codex.ask_limits = codex_limits(ask=contract.ASK_PACED)
        r('providers', '--ask')
        self.assertEqual(r.lines()[0], 'Codex was asked 2 min ago; the next ask can be at %s.'
                         % words.when(NOW + 600, NOW))


class Json(KitCase, unittest.TestCase):
    def test_the_shape(self):
        r = self.rig()
        r.choices.set_consent(r.folder, 'codex', 'yes', NOW)
        self.assertEqual(r('providers', '--json'), 0)
        data = json.loads(r.text())
        self.assertEqual(sorted(data), ['ask', 'folder', 'order', 'providers', 'v'])
        self.assertEqual(data['v'], 1)
        self.assertEqual(data['order'], ['claude', 'codex'])
        self.assertEqual(data['folder'], {'path': r.folder, 'stays': None, 'consent': {'codex': 'yes'}})
        self.assertIsNone(data['ask'])
        claude, codex = data['providers']
        for p in (claude, codex):
            self.assertTrue({'id', 'name', 'vendor', 'added', 'state', 'why', 'back_at', 'program', 'programs',
                             'signed_in', 'newest_seen', 'seats', 'limits', 'can'} <= set(p), p)
        self.assertEqual((codex['id'], codex['name'], codex['vendor'], codex['state'], codex['why']),
                         ('codex', 'Codex', 'OpenAI', 'room', 'has room'))
        self.assertEqual(codex['program']['version'], '0.169.1')
        self.assertEqual(codex['limits'][0]['windows'][0]['used_pct'], 42.0)
        self.assertEqual(codex['added'], NOW - 86400)
        self.assertIn('job', codex['can'])
        self.assertEqual(len(claude['seats']), 5)

    def test_with_an_ask(self):
        r = self.rig()
        self.assertEqual(r('providers', '--ask', '--json'), 0)
        data = json.loads(r.text())
        self.assertEqual(data['ask'], {'outcome': 'off', 'why': ''})

    def test_an_escape_is_written_as_json_escapes(self):
        r = self.rig()
        r.claude.seats_ = (Seat('claude', 'acct-01', 'x\x1b[2Jy@northwind.example', in_use=True),)
        r('providers', '--json')
        self.assertNotIn('\x1b', r.text())
        self.assertIn('\\u001b', r.text())


if __name__ == '__main__':
    unittest.main()
