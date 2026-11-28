import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The accounts view (no monospace, no sidebar footer), an About line at the end of Settings, and the week under the
# accounts, drawn from /api/usage. In headless Chrome (a throwaway profile each run, no window) on the demo's
# made-up accounts and made-up weeks.
import copy
import json
import os
import sys
import time
import unittest
from datetime import datetime
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from switchboard import demo, insights, server
from test_app import CHROME, ModelsBase
from test_page_links_and_logo import FramedPage

PAGE = os.path.join(ROOT, 'switchboard', 'web', 'index.html')
EMAILS = ('maya@northwind.example', 'maya.reyes@mail.example', 'maya@fernhill.example', 'm.reyes@harbor.example',
          'maya@ivy.example')
WEEKDAY = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')


def read():
    with open(PAGE, encoding='utf-8') as f:
        return f.read()


def hm(dt):
    h = dt.hour % 12 or 12
    return '%d%s %s' % (h, '' if dt.minute == 0 else ':%02d' % dt.minute, 'AM' if dt.hour < 12 else 'PM')


def times(r):
    """The page's and insights._times' words for a multiple."""
    return insights._times(r)


# One frame's facts: the week as drawn, the accounts' cells, fonts and colours. `d` is the frame's document.
FACTS = r"""
      const facts = (d, theme) => {
        if (theme) d.documentElement.dataset.theme = theme;
        const w = d.defaultView, chart = d.getElementById('wk-chart');
        const rgb = (c) => (c.match(/[\d.]+/g) || []).slice(0, 3).map(Number);
        const lum = (c) => { const v = rgb(c).map((x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); });
          return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]; };
        const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
        const page = w.getComputedStyle(d.body).backgroundColor;
        const tone = (cls) => { const el = d.querySelector('#wk-chart .' + cls); return el ? Math.round(ratio(w.getComputedStyle(el).fill, page) * 100) / 100 : null; };
        const mono = [...d.querySelectorAll('body *')].filter((el) => /mono/i.test(w.getComputedStyle(el).fontFamily)).map((el) => el.tagName + '.' + el.className);
        const out = {
          by: chart && chart.dataset.by, avg: chart && +chart.dataset.avg, peak: (d.getElementById('wk-peak') || {}).textContent || null,
          groups: d.querySelectorAll('#wk-bars > g').length, lim: [...d.querySelectorAll('#wk-chart .ev-lim')].map((x) => x.dataset.at),
          sw: [...d.querySelectorAll('#wk-chart .ev-sw')].map((x) => x.dataset.at), now: !!d.querySelector('#wk-chart .now'),
          scale: [...d.querySelectorAll('#wk-chart text.halo:not(.peak)')].map((x) => x.textContent),
          avgLine: (() => { const l = d.querySelector('#wk-chart .avg'), b = d.querySelector('#wk-chart .base'); return l && b ? [+l.getAttribute('y1'), +b.getAttribute('y1')] : null; })(),
          legend: (d.getElementById('wk-legend') || {}).textContent || '',
          highs: [...d.querySelectorAll('.high')].map((x) => [x.dataset.key, (x.querySelector('.v') || {}).textContent || '', x.querySelector('p').textContent, !!x.querySelector('button, a')]),
          mults: [...d.querySelectorAll('.mult h3')].map((x) => x.textContent), none: (d.querySelector('.wk-none') || {}).textContent || null,
          tones: {wf: tone('s-wf'), help: tone('s-help'), you: tone('s-you'), lim: tone('ev-lim')},
          rows: [...d.querySelectorAll('#acc .arow')].map((r) => ({who: r.querySelector('.c-who .who').textContent,
            cells: [...r.querySelectorAll('.em, .sub, .rf, .c-five > .pct, .c-week > .pct, .note[data-kind="model"] > .pct')].filter((c) => c.offsetParent)
              .map((c) => [c.className, Math.round(c.getBoundingClientRect().height)]),
            state: r.querySelector('.c-state').textContent})),
          head: [...d.querySelectorAll('.ahead [role="columnheader"]')].map((x) => x.textContent),
          overflow: d.documentElement.scrollWidth - d.documentElement.clientWidth, mono,
        };
        if (theme) delete d.documentElement.dataset.theme;
        return out;
      };
"""


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class TheWeek(FramedPage):
    def setUp(self):
        super().setUp()
        # the demo's week moves with the clock: here it stands still, so the page and the test read the same week
        now = time.time()
        clock = mock.patch.object(demo, 'time', mock.Mock(time=lambda: now, sleep=time.sleep))
        clock.start()
        self.addCleanup(clock.stop)

    def payload(self):
        return server.usage_view()

    def test_the_week_draws_from_the_payload_with_one_average(self):
        got = self.in_frames(FACTS + r"""
      const f = document.querySelectorAll('iframe');
      done({wide: facts(f[0].contentDocument), phone: facts(f[1].contentDocument)});""", widths=(1440, 390))
        page = self.payload()
        wide, phone = got['wide'], got['phone']
        # by hour at every width, 168 of them: a phone draws the same hours edge to edge
        self.assertEqual((wide['by'], wide['groups']), ('hour', 168))
        self.assertEqual((phone['by'], phone['groups']), ('hour', 168))
        # one average: the payload's, an hour with use (the hour so far never in it), on the line, the scale and the name
        self.assertAlmostEqual(wide['avg'], page['average']['hour'], places=2)
        self.assertAlmostEqual(phone['avg'], page['average']['hour'], places=2)
        peak = page['peak']['hour']
        at = datetime.fromisoformat(peak['at'])
        named = '%s %s · %s an average hour' % (WEEKDAY[at.weekday()], hm(at), times(peak['weight'] / page['average']['hour']))
        self.assertEqual(wide['peak'], named)
        self.assertEqual(phone['peak'], named)
        self.assertEqual(wide['scale'][0], 'avg')
        marks = [int(x[:-1]) for x in wide['scale'][1:]]             # '2×', '4×', …; '5×', '10×' on a taller week
        self.assertIn(marks[0], (2, 5, 10, 20, 50, 100))               # one step for the whole scale, six lines at most
        self.assertEqual(marks, [marks[0] * k for k in range(1, len(marks) + 1)])
        self.assertLessEqual(len(marks), 6)
        self.assertLess(wide['avgLine'][0], wide['avgLine'][1])                                  # the average sits above the base
        # History's moments on the same axis, each at its time
        limits = [e['at'] for e in page['events'] if e['kind'] == 'limit']
        switches = [e['at'] for e in page['events'] if e['kind'] == 'switch']
        self.assertTrue(limits and switches)
        first = page['series']['hours'][0]['at']
        self.assertEqual(wide['lim'], [t for t in limits if datetime.fromisoformat(t) >= datetime.fromisoformat(first)])
        self.assertEqual(wide['sw'], [t for t in switches if datetime.fromisoformat(t) >= datetime.fromisoformat(first)])
        self.assertTrue(wide['now'])
        self.assertIn('Limit reached', wide['legend'])
        self.assertIn('Workflows 81%', wide['legend'])
        # the highlights: the payload's, the figure set apart, no button anywhere; never the spike again
        want = [[x['key'], x['figure'], x['text']] for x in page['insights']]
        self.assertEqual([h[:3] for h in wide['highs']], want)
        self.assertFalse(any(h[3] for h in wide['highs']))
        self.assertNotIn('hour', [h[0] for h in wide['highs']])
        self.assertEqual(wide['mults'], ['Projects', 'What agents did', 'Models'])

    def test_the_three_tones_are_marks_in_both_themes(self):
        got = self.in_frames(FACTS + r"""
      const d = document.querySelector('iframe').contentDocument;
      done({light: facts(d, 'light'), dark: facts(d, 'dark')});""", widths=(1440,))
        for theme in ('light', 'dark'):
            for k, v in got[theme]['tones'].items():
                self.assertGreaterEqual(v, 3.0, (theme, k, v))
        # three different things, not steps of one grey (test_week_parts_tell_apart): workflows are the ink, the
        # strongest mark on the page in both themes; you wear the accent; helpers are the quiet grey
        for theme in ('light', 'dark'):
            tones = got[theme]['tones']
            self.assertGreater(tones['wf'], tones['you'], theme)
            self.assertGreater(tones['you'], tones['help'], theme)

    def test_390_px_has_no_overflow_and_the_accounts_hold(self):
        got = self.in_frames(FACTS + r"""
      const f = document.querySelectorAll('iframe');
      done([facts(f[0].contentDocument, 'light'), facts(f[0].contentDocument, 'dark'), facts(f[1].contentDocument)]);""", widths=(390, 1440))
        for g in got:
            self.assertEqual(g['overflow'], 0)
        self.assertEqual(len(got[0]['rows']), 5)

    def test_the_arrow_keys_and_the_table(self):
        got = self.in_frames(r"""
      const d = document.querySelector('iframe').contentDocument, w = d.defaultView;
      const key = (k) => d.getElementById('wk-chart').dispatchEvent(new w.KeyboardEvent('keydown', {key: k, bubbles: true}));
      const tip = () => (d.getElementById('tip').hidden ? null : d.querySelector('#tip .t-h').textContent);
      d.getElementById('wk-chart').focus();
      key('ArrowRight'); const first = tip();
      key('Home'); const home = tip();
      key('ArrowRight'); const next = tip();
      key('End'); const end = tip();
      key('Escape'); const gone = tip();
      d.getElementById('wk-tbl').click(); await wait(100);
      const table = {rows: d.querySelectorAll('#wk-table tbody tr').length, chart: d.getElementById('wk-chart').hidden,
                     pressed: d.getElementById('wk-tbl').getAttribute('aria-pressed'), head: [...d.querySelectorAll('#wk-table th')].map((x) => x.textContent)};
      d.getElementById('wk-tbl').click(); await wait(100);
      done({first, home, next, end, gone, table, back: !d.getElementById('wk-chart').hidden && !!d.querySelector('#wk-chart svg')});""", widths=(1440,))
        page = self.payload()
        hours = page['series']['hours']
        at = lambda h: datetime.fromisoformat(h['at'])            # noqa: E731
        peak = max(range(len(hours)), key=lambda i: hours[i]['weight'])
        self.assertTrue(got['first'].endswith(' · ' + hm(at(hours[min(peak + 1, 167)]))), got['first'])   # from the spike on
        self.assertTrue(got['home'].endswith(' · ' + hm(at(hours[0]))), got['home'])
        self.assertTrue(got['next'].endswith(' · ' + hm(at(hours[1]))), got['next'])
        self.assertTrue(got['end'].endswith(' · ' + hm(at(hours[-1])) + ', so far'), got['end'])   # the hour now is not over
        self.assertIsNone(got['gone'])
        self.assertEqual((got['table']['rows'], got['table']['chart'], got['table']['pressed']), (8, True, 'true'))
        self.assertEqual(got['table']['head'], ['Day', 'Use', 'Workflows', 'Helpers', 'You', 'Most of it', 'Limits', 'Switches'])
        self.assertTrue(got['back'])


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class WhenTheWeekCannotShow(FramedPage):
    def shown(self, payload):
        with mock.patch.object(server, 'usage_view', lambda fresh=False: payload):
            return self.in_frames(FACTS + r"""
      done(facts(document.querySelector('iframe').contentDocument));""", widths=(1440,))

    def test_a_failed_read_says_so_quietly_and_the_accounts_still_show(self):
        got = self.shown({'days': 7, 'cuts': {}, 'moved': None, 'failed': True, 'state': 'ready'})
        self.assertEqual(got['none'], 'Your week can’t be read right now.')
        self.assertEqual(len(got['rows']), 5)
        self.assertEqual((got['highs'], got['mults'], got['legend']), ([], [], ''))

    def test_no_answer_at_all(self):
        # no week in any answer: "Reading your week…" while the page asks again every 10 s (on a very busy Mac the next
        # answer may come in time), and only after a minute of that does it say the week can't be read
        with mock.patch.object(server, 'usage_view', lambda fresh=False: None):
            got = self.in_frames(FACTS + r"""
      const d = document.querySelector('iframe').contentDocument, seen = [], t0 = performance.now();
      let ms = null;
      for (let i = 0; i < 1500 && ms === null; i++) {
        const t = (d.querySelector('.wk-none') || {}).textContent;
        if (t && seen[seen.length - 1] !== t) seen.push(t);
        if (t === 'Your week can’t be read right now.') ms = Math.round(performance.now() - t0);
        else await wait(100);
      }
      done(Object.assign(facts(d), {seen, ms}));""", widths=(1440,), budget=200000)
        self.assertEqual(got['seen'], ['Reading your week…', 'Your week can’t be read right now.'])
        self.assertIsNotNone(got['ms'], got['seen'])
        self.assertGreater(got['ms'], 30000)                                   # not at the first answer without one
        self.assertEqual(len(got['rows']), 5)

    def test_an_empty_week(self):
        page = copy.deepcopy(server.usage_view())
        for h in page['series']['hours']:
            h['weight'] = 0
            h['who'] = dict.fromkeys(h['who'], 0)
        for x in page['series']['days']:
            x['weight'] = 0
        page.update(cuts={}, insights=[], events=[])
        got = self.shown(page)
        self.assertEqual(got['none'], 'Your week shows here after Switchboard has read a day of your work.')
        self.assertEqual((got['highs'], got['mults']), ([], []))

    def test_a_calm_week_names_no_spike_and_invents_nothing(self):
        page = copy.deepcopy(server.usage_view())
        for h in page['series']['hours']:
            h['weight'] = 100.0
            h['who'] = {'chat': 20.0, 'helpers': 20.0, 'workflows': 60.0}
        page.update(insights.week_numbers(page), insights=[])
        got = self.shown(page)
        self.assertIsNone(got['peak'])                   # 1x an average hour: no spike to name
        self.assertEqual(got['highs'], [])
        self.assertEqual(got['by'], 'hour')

    def test_day_one_with_no_readings(self):
        page = copy.deepcopy(server.usage_view())
        for h in page['series']['hours'][:-3]:
            h['weight'] = 0
            h['who'] = dict.fromkeys(h['who'], 0)
        for x in page['series']['days'][:-1]:
            x['weight'] = 0
        page.update(readings={}, events=[], last_week=None, insights=[], average=None, peak=None)
        got = self.shown(page)
        self.assertIsNone(got['none'])
        self.assertEqual((got['lim'], got['sw']), ([], []))
        self.assertNotIn('Limit reached', got['legend'])
        self.assertEqual(got['highs'], [])
        self.assertTrue(got['avgLine'])                  # the page measures the same average itself


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class TheCleanerPage(FramedPage):
    def test_no_tags_beside_emails_one_line_cells_and_refills(self):
        got = self.in_frames(FACTS + r"""
      done(facts(document.querySelector('iframe').contentDocument));""", widths=(1440,))
        self.assertEqual([r['who'] for r in got['rows']], list(EMAILS))      # the email alone names a row
        self.assertEqual(got['head'], ['Account', '5-hour', 'Weekly', 'State'])    # names for a screen reader, not on the page
        for r in got['rows']:
            for cls, h in r['cells']:
                self.assertLessEqual(h, 22, (r['who'], cls, h))                # one line each
        self.assertEqual(got['rows'][3]['state'], 'Sign inUse')              # a row that needs a sign-in: a button, then Use
        html = read()
        self.assertNotIn('Not started', html)
        self.assertNotIn('kept by itself</span>', html)                       # the /login line is Add account's tooltip

    def test_no_mono_no_footer_and_the_about_line(self):
        got = self.in_frames(FACTS + r"""
      const d = document.querySelector('iframe').contentDocument, w = d.defaultView;
      const out = {};
      for (const h of ['accounts', 'artifacts', 'activity', 'settings']) {
        w.location.hash = '#' + h; await wait(700);
        out[h] = {light: facts(d, 'light').mono, dark: facts(d, 'dark').mono};
      }
      out.side = {foot: !!d.querySelector('.side-foot, #ver'), text: d.getElementById('side').textContent,
                  counts: [...d.querySelectorAll('#nav a')].map((a) => [a.querySelector('span').textContent, (a.querySelector('.count') || {}).textContent || null])};
      out.about = (d.getElementById('about') || {}).textContent;
      out.last = d.querySelector('#sec-settings > :last-child').id;
      out.port = w.location.port;
      out.keys = [...d.querySelectorAll('#set-list .r-head')].map((x) => x.textContent);
      out.gateway = d.querySelector('#row-gateway .r-title').title;
      d.dispatchEvent(new w.KeyboardEvent('keydown', {key: '?', bubbles: true})); await wait(100);
      out.shortcuts = !d.getElementById('kbd').hidden;
      d.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
      d.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'k', metaKey: true, bubbles: true})); await wait(100);
      const q = d.getElementById('pal-q'); q.value = 'gateway_on'; q.dispatchEvent(new w.Event('input', {bubbles: true})); await wait(50);
      out.found = [...d.querySelectorAll('.pal-it .lb')].map((x) => x.textContent);
      q.value = 'week'; q.dispatchEvent(new w.Event('input', {bubbles: true})); await wait(50);
      out.week = [...d.querySelectorAll('.pal-it .lb')].map((x) => x.textContent);
      done(out);""", widths=(1440,))
        for h in ('accounts', 'artifacts', 'activity', 'settings'):
            self.assertEqual(got[h], {'light': [], 'dark': []}, h)
        self.assertFalse(got['side']['foot'])
        self.assertNotIn('Keyboard shortcuts', got['side']['text'])
        self.assertNotIn(server.view()['version'], got['side']['text'])         # the version is in About only
        self.assertEqual([c[1] is not None for c in got['side']['counts']], [True, True, True, False, False])   # Chats counts
        self.assertEqual(got['about'], 'Switchboard %s · demo · 127.0.0.1:%s' % (server.view()['version'], got['port']))
        self.assertEqual(got['last'], 'newer')          # after About, only the line that says a newer version is out
        self.assertFalse(any('gateway_on' in k or 'auto_policy' in k for k in got['keys']))
        self.assertIn('gateway_on', got['gateway'])                            # in the row's tooltip
        self.assertTrue(got['shortcuts'])                                     # "?" still opens them
        self.assertIn('Gateway', got['found'])                                # and ⌘K still finds a key
        self.assertIn('Your week', got['week'])
        html = read()
        self.assertNotIn('Geist Mono', html)
        self.assertNotIn('--mono', html)
        self.assertNotIn('monospace', html)


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class NoSonnetButton(FramedPage):
    """The main page never pushes you to Sonnet for helpers. The
    Settings row stays as it was, and sends the switch's own POST."""

    def test_no_button_on_the_main_page_and_the_settings_switch_posts(self):
        got = self.in_frames(r"""
      const d = document.querySelector('iframe').contentDocument, w = d.defaultView, sent = [];
      const real = w.fetch;
      w.fetch = function (u, o) { if (o && o.method === 'POST') sent.push([String(u), JSON.parse(o.body || '{}')]); return real.apply(this, arguments); };
      const main = d.getElementById('sec-accounts').textContent;
      w.location.hash = '#settings'; await wait(900);
      const sw = d.querySelector('#row-models .sw');
      sw.click(); await wait(300);
      done({button: !!d.querySelector('#sec-accounts [data-do="helpersOn"], #sec-accounts [data-do="models"]'),
            words: /Turn on Sonnet/.test(main), sent: sent.filter((s) => s[0] === '/api/models')});""", widths=(1440,))
        self.assertFalse(got['button'])
        self.assertFalse(got['words'])
        self.assertEqual(got['sent'], [['/api/models', {'enabled': True}]])


class TheSwitchsPostInATempHome(ModelsBase):
    def test_the_body_the_page_sends_sets_the_one_variable(self):
        self.assertEqual(server.act('/api/models', {'enabled': True}), {'changed': True, 'note': None})
        with open(self.settings) as f:
            self.assertEqual(json.load(f)['env']['CLAUDE_CODE_SUBAGENT_MODEL'], 'sonnet')


if __name__ == '__main__':
    unittest.main()
