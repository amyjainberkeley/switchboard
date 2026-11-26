import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The week's three parts as the page draws them: one colour a part wherever it is drawn (the stacked hours, the legend,
# the hover's key, the this-week and last-week bars, a project's bar), you in the page's one accent, and any two parts
# far apart. In headless Chrome (a throwaway profile each run, no window) on the demo's made-up week, in both themes.
# Run with (always under the Chrome lock: /usr/bin/lockf -k <lock> ...):
#   SWITCHBOARD_HOME="$(mktemp -d)/data" taskpolicy -b python3 -m unittest discover -s tests -p 'test_page_week_parts.py'
import itertools
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)

from test_app import CHROME
from test_page_week_details import StillClock
from test_week_parts_tell_apart import FAR, FAR_RED_GREEN, SHOWS, apart, contrast, hexed

PARTS = ('wf', 'help', 'you')

# One theme's colours as drawn. The hover is opened on the busiest hour first, so its key is on the page.
DRAWN = r"""
      const drawn = async (f, theme) => {
        const d = f.contentDocument, w = f.contentWindow;
        d.documentElement.dataset.theme = theme;
        const chart = d.getElementById('wk-chart'), svg = chart.querySelector('svg');
        const r = svg.getBoundingClientRect(), k = r.width / svg.viewBox.baseVal.width, lead = svg.querySelector('.lead');
        const x = lead ? +lead.getAttribute('x1') : svg.viewBox.baseVal.width / 2;
        chart.dispatchEvent(new w.PointerEvent('pointermove', {clientX: r.left + x * k, clientY: r.top + 120 * k, bubbles: true}));
        await wait(120);
        const css = (el, what) => (el ? w.getComputedStyle(el)[what] : null);
        const tip = d.getElementById('tip'), keys = tip && !tip.hidden ? [...tip.querySelectorAll('.t-rows i')] : [];
        const out = {page: css(d.body, 'backgroundColor'), accent: css(svg.querySelector('.now'), 'fill'), parts: {}};
        ['wf', 'help', 'you'].forEach((c, j) => {
          out.parts[c] = {hours: css(svg.querySelector('#wk-bars .s-' + c), 'fill'), legend: css(d.querySelector('#wk-legend .sw-k.' + c), 'backgroundColor'),
            hover: css(keys[j], 'backgroundColor'), weeks: css(d.querySelector('.vs .pbar i.' + c), 'backgroundColor'),
            project: css(d.querySelector('.mult .pbar i.' + c), 'backgroundColor')};
        });
        out.plain = css(d.querySelector('.mult .pbar i.one'), 'backgroundColor');
        chart.dispatchEvent(new w.PointerEvent('pointerleave', {bubbles: true}));
        delete d.documentElement.dataset.theme;
        return out;
      };
      const f = document.querySelector('iframe');
      done({light: await drawn(f, 'light'), dark: await drawn(f, 'dark')});"""


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class TheThreePartsAsDrawn(StillClock):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.got = None

    def drawn(self):
        if type(self).got is None:                      # one look at the page serves every test here
            type(self).got = self.in_frames(DRAWN, widths=(1440,))
        return type(self).got

    def test_one_colour_a_part_wherever_it_is_drawn(self):
        got = self.drawn()
        for theme in ('light', 'dark'):
            for c in PARTS:
                places = got[theme]['parts'][c]
                for place in ('hours', 'legend', 'hover', 'weeks', 'project'):
                    self.assertIsNotNone(places[place], (theme, c, place))            # the demo's week draws every one
                self.assertEqual(len({hexed(v) for v in places.values()}), 1, (theme, c, places))
            self.assertEqual(len({hexed(got[theme]['parts'][c]['hours']) for c in PARTS}), 3, theme)
        self.assertNotEqual(got['light']['parts']['wf']['hours'], got['dark']['parts']['wf']['hours'])

    def test_you_are_drawn_in_the_pages_one_accent(self):
        got = self.drawn()
        for theme in ('light', 'dark'):
            self.assertEqual(hexed(got[theme]['parts']['you']['hours']), hexed(got[theme]['accent']), theme)

    def test_any_two_parts_are_far_apart_and_each_shows(self):
        got = self.drawn()
        for theme in ('light', 'dark'):
            page = hexed(got[theme]['page'])
            fills = {c: hexed(got[theme]['parts'][c]['hours']) for c in PARTS}
            for c, colour in fills.items():
                self.assertGreaterEqual(contrast(colour, page), SHOWS, (theme, c, colour))
            for a, b in itertools.combinations(PARTS, 2):
                self.assertGreaterEqual(apart(fills[a], fills[b]), FAR, (theme, a, b))
                for sight in ('protan', 'deutan'):
                    self.assertGreaterEqual(apart(fills[a], fills[b], sight), FAR_RED_GREEN, (theme, a, b, sight))
            self.assertNotIn(hexed(got[theme]['plain']), fills.values(), theme)       # a bar that is not split is none of them


if __name__ == '__main__':
    unittest.main()
