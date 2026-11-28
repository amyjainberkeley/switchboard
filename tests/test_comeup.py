import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The one moment Switchboard speaks up by itself, and the one Next of every face. Pure functions decide from the
# page's state and the board alone:
#
# - with_next: a provider row is next only when every usable Claude account is at or over the person's own switch
#   lines (automatic switching has nowhere left to go) and that row holds (room by numbers of the last hour, it can be
#   continued to, not in use). Accounts kept out of automatic switching are never counted.
# - note: when the first provider in the person's order is the one whose seats are the account list, the one
#   notification says that moment in words (the accounts counted, "Both" only for two) and where to press in the
#   terminal's list; key providers:out:<that provider>, its instance the end of the out spell when known, else when it
#   began, so notify's own memory says it once per spell. Another provider first: the board's own note, unchanged.
# - The server sends it after each build of the board, through notify.send, never in the demo or with notifications
#   off. There is no panel, no held record and no Not now.
#
# Made-up accounts only; no notification is shown.
import copy
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import comeup, core, demo, notify, server  # noqa: E402
from switchboard.providers import view  # noqa: E402
from test_menubar import NOW, at, w  # noqa: E402

FIVE_BACK = at(13, 18)          # 6:00 PM the same Friday
WEEK_BACK = at(16, 13)          # Mon 1:00 PM
SAID = ('Both Claude accounts are at your lines. Codex has room. In a terminal in your project folder, type '
        'switchboard and press 3.')


def acct(slot, email, five=None, week=None, **more):
    return dict({'email': email, 'slot': slot, 'active': slot == 1, 'needs_login': False, 'unmanaged': False,
                 'trusted': True, 'usage': {'session': five, 'weekly': week, 'scoped': []}}, **more)


def codex(**more):
    return dict({'provider': 'codex', 'name': 'Codex', 'vendor': 'OpenAI', 'email': 'ana@northwind.example',
                 'plan': 'Plus', 'windows': [], 'read_at': NOW - 40, 'state': 'room', 'back_at': None, 'fresh': True,
                 'next': True, 'in_use': False, 'why': None, 'chat': {'name': 'Upload retries', 'mark': 'a1b2c3d4'},
                 'can': True, 'app': True},
                **more)


def state(accounts=None, provider_rows=None, **more):
    """Two Claude accounts, both at their lines (one by its 5-hour limit, one by its weekly), and Codex with room."""
    if accounts is None:
        accounts = [acct(1, 'ana@harbor.example', five=w(93, FIVE_BACK), week=w(40, WEEK_BACK)),
                    acct(2, 'ana@ivy.example', five=w(10, FIVE_BACK), week=w(96, WEEK_BACK))]
    return dict({'now': NOW, 'auto': {'enabled': True, 'session_at': 90.0, 'weekly_at': 95.0, 'model_at': 90.0},
                 'accounts': accounts, 'provider_rows': [codex()] if provider_rows is None else provider_rows},
                **more)


def board(first='claude', carry=('codex', 'later'), codex_state='room', codex_back=None):
    """The board the note reads: who is first in the person's order, and which providers can be carried to."""
    def entry(pid, name, **kw):
        return dict({'id': pid, 'name': name, 'account_list': False, 'can_carry': pid in carry, 'state': 'room',
                     'back_at': None, 'read_at': NOW - 60, 'fit_why': None, 'seats': []}, **kw)
    shown = [entry('claude', 'Claude', account_list=True, count=2),
             entry('codex', 'Codex', state=codex_state, back_at=codex_back), entry('later', 'Later')]
    if first != 'claude':
        shown = [shown[1], shown[0], shown[2]]
    return {'v': 1, 'now': NOW, 'order': [p['id'] for p in shown], 'providers': shown, 'add': [], 'carry': True}


def body(s, b=None, **kw):
    got = comeup.note(s, b or board(), **kw)
    return got[1] if got else None


class TheNote(unittest.TestCase):
    def test_both_at_their_lines_and_codex_has_room(self):
        self.assertEqual(comeup.note(state(), board()), ('providers:out:claude', SAID, core.parse_iso(FIVE_BACK)))

    def test_the_accounts_are_counted_both_only_for_two(self):
        one = [acct(1, 'a@x.example', five=w(91, FIVE_BACK), week=w(1, WEEK_BACK))]
        three = one + [acct(i, '%d@x.example' % i, week=w(99, WEEK_BACK)) for i in (2, 3)]
        self.assertEqual(body(state(one)), 'Your Claude account is at your lines. Codex has room. In a terminal in '
                                           'your project folder, type switchboard and press 2.')
        self.assertEqual(body(state(three)), 'All 3 Claude accounts are at your lines. Codex has room. In a terminal '
                                             'in your project folder, type switchboard and press 4.')

    def test_not_while_any_claude_account_has_room_or_is_not_known(self):
        s = state()
        s['accounts'][1]['usage']['weekly'] = w(80, WEEK_BACK)
        self.assertIsNone(comeup.note(s, board()))
        s = state()
        s['accounts'][1]['trusted'] = False                     # a reading too old: not known, never "at its lines"
        self.assertIsNone(comeup.note(s, board()))
        s = state()
        s['accounts'][0]['usage']['session'] = w(93, at(13, 15))     # its 5-hour window reset at 3:00 PM
        self.assertIsNone(comeup.note(s, board()))
        self.assertIsNone(comeup.note(state(accounts=[]), board()))

    def test_an_account_that_needs_a_sign_in_is_not_at_your_lines_and_the_sentence_says_so(self):
        s = state()
        s['accounts'][1] = acct(2, 'ana@ivy.example', needs_login=True)
        s['accounts'].append(acct(None, 'seen@x.example', week=w(5, WEEK_BACK), unmanaged=True))
        got = comeup.note(s, board())
        self.assertEqual(got[1], 'One Claude account is at your lines and the other needs a sign-in. Codex has room. '
                                 'In a terminal in your project folder, type switchboard and press 3.')
        self.assertEqual(got[2], core.parse_iso(FIVE_BACK))
        more = state()
        more['accounts'] += [acct(3, 'c@x.example', needs_login=True), acct(4, 'd@x.example', needs_login=True)]
        self.assertTrue(body(more).startswith('2 Claude accounts are at your lines and 2 need a sign-in. Codex has '
                                              'room. In a terminal in your project folder, type switchboard and '
                                              'press 5.'))
        more['accounts'].pop()
        self.assertTrue(body(more).startswith('2 Claude accounts are at your lines and one needs a sign-in.'))
        none = state(accounts=[acct(1, 'a@x.example', needs_login=True), acct(2, 'b@x.example', needs_login=True)])
        self.assertIsNone(comeup.note(none, board()))                # nothing usable is at its lines
        room = state()
        room['accounts'][1] = acct(2, 'ana@ivy.example', needs_login=True)
        room['accounts'][0]['usage']['session'] = w(30, FIVE_BACK)
        self.assertIsNone(comeup.note(room, board()))

    def test_a_models_week_never_makes_an_account_at_your_lines(self):
        # Automatic switching's one rule (policy.unusable_by_windows): an account is at its lines only by its own
        # 5-hour and weekly windows. Five accounts as one Mac had them when a model's week stopped automatic
        # switching there: the main account at weekly 96, a Team seat at 5-hour 14 and weekly 35, three Max accounts
        # at weekly 77, 81 and 87 whose Fable week is at 100, 98 and 100, automatic switching counting Fable.
        def fable(a, pct):
            a['usage']['scoped'] = [{'name': 'Fable', 'pct': pct, 'resets': WEEK_BACK}]
            return a
        main = acct(1, 'main@harbor.example', week=w(96, WEEK_BACK))
        seat = acct(2, 'seat@team.example', five=w(14, FIVE_BACK), week=w(35, WEEK_BACK), id='seat')
        maxes = [fable(acct(slot, 'max%d@ivy.example' % slot, week=w(week, WEEK_BACK)), pct)
                 for slot, week, pct in ((3, 77, 100), (4, 81, 98), (5, 87, 100))]

        def counting(accounts, models=('Fable',)):
            s = state(accounts=accounts)
            s['auto'].update(model='Fable' if models else None, models=list(models), model_at=95.0)
            return s
        # only the main account is at its lines, by its weekly window
        self.assertEqual([comeup.at_lines(counting([a])) for a in [main, seat] + maxes],
                         [True, False, False, False, False])
        self.assertEqual(comeup.period_end(counting([main])), core.parse_iso(WEEK_BACK))
        everyone = counting([main, seat] + maxes)
        self.assertIsNone(comeup.note(everyone, board()))
        self.assertEqual([r['next'] for r in comeup.with_next(everyone)['provider_rows']], [False])
        # only a model's week used up on every account: they still work on other models, so nothing is offered
        for models in (('Fable',), ()):
            with self.subTest(models=models):
                only = counting(maxes, models)
                self.assertFalse(comeup.at_lines(only))
                self.assertIsNone(comeup.note(only, board()))
                self.assertEqual([r['next'] for r in comeup.with_next(only)['provider_rows']], [False])
        # its own lines count, as automatic switching's do: the Team seat with its own weekly line at 30
        own = counting([seat])
        own['auto']['own_lines'] = {'seat': {'weekly_at': 30.0}}
        self.assertTrue(comeup.at_lines(own))
        # the number as automatic switching reads it, not as a face rounds it: 89.6 is under a line of 90
        self.assertFalse(comeup.at_lines(state(accounts=[acct(1, 'a@x.example', five=w(89.6, FIVE_BACK))])))
        self.assertTrue(comeup.at_lines(state(accounts=[acct(1, 'a@x.example', five=w(90, FIVE_BACK))])))

    def test_the_row_must_hold_and_no_chat_is_needed(self):
        for bad in (dict(state='out'), dict(state='unknown'), dict(can=False), dict(in_use=True), dict(fresh=False)):
            with self.subTest(bad=bad):
                self.assertIsNone(comeup.note(state(provider_rows=[codex(**bad)]), board()))
        self.assertIsNone(comeup.note(state(provider_rows=[]), board()))
        self.assertEqual(body(state(provider_rows=[codex(chat=None)])), SAID)   # the press in the terminal finds it
        later = codex(provider='later', name='Later')
        self.assertEqual(body(state(provider_rows=[codex(state='out'), later])),
                         'Both Claude accounts are at your lines. Later has room. In a terminal in your project '
                         'folder, type switchboard and press 4.')
        self.assertIsNone(comeup.note(state(), board(carry=('later',))))     # Codex cannot be carried to
        self.assertEqual(body(state(), number=7), SAID.replace('press 3', 'press 7'))

    def test_another_provider_first_is_the_boards_own_note(self):
        b = board(first='codex', codex_state='out', codex_back=NOW + 7200)
        self.assertEqual(comeup.note(state(), b), view.out_note(b, NOW))
        self.assertTrue(comeup.note(state(), b)[1].startswith('Codex is out until '))
        self.assertEqual(comeup.note(None, b), view.out_note(b, NOW))        # the board's own clock
        self.assertIsNone(comeup.note(state(), board(first='codex')))       # Codex has room: nothing to say
        for damaged in (None, {}, {'providers': 'x'}):
            self.assertIsNone(comeup.note(state(), damaged))

    def test_once_per_spell_its_end_when_known_else_when_it_began(self):
        s = state()
        s['accounts'][0]['usage']['session'] = w(93, None)
        s['accounts'][1]['usage']['weekly'] = w(96, None)
        self.assertIsNone(comeup.period_end(s))
        self.assertEqual(comeup.note(s, board(), since=NOW - 900)[2], NOW - 900)
        self.assertEqual(comeup.note(s, board())[2], NOW)                    # no start given: the state's own clock
        self.assertEqual(comeup.note(state(), board(), since=NOW - 900)[2], core.parse_iso(FIVE_BACK))

    def test_when_the_out_period_ends(self):
        s = state()
        self.assertEqual(comeup.period_end(s), core.parse_iso(FIVE_BACK))     # harbor's 5-hour window comes back first
        s['accounts'][0]['usage']['weekly'] = w(97, WEEK_BACK)               # harbor held by both: its later one
        self.assertEqual(comeup.period_end(s), core.parse_iso(WEEK_BACK))
        s['accounts'][0]['usage']['session'] = w(93, None)
        s['accounts'][1]['usage']['weekly'] = w(96, None)
        self.assertIsNone(comeup.period_end(s))

    def test_from_the_state_and_the_board_alone_and_it_changes_nothing(self):
        s, b = state(), board()
        before = copy.deepcopy((s, b))
        comeup.note(s, b)
        comeup.with_next(s)
        self.assertEqual((s, b), before)

    def test_the_panel_and_its_record_are_gone(self):
        for gone in ('decide', 'notice', 'held', 'said', 'running', 'chat_line', 'NOTICE_TAIL'):
            self.assertFalse(hasattr(comeup, gone), gone)


class TheNext(unittest.TestCase):
    def test_one_next_across_providers(self):
        got = comeup.with_next(state(next='ana@ivy.example'))
        self.assertEqual(([p['next'] for p in got['provider_rows']], got['next'], got['claude_next']),
                         ([True], None, 'ana@ivy.example'))
        room = state(next='ana@ivy.example')
        room['accounts'][1]['usage']['weekly'] = w(20, WEEK_BACK)
        got = comeup.with_next(room)
        self.assertEqual(([p['next'] for p in got['provider_rows']], got['next']), ([False], 'ana@ivy.example'))
        for bad in (dict(fresh=False), dict(can=False), dict(in_use=True), dict(state='out')):
            got = comeup.with_next(state(next='ana@ivy.example', provider_rows=[codex(**bad)]))
            self.assertEqual(([p['next'] for p in got['provider_rows']], got['next']), ([False], 'ana@ivy.example'))
        two = comeup.with_next(state(provider_rows=[codex(provider='later', name='Later', can=False), codex()]))
        self.assertEqual([p['next'] for p in two['provider_rows']], [False, True])         # the first that can, only
        again = comeup.with_next(dict(comeup.with_next(state(next='ana@ivy.example')),
                                      provider_rows=[codex(can=False)]))
        self.assertEqual(again['next'], 'ana@ivy.example')                # decided again: automatic switching's own
        s = state()
        comeup.with_next(s)
        self.assertEqual(s, state())                                      # a copy: the state is not changed

    def test_two_seats_of_one_provider_only_the_one_that_holds_is_next(self):
        seats = [codex(state='out', back_at=NOW + 3600, next=False), codex(email='ana@harbor.example', next=False)]
        got = comeup.with_next(state(provider_rows=seats))
        self.assertEqual([p['next'] for p in got['provider_rows']], [False, True])
        self.assertIsNone(got['next'])

    def test_the_next_reads_and_writes_the_rows_and_leaves_the_board_alone(self):
        b = {'v': 1, 'now': NOW, 'order': ['claude', 'codex'], 'providers': [{'id': 'codex', 'state': 'out'}],
             'add': [], 'carry': True}
        got = comeup.with_next(state(providers=b))
        self.assertIs(got['providers'], b)
        self.assertEqual(b['providers'], [{'id': 'codex', 'state': 'out'}])
        self.assertEqual([p['next'] for p in got['provider_rows']], [True])

    def test_an_account_kept_out_is_never_usable_nor_next(self):
        spare = acct(3, 'ana@spare.example', five=w(10, FIVE_BACK), week=w(10, WEEK_BACK), id='acct-3')
        s = state(next='ana@spare.example')
        s['accounts'].append(spare)
        got = comeup.with_next(s)                              # the spare account has room: Codex is not next
        self.assertEqual(([p['next'] for p in got['provider_rows']], got['next']), ([False], 'ana@spare.example'))
        s['auto']['rules'] = {'acct-3': 'out'}                 # kept out of automatic switching
        self.assertEqual(comeup.usable(s), s['accounts'][:2])
        got = comeup.with_next(s)
        self.assertEqual(([p['next'] for p in got['provider_rows']], got['next']), ([True], None))
        self.assertEqual(body(s), SAID.replace('press 3', 'press 4'))     # it is not "needs a sign-in" either
        s['auto']['rules'] = {}
        s['accounts'][2] = dict(spare, rule='out')             # a face's account says its rule itself
        self.assertEqual([p['next'] for p in comeup.with_next(s)['provider_rows']], [True])
        alone = state(accounts=[dict(spare, rule='out')])
        self.assertFalse(comeup.at_lines(alone))               # nothing usable is at its lines


class TheServer(unittest.TestCase):
    """server.providers_note after a build of the board: the state it reads, what it sends, and what is gone."""

    def setUp(self):
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = False
        self.sent = []
        for p in (mock.patch.object(notify, 'send', lambda note, **k: self.sent.append(note) or True),
                  mock.patch.object(server, 'helper_settings', lambda: {'notify': True}),
                  mock.patch.dict(server.NOTE_SPELL, {'since': None}, clear=True)):
            p.start()
            self.addCleanup(p.stop)
        self.t = 1795600000.0
        self.board = demo.providers(self.t)          # Claude first; one made-up Codex with room, read 40 s ago

    def note(self, st, b=None, now=None):
        return server.providers_note(self.board if b is None else b, now=now or self.t, state=st)

    def test_one_note_with_where_to_press_from_the_rows_of_this_board(self):
        self.assertTrue(self.note(state(provider_rows=[])))         # the rows come from the board, not the state's
        (n,) = self.sent
        self.assertEqual((n.key, n.title, n.body, n.instance, n.every),
                         ('providers:out:claude', 'Switchboard', SAID, core.parse_iso(FIVE_BACK), None))
        self.assertFalse(self.note(state(), demo.providers(self.t, out=True)))       # Codex out: nothing
        self.assertEqual(len(self.sent), 1)

    def test_the_state_is_built_only_when_the_note_may_speak(self):
        built = []
        with mock.patch.object(server, 'view', lambda: built.append(1) or state()):
            self.assertTrue(server.providers_note(self.board, now=self.t))
            self.assertEqual(built, [1])
            self.assertFalse(server.providers_note(demo.providers(self.t, codex=False), now=self.t))   # no row
            b = demo.providers(self.t, out=True)
            b['providers'], b['order'] = b['providers'][::-1], ['codex', 'claude']
            self.assertTrue(server.providers_note(b, now=self.t))    # Codex first: the board's own note
        self.assertEqual(built, [1])
        self.assertEqual([n.key for n in self.sent], ['providers:out:claude', 'providers:out:codex'])
        self.assertTrue(self.sent[1].body.startswith('Codex is out until '))

    @staticmethod
    def no_end(now):
        """Both accounts at their lines, with no known refill: a spell whose end is not known."""
        s = state(now=now)
        s['accounts'][0]['usage']['session'] = w(93, None)
        s['accounts'][1]['usage']['weekly'] = w(96, None)
        return s

    @staticmethod
    def room():
        s = state()
        s['accounts'][1]['usage']['weekly'] = w(20, WEEK_BACK)
        return s

    def test_a_spell_whose_end_is_not_known_is_said_for_when_this_page_first_saw_it(self):
        for t, st in ((self.t, self.no_end(NOW)), (self.t + 300, self.no_end(NOW + 300)), (self.t + 600, self.room()),
                      (self.t + 7200, self.no_end(NOW + 7200))):
            self.note(st, demo.providers(t), now=t)                  # Codex's numbers read 40 s before each
        self.assertEqual([n.instance for n in self.sent], [self.t, self.t, self.t + 7200])   # notify keeps once
        self.assertEqual(server.NOTE_SPELL['since'], self.t + 7200)

    def test_a_spell_that_ends_while_notifications_are_off_leaves_no_start_behind(self):
        # Said at its start; it ends while notifications are off; a new one starts once they are on again: the new
        # one is said for its own start, never for the old one, which notify would take for a spell said already.
        self.note(self.no_end(NOW), demo.providers(self.t), now=self.t)
        with mock.patch.object(server, 'helper_settings', lambda: {'notify': False}):
            self.note(self.room(), demo.providers(self.t + 600), now=self.t + 600)
        self.assertIsNone(server.NOTE_SPELL['since'])
        self.note(self.no_end(NOW + 7200), demo.providers(self.t + 7200), now=self.t + 7200)
        self.assertEqual([n.instance for n in self.sent], [self.t, self.t + 7200])

    def test_never_in_the_demo_or_with_notifications_off(self):
        with mock.patch.object(server, 'helper_settings', lambda: {'notify': False}):
            self.assertFalse(self.note(state()))
        core.DEMO = True
        self.assertFalse(self.note(state()))
        self.assertEqual(self.sent, [])

    def test_the_held_record_not_now_and_its_clock_are_gone(self):
        for gone in ('comeup_tick', 'COMEUP', 'COMEUP_EVERY', 'HELD_FILE', 'held_view', 'hold', 'said_now', 'let_go'):
            self.assertFalse(hasattr(server, gone), gone)
        import inspect
        self.assertNotIn('comeup', inspect.getsource(server.loop))
        for path in ('/api/not-now', '/api/said'):
            with self.assertRaises(core.SwitchError):
                server._act(path, {})
        self.assertNotIn('held', server.facts())
        self.assertFalse(os.path.exists(core.data_path('comeup.json')))


if __name__ == '__main__':
    unittest.main()
