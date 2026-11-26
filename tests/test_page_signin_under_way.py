import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The page while a sign-in is under way, and after an update.
#
# - A row whose sign-in is under way says "Signing in…" where it said "Sign in": waiting in the browser (pressing it
#   asks for the same sign-in page again), or approved there and being kept (its hover says so, and the line under the
#   accounts says "Keeping the new sign-in as …"). It never reads as "nothing happened, press again". (On a real Mac
#   the row went on saying "Sign in" after the browser had approved one, and the second press threw that sign-in away:
#   tests/test_signin_pressed_twice.py.) In a row that also has Use the longer button goes on the line under.
# - A row with no sign-in under way, and one whose sign-in came back as another account, still say "Sign in".
# - While a sign-in the browser approved is being kept the page looks at the server every 1.2 seconds, not every 5: the
#   row changes the moment it is kept. While one only waits for the browser it looks as often as ever.
# - An update replaces the page's file. A tab left open on the old page loads itself again: once (never a loop, even
#   if the names went on differing), and not while something is being typed (then as soon as it is not). The tab knows
#   which page it is from the name it was sent with, so a tab that was sent the old page and hears of the new one in
#   its first answer loads itself again before it draws; a page sent without a name takes the first answer's.
#
# Headless Chrome (a throwaway profile, no window) on the demo's made-up accounts.
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import core, demo, server
import test_app  # its headless-Chrome page (it has no tests of its own here)

WORK, MAIL, FERN, HARBOR = ('maya@northwind.example', 'maya.reyes@mail.example', 'maya@fernhill.example',
                            'm.reyes@harbor.example')
WAITING_TIP = 'Finish it in your browser. Press to open the sign-in page again.'
KEEPING_TIP = 'Your browser approved it. Switchboard is keeping the new sign-in.'

ROW = """
      await wait(400);
      const row = (e) => { const b = all('#acc .arow').find((r) => r.querySelector('.em').textContent === e), again = b.querySelector('[data-do="relogin"]');
        return {again: again ? [again.textContent, again.getAttribute('aria-label'), again.title] : null, use: !!b.querySelector('[data-do="use"]'),
                under: !!(again && again.closest('.under'))}; };
"""


def sign_in(email, **more):
    """One sign-in as the server lists it for the page (connect.view): waiting in the browser, unless said otherwise."""
    return dict({'key': email, 'email': email, 'name': email, 'profile_name': 'Work', 'started': time.time(),
                 'default_browser': False, 'keeping': False, 'ended': False, 'line': None}, **more)


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class TheRowOfASignInUnderWay(test_app.ChromePage):
    def setUp(self):
        super().setUp()
        demo.view()

    def test_it_says_signing_in_while_it_waits_and_while_it_is_kept(self):
        demo.FACTS['connecting'] = [sign_in(MAIL), sign_in(FERN, ended=True)]
        got = self.run_page(ROW + """
      const rows = {mail: row('%s'), fern: row('%s'), harbor: row('%s')};
      document.querySelector('[data-do="relogin"][data-email="%s"]').click();
      await wait(600);
      done({rows, sent: sent.filter((x) => x[0] === '/api/connect').map((x) => x[1].email),
            lines: all('.nl').map((x) => x.textContent).filter((t) => t.indexOf('sign-in') >= 0)});
""" % (MAIL, FERN, HARBOR, MAIL))
        self.assertEqual(got['rows']['mail']['again'],
                         ['Signing in…', 'Signing in as %s. Open the sign-in page again' % MAIL, WAITING_TIP])
        self.assertFalse(got['rows']['mail']['use'])
        self.assertEqual(got['rows']['fern']['again'],          # approved in the browser, being kept
                         ['Signing in…', 'Signing in as %s. The new sign-in is being kept' % FERN, KEEPING_TIP])
        self.assertTrue(got['rows']['fern']['use'])                         # its saved login still works: Use stays
        # beside Use the longer button pushed the row's menu button to a second line: it goes under, as on a Next row
        self.assertTrue(got['rows']['fern']['under'])
        self.assertFalse(got['rows']['mail']['under'])                      # alone with the menu button: beside it
        self.assertIsNone(got['rows']['harbor']['again'])                   # no sign-in of its own: as it was
        self.assertEqual(got['sent'], [MAIL])                               # the same route: the server opens the same link
        self.assertTrue([t for t in got['lines'] if t.startswith('Waiting for sign-in as ' + MAIL)], got['lines'])
        self.assertTrue([t for t in got['lines'] if t.startswith('Keeping the new sign-in as ' + FERN)], got['lines'])

    def test_a_row_with_none_under_way_still_says_sign_in(self):
        # one that came back as another account is over: its row offers Sign in again
        demo.FACTS['connecting'] = [sign_in(MAIL, done=True, got=WORK, line='Your browser signed in as another account.')]
        got = self.run_page(ROW + "done({mail: row('%s'), fern: row('%s')});" % (MAIL, FERN))
        self.assertEqual(got['mail']['again'][:2], ['Sign in', 'Sign in again as ' + MAIL])
        self.assertEqual(got['fern']['again'][:2], ['Sign in', 'Sign in again as ' + FERN])
        self.assertFalse(got['fern']['under'])                              # beside Use, where it was

    def looks(self):
        """How many times the page asked the server for its state in six seconds."""
        return self.run_page("""
      await wait(400);
      let looks = 0;
      const asked = window.fetch;
      window.fetch = function (u) { if (String(u).indexOf('/api/state') >= 0) looks++; return asked.apply(this, arguments); };
      await wait(6000);
      done({looks});
""", budget=12000)['looks']

    def test_the_page_looks_every_second_or_so_while_one_is_being_kept(self):
        demo.FACTS['connecting'] = [sign_in(MAIL)]                          # waiting for the browser: as often as ever
        waiting = self.looks()
        demo.FACTS['connecting'] = [sign_in(MAIL, ended=True)]              # approved: being kept
        kept = self.looks()
        self.assertLessEqual(waiting, 2)                                    # every 5 seconds
        self.assertGreaterEqual(kept, 4)                                    # every 1.2 seconds


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class AnOpenTabAfterAnUpdate(test_app.ChromePage):
    """A tab on the page as it was before an update: the file it was made from is no longer the one on disk."""

    def setUp(self):
        super().setUp()
        demo.view()

    def state_names(self, *names):
        """The state's answers name these pages, one after the other; the last one from then on."""
        left = list(names)

        def page_id():
            return left.pop(0) if len(left) > 1 else left[0]
        p = mock.patch.object(server, 'page_id', page_id, create=True)
        p.start()
        self.addCleanup(p.stop)

    def sent_as(self, name):
        """The page is sent with this name for its own file (as server.page_bytes puts it in)."""
        with open(os.path.join(core.WEB, 'index.html'), 'rb') as f:
            page = f.read()
        web = tempfile.mkdtemp(prefix='switchboard-web-')
        self.addCleanup(__import__('shutil').rmtree, web, True)
        with open(os.path.join(web, 'index.html'), 'wb') as f:
            f.write(page.replace(b'<meta charset="utf-8">',
                                 b'<meta charset="utf-8">\n<meta name="switchboard-page" content="' + name + b'">', 1))
        p = mock.patch.object(core, 'WEB', web)     # (where the test's page is read from; the server's own stays)
        p.start()
        self.addCleanup(p.stop)

    def test_a_tab_sent_the_old_page_loads_itself_again_at_once_and_only_once(self):
        # The tab was sent the old page; every answer, the first one too, names the new one. So it loads itself again
        # before it has drawn anything. This test goes on sending the old page, so the names differ after that as
        # well: still one reload, never a loop. (The script here only runs on a page that has drawn.)
        self.sent_as(b'the-old-page')
        self.state_names('the-new-page')
        got = self.run_page("""
      const n = +(sessionStorage.getItem('drawn') || 0) + 1;
      sessionStorage.setItem('drawn', n);
      await wait(12000);                          // it stays, whatever the names say
      done({drawn: +sessionStorage.getItem('drawn'), again: sessionStorage.getItem('switchboard-loaded-for'),
            rows: !!document.querySelector('#acc .arow')});
""", budget=26000)
        self.assertEqual(got, {'drawn': 1, 'again': 'the-new-page', 'rows': True})

    def test_not_while_something_is_typed(self):
        # The update comes while the tab is open: the first answer names the page the tab was sent, the later ones
        # the new one. Something is being typed: the tab waits, and loads itself again as soon as it is not.
        self.sent_as(b'the-old-page')
        self.state_names('the-old-page', 'the-new-page')
        got = self.run_page("""
      const n = +(sessionStorage.getItem('loads') || 0) + 1;
      sessionStorage.setItem('loads', n);
      if (n === 1) {
        const box = document.querySelector('main input');
        if (!box) { done({error: 'the page has no input to type in'}); return; }
        box.focus();
        await wait(8000);                           // two looks at the server went by: still the first page
        sessionStorage.setItem('held', document.activeElement === box ? 'yes' : 'lost focus');
        box.blur();
        await wait(11000);                          // gone before this ends: the tab loads itself again
      } else await wait(12000);                     // the second page stays, whatever the names say
      done({loads: +sessionStorage.getItem('loads'), held: sessionStorage.getItem('held'), drawn: !!document.querySelector('#acc .arow')});
""", budget=38000)
        self.assertEqual(got, {'loads': 2, 'held': 'yes', 'drawn': True})

    def test_a_page_sent_without_its_name_takes_the_first_answers(self):
        self.state_names('the-old-page', 'the-new-page')
        got = self.run_page("""
      const n = +(sessionStorage.getItem('loads') || 0) + 1;
      sessionStorage.setItem('loads', n);
      await wait(n === 1 ? 11000 : 8000);         // the first page is gone before its wait ends; the second stays
      done({loads: +sessionStorage.getItem('loads'), drawn: !!document.querySelector('#acc .arow')});
""", budget=26000)
        self.assertEqual(got, {'loads': 2, 'drawn': True})


if __name__ == '__main__':
    unittest.main()
