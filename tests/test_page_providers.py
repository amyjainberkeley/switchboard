import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# Another assistant on the page, in headless Chrome at 680 px and 1280 px, light and dark, through test_app's helper:
# the demo's accounts, with the state's two keys for other assistants set by each test (provider_rows: the rows;
# providers: public's board, read only for what the rows do not say), or left out.
#
# - Claude and a Codex that is out: one Codex row after the accounts, under the heading "OpenAI" (and "Claude" over
#   the accounts), drawn as an account's row is: its plan under its name, its weekly limit in the accounts' own cell
#   (the same "Weekly" words, red at 100%, with when it refills), "No 5-hour limit on this plan" in the 5-hour line's
#   place (it has no 5-hour window), and "Out": the time it is out until is said once, by the limit line. Out leaves
#   nothing to press. Its bars start at the accounts' left edge and are their width; at 1280 px its limits (those
#   words first) sit beside its name, at 680 px under it. Nothing runs past the right edge.
# - Two accounts of one assistant: two rows. Each says its own state: "Out until" a time only when none of its limit
#   lines says that time.
# - A 5-hour window with no number has no line, as on the account rows; a row that can be continued to has exactly
#   one control, Continue here.
# - A long reason (a folder's name in it) wraps in the last column, whole, at both widths, and its hover says it all.
# - Under the board, quietly, only what the rows do not say: an assistant on this Mac not added (the command that adds
#   it, with Copy and no other control); one added that has no row, and why.
# - Nothing to show draws nothing; a damaged board and damaged rows never stop the page (the five account rows still
#   draw); a state from before either key draws the accounts alone.
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

H, D = 3600, 86400

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
        const rows = [...d.querySelectorAll('#acc-other .arow.other[data-provider]')];
        const box = (el) => (el ? el.getBoundingClientRect() : null);
        const one = {width: f.contentWindow.innerWidth, theme,
          spills: root.scrollWidth > f.contentWindow.innerWidth,
          heads: [...d.querySelectorAll('#acc-board .mk')].map((m) => m.textContent),
          rows: rows.map((r) => ({id: r.dataset.provider, name: (r.querySelector('.em') || {}).textContent || '',
            sub: (r.querySelector('.c-who .sub') || {}).textContent || '',
            state: r.querySelector('.c-state').textContent,
            why_tip: (r.querySelector('.c-state .sub.why') || {}).title || '',
            why_cut: [...r.querySelectorAll('.c-state .sub.why')].some((w) => w.scrollWidth > w.clientWidth + 1 ||
              w.getBoundingClientRect().right > r.getBoundingClientRect().right + 1),
            limits: [...r.querySelectorAll(':scope > .c[data-kind]')].map((c) => ({
              label: c.querySelector('.ml').textContent, pct: c.querySelector('.pct').textContent,
              over: c.classList.contains('over'), bar: !!c.querySelector('.bar'),
              refill: (c.querySelector('.rf') || {}).textContent || '', tip: c.title})),
            gone: [...r.querySelectorAll(':scope > .c-gone')].map((c) => c.textContent),
            nofive: [...r.querySelectorAll(':scope > .c-nofive')].map((c) => c.textContent),
            text: r.textContent,
            controls: r.querySelectorAll('button, a, input, select').length,
            carry: r.querySelectorAll('[data-do="carry"]').length})),
          go: [...d.querySelectorAll('#below .nl')].map((n) => ({text: n.querySelector('.tx').textContent,
            copies: [...n.querySelectorAll('[data-do="copy"]')].map((b) => b.dataset.text),
            controls: n.querySelectorAll('button, a, input, select').length})),
          accounts: d.querySelectorAll('#acc .arow').length, broken: !!d.querySelector('p.boot')};
        const r = rows[0];
        if (r) {
          // the first of its limits: a limit's line, or the words of a plan with no 5-hour limit in that line's place
          const who = box(r.querySelector('.em'));
          const lims = box(r.querySelector(':scope > .c-nofive, :scope > .c[data-kind]'));
          if (lims) {
            one.beside = Math.abs(who.top - lims.top) < 4 && lims.left > who.left + 100;
            one.under = lims.top >= who.bottom - 1;
          }
          const acc = d.querySelector('#acc .arow .c-week .bar'), mine = r.querySelector('.c-week .bar');
          one.bars_line_up = !!acc && !!mine && Math.abs(box(acc).left - box(mine).left) < 1 &&
            Math.abs(box(acc).width - box(mine).width) < 1;
        }
        out.looks.push(one);
      }
      delete doc(f).documentElement.dataset.theme;
    }
  } catch (e) { out.error = String(e && e.stack || e); }
  document.getElementById('result').textContent = JSON.stringify(out);
})();
</script>"""


def board(entries=(), add=(), carry=False, now=None):
    """The state's providers: public's board (providers/view.py), Claude first, then each entry."""
    claude = {'id': 'claude', 'name': 'Claude', 'vendor': 'Anthropic', 'app': 'Claude Code', 'added': True,
              'account_list': True, 'installed': True, 'state': 'room', 'fit_why': None, 'email': None, 'seats': []}
    return {'v': 1, 'now': now or time.time(), 'order': ['claude'] + [e['id'] for e in entries],
            'providers': [claude] + list(entries), 'add': list(add), 'carry': carry}


def entry(**more):
    """Codex on the board, added and fit to be continued to unless said otherwise."""
    return dict({'id': 'codex', 'name': 'Codex', 'vendor': 'OpenAI', 'app': 'Codex', 'added': True,
                 'account_list': False, 'installed': True, 'signed_in': True, 'state': 'room', 'fit_why': None,
                 'email': None, 'has_app': True, 'seats': []}, **more)


def row(now, **more):
    """One of the state's provider_rows (DESIGN.md D3): Codex on Pro, out on its weekly window until a day and a half
    from now, its numbers read 12 minutes ago, no email known."""
    back = round((now + 1.6 * D) / H) * H
    return dict({'provider': 'codex', 'name': 'Codex', 'vendor': 'OpenAI', 'email': None, 'plan': 'Pro',
                 'windows': [{'label': 'weekly', 'used_pct': 100.0, 'resets_at': back, 'line': 95.0}],
                 'read_at': now - 12 * 60, 'state': 'out', 'back_at': back, 'fresh': False, 'why': None, 'can': True,
                 'app': True, 'in_use': False, 'next': False,
                 'chat': {'name': 'Billing export fix', 'mark': 'a1b2c3d4'}}, **more)


OFFER = {'id': 'codex', 'name': 'Codex', 'app': 'Codex', 'version': '0.169.1'}


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class TheRow(test_app.ChromePage):
    WIDTHS = (680, 1280)
    maxDiff = None

    def setUp(self):
        super().setUp()
        demo._ready()

    def looks(self, providers, rows):
        """The page at each width, light and dark, with the state's providers and provider_rows set to these
        ('absent': the key left out), whatever the server and the demo would have said."""
        render = server.render

        def as_given(st, f):
            v = render(st, f)
            for key, value in (('providers', providers), ('provider_rows', rows)):
                if value == 'absent':
                    v.pop(key, None)
                else:
                    v[key] = value
            return v

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
        with mock.patch.object(server, 'render', as_given), mock.patch.object(server.Handler, '_page', page):
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
        for look in self.looks(board([entry(state='out')], carry=True), [row(now)]):
            with self.subTest(width=look['width'], theme=look['theme']):
                self.assertFalse(look['spills'])
                self.assertEqual(look['heads'], ['Claude', 'OpenAI'])
                self.assertEqual(look['accounts'], 5)
                self.assertEqual([r['id'] for r in look['rows']], ['codex'])
                codex = look['rows'][0]
                self.assertEqual((codex['name'], codex['sub']), ('Codex', 'Pro'))
                self.assertEqual(codex['state'], 'Out')             # its weekly line says when it refills
                self.assertEqual(len(codex['limits']), 1)
                weekly = codex['limits'][0]
                self.assertEqual((weekly['label'], weekly['pct'], weekly['over'], weekly['bar']),
                                 ('Weekly', '100%', True, True))
                self.assertTrue(weekly['refill'].startswith('Refills '), weekly['refill'])
                self.assertEqual((codex['gone'], codex['nofive']), ([], ['No 5-hour limit on this plan']))  # no window
                self.assertEqual(weekly['tip'].split('\n')[-1], 'OpenAI reports no 5-hour limit for this account')
                self.assertEqual(codex['controls'], 0)                  # out: nothing to press
                self.assertEqual(look['go'], [])                        # the row says it all: no line under the board
                self.assertTrue(look['bars_line_up'])                   # on the accounts' own columns
                if look['width'] == 1280:
                    self.assertTrue(look['beside'])
                else:
                    self.assertTrue(look['under'])

    def test_out_until_a_time_only_when_no_limit_line_says_it(self):
        # Two Codex accounts, both out. The first one's weekly number is not known (no refill on its line): "Out
        # until" its own time back. The second one's weekly line refills a day later, at its own time back: "Out".
        now = time.time()
        first = row(now, email='ana@work.example')
        week = first['windows'][0]
        second = row(now, email='ana@home.example', back_at=week['resets_at'] + D,
                     windows=[dict(week, resets_at=week['resets_at'] + D)])
        first['windows'] = [dict(week, used_pct=None)]
        for look in self.looks(board([entry(state='out')]), [first, second]):
            with self.subTest(width=look['width'], theme=look['theme']):
                self.assertEqual(look['heads'], ['Claude', 'OpenAI'])
                work, home = look['rows']
                self.assertEqual((work['name'], home['name']), ('ana@work.example', 'ana@home.example'))
                self.assertTrue(work['state'].startswith('Out until '), work['state'])
                self.assertEqual([l['refill'] for l in work['limits']], [''])
                self.assertEqual(work['limits'][0]['pct'], '—')
                self.assertEqual(home['state'], 'Out')
                self.assertTrue(home['limits'][0]['refill'].startswith('Refills '))
                self.assertNotEqual(home['limits'][0]['refill'], 'Refills ' + work['state'][len('Out until '):])

    def test_a_five_hour_window_with_no_number_has_no_line_as_on_the_account_rows(self):
        # Two Codex accounts with room: one whose 5-hour number is not known (no line, a hidden cell says why, and the
        # weekly line's hover), one whose 5-hour window has a number (two lines: 5-hour, then weekly).
        now = time.time()
        five = {'label': '5-hour', 'used_pct': None, 'resets_at': now + H, 'line': 90.0}
        weekly = {'label': 'weekly', 'used_pct': 40.0, 'resets_at': now + 3 * D, 'line': 95.0}
        rows = [row(now, email='ana@work.example', state='room', back_at=None, fresh=True, read_at=now - 40,
                    windows=[five, weekly]),
                row(now, email='ana@home.example', state='room', back_at=None, fresh=True, read_at=now - 40,
                    windows=[dict(five, used_pct=30.0), dict(weekly, used_pct=50.0)])]
        for look in self.looks(board([entry()]), rows):
            with self.subTest(width=look['width'], theme=look['theme']):
                self.assertFalse(look['spills'])
                self.assertEqual([r['id'] for r in look['rows']], ['codex', 'codex'])
                unknown, known = look['rows']
                self.assertEqual(([l['label'] for l in unknown['limits']], unknown['gone']),
                                 (['Weekly'], ['Not known']))
                self.assertEqual(unknown['limits'][0]['tip'].split('\n')[-1], '5-hour not known')
                self.assertEqual(([l['label'] for l in known['limits']], known['gone']), (['5-hour', 'Weekly'], []))
                self.assertEqual(known['limits'][0]['pct'], '30%')
                for r in look['rows']:                              # with room: exactly one control, Continue here
                    self.assertEqual((r['controls'], r['carry']), (1, 1))
                    self.assertEqual(r['state'], 'Continue here')

    def test_a_long_reason_wraps_in_its_column_whole(self):
        now = time.time()
        why = 'chats in acme-billing-service-migration stay on Claude'
        rows = [row(now, state='room', back_at=None, fresh=True, read_at=now - 40, why=why, can=False)]
        for look in self.looks(board([entry()]), rows):
            with self.subTest(width=look['width'], theme=look['theme']):
                self.assertFalse(look['spills'])
                codex = look['rows'][0]
                self.assertEqual(codex['state'], 'Chats in acme-billing-service-migration stay on Claude')
                self.assertEqual(codex['why_tip'], codex['state'])
                self.assertFalse(codex['why_cut'])                  # wrapped in its column, never cut or spilling
                self.assertEqual(codex['controls'], 0)

    def test_only_claude_added_and_codex_on_the_mac(self):
        for look in self.looks(board(add=[OFFER]), []):
            with self.subTest(width=look['width'], theme=look['theme']):
                self.assertFalse(look['spills'])
                self.assertEqual((look['heads'], look['rows']), ([], []))
                self.assertEqual(look['go'], [{'text': 'Codex is on this Mac too: switchboard add codex adds it.',
                                               'copies': ['switchboard add codex'], 'controls': 1}])   # Copy, no Add

    def test_codex_added_but_not_signed_in_says_so_in_one_line(self):
        for look in self.looks(board([entry(state='signed_out', signed_in=False, fit_why='not signed in')]), []):
            with self.subTest(width=look['width'], theme=look['theme']):
                self.assertEqual((look['heads'], look['rows']), ([], []))
                self.assertEqual(look['go'], [{'text': 'Codex: not signed in', 'copies': [], 'controls': 0}])

    def test_only_claude_and_nothing_else_on_the_mac_draws_nothing(self):
        for look in self.looks(board(), []):
            with self.subTest(width=look['width'], theme=look['theme']):
                self.assertEqual((look['heads'], look['rows'], look['go']), ([], [], []))
                self.assertEqual(look['accounts'], 5)

    def test_a_damaged_board_never_stops_the_page(self):
        damaged = {'providers': [{'id': 'codex', 'name': 'Codex', 'state': 'out', 'seats': 'damaged'}, 5, None],
                   'add': 'damaged', 'carry': True}
        rows = [{'provider': 'codex', 'name': 'Codex', 'state': 'out', 'windows': 'damaged'}, 5, None, 'x']
        for look in self.looks(damaged, rows):
            with self.subTest(width=look['width'], theme=look['theme']):
                self.assertFalse(look['broken'])
                self.assertEqual(look['accounts'], 5)
                self.assertEqual([r['id'] for r in look['rows']], ['codex'])
                self.assertEqual(look['rows'][0]['limits'], [])
                self.assertIn('Numbers not known', look['rows'][0]['text'])
                self.assertEqual(look['rows'][0]['state'], 'Out')
                self.assertEqual(look['go'], [])

    def test_a_state_from_before_either_key_draws_the_accounts_alone(self):
        for look in self.looks('absent', 'absent'):
            with self.subTest(width=look['width'], theme=look['theme']):
                self.assertFalse(look['broken'])
                self.assertEqual((look['heads'], look['rows'], look['go']), ([], [], []))
                self.assertEqual(look['accounts'], 5)


if __name__ == '__main__':
    unittest.main()
