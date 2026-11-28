import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Takeaways on the week's numbers, in the page: a number one hangs on gets a quiet mark; hover, focus, Enter or a tap
# opens it beside the number (the measured share, its sentence, its press), Escape closes it, a press posts to
# /api/optimize and shows what it did with Undo. It is placed in the page, so a scroll moves it with its number. With no takeaways the week section is what it draws without them.
# In headless Chrome (a throwaway profile each run, no window) on the demo's made-up week.
import os
import sys
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))
sys.path.insert(0, HERE)

from switchboard import demo, server  # noqa: E402
from test_page_links_and_logo import FramedPage  # noqa: E402

TAKE = [{'id': 'rereading', 'on': 'tokens.cache_read',
         'measured': {'value': 71, 'unit': 'percent_of_week', 'basis': 'your last 7 days'},
         'line': 'Every reply reads the whole chat again, so a long chat started fresh from a short summary reads less.',
         'change': None, 'state': 'off', 'can': False, 'why_not': None},
        {'id': 'who', 'on': 'who.workflows',
         'measured': {'value': 81, 'unit': 'percent_of_week', 'basis': 'your last 7 days'},
         'line': '<b>Workflow steps</b> did the largest part of your week.',
         'change': {'key': 'dry', 'label': 'Try it', 'estimate': None},
         'state': 'off', 'can': True, 'why_not': None}]

WEEK = r"""
      const d = document.querySelector('iframe').contentDocument, w = d.defaultView;
      for (let i = 0; i < 100 && !d.querySelector('#wk-legend li'); i++) await wait(100);
      await wait(400);
"""


class Base(FramedPage):
    def setUp(self):
        super().setUp()
        now = time.time()
        clock = mock.patch.object(demo, 'time', mock.Mock(time=lambda: now, sleep=time.sleep))
        clock.start()
        self.addCleanup(clock.stop)

    def take(self, items):
        p = mock.patch.object(server, 'optimize_view', lambda: {'takeaways': [dict(t) for t in items]})
        p.start()
        self.addCleanup(p.stop)


class Marks(Base):
    def test_a_mark_on_each_number_and_the_takeaway_beside_it(self):
        self.take(TAKE)
        got = self.in_frames(WEEK + r"""
      const marks = [...d.querySelectorAll('#week .opt')].map((el) => [el.dataset.opt, el.tabIndex, el.textContent]);
      const legend = d.querySelector('#wk-legend ul li b');
      legend.dispatchEvent(new w.PointerEvent('pointerover', {bubbles: true}));
      await wait(100);
      const pop = d.getElementById('opt-pop');
      const open = {shown: !pop.hidden, text: [...pop.children].map((c) => c.textContent),
                    html: pop.innerHTML.includes('<b>Workflow'), button: pop.querySelector('button').textContent};
      legend.focus();
      legend.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
      await wait(50);
      const closed = pop.hidden;
      legend.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
      await wait(50);
      const reopened = !pop.hidden, onPress = d.activeElement === pop.querySelector('button');
      d.activeElement.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
      await wait(50);
      const back = d.activeElement === legend && pop.hidden;
      legend.focus(); await wait(50);
      d.activeElement.blur(); await wait(500);
      done({marks, open, closed, reopened, onPress, back, leftClosed: pop.hidden,
            role: legend.getAttribute('role'), described: legend.getAttribute('aria-describedby')});""", widths=(1440,))
        self.assertIn(['who', 0, '81%'], got['marks'])
        self.assertTrue(got['open']['shown'])
        self.assertEqual(got['open']['text'][:3], ['81%', 'of your last 7 days', TAKE[1]['line']])
        self.assertFalse(got['open']['html'])                                   # text, never HTML
        self.assertEqual(got['open']['button'], 'Try it')
        self.assertTrue(got['closed'])
        self.assertTrue(got['reopened'])
        self.assertTrue(got['onPress'])                                         # Enter moves focus to the press
        self.assertTrue(got['back'])                                            # Escape gives focus back, closed
        self.assertTrue(got['leftClosed'])                                      # focus leaving both closes it
        self.assertEqual((got['role'], got['described']), ('button', 'opt-pop'))

    def test_a_scroll_moves_it_with_its_number(self):
        # A focus that brings the number into view scrolls the page: what that focus opened stays open. The takeaway is
        # placed in the page, so any scroll moves it with its number, with no scroll event to wait for.
        self.take(TAKE)
        got = self.in_frames(WEEK + r"""
      const legend = d.querySelector('#wk-legend ul li b'), pop = () => d.getElementById('opt-pop');
      const beside = () => { const p = pop().getBoundingClientRect(), r = legend.getBoundingClientRect();
        return Math.abs(p.top - r.bottom - 8) <= 1 || Math.abs(r.top - p.bottom - 8) <= 1; };
      w.scrollTo(0, 0); await wait(200);
      const below = legend.getBoundingClientRect().top > w.innerHeight;     // so the focus has to scroll
      legend.focus(); await wait(400);
      const opened = !!pop() && !pop().hidden, there = opened && beside(), y0 = w.scrollY;
      w.scrollBy(0, -60);
      const moved = w.scrollY !== y0, stayed = !pop().hidden, still = beside();
      done({below, opened, there, moved, stayed, still});""", widths=(1440,))
        self.assertTrue(got['below'])
        self.assertTrue(got['opened'])                      # the focus's own scroll did not close it
        self.assertTrue(got['there'])
        self.assertTrue(got['moved'])
        self.assertTrue(got['stayed'])
        self.assertTrue(got['still'])                       # it moved with its number, at once

    def test_a_press_posts_and_shows_undo(self):
        self.take(TAKE)
        tried = dict(TAKE[1], state='trying')
        with mock.patch.object(server, 'act', lambda path, body: {'takeaways': [tried], 'says': 'Trial run on.'}):
            got = self.in_frames(WEEK + r"""
      const sent = [], real = w.fetch;
      w.fetch = function (u, o) { if (o && o.method === 'POST') sent.push([String(u), JSON.parse(o.body || '{}')]); return real.apply(this, arguments); };
      const legend = d.querySelector('#wk-legend ul li b');
      legend.click(); await wait(100);
      d.querySelector('#opt-pop button').click(); await wait(600);
      const pop = d.getElementById('opt-pop');
      done({sent, button: pop.querySelector('button').textContent, said: pop.querySelector('.t-x').textContent,
            focused: d.activeElement === pop.querySelector('button')});""",
                                 widths=(1440,))
        self.assertEqual(got['sent'], [['/api/optimize', {'id': 'who', 'do': 'try'}]])
        self.assertEqual((got['button'], got['said']), ('Undo', 'Trial run on.'))
        self.assertTrue(got['focused'])                                         # focus is on Undo after the press


class NothingToSay(Base):
    def test_an_empty_list_leaves_the_week_as_it_was(self):
        self.take([])
        got = self.in_frames(WEEK + r"""
      done({opt: d.querySelectorAll('.opt').length, pop: !!d.getElementById('opt-pop'),
            tab: d.querySelectorAll('#wk-legend [tabindex], #wk-highs [tabindex]').length});""", widths=(1440,))
        self.assertEqual(got, {'opt': 0, 'pop': False, 'tab': 0})


if __name__ == '__main__':
    unittest.main()
