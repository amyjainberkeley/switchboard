import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The page says when each account's sign-in ends, in the words of every other surface (term.signin_words):
#
# - a row whose sign-in ends within the week: one quiet line under its plan ("Sign-in ends by Thu 9:00 AM", why on
#   hover) and "Sign in" before its Use (it still works); the same in its row menu and in the command palette;
# - a row whose sign-in ended: its Sign in, and the line "Sign-in ended …";
# - the account Claude Code is on: the same line and its own Sign in, like any other row (the engine puts its new
#   sign-in live at once: test_accounts_signin_again_in_use); no "/login" line anywhere;
# - nothing for a date over 7 days away or not known;
# - one note at the top while any sign-in has ended or ends within the week, from the server's one list
#   (signins.due: the accounts Sign in to all opens): one short line and one button, Sign in to all. One account: its
#   whole email and when. Several: how many have ended and how many end this week, each email with its words on hover.
#   The button's hover names every account a press opens, by its whole email, in the order they open;
# - at 680 and 1280 px the line wraps no email and moves no bar sideways.
#
# The demo shows one sign-in that ends in 3 days and one that ended yesterday. Headless Chrome (a throwaway profile, no
# window) on the demo's made-up accounts.
import copy
import os
import sys
import time
import unittest
from datetime import datetime
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import core, demo, server, signins, term
import test_app  # its headless-Chrome page (it has no tests of its own here)
import test_page_links_and_logo as framed  # its page in frames of a given width

D, H = 86400, 3600
WORK, MAIL, FERN, HARBOR, IVY = ('maya@northwind.example', 'maya.reyes@mail.example', 'maya@fernhill.example',
                                 'm.reyes@harbor.example', 'maya@ivy.example')


def local(day, hour, minute=0, month=11, year=2026):
    """A time in this Mac's own time zone, as the page and the terminal both read it."""
    return datetime(year, month, day, hour, minute).timestamp()


# ---------- the demo ----------

class TheDemo(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = True
        demo.STATE.clear()
        demo.FACTS.clear()
        self.addCleanup(demo.STATE.clear)
        self.addCleanup(demo.FACTS.clear)

    def ends(self, v):
        return dict((a['email'], (a['signin_ends_at'], a['needs_login'])) for a in v['accounts'])

    def test_one_ends_in_3_days_and_one_ended_yesterday(self):
        v = demo.view()
        now = v['now']
        soon = [(e, at) for e, (at, need) in self.ends(v).items()
                if at is not None and now < at <= now + term.SIGNIN_SOON and not need]
        ended = [(e, at) for e, (at, need) in self.ends(v).items() if at is not None and at <= now and need]
        self.assertEqual(soon[0][0], FERN)
        self.assertEqual(len(soon), 1)
        self.assertEqual([e for e, _ in ended], [MAIL])
        self.assertTrue(2 * D < soon[0][1] - now <= term.SIGNIN_NEAR, soon[0][1] - now)
        self.assertTrue(term.signin_words(soon[0][1], now).startswith('Sign-in ends '))
        self.assertTrue(term.signin_words(ended[0][1], now, True).startswith('Sign-in ended by yesterday '))
        far = [e for e, (at, _) in self.ends(v).items() if at is not None and at - now > term.SIGNIN_SOON]
        self.assertEqual(sorted(far), sorted([WORK, HARBOR, IVY]))      # known, and far off: nothing said

    def test_they_keep_their_distance_from_now_as_the_clock_moves(self):
        first = demo.view()
        later = first['now'] + 2 * H
        with mock.patch.object(demo, 'time', mock.Mock(time=lambda: later, sleep=time.sleep)):
            second = demo.view()
        for e, (at, _) in self.ends(first).items():
            with self.subTest(account=e):
                self.assertAlmostEqual(self.ends(second)[e][0] - second['now'], at - first['now'], places=3)


# ---------- the page ----------

ROWS = """
      await wait(400);
      const rows = {};
      for (const b of all('#acc .arow')) {
        const sig = b.querySelector('.sig'), again = b.querySelector('[data-do="relogin"]');
        rows[b.querySelector('.em').textContent] = {sig: sig ? sig.textContent : null, why: sig ? sig.title : null,
          again: again ? [again.textContent, again.getAttribute('aria-label')] : null,
          use: !!b.querySelector('[data-do="use"]'), state: b.querySelector('.c-state').textContent};
      }
      const note = $('#notes [data-k="signin-note"]'), noteTx = note ? note.querySelector('.tx') : null;
      const noteBtn = note ? note.querySelector('[data-do="signinAll"]') : null;
      const noteBtnWhy = noteBtn ? noteBtn.title : null;
"""


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class TheRows(test_app.ChromePage):
    def setUp(self):
        super().setUp()
        self.now = time.time()          # the demo's clock stands still: each date is exactly what the test set
        clock = mock.patch.object(demo, 'time', mock.Mock(time=lambda: self.now, sleep=time.sleep))
        clock.start()
        self.addCleanup(clock.stop)
        demo.view()

    def ends(self, email, at, needs_login=False):
        demo.STATE['accounts'][email].update(signin_ends_at=at, needs_login=needs_login,
                                             status='needs_login' if needs_login else 'ok')

    def look(self):
        return self.run_page(ROWS + "done({rows, note: noteTx ? noteTx.textContent : null, noteWhy: note ? (noteTx.querySelector('[title]') || {}).title : null, "
                             "noteBtn: noteBtn ? noteBtn.textContent : null, noteBtnWhy});")

    def opens(self):
        """The button's hover: every account a press opens (the server's own pick), by its whole email, in order."""
        due = signins.pick(server.signins_accounts(demo.STATE), self.now)
        return '\n'.join(['Signs in again, one after another, each in its own browser profile:'] + due +
                         ['You approve each in the tab that opens.'])

    def test_the_demo_shows_a_sign_in_that_ends_soon_and_one_that_ended(self):
        v = demo.view()
        fern = next(a for a in v['accounts'] if a['email'] == FERN)
        mail = next(a for a in v['accounts'] if a['email'] == MAIL)
        got = self.look()
        soon = term.signin_words(fern['signin_ends_at'], self.now)
        self.assertEqual(got['rows'][FERN]['sig'], soon)
        self.assertEqual(got['rows'][FERN]['why'], term.SIGNIN_WHY)
        self.assertEqual(got['rows'][FERN]['again'], ['Sign in', 'Sign in again as ' + FERN])
        self.assertTrue(got['rows'][FERN]['use'])                         # it still works: Use stays
        self.assertEqual(got['rows'][MAIL]['sig'], term.signin_words(mail['signin_ends_at'], self.now, True))
        self.assertTrue(got['rows'][MAIL]['sig'].startswith('Sign-in ended by yesterday '))
        self.assertEqual(got['rows'][MAIL]['again'], ['Sign in', 'Sign in again as ' + MAIL])
        self.assertFalse(got['rows'][MAIL]['use'])
        for e in (WORK, HARBOR, IVY):                                      # far off: nothing
            with self.subTest(account=e):
                self.assertIsNone(got['rows'][e]['sig'])
        self.assertIsNone(got['rows'][HARBOR]['again'])                   # its own Sign in is claude.ai's, as before
        # the note: one that ended (MAIL) and one that ends soon (FERN), counted; each on hover, the ended one first
        self.assertEqual(got['note'], '1 sign-in has ended and 1 ends this week.')
        self.assertEqual(got['noteBtn'], 'Sign in to all')
        self.assertEqual(got['noteWhy'], '%s\n%s: %s\n%s: %s' % (term.SIGNIN_WHY, MAIL, got['rows'][MAIL]['sig'], FERN, soon))
        # the button opens more than the two counted: the demo's harbor and northwind end well before ivy, so a press
        # signs them in too (next time is again one press); its hover names all four, in the order they open
        self.assertEqual(got['noteBtnWhy'], self.opens())
        self.assertEqual(got['noteBtnWhy'].split('\n')[1:-1], [MAIL, FERN, HARBOR, WORK])
        self.ends(MAIL, self.now + 20 * D)                                 # one account alone: its email and when
        got = self.look()
        self.assertEqual(got['note'], 'Sign-in for %s ends %s.' % (FERN, soon[len('Sign-in ends '):]))
        self.assertEqual(got['noteBtn'], 'Sign in to all')

    def test_a_date_over_7_days_away_or_not_known_shows_nothing(self):
        self.ends(FERN, self.now + 7 * D + 120)
        self.ends(IVY, None)
        got = self.look()
        self.assertEqual((got['rows'][FERN]['sig'], got['rows'][FERN]['again']), (None, None))
        self.assertIsNone(got['rows'][IVY]['sig'])
        self.assertEqual(got['note'], '1 sign-in has ended.')            # the demo's ended one; FERN is not counted
        self.ends(MAIL, self.now + 20 * D)
        self.assertIsNone(self.look()['note'])

    def test_the_account_in_use_signs_in_again_from_its_row_like_any_other(self):
        self.ends(WORK, self.now + 5 * D)                                  # within the week, not within 3 days
        self.ends(FERN, self.now + 20 * D)
        self.ends(MAIL, self.now + 20 * D)
        got = self.look()
        words = term.signin_words(self.now + 5 * D, self.now)
        self.assertEqual(got['rows'][WORK]['sig'], words)                 # no "/login" line
        self.assertEqual(got['rows'][WORK]['again'], ['Sign in', 'Sign in again as ' + WORK])
        self.assertTrue(got['rows'][WORK]['state'].startswith('In use'))
        # the account in use's within the week is in the note too, with the one button
        self.assertEqual(got['note'], 'Sign-in for %s ends %s.' % (WORK, words[len('Sign-in ends '):]))
        self.assertEqual(got['noteBtn'], 'Sign in to all')

    def test_the_account_in_use_once_ended_signs_in_again_here(self):
        self.ends(WORK, self.now - 2 * H, needs_login=True)
        got = self.look()
        self.assertEqual(got['rows'][WORK]['sig'], term.signin_words(self.now - 2 * H, self.now, True))
        self.assertEqual(got['rows'][WORK]['again'], ['Sign in', 'Sign in again as ' + WORK])
        self.assertTrue(got['rows'][WORK]['state'].startswith('In use'))

    def test_refused_before_its_date_keeps_its_words(self):
        self.ends(FERN, self.now + 2 * D, needs_login=True)
        got = self.look()
        self.assertIsNone(got['rows'][FERN]['sig'])
        self.assertEqual(got['rows'][FERN]['again'], ['Sign in', 'Sign in again as ' + FERN])
        # it needs a sign-in as one that ended does (signins.pick's own rule): counted, and a press signs it in; on
        # hover, what its row says on hover
        self.assertEqual(got['note'], '2 sign-ins have ended.')           # the demo's MAIL, and FERN
        self.assertIn(FERN, got['noteBtnWhy'].split('\n'))
        self.assertIn('%s: Its saved login stopped working' % FERN, got['noteWhy'].split('\n'))
        self.ends(MAIL, self.now + 20 * D)
        self.assertEqual(self.look()['note'], '1 sign-in has ended.')

    def test_the_note_is_one_short_line_and_one_button(self):
        self.ends(MAIL, self.now + 20 * D)
        self.ends(FERN, self.now + 2 * D)
        self.ends(HARBOR, self.now + 1 * D + 3 * H)
        self.ends(IVY, self.now + 5 * H)
        got = self.look()
        self.assertEqual(got['note'], '3 sign-ins end this week.')
        self.assertEqual(got['noteBtn'], 'Sign in to all')
        self.assertEqual(got['noteBtnWhy'], self.opens())
        # each email and its words on hover, soonest first (the ended ones before them)
        self.assertEqual(got['noteWhy'].split('\n'), [term.SIGNIN_WHY] + ['%s: %s' % (e, term.signin_words(at, self.now)) for e, at in (
            (IVY, self.now + 5 * H), (HARBOR, self.now + D + 3 * H), (FERN, self.now + 2 * D))])
        self.ends(MAIL, self.now - 2 * H, needs_login=True)                 # one has ended
        self.assertEqual(self.look()['note'], '1 sign-in has ended and 3 end this week.')
        self.ends(HARBOR, self.now + 20 * D)
        self.ends(IVY, self.now + 20 * D)
        self.assertEqual(self.look()['note'], '1 sign-in has ended and 1 ends this week.')
        self.ends(WORK, self.now - H, needs_login=True)
        self.assertEqual(self.look()['note'], '2 sign-ins have ended and 1 ends this week.')
        # "this week" is the button's own week (signins.WEEK): 6.5 days off is counted; over 7 days off it is not,
        # though a press still signs it in (it ends well before the others), and the hover says so
        self.ends(FERN, self.now + 6.5 * D)
        self.assertEqual(self.look()['note'], '2 sign-ins have ended and 1 ends this week.')
        self.ends(FERN, self.now + 7 * D + 120)
        got = self.look()
        self.assertEqual(got['note'], '2 sign-ins have ended.')
        self.assertEqual(got['noteBtnWhy'].split('\n')[1:-1], [MAIL, WORK, FERN])

    def test_the_row_menu_and_the_palette_offer_it(self):
        got = self.run_page("""
      await wait(400);
      const items = (e) => { document.querySelector('button[data-do="menu"][data-email="' + e + '"]').click();
        const out = all('#menu [role="menuitem"]').map((b) => [b.dataset.do, b.querySelector('span').textContent]); $('#menu').hidden = true; return out; };
      const menus = {fern: items('%s'), mail: items('%s'), ivy: items('%s'), work: items('%s')};
      document.dispatchEvent(new KeyboardEvent('keydown', {key: 'k', metaKey: true, bubbles: true})); await wait(200);
      const q = $('#pal-q'), find = async (text) => { q.value = text; q.dispatchEvent(new Event('input', {bubbles: true})); await wait(100);
        return all('.pal-it .lb').map((x) => x.textContent); };
      const first = (await find('')).filter((x) => x.includes('@'));
      const signIn = await find('sign in again');
      q.value = 'sign in again: maya@fern'; q.dispatchEvent(new Event('input', {bubbles: true})); await wait(100);
      q.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
      await wait(800);
      done({menus, first, signIn, sent: sent.filter((x) => x[0] === '/api/connect').map((x) => x[1].email)});
""" % (FERN, MAIL, IVY, WORK))
        self.assertEqual([x[0] for x in got['menus']['fern'][:2]], ['relogin', 'use'])
        self.assertEqual(got['menus']['fern'][0][1], 'Sign in again')
        self.assertEqual(got['menus']['mail'][0], ['relogin', 'Sign in again'])
        self.assertNotIn('relogin', [x[0] for x in got['menus']['ivy']])
        self.assertNotIn('relogin', [x[0] for x in got['menus']['work']])
        self.assertEqual(got['first'], ['In use: ' + WORK, 'Sign in again: ' + MAIL, 'Use ' + FERN, 'Use ' + HARBOR,
                                        'Use ' + IVY])                        # with nothing typed: each account once
        self.assertIn('Sign in to all', got['signIn'])                       # and the one press for all of them
        self.assertIn('Sign in again: ' + FERN, got['signIn'])
        self.assertEqual(got['sent'], [FERN])


# ---------- the same words as the terminal ----------

@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class SameWordsAsTheTerminal(test_app.ChromePage):
    """Each case is a made-up account of its own on the demo page, its date and whether it needs a sign-in set, the
    page's clock standing still at `now`; the line the page draws under it is what term.signin_words says (the page
    has no words of its own for an account it is not on: the account in use's are the terminal's signin_line)."""

    def drawn(self, now, cases):
        clock = mock.patch.object(demo, 'time', mock.Mock(time=lambda: now, sleep=time.sleep))
        clock.start()
        self.addCleanup(clock.stop)
        demo.STATE.clear()
        demo.FACTS.clear()
        demo.view()
        base = demo.STATE['accounts'][IVY]
        for e in (MAIL, FERN, HARBOR, IVY):
            demo.STATE['accounts'].pop(e)
            demo.STATE['order'].remove(e)
        for i, (at, need) in enumerate(cases):
            email = 'case%02d@words.example' % i
            demo.STATE['accounts'][email] = dict(copy.deepcopy(base), email=email, alias=None, slot=i + 2, profile=None,
                                                 profile_src=None, org_id='demo-org-case-%d' % i, signin_ends_at=at,
                                                 needs_login=need, status='needs_login' if need else 'ok')
            demo.STATE['order'].append(email)
        got = self.run_page("""
      await wait(400);
      done(all('#acc .arow').map((b) => [b.querySelector('.em').textContent, (b.querySelector('.sig') || {}).textContent || '']));
""", budget=12000)
        got = dict(got)
        return [got['case%02d@words.example' % i] for i in range(len(cases))]

    def test_the_page_and_python_say_the_same(self):
        now = local(18, 10)                                               # Wed Nov 18, 10:00 AM here
        cases = [(at, need) for at in (
            local(18, 20, 38), local(19, 1, 15), local(21, 1, 15), local(19, 0), local(18, 12), local(20, 0),
            local(22, 12), local(24, 23, 59), local(18, 23, 59) + 45, local(25, 9), now + 7 * D, now + 7 * D + 60,
            local(30, 9), local(18, 3, 5), local(17, 20, 38), local(13, 20, 38), local(12, 20, 38), local(11, 20, 38),
            local(1, 9), now, None, -9e10, -1, 0, 5e11) for need in (False, True)]
        want = [term.signin_words(at, now, need) for at, need in cases]
        self.assertEqual(self.drawn(now, cases), want)
        for said in ('Sign-in ends by today 8:38 PM', 'Sign-in ends by tomorrow 12:00 AM', 'Sign-in ends by Sun 12:00 PM',
                     'Sign-in ends by Nov 25 9:00 AM', 'Sign-in ended by yesterday 8:38 PM', 'Sign-in ended by Nov 11', ''):
            self.assertIn(said, want)                                     # the cases say each kind of thing

    def test_seconds_before_midnight_it_ended_today_never_tomorrow(self):
        now = local(18, 23, 59) + 50
        cases = [(now - 10, True), (now - 100, True), (now + 5, False)]
        want = [term.signin_words(at, now, need) for at, need in cases]
        self.assertEqual(want, ['Sign-in ended by today 11:59 PM', 'Sign-in ended by today 11:58 PM',
                                'Sign-in ends by tomorrow 12:00 AM'])
        self.assertEqual(self.drawn(now, cases), want)

    def test_across_a_new_year(self):
        now = local(31, 22, month=12)
        cases = [(local(1, 1, month=1, year=2027), False), (local(3, 1, month=1, year=2027), False),
                 (local(24, 23, month=12), True)]
        want = [term.signin_words(at, now, need) for at, need in cases]
        self.assertEqual(want, ['Sign-in ends by tomorrow 1:00 AM', 'Sign-in ends by Sun 1:00 AM', 'Sign-in ended by Dec 24'])
        self.assertEqual(self.drawn(now, cases), want)


# ---------- at 680 and 1280 px ----------

@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class TheLineFits(framed.FramedPage):
    LOOK = r"""
    const out = {};
    for (const f of frames) {
      const d = doc(f), w = f.contentWindow.innerWidth, rows = {};
      for (const b of d.querySelectorAll('#acc .arow')) {
        const em = b.querySelector('.em'), bar = b.querySelector('.c-week .bar').getBoundingClientRect(), r = b.getBoundingClientRect();
        const sig = b.querySelector('.sig');
        rows[em.textContent] = {emH: em.getBoundingClientRect().height, emLine: parseFloat(getComputedStyle(em).lineHeight),
          cut: em.scrollWidth > em.clientWidth, bar: [Math.round(bar.left), Math.round(bar.width)], barTop: Math.round(bar.top - r.top),
          sig: sig ? sig.textContent : null, sigRight: sig ? Math.round(sig.getBoundingClientRect().right) : null,
          right: Math.round(r.right)};
      }
      out[w] = {rows, wide: d.documentElement.scrollWidth > w};
    }
    done(out);
"""

    def test_no_email_wraps_and_no_bar_moves_sideways(self):
        demo.view()
        demo.STATE['accounts'][WORK]['signin_ends_at'] = time.time() + 2 * D      # the account in use's longer line
        got = self.in_frames(self.LOOK, widths=(680, 1280))
        for w in ('680', '1280'):
            rows = got[w]['rows']
            with self.subTest(width=w):
                self.assertFalse(got[w]['wide'])                              # nothing runs past the page
                self.assertIsNotNone(rows[FERN]['sig'])
                self.assertIsNotNone(rows[MAIL]['sig'])
                self.assertTrue(rows[WORK]['sig'].startswith('Sign-in ends by '))
                self.assertNotIn('/login', rows[WORK]['sig'])
                for e, r in rows.items():
                    self.assertEqual(r['emH'], r['emLine'], e)               # every email on one line
                    self.assertFalse(r['cut'], e)                             # and whole
                    if r['sig'] is not None:
                        self.assertLessEqual(r['sigRight'], r['right'], e)   # the line stays in its row
                # the bars of a row with the line sit where a row's without it do
                self.assertEqual(rows[FERN]['bar'], rows[HARBOR]['bar'])
                self.assertEqual(rows[WORK]['bar'], rows[IVY]['bar'])
        # in the table, the line is under the email and the bars stay at the top of their row
        self.assertEqual(got['1280']['rows'][FERN]['barTop'], got['1280']['rows'][HARBOR]['barTop'])


if __name__ == '__main__':
    unittest.main()
