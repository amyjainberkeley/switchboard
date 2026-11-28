import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The page's board with another provider's row (the state's provider_rows, one row per account): after the Claude
# accounts, under a small heading for each maker ("Claude", "OpenAI") that is there only when such a row is; its limits
# one under another in the same cells as an account's (5-hour over weekly, never side by side). A 5-hour window with no
# number has no line, as on the account rows (a hidden cell, and why on the weekly line's hover); a window already
# refilled reads 0%; another window ("3-day") is the week line when there is no weekly one; no windows at all say
# "Numbers not known" and draw no bar. Its last cell, first match wins: In use, why it cannot be continued to, Out
# (until when, unless a limit line already says it), Continue here (with Next), else nothing. Continue here posts
# /api/continue with the provider and its chat's mark and nothing else; the line to run, when the answer gives one, is
# under the board with Copy. Under the board, quietly, only what the rows do not say: an assistant added with no row
# and why, one on this Mac not added (the command that adds it, with Copy), and the command to carry a chat on only
# when there is no row at all. With no other provider, or with either key missing or damaged, the board's rows are
# exactly what the Claude accounts alone draw.
#
# No browser: the page's own script runs in Node with the browser's objects stubbed, and these tests call its
# functions (rowsHTML, belowHTML, the Continue here action) on a made-up state. Skipped without Node.
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
sys.path.insert(0, ROOT)

NODE = shutil.which('node') or next((p for p in ('/opt/homebrew/bin/node', '/usr/local/bin/node') if os.path.exists(p)),
                                    None)
NOW = 1794699085.0                      # Sat Nov 14 2026, 3:31 PM in Los Angeles
BACK = 1794790800.0                     # Sun Nov 15 2026, 5 PM there: "Sun 5 PM"
EMAIL = 'ana@northwind.example'

# Everything the script touches outside itself answers with a stand-in that takes any call; fetch is recorded.
HARNESS = r"""
const H = {get(t, k) {
             if (k in t) return t[k];
             if (k === Symbol.iterator) return function* () {};
             if (k === Symbol.toPrimitive) return () => '';
             return t[k] = any(); },
           apply() { return any(); }, construct() { return any(); }, set(t, k, v) { t[k] = v; return true; }};
function any() { return new Proxy(function () {}, H); }
const posted = [];
globalThis.window = globalThis;
globalThis.document = any();
globalThis.location = {hash: '', search: '', pathname: '/', host: 'localhost'};
globalThis.history = {replaceState() {}, pushState() {}};
globalThis.CSS = {escape: (s) => s};
globalThis.setInterval = () => 0;
globalThis.setTimeout = () => 0;
globalThis.fetch = (path, opts) => {
  posted.push([path, opts && opts.body ? JSON.parse(opts.body) : null, opts && opts.headers]);
  return new Promise(() => {});
};
globalThis.addEventListener = () => {};
"""


def page_script():
    with open(os.path.join(ROOT, 'switchboard', 'web', 'index.html'), encoding='utf-8') as f:
        text = f.read()
    main = [b for b in re.findall(r'<script>(.*?)</script>', text, re.S) if "'use strict'" in b][0]
    end = 'route();\nload();\n})();'
    assert main.rstrip().endswith(end), 'the page script ends otherwise'
    expose = ('globalThis.__page = {set: (x) => { S = x; }, rowsHTML, belowHTML, accRow, bySlot, nextEmail, actions,'
              ' top: typeof makerTopHTML === "function" ? makerTopHTML : () => "",'
              ' more: typeof otherRowsHTML === "function" ? otherRowsHTML : () => ""};\n})();')
    return main.rstrip()[:-len(end)] + expose


def page_css():
    """The page's own style sheet."""
    with open(os.path.join(ROOT, 'switchboard', 'web', 'index.html'), encoding='utf-8') as f:
        return max(re.findall(r'<style>(.*?)</style>', f.read(), re.S), key=len)


def run(js, state):
    """The page's script, then js (with `page`, `state` and `posted` in reach); what js prints, as JSON."""
    src = HARNESS + page_script() + '\nconst page = globalThis.__page, state = %s;\npage.set(state);\n%s\n' % (
        json.dumps(state), js)
    folder = tempfile.mkdtemp(prefix='sb-page-')
    try:
        path = os.path.join(folder, 'page.js')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(src)
        got = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60,
                             env={'TZ': 'America/Los_Angeles', 'PATH': '/usr/bin:/bin', 'HOME': folder})
    finally:
        shutil.rmtree(folder, True)
    if got.returncode != 0:
        lines = got.stderr.strip().splitlines()
        said = [ln for ln in lines if 'Error' in ln] or lines or ['?']
        raise AssertionError('node: %s' % said[0])
    return json.loads(got.stdout)


def account(slot, email, five, week, **more):
    return dict({'email': email, 'name': email, 'alias': '', 'slot': slot, 'active': False, 'needs_login': False,
                 'disabled': False, 'unmanaged': False, 'trusted': True, 'plan': 'max', 'plan_line': 'Max 20x',
                 'usage': {'session': {'pct': five, 'resets': '2026-11-13T23:40:00+00:00'},
                           'weekly': {'pct': week, 'resets': '2026-11-16T20:00:00+00:00'}, 'scoped': []},
                 'usage_at': NOW - 30, 'checked_at': NOW - 30}, **more)


def state(**more):
    return dict({'now': NOW, 'active': 'maya@harbor.example', 'next': 'maya@ivy.example',
                 'auto': {'enabled': True, 'on': True, 'session_at': 90, 'weekly_at': 95, 'model_at': 95},
                 'accounts': [account(1, 'maya@harbor.example', 40, 50, active=True),
                              account(2, 'maya@ivy.example', 10, 20)], 'provider_rows': []}, **more)


FIVE = {'label': '5-hour', 'used_pct': 8.0, 'resets_at': NOW + 3.7 * 3600, 'line': 90.0}
WEEK = {'label': 'weekly', 'used_pct': 21.0, 'resets_at': NOW + 5.6 * 86400, 'line': 95.0}


def codex(**more):
    """One row of the state's provider_rows, every field of it (DESIGN.md D3)."""
    return dict({'provider': 'codex', 'name': 'Codex', 'vendor': 'OpenAI', 'email': EMAIL, 'plan': 'Plus',
                 'windows': [dict(FIVE), dict(WEEK)], 'read_at': NOW - 40, 'state': 'room', 'back_at': None,
                 'fresh': True, 'why': None, 'can': True, 'app': True, 'in_use': False, 'next': False,
                 'chat': {'name': 'Upload retries', 'mark': 'a1b2c3d4'}}, **more)


def board(entries=(), add=(), carry=False):
    """The state's providers: public's board (providers/view.py), with Claude first."""
    claude = {'id': 'claude', 'name': 'Claude', 'vendor': 'Anthropic', 'app': 'Claude Code', 'added': True,
              'account_list': True, 'state': 'room', 'fit_why': None, 'seats': []}
    return {'v': 1, 'now': NOW, 'order': ['claude'] + [e['id'] for e in entries],
            'providers': [claude] + list(entries), 'add': list(add), 'carry': carry}


def entry(**more):
    """Codex on the board: added, not the account list."""
    return dict({'id': 'codex', 'name': 'Codex', 'vendor': 'OpenAI', 'app': 'Codex', 'added': True,
                 'account_list': False, 'state': 'signed_out', 'fit_why': 'not signed in', 'seats': []}, **more)


OFFER = {'id': 'codex', 'name': 'Codex', 'app': 'Codex', 'version': '0.169.1'}


ROWS = 'console.log(JSON.stringify(page.top() + page.rowsHTML() + page.more()));'


def cells(html, provider='codex'):
    """The provider row's cells, in order: [(class, text)]."""
    row = re.search(r'<div class="ag arow other[^"]*" role="row" data-provider="%s">(.*?)</div></div>$' % provider,
                    html, re.S)
    assert row, html[-400:]
    return row.group(1)


def row_of(**more):
    """The row drawn for codex(**more), alone."""
    return cells(run(ROWS, state(provider_rows=[codex(**more)])))


def last_cell(row):
    """What the row's last cell holds."""
    return re.search(r'<div class="c c-state" role="cell">(.*)$', row, re.S).group(1)


def lines(row):
    """Each limit line the row draws: (its cell's kind, its name, its percent)."""
    return re.findall(r'<div class="c c-(five|week)[^"]*" role="cell" data-kind="\1"[^>]*><span class="ml" '
                      r'aria-hidden="true">([^<]*)</span><span class="pct">([^<]*)</span>', row)


def week_tip(row):
    """The weekly line's hover."""
    return re.search(r'<div class="c c-week[^"]*" role="cell" data-kind="week"[^>]* title="([^"]*)"', row).group(1)


@unittest.skipUnless(NODE, 'needs Node')
class WithNone(unittest.TestCase):
    def test_the_rows_are_what_the_claude_accounts_alone_draw(self):
        damaged = ('damaged', 5, {'provider': 'codex'}, [5, None, 'x', {'provider': 3}, {'provider': ''}])
        for s in ((state(), state(provider_rows=None), {k: v for k, v in state().items() if k != 'provider_rows'})
                  + tuple(state(provider_rows=d) for d in damaged)):
            got = run('const nx = page.nextEmail(); console.log(JSON.stringify([page.rowsHTML(), '
                      'page.bySlot().map((a) => page.accRow(a, nx)).join(""), page.belowHTML(), nx, page.top(), '
                      'page.more()]));', s)
            self.assertEqual(got[0], got[1])
            self.assertNotIn('class="mk"', got[0])
            self.assertEqual(got[2:], ['', 'maya@ivy.example', '', ''])

    def test_the_accounts_own_block_stays_as_it_was_beside_them(self):
        both = 'console.log(JSON.stringify(page.rowsHTML()));'
        self.assertEqual(run(both, state(provider_rows=[codex()])), run(both, state()))

    def test_an_account_with_no_5_hour_limit_still_says_so_on_its_weekly_line(self):
        no_five = account(3, 'maya@fern.example', 0, 30)
        del no_five['usage']['session']
        html = run('console.log(JSON.stringify(page.rowsHTML()));',
                   state(accounts=state()['accounts'] + [no_five], provider_rows=[codex()]))
        row = html[html.index('data-email="maya@fern.example"'):]
        self.assertIn('<div class="c c-nofive" role="cell">No 5-hour limit on this plan</div>', row)   # said, in its place
        self.assertEqual(week_tip(row).split('\n')[-1], 'Anthropic reports no 5-hour limit for this account')


@unittest.skipUnless(NODE, 'needs Node')
class TheRow(unittest.TestCase):
    def test_after_the_claude_accounts_under_a_heading_for_each_maker(self):
        html = run(ROWS, state(provider_rows=[codex()]))
        heads = re.findall(r'<div class="mk" role="row"><span role="cell">([^<]*)</span></div>', html)
        self.assertEqual(heads, ['Claude', 'OpenAI'])
        marks = r'class="mk"|data-email="maya@harbor|data-email="maya@ivy|data-provider="codex"'
        order = [m.start() for m in re.finditer(marks, html)]
        self.assertEqual(order, sorted(order))
        self.assertTrue(html.startswith('<div class="mk" role="row"><span role="cell">Claude</span></div>'
                                        '<div class="ag arow'))
        self.assertIn(EMAIL, html)
        self.assertIn('<span class="sub">Plus · chat “Upload retries”</span>', html)    # under its maker's heading

    def test_its_limits_one_under_another_in_the_accounts_own_cells(self):
        row = cells(run(ROWS, state(provider_rows=[codex()])))
        found = re.findall(r'<div class="c (c-five|c-week)[^"]*" role="cell" data-kind="(five|week)".*?'
                           r'<span class="pct">([^<]*)</span>', row, re.S)
        self.assertEqual([(c, p) for c, _, p in found], [('c-five', '8%'), ('c-week', '21%')])
        self.assertIn('Refills 7:13 PM', row)
        self.assertEqual(lines(row), [('five', '5-hour', '8%'), ('week', 'Weekly', '21%')])

    def test_each_account_of_a_provider_is_a_row_of_its_own_under_one_heading(self):
        html = run(ROWS, state(provider_rows=[codex(), codex(email='ana@home.example', plan='Pro')]))
        heads = re.findall(r'<div class="mk" role="row"><span role="cell">([^<]*)</span></div>', html)
        self.assertEqual(heads, ['Claude', 'OpenAI'])
        self.assertEqual(re.findall(r'data-provider="(\w+)"><div class="c c-who" role="cell"><div class="who">'
                                    r'<span class="em" title="([^"]*)"', html),
                         [('codex', EMAIL), ('codex', 'ana@home.example')])

    def test_continue_here_while_a_chat_would_go(self):
        row = cells(run(ROWS, state(provider_rows=[codex()])))
        self.assertIn('data-do="carry" data-provider="codex"', row)
        self.assertIn('>Continue here</button>', row)
        self.assertIn('title="Opens Codex with a note of your chat “Upload retries” in its message box. '
                      'You send it there."', row)
        no_app = cells(run(ROWS, state(provider_rows=[codex(app=False)])))
        self.assertIn('title="Codex’s app is not on this Mac: this gives a line to run in a terminal."', no_app)
        self.assertNotIn('data-do="carry"', cells(run(ROWS, state(provider_rows=[codex(chat=None, can=False)]))))

    def test_the_chat_that_will_go_is_said_without_a_mouse(self):
        # Its name was only in the button's hover: a keyboard or a touch never saw which chat a press drafts.
        row = row_of()
        self.assertIn('<span class="sub">Plus · chat “Upload retries”</span>', row)
        self.assertIn('aria-label="Continue here in Codex with your chat “Upload retries”"', row)
        for more in ({'chat': None, 'can': False}, {'chat': {'name': None, 'mark': 'a1b2c3d4'}},
                     {'state': 'out', 'back_at': BACK}, {'in_use': True}, {'why': 'not on this Mac', 'can': False}):
            with self.subTest(more=more):
                got = row_of(**more)
                self.assertIn('<span class="sub">Plus</span>', got)            # no Continue here, or no name: the plan
        self.assertIn('aria-label="Continue here in Codex"', row_of(chat={'name': None, 'mark': 'a1b2c3d4'}))
        odd = row_of(chat={'name': '"><img src=x>', 'mark': 'a1b2c3d4'})
        self.assertNotIn('<img', odd)
        self.assertIn('chat “&quot;&gt;&lt;img src=x&gt;”', odd)

    def test_exactly_one_control_continue_here_and_only_when_it_can(self):
        def controls(row):
            return re.findall(r'<(button|a|input|select)\b[^>]*>', row)
        self.assertEqual(len(controls(row_of())), 1)
        self.assertEqual(re.findall(r'data-do="(\w+)"', row_of()), ['carry'])
        for more in ({'can': False}, {'state': 'out', 'back_at': BACK}, {'in_use': True},
                     {'why': 'this folder stays on Claude', 'can': False}):
            with self.subTest(more=more):
                self.assertEqual(controls(row_of(**more)), [])

    def test_a_provider_that_cannot_be_continued_to_says_why_instead(self):
        row = cells(run(ROWS, state(provider_rows=[codex(why='not on this Mac', can=False)])))
        self.assertIn('<span class="sub why" title="Not on this Mac">Not on this Mac</span>', row)
        self.assertNotIn('data-do="carry"', row)

    def test_a_reason_wraps_in_its_column_and_says_all_of_it_on_hover(self):
        # The last column is 150 px wide: a reason with a folder's name in it was cut with an ellipsis, with no hover.
        why = 'chats in acme-billing-service-migration stay on Claude'
        row = row_of(why=why, can=False)
        self.assertIn('<span class="sub why" title="Chats in acme-billing-service-migration stay on Claude">'
                      'Chats in acme-billing-service-migration stay on Claude</span>', row)
        rule = re.search(r'\.c-state \.sub\.why \{([^}]*)\}', page_css())
        self.assertIsNotNone(rule, 'no rule for the reason in the last column')
        for want in ('white-space: normal', 'text-align: right', 'overflow-wrap: anywhere'):
            self.assertIn(want, rule.group(1))
        odd = row_of(why='"><img src=x>', can=False)
        self.assertNotIn('<img', odd)
        self.assertIn('title="&quot;&gt;&lt;img src=x&gt;"', odd)

    def test_the_last_cell_first_match_wins(self):
        out = {'state': 'out', 'back_at': BACK}
        go = r'^<button type="button" class="btn xs ghost use" data-do="carry" [^>]*>Continue here</button>$'
        cases = [({'in_use': True, 'why': 'this folder stays on Claude', 'can': False, **out},
                  '<span class="inuse"><i></i>In use</span>'),
                 ({'why': 'this folder stays on Claude', 'can': False, **out},
                  '<span class="sub why" title="This folder stays on Claude">This folder stays on Claude</span>'),
                 (out, '<span class="sub why" title="Out until Sun 5 PM">Out until Sun 5 PM</span>'),
                 ({'can': False, 'state': 'room'}, ''),
                 ({'state': 'unknown', 'windows': []}, go),
                 ({}, go)]
        for more, want in cases:
            with self.subTest(more=more):
                got = last_cell(row_of(**more))
                if want == go:
                    self.assertRegex(got, go)
                else:
                    self.assertEqual(got, want)
        self.assertRegex(last_cell(row_of(next=True)), r'^<span class="badge next"[^>]*>Next</span>' + go[1:])

    def test_out_says_until_when_only_when_no_limit_line_says_it(self):
        said = [dict(FIVE), dict(WEEK, used_pct=100.0, resets_at=BACK)]       # its weekly line: "Refills Sun 5 PM"
        for more, want in (({'windows': said, 'back_at': BACK}, 'Out'),
                           ({'back_at': BACK}, 'Out until Sun 5 PM'),
                           ({'back_at': None}, 'Out'),
                           ({'back_at': NOW - 60}, 'Out'),                    # a time gone by: not "until" it
                           ({'back_at': 1e300}, 'Out'), ({'back_at': -1e300}, 'Out'), ({'back_at': 'soon'}, 'Out')):
            with self.subTest(more=more):
                row = row_of(state='out', **more)
                self.assertEqual(re.findall(r'<span class="sub why" title="([^"]*)">\1</span>', row), [want])
                shown = row.replace(' title="%s"' % want, '')          # the reason's own hover repeats it, on purpose
                self.assertEqual(shown.count('Sun 5 PM'), 1 if more.get('back_at') == BACK else 0)

    def test_next_is_one_row_across_providers_and_in_use(self):
        html = run(ROWS, state(provider_rows=[codex(next=True)]))
        self.assertEqual(html.count('class="badge next"'), 1)
        self.assertIn('class="badge next"', cells(html))
        self.assertEqual(run(ROWS, state(provider_rows=[codex()])).count('class="badge next"'), 1)   # ivy's own
        on = cells(run(ROWS, state(provider_rows=[codex(in_use=True)])))
        self.assertIn('<span class="inuse"><i></i>In use</span>', on)
        self.assertNotIn('data-do="carry"', on)

    def test_named_by_the_app_a_window_not_known_refilled_or_missing(self):
        s = state(provider_rows=[codex(email=None, windows=[{'label': 'weekly', 'used_pct': None, 'resets_at': None,
                                                              'line': 95.0}])])
        row = cells(run(ROWS, s))
        self.assertIn('<span class="em" title="Codex">Codex</span>', row)
        # no 5-hour window: no 5-hour bar, as on the account rows; the words in its place and the weekly hover say why
        self.assertEqual(lines(row), [('week', 'Weekly', '—')])                 # a weekly with no number: not known
        self.assertIn('<div class="c c-nofive" role="cell">No 5-hour limit on this plan</div>', row)
        self.assertEqual(week_tip(row).split('\n')[-1], 'OpenAI reports no 5-hour limit for this account')
        refilled = codex(windows=[{'label': '5-hour', 'used_pct': 70.0, 'resets_at': NOW - 60, 'line': 90.0},
                                  {'label': 'weekly', 'used_pct': 97.0, 'resets_at': NOW + 86400, 'line': 95.0}])
        row = cells(run(ROWS, state(provider_rows=[refilled])))
        self.assertIn('<span class="pct">0%</span>', row)
        self.assertEqual(lines(row), [('five', '5-hour', '0%'), ('week', 'Weekly', '97%')])    # refilled: 0%
        self.assertNotIn('Refills 3:30 PM', row)
        self.assertIn('c-week over', row)

    def test_a_5_hour_window_with_no_number_has_no_line(self):
        row = row_of(windows=[dict(FIVE, used_pct=None), dict(WEEK)])
        self.assertEqual(lines(row), [('week', 'Weekly', '21%')])
        self.assertIn('<div class="c c-gone sr" role="cell">Not known</div>', row)
        self.assertEqual(week_tip(row).split('\n')[-1], '5-hour not known')

    def test_a_5_hour_window_with_no_number_has_no_line_also_once_its_reset_has_passed(self):
        row = row_of(windows=[dict(FIVE, used_pct=None, resets_at=NOW - 5), dict(WEEK)])
        self.assertEqual(lines(row), [('week', 'Weekly', '21%')])
        self.assertIn('<div class="c c-gone sr" role="cell">Not known</div>', row)
        self.assertEqual(week_tip(row).split('\n')[-1], '5-hour not known')
        refilled = row_of(windows=[dict(FIVE, resets_at=NOW - 5), dict(WEEK)])      # a number: refilled, 0%
        self.assertEqual(lines(refilled), [('five', '5-hour', '0%'), ('week', 'Weekly', '21%')])

    def test_another_window_is_the_week_line_with_its_own_label_when_there_is_no_weekly(self):
        three = {'label': '3-day', 'used_pct': 40.0, 'resets_at': NOW + 2 * 86400, 'line': 95.0}
        self.assertEqual(lines(row_of(windows=[dict(FIVE), three])),
                         [('five', '5-hour', '8%'), ('week', '3-day', '40%')])
        both = row_of(windows=[dict(FIVE), dict(WEEK), three])
        self.assertEqual(lines(both), [('five', '5-hour', '8%'), ('week', 'Weekly', '21%')])
        self.assertNotIn('3-day', both)
        odd = row_of(windows=[dict(three, label='<b>')])
        self.assertIn('<span class="ml" aria-hidden="true">&lt;b&gt;</span>', odd)
        self.assertNotIn('<b>', odd)

    def test_a_window_of_another_length_says_its_own_name_to_a_screen_reader_and_on_hover(self):
        # In the weekly column, a "Monthly credits" window was read out as Weekly, and its label was cut short.
        monthly = {'label': 'Monthly credits', 'used_pct': 30.0, 'resets_at': NOW + 9 * 86400, 'line': 95.0}
        row = row_of(windows=[dict(FIVE), monthly])
        cell = re.search(r'<div class="c c-week[^"]*" role="cell" data-kind="week"[^>]*>', row).group(0)
        self.assertIn('aria-label="Monthly credits limit: 30% used"', cell)
        self.assertTrue(week_tip(row).startswith('Monthly credits limit: 30% used'), week_tip(row))
        unknown = row_of(windows=[dict(FIVE), dict(monthly, used_pct=None)])
        self.assertIn('aria-label="Monthly credits limit: not known"', unknown)
        plain = row_of()                                       # the column's own words: as an account's cells
        self.assertNotIn('aria-label="Weekly', plain)
        self.assertNotIn('aria-label="5-hour', plain)
        self.assertTrue(week_tip(plain).startswith('21% used'), week_tip(plain))

    def test_a_row_with_no_numbers_says_so_and_draws_no_bar(self):
        for windows in ([], None, 'damaged', [None, 5, 'x']):
            with self.subTest(windows=windows):
                row = row_of(windows=windows, state='unknown', read_at=None)
                self.assertIn('Numbers not known', row)
                self.assertIn('<div class="c c-gone sr" role="cell">Not known</div>', row)     # four cells, as theirs
                self.assertNotIn('class="bar', row)
                self.assertEqual(lines(row), [])
                self.assertIn('data-do="carry"', row)                       # its number can still be pressed

    def test_a_row_with_only_a_5_hour_window_keeps_the_tables_four_cells(self):
        # The week column gets the same hidden cell as a row with no window: four cells under the four-column
        # header (who, 5-hour, week, last cell), as an account's row.
        self.assertEqual(row_of().count('role="cell"'), 4)
        for five in (dict(FIVE), dict(FIVE, used_pct=None)):
            with self.subTest(five=five):
                row = row_of(windows=[five])
                self.assertEqual(row.count('role="cell"'), 4, row)
                self.assertTrue(row.endswith('<div class="c c-gone sr" role="cell">Not known</div>'
                                             + row[row.index('<div class="c c-state"'):]), row)

    def test_two_rows_of_one_provider_keep_their_own_focus(self):
        # Each Continue here has its own focus key, so the focus stays on the one pressed when the board is drawn again.
        html = run(ROWS, state(provider_rows=[codex(), codex(email='bo@northwind.example')]))
        keys = re.findall(r'data-do="carry"[^>]* data-k="([^"]*)"', html)
        self.assertEqual(len(keys), 2, html[-600:])
        self.assertEqual(len(set(keys)), 2, keys)

    def test_a_damaged_row_never_breaks_the_board(self):
        row = row_of(windows='damaged', chat='x', back_at='soon', state='out', plan=5, email=['x'], read_at='old')
        self.assertIn('Numbers not known', row)
        self.assertIn('<span class="sub why" title="Out">Out</span>', row)

    def test_a_reset_time_that_is_not_a_time_never_breaks_the_board(self):
        for t in (1e300, -1e300, 1e20):
            with self.subTest(t=t):
                row = cells(run(ROWS, state(provider_rows=[codex(windows=[{'label': '5-hour', 'used_pct': 8.0,
                                                                            'resets_at': t, 'line': 90.0}])])))
                self.assertIn('<span class="pct">8%</span>', row)
                self.assertNotIn('Refills', row)

    def test_text_from_outside_is_escaped(self):
        row = cells(run(ROWS, state(provider_rows=[codex(email='<b>x</b>@y.example', plan='<i>',
                                                     chat={'name': '"><img src=x>'})])))
        self.assertNotIn('<b>x', row)
        self.assertNotIn('<img', row)
        self.assertIn('&lt;b&gt;x&lt;/b&gt;@y.example', row)


BELOW = 'console.log(JSON.stringify(page.belowHTML()));'


def below_lines(html):
    """Each quiet line under the board: (its words, the commands its buttons copy, how many buttons it has)."""
    return [(re.sub(r'<[^>]+>', '', tx), re.findall(r'data-do="copy" data-text="([^"]*)"', acts or ''),
             len(re.findall(r'<button\b', acts or '')))
            for tx, acts in re.findall(r'<span class="tx">(.*?)</span>(?:<span class="acts">(.*?)</span>)?</div>',
                                       html)]


@unittest.skipUnless(NODE, 'needs Node')
class UnderTheBoard(unittest.TestCase):
    def test_an_assistant_on_this_mac_not_added_has_the_command_that_adds_it_to_copy(self):
        got = below_lines(run(BELOW, state(providers=board(add=[OFFER]))))
        self.assertEqual(got, [('Codex is on this Mac too: switchboard add codex adds it.',
                                ['switchboard add codex'], 1)])

    def test_an_assistant_added_with_no_row_says_why_in_one_line(self):
        got = below_lines(run(BELOW, state(providers=board([entry()]))))
        self.assertEqual(got, [('Codex: not signed in', [], 0)])
        # one that can be continued to has its row instead (fit_why None): no line
        self.assertEqual(run(BELOW, state(providers=board([entry(fit_why=None, state='room')]),
                                          provider_rows=[codex()])), '')
        claude_unfit = board()
        claude_unfit['providers'][0]['fit_why'] = 'not signed in'               # the accounts above say Claude's
        self.assertEqual(run(BELOW, state(providers=claude_unfit)), '')

    def test_the_command_to_carry_a_chat_on_only_when_there_is_no_row_at_all(self):
        got = below_lines(run(BELOW, state(providers=board([entry(fit_why='sign-in not seen here')], carry=True))))
        self.assertEqual(got, [('Codex: sign-in not seen here', [], 0),
                               ('To carry a chat on in the next one with room, type in its folder: '
                                'switchboard continue', ['switchboard continue'], 1)])
        with_row = run(BELOW, state(providers=board([entry(fit_why=None, state='room')], carry=True),
                                    provider_rows=[codex()]))
        self.assertNotIn('switchboard continue', with_row)

    def test_a_damaged_or_missing_board_says_nothing_and_never_stops_the_board(self):
        rows = 'console.log(JSON.stringify([page.rowsHTML(), page.belowHTML(), page.top(), page.more()]));'
        alone = run(rows, state())
        for b in ('damaged', 5, None, [], [entry()], {'providers': 'x', 'add': 5, 'carry': 'yes'},
                  {'providers': [5, None, 'x', {'id': 3}, {'id': ''}, {'id': 'codex', 'fit_why': 7}],
                   'add': [None, 5, {'id': ''}, {'name': 'Codex'}]}):
            with self.subTest(board=b):
                self.assertEqual(run(rows, state(providers=b)), alone)
        self.assertEqual(alone[1:], ['', '', ''])

    def test_text_from_the_board_is_escaped(self):
        got = run(BELOW, state(providers=board([entry(name='<b>x</b>', fit_why='<i>')],
                                               add=[dict(OFFER, id='a"b', app='<img src=x>')])))
        self.assertNotIn('<b>x', got)
        self.assertNotIn('<img', got)
        self.assertNotIn('<i>', got)
        self.assertIn('&lt;b&gt;x&lt;/b&gt;: &lt;i&gt;', got)
        self.assertIn('data-text="switchboard add a&quot;b"', got)


@unittest.skipUnless(NODE, 'needs Node')
class ThePress(unittest.TestCase):
    def test_continue_here_posts_the_provider_with_the_pages_header_and_nothing_else(self):
        got = run('page.actions.carry({provider: "codex", zone: "acc"}); page.actions.carry({provider: "codex"});'
                  'page.actions.carry({provider: "other"}); console.log(JSON.stringify(posted));',
                  state(provider_rows=[codex()]))
        self.assertEqual(len(got), 1)                                   # one at a time, and only a row that is there
        path, body, headers = got[0]
        self.assertEqual((path, body), ('/api/continue', {'provider': 'codex', 'mark': 'a1b2c3d4'}))
        self.assertEqual(headers.get('X-Switchboard'), '1')

    def test_continue_here_keeps_the_keyboard_focus_while_its_press_is_under_way(self):
        # A disabled button cannot hold focus: the page's repaint could not put focus back on it, and the next Tab
        # started from the top. aria-disabled keeps it focusable; the press's own guard makes a second press nothing.
        got = run('page.actions.carry({provider: "codex", zone: "acc"});'
                  'console.log(JSON.stringify([page.more(), posted.length]));', state(provider_rows=[codex()]))
        button = re.search(r'<button[^>]*data-do="carry"[^>]*>', got[0]).group(0)
        self.assertIn('aria-disabled="true"', button)
        self.assertNotRegex(button, r'\sdisabled[\s>=]')
        self.assertEqual(got[1], 1)
        idle = re.search(r'<button[^>]*data-do="carry"[^>]*>', row_of()).group(0)
        self.assertNotIn('aria-disabled', idle)
        self.assertNotRegex(idle, r'\sdisabled[\s>=]')
        done = run('globalThis.fetch = () => Promise.resolve({ok: true, json: () => Promise.resolve({ok: true, said: '
                   '["Codex is open with your chat in its message box. Press send to carry on."]})});'
                   'page.actions.carry({provider: "codex", zone: "acc"});'
                   'setImmediate(() => setImmediate(() => console.log(JSON.stringify(page.more()))));',
                   state(provider_rows=[codex()]))
        self.assertNotIn('aria-disabled', re.search(r'<button[^>]*data-do="carry"[^>]*>', done).group(0))

    def test_the_line_to_run_stays_under_the_board_with_copy(self):
        got = run('globalThis.fetch = () => Promise.resolve({ok: true, json: () => Promise.resolve({ok: true, said: '
                  '["Codex\'s app is not on this Mac. To carry on, run this in a terminal:"], '
                  'run: "cd /x && switchboard 3"})});'
                  'page.actions.carry({provider: "codex", zone: "acc"});'
                  'setImmediate(() => setImmediate(() => console.log(JSON.stringify(page.belowHTML()))));',
                  state(provider_rows=[codex()]))
        self.assertIn("Codex's app is not on this Mac. To carry on, run this in a terminal: "
                      "<code>cd /x &amp;&amp; switchboard 3</code>", got.replace('&#39;', "'"))
        self.assertIn('data-do="copy" data-text="cd /x &amp;&amp; switchboard 3"', got)
        self.assertIn('data-do="hideRun"', got)

    def test_the_line_to_run_goes_once_its_row_has_another_number(self):
        # 'switchboard 3' was built when Codex was row 3; after a third Claude account it would move Claude Code.
        moved = state(provider_rows=[codex()])
        moved['accounts'] = moved['accounts'] + [account(3, 'maya@fern.example', 5, 5)]
        got = run('globalThis.fetch = () => Promise.resolve({ok: true, json: () => Promise.resolve({ok: true, said: '
                  '["Codex\'s app is not on this Mac. To carry on, run this in a terminal:"], '
                  'run: "cd /x && switchboard 3"})});'
                  'page.actions.carry({provider: "codex", zone: "acc"});'
                  'setImmediate(() => setImmediate(() => { const before = page.belowHTML(); page.set(%s); '
                  'const after = page.belowHTML(); page.set(state); '
                  'console.log(JSON.stringify([before, after, page.belowHTML()])); }));' % json.dumps(moved),
                  state(provider_rows=[codex()]))
        self.assertIn('switchboard 3', got[0])
        self.assertNotIn('switchboard 3', got[1])
        self.assertNotIn('switchboard 3', got[2])                  # gone for good, not back with the old list


if __name__ == '__main__':
    unittest.main()
