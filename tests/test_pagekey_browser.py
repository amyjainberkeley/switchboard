import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The page's own script and the page key (switchboard/pagekey.py), in headless Chrome, outside the demo: a good ticket
# is traded for the key, which the page keeps in its own storage and sends in a header; with no key, a bad ticket or a
# key that was made again, the page shows its two sentences once and never reloads in a loop. Run with:
#   SWITCHBOARD_HOME="$(mktemp -d)/data" python3 -m unittest discover -s tests -p 'test_pagekey_browser.py'
# The page's data is the demo's made-up accounts (nothing real is read and nothing is written), while the page key and
# the page itself act as outside the demo: the server's check sees "not the demo", and the state says demo: false.
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import core, pagekey, server  # noqa: E402
from test_app import CHROME, ChromePage  # noqa: E402  (its headless-Chrome helper; it has no tests of its own here)

LOCKED = 'To open this page, type switchboard page in a terminal, or use Open Switchboard in your menu bar.'
WHY = 'It can show your chats, so it opens only for you.'
# What the page shows, read the same way in every test (the page's own markers: #locked, the account rows, the key).
LOOK = """
  const look = () => ({locked: !!$('#locked'), text: ($('#locked') || {}).textContent || null,
                       why: ($('.locked-why') || {}).textContent || null, rows: all('.arow').length,
                       search: location.search, stored: localStorage.getItem('switchboard-key')});
  sessionStorage.setItem('loads', String(+(sessionStorage.getItem('loads') || 0) + 1));
"""


class NotTheDemo(object):
    """switchboard.core as the page key sees it: not the demo, with a made-up secret of its own (the demo's is the
    fixed word 'demo'). Everything else is the real module, so the demo's made-up data stays behind the page and
    nothing is written."""
    DEMO = False

    def key(self):
        return 'made-up-secret-for-this-test'

    def __getattr__(self, name):
        return getattr(core, name)


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class ThePageAndItsKey(ChromePage):
    def setUp(self):
        ChromePage.setUp(self)
        pagekey._SPENT.clear()
        self.addCleanup(pagekey._SPENT.clear)
        real_view = server.view
        for target, name, value in ((pagekey, 'core', NotTheDemo()),
                                    (server, 'view', lambda: dict(real_view(), demo=False))):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)

    def page(self, body, path='', budget=20000):
        return self.run_page(LOOK + body, path=path, budget=budget)

    def test_a_good_ticket_opens_the_page_keeps_the_key_and_a_second_open_needs_no_ticket(self):
        got = self.page("""
      if (!sessionStorage.getItem('first')) {
        await wait(1500);
        sessionStorage.setItem('first', JSON.stringify(look()));
        location.href = '/index.html';                  // the same browser, no ticket this time
        return;
      }
      await wait(1500);
      done({first: JSON.parse(sessionStorage.getItem('first')), second: look(), loads: sessionStorage.getItem('loads')});
""")
        key = pagekey.key()
        first, second = got['first'], got['second']
        self.assertEqual((first['locked'], first['stored'], first['search']), (False, key, ''))   # no ?t= left
        self.assertGreater(first['rows'], 0)                                                      # its first screen
        self.assertEqual((second['locked'], second['stored']), (False, key))
        self.assertGreater(second['rows'], 0)
        self.assertEqual(got['loads'], '2')

    def test_no_ticket_in_a_fresh_browser_shows_the_two_sentences_and_nothing_of_yours(self):
        got = self.page("""
      await wait(2500);
      done(look());
""", path='index.html')
        self.assertEqual((got['locked'], got['text'], got['why'], got['rows'], got['stored']),
                         (True, LOCKED, WHY, 0, None))

    def test_a_bad_ticket_shows_the_two_sentences_and_stores_nothing(self):
        got = self.page("""
      await wait(2500);
      done(look());
""", path='index.html?t=1700000000.AAAAAAAAAAAAAAAA.' + '0' * 64)
        self.assertEqual((got['locked'], got['text'], got['rows'], got['stored'], got['search']),
                         (True, LOCKED, 0, None, ''))

    def test_a_key_made_again_locks_the_page_once_and_never_reloads_it(self):
        # The browser holds a key that no longer works (switchboard page --new-key, or a tab from before an update):
        # the state is answered (it is open), the first locked route it asks for answers 401, the stored key is
        # cleared and the page shows its two sentences, once. The number of loads stays at two (the one that put the
        # old key in place, and this one).
        got = self.page("""
      if (!sessionStorage.getItem('set')) {
        sessionStorage.setItem('set', '1');
        localStorage.setItem('switchboard-key', 'a key that was made again');
        location.href = '/index.html';
        return;
      }
      await wait(6000);
      done(Object.assign(look(), {loads: sessionStorage.getItem('loads')}));
""", path='index.html', budget=30000)
        self.assertEqual((got['locked'], got['text'], got['stored'], got['loads']), (True, LOCKED, None, '2'))

    def test_the_trade_is_the_only_post_it_makes_before_its_key_and_it_sends_the_key_after(self):
        got = self.page("""
      await wait(1500);
      const heads = [];
      const real = window.fetch;
      window.fetch = function (u, o) { heads.push([String(u), ((o || {}).headers || {})['X-Switchboard-Key'] || null]); return real.apply(this, arguments); };
      document.dispatchEvent(new Event('visibilitychange')); await wait(7000);      // the state is asked every 5 s
      done({heads: heads.filter((h) => String(h[0]).startsWith('/api/')).slice(0, 5), stored: look().stored});
""")
        self.assertTrue(got['heads'])
        for url, sent in got['heads']:                                  # every request to /api/ carries the key,
            self.assertEqual(sent, got['stored'], url)                  # the state too: the page's own is whole
        self.assertEqual(got['stored'], pagekey.key())

    def test_opened_at_the_name_localhost_it_keeps_no_key_and_goes_to_127_0_0_1(self):
        # localhost may lead to ::1, where another program could listen: the key belongs to 127.0.0.1 alone. The page
        # opened under the name trades no ticket (it is still good afterwards), stores nothing, and moves to 127.0.0.1.
        t = pagekey.ticket()
        self.url = self.url.replace('127.0.0.1', 'localhost')
        got = self.page("""
      await wait(2500);
      done(Object.assign(look(), {host: location.hostname}));
""", path='index.html?t=' + t)
        self.assertEqual((got['host'], got['stored'], got['search'], got['locked']), ('127.0.0.1', None, '', True))
        self.assertTrue(pagekey.redeem(t))                              # never traded

    def test_a_good_ticket_with_the_add_form_in_the_address_draws_the_add_form(self):
        got = self.page("""
      for (let i = 0; i < 60 && !document.querySelector('[data-k="add-email"]'); i++) await wait(100);
      done(Object.assign(look(), {form: !!document.querySelector('[data-k="add-email"]')}));
""", path='#accounts/add')
        self.assertEqual((got['form'], got['locked'], got['stored']), (True, False, pagekey.key()))


# No test in this tree shows a picture of a page outside the demo: the one helper that stands pictures in
# (test_page_layout.stand_in_previews) answers /api/preview ahead of the server's own checks, on the demo's page. So
# the blob path for pictures (fillPictures in web/index.html) is not shown in a browser here.

if __name__ == '__main__':
    unittest.main()
