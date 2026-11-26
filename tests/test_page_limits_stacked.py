import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# An account's limits are one under another, never side by side:
#
# - every limit is one line: its name, how much is used, its bar with its switch line, and when it refills; 5-hour
#   (when there is a 5-hour number to show) over weekly; the bars start at one left edge and are one width in every
#   line of every account, so they compare by eye; at 680, 1280 and 1440 px, and at the widths where the layout
#   changes (521, 901, 1181, 1400);
# - the plan is under the email, small and grey; there is no Plan column and no Refills column, and no row of column
#   names to read (a screen reader still gets one);
# - a limit says when it refills on its own line, the 5-hour one too once its window runs; one not started says nothing;
# - an account with no 5-hour limit, or a reading too old to show, has no 5-hour line: its weekly line alone, why on
#   the weekly line's hover;
# - a model's week has no line of its own: the weekly line's hover says each one, a counted one or not, and two are
#   two sentences there (the red note: tests/test_page_rows_two_limits.py);
# - an old reading says its age under its numbers, from where the bars start;
# - the email keeps at least the room it had beside the old columns, and no refill time is cut short;
# - "Auto-switch at [90 %] 5-hour [95 %] weekly" reads as one line (no model's field: it is set in Settings).
#
# Headless Chrome (a throwaway profile, no window) on the demo's made-up accounts.
import os
import re
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import demo
import test_app  # its headless-Chrome page (it has no tests of its own here)
import test_page_links_and_logo as framed  # its page in frames of a given width

WORK, MAIL, FERN, HARBOR, IVY = ('maya@northwind.example', 'maya.reyes@mail.example', 'maya@fernhill.example',
                                 'm.reyes@harbor.example', 'maya@ivy.example')
WIDTHS = (680, 1280, 1440)
REFILLS = re.compile(r'^Refills ((Mon|Tue|Wed|Thu|Fri|Sat|Sun) )?\d{1,2}(:\d\d)? [AP]M$')

# Every row of every frame: where its email, its plan and each of its limit lines sit.
LOOK = r"""
    const box = (el) => { const r = el.getBoundingClientRect(); return {left: Math.round(r.left), right: Math.round(r.right), top: Math.round(r.top), bottom: Math.round(r.bottom), width: Math.round(r.width)}; };
    const out = {};
    for (const f of frames) {
      const d = doc(f), w = f.contentWindow.innerWidth, rows = {};
      for (const b of d.querySelectorAll('#acc .arow')) {
        const em = b.querySelector('.em'), sub = b.querySelector('.sub'), lines = [];
        for (const c of b.querySelectorAll('.c-five, .c-week, .note[data-kind="model"]')) {
          const kind = c.classList.contains('none') ? 'none' : c.dataset.kind;
          const own = (q) => [...c.children].find((x) => x.matches(q));
          lines.push({kind, name: own('.ml').textContent, nameAt: box(own('.ml')), pct: own('.pct, .dash').textContent, pctAt: box(own('.pct, .dash')),
            bar: own('.bar') ? box(own('.bar')) : null, refill: own('.rf') ? own('.rf').textContent : null, refillAt: own('.rf') ? box(own('.rf')) : null,
            refillCut: own('.rf') ? own('.rf').scrollWidth > own('.rf').clientWidth : false});
        }
        const plan = sub.cloneNode(true);                 // the plan alone, without a count of chats after it
        plan.querySelectorAll('.chn').forEach((x) => x.remove());
        rows[em.textContent] = {em: box(em), cut: em.scrollWidth > em.clientWidth, sub: box(sub), plan: plan.textContent.replace(/ · $/, ''), planSize: getComputedStyle(sub).fontSize,
          planColour: getComputedStyle(sub).color, muted: getComputedStyle(d.querySelector('.strip .w')).color, ink: getComputedStyle(em).color,
          state: box(b.querySelector('.c-state')), lines, row: box(b), room: Math.round(em.parentNode.getBoundingClientRect().width)};
      }
      const head = d.querySelector('.ahead');
      out[w] = {rows, wide: d.documentElement.scrollWidth > w, planColumn: !!d.querySelector('.c-plan'), refillsColumn: !!d.querySelector('.c-reset'),
        head: [...head.querySelectorAll('[role="columnheader"]')].map((x) => x.textContent), headSize: [box(head).width, box(head).bottom - box(head).top]};
    }
    done(out);
"""


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class OneUnderAnother(framed.FramedPage):
    got = None                                           # the three frames are drawn once: every test here reads them

    def setUp(self):
        super().setUp()
        if OneUnderAnother.got is None:
            OneUnderAnother.got = self.in_frames(LOOK, widths=WIDTHS)

    def frames(self):
        for w in WIDTHS:
            yield w, self.got[str(w)]

    def test_each_limit_is_under_the_one_before_it(self):
        for w, page in self.frames():
            self.assertEqual(list(page['rows']), [WORK, MAIL, FERN, HARBOR, IVY])
            for email, row in page['rows'].items():
                with self.subTest(width=w, account=email):
                    kinds = [x['kind'] for x in row['lines']]
                    # no 5-hour line for MAIL (no 5-hour limit, and its reading is too old to show); no model's line
                    self.assertEqual(kinds, {MAIL: ['week']}.get(email, ['five', 'week']))
                    self.assertEqual([x['name'] for x in row['lines']], [{'five': '5-hour', 'week': 'Weekly'}[k] for k in kinds])
                    for above, below in zip(row['lines'], row['lines'][1:]):
                        self.assertGreaterEqual(below['nameAt']['top'], above['nameAt']['bottom'])     # under it, not beside it
                        self.assertLessEqual(below['nameAt']['top'] - above['nameAt']['bottom'], 12)   # and close: one block
                        self.assertEqual(below['nameAt']['left'], above['nameAt']['left'])

    def test_every_bar_starts_at_one_edge_and_is_one_width(self):
        for w, page in self.frames():
            bars = [x['bar'] for row in page['rows'].values() for x in row['lines'] if x['bar']]
            with self.subTest(width=w):
                self.assertEqual(len(bars), 9)                       # 4 five-hour, 5 weekly (MAIL's empty), no model's
                self.assertEqual(len(set(b['left'] for b in bars)), 1, bars)
                self.assertEqual(len(set(b['width'] for b in bars)), 1, bars)
                self.assertGreaterEqual(bars[0]['width'], 96)        # a bar to read, not a tick
                for row in page['rows'].values():
                    for x in row['lines']:
                        self.assertLess(x['nameAt']['right'], x['pctAt']['right'])      # name, then percent,
                        if x['bar']:
                            self.assertLessEqual(x['pctAt']['right'], x['bar']['left'])  # then its bar on the same line
                            self.assertLess(abs((x['bar']['top'] + x['bar']['bottom']) - (x['pctAt']['top'] + x['pctAt']['bottom'])), 4)

    def test_the_plan_is_under_the_email_small_and_grey(self):
        plans = {WORK: 'Team', MAIL: 'Max 20x', FERN: 'Team', HARBOR: 'Enterprise', IVY: 'Max 5x'}
        for w, page in self.frames():
            self.assertFalse(page['planColumn'])
            for email, row in page['rows'].items():
                with self.subTest(width=w, account=email):
                    self.assertEqual(row['plan'], plans[email])
                    self.assertEqual(row['sub']['left'], row['em']['left'])
                    self.assertGreaterEqual(row['sub']['top'], row['em']['bottom'])
                    self.assertLessEqual(row['sub']['top'] - row['em']['bottom'], 8)
                    self.assertEqual(row['planSize'], '12px')
                    self.assertEqual(row['planColour'], row['muted'])
                    self.assertNotEqual(row['planColour'], row['ink'])
                    self.assertFalse(row['cut'])                     # the whole email, at every width
                    if w > 900:
                        self.assertGreaterEqual(row['room'], old_room(row, w))   # and at least the room the old columns left it

    def test_no_column_names_to_read_and_nothing_past_the_page(self):
        for w, page in self.frames():
            with self.subTest(width=w):
                self.assertFalse(page['refillsColumn'])
                self.assertEqual(page['head'], ['Account', '5-hour', 'Weekly', 'State'])     # for a screen reader
                self.assertLessEqual(max(page['headSize']), 1)                                # not on the page to read
                self.assertFalse(page['wide'])

    def test_a_refill_is_on_its_own_limits_line(self):
        for w, page in self.frames():
            rows = page['rows']
            with self.subTest(width=w):
                for email in (WORK, FERN, IVY):                      # every weekly window here is running
                    week = [x for x in rows[email]['lines'] if x['kind'] == 'week'][0]
                    # WORK's week is ahead of its pace: when it would be full is said in the refill's place
                    self.assertRegex(week['refill'], REFILLS if email != WORK else REFILLS.pattern.replace('Refills', 'Full by'))
                    self.assertGreaterEqual(week['refillAt']['left'], week['bar']['right'])       # after its bar, on its line
                    self.assertLess(abs(week['refillAt']['top'] - week['nameAt']['top']), 4)
                # HARBOR's Fable week is over its line: the red note takes the refill's place (both refills on hover);
                # MAIL's reading is too old: not known, nothing to refill
                self.assertIsNone(rows[HARBOR]['lines'][1]['refill'])
                self.assertIsNone(rows[MAIL]['lines'][0]['refill'])
                five = dict((e, [x for x in rows[e]['lines'] if x['kind'] == 'five']) for e in rows)
                self.assertRegex(five[WORK][0]['refill'], REFILLS)   # a 5-hour window that runs says when it refills
                self.assertRegex(five[IVY][0]['refill'], REFILLS)
                self.assertIsNone(five[FERN][0]['refill'])           # one not started says nothing
                self.assertIsNone(five[HARBOR][0]['refill'])
                self.assertEqual(five[MAIL], [])                     # no 5-hour line at all
                self.assertFalse([e for e in rows for x in rows[e]['lines'] if x['refillCut']])


def old_room(row, w):
    """The room an email had beside the old columns in a row this wide (px): the row less its fixed columns and gaps,
    758 from 1400 px (plan, 5-hour, weekly, refills, state), 604 under it. It never gets less."""
    return row['row']['width'] - 16 - (758 if w >= 1400 else 604)


def weekday_late(now):
    """A moment 3 to 4 days off at 10:58 PM in this Mac's time zone: the longest words a refill has ("Wed 10:58 PM")."""
    day = datetime.fromtimestamp(now) + timedelta(days=3)
    return day.replace(hour=22, minute=58, second=0, microsecond=0).timestamp()


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class WhereTheLayoutChanges(framed.FramedPage):
    def test_stacked_whole_and_uncut_on_each_side_of_every_change(self):
        demo.view()
        demo.STATE['accounts'][IVY]['usage']['weekly']['resets'] = iso(weekday_late(time.time()))
        demo.STATE['accounts'][IVY]['usage']['scoped'][0]['resets'] = demo.STATE['accounts'][IVY]['usage']['weekly']['resets']
        demo.STATE['accounts'][IVY]['usage']['weekly']['pct'] = 5     # too little used for its pace to say: its refill stays
        got = self.in_frames(LOOK, widths=(521, 901, 1181, 1400))
        for w in (521, 901, 1181, 1400):
            page = got[str(w)]
            bars = [x['bar'] for row in page['rows'].values() for x in row['lines'] if x['bar']]
            with self.subTest(width=w):
                self.assertFalse(page['wide'])
                self.assertEqual(len(set((b['left'], b['width']) for b in bars)), 1, bars)
                self.assertGreaterEqual(bars[0]['width'], 96)
                self.assertRegex(page['rows'][IVY]['lines'][1]['refill'], r'^Refills (Mon|Tue|Wed|Thu|Fri|Sat|Sun) 10:5\d PM$')
                for email, row in page['rows'].items():
                    self.assertFalse(row['cut'], email)
                    for above, below in zip(row['lines'], row['lines'][1:]):
                        self.assertGreaterEqual(below['nameAt']['top'], above['nameAt']['bottom'], email)
                        self.assertEqual(below['nameAt']['left'], above['nameAt']['left'], email)
                    for x in row['lines']:
                        self.assertFalse(x['refillCut'], (email, x['refill']))
                        if x['refill']:
                            self.assertLessEqual(x['refillAt']['right'], row['state']['left'] if w > 900 else row['row']['right'], (email, x['refill']))
                    if w > 900:
                        self.assertGreaterEqual(row['room'], old_room(row, w), email)       # the email's room
                        self.assertGreaterEqual(row['state']['left'], max(x['refillAt']['right'] if x['refill'] else x['pctAt']['right'] for x in row['lines']))


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class OnAPhone(framed.FramedPage):
    def test_a_refill_goes_under_its_bar_and_the_lines_stay_stacked(self):
        got = self.in_frames(LOOK, widths=(390,))['390']
        self.assertFalse(got['wide'])
        bars = [x['bar'] for row in got['rows'].values() for x in row['lines'] if x['bar']]
        self.assertEqual(len(set((b['left'], b['width']) for b in bars)), 1, bars)
        for email, row in got['rows'].items():
            with self.subTest(account=email):
                self.assertFalse(row['cut'])
                for above, below in zip(row['lines'], row['lines'][1:]):
                    self.assertGreaterEqual(below['nameAt']['top'], above['nameAt']['bottom'])
                for x in row['lines']:
                    if x['refill']:
                        self.assertGreaterEqual(x['refillAt']['top'], x['bar']['bottom'])     # under the bar
                        self.assertEqual(x['refillAt']['left'], x['bar']['left'])


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class WhatALineSays(test_app.ChromePage):
    ROWS = r"""
      for (let i = 0; i < 200 && all('#acc .arow').length < 5; i++) await wait(50);
      await wait(100);
      const row = (e) => all('#acc .arow').find((b) => b.querySelector('.em').textContent === e);
      const line = (c) => c && {text: c.textContent, title: c.title, bar: !!c.querySelector(':scope > .bar'),
        tick: c.querySelector(':scope > .bar > b') ? c.querySelector(':scope > .bar > b').style.left : null,
        barTitle: c.querySelector(':scope > .bar') ? c.querySelector(':scope > .bar').title : null,
        refillTitle: c.querySelector(':scope > .rf') ? c.querySelector(':scope > .rf').title : null};
      const of = (e) => ({five: line(row(e).querySelector('.c-five')), week: line(row(e).querySelector('.c-week')),
        model: line(row(e).querySelector('.note[data-kind="model"]'))});
      const at = (el) => { const r = el.getBoundingClientRect(); return [Math.round(r.left), Math.round(r.top), Math.round(r.width)]; };
"""

    def test_no_five_hour_limit_has_no_5_hour_line(self):
        # (it was a dash with no bar, why on hover: now no line at all, why on the weekly line's hover)
        got = self.run_page(self.ROWS + "done(Object.assign(of('%s'), {hidden: row('%s').querySelector('.c-gone').textContent}));"
                            % (MAIL, MAIL))
        self.assertIsNone(got['five'])
        self.assertEqual(got['hidden'], 'No 5-hour limit')                      # for a screen reader
        self.assertEqual(got['week']['title'].split('\n')[-1], 'Anthropic reports no 5-hour limit for this account')
        self.assertTrue(got['week']['bar'])

    def test_a_running_window_says_its_refill_and_how_far_off(self):
        got = self.run_page(self.ROWS + "done(of('%s'));" % WORK)
        self.assertRegex(got['five']['text'], r'^5-hour62%Refills \d{1,2}(:\d\d)? [AP]M$|^5-hour62%Refills \w{3} \d{1,2}(:\d\d)? [AP]M$')
        self.assertRegex(got['five']['refillTitle'], r', in \d+ (min|hours?)$')
        self.assertRegex(got['five']['title'], r'^62% used, switches at 90%\. Refills .+, in \d+ (min|hours?)$')
        self.assertRegex(got['week']['title'], r'^70% used, switches at 95%\. Refills .+, in \d+ (hours?|days?)'
                                               r'\nAt this pace, full by [^\n]+$')       # ahead of its week
        self.assertEqual((got['five']['tick'], got['week']['tick']), ('90%', '95%'))

    def test_a_models_week_is_said_on_the_weekly_lines_hover(self):
        # (it was a line of its own, with a bar and a switch line)
        got = self.run_page(self.ROWS + "done(of('%s'));" % IVY)
        self.assertIsNone(got['model'])
        self.assertEqual(got['week']['title'].split('\n')[1:], ['Fable’s own week: 80% used, switches at 95%'])
        self.assertEqual(got['week']['tick'], '95%')

    def test_a_models_week_that_is_not_counted_has_no_switch_line_in_the_hover(self):
        demo._ready()
        demo.FACTS['auto'].update(model=None, models=[])
        got = self.run_page(self.ROWS + "done(of('%s'));" % IVY)
        self.assertIsNone(got['model'])
        self.assertEqual(got['week']['title'].split('\n')[1:], ['Fable’s own week: 80% used'])
        self.assertEqual(got['week']['tick'], '95%')                 # the weekly line keeps its own

    def test_two_model_weeks_are_two_sentences_on_the_hover_and_no_lines(self):
        demo.view()
        ivy = demo.STATE['accounts'][IVY]['usage']
        ivy['scoped'] = [dict(ivy['scoped'][0]), dict(ivy['scoped'][0], name='Opus', pct=30)]
        got = self.run_page(self.ROWS + """
      const r = row('%s');
      done({title: r.querySelector('.c-week').title, models: r.querySelectorAll('[data-kind="model"]').length,
            bars: [...r.querySelectorAll('.bar')].map(at), heard: [...r.querySelectorAll('.ml')].map((m) => m.getAttribute('aria-hidden'))});
""" % IVY)
        self.assertEqual(got['title'].split('\n')[1:], ['Fable’s own week: 80% used, switches at 95%', 'Opus’s own week: 30% used'])
        self.assertEqual(got['models'], 0)
        self.assertEqual(len(got['bars']), 2)                                     # 5-hour and weekly
        self.assertEqual(len(set((b[0], b[2]) for b in got['bars'])), 1, got['bars'])
        # a screen reader hears "5-hour" and "Weekly" from the columns' names
        self.assertEqual(got['heard'], ['true', 'true'])

    def test_an_old_reading_says_its_age_under_its_numbers_from_where_the_bars_start(self):
        demo.view()
        demo.STATE['accounts'][HARBOR]['usage_at'] = time.time() - 7200          # too old to show as numbers (as the demo)
        got = self.run_page(self.ROWS + """
      const r = row('%s'), age = r.querySelector('.note[data-kind="age"]'), bar = r.querySelector('.c-week > .bar');
      done({age: age.textContent, at: at(age), bar: at(bar), pct: [...r.querySelectorAll('.c[data-kind] > .pct')].map((x) => x.textContent),
            bars: r.querySelectorAll('.c[data-kind] > .bar').length});
""" % HARBOR)
        self.assertEqual(got['age'], 'checked 2 hours ago')
        self.assertEqual(got['pct'], ['—'])                                       # not known: a weekly dash, its empty bar
        self.assertEqual(got['bars'], 1)                                          # and no 5-hour line
        self.assertEqual(got['at'][0], got['bar'][0])                             # the age starts where the bars start
        self.assertGreater(got['at'][1], got['bar'][1])                           # and is under them


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class TheSwitchLineReadsAsOneLine(framed.FramedPage):
    def test_auto_switch_and_its_two_fields_are_one_line_in_a_wide_window(self):
        got = self.in_frames(r"""
    const out = {};
    for (const f of frames) {
      const d = doc(f), at = (el) => { const r = el.getBoundingClientRect(); return [Math.round(r.left), Math.round(r.right), Math.round((r.top + r.bottom) / 2)]; };
      const strip = d.getElementById('auto');
      const bits = [...strip.querySelectorAll('.auto-sw, .w, input.line')].filter((x) => x.offsetParent && x.getBoundingClientRect().width);
      out[f.contentWindow.innerWidth] = {bits: bits.map((x) => [x.tagName === 'INPUT' ? x.id : x.textContent.trim(), ...at(x)]),
        first: at(d.querySelector('#acc .arow')), edge: getComputedStyle(strip).borderBottomWidth, strip: at(strip)};
    }
    done(out);
""", widths=(1280, 1440))
        for w in ('1280', '1440'):
            bits = got[w]['bits']
            with self.subTest(width=w):
                # (the model's field left this line: it is set in Settings)
                self.assertEqual([b[0] for b in bits], ['Auto-switch', 'at', 'line5', '5-hour', 'lineW', 'weekly'])
                self.assertLessEqual(max(b[3] for b in bits) - min(b[3] for b in bits), 2)        # on one line
                for a, b in zip(bits, bits[1:]):
                    self.assertGreaterEqual(b[1], a[2])                                           # in reading order
                    self.assertLessEqual(b[1] - a[2], 24, (a, b))                                  # and close: a sentence, not columns
                self.assertEqual(got[w]['edge'], '1px')                                           # a hairline, then the accounts


if __name__ == '__main__':
    unittest.main()
