import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The menu bar's menu with another provider's row (the state's 'provider_rows'): after the Claude rows and Sign in to
# all, drawn by the same pure functions (rows, row_ops): the account's email (or the app's name), its plan in grey after
# it, at most two bars, and a last cell that says one thing (In use, why it cannot be continued to, Out until, or
# Next and Continue here); a heading for each maker ("Claude", "OpenAI"), only when such a row is there. Its reset
# times are the page's, as an account's: no line under its bars, so it is as tall as an account's row with as many
# bars. A line there says only what no bar can: "Not known" for a window with no number, "Numbers not known" for a
# row with no windows. A 5-hour window with no number has no bar, as an account's. VoiceOver says its last cell.
# A click on it asks the page for /api/continue, never anything else, and the answer is said in the menu's note lines,
# the page's own words, wrapped between words, the line to type whole.
# With no other provider the menu is what it was. The item (the S) does not change.
#
# Made-up accounts only, no AppKit.
import copy
import os
import queue
import sys
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import core, menubar, term  # noqa: E402
import test_menubar as tm  # noqa: E402
from test_menubar import NOW, TZ, view  # noqa: E402

EMAIL = 'ana@northwind.example'         # the same address as Claude account 1: the rows must still be told apart
FIVE_AT = NOW + 3.7 * 3600
WEEK_AT = NOW + 5.6 * 86400


def codex(**more):
    """One row of the state's 'provider_rows', every key of it."""
    return dict({'provider': 'codex', 'name': 'Codex', 'vendor': 'OpenAI', 'email': EMAIL, 'plan': 'Plus',
                 'windows': [{'label': '5-hour', 'used_pct': 8.0, 'resets_at': FIVE_AT, 'line': 90.0},
                             {'label': 'weekly', 'used_pct': 21.0, 'resets_at': WEEK_AT, 'line': 95.0}],
                 'read_at': NOW - 40, 'state': 'room', 'back_at': None, 'fresh': True, 'why': None, 'can': True,
                 'app': True, 'in_use': False, 'next': False,
                 'chat': {'name': 'Upload retries', 'mark': 'a1b2c3d4'}}, **more)


def window(label, used_pct, resets_at, line=95.0):
    return {'label': label, 'used_pct': used_pct, 'resets_at': resets_at, 'line': line}


def other(state):
    return [r for r in menubar.rows(state, TZ) if r.get('provider')]


class WithNone(unittest.TestCase):
    def test_the_menu_is_what_it_was(self):
        self.assertEqual(menubar.rows(view(provider_rows=[]), TZ), menubar.rows(view(), TZ))
        self.assertEqual(menubar.menu(view(provider_rows=[]), tz=TZ), menubar.menu(view(), tz=TZ))
        self.assertFalse(any('provider' in r or 'plan' in r for r in menubar.rows(view(), TZ)))
        self.assertEqual(menubar.reading(view(provider_rows=[codex()])), menubar.reading(view()))   # the item


class TheRow(unittest.TestCase):
    def test_after_the_claude_rows_numbered_as_the_terminal_numbers_it(self):
        got = menubar.rows(view(provider_rows=[codex()]), TZ)
        self.assertEqual([r['slot'] for r in got], [1, 2, 3, 4, 5, 6])
        r = got[-1]
        self.assertEqual((r['email'], r['provider'], r['maker'], r['plan'], r['action'], r['marks']),
                         (EMAIL, 'codex', 'OpenAI', 'Plus', 'continue', [('Continue here', 'use')]))
        self.assertEqual((r['five']['label'], r['five']['pct'], r['week']['label'], r['week']['pct']),
                         ('5-hour', 8, 'Weekly', 21))
        self.assertEqual(r['foot'], '')                 # its reset times are the page's, as an account's
        self.assertEqual(menubar.row_height(r), menubar.ROW_H)
        self.assertEqual(r['chat'], {'name': 'Upload retries', 'mark': 'a1b2c3d4'})

    def test_no_chat_no_continue_here(self):
        r = other(view(provider_rows=[codex(chat=None, can=False)]))[0]
        self.assertEqual((r['action'], r['marks']), (None, []))

    def test_the_last_cell_says_one_thing_first_match_wins(self):
        def cell(**more):
            r = other(view(provider_rows=[codex(**more)]))[0]
            return r['marks'], r['action']
        stays = 'this folder stays on Claude'
        self.assertEqual(cell(in_use=True, why=stays, can=False, state='out', back_at=WEEK_AT),
                         ([('In use', 'in-use')], None))
        self.assertEqual(cell(why=stays, can=False, state='out', back_at=WEEK_AT),
                         ([('This folder stays on Claude', 'next')], None))
        self.assertEqual(cell(state='out', back_at=WEEK_AT), ([('Out until Thu 5:55 AM', 'next')], None))
        self.assertEqual(cell(state='out', back_at=None), ([('Out', 'next')], None))
        self.assertEqual(cell(state='out', back_at=NOW - 60), ([('Out', 'next')], None))   # a time gone by
        self.assertEqual(cell(next=True), ([('Next', 'next'), ('Continue here', 'use')], 'continue'))
        self.assertEqual(cell(), ([('Continue here', 'use')], 'continue'))
        self.assertEqual(cell(can=False), ([], None))

    def test_room_by_old_numbers_says_how_old_as_a_claude_row_does(self):
        # Room read over an hour ago is not fresh: the row says its age first, as a Claude row's age mark, so it never
        # looks current. Fresh room says nothing more.
        old = other(view(provider_rows=[codex(read_at=NOW - 3 * 3600, fresh=False)]))[0]
        self.assertEqual((old['marks'], old['action']), ([('3 hours ago', 'next'), ('Continue here', 'use')],
                                                         'continue'))
        self.assertEqual(other(view(provider_rows=[codex()]))[0]['marks'], [('Continue here', 'use')])
        out = other(view(provider_rows=[codex(read_at=NOW - 3 * 3600, fresh=False, state='out', back_at=None)]))[0]
        self.assertEqual(out['marks'], [('Out', 'next')])                  # out says its own words, no age

    def test_next_is_one_row_across_providers(self):
        s = view(provider_rows=[codex(next=True)])
        got = menubar.rows(s, TZ)
        self.assertEqual([r['slot'] for r in got if r['next']], [6])
        self.assertEqual(got[-1]['marks'], [('Next', 'next'), ('Continue here', 'use')])
        self.assertFalse(any(('Next', 'next') in r['marks'] for r in got[:-1]))
        self.assertTrue(any(('Next', 'next') in r['marks'] for r in menubar.rows(view(provider_rows=[codex()]), TZ)))

    def test_in_use(self):
        r = other(view(provider_rows=[codex(in_use=True, next=True)]))[0]
        self.assertEqual((r['action'], r['marks'], r['in_use'], r['next']), (None, [('In use', 'in-use')], True,
                                                                               False))

    def test_named_by_the_app_until_it_says_an_email(self):
        self.assertEqual(other(view(provider_rows=[codex(email=None)]))[0]['email'], 'Codex')

    def test_numbers_refilled_not_known_weekly_alone_and_at_a_line(self):
        refilled = codex(windows=[window('5-hour', 64.0, NOW - 60, 90.0), window('weekly', None, None)])
        r = other(view(provider_rows=[refilled]))[0]
        self.assertEqual((r['five']['pct'], r['five']['started'], r['week']['unknown']), (0, False, True))
        self.assertEqual(r['foot'], 'Not known')        # the % cell holds only the dash: the reset line says it
        unknown = other(view(provider_rows=[codex(windows=[window('3-day', None, None)])]))[0]
        self.assertEqual((unknown['five'], menubar.pct_text(unknown['week']), unknown['foot']),
                         (None, '—', 'Not known'))
        alone = other(view(provider_rows=[codex(windows=[window('weekly', 97.0, WEEK_AT)])]))[0]
        self.assertIsNone(alone['five'])
        self.assertTrue(alone['week']['over'])
        # no 5-hour window: its words keep the 5-hour row's place; no line under its bars (reset times are the page's)
        self.assertEqual((alone['foot'], menubar.row_height(alone)), ('', menubar.ROW_H))
        self.assertEqual(alone['no_five'], 'No 5-hour limit')

    def test_a_5_hour_window_with_no_number_has_no_bar_any_other_is_not_known(self):
        r = other(view(provider_rows=[codex(windows=[window('5-hour', None, FIVE_AT, 90.0),
                                                     window('weekly', 21.0, WEEK_AT)])]))[0]
        self.assertIsNone(r['five'])
        self.assertEqual((r['week']['pct'], r['foot']), (21, ''))
        self.assertEqual(menubar.row_height(r), menubar.ROW_H - menubar.BAR_ROW)
        self.assertEqual([op[5][0][0] for op in menubar.row_ops(r) if op[0] == 'text' and op[-1] == 'left'],
                         ['Weekly'])
        r = other(view(provider_rows=[codex(windows=[window('5-hour', 8.0, FIVE_AT, 90.0),
                                                     window('weekly', None, None)])]))[0]
        self.assertEqual((r['five']['pct'], r['week']['unknown'], menubar.pct_text(r['week'])), (8, True, '—'))
        self.assertEqual(r['foot'], 'Not known')
        self.assertEqual(menubar.row_height(r), menubar.ROW_H + menubar.FOOT_H)

    def test_a_5_hour_window_with_no_number_has_no_bar_also_once_its_reset_has_passed(self):
        r = other(view(provider_rows=[codex(windows=[window('5-hour', None, NOW - 5, 90.0),
                                                     window('weekly', 21.0, WEEK_AT)])]))[0]
        self.assertIsNone(r['five'])
        self.assertEqual((r['week']['pct'], r['foot']), (21, ''))
        r = other(view(provider_rows=[codex(windows=[window('5-hour', 8.0, NOW - 5, 90.0),
                                                     window('weekly', 21.0, WEEK_AT)])]))[0]
        self.assertEqual((r['five']['pct'], r['five']['started']), (0, False))     # a number: refilled, 0%

    def test_the_providers_own_window_names_two_bars_at_most(self):
        r = other(view(provider_rows=[codex(windows=[window('3-day', 30.0, WEEK_AT)])]))[0]
        self.assertEqual((r['five'], r['week']['label'], r['week']['pct']), (None, '3-day', 30))
        three = [window('5-hour', 8.0, FIVE_AT, 90.0), window('3-day', 30.0, WEEK_AT), window('weekly', 21.0, WEEK_AT)]
        r = other(view(provider_rows=[codex(windows=three)]))[0]
        self.assertEqual([lim['label'] for lim in (r['five'], r['week'])], ['5-hour', '3-day'])
        self.assertEqual((r['foot'], menubar.row_height(r)), ('', menubar.ROW_H))

    def test_a_row_with_no_windows_says_numbers_not_known_and_draws_no_bar(self):
        r = other(view(provider_rows=[codex(windows=[], state='unknown', read_at=None)]))[0]
        self.assertEqual((r['five'], r['week'], r['foot']), (None, None, 'Numbers not known'))
        self.assertEqual([op for op in menubar.row_ops(r) if op[0] == 'box'], [])
        self.assertEqual(menubar.row_height(r), menubar.ROW_H + menubar.FOOT_H - 2 * menubar.BAR_ROW)
        self.assertEqual(menubar.row_label(r), 'Continue here in ana@northwind.example, OpenAI, Plus, '
                                               'numbers not known')

    def test_a_reset_time_that_is_not_a_time_never_breaks_a_face(self):
        for t in (1e300, -1e300, 1e20, 0.0, -5.0):
            with self.subTest(t=t):
                row = codex(windows=[window('5-hour', 8.0, t, 90.0), window('weekly', 21.0, t)])
                (r,) = other(view(provider_rows=[row]))
                self.assertEqual((r['five']['pct'], r['five']['resets'], r['week']['resets']), (8, None, None))
                menubar.row_ops(r)
                term.view(term.read(view(provider_rows=[row]), tz=TZ))
        self.assertEqual([term.a_time(t) for t in (1e9, 1e300, -1.0, None, float('nan'))], [1e9, None, None, None,
                                                                                         None])

    def test_numbers_too_old_to_trust_are_grey(self):
        r = other(view(provider_rows=[codex(state='unknown')]))[0]
        fills = [op for op in menubar.row_ops(r) if op[0] == 'box' and op[-1] not in ('track', 'hover')]
        self.assertEqual({op[-1] for op in fills}, {'fill'})


class TheDrawing(unittest.TestCase):
    def test_the_same_shapes_as_a_claude_row_with_the_plan_in_grey(self):
        s = view(provider_rows=[codex()])
        r = other(s)[0]
        ops = menubar.row_ops(r, hover=True)
        self.assertEqual(ops[0], ('box', 6.0, 0.0, 348.0, 72.0, 8.0, 'hover'))     # an account's row's height
        self.assertEqual(ops[1], ('pair', 16.0, 8.0, 328.0, 20.0,
                                  [(EMAIL, 13.0, 500, 'ink'), ('  Plus', 12.0, 400, 'ink2')],
                                  [[('Continue here', 12.0, 500, 'accent')]], 8.0))
        claude = menubar.row_ops(next(x for x in menubar.rows(s, TZ) if x['email'] == 'ana@harbor.example'))
        tracks = [op[1:6] for op in ops if op[0] == 'box' and op[-1] == 'track']
        self.assertEqual(tracks, [op[1:6] for op in claude if op[0] == 'box' and op[-1] == 'track'])
        self.assertEqual([op[0] for op in ops].count('pair'), 1)     # its name line only: no reset line under the bars
        over = other(view(provider_rows=[codex(windows=[window('weekly', 97.0, WEEK_AT)])]))[0]
        self.assertIn('red', [op[-1] for op in menubar.row_ops(over) if op[0] == 'box'])

    def test_a_long_email_keeps_its_room_and_the_age_stays_in_the_label(self):
        # Room by numbers over an hour old says how old, beside Continue here. When that would cut a long email
        # short, the age is left to the label (VoiceOver and the hover) and the email keeps its room.
        for email, shown in (('ana@x.example', True), (EMAIL, True),
                             ('maya.rodriguez-fernandez@northwind-analytics.example', False)):
            with self.subTest(email=email):
                r = other(view(provider_rows=[codex(email=email, read_at=NOW - 3 * 3600, fresh=False)]))[0]
                self.assertEqual(r['marks'], [('3 hours ago', 'next'), ('Continue here', 'use')])
                (pair,) = [op for op in menubar.row_ops(r) if op[0] == 'pair']
                pieces = [piece[0][0] for piece in pair[6]]
                self.assertEqual(pieces, ['3 hours ago', 'Continue here'] if shown else ['Continue here'])
                self.assertIn(', checked 3 hours ago, ', menubar.row_label(r))

    def test_what_voiceover_says(self):
        r = other(view(provider_rows=[codex()]))[0]
        self.assertEqual(menubar.row_label(r), 'Continue here in ana@northwind.example, OpenAI, Plus, '
                                               'checked just now, 5-hour 8%, weekly 21%')

    def test_voiceover_says_what_its_last_cell_shows(self):
        # Whatever the last cell shows is said: its reason, Out until a time (or Out), In use, Next and Continue here.
        cases = ((codex(why='its folder stays on Claude', can=False), ('Its folder stays on Claude', 'next'),
                  'ana@northwind.example, OpenAI, Plus, its folder stays on Claude, checked just now, 5-hour 8%, '
                  'weekly 21%'),
                 (codex(state='out', back_at=FIVE_AT, can=False), ('Out until 7:13 PM', 'next'),
                  'ana@northwind.example, OpenAI, Plus, out until 7:13 PM, checked just now, 5-hour 8%, weekly 21%'),
                 (codex(state='out', back_at=None, can=False), ('Out', 'next'),
                  'ana@northwind.example, OpenAI, Plus, out, checked just now, 5-hour 8%, weekly 21%'),
                 (codex(in_use=True), ('In use', 'in-use'),
                  'ana@northwind.example, OpenAI, Plus, in use, checked just now, 5-hour 8%, weekly 21%'),
                 (codex(next=True), ('Continue here', 'use'),
                  'Continue here in ana@northwind.example, OpenAI, Plus, next, checked just now, 5-hour 8%, '
                  'weekly 21%'))
        for row, mark, said in cases:
            with self.subTest(said=said):
                r = other(view(provider_rows=[row]))[0]
                self.assertEqual(r['marks'][-1], mark)
                self.assertEqual(menubar.row_label(r), said)


class TheMenu(unittest.TestCase):
    def test_after_the_claude_rows_before_auto_switch_under_a_heading_for_each_maker(self):
        items = menubar.menu(view(provider_rows=[codex()]), tz=TZ)
        self.assertEqual([x[0] if x[0] == 'row' else x for x in items[:9]],
                         [('line', 'Claude'), 'row', 'row', 'row', 'row', 'row', ('line', 'OpenAI'), 'row',
                          ('separator',)])
        self.assertEqual([x[1]['slot'] for x in items if x[0] == 'row'], [1, 2, 3, 4, 5, 6])
        self.assertEqual(items[8:], menubar.menu(view(), tz=TZ)[5:])
        self.assertNotIn(('line', 'Claude'), menubar.menu(view(), tz=TZ))

    def test_sign_in_to_all_directly_under_the_claude_rows(self):
        due = {'state': 'idle', 'ended': ['ana@lab.example'], 'week': []}
        items = menubar.menu(view(provider_rows=[codex()], signins=due), tz=TZ)
        self.assertEqual([x[0] if x[0] == 'row' else x for x in items[:10]],
                         [('line', 'Claude'), 'row', 'row', 'row', 'row', 'row', ('do', term.SIGNIN_ALL, 'signinall'),
                          ('line', 'OpenAI'), 'row', ('separator',)])
        self.assertEqual(items[9:], menubar.menu(view(signins=due), tz=TZ)[6:])

    def test_two_seats_one_heading_two_rows_each_its_own_key(self):
        two = [codex(), codex(email='ana@ivy.example', plan='Pro', chat=None, can=False)]
        items = menubar.menu(view(provider_rows=two), tz=TZ)
        self.assertEqual([x for x in items if x[0] == 'line'], [('line', 'Claude'), ('line', 'OpenAI')])
        got = [x[1] for x in items if x[0] == 'row' and x[1].get('provider')]
        self.assertEqual([(r['slot'], r['email'], r['action']) for r in got],
                         [(6, EMAIL, 'continue'), (7, 'ana@ivy.example', None)])
        self.assertEqual(len({menubar.row_key(r) for r in got}), 2)

    def test_a_click_asks_the_page_to_continue_and_nothing_else(self):
        r = other(view(provider_rows=[codex()]))[0]
        self.assertEqual(menubar.click(r), ('/api/continue', {'provider': 'codex', 'mark': 'a1b2c3d4'}))
        self.assertIsNone(menubar.click(other(view(provider_rows=[codex(chat=None, can=False)]))[0]))

    def test_same_email_two_rows_each_its_own_key(self):
        rows = menubar.rows(view(provider_rows=[codex()]), TZ)
        keys = [menubar.row_key(r) for r in rows]
        self.assertEqual(len(set(keys)), 6)
        self.assertEqual(keys[-1], 'provider:codex:6')
        self.assertEqual(keys[0], 'ana@northwind.example')
        a = menubar.menu(view(provider_rows=[codex()]), tz=TZ)
        b = menubar.menu(view(provider_rows=[codex(chat=None, can=False)]), tz=TZ)
        self.assertNotEqual(menubar.shape(a), menubar.shape(b))         # Continue here came or went: made again
        c = copy.deepcopy(view(provider_rows=[codex()]))
        c['provider_rows'][0]['windows'][1]['used_pct'] = 22.0
        self.assertEqual(menubar.shape(a), menubar.shape(menubar.menu(c, tz=TZ)))

    def test_the_demo(self):
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = True
        from switchboard import demo
        rows = [x[1] for x in menubar.menu(demo.view()) if x[0] == 'row']
        self.assertEqual([(r['email'], r['plan'], r['action']) for r in rows if r.get('provider')],
                         [('maya@northwind.example', 'Plus', 'continue')])


class TheAnswer(tm.Sandbox):
    """Continue here answers in the menu's own note lines, where a failed click is said, for as long: nothing floats,
    nothing is drawn outside the menu. The real menu bar class, on stand-in AppKit modules (nothing is drawn)."""

    OPEN = 'Codex is open with your chat "Upload retries" in its message box. Press send to carry on.'

    def setUp(self):
        super().setUp()
        self.calls = queue.Queue()
        p = mock.patch.dict(sys.modules, tm.stand_in_pyobjc(self.calls))
        p.start()
        self.addCleanup(p.stop)
        menubar._UI.clear()
        self.addCleanup(menubar._UI.clear)
        self.bar = menubar._ui()['MenuBar'].alloc().init()
        self.bar.status, self.bar.menu = mock.MagicMock(), mock.MagicMock()
        self.bar.watch.took(view(provider_rows=[codex()]), None, time.monotonic(), time.time())    # a reading
        self.bar.items = menubar.menu(view(provider_rows=[codex()]), tz=TZ)

    def press(self, answer):
        """Press Codex's row; the page answers `answer` (what post() gives). The note, line by line."""
        posted = []
        with mock.patch.object(menubar, 'post', lambda path, body: posted.append((path, body)) or answer):
            self.bar.use_(mock.Mock(representedObject=lambda: 'provider:codex:6'))
            f, args = self.calls.get(timeout=15)        # handed back to AppKit's main thread
            f(*args)
        self.assertEqual(posted, [('/api/continue', {'provider': 'codex', 'mark': 'a1b2c3d4'})])
        words, until = self.bar.note
        self.assertAlmostEqual(until - time.monotonic(), menubar.NOTE_FOR, delta=5)
        lines = words.split('\n')
        self.assertEqual(self.bar.items[:len(lines)], [('note', x) for x in lines])     # at the top, at once
        self.assertNotEqual(self.bar.items[len(lines)][0], 'note')
        return lines

    def test_it_went_every_line_of_the_answer_as_the_page_says_it(self):
        self.assertEqual(self.press((True, {'said': [self.OPEN]})),
                         ['Codex is open with your chat "Upload retries" in its message box. Press',
                          'send to carry on.'])
        working = 'That chat is still working: its newest part may not be in the note.'
        got = self.press((True, {'said': [working, self.OPEN]}))
        self.assertEqual(' '.join(got), working + ' ' + self.OPEN)              # the page's words: said.join(' ')
        self.assertTrue(all(len(x) <= menubar.NOTE_W for x in got), got)

    def test_out_and_part_read_the_warnings_first_and_nothing_dropped(self):
        said = ['Codex is out until Sun 5 PM; it opens all the same, since you picked it.',
                'Only part of that chat could be read, so the note holds less of it.',
                'Codex is open with part of your chat "Upload retries" in its message box. Press send to carry on.']
        got = self.press((True, {'said': said}))
        self.assertEqual(' '.join(got), ' '.join(said))
        self.assertTrue(got[0].startswith('Codex is out until Sun 5 PM'))
        self.assertIn('part of your chat', ' '.join(got))
        self.assertTrue(all(len(x) <= menubar.NOTE_W for x in got), got)

    def test_no_app_on_this_mac_the_reason_then_the_line_to_type_whole(self):
        said = {'said': ["Codex's app is not on this Mac. To carry on, run this in a terminal:"],
                'run': "cd ~/code/site && switchboard 6"}
        self.assertEqual(self.press((True, said)),
                         ["Codex's app is not on this Mac. To carry on, run this in a terminal:",
                          'cd ~/code/site && switchboard 6'])
        far = "cd '/Users/ana/code/a folder with a name far longer than one line of the menu' && switchboard 6"
        self.assertGreater(len(far), menubar.NOTE_W)
        got = self.press((True, {'said': ["Codex's app did not open. To carry on, run this in a terminal:"],
                                 'run': far}))
        self.assertEqual(got, ["Codex's app did not open. To carry on, run this in a terminal:", far])

    def test_refused_its_words(self):
        self.assertEqual(self.press((False, 'A newer chat is there now. Look again.')),
                         ['A newer chat is there now. Look again.'])
        self.assertEqual(self.press((False, menubar.NOT_RUNNING)), [menubar.NOT_RUNNING])
        self.assertEqual(self.press((False, menubar.UNREADABLE)), ["The page's answer could not be read"])
        self.assertEqual(self.press((True, 'not an answer')), ["The page's answer could not be read"])
        self.assertEqual(self.press((True, {'said': []})), ["The page's answer could not be read"])
        never = 'You said chats in site never go to Codex. switchboard allow codex, run there, lifts that.'
        got = self.press((False, never))
        self.assertEqual(' '.join(got), never)
        self.assertTrue(all(len(x) <= menubar.NOTE_W for x in got), got)


class TheNoteLines(unittest.TestCase):
    """The menu's note: one line of the menu for each line of the note, in order, at the top."""

    def test_each_line_of_a_note_is_a_note_line_of_the_menu(self):
        items = menubar.menu(view(provider_rows=[codex()]), 'First line\nsecond line', tz=TZ)
        self.assertEqual(items[:2], [('note', 'First line'), ('note', 'second line')])
        self.assertNotEqual(items[2][0], 'note')
        self.assertEqual(menubar.menu(view(), "Couldn't switch: no.", tz=TZ)[:2][0], ('note', "Couldn't switch: no."))



class TheS(unittest.TestCase):
    def test_when_another_assistant_is_next_the_s_says_what_the_notification_says(self):
        # Every Claude account at your lines and Codex's row with room is Next (comeup.with_next): the red dot stays
        # (a press is wanted) and the words are the one notification's, with the number to press, not "no account
        # has room". With no row that can take over, they are as before.
        from switchboard import comeup
        from test_comeup import state
        setup = {'engine': True, 'accounts': 2}       # the page's first run is done: the menu draws the accounts
        s = comeup.with_next(state(active='ana@harbor.example', setup=setup))
        (row,) = s['provider_rows']
        self.assertIs(row['next'], True)
        said = comeup.press_words(s, row)
        self.assertEqual(said, 'Both Claude accounts are at your lines. Codex has room. In a terminal in your project '
                               'folder, type switchboard and press 3.')
        r = menubar.reading(s)
        self.assertEqual(r['dot'], 'red')
        self.assertTrue(r['label'].startswith('Switchboard: ana@harbor.example, 5-hour 93%, weekly 40%. '), r['label'])
        self.assertTrue(r['label'].endswith('. ' + said), r['label'])
        self.assertNotIn('no account has room', r['label'])
        r = menubar.reading(comeup.with_next(state(active='ana@harbor.example', setup=setup, provider_rows=[])))
        self.assertEqual(r['dot'], 'red')
        self.assertTrue(r['label'].endswith(', no account has room'), r['label'])


if __name__ == '__main__':
    unittest.main()
