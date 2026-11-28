import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The one list: each added provider besides Claude is a row of the list `switchboard` prints, after the Claude
# accounts, with the same keys: its number, the account's email when the app told it (else the app's name), its maker
# and plan in the plan column ("OpenAI · Plus"), its limits in the same two-bar form, and the marks "in use" and
# "next" worked out across providers by the picking code (place.room_of, job.choose).
#
# With no other provider added the list is byte for byte what it was. The rows are built from files only: no program
# runs. Made-up accounts and numbers only; the providers' adapters are the stand-ins of tests/fakes_provider_kit.py.
import copy
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from fakes_provider_kit import NOW as KIT_NOW, KitCase, codex_found, codex_limits  # noqa: E402
from switchboard import __main__ as cli  # noqa: E402
from switchboard import comeup, connect, core, server, term  # noqa: E402
from switchboard.providers import contract, rows  # noqa: E402
from switchboard.providers.contract import Hop  # noqa: E402
from test_term import NOW, PLAIN, TZ, VIEW  # noqa: E402

EMAIL = 'ana@northwind.example'         # the same person's OpenAI account (21 characters, as wide as the widest row)
FIVE_RESETS = datetime(2026, 11, 13, 19, 15, tzinfo=TZ).timestamp()        # 7:15 PM the same Friday
WEEK_RESETS = datetime(2026, 11, 19, 9, 0, tzinfo=TZ).timestamp()          # Thu 9:00 AM


def codex_row(**more):
    """One provider row, exactly the shape the state's 'provider_rows' holds (providers.rows.from_board)."""
    return dict({'provider': 'codex', 'name': 'Codex', 'vendor': 'OpenAI', 'email': EMAIL, 'plan': 'Plus',
                 'windows': [{'label': '5-hour', 'used_pct': 8.0, 'resets_at': FIVE_RESETS, 'line': 90.0},
                             {'label': 'weekly', 'used_pct': 21.0, 'resets_at': WEEK_RESETS, 'line': 95.0}],
                 'read_at': NOW - 720, 'state': 'room', 'back_at': None, 'fresh': True, 'why': None, 'can': True,
                 'app': True, 'in_use': False, 'next': False, 'chat': None}, **more)


def lines(view, width=None, colour=False):
    return term.view(term.read(view, tz=TZ), width=width, colour=colour)


ROW_6 = """

6  ana@northwind.example  OpenAI · Plus
   5-hour  ━━──────────────────────    8%  resets 7:15 PM · in 4 hours
   weekly  ━━━━━───────────────────   21%  resets Thu 9:00 AM · in 6 days"""


class TheList(unittest.TestCase):
    def test_with_no_other_provider_the_list_is_byte_for_byte_what_it_was(self):
        self.assertEqual('\n'.join(lines(VIEW)), PLAIN)
        self.assertEqual('\n'.join(lines(dict(VIEW, provider_rows=[]))), PLAIN)
        self.assertEqual(lines(dict(VIEW, provider_rows=[]), colour=True), lines(VIEW, colour=True))
        self.assertEqual(lines(dict(VIEW, provider_rows=[]), width=60), lines(VIEW, width=60))
        self.assertEqual(term.read(dict(VIEW, provider_rows=[]), tz=TZ), term.read(VIEW, tz=TZ))

    def test_the_other_provider_comes_after_the_claude_accounts_in_the_same_form(self):
        self.assertEqual('\n'.join(lines(dict(VIEW, provider_rows=[codex_row()]))), PLAIN + ROW_6)
        got = term.read(dict(VIEW, provider_rows=[codex_row()]), tz=TZ)['accounts']
        self.assertEqual([a['slot'] for a in got], [1, 2, 3, 4, 5, 6])
        self.assertEqual(got[-1]['provider'], 'codex')
        self.assertFalse(any('provider' in a for a in got[:-1]))        # a Claude row gains nothing
        self.assertEqual(term.prompt([a['slot'] for a in got]), 'Switch to 1–6, or Enter to stay › ')

    def test_two_providers_follow_in_the_order_given(self):
        other = codex_row(provider='later', name='Later', vendor='Made-up', email=None, plan='', windows=[])
        got = term.read(dict(VIEW, provider_rows=[codex_row(), other]), tz=TZ)['accounts']
        self.assertEqual([(a['slot'], a['email'], a['plan']) for a in got[5:]],
                         [(6, EMAIL, 'OpenAI · Plus'), (7, 'Later', 'Made-up')])

    def test_next_across_providers_is_one_row(self):
        view = dict(VIEW, provider_rows=[codex_row(next=True)])
        got = term.read(view, tz=TZ)['accounts']
        self.assertEqual([a['slot'] for a in got if a['next']], [6])          # the Claude row's own mark gives way
        self.assertIn('6  ana@northwind.example  OpenAI · Plus       next', lines(view))
        coloured = lines(view, colour=True)
        self.assertIn('6  \x1b[1mana@northwind.example\x1b[0m  \x1b[2mOpenAI · Plus\x1b[0m       \x1b[2mnext\x1b[0m',
                      coloured)
        # not marked next: the Claude row keeps the server's own mark
        self.assertEqual([a['slot'] for a in term.read(dict(VIEW, provider_rows=[codex_row()]), tz=TZ)['accounts']
                          if a['next']], [4])

    def test_in_use(self):
        got = lines(dict(VIEW, provider_rows=[codex_row(in_use=True, next=True)]))
        self.assertIn('6  ana@northwind.example  OpenAI · Plus       in use', got)

    def test_a_row_the_app_has_not_named_is_named_by_the_app(self):
        got = lines(dict(VIEW, provider_rows=[codex_row(email=None, plan='')]))
        self.assertIn('6  Codex                  OpenAI', got)

    def test_only_old_room_says_how_old_the_numbers_are(self):
        # The list says nothing of how old a row's numbers are (12 minutes above), with one exception: another
        # provider's row with room by numbers read over an hour ago (providers.cli.WORDS_FRESH_S) says so, in the
        # board's own words, since old room is not room. A row that is out, or an hour old, says nothing.
        old = codex_row(read_at=NOW - 2 * 3600 - 60, fresh=False)
        self.assertIn('6  ana@northwind.example  OpenAI · Plus       had room when last read 2 hours ago',
                      lines(dict(VIEW, provider_rows=[old])))
        self.assertEqual(term.read(dict(VIEW, provider_rows=[old]), tz=TZ)['accounts'][-1]['numbers'],
                         'had room when last read 2 hours ago')
        coloured = lines(dict(VIEW, provider_rows=[dict(old, in_use=True)]), colour=True)
        self.assertIn('6  \x1b[1mana@northwind.example\x1b[0m  \x1b[2mOpenAI · Plus\x1b[0m       \x1b[94min use\x1b[0m'
                      '\x1b[2m · \x1b[0m\x1b[2mhad room when last read 2 hours ago\x1b[0m', coloured)
        for row in (codex_row(read_at=NOW - 2 * 3600, fresh=False, state='out', back_at=WEEK_RESETS),
                    codex_row(read_at=NOW - 3600), codex_row(read_at=NOW + 600)):
            with self.subTest(state=row['state'], read_at=row['read_at'] - NOW):
                self.assertIn('6  ana@northwind.example  OpenAI · Plus', lines(dict(VIEW, provider_rows=[row])))

    def test_numbers_not_known_a_refilled_window_and_a_line(self):
        # A row with no seat yet (signed in, numbers never read): its status says so, and it has no bar.
        unknown = lines(dict(VIEW, provider_rows=[codex_row(windows=[], read_at=None, state='unknown')]))
        self.assertEqual(unknown[-2:], ['',
                                        '6  ana@northwind.example  OpenAI · Plus       numbers not known'])
        refilled = codex_row(windows=[{'label': 'weekly', 'used_pct': None, 'resets_at': NOW - 60, 'line': 95.0}])
        self.assertEqual(lines(dict(VIEW, provider_rows=[refilled]))[-1],
                         '   weekly  ────────────────────────    0%  not started')
        at_line = codex_row(windows=[{'label': 'weekly', 'used_pct': 95.0, 'resets_at': WEEK_RESETS, 'line': 95.0},
                                     {'label': '3-day', 'used_pct': 40.0, 'resets_at': None, 'line': None}])
        got = term.read(dict(VIEW, provider_rows=[at_line]), tz=TZ)['accounts'][-1]['limits']
        self.assertEqual([(lim['label'], lim['pct'], lim['over'], lim['words']) for lim in got],
                         [('5-hour', None, False, 'no 5-hour limit'),        # no 5-hour window: said in words
                          ('weekly', 95, True, 'resets Thu 9:00 AM · in 6 days'), ('3-day', 40, False, '')])
        self.assertIn('   weekly  \x1b[91m' + '━' * 23 + '\x1b[0m\x1b[2m─\x1b[0m   \x1b[91m95%\x1b[0m  '
                      '\x1b[2mresets Thu 9:00 AM · in 6 days\x1b[0m',
                      lines(dict(VIEW, provider_rows=[at_line]), colour=True))

    def test_numbers_not_known_says_the_step_when_it_fits_the_line(self):
        # At a terminal wide enough, the row's status names the command that reads the numbers; never on a second
        # line, and never in output that goes to a file or a script.
        view = dict(VIEW, provider_rows=[codex_row(windows=[], read_at=None, state='unknown')])
        wide = ('6  ana@northwind.example  OpenAI · Plus       numbers not known · switchboard providers --ask reads '
                'them')
        self.assertEqual(lines(view, width=len(wide))[-1], wide)
        for width in (len(wide) - 1, 80, None):
            with self.subTest(width=width):
                self.assertEqual(lines(view, width=width)[-2:],
                                 ['', '6  ana@northwind.example  OpenAI · Plus       numbers not known'])
        self.assertNotIn('--ask', '\n'.join(lines(dict(VIEW, provider_rows=[codex_row()]), width=200)))

    def test_a_window_with_no_number(self):
        # As on the account rows: no 5-hour line without a 5-hour number; any other window says it is not known.
        row = codex_row(windows=[{'label': '5-hour', 'used_pct': None, 'resets_at': FIVE_RESETS, 'line': 90.0},
                                 {'label': 'weekly', 'used_pct': 21.0, 'resets_at': WEEK_RESETS, 'line': 95.0},
                                 {'label': '3-day', 'used_pct': None, 'resets_at': None, 'line': None}])
        self.assertEqual(lines(dict(VIEW, provider_rows=[row]))[-3:],
                         ['6  ana@northwind.example  OpenAI · Plus',
                          '   weekly  ━━━━━───────────────────   21%  resets Thu 9:00 AM · in 6 days',
                          '   3-day   ────────────────────────     —  not known'])
        only_five = codex_row(windows=[{'label': '5-hour', 'used_pct': None, 'resets_at': None, 'line': 90.0}])
        self.assertEqual(lines(dict(VIEW, provider_rows=[only_five]))[-2:],
                         ['', '6  ana@northwind.example  OpenAI · Plus       numbers not known'])

    def test_a_5_hour_window_with_no_number_has_no_line_also_once_its_reset_has_passed(self):
        row = codex_row(windows=[{'label': '5-hour', 'used_pct': None, 'resets_at': NOW - 60, 'line': 90.0},
                                 {'label': 'weekly', 'used_pct': 21.0, 'resets_at': WEEK_RESETS, 'line': 95.0}])
        self.assertEqual(lines(dict(VIEW, provider_rows=[row]))[-2:],
                         ['6  ana@northwind.example  OpenAI · Plus',
                          '   weekly  ━━━━━───────────────────   21%  resets Thu 9:00 AM · in 6 days'])

    def test_two_seats_of_one_provider_are_two_numbers(self):
        second = codex_row(email='ana@harbor.example', plan='Team',
                           windows=[{'label': 'weekly', 'used_pct': 64.0, 'resets_at': WEEK_RESETS, 'line': 95.0}])
        view = dict(VIEW, provider_rows=[codex_row(), second])
        got = term.read(view, tz=TZ)['accounts']
        self.assertEqual([(a['slot'], a.get('provider'), a['email']) for a in got[5:]],
                         [(6, 'codex', EMAIL), (7, 'codex', 'ana@harbor.example')])
        self.assertEqual('\n'.join(lines(view)), PLAIN + ROW_6 + """

7  ana@harbor.example     OpenAI · Team
   no 5-hour limit
   weekly  ━━━━━━━━━━━━━━━─────────   64%  resets Thu 9:00 AM · in 6 days""")
        self.assertEqual(term.prompt([a['slot'] for a in got]), 'Switch to 1–7, or Enter to stay › ')

    def test_text_from_outside_is_made_plain(self):
        got = term.read(dict(VIEW, provider_rows=[codex_row(email='x\x1b[31m@made-up.example', plan='Pl\nus')]),
                        tz=TZ)['accounts'][-1]
        self.assertNotIn('\x1b', got['email'] + got['plan'])
        self.assertNotIn('\n', got['plan'])


# ---------- the rows, from the providers' own files ----------

class TheRows(KitCase, unittest.TestCase):
    def fit(self, **more):
        """The stand-ins with a Codex that says it is signed in: one that can be continued to, so it has a row."""
        return self.rig(codex_found_=codex_found(self.root, signed_in=True), **more)

    def test_none_added_none_shown(self):
        r = self.rig(codex_added=False)
        self.assertEqual(rows.rows(r.kit), [])
        self.assertEqual(rows.rows(self.rig(codex=False).kit), [])

    def test_one_row_after_claude(self):
        r = self.fit()
        r.codex.account_email = lambda: EMAIL
        self.assertEqual(rows.rows(r.kit), [{
            'provider': 'codex', 'name': 'Codex', 'vendor': 'OpenAI', 'email': EMAIL, 'plan': 'Pro',
            'windows': [{'label': 'weekly', 'used_pct': 42.0, 'resets_at': KIT_NOW + 2 * 86400.0, 'line': 95.0}],
            'read_at': KIT_NOW - 720.0, 'state': contract.ROOM, 'back_at': None, 'fresh': True, 'why': None,
            'can': True, 'app': True, 'in_use': False, 'next': False, 'chat': None}])

    def test_one_that_cannot_be_continued_to_has_no_row(self):
        # The stand-in's own Codex says no sign-in: it has no row; the line under the list says why.
        self.assertEqual(rows.rows(self.rig().kit), [])

    def test_no_email_until_the_app_says_one(self):
        r = self.fit()
        self.assertIsNone(rows.rows(r.kit)[0]['email'])
        r.codex.account_email = lambda: 1 / 0               # an adapter's own failure: the app's name instead
        self.assertIsNone(rows.rows(r.kit)[0]['email'])

    def test_files_only(self):
        # The rows come from the board, which reads files only: each adapter is asked through a reach that starts
        # nothing, so no program runs, nothing is opened, and nothing is asked.
        r = self.fit()
        self.assertTrue(rows.rows(r.kit))
        self.assertEqual((r.calls.runs, r.calls.execs, r.calls.links, r.calls.pipes), ([], [], [], []))
        self.assertEqual(r.asked, [])

    def test_next_is_decided_by_the_one_function_for_every_face(self):
        # The rows never mark it; comeup.with_next does, from the whole state: Codex is next when every usable Claude
        # account is at its lines and Codex has room by numbers of the last hour and can be continued to.
        r = self.rig(codex_found_=codex_found(self.root, signed_in=True))
        self.assertFalse(rows.rows(r.kit)[0]['next'])
        self.assertTrue(next_of(at_lines_view(rows.rows(r.kit))))
        self.assertFalse(next_of(dict(at_lines_view(rows.rows(r.kit)), accounts=VIEW['accounts'])))   # Claude has room
        r.codex.limits_ = codex_limits(pct=97.0)                            # Codex out too: nobody is next
        self.assertFalse(next_of(at_lines_view(rows.rows(r.kit))))
        r.codex.limits_ = codex_limits(read_ago=2 * 3600.0)                 # Codex's numbers two hours old
        self.assertFalse(next_of(at_lines_view(rows.rows(r.kit))))

    def test_this_folders_stay_and_never_are_kept(self):
        r = self.rig(codex_found_=codex_found(self.root, signed_in=True))
        r.choices.set_consent(r.folder, 'codex', 'never', KIT_NOW)
        self.assertFalse(next_of(at_lines_view(rows.rows(r.kit))))
        r.choices.set_consent(r.folder, 'codex', 'yes', KIT_NOW)
        self.assertTrue(next_of(at_lines_view(rows.rows(r.kit))))
        r.choices.set_stay(r.folder, 'claude', KIT_NOW)
        self.assertFalse(next_of(at_lines_view(rows.rows(r.kit))))

    def test_in_use_while_this_folders_newest_move_is_there(self):
        r = self.fit()
        hop = Hop(id='h-0000000a', task='t-0000000a', kind='continue', at=KIT_NOW - 60, folder=r.folder,
                  from_provider='claude', from_chat=None, to_provider='codex', state='started')
        r.tasks.add_hop(hop)
        self.assertTrue(rows.rows(r.kit)[0]['in_use'])
        r.tasks.update_hop(hop.id, state='returned')
        self.assertFalse(rows.rows(r.kit)[0]['in_use'])

    def test_in_use_only_when_the_move_started_the_app(self):
        # A line to run that was printed, or a note handed to the app's message box, started nothing yet.
        for state, in_use in (('printed', False), ('in_app', False), ('made', False), ('failed', False),
                              ('started', True)):
            with self.subTest(state=state):
                r = self.fit()
                r.tasks.add_hop(Hop(id='h-0000000b', task='t-0000000b', kind='continue', at=KIT_NOW - 60,
                                    folder=r.folder, from_provider='claude', from_chat=None, to_provider='codex',
                                    state=state))
                self.assertIs(rows.rows(r.kit)[0]['in_use'], in_use)

    def test_plan_words(self):
        self.assertEqual([rows.plan_word(p) for p in ('plus', 'pro', 'prolite', 'free', 'team', 'ent26', None, 3)],
                         ['Plus', 'Pro', 'Pro Lite', 'Free', 'Team', '', '', ''])

    def test_a_failure_leaves_the_rows_out(self):
        r = self.rig()
        r.choices.order = lambda: 1 / 0
        self.assertEqual(rows.rows(r.kit), [])


def at_lines_view(provider_rows):
    """test_comeup's state (both Claude accounts at their lines) with these rows."""
    from test_comeup import state
    return state(provider_rows=list(provider_rows))


def next_of(view):
    """Is the provider row next, and no Claude account, by the one function."""
    got = comeup.with_next(view)
    return got['provider_rows'][0]['next'] and got['next'] is None


# ---------- the terminal, the server's state and the demo ----------

PAGE_BOARD = {'v': 1, 'now': NOW, 'order': ['claude', 'codex'], 'providers': [], 'add': [], 'carry': False}

class TheTerminal(unittest.TestCase):
    def setUp(self):
        for target, name, value in ((cli, 'identity', lambda: None), (cli, 'width', lambda: None),
                                    (connect, 'claude_bin', lambda: '/made/up/claude')):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)

    def rows_from(self, rows, seen):
        """A stand-in for cli.provider_rows: notes the board it was given, gives `rows`."""
        return lambda board: seen.append(board) or list(rows)

    def test_the_view_gains_the_provider_rows(self):
        seen = []
        with mock.patch.object(cli, 'page_state', lambda: dict(copy.deepcopy(VIEW), providers=PAGE_BOARD)), \
                mock.patch.object(cli, 'provider_board', lambda: self.fail('the page sent a board')), \
                mock.patch.object(cli, 'provider_rows', self.rows_from([codex_row()], seen)):
            got = cli.current_view()
        self.assertEqual(got['provider_rows'], [codex_row()])
        self.assertEqual(got['providers'], PAGE_BOARD)                  # the page's board, untouched
        self.assertEqual(seen, [PAGE_BOARD])                            # this folder's rows come from it
        with mock.patch.object(cli, 'page_state', lambda: dict(copy.deepcopy(VIEW), providers=PAGE_BOARD)), \
                mock.patch.object(cli, 'provider_rows', self.rows_from([], [])):
            # none added: the page's own state, as it was
            self.assertEqual(cli.current_view(), dict(VIEW, providers=PAGE_BOARD))
        with mock.patch.object(cli, 'page_state', lambda: dict(copy.deepcopy(VIEW), providers=PAGE_BOARD,
                                                              provider_rows=[codex_row()])), \
                mock.patch.object(cli, 'provider_rows', self.rows_from([], [])):
            self.assertEqual(cli.current_view()['provider_rows'], [])   # this folder's own rows win

    def test_with_no_page_the_board_is_read_here(self):
        # The same list with and without the page: with none (or a page that sent no board), the board is read
        # here, from files only, and the rows come from it.
        seen = []
        here = {'v': 1, 'providers': [], 'add': [{'id': 'codex', 'name': 'Codex', 'app': 'Codex', 'version': None}]}
        with mock.patch.object(cli, 'page_state', lambda: None), \
                mock.patch.object(cli, 'local_view', lambda: copy.deepcopy(VIEW)), \
                mock.patch.object(cli, 'provider_board', lambda: here), \
                mock.patch.object(cli, 'provider_rows', self.rows_from([], seen)):
            got = cli.current_view()
        self.assertEqual((got['providers'], seen), (here, [here]))
        with mock.patch.object(cli, 'page_state', lambda: copy.deepcopy(VIEW)), \
                mock.patch.object(cli, 'provider_board', lambda: here), \
                mock.patch.object(cli, 'provider_rows', self.rows_from([codex_row()], [])):
            got = cli.current_view()
        self.assertEqual((got['providers'], got['provider_rows']), (here, [codex_row()]))

    def test_the_next_account_is_a_claude_account_never_another_providers_row(self):
        # switchboard switch alone and the first run's "See it move" move Claude Code to the next Claude account:
        # another provider's row in the list (it has no rule of auto-switch) is never that account, whatever it says.
        plain = cli.next_account(copy.deepcopy(VIEW))
        self.assertIsNotNone(plain[0])
        for row in (codex_row(), codex_row(next=True), codex_row(state='out', can=False), codex_row(in_use=True)):
            with self.subTest(row=row):
                self.assertEqual(cli.next_account(dict(copy.deepcopy(VIEW), provider_rows=[row])), plain)

    def test_a_slow_board_leaves_the_other_rows_out_of_the_list(self):
        # With no page, the list and status wait for the board read here at most BOARD_WAIT seconds; past that they
        # print the Claude accounts and one dim line says the other assistants were not read in time.
        import threading
        import time
        gate = threading.Event()
        self.addCleanup(gate.set)

        def slow():
            gate.wait(10)
            return PAGE_BOARD
        self.assertTrue(1 <= cli.BOARD_WAIT <= 10)
        with mock.patch.object(cli, 'page_state', lambda: None), \
                mock.patch.object(cli, 'local_view', lambda: copy.deepcopy(VIEW)), \
                mock.patch.object(cli, 'provider_board', slow):
            began = time.monotonic()
            got = cli.current_view(wait=0.2)
            self.assertLess(time.monotonic() - began, 5)
        self.assertEqual((got['providers'], got['provider_rows'] if 'provider_rows' in got else []), (None, []))
        out = io.StringIO()
        with redirect_stdout(out):
            cli.show(got)
        self.assertTrue(out.getvalue().endswith('\n\n  Other assistants  not read in time\n'), out.getvalue())
        out = io.StringIO()
        with redirect_stdout(out):
            cli.show(VIEW)
        self.assertNotIn('not read in time', out.getvalue())

    def test_the_list_and_status_wait_for_the_board_at_most_a_few_seconds(self):
        seen = []
        with mock.patch.object(cli, 'keep_now', lambda: False), mock.patch.object(cli, 'terminal', lambda: False), \
                mock.patch.object(cli, 'show', lambda view, **k: 0), \
                mock.patch.object(cli.newer, 'line', lambda: None), \
                mock.patch.object(cli, 'current_view', lambda fresh=False, wait=None: seen.append(wait) or {}):
            cli.home()
            cli.status()
        self.assertEqual(seen, [cli.BOARD_WAIT, cli.BOARD_WAIT])

    def test_the_list_marks_the_one_next_and_this_folders_rows_decide_again(self):
        from test_comeup import state
        page = dict(state(provider_rows=[codex_row(fresh=True, can=True, why=None)]), next='ana@ivy.example',
                    providers=PAGE_BOARD)
        page = server_like(page)
        self.assertTrue(page['provider_rows'][0]['next'])                   # the page chose Codex
        with mock.patch.object(cli, 'page_state', lambda: copy.deepcopy(page)), \
                mock.patch.object(cli, 'provider_rows', self.rows_from([codex_row(fresh=True, can=True)], [])):
            got = term.read(cli.current_view(), tz=TZ)['accounts']
        self.assertEqual([a['slot'] for a in got if a['next']], [3])     # Codex's row, after the two accounts
        with mock.patch.object(cli, 'page_state', lambda: copy.deepcopy(page)), \
                mock.patch.object(cli, 'provider_rows', self.rows_from([codex_row(fresh=True, can=False)], [])):
            view = cli.current_view()                                    # this folder says never: Claude again
        self.assertEqual(view['next'], 'ana@ivy.example')
        self.assertFalse(view['provider_rows'][0]['next'])

    def test_one_claude_account_and_codex_is_something_to_switch_to(self):
        one = dict(VIEW, accounts=[VIEW['accounts'][4]], provider_rows=[codex_row()])
        out = io.StringIO()
        with mock.patch.object(cli, 'read_one_key', lambda: '\r'), redirect_stdout(out):
            self.assertEqual(cli.show(one, ask=True), 0)
        self.assertNotIn('Sign in to another account', out.getvalue())
        self.assertTrue(out.getvalue().endswith('\n\nSwitch to 5–6, or Enter to stay › \n'), out.getvalue())


def server_like(view):
    """The page's state as its server leaves it: the one Next decided (server.render ends with comeup.with_next)."""
    return comeup.with_next(view)


class TheServersStateAndTheDemo(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = True

    def test_the_demo_has_one_made_up_openai_row(self):
        from switchboard import demo
        view = demo.view()
        self.assertEqual([(p['provider'], p['vendor'], p['plan']) for p in view['provider_rows']],
                         [('codex', 'OpenAI', 'Plus')])
        got = term.view(term.read(view), width=78)
        self.assertIn('6  maya@northwind.example   OpenAI · Plus', '\n'.join(got))
        self.assertLessEqual(max(len(x) for x in got), 78)

    def test_the_servers_state_carries_the_rows_it_is_given(self):
        from switchboard import demo
        demo.view()
        self.assertEqual(server.render(demo.STATE, dict(demo.FACTS, now=NOW))['provider_rows'], [])
        given = server.render(demo.STATE, dict(demo.FACTS, now=NOW, provider_rows=[codex_row()]))
        self.assertEqual(given['provider_rows'], [codex_row()])


if __name__ == '__main__':
    unittest.main()
