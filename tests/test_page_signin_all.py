import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Sign in to all on the page (the queue is the server's: signins.py, tests/test_signins_queue.py):
#
# - the note's one button sends one start; the server's answer is drawn at once, so the note says, in place, which
#   account is being signed in (its whole email), "approve it in the tab that opened", how many are done of how many,
#   and Stop (the button is gone: a second press can't start a second queue from here);
# - the sign-in it is on is not said twice (no "Waiting for sign-in" line for it under the accounts);
# - Stop sends stop; when it ends, its one line and Hide;
# - every tab draws the same (the state is the server's);
# - ⌘K has Sign in to all while the note above the accounts offers it, and Stop signing in while it runs;
# - at 680 and 1280 px, light and dark, the line wraps no email and runs past nothing.
#
# Headless Chrome (a throwaway profile, no window) on the demo's made-up accounts.
import os
import sys
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import demo, server, signins
import test_app  # its headless-Chrome page (it has no tests of its own here)
import test_page_links_and_logo as framed  # its page in frames of a given width

D, H = 86400, 3600
WORK, MAIL, FERN, HARBOR, IVY = ('maya@northwind.example', 'maya.reyes@mail.example', 'maya@fernhill.example',
                                 'm.reyes@harbor.example', 'maya@ivy.example')
RUNNING = {'state': 'running', 'current': FERN, 'waiting': False, 'done': 1, 'of': 3, 'emails': [MAIL, FERN, WORK],
           'kept': [MAIL], 'skipped': [], 'due': [FERN, WORK]}
NOTE = """
      const note = () => { const n = $('#notes [data-k="signin-note"]'); return n ? {text: n.querySelector('.tx').textContent,
        buttons: all('#notes [data-k="signin-note"] button').map((b) => [b.dataset.do, b.textContent])} : null; };
"""


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class TheNote(test_app.ChromePage):
    def setUp(self):
        super().setUp()
        self.now = time.time()
        clock = mock.patch.object(demo, 'time', mock.Mock(time=lambda: self.now, sleep=time.sleep))
        clock.start()
        self.addCleanup(clock.stop)
        demo.view()

    def test_the_button_sends_one_start_and_the_note_says_where_it_is_in_place(self):
        got = self.run_page(NOTE + """
      await wait(400);
      const before = note();
      $('#notes [data-do="signinAll"]').click();
      await wait(600);
      done({before, after: note(), sent: sent.filter((x) => x[0] === '/api/signins').map((x) => x[1])});
""")
        self.assertEqual(got['before']['buttons'], [['signinAll', 'Sign in to all']])
        self.assertEqual(got['sent'], [{'do': 'start'}])
        # the demo signs nothing in: it shows the first account it takes (the ended one) being signed in
        taken = signins.pick(server.signins_accounts(demo.STATE), self.now)
        self.assertEqual(taken[:2], [MAIL, FERN])                          # the ended one, then the soonest
        self.assertEqual(got['after']['text'], 'Signing in to %s: approve it in the tab that opened. 0 of %d done.'
                         % (MAIL, len(taken)))
        self.assertEqual(got['after']['buttons'], [['signinStop', 'Stop']])

    def test_while_it_runs_the_note_names_the_account_and_how_many_are_done(self):
        demo.FACTS['signins'] = dict(RUNNING)
        demo.STATE['connecting'] = [{'key': FERN, 'email': FERN, 'name': FERN, 'profile_name': 'Fern', 'started': self.now,
                                     'default_browser': False, 'keeping': False, 'ended': False, 'line': None}]
        got = self.run_page(NOTE + """
      await wait(400);
      const first = note();
      $('#notes [data-do="signinStop"]').click();
      await wait(400);
      done({first, below: $('#below') ? $('#below').textContent : '', row: ($('#acc .arow[data-email="%s"] [data-do="relogin"]') || {}).textContent,
            sent: sent.filter((x) => x[0] === '/api/signins').map((x) => x[1])});
""" % FERN)
        self.assertEqual(got['first']['text'], 'Signing in to %s: approve it in the tab that opened. 1 of 3 done.' % FERN)
        self.assertEqual(got['first']['buttons'], [['signinStop', 'Stop']])
        self.assertNotIn('Waiting for sign-in', got['below'])          # said once, in the note
        self.assertEqual(got['sent'], [{'do': 'stop'}])

    def test_while_its_new_sign_in_is_kept_the_note_says_so(self):
        demo.FACTS['signins'] = dict(RUNNING, waiting=True)
        got = self.run_page(NOTE + "await wait(400); done(note());")
        self.assertEqual(got['text'], 'Keeping the new sign-in of %s. 1 of 3 done.' % FERN)

    def test_when_it_ends_its_one_line_and_hide(self):
        line = 'Signed in again to 2 of 3. Not signed in: %s (it was not approved in time).' % WORK
        demo.FACTS['signins'] = {'state': 'ended', 'line': line, 'kept': [MAIL, FERN], 'skipped': [], 'due': []}
        got = self.run_page(NOTE + """
      await wait(400);
      const first = note();
      $('#notes [data-do="signinHide"]').click();
      await wait(600);
      done({first, sent: sent.filter((x) => x[0] === '/api/signins').map((x) => x[1])});
""")
        self.assertEqual(got['first']['text'], line)
        self.assertEqual(got['first']['buttons'], [['signinHide', 'Hide']])
        self.assertEqual(got['sent'], [{'do': 'hide'}])

    def test_every_tab_draws_the_same(self):
        demo.FACTS['signins'] = dict(RUNNING)
        one = self.run_page(NOTE + "await wait(400); done(note());")
        two = self.run_page(NOTE + "await wait(400); done(note());")
        self.assertEqual(one, two)

    def test_the_palette_offers_it_and_stop_while_it_runs(self):
        find = NOTE + """
      await wait(400);
      document.dispatchEvent(new KeyboardEvent('keydown', {key: 'k', metaKey: true, bubbles: true})); await wait(200);
      const q = $('#pal-q'); q.value = 'sign in'; q.dispatchEvent(new Event('input', {bubbles: true})); await wait(100);
      const labels = all('.pal-it .lb').map((x) => x.textContent);
      q.value = '%s'; q.dispatchEvent(new Event('input', {bubbles: true})); await wait(100);
      q.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
      await wait(600);
      done({labels, sent: sent.filter((x) => x[0] === '/api/signins').map((x) => x[1])});
"""
        got = self.run_page(find % 'sign in to all')
        self.assertIn('Sign in to all', got['labels'])
        self.assertEqual(got['sent'], [{'do': 'start'}])
        demo.FACTS['signins'] = dict(RUNNING)
        got = self.run_page(find % 'stop signing in')
        self.assertNotIn('Sign in to all', got['labels'])
        self.assertEqual(got['sent'], [{'do': 'stop'}])


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class TheLineFits(framed.FramedPage):
    LOOK = r"""
    const out = {};
    for (const theme of ['light', 'dark']) {
      for (const f of frames) {
        const d = doc(f), w = f.contentWindow.innerWidth;
        d.documentElement.dataset.theme = theme;
        const n = d.querySelector('#notes [data-k="signin-note"]'), r = n.getBoundingClientRect(), tx = n.querySelector('.tx');
        const stop = n.querySelector('button'), sr = stop.getBoundingClientRect();
        out[theme + w] = {wide: d.documentElement.scrollWidth > w, right: Math.round(r.right), page: w,
          stopRight: Math.round(sr.right), stopShown: sr.width > 0 && sr.height > 0, text: tx.textContent,
          fg: getComputedStyle(tx).color, bg: getComputedStyle(d.body).backgroundColor};
      }
    }
    done(out);
"""

    def test_the_running_line_fits_at_680_and_1280_light_and_dark(self):
        demo.view()
        demo.FACTS['signins'] = dict(RUNNING)
        got = self.in_frames(self.LOOK, widths=(680, 1280))
        for key, v in got.items():
            with self.subTest(frame=key):
                self.assertFalse(v['wide'])                                  # nothing runs past the page
                self.assertLessEqual(v['right'], v['page'])
                self.assertTrue(v['stopShown'])
                self.assertLessEqual(v['stopRight'], v['page'])
                self.assertIn(FERN, v['text'])
        self.assertNotEqual(got['light680']['bg'], got['dark680']['bg'])     # each theme draws its own


if __name__ == '__main__':
    unittest.main()
