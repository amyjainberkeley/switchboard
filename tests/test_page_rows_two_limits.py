import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The page's accounts list shows at most two limits per account:
#
# - 5-hour only when there is a 5-hour number to show: no 5-hour line at all (no label, no dash, no empty bar, no gap)
#   for an account with no 5-hour limit, for a reading too old to show, or for one never read. 0% not started stays.
# - Weekly always; not known, a dash with its age under it.
# - No line for a model's own week. The weekly line's hover says each model week the reading has; a model week that
#   auto-switch counts, at or over its switch line, is one short red note at the end of the weekly line (the highest
#   when several are), never one auto-switch does not count, never under its line.
# - A row with one line and a row with two line up: the first limit line beside the email, the state column beside
#   both, at 680 and 1280 px. A screen reader still gets four cells a row (a hidden one for the 5-hour column).
# - The switch line names only what the rows show: no model field; the 5-hour field only when an account has a
#   5-hour limit. The model's line is set in Settings, as before.
#
# Headless Chrome (a throwaway profile, no window) on the demo's made-up accounts.
import os
import sys
import time
import unittest
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import demo
import test_app  # its headless-Chrome page
import test_page_links_and_logo as framed  # its page in frames of a given width

WORK, MAIL, FERN, HARBOR, IVY = ('maya@northwind.example', 'maya.reyes@mail.example', 'maya@fernhill.example',
                                 'm.reyes@harbor.example', 'maya@ivy.example')
PAGE = os.path.join(HERE, '..', 'switchboard', 'web', 'index.html')


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def accounts():
    demo._ready()
    return demo.STATE['accounts']


def fable(pct, resets=None, name='Fable'):
    return {'name': name, 'pct': pct, 'resets': resets}


def set_up(old=HARBOR, never=None, over=None, counted=('Fable',)):
    """The demo with every reading fresh but `old`'s, too old to show; `never` never read; `over` given a Fable week at
    97 (over its 95 line), and IVY's Fable week at 80 (under it); no other model week."""
    st = accounts()
    now = time.time()
    for a in st.values():
        a['usage']['scoped'] = []
        a['usage_at'] = now - 40
    st[IVY]['usage']['scoped'] = [fable(80, st[IVY]['usage']['weekly']['resets'])]
    if old:
        st[old]['usage_at'] = now - 4 * 86400
    if never:
        st[never].update(usage=None, usage_at=None)
    if over:
        st[over]['usage']['scoped'] = [fable(97, st[over]['usage']['weekly']['resets'])]
    demo.FACTS['auto'].update(model=counted[0] if counted else None, models=list(counted), model_at=95)
    return st


ROWS = r"""
      for (let i = 0; i < 200 && all('#acc .arow').length < 5; i++) await wait(50);
      await wait(150);
      const row = (e) => all('#acc .arow').find((b) => b.querySelector('.em').textContent === e);
      const box = (el) => { const r = el.getBoundingClientRect(); return {left: Math.round(r.left), right: Math.round(r.right), top: Math.round(r.top), bottom: Math.round(r.bottom)}; };
      const of = (e) => { const r = row(e), note = r.querySelector('.c-week .mnote');
        return {cells: [...r.children].filter((c) => c.getAttribute('role') === 'cell').map((c) => c.classList.contains('c-week') ? 'week' : c.classList.contains('c-five') ? 'five' : c.classList.contains('c-who') ? 'who' : c.classList.contains('c-state') ? 'state' : 'hidden:' + c.textContent),
          lines: [...r.querySelectorAll('.c[data-kind]')].map((c) => [c.dataset.kind, c.querySelector('.ml').textContent, c.querySelector('.pct').textContent]),
          dashes: r.querySelectorAll('.dash').length, models: r.querySelectorAll('[data-kind="model"]').length, bars: r.querySelectorAll('.bar').length,
          week: r.querySelector('.c-week').title, refill: r.querySelector('.c-week > .rf') ? r.querySelector('.c-week > .rf').textContent : null,
          note: note ? {text: note.textContent, title: note.title, colour: getComputedStyle(note).color, at: box(note), pct: box(r.querySelector('.c-week > .pct')), bar: box(r.querySelector('.c-week > .bar'))} : null,
          red: getComputedStyle(document.documentElement).getPropertyValue('--red').trim()};
      };
"""


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class TheLines(test_app.ChromePage):
    def test_no_5_hour_line_without_a_5_hour_number(self):
        set_up(old=HARBOR, never=FERN)
        got = self.run_page(ROWS + "done({mail: of('%s'), harbor: of('%s'), fern: of('%s'), work: of('%s')});"
                            % (MAIL, HARBOR, FERN, WORK))
        for key, why in (('mail', 'No 5-hour limit'), ('harbor', 'Not known'), ('fern', 'Not known')):
            with self.subTest(account=key):
                r = got[key]
                self.assertEqual([l[0] for l in r['lines']], ['week'])                # one line: no label, no dash
                self.assertEqual((r['dashes'], r['bars']), (0, 1))                    # no empty 5-hour bar
                # a screen reader still gets the table's four cells, the 5-hour one saying why it is empty
                self.assertEqual(r['cells'], ['who', 'hidden:' + why, 'week', 'state'])
        self.assertEqual(got['harbor']['lines'], [['week', 'Weekly', '—']])            # too old: a dash, and its age
        self.assertEqual(got['work']['cells'], ['who', 'five', 'week', 'state'])        # a 5-hour number: its line

    def test_a_5_hour_line_at_0_not_started_stays(self):
        set_up(old=None)
        got = self.run_page(ROWS + "done(of('%s'));" % FERN)
        self.assertEqual(got['lines'], [['five', '5-hour', '0%'], ['week', 'Weekly', '23%']])

    def test_no_line_for_a_models_week(self):
        set_up(old=None, over=HARBOR)
        got = self.run_page(ROWS + "done({ivy: of('%s'), harbor: of('%s'), all: document.querySelectorAll('#acc [data-kind=\"model\"]').length});"
                            % (IVY, HARBOR))
        self.assertEqual(got['all'], 0)
        self.assertEqual([l[0] for l in got['ivy']['lines']], ['five', 'week'])
        self.assertEqual(got['ivy']['bars'], 2)
        self.assertEqual(got['harbor']['bars'], 2)                                    # the note has no bar

    def test_the_weekly_hover_says_each_model_week(self):
        set_up(old=None)
        st = accounts()
        st[MAIL]['usage']['scoped'] = [fable(40, iso(time.time() + 2 * 86400 + 1800))]   # its own reset, not the week's
        got = self.run_page(ROWS + "done({ivy: of('%s'), mail: of('%s'), work: of('%s')});" % (IVY, MAIL, WORK))
        ivy = got['ivy']['week'].split('\n')
        self.assertRegex(ivy[0], r'^65% used, switches at 95%\. Refills .+, in \d+ (hours?|days?)$')
        self.assertEqual(ivy[1:], ['Fable’s own week: 80% used, switches at 95%'])
        mail = got['mail']['week'].split('\n')
        self.assertRegex(mail[1], r'^Fable’s own week: 40% used, switches at 95%\. Refills (\w{3} )?\d{1,2}:\d{2} [AP]M$')
        self.assertEqual(mail[2], 'Anthropic reports no 5-hour limit for this account')
        self.assertRegex('\n'.join(mail[3:]), r'^At this pace, full by [^\n]+$')       # 91% used: ahead of its week
        self.assertRegex('\n'.join(got['work']['week'].split('\n')[1:]), r'^At this pace, full by [^\n]+$')   # no more

    def test_a_model_week_auto_switch_does_not_count_has_no_switch_line_in_the_hover(self):
        set_up(old=None, counted=())
        got = self.run_page(ROWS + "done(of('%s'));" % IVY)
        self.assertEqual(got['week'].split('\n')[1:], ['Fable’s own week: 80% used'])


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class TheRedNote(test_app.ChromePage):
    def note(self, pct, counted=('Fable',), more=()):
        set_up(old=None, counted=counted)
        st = accounts()
        resets = st[HARBOR]['usage']['weekly']['resets']
        st[HARBOR]['usage']['scoped'] = [fable(pct, resets)] + [fable(p, resets, n) for n, p in more]
        return self.run_page(ROWS + "done(of('%s'));" % HARBOR)

    def test_at_or_over_the_counted_line_a_red_note_ends_the_weekly_line(self):
        got = self.note(97)
        self.assertEqual(got['note']['text'], 'Fable 97%')
        self.assertEqual(got['lines'], [['five', '5-hour', '0%'], ['week', 'Weekly', '5%']])    # no line of its own
        self.assertEqual(got['note']['colour'], 'rgb(220, 38, 38)')                   # the page's red
        at, pct, bar = got['note']['at'], got['note']['pct'], got['note']['bar']
        self.assertLess(abs((at['top'] + at['bottom']) - (pct['top'] + pct['bottom'])), 4)   # on the weekly line
        self.assertGreaterEqual(at['left'], bar['right'])                             # at its end, after the bar
        self.assertIsNone(got['refill'])                                              # in the refill's place:
        self.assertRegex(got['note']['title'], r'^Fable’s own week: 97% used, switches at 95%\. Refills .+, in \d+ (hours?|days?)$')
        self.assertRegex(got['week'].split('\n')[0], r'\. Refills .+, in \d+ (hours?|days?)$')      # both on hover
        self.assertEqual(self.note(95)['note']['text'], 'Fable 95%')                   # at its line

    def test_never_under_its_line_nor_for_a_week_auto_switch_does_not_count(self):
        self.assertIsNone(self.note(94)['note'])
        self.assertRegex(self.note(94)['refill'], r'^Refills ')                       # the refill stays
        self.assertIsNone(self.note(99, counted=())['note'])
        self.assertIsNone(self.note(99, counted=('Opus',))['note'])

    def test_the_highest_counted_one_when_several_are_over(self):
        got = self.note(96, counted=('Fable', 'Opus'), more=(('Opus', 99), ('Sonnet', 100)))
        self.assertEqual(got['note']['text'], 'Opus 99%')

    def test_it_is_one_constant_to_take_out(self):
        with open(PAGE, encoding='utf-8') as f:
            html = f.read()
        self.assertEqual(html.count('const MODEL_OVER_NOTE = true;'), 1)
        self.assertIn('if (!MODEL_OVER_NOTE) return null;', html)


# Every frame: each row's email, state cell and limit lines, where they sit; the switch line's words and fields.
LOOK = r"""
    const box = (el) => { const r = el.getBoundingClientRect(); return {left: Math.round(r.left), right: Math.round(r.right), top: Math.round(r.top), bottom: Math.round(r.bottom), mid: Math.round((r.top + r.bottom) / 2)}; };
    const out = {};
    for (const f of frames) {
      const d = doc(f), w = f.contentWindow.innerWidth, rows = {};
      for (const b of d.querySelectorAll('#acc .arow')) {
        const em = b.querySelector('.em'), st = b.querySelector('.c-state'), lines = [...b.querySelectorAll('.c[data-kind]')];
        const kids = [...st.children].filter((x) => x.offsetParent && x.getBoundingClientRect().width);
        const who = [em, b.querySelector('.sub'), b.querySelector('.sig')].filter((x) => x && x.getBoundingClientRect().height);
        rows[em.textContent] = {em: box(em), row: box(b), state: box(st), stateTops: [...new Set(kids.map((x) => box(x).mid))].length,
          whoBottom: Math.max(...who.map((x) => box(x).bottom)), cellsBottom: Math.max(...lines.map((c) => box(c).bottom)),
          lines: lines.map((c) => ({kind: c.dataset.kind, name: box(c.querySelector('.ml')), pct: box(c.querySelector('.pct')), bar: box(c.querySelector('.bar'))})),
          cut: em.scrollWidth > em.clientWidth};
      }
      const strip = d.getElementById('auto');
      out[w] = {rows, wide: d.documentElement.scrollWidth > w,
        strip: [...strip.querySelectorAll('.auto-sw, .w, input.line')].filter((x) => x.offsetParent && x.getBoundingClientRect().width)
          .map((x) => [x.tagName === 'INPUT' ? x.id : x.textContent.trim(), box(x).mid])};
    }
    done(out);
"""


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class OneLineAndTwoLineRows(framed.FramedPage):
    def test_they_line_up_at_680_and_1280(self):
        set_up(old=HARBOR, over=FERN)
        got = self.in_frames(LOOK, widths=(680, 1280))
        for w in ('680', '1280'):
            page = got[w]
            with self.subTest(width=w):
                self.assertFalse(page['wide'])
                rows = page['rows']
                self.assertEqual([len(rows[e]['lines']) for e in (WORK, MAIL, FERN, HARBOR, IVY)], [2, 1, 2, 1, 2])
                bars = [l['bar'] for r in rows.values() for l in r['lines']]
                self.assertEqual(len(set((b['left'], b['right']) for b in bars)), 1, bars)    # one left edge, one width
                one, two = rows[MAIL], rows[WORK]
                if w == '1280':
                    # the first limit line sits beside the email, in a one-line row as in a two-line one
                    for r in (one, two):
                        self.assertLess(abs(r['lines'][0]['name']['mid'] - r['em']['mid']), 4)
                        self.assertLess(abs(r['state']['top'] - r['row']['top'] - (two['state']['top'] - two['row']['top'])), 2)
                    harbor = rows[HARBOR]                                     # one line (and its age), no sign-in line
                    self.assertLess(harbor['row']['bottom'] - harbor['row']['top'], two['row']['bottom'] - two['row']['top'])
                else:
                    # a block: the email and its state on one line, the limits under the plan (and the sign-in line);
                    # the one line where the first of two is
                    gap = lambda r: r['lines'][0]['name']['top'] - r['whoBottom']          # noqa: E731
                    self.assertLess(abs(gap(one) - gap(two)), 2)
                    self.assertLess(abs(gap(rows[HARBOR]) - gap(two)), 2)
                    for r in (one, two):
                        self.assertLess(abs(r['state']['mid'] - r['em']['mid']), 6)
                for email, r in rows.items():
                    self.assertFalse(r['cut'], email)
                    self.assertEqual(r['stateTops'], 1, email)                                 # the state cell unwrapped
                    # no hole: the row ends its padding under what it shows (the limits, or the email and plan)
                    self.assertLessEqual(r['row']['bottom'] - max(r['cellsBottom'], r['whoBottom']), 17, email)

    def test_the_switch_line_names_only_what_the_rows_show(self):
        set_up(old=None)
        got = self.in_frames(LOOK, widths=(1280,))['1280']
        self.assertEqual([b[0] for b in got['strip']], ['Auto-switch', 'at', 'line5', '5-hour', 'lineW', 'weekly'])
        self.assertLessEqual(max(b[1] for b in got['strip']) - min(b[1] for b in got['strip']), 2)

    def test_with_no_5_hour_limit_anywhere_the_switch_line_says_weekly_alone(self):
        st = set_up(old=None)
        for a in st.values():
            a['usage']['session'] = None
        got = self.in_frames(LOOK, widths=(680, 1280))
        for w in ('680', '1280'):
            with self.subTest(width=w):
                self.assertEqual([b[0] for b in got[w]['strip']], ['Auto-switch', 'at', 'lineW', 'weekly'])
                self.assertFalse(got[w]['wide'])
        mids = [b[1] for b in got['1280']['strip']]
        self.assertLessEqual(max(mids) - min(mids), 2)                                         # one line


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class SettingsKeepsTheModelLine(test_app.ChromePage):
    def test_the_model_line_is_set_in_settings(self):
        set_up(old=None)
        got = self.run_page("""
      await wait(600);
      const strip = {model: !!document.querySelector('#auto [data-key="model_at"]'), five: !!$('#line5')};
      location.hash = '#settings'; await wait(900);
      const labels = all('#row-lines label').map((x) => x.textContent.trim());
      const m = document.querySelector('#row-lines input[data-key="model_at"]');
      const label = m.getAttribute('aria-label'), desc = $('#row-lines .r-desc').textContent;
      m.focus(); m.value = '92'; m.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
      await wait(900);
      done({strip, labels, label, desc, value: document.querySelector('#row-lines input[data-key="model_at"]').value,
            sent: sent.filter((x) => x[0] === '/api/auto').map((x) => x[1])});
""")
        self.assertEqual(got['strip'], {'model': False, 'five': True})                # not on the switch line
        self.assertEqual(got['labels'], ['5-hour', 'Weekly', 'Fable’s week'])
        self.assertEqual(got['label'], 'Switch at this % of Fable’s own week')
        self.assertIn('Fable’s week is set only here', got['desc'])
        self.assertEqual(got['sent'], [{'model_at': 92}])                             # saved as before
        self.assertEqual(got['value'], '92')


if __name__ == '__main__':
    unittest.main()
