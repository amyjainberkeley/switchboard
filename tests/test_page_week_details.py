import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Details of the week page: the busiest day's words, settings tooltips, the limit dot, a row that needs action, the
# demo's short names. In headless Chrome (a throwaway profile each run, no window) on the demo's made-up accounts.
import copy
import os
import sys
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)

from switchboard import core, demo, insights, server
from test_app import CHROME
from test_page_links_and_logo import FramedPage

HARBOR = 'm.reyes@harbor.example'


class StillClock(FramedPage):
    def setUp(self):
        super().setUp()
        now = time.time()
        clock = mock.patch.object(demo, 'time', mock.Mock(time=lambda: now, sleep=time.sleep))
        clock.start()
        self.addCleanup(clock.stop)

    def shown(self, payload, body, widths):
        with mock.patch.object(server, 'usage_view', lambda fresh=False: payload):
            return self.in_frames(body, widths=widths)


PEAK = r"""
      const f = [...document.querySelectorAll('iframe')];
      done(f.map((x) => { const d = x.contentDocument, c = d.getElementById('wk-chart');
        return {by: c && c.dataset.by, peak: (d.getElementById('wk-peak') || {}).textContent || null}; }));"""


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class TheBusiestIsNamed(StillClock):
    def test_a_busiest_hour_is_named_at_one_and_a_half_times_on_a_phone_too(self):
        # eight hours at 100 and one at 200: an average hour with use is 111.1, so the busiest is 1.8x one (2x names
        # too few), and a phone names it in the same words as a wide page
        page = copy.deepcopy(server.usage_view())
        for h in page['series']['hours']:
            h['weight'] = 0.0
            h['who'] = dict.fromkeys(h['who'], 0.0)
        for i in list(range(20, 28)) + [30]:
            w = 200.0 if i == 30 else 100.0
            page['series']['hours'][i].update(weight=w, who={'chat': w, 'helpers': 0.0, 'workflows': 0.0})
        page.update(insights.week_numbers(page), insights=[])
        phone, wide = self.shown(page, PEAK, (390, 1440))
        for got in (phone, wide):
            self.assertEqual(got['by'], 'hour')
            self.assertIsNotNone(got['peak'])
            self.assertTrue(got['peak'].endswith(' · 1.8× an average hour'), got['peak'])
        self.assertEqual(phone['peak'], wide['peak'])

    def test_no_name_under_the_engines_floor(self):
        # one past hour of use and the hour so far is no week to name a busiest hour in
        page = copy.deepcopy(server.usage_view())
        for h in page['series']['hours']:
            h['weight'] = 0.0
            h['who'] = dict.fromkeys(h['who'], 0.0)
        for i, w in ((100, 50.0), (167, 120.0)):
            page['series']['hours'][i].update(weight=w, who={'chat': w, 'helpers': 0.0, 'workflows': 0.0})
        for x in page['series']['days']:
            x['weight'] = 0.0
        page['series']['days'][-1]['weight'] = 170.0
        page.update(insights.week_numbers(page), insights=[])
        (wide,) = self.shown(page, PEAK, (1440,))
        self.assertEqual(wide['by'], 'hour')
        self.assertIsNone(wide['peak'])


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class SettingsTooltips(FramedPage):
    def test_every_key_tooltip_reads_as_a_place(self):
        # a tooltip never reads like "awake, in Set "awake": false in …" or
        # "15.18.3, in The version its switch installs …"
        got = self.in_frames(r"""
      const d = document.querySelector('iframe').contentDocument, w = d.defaultView;
      w.location.hash = '#settings'; await wait(900);
      done([...d.querySelectorAll('#set-list .r-title[title]')].map((x) => [x.closest('.row').dataset.id, x.title]));""",
                             widths=(1440,))
        tips = dict(got)
        self.assertTrue(tips)
        for row, tip in got:
            for line in tip.split('\n'):
                self.assertNotRegex(line, r', in [A-Z]', (row, line))
        self.assertTrue(tips['awake'].startswith('awake, in ~/.switchboard/settings.json'), tips['awake'])
        self.assertTrue(tips['claudemem'].startswith('claude-mem@'), tips['claudemem'])
        self.assertIn('the version its switch installs', tips['claudemem'])


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class ARefillIsNotALimit(FramedPage):
    def test_only_a_limit_reached_has_the_red_dot(self):
        # "weekly refilled" does not carry the limit's red dot in Activity
        got = self.in_frames(r"""
      const d = document.querySelector('iframe').contentDocument, w = d.defaultView;
      w.location.hash = '#activity'; await wait(900);
      done([...d.querySelectorAll('.hist li')].map((li) => [li.querySelector('.k').textContent, li.textContent,
                                                            w.getComputedStyle(li.querySelector('.w'), '::before').content]));""",
                             widths=(1440,))
        refills = [c for k, t, c in got if k == 'limit' and 'refilled' in t]
        reached = [c for k, t, c in got if k == 'limit' and 'refilled' not in t]
        self.assertTrue(refills and reached, got)
        self.assertEqual(set(refills), {'none'})
        self.assertNotIn('none', reached)


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class ARowThatNeedsAction(StillClock):
    def test_keeps_use_and_next_beside_its_one_button(self):
        # the row keeps "In use, Use, and which account auto-switch picks next"
        real = server.view
        with mock.patch.object(server, 'view', lambda *a, **k: dict(real(*a, **k), next=HARBOR)):   # auto-switch's next
            got = self.in_frames(r"""
      const f = [...document.querySelectorAll('iframe')], out = [];
      for (const x of f) {
        const d = x.contentDocument;
        const row = d.querySelector('.arow[data-email="%(e)s"]'), cell = row.querySelector('.c-state'), r = row.getBoundingClientRect();
        const off = [...cell.children].filter((c) => c.offsetParent).map((c) => c.getBoundingClientRect())
          .filter((b) => b.right > r.right + 1 || b.left < r.left - 1).length;
        out.push({state: [...cell.querySelectorAll('button, .badge')].filter((c) => c.offsetParent).map((c) => c.textContent).filter(Boolean),
                  off, overflow: d.documentElement.scrollWidth - d.documentElement.clientWidth});
      }
      done(out);""" % {'e': HARBOR}, widths=(1440, 1100, 390))
        for g in got:
            self.assertEqual(g['state'], ['Sign in', 'Next', 'Use'], g)
            self.assertEqual((g['off'], g['overflow']), (0, 0), g)


class TheDemosShortNames(unittest.TestCase):
    def test_not_the_rejected_short_names(self):
        # the short names follow their emails; studio, lab and client are not used
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = True
        demo.STATE.clear()
        demo.FACTS.clear()
        self.addCleanup(demo.STATE.clear)
        self.addCleanup(demo.FACTS.clear)
        st = server.view()
        aliases = [a['alias'] for a in st['accounts']]
        self.assertEqual(aliases, ['work', 'personal', 'fernhill', 'harbor', 'ivy'])
        for a in st['accounts']:
            if a['alias'] not in ('work', 'personal'):
                self.assertIn('@' + a['alias'] + '.', a['email'])      # named by its email, as the others are
        names = ' '.join(list(demo.FACTS['profiles']) + list(demo.FACTS['profiles'].values()) +
                         [a['email'] for a in st['accounts']]).lower()
        for word in ('studio', 'client', 'lab'):
            self.assertNotRegex(names, r'\b%s\b' % word)


if __name__ == '__main__':
    unittest.main()
