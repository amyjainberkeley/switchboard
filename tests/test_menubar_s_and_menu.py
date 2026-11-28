import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The menu bar item is Switchboard's S, drawn as shapes (menubar.item_ops), with a small dot at its top right only when
# the person should look: orange when the account in use is at or over a switch line, red when something needs them.
# Its menu is a row per account (the whole email, In use, Next or Use, the bars with their %; a plan with no 5-hour
# limit says "No 5-hour limit" in the 5-hour bar row's place), then Auto-switch, Add account and Open Switchboard, and
# no Quit in any state. Made-up accounts only; nothing here draws or runs AppKit.
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import menubar
from test_menubar import NOW, TZ, acct, find, row_of, view, w

KINDS = ('stroke', 'dot', 'clear')


def states():
    """Every state the item can be in, by name: (state, status)."""
    over = view()
    find(over, 'ana@mail.example')['usage']['weekly']['pct'] = 96
    ended = view()
    find(ended, 'ana@harbor.example')['needs_login'] = True
    full = view()
    for a in full['accounts']:
        a['usage']['weekly']['pct'] = 99
    stale = view()
    unknown = view()
    find(unknown, 'ana@mail.example')['trusted'] = False
    return {
        'live': (view(), None),
        'line': (over, None),
        'signin-ended': (ended, None),
        'no-room': (full, None),
        'no-account-in-use': (view(active='someone@else.example'), None),
        'no-accounts-yet': (view(accounts=[]), None),
        'not-running': (None, {'kind': 'down', 'stale': False, 'line': "Switchboard isn't running"}),
        'first-refusal': (None, None),
        'not-running-old-numbers': (stale, {'kind': 'down', 'stale': True, 'line': "Switchboard isn't running"}),
        'busy': (None, {'kind': 'busy', 'stale': False, 'line': 'Switchboard is busy'}),
        'old-numbers': (stale, {'kind': 'busy', 'stale': True, 'line': 'Not updated since 3:20 PM'}),
        'unknown': (unknown, None),
    }


class TheS(unittest.TestCase):
    def test_each_state_is_the_s_with_a_dot_only_when_the_person_should_look(self):
        want = {'live': None, 'line': 'orange', 'signin-ended': 'red', 'no-room': 'red', 'no-account-in-use': 'red',
                'no-accounts-yet': 'red', 'not-running': 'red', 'not-running-old-numbers': 'red', 'busy': None,
                'old-numbers': None, 'unknown': None, 'first-refusal': None}
        for name, (state, status) in states().items():
            with self.subTest(state=name):
                r = menubar.reading(state, status)
                self.assertEqual(r['dot'], want[name])
                ops = menubar.item_ops(r)
                dots = [op for op in ops if op[0] == 'dot' and op[-1] in ('orange', 'red')]
                self.assertEqual([op[-1] for op in dots], [want[name]] if want[name] else [])

    def test_no_state_draws_a_bar_a_number_or_words(self):
        for name, (state, status) in states().items():
            with self.subTest(state=name):
                ops = menubar.item_ops(menubar.reading(state, status))
                self.assertEqual(sorted(set(op[0] for op in ops) - set(KINDS)), [])
                self.assertNotIn('box', [op[0] for op in ops])
                self.assertNotIn('text', [op[0] for op in ops])

    def test_the_s_is_the_marks_path_with_its_two_round_ends(self):
        ops = menubar.item_ops(menubar.reading(view()))
        self.assertEqual([op[0] for op in ops], ['stroke', 'dot', 'dot'])
        stroke = ops[0]
        self.assertEqual([c[0] for c in stroke[1]], ['M', 'L', 'A', 'L', 'A', 'L'])
        k = menubar.IMAGE_W / (menubar.MARK_VIEW[1] - menubar.MARK_VIEW[0])
        self.assertAlmostEqual(stroke[2], 2.2 * k)                                 # docs/logo.svg: stroke-width 2.2
        self.assertEqual(stroke[3], 'ink')
        start, end = ops[2], ops[1]                                                # the top-right end, the bottom-left
        self.assertAlmostEqual(start[1], (16.3 - menubar.MARK_VIEW[0]) * k)
        self.assertAlmostEqual(start[2], (7 - menubar.MARK_VIEW[0]) * k)
        self.assertAlmostEqual(end[1], (7.55 - menubar.MARK_VIEW[0]) * k)
        self.assertAlmostEqual(end[2], (17 - menubar.MARK_VIEW[0]) * k)
        for op in ops:                                                            # all of it inside the image
            for x, y in points(op):
                self.assertTrue(0 <= x <= menubar.IMAGE_W and 0 <= y <= menubar.IMAGE_H, (op, x, y))

    def test_a_dot_is_cut_clear_of_the_s_then_drawn_bigger_in_its_colour(self):
        r = menubar.reading(*states()['line'])
        ops = menubar.item_ops(r)
        self.assertEqual([op[0] for op in ops], ['stroke', 'dot', 'clear', 'dot'])
        clear, dot = ops[2], ops[3]
        self.assertEqual((clear[1], clear[2]), (dot[1], dot[2]))
        self.assertGreater(clear[3], dot[3])
        plain = menubar.item_ops(menubar.reading(view()))[2]
        self.assertGreater(dot[3], plain[3])
        self.assertEqual((plain[1], plain[2]), (dot[1], dot[2]))                  # the S's own top-right end

    def test_numbers_not_current_draw_the_s_dimmed_never_coloured(self):
        for name in ('busy', 'old-numbers', 'unknown', 'first-refusal'):
            ops = menubar.item_ops(menubar.reading(*states()[name]))
            self.assertEqual(set(op[-1] for op in ops if op[0] != 'clear'), {'ink50'}, name)

    def test_no_reading_yet_or_one_refusal_is_dimmed_not_red(self):
        # At login the item and the page start together and the first reading is often refused. The watch says the
        # page is down only after DOWN_AFTER refusals in a row; until then the S is dimmed, with no dot.
        w8 = menubar.Watch()
        self.assertEqual(menubar.reading(*w8.view(100.0)), menubar.reading(None))
        self.assertIsNone(menubar.reading(None)['dot'])
        self.assertTrue(menubar.reading(None)['dim'])
        mono = 100.0
        for n in range(1, menubar.DOWN_AFTER + 1):
            w8.took(None, 'down', mono, mono)
            r = menubar.reading(*w8.view(mono))
            self.assertEqual(r['dot'], 'red' if n >= menubar.DOWN_AFTER else None, n)
            mono += 15.0

    def test_the_item_is_no_wider_than_the_two_bars_were(self):
        self.assertLessEqual(menubar.ITEM_W, 34.0)
        self.assertLessEqual(menubar.IMAGE_W, menubar.ITEM_W)
        self.assertEqual(menubar.IMAGE_W, menubar.IMAGE_H)

    def test_what_voiceover_and_the_tooltip_say(self):
        self.assertEqual(menubar.reading(view())['label'], 'Switchboard: ana@mail.example, weekly 27%')
        self.assertEqual(menubar.reading(*states()['line'])['label'],
                         'Switchboard: ana@mail.example, weekly 96%, at its switch line')
        self.assertEqual(menubar.reading(*states()['signin-ended'])['label'],
                         'Switchboard: ana@mail.example, weekly 27%, a sign-in has ended')
        self.assertEqual(menubar.reading(*states()['no-room'])['label'],
                         'Switchboard: ana@mail.example, weekly 99%, no account has room')
        self.assertEqual(menubar.reading(None)['label'], "Switchboard isn't running")
        self.assertEqual(menubar.reading(*states()['old-numbers'])['label'],
                         'Switchboard: ana@mail.example, weekly 27% · Not updated since 3:20 PM')

    def test_an_account_switchboard_does_not_hold_never_counts_as_room_or_as_ended(self):
        s = view()
        for a in s['accounts']:
            a['usage']['weekly']['pct'] = 99
        s['accounts'].append(acct(6, 'ana@outside.example', weekly=w(1), unmanaged=True))
        self.assertEqual(menubar.reading(s)['dot'], 'red')
        s = view()
        s['accounts'].append(acct(6, 'ana@outside.example', weekly=w(1), unmanaged=True, needs_login=True))
        self.assertIsNone(menubar.reading(s)['dot'])

    def test_the_pages_list_of_ended_sign_ins_counts_too(self):
        s = view(signins={'ended': ['ana@lab.example'], 'week': [], 'state': 'idle'})
        self.assertEqual(menubar.reading(s)['dot'], 'red')
        s = view(signins={'ended': [], 'week': ['ana@lab.example'], 'state': 'idle'})
        self.assertIsNone(menubar.reading(s)['dot'])             # ends this week: it still works, no dot


def points(op):
    if op[0] in ('dot', 'clear'):
        _, x, y, r = op[:4]
        return [(x - r, y - r), (x + r, y + r)] if op[0] == 'dot' else []
    out = []
    for c in op[1]:
        if c[0] in ('M', 'L'):
            out.append((c[1], c[2]))
        else:
            _, cx, cy, r = c[:4]
            out += [(cx - r, cy - r), (cx + r, cy + r)]
    return out


class TheRows(unittest.TestCase):
    def test_a_row_holds_the_email_its_one_word_and_its_bars_and_nothing_else(self):
        rows = dict((r['email'], r) for r in menubar.rows(view(), TZ))
        self.assertEqual(rows['ana@mail.example']['marks'], [('In use', 'in-use')])
        self.assertEqual(rows['ana@lab.example']['marks'], [('Next', 'next')])
        self.assertEqual(rows['ana@northwind.example']['marks'], [('Use', 'use')])
        for r in rows.values():
            self.assertNotIn('foot', r)
            self.assertNotIn('models', r)
            self.assertNotIn('pace', r)
            self.assertNotIn('age', r)

    def test_what_a_row_draws(self):
        s = view()
        find(s, 'ana@lab.example')['usage']['session'] = w(0)
        for email in [a['email'] for a in s['accounts']]:
            r = row_of(s, email)
            ops = menubar.row_ops(r, hover=True)
            words = [run[0] for op in ops if op[0] == 'text' for run in op[5]]
            words += [run[0] for op in ops if op[0] == 'pair' for run in op[5]]
            words += [run[0] for op in ops if op[0] == 'pair' for piece in op[6] for run in piece]
            bars = [x for x in (r['five'], r['week']) if x]
            want = [x['label'] for x in bars] + [menubar.pct_text(x) for x in bars]
            want += [r['no_five']] if r['no_five'] else []                    # words in the 5-hour place
            self.assertEqual(sorted(words), sorted(want + [email] + [m[0] for m in r['marks']]), email)
            self.assertEqual(len([op for op in ops if op[0] == 'pair']), 1)                  # no reset line under it

    def test_no_when_it_was_checked_no_refill_line_no_models_week(self):
        s = view()
        find(s, 'ana@lab.example')['usage']['session'] = w(31, '2026-11-13T17:40:00-08:00')
        find(s, 'ana@mail.example')['live'] = True
        for r in menubar.rows(s, TZ):
            text = repr(menubar.row_ops(r))
            for gone in ('min ago', 'just now', 'Now', 'Resets', 'resets', 'Not started', 'Fable', 'At this pace'):
                self.assertNotIn(gone, text, (r['email'], gone))

    def test_the_rows_height_is_its_email_and_its_bars(self):
        self.assertEqual(menubar.ROW_H, 72.0)
        self.assertEqual(menubar.row_height(row_of(view(), 'ana@harbor.example')), 72.0)       # 5-hour and weekly
        self.assertEqual(menubar.row_height(row_of(view(), 'ana@lab.example')), 72.0)          # no 5-hour limit: words
        never = view()
        find(never, 'ana@lab.example')['usage'] = None
        self.assertEqual(menubar.row_height(row_of(never, 'ana@lab.example')), 54.0)          # never read: weekly alone

    def test_an_ended_sign_in_keeps_its_words_and_its_action(self):
        s = view()
        a = find(s, 'ana@harbor.example')
        a.update(needs_login=True, signin_ends_at=NOW - 3600)
        r = row_of(s, 'ana@harbor.example')
        self.assertEqual(r['marks'], [('ended by today', 'next'), ('Sign in again', 'use')])
        self.assertEqual(r['action'], 'signin')
        soon = view()
        find(soon, 'ana@lab.example')['signin_ends_at'] = NOW + 2 * 86400
        r = row_of(soon, 'ana@lab.example')
        self.assertEqual(r['marks'], [('Sign-in ends by Sun', 'next'), ('Next', 'next')])
        items = menubar.menu(soon, tz=TZ)
        self.assertIn(('do', 'Sign in again as ana@lab.example', 'signin'), items)

    def test_what_voiceover_says_for_a_row(self):
        self.assertEqual(menubar.row_label(row_of(view(), 'ana@lab.example')),
                         'Use ana@lab.example, next, no 5-hour limit, weekly 65%')
        self.assertEqual(menubar.row_label(row_of(view(), 'ana@harbor.example')),
                         'Use ana@harbor.example, 5-hour 0%, weekly 17%')
        self.assertEqual(menubar.row_label(row_of(view(), 'ana@mail.example')),
                         'ana@mail.example, in use, no 5-hour limit, weekly 27%')


class TheMenu(unittest.TestCase):
    def all_menus(self):
        out = {}
        for name, (state, status) in states().items():
            out[name] = menubar.menu(state, status=status, tz=TZ)
        first = view(accounts=[acct(None, 'new@mail.example', unmanaged=True, active=True)],
                     setup={'engine': True, 'accounts': False})
        out['first-run'] = menubar.menu(first)
        out['no-engine'] = menubar.menu(view(setup={'engine': False}))
        out['before-a-reading'] = menubar.FIRST_MENU
        return out

    def test_no_menu_in_any_state_has_quit(self):
        for name, items in self.all_menus().items():
            with self.subTest(state=name):
                words = [x[1] for x in items if len(x) > 1 and isinstance(x[1], str)]
                self.assertFalse([x for x in words if 'Quit' in x], words)
                self.assertNotIn('quit', [x[2] for x in items if x[0] == 'do'])

    def test_rows_then_auto_switch_add_account_and_open_switchboard(self):
        items = menubar.menu(view(), tz=TZ)
        self.assertEqual([x[1]['slot'] for x in items if x[0] == 'row'], [1, 2, 3, 4, 5])
        self.assertEqual(items[5:], [('separator',), ('auto', True, 'at 90% 5-hour · 95% weekly', True),
                                     ('separator',), ('do', 'Add account', 'add'),
                                     ('do', 'Open Switchboard', 'open')])

    def test_the_page_not_running_keeps_start_and_the_first_run_offers_add_account(self):
        self.assertEqual(menubar.menu(None), [('line', "Switchboard isn't running"), ('do', 'Start', 'start')])
        busy = menubar.menu(None, status={'kind': 'busy', 'line': 'Switchboard is busy'})
        self.assertEqual(busy, [('line', 'Switchboard is busy'), ('separator',), ('do', 'Open Switchboard', 'open')])
        first = view(accounts=[acct(None, 'new@mail.example', unmanaged=True, active=True)],
                     setup={'engine': True, 'accounts': False})
        self.assertEqual(menubar.menu(first), [('line', 'Claude Code is on new@mail.example'), ('separator',),
                                               ('do', 'Add account', 'add'), ('do', 'Open Switchboard', 'open')])
        self.assertEqual(menubar.menu(view(setup={'engine': False})),
                         [('line', 'Finish installing Switchboard'), ('separator',),
                          ('do', 'Open Switchboard', 'open')])
        self.assertEqual(menubar.FIRST_MENU, [('do', 'Open Switchboard', 'open')])

    def test_the_line_when_something_is_off_stays(self):
        s = view()
        s['auto'].update(decided='Auto-switch last decided 14 minutes ago.')
        items = menubar.menu(s)
        self.assertIn(('line', 'Auto-switch last decided 14 minutes ago.'), items)
        self.assertLess(items.index(('line', 'Auto-switch last decided 14 minutes ago.')),
                        items.index(('do', 'Add account', 'add')))

    def test_sign_in_to_all_stays(self):
        s = view(signins={'ended': ['ana@harbor.example'], 'week': [], 'state': 'idle'})
        find(s, 'ana@harbor.example')['needs_login'] = True
        self.assertIn(('do', 'Sign in to all', 'signinall'), menubar.menu(s))


class AddAccount(unittest.TestCase):
    def test_it_opens_the_page_at_its_add_form(self):
        # A menu can't ask for an email: the page's own Add form does, and starts the browser sign-in from there.
        self.assertEqual(menubar.add_url(), 'http://127.0.0.1:%d/#accounts/add' % menubar.core.port())

    def test_the_page_opens_its_add_form_at_that_address(self):
        with open(os.path.join(HERE, '..', 'switchboard', 'web', 'index.html'), encoding='utf-8') as f:
            html = f.read()
        route = html[html.index('function route() {'):html.index('function goTo(')]
        self.assertIn("sec === 'accounts' && sub === 'add'", route)
        self.assertIn('adding = true', route)


if __name__ == '__main__':
    unittest.main()
