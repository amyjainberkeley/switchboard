import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The page's account rows: the pace of a week, a spend limit, and two accounts with one email.
#
# - When, at the pace of its use so far, an account's weekly limit would be full before it refills, its weekly line
#   says when, in the refill's place ("Full by Sat 4 PM"; "At this pace" and the refill on hover). When it lasts,
#   nothing: no words, no mark. The page draws the server's answer (server.render: each account's `pace`, from
#   switchboard/pace.py, the rule the terminal and the menu bar say too).
# - A spend limit, where a reading carries one with numbers, is one of the row's two lines, under the weekly one:
#   "Spend 41%", its bar with no switch line (no mark, never red), "$412 of $1,000" in the refill's place. It stands in
#   for the 5-hour line when more of it is used; the weekly line always stays. With no spend limit: nothing.
#
# Headless Chrome (a throwaway profile, no window) on the demo's made-up accounts.
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import demo
import test_app  # its headless-Chrome page

WORK, MAIL, FERN, HARBOR, IVY = ('maya@northwind.example', 'maya.reyes@mail.example', 'maya@fernhill.example',
                                 'm.reyes@harbor.example', 'maya@ivy.example')


def accounts():
    demo._ready()
    return demo.STATE['accounts']


def fresh():
    """Every reading fresh, no model week: WORK is 70% used four days into its week (ahead of an even week, full in
    under two days); FERN 23% two days in, IVY 65% five and a half days in (both last); HARBOR 5% (too little)."""
    st = accounts()
    now = time.time()
    for a in st.values():
        a['usage']['scoped'] = []
        a['usage_at'] = now - 40
    return st


ROWS = r"""
      for (let i = 0; i < 200 && all('#acc .arow').length < 5; i++) await wait(50);
      await wait(150);
      const row = (e) => all('#acc .arow').find((b) => b.querySelector('.em').textContent === e);
      const of = (e) => { const r = row(e), wk = r.querySelector('.c-week');
        const tail = wk.querySelector('.rf');
        return {tail: tail ? tail.textContent : null, tip: tail ? tail.title : null, cls: tail ? tail.className : null,
          week: wk.title, text: r.textContent, cells: r.querySelectorAll('[role="cell"]').length,
          lines: [...r.querySelectorAll('.c[data-kind]')].map((c) => c.dataset.kind)};
      };
"""


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class ThePace(test_app.ChromePage):
    def test_a_week_full_before_it_refills_says_when(self):
        fresh()
        got = self.run_page(ROWS + "done({work: of('%s'), fern: of('%s'), ivy: of('%s'), harbor: of('%s')});"
                            % (WORK, FERN, IVY, HARBOR))
        work = got['work']
        self.assertRegex(work['tail'], r'^Full by ((Mon|Tue|Wed|Thu|Fri|Sat|Sun) )?\d{1,2}(:\d\d)? (AM|PM)$')
        self.assertRegex(work['tip'], r'^At this pace, full by .+\. Refills .+, in \d+ days?$')
        self.assertIn('pace', work['cls'])
        self.assertEqual(work['lines'], ['five', 'week'])                          # no new line in the row
        for key in ('fern', 'ivy', 'harbor'):                                      # it lasts, or too little to say
            with self.subTest(account=key):
                self.assertRegex(got[key]['tail'], r'^Refills ')
                self.assertNotIn('Full by', got[key]['text'])
                self.assertNotIn('pace', got[key]['week'].lower())

    def test_a_reading_too_old_to_show_says_nothing(self):
        st = fresh()
        st[WORK]['usage_at'] = time.time() - 2 * 3600          # over an hour old: a dash, and nothing about its pace
        got = self.run_page(ROWS + "done(of('%s'));" % WORK)
        self.assertIsNone(got['tail'])
        self.assertNotIn('Full by', got['text'])
        self.assertNotIn('pace', got['week'].lower())

    def test_the_model_note_keeps_its_place_and_the_pace_goes_on_hover(self):
        st = fresh()
        st[WORK]['usage']['scoped'] = [{'name': 'Fable', 'pct': 97, 'resets': st[WORK]['usage']['weekly']['resets']}]
        got = self.run_page(ROWS + "done(of('%s'));" % WORK)
        self.assertNotIn('Full by', got['text'])
        self.assertIn('Fable 97%', got['text'])
        self.assertRegex(got['week'], r'(?m)^At this pace, full by ')


def cost(pct=41.2, used=412.0, limit=1000.0):
    return {'pct': pct, 'used': used, 'limit': limit, 'currency': 'USD',
            'resets': demo._iso(time.time() + 18 * 86400)}


SPEND = r"""
      const box = (el) => { const r = el.getBoundingClientRect(); return {left: Math.round(r.left), top: Math.round(r.top), bottom: Math.round(r.bottom)}; };
      const sp = (e) => { const r = row(e), c = r.querySelector('.c-spend');
        if (!c) return null;
        return {text: c.textContent, ticks: c.querySelectorAll('.bar > b').length, red: c.classList.contains('over') || !!c.querySelector('.bar.over'),
          label: c.getAttribute('aria-label'), title: c.title, at: box(c), week: box(r.querySelector('.c-week')), role: c.getAttribute('role')};
      };
"""


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class TheSpendLimit(test_app.ChromePage):
    def test_a_spend_limit_is_a_line_under_the_weekly_one(self):
        st = fresh()
        st[MAIL]['usage']['spend'] = cost()                      # no 5-hour limit: spend is its second line
        st[MAIL].update(needs_login=False, status='ok')
        got = self.run_page(ROWS + SPEND + "done({mail: of('%s'), sp: sp('%s'), fern: of('%s'), fsp: sp('%s')});"
                            % (MAIL, MAIL, FERN, FERN))
        self.assertEqual(got['mail']['lines'], ['spend', 'week'])               # in the 5-hour cell's place, for a reader
        self.assertEqual(got['mail']['cells'], 4)
        sp = got['sp']
        self.assertEqual(sp['text'], 'Spend41%$412 of $1,000')
        self.assertEqual((sp['ticks'], sp['red']), (0, False))                  # no switch line: no mark, never red
        self.assertGreater(sp['at']['top'], sp['week']['top'])                  # under the weekly line
        self.assertEqual(sp['label'], 'Spend limit: 41% used, $412 of $1,000')
        self.assertRegex(sp['title'], r'^41% used\. Refills .+, in 18 days$')
        self.assertIsNone(got['fsp'])                                           # no spend limit: nothing
        self.assertNotIn('$', got['fern']['text'])

    def test_the_5_hour_line_stays_when_more_of_it_is_used(self):
        st = fresh()
        st[WORK]['usage']['spend'] = cost()                      # 5-hour 62% beats spend 41%
        got = self.run_page(ROWS + SPEND + "done({work: of('%s'), sp: sp('%s')});" % (WORK, WORK))
        self.assertEqual(got['work']['lines'], ['five', 'week'])
        self.assertIsNone(got['sp'])
        st[WORK]['usage']['spend'] = cost(pct=80, used=800)      # spend 80% beats it
        got = self.run_page(ROWS + SPEND + "done({work: of('%s'), sp: sp('%s')});" % (WORK, WORK))
        self.assertEqual(got['work']['lines'], ['spend', 'week'])
        self.assertEqual(got['sp']['text'], 'Spend80%$800 of $1,000')

    def test_a_full_spend_limit_is_not_red(self):
        st = fresh()
        st[MAIL]['usage']['spend'] = cost(pct=100, used=1000)
        got = self.run_page(ROWS + SPEND + "done(sp('%s'));" % MAIL)
        self.assertEqual((got['ticks'], got['red']), (0, False))


if __name__ == '__main__':
    unittest.main()
