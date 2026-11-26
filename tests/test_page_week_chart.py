import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The week chart's small details: gridline steps, a limit between the event and hour ranges, a command's mark. In
# headless Chrome (a throwaway profile each run, no window) on the demo's made-up accounts.
import copy
import os
import sys
import time
import unittest
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)

from switchboard import demo, insights, server
from test_app import CHROME
from test_page_links_and_logo import FramedPage
from test_page_week_details import StillClock
from test_insights_week import EVENT_KEYS

CHART = r"""
      const f = [...document.querySelectorAll('iframe')];
      done(f.map((x) => { const d = x.contentDocument, c = d.getElementById('wk-chart');
        return {by: c && c.dataset.by,
                scale: [...c.querySelectorAll('svg text.halo:not(.peak)')].map((t) => t.textContent).filter((t) => t !== 'avg'),
                lines: c.querySelectorAll('line.gl').length,
                dots: c.querySelectorAll('circle.ev-lim').length,
                legend: [...d.querySelectorAll('#wk-legend ul.ev li')].map((li) => li.textContent),
                sum: d.getElementById('wk-sum').textContent}; }));"""


def quiet(page):
    """The demo's week with no use, no events and no highlights: each test puts in what it needs."""
    for h in page['series']['hours']:
        h['weight'] = 0.0
        h['who'] = dict.fromkeys(h['who'], 0.0)
    for x in page['series']['days']:
        x['weight'] = 0.0
    page.update(events=[], insights=[])
    return page


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class TheScaleOnAHugeSpike(StillClock):
    """The lines and their labels step with the height: at 55x an average hour, 27 labels would sit 6 px
    apart. They step so that six at most are drawn."""

    def test_steps_its_lines_so_they_never_crowd(self):
        page = quiet(copy.deepcopy(server.usage_view()))
        hours = page['series']['hours']
        for h in hours[40:140]:
            h.update(weight=1.0, who={'chat': 1.0, 'helpers': 0.0, 'workflows': 0.0})
        hours[150].update(weight=120.0, who={'chat': 120.0, 'helpers': 0.0, 'workflows': 0.0})
        page['series']['days'][-2]['weight'] = 220.0
        page.update(insights.week_numbers(page))
        self.assertGreaterEqual(page['peak']['hour']['times'], 50)
        (wide,) = self.shown(page, CHART, (1440,))
        self.assertEqual(wide['by'], 'hour')
        steps = [int(t.rstrip('×')) for t in wide['scale']]
        self.assertTrue(0 < len(steps) <= 6, wide['scale'])
        self.assertEqual(wide['lines'], len(steps))
        self.assertEqual(steps, [steps[0] * (i + 1) for i in range(len(steps))], wide['scale'])   # evenly stepped

    def test_a_usual_week_keeps_its_twos(self):
        # The demo's week moves with the clock, and in the last twelve minutes of an hour its spike reads near 10x an
        # average hour instead of 8x. So this draws the week as the demo makes it at a quarter past the hour.
        page = copy.deepcopy(server.usage_view())
        page.update(demo._week(time.time() // 3600 * 3600 + 900))
        page['insights'] = insights.find(page)
        (wide,) = self.shown(page, CHART, (1440,))
        self.assertEqual(wide['scale'], ['2×', '4×', '6×', '8×'])


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class TheLegendCountsWhatTheChartDraws(StillClock):
    """The events run from midnight 7 days ago, the hour chart from 167 hours before this one; a limit in
    between is counted in the legend and the screen-reader line and is drawn."""

    def test_a_limit_before_the_first_hour(self):
        page = copy.deepcopy(server.usage_view())
        first = insights._at(page['series']['hours'][0]['at']).timestamp()
        day0 = time.mktime(datetime.strptime(page['series']['days'][0]['day'], '%Y-%m-%d').timetuple())
        self.assertGreaterEqual(first - day0, 3600)
        e = dict.fromkeys(EVENT_KEYS)
        e.update(at=insights.iso((first + day0) / 2), kind='limit', account='maya@northwind.example', window='weekly',
                 pct=95)
        page['events'] = [e]
        wide, phone = self.shown(page, CHART, (1440, 390))
        for got in (wide, phone):                                        # a phone draws the same hours
            self.assertEqual((got['by'], got['dots']), ('hour', 0))
            self.assertNotIn('Limit reached', got['legend'])
            self.assertIn('0 limits reached', got['sum'])


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class CommandChips(FramedPage):
    """A command is drawn with a terminal mark, at 12 px, not a "$"."""

    def test_start_with_the_designs_terminal_mark(self):
        got = self.in_frames(r"""
      const d = document.querySelector('iframe').contentDocument, w = d.defaultView, out = [];
      for (const view of ['#settings', '#activity', '#artifacts']) {
        w.location.hash = view; await wait(900);
        for (const t of [...d.querySelectorAll('.tok')].filter((x) => x.offsetParent)) {     // the ones in view
          const mark = t.querySelector('svg.i path[d="m4 17 6-6-6-6"]');
          out.push({view, text: t.textContent, mark: !!mark, size: mark ? mark.closest('svg').getBoundingClientRect().width : 0,
                    dollar: !!t.querySelector('.p')});
        }
      }
      done(out);""", widths=(1440,))
        self.assertTrue(got)
        self.assertEqual({g['view'] for g in got}, {'#settings', '#activity', '#artifacts'})
        for g in got:
            self.assertTrue(g['mark'], g)
            self.assertEqual(g['size'], 12, g)
            self.assertFalse(g['dollar'] or g['text'].startswith('$'), g)


if __name__ == '__main__':
    unittest.main()
