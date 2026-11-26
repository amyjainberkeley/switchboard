import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Three things on the page, in headless Chrome and against the server:
#   - The links banner (linkapp.banner, which the links-lost notification shows) says what the page's links line says
#      for whoever holds the default-browser seat: an app that sends clicked links here, a browser, an app not seen
#      passing links on. A link router that sends them here is never said to have lost them.
#   - A plan cut short with "…" in the accounts table shows in full on hover (its title), with the account's note.
#   - The API key form's name box is wide enough for its words, at 1440 and at 390.
# Made-up *.example accounts and apps only.
import json
import os
import plistlib
import sys
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)

from switchboard import core, linkapp, server
from test_app import CHROME
from test_page_links_and_logo import FramedPage
from test_server_robust import Sandbox

PAGE = os.path.join(ROOT, 'switchboard', 'web', 'index.html')
ROUTER_ID = 'com.example.linkrouter'

# The page's links line (index.html, linksLine), each case in the banner's words. Off the page "here" and "this page"
# name nothing, so the banner says Switchboard and the page.
FORWARDS = ('%s takes clicked links and sends them to Switchboard: they open as their owner while the page runs. '
            'Switchboard Links also works when the page is closed.')
BROWSER = '%s is the default browser, so links you click in other apps open there, not as their owner.'
NOT_SEEN = '%s takes clicked links now. They open as their owner only if it sends them to Switchboard.'
FALSE_WORDS = ("don't open as their owner", 'don’t open as their owner', 'no longer open as their owner')


def seat(name, kind, bid):
    return {'holder': {'path': '/Applications/%s.app' % name, 'id': bid, 'name': name, 'kind': kind}, 'loop': False}


ROUTER = seat('Link Router', 'handler', ROUTER_ID)
CHROME_SEAT = seat('Google Chrome', 'browser', 'com.google.Chrome')
NOBODY = {'holder': {'path': None, 'id': None, 'name': None, 'kind': None}, 'loop': False}


def until(test, seconds=10.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if test():
            return True
        time.sleep(0.02)
    return False


# ---------- the links banner says what the page's links line says ----------

class TheBannerSaysWhatTheLinksLineSays(unittest.TestCase):
    """server._links_banner for the status the background look just read: the page's words for each holder, whether
    links were never set up or the seat went since."""

    def setUp(self):
        for target, values in ((server.FORWARDED, {'at': None, 'by': None, 'read': False, 'saved': None, 'saved_by': None}),
                               (server.LINKS, {'holder': None})):
            p = mock.patch.dict(target, values)
            p.start()
            self.addCleanup(p.stop)
        for k in (server.LINKS_WANTED, server.FORWARDED_KEY):
            core.set_setting(k, None)
            self.addCleanup(core.set_setting, k, None)

    def banner(self, where, wanted, browser=None):
        core.set_setting(server.LINKS_WANTED, wanted)
        with mock.patch.object(linkapp, 'seat', lambda fresh=False, look=True: where):
            return server._links_banner({'installed': True, 'on': False, 'browser': browser})

    def passed_on(self, where):
        """The app in the seat passed a clicked link to the page (what Handler._old_router notes)."""
        server.LINKS['holder'] = where['holder']
        server.note_forwarded()

    def test_a_link_router_sends_links_here(self):
        self.passed_on(ROUTER)
        for wanted in (None, True):                     # never set up, or set up and the seat went since
            with self.subTest(wanted=wanted):
                b = self.banner(ROUTER, wanted, 'Link Router')
                self.assertEqual(b, {'text': FORWARDS % 'Link Router', 'action': 'make_default'})
                for words in FALSE_WORDS:
                    self.assertNotIn(words, b['text'])

    def test_a_browser_holds_the_seat(self):
        self.passed_on(CHROME_SEAT)                     # noted, but a browser never passes links on
        for wanted in (None, True):
            with self.subTest(wanted=wanted):
                self.assertEqual(self.banner(CHROME_SEAT, wanted, 'Google Chrome'),
                                 {'text': BROWSER % 'Google Chrome', 'action': 'make_default'})

    def test_an_app_not_seen_passing_links_on(self):
        for wanted in (None, True):
            with self.subTest(wanted=wanted):
                self.assertEqual(self.banner(ROUTER, wanted, 'Link Router'),
                                 {'text': NOT_SEEN % 'Link Router', 'action': 'make_default'})

    def test_a_holder_with_no_name(self):
        self.assertEqual(self.banner(NOBODY, True)['text'], NOT_SEEN % 'Another app')
        self.assertEqual(self.banner(NOBODY, True, 'Link Router')['text'], NOT_SEEN % 'Link Router')   # as the page

    def test_still_nothing_when_links_open_as_their_owner_or_were_turned_off(self):
        self.assertIsNone(self.banner(ROUTER, False, 'Link Router'))          # `switchboard links off`
        core.set_setting(server.LINKS_WANTED, None)
        with mock.patch.object(linkapp, 'seat', lambda fresh=False, look=True: ROUTER):
            self.assertIsNone(server._links_banner({'installed': True, 'on': True, 'browser': 'Switchboard Links'}))
            self.assertIsNone(server._links_banner({'installed': False, 'on': False, 'browser': None}))

    def test_the_same_words_as_the_pages_links_line(self):
        with open(PAGE, encoding='utf-8') as f:
            page = f.read()
        on_the_page = (FORWARDS % '').replace('sends them to Switchboard:', 'sends them here:').replace(
            'while the page runs', 'while this page runs')
        for words in (on_the_page, BROWSER % '', NOT_SEEN % ''):
            self.assertIn(words, page)


class ALinkRouterIsNeverSaidToLoseLinks(Sandbox):
    """One notification, when the seat leaves Switchboard Links, shows the banner's words: for an app that has
    sent clicked links here, that they open as their owner while the page runs."""

    def setUp(self):
        super().setUp()
        for target, values in ((server.FORWARDED, {'at': None, 'by': None, 'read': False, 'saved': None, 'saved_by': None}),
                               (server.LINKS, {'banner': None, 'handlers': None, 'holder': None})):
            p = mock.patch.dict(target, values)
            p.start()
            self.addCleanup(p.stop)
        self.patch(linkapp, 'handlers_note', lambda found=None, **k: None)      # nothing read from this Mac's Applications
        apps = os.path.join(self.home, 'Applications')
        self.router = self.app(apps, 'Link Router.app', ROUTER_ID, 'Link Router')
        self.links_app = self.app(apps, 'Switchboard Links.app', linkapp.BUNDLE_ID, 'Switchboard Links')

    @staticmethod
    def app(folder, name, bid, display):
        path = os.path.join(folder, name)
        os.makedirs(os.path.join(path, 'Contents'))
        with open(os.path.join(path, 'Contents', 'Info.plist'), 'wb') as f:
            plistlib.dump({'CFBundleIdentifier': bid, 'CFBundleDisplayName': display}, f)
        return path

    def look(self, holder):
        """One background look, the link app saying `holder` has the seat."""
        value = {'installed': True, 'on': holder == self.links_app,
                 'browser': os.path.splitext(os.path.basename(holder))[0]}
        self.patch(linkapp, 'status', lambda fresh=False: value)
        self.patch(linkapp, 'CACHE', {'at': time.time(), 'value': value, 'path': holder})
        with server.LINKS_LOCK:
            server.LINKS.update(loop=True, at=None, running=False)
        self.assertTrue(server.refresh_link_status(force=True))
        self.assertTrue(until(lambda: not server.LINKS['running']))
        return server.link_extras()

    def test_the_seat_goes_back_to_the_link_router(self):
        got = self.look(self.router)                                   # before the install: the router has the seat
        self.assertEqual(got['banner']['text'], NOT_SEEN % 'Link Router')
        server.note_forwarded()                                        # it sends a clicked link to the page
        got = self.look(self.router)
        self.assertTrue(got['forwards'])
        self.assertEqual(got['banner']['text'], FORWARDS % 'Link Router')
        self.assertIsNone(self.look(self.links_app)['banner'])          # Switchboard Links is made the default
        self.assertEqual(self.shown, [])
        got = self.look(self.router)                                   # later the router takes the seat back
        self.assertEqual(got['banner']['text'], FORWARDS % 'Link Router')
        self.assertEqual(len(self.shown), 1)                           # one notification, in the same words
        self.assertEqual(self.shown[0][-1], FORWARDS % 'Link Router')
        for words in FALSE_WORDS:
            self.assertNotIn(words, ' '.join(self.shown[0]))


# ---------- a plan cut short shows in full on hover ----------

FERNHILL = 'maya@fernhill.example'
NOTE = 'Anthropic is holding back new numbers for a while: the real ones can only be higher.'

# In each frame the made-up fernhill account's state gets a note (the engine's line when its numbers are not current).
PLANS = r"""
    for (const f of frames) {
      const w = f.contentWindow, base = w.fetch;
      w.fetch = function (u, o) {
        if (String(u) === '/api/state') return base.call(w, '/api/state').then((r) => r.json()).then((s) => {
          for (const a of s.accounts) if (a.email === %(email)s) a.note = %(note)s;
          return new w.Response(JSON.stringify(s), {status: 200, headers: {'Content-Type': 'application/json'}});
        });
        return base.apply(w, arguments);
      };
      doc(f).dispatchEvent(new w.Event('visibilitychange'));
    }
    await wait(2500);
    const cells = (f, q) => [...doc(f).querySelectorAll('#acc .arow')].map((r) => {
      const el = r.querySelector(q), sn = el.querySelector('.sn-m'), ch = el.querySelector('.chn');   // "· 2 chats" after the plan
      const all = el.textContent.slice(sn ? sn.textContent.length : 0), chats = ch ? ch.textContent : null;
      return {email: r.dataset.email, title: el.getAttribute('title'), chats,
              plan: chats ? all.slice(0, all.length - (' · ' + chats).length) : all,
              shown: getComputedStyle(el).display !== 'none', cut: el.scrollWidth > el.clientWidth};
    });
    done({widths: frames.map((f) => f.contentWindow.innerWidth), wide: cells(frames[0], '.sub'),
          narrow: cells(frames[1], '.sub')});
"""


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class ThePlanInFullWhereItIsCut(FramedPage):
    def test_a_plan_cut_short_shows_in_full_on_hover_with_the_note_under_it(self):
        got = self.in_frames(PLANS % {'email': json.dumps(FERNHILL), 'note': json.dumps(NOTE)}, widths=(1440, 1200),
                             budget=30000)
        self.assertEqual(got['widths'], [1440, 1200])
        wide, narrow = got['wide'], got['narrow']
        self.assertEqual(len(wide), 5)
        self.assertTrue(all(x['shown'] for x in wide + narrow))           # under the email at 1440 and at 1200
        # the plan without what the email says ("Team · Northwind" on maya@northwind.example is "Team"), so
        # none is cut at 1440; the whole line is on hover, with the note under it
        self.assertEqual([x['cut'] for x in wide], [False] * 5)
        lines = dict((a['email'], a.get('plan_line') or ' · '.join(x for x in (a.get('plan'), a.get('org')) if x))
                     for a in server.view()['accounts'])
        for width, cells in ((1440, wide), (1200, narrow)):
            for x in cells:
                with self.subTest(email=x['email'], width=width):
                    self.assertTrue(lines[x['email']].startswith(x['plan']))
                    chats = ' · ' + x['chats'] if x['chats'] else ''               # the line's chats, in full on hover too
                    self.assertEqual(x['title'], lines[x['email']] + chats + ('\n' + NOTE if x['email'] == FERNHILL else ''))


# ---------- the API key form's name box fits its words ----------

KEYNAME = r"""
    for (const f of frames) f.contentWindow.location.hash = '#settings';
    const box = (f) => doc(f).querySelector('input[data-k="kname"]');
    for (let i = 0; i < 150 && !frames.every(box); i++) await wait(100);
    await wait(300);
    done(frames.map((f) => {
      const d = doc(f), i = box(f), width = f.contentWindow.innerWidth;
      if (!i) return {width, found: false};
      const cs = getComputedStyle(i), px = (k) => parseFloat(cs[k]) || 0, ctx = d.createElement('canvas').getContext('2d');
      ctx.font = cs.fontWeight + ' ' + cs.fontSize + ' ' + cs.fontFamily;
      // the input's own box less its border and padding (an input's clientWidth already leaves the padding out)
      const room = i.getBoundingClientRect().width - px('borderLeftWidth') - px('borderRightWidth') - px('paddingLeft') - px('paddingRight');
      const words = ctx.measureText(i.placeholder).width;
      i.value = i.placeholder;
      const scrolls = i.scrollWidth > i.clientWidth;
      i.value = '';
      return {width, found: true, placeholder: i.placeholder, room: Math.floor(room), words: Math.ceil(words), scrolls,
              right: Math.round(i.parentNode.getBoundingClientRect().right)};
    }));
"""


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class TheKeyNameBoxFitsItsWords(FramedPage):
    def test_at_1440_and_390_the_name_box_shows_all_its_words(self):
        got = self.in_frames(KEYNAME, widths=(1440, 390), budget=30000)
        self.assertEqual([x['width'] for x in got], [1440, 390])
        for x in got:
            with self.subTest(width=x['width']):
                self.assertTrue(x['found'])
                self.assertEqual(x['placeholder'], 'Name, like console')      # the same words, in a box that fits them
                self.assertLessEqual(x['words'], x['room'])
                self.assertFalse(x['scrolls'])
                self.assertLessEqual(x['right'], x['width'])                 # and the box stays inside the window


if __name__ == '__main__':
    unittest.main()
