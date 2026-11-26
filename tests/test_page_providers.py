import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# The page's group of providers, under the accounts (drawProviders), in headless Chrome at 680 px and 1280 px, light
# and dark, through test_app's helper (the demo state, its board set by each test).
#
# - Claude and Codex added, Codex out: two rows in the person's order. Claude's says "The accounts above" (its rows
#   are not drawn twice) and "Has room". Codex's has its plan, its weekly limit drawn by the accounts' own useCell
#   (the same "Weekly" words, red at 100%, with when it refills), and "Out": the time it is out until is said once,
#   by the limit line; "Out until" a time only when no limit line of its rows says that time. The command to carry a
#   chat on is plain text set to be selected whole. Nothing in the group is a button or a link: the page starts
#   nothing from it.
# - At 1280 px a row's limits sit beside its name (the accounts' own columns); at 680 px under it. Nothing runs past
#   the right edge.
# - Only Claude added and Codex on the Mac: one quiet line with `switchboard add codex`, no rows, no heading.
# - A state from before the board (no `providers`): no group at all.
import html as htmllib
import json
import os
import re
import sys
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))

import test_app  # noqa: E402  (its headless-Chrome helper; it has no tests of its own here)
from switchboard import core, demo, server  # noqa: E402

FRAMES = r"""<!doctype html><meta charset="utf-8"><body style="margin:0"><main hidden></main>%(frames)s<pre id="result"></pre><script>
(async () => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms)), out = {widths: [], looks: []};
  const frames = [...document.querySelectorAll('iframe')], doc = (f) => f.contentDocument;
  try {
    for (let i = 0; i < 300 && !frames.every((f) => doc(f) && doc(f).querySelector('main[data-drawn]')); i++) await wait(50);
    await Promise.all(frames.map((f) => doc(f).fonts.ready));
    await wait(500);
    out.widths = frames.map((f) => f.contentWindow.innerWidth);
    for (const f of frames) {
      for (const theme of ['light', 'dark']) {
        const d = doc(f), root = d.documentElement;
        root.dataset.theme = theme;
        const g = d.getElementById('providers'), rows = g ? [...g.querySelectorAll('.pv-row')] : [];
        const row = (id) => g.querySelector('.pv-row[data-provider="' + id + '"]');
        const box = (el) => (el ? el.getBoundingClientRect() : null);
        const one = {width: f.contentWindow.innerWidth, theme, shown: !!g && getComputedStyle(g).display !== 'none',
          spills: root.scrollWidth > f.contentWindow.innerWidth,
          head: g && g.querySelector('.pv-head') ? g.querySelector('.pv-head').textContent : null,
          rows: rows.map((r) => ({id: r.dataset.provider, name: (r.querySelector('.em') || {}).textContent || '',
            sub: (r.querySelector('.sub') || {}).textContent || '', above: (r.querySelector('.pv-above') || {}).textContent || '',
            state: r.querySelector('.pv-state').textContent,
            limits: [...r.querySelectorAll('.pv-lims > .c:not(.c-gone)')].map((c) => ({label: c.querySelector('.ml').textContent,
              pct: c.querySelector('.pct').textContent, over: c.classList.contains('over'),
              bar: !!c.querySelector('.bar'), refill: (c.querySelector('.rf') || {}).textContent || ''})),
            gone: [...r.querySelectorAll('.pv-lims > .c-gone')].map((c) => c.textContent)})),
          go: g ? [...g.querySelectorAll('.pv-go')].map((p) => p.textContent) : [],
          code: g ? [...g.querySelectorAll('code.pv-cmd')].map((c) => [c.textContent, getComputedStyle(c).userSelect || getComputedStyle(c).webkitUserSelect]) : [],
          controls: g ? g.querySelectorAll('button, a, input, select, [data-do]').length : 0,
          accounts: d.querySelectorAll('.arow').length, broken: !!d.querySelector('p.boot')};
        const r = row('codex');
        if (r) {
          const who = box(r.querySelector('.pv-who')), lims = box(r.querySelector('.pv-lims'));
          one.beside = Math.abs(who.top - lims.top) < 4 && lims.left > who.left + 100;
          one.under = lims.top >= who.bottom - 1;
          const acc = d.querySelector('.arow .c-week .bar'), mine = r.querySelector('.c-week .bar');
          one.bars_line_up = !!acc && !!mine && Math.abs(box(acc).left - box(mine).left) < 1 && Math.abs(box(acc).width - box(mine).width) < 1;
        }
        out.looks.push(one);
      }
      delete doc(f).documentElement.dataset.theme;
    }
  } catch (e) { out.error = String(e && e.stack || e); }
  document.getElementById('result').textContent = JSON.stringify(out);
})();
</script>"""


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class TheGroup(test_app.ChromePage):
    WIDTHS = (680, 1280)
    maxDiff = None

    def setUp(self):
        super().setUp()
        demo._ready()

    def looks(self, board):
        """The page at each width, light and dark, with this board in the state ('absent': no providers key)."""
        if board == 'absent':
            demo.FACTS.pop('providers', None)
            render = server.render

            def without(st, f):
                v = render(st, f)
                v.pop('providers', None)                # a state from before the board
                return v
            patch = mock.patch.object(server, 'render', without)
        else:
            demo.FACTS['providers'] = board
            patch = mock.patch.object(server, 'render', server.render)

        def page(handler, name):
            if 'frames' in handler.path:
                body = (FRAMES % {'frames': ''.join(
                    '<iframe src="/" style="display:block;width:%dpx;height:1400px;border:0"></iframe>' % w
                    for w in self.WIDTHS)}).encode()
            else:
                with open(os.path.join(core.WEB, name), 'rb') as f:
                    body = f.read()
            handler.send_response(200)                  # framed here only: the real page refuses to be framed
            handler.send_header('Content-Type', 'text/html; charset=utf-8')
            handler.send_header('Content-Length', str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)
        with patch, mock.patch.object(server.Handler, '_page', page):
            dom = self.dom('?frames=1', budget=20000)
        m = re.search(r'<pre id="result">(.*?)</pre>', dom, re.S)
        if not m or not m.group(1).strip():
            self.fail('the frames did not finish')
        got = json.loads(htmllib.unescape(m.group(1)))
        self.assertNotIn('error', got)
        self.assertEqual(got['widths'], list(self.WIDTHS))
        self.assertEqual(len(got['looks']), 4)
        return got['looks']

    def test_claude_and_codex_with_codex_out(self):
        now = time.time()
        for look in self.looks(demo.providers(now)):
            with self.subTest(width=look['width'], theme=look['theme']):
                self.assertTrue(look['shown'])
                self.assertFalse(look['spills'])
                self.assertEqual(look['head'], 'Providersin your order')
                self.assertEqual([r['id'] for r in look['rows']], ['claude', 'codex'])
                claude, codex = look['rows']
                self.assertEqual((claude['name'], claude['above'], claude['state'], claude['limits']),
                                 ('Claude', 'The accounts above', 'Has room', []))
                self.assertEqual((codex['name'], codex['sub']), ('Codex', 'ChatGPT Pro'))
                self.assertEqual(codex['state'], 'Out')         # its weekly line says when it refills
                self.assertEqual(len(codex['limits']), 1)
                weekly = codex['limits'][0]
                self.assertEqual((weekly['label'], weekly['pct'], weekly['over'], weekly['bar']),
                                 ('Weekly', '100%', True, True))
                self.assertTrue(weekly['refill'].startswith('Refills '), weekly['refill'])
                self.assertEqual(look['go'], ['To carry a chat on in the next one with room, type in its folder: '
                                              'switchboard continue'])
                self.assertEqual(look['code'], [['switchboard continue', 'all']])
                self.assertEqual(look['controls'], 0)                       # nothing here starts anything
                self.assertTrue(look['bars_line_up'])                       # on the accounts' own columns
                if look['width'] == 1280:
                    self.assertTrue(look['beside'])
                else:
                    self.assertTrue(look['under'])

    def test_out_until_a_time_only_when_no_limit_line_says_it(self):
        # The weekly number is not known (no refill on its line), and a second Codex account's weekly line refills
        # later than the first one back: "Every account is out until" the first refill, said once, at the right.
        now = time.time()
        board = demo.providers(now)
        codex = board['providers'][1]
        week = codex['seats'][0]['windows'][0]
        codex['count'] = 2
        codex['seats'] = [dict(codex['seats'][0], label='Work', windows=[dict(week, used_pct=None)]),
                          dict(codex['seats'][0], label='Home', in_use=False,
                               windows=[dict(week, resets_at=week['resets_at'] + 86400)])]
        for look in self.looks(board):
            with self.subTest(width=look['width'], theme=look['theme']):
                work, home = look['rows'][1], look['rows'][2]
                self.assertTrue(work['state'].startswith('Every account is out until '), work['state'])
                self.assertEqual(home['state'], '')
                self.assertEqual([l['refill'] for l in work['limits']], [''])
                self.assertNotEqual(home['limits'][0]['refill'], 'Refills ' + work['state'][len('Every account is '
                                                                                              'out until '):])

    def test_a_five_hour_window_with_no_number_has_no_line_as_on_the_account_rows(self):
        # Two Codex accounts: one whose 5-hour window refilled since it was read (no number to show: no line, a hidden
        # cell says why), one whose 5-hour window has a number (two lines: 5-hour, then weekly).
        now = time.time()
        board = demo.providers(now)
        codex = board['providers'][1]
        week = codex['seats'][0]['windows'][0]
        five = {'label': '5-hour', 'used_pct': 30.0, 'resets_at': now - 60, 'length_min': 300, 'line': 90.0}
        codex['count'] = 2
        codex['seats'] = [dict(codex['seats'][0], label='Work', windows=[five, week]),
                          dict(codex['seats'][0], label='Home', in_use=False,
                               windows=[dict(five, resets_at=now + 3600), dict(week, used_pct=50.0)])]
        for look in self.looks(board):
            with self.subTest(width=look['width'], theme=look['theme']):
                self.assertFalse(look['spills'])
                self.assertEqual([r['id'] for r in look['rows']], ['claude', 'codex', 'codex'])
                refilled, known = look['rows'][1], look['rows'][2]
                self.assertEqual(([l['label'] for l in refilled['limits']], refilled['gone']),
                                 (['Weekly'], ['5-hour not known']))
                self.assertEqual(([l['label'] for l in known['limits']], known['gone']), (['5-hour', 'Weekly'], []))
                self.assertEqual(known['limits'][0]['pct'], '30%')

    def test_only_claude_added_and_codex_on_the_mac(self):
        for look in self.looks(demo.providers(time.time(), codex=False)):
            with self.subTest(width=look['width'], theme=look['theme']):
                self.assertTrue(look['shown'])
                self.assertFalse(look['spills'])
                self.assertEqual((look['head'], look['rows']), (None, []))
                self.assertEqual(look['go'], ['Codex is on this Mac too: switchboard add codex adds it.'])
                self.assertEqual(look['controls'], 0)

    def test_only_claude_and_nothing_else_on_the_mac_draws_nothing(self):
        board = dict(demo.providers(time.time(), codex=False), add=[])
        for look in self.looks(board):
            self.assertFalse(look['shown'])

    def test_a_damaged_board_never_stops_the_page(self):
        board = {'providers': [{'id': 'codex', 'name': 'Codex', 'state': 'out', 'seats': 'damaged'}, 5, None],
                 'add': 'damaged', 'carry': True}
        for look in self.looks(board):
            with self.subTest(width=look['width'], theme=look['theme']):
                self.assertFalse(look['broken'])
                self.assertEqual(look['accounts'], 5)
                self.assertEqual([r['id'] for r in look['rows']], ['codex'])
                self.assertEqual(look['rows'][0]['limits'], [])

    def test_a_state_from_before_the_board_draws_no_group(self):
        for look in self.looks('absent'):
            self.assertFalse(look['shown'])


if __name__ == '__main__':
    unittest.main()
