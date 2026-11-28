import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The numbered rows every face draws, derived from the board (providers/view.py), never a second reading of the world.
#
# - One row per seat of each added provider whose seats are not the account list's and that can be continued to as
#   far as files can tell (the board's fit_why is None), in the person's order; one row with no windows, its state
#   'unknown', for such a provider with no seat yet. A provider with a fit_why has no row: one line under the list
#   says why (view.under_list).
# - The folder's facts are file reads added here: a stay on another provider or a never for this one is the row's
#   why (so it cannot be pressed); a move this folder started there and that has not come back is "in use". With no
#   folder they are left out.
# - fresh: room by numbers read within the hour (cli.WORDS_FRESH_S), the one rule.
# - rows.rows(kit) is the same derivation from the board view.view builds, for the folder the kit is in.
#
# Made-up accounts, plans and numbers only; the adapters are the stand-ins of tests/fakes_provider_kit.py.
import copy
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))

from fakes_provider_kit import NOW, KitCase, codex_found, codex_limits  # noqa: E402
from switchboard.providers import cli, contract, rows, view, words  # noqa: E402
from switchboard.providers.contract import Found, Hop, Limits, Seat, Window  # noqa: E402

EMAIL = 'maya@northwind.example'
KEYS = ['app', 'back_at', 'can', 'chat', 'email', 'fresh', 'in_use', 'name', 'next', 'plan', 'provider', 'read_at',
        'state', 'vendor', 'why', 'windows']


class FromTheBoard(KitCase, unittest.TestCase):
    def signed_in(self, **kw):
        kw.setdefault('codex_found_', codex_found(self.root, signed_in=True))
        return self.rig(**kw)

    def board(self, r):
        return view.view(kit=r.kit)

    def test_a_row_per_seat_in_the_one_shape(self):
        r = self.signed_in()
        r.codex.account_email = lambda: EMAIL
        self.assertEqual(rows.from_board(self.board(r), kit=r.kit), [{
            'provider': 'codex', 'name': 'Codex', 'vendor': 'OpenAI', 'email': EMAIL, 'plan': 'Pro',
            'windows': [{'label': 'weekly', 'used_pct': 42.0, 'resets_at': NOW + 2 * 86400.0, 'line': 95.0}],
            'read_at': NOW - 720.0, 'state': contract.ROOM, 'back_at': None, 'fresh': True, 'why': None, 'can': True,
            'app': True, 'in_use': False, 'next': False, 'chat': None}])

    def test_a_provider_with_no_seat_yet_is_one_row_with_no_windows(self):
        r = self.signed_in()
        r.codex.seats_ = ()
        (row,) = rows.from_board(self.board(r), kit=r.kit)
        self.assertEqual(sorted(row), KEYS)
        self.assertEqual((row['provider'], row['windows'], row['state'], row['back_at'], row['read_at'],
                          row['fresh'], row['plan'], row['can']), ('codex', [], contract.UNKNOWN, None, None, False,
                                                                     None, True))

    def test_two_seats_are_two_rows_of_one_provider(self):
        r = self.signed_in()
        r.codex.seats_ = (Seat('codex', 'a', 'ChatGPT Plus', plan='plus', in_use=True),
                          Seat('codex', 'b', 'ChatGPT Pro', plan='pro', in_use=False))
        r.codex.limits_ = (Limits('codex', 'a', (Window('weekly', 30.0, NOW + 86400.0, 10080, 95.0),), NOW - 60,
                                  'file', plan='plus'),
                           Limits('codex', 'b', (Window('weekly', 99.0, NOW + 3600.0, 10080, 95.0),), NOW - 60,
                                  'file', plan='pro'))
        got = rows.from_board(self.board(r), kit=r.kit)
        self.assertEqual([(x['provider'], x['plan'], x['state'], x['back_at'], x['windows'][0]['used_pct'])
                          for x in got], [('codex', 'Plus', contract.ROOM, None, 30.0),
                                          ('codex', 'Pro', contract.OUT, NOW + 3600.0, 99.0)])

    def test_a_provider_that_cannot_be_continued_to_has_no_row_and_one_line_under_the_list(self):
        cases = [(Found('codex', installed=False), 'not on this Mac'),
                 (codex_found(self.root, signed_in=False), 'not signed in'),
                 (codex_found(self.root, signed_in=None),               # with the step to take
                  'sign-in not seen here: switchboard providers --ask, or run codex login, or open the ChatGPT app'),
                 (codex_found(self.root, version='0.155.1', seen='0.169.1', signed_in=True),
                  'older than the one that wrote your chats')]
        for found, why in cases:
            with self.subTest(why=why):
                b = self.board(self.rig(codex_found_=found))
                self.assertEqual(rows.from_board(b), [])
                said = view.under_list(b, NOW, rows=[])
                self.assertEqual([x for x in said if x[0] != 'carry'], [('provider', 'Codex', why)])

    def test_a_board_from_before_fit_why_gives_a_row_only_when_installed_and_signed_in(self):
        # A newer command reading the state of a page started before the update: its entries have no fit_why key.
        # What every board has decides: installed and signed in (and a state that says neither is not so).
        r = self.signed_in()
        old = self.board(r)
        for p in old['providers']:
            del p['fit_why']
        codex = next(p for p in old['providers'] if p['id'] == 'codex')
        self.assertEqual([x['provider'] for x in rows.from_board(old)], ['codex'])
        for change in ({'signed_in': False}, {'signed_in': None}, {'installed': False}, {'installed': None},
                       {'state': contract.SIGNED_OUT}, {'state': contract.NOT_INSTALLED}):
            with self.subTest(change=change):
                b = copy.deepcopy(old)
                next(p for p in b['providers'] if p['id'] == 'codex').update(change)
                self.assertEqual(rows.from_board(b), [])
        self.assertTrue(codex['installed'] and codex['signed_in'])

    def test_a_board_from_before_fit_why_says_why_a_provider_has_no_row(self):
        # The same board: an added provider with no row has its one line under the list, decided as its row is, and
        # one with a row has none: never both, never neither.
        old = self.board(self.signed_in())
        for p in old['providers']:
            del p['fit_why']
            p.pop('fit_line', None)
        self.assertEqual([x for x in view.under_list(old, NOW, rows=rows.from_board(old)) if x[0] == 'provider'], [])
        for change, why in (({'signed_in': False}, words.STATE_SIGNED_OUT),
                            ({'signed_in': None}, words.STATE_SIGN_IN_NOT_SEEN),
                            ({'installed': False}, words.STATE_NOT_INSTALLED),
                            ({'installed': None}, words.STATE_NOT_INSTALLED),
                            ({'state': contract.SIGNED_OUT}, words.STATE_SIGNED_OUT),
                            ({'state': contract.NOT_INSTALLED}, words.STATE_NOT_INSTALLED)):
            with self.subTest(change=change):
                b = copy.deepcopy(old)
                next(p for p in b['providers'] if p['id'] == 'codex').update(change)
                shown = rows.from_board(b)
                self.assertEqual(shown, [])
                said = view.under_list(b, NOW, rows=shown)
                self.assertEqual([x for x in said if x[0] == 'provider'], [('provider', 'Codex', why)])

    def test_none_added_none_shown(self):
        self.assertEqual(rows.from_board(self.board(self.rig(codex_added=False))), [])
        self.assertEqual(rows.from_board(self.board(self.rig(codex=False))), [])

    def test_fresh_only_for_room_by_numbers_read_within_the_hour(self):
        for ago, fresh in ((720.0, True), (cli.WORDS_FRESH_S, True), (cli.WORDS_FRESH_S + 1, False),
                           (5 * 86400.0, False)):
            with self.subTest(ago=ago):
                r = self.signed_in()
                r.codex.limits_ = codex_limits(read_ago=ago)
                (row,) = rows.from_board(self.board(r), kit=r.kit)
                self.assertEqual((row['state'], row['fresh']), (contract.ROOM, fresh))
        r = self.signed_in()
        r.codex.limits_ = codex_limits(pct=97.0)
        (row,) = rows.from_board(self.board(r), kit=r.kit)
        self.assertEqual((row['state'], row['fresh']), (contract.OUT, False))       # out: no room, however fresh

    def test_the_folders_stay_and_never_are_the_rows_why(self):
        r = self.signed_in()
        b = self.board(r)
        r.choices.set_consent(r.folder, 'codex', 'never', NOW)
        (row,) = rows.from_board(b, folder=r.folder, kit=r.kit)
        self.assertEqual((row['why'], row['can']), ('chats in site never go to Codex', False))
        r.choices.set_consent(r.folder, 'codex', 'yes', NOW)
        (row,) = rows.from_board(b, folder=r.folder, kit=r.kit)
        self.assertEqual((row['why'], row['can']), (None, True))
        r.choices.set_stay(r.folder, 'claude', NOW)
        (row,) = rows.from_board(b, folder=r.folder, kit=r.kit)
        self.assertEqual((row['why'], row['can']), ('chats in site stay on Claude', False))
        (row,) = rows.from_board(b, kit=r.kit)                       # no folder: no folder facts
        self.assertEqual((row['why'], row['can'], row['in_use']), (None, True, False))

    def test_in_use_only_while_this_folders_move_started_the_app_there(self):
        for state, in_use in (('printed', False), ('in_app', False), ('made', False), ('failed', False),
                              ('returned', False), ('started', True)):
            with self.subTest(state=state):
                r = self.signed_in()
                r.tasks.add_hop(Hop(id='h-0000000b', task='t-0000000b', kind='continue', at=NOW - 60,
                                    folder=r.folder, from_provider='claude', from_chat=None, to_provider='codex',
                                    state=state))
                (row,) = rows.from_board(self.board(r), folder=r.folder, kit=r.kit)
                self.assertIs(row['in_use'], in_use)

    def test_in_use_for_a_few_hours_after_the_start_and_back_still_finds_the_move(self):
        # A start in this folder counts as in use for rows.IN_USE_S, not for the month its record is kept: an app
        # closed that night no longer hides Continue here days later. The record itself stays open for back.
        for ago, in_use in ((60.0, True), (rows.IN_USE_S, True), (rows.IN_USE_S + 1, False), (4 * 86400.0, False)):
            with self.subTest(ago=ago):
                r = self.signed_in()
                r.tasks.add_hop(Hop(id='h-0000000c', task='t-0000000c', kind='continue', at=NOW - ago,
                                    folder=r.folder, from_provider='claude', from_chat=None, to_provider='codex',
                                    state='started'))
                (row,) = rows.from_board(self.board(r), folder=r.folder, kit=r.kit)
                self.assertIs(row['in_use'], in_use)
                self.assertEqual(r.tasks.open_continue(r.folder).id, 'h-0000000c')
        self.assertLessEqual(rows.IN_USE_S, 12 * 3600.0)

    def test_rows_is_the_same_derivation_for_the_kits_folder(self):
        r = self.signed_in()
        r.choices.set_consent(r.folder, 'codex', 'never', NOW)
        self.assertEqual(rows.rows(r.kit), rows.from_board(self.board(r), folder=r.folder, kit=r.kit))
        self.assertEqual(rows.rows(r.kit)[0]['why'], 'chats in site never go to Codex')
        self.assertEqual((r.calls.runs, r.calls.execs, r.calls.links, r.calls.pipes), ([], [], [], []))

    def test_a_failure_leaves_the_rows_out(self):
        r = self.rig()
        r.choices.order = lambda: 1 / 0
        self.assertEqual(rows.rows(r.kit), [])


class Pure(unittest.TestCase):
    def entry(self, pid, **kw):
        base = {'id': pid, 'name': pid.capitalize(), 'vendor': pid.capitalize() + ' Labs', 'app': pid.capitalize(),
                'added': True, 'account_list': False, 'state': contract.ROOM, 'email': None, 'has_app': False,
                'newest_seen': None, 'fit_why': None,
                'seats': [{'label': 'Crew', 'plan': 'crew', 'in_use': True, 'read_at': NOW - 60,
                           'state': contract.ROOM, 'back_at': None,
                           'windows': [{'label': '3-day', 'used_pct': 30.0, 'resets_at': NOW + 86400.0,
                                        'length_min': 4320, 'line': 95.0}]}]}
        base.update(kw)
        return base

    def board(self):
        claude = self.entry('claude', account_list=True, seats=[])
        return {'v': 1, 'now': NOW, 'order': ['claude', 'tern', 'codex'], 'add': [], 'carry': True,
                'providers': [claude, self.entry('tern'), self.entry('codex', fit_why='not signed in'),
                              self.entry('later')]}

    def test_in_the_persons_order_and_never_the_account_lists_provider(self):
        got = rows.from_board(self.board(), now=NOW)
        self.assertEqual([(x['provider'], x['name'], x['vendor'], x['plan']) for x in got],
                         [('tern', 'Tern', 'Tern Labs', None), ('later', 'Later', 'Later Labs', None)])
        self.assertEqual(got[0]['windows'], [{'label': '3-day', 'used_pct': 30.0, 'resets_at': NOW + 86400.0,
                                              'line': 95.0}])

    def test_from_the_board_alone_and_it_changes_nothing(self):
        b = self.board()
        before = copy.deepcopy(b)
        got = rows.from_board(b, now=NOW)
        self.assertEqual(b, before)
        got[0]['windows'][0]['used_pct'] = 99.0                   # a copy: the board is never changed through a row
        self.assertEqual(b, before)

    def test_a_damaged_board_gives_no_rows(self):
        for damaged in (None, 7, {}, {'providers': 'x'}, {'providers': [7, None, {'id': 3}]},
                        {'providers': [self.entry('tern', seats='x')]}):
            with self.subTest(damaged=damaged):
                got = rows.from_board(damaged, now=NOW)
                self.assertTrue(all(sorted(x) == KEYS for x in got))
                self.assertLessEqual(len(got), 1)
        self.assertEqual(rows.from_board(None), [])

    def test_the_words_of_a_folders_reason(self):
        self.assertEqual(words.ROW_STAYS.format(basename='site', p='Claude'), 'chats in site stay on Claude')
        self.assertEqual(words.ROW_NEVER.format(basename='site', to='Codex'), 'chats in site never go to Codex')


if __name__ == '__main__':
    unittest.main()
