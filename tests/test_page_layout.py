import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The page's layout: the sidebar that holds everything, the accounts table with the switch line over its usage,
# artifacts as folders of page pictures, short names, the links line. Run in headless Chrome (a throwaway profile each
# run, no window) on the demo's made-up accounts, plus the server's two facts for the links line.
import base64
import json
import os
import re
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)

from switchboard import core, demo, server
from test_app import CHROME, ChromePage
from test_page_links_and_logo import FramedPage, STAND_IN

PAGE = os.path.join(ROOT, 'switchboard', 'web', 'index.html')
NORTHWIND, MAIL, FERNHILL, HARBOR, LAB = ('maya@northwind.example', 'maya.reyes@mail.example', 'maya@fernhill.example',
                                          'm.reyes@harbor.example', 'maya@ivy.example')
# A made-up picture of a page (48 x 30, one colour): what GET /api/preview/<id> answers for an artifact with one.
PICTURE = base64.b64decode(
    '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAA0JCgsKCA0LCgsODg0PEyAVExISEyccHhcgLikxMC4pLSwzOko+MzZGNywtQFdBRkxOUlNSMj5aYVpQYEpRUk//2wBD'
    'AQ4ODhMREyYVFSZPNS01T09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT0//wAARCAAeADADASIAAhEBAxEB/8QAHwAAAQUB'
    'AQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJico'
    'KSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ'
    '2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMi'
    'MoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOk'
    'paanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwCKiiivaOIKKKKACiiigAooooAKKKKACiiigD//2Q==')

# /api/state, stood in for: the server's own answer with `extra` laid over it (the links line's cases).
STATE_EXTRA = r"""
      let extra = {};
      const base = window.fetch;
      window.fetch = function (u, o) {
        if (String(u) === '/api/state' && Object.keys(extra).length) return base('/api/state').then((r) => r.json()).then((s) =>
          new Response(JSON.stringify(Object.assign(s, extra)), {status: 200, headers: {'Content-Type': 'application/json'}}));
        return base.apply(this, arguments);
      };
      const look = async (ms) => { document.dispatchEvent(new Event('visibilitychange')); await wait(ms); };
"""


def read():
    with open(PAGE, encoding='utf-8') as f:
        return f.read()


def own(email):
    return sorted((x for x in demo.STATE['artifacts'] if x['owner'] == email), key=lambda x: -x['t'])


# ---------- the sidebar holds everything, each item a view with its address ----------

@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class TheSidebarAndItsAddresses(ChromePage):
    def test_each_item_is_a_view_with_its_count_and_its_address(self):
        got = self.run_page("""
      await wait(600);
      const nav = () => all('#nav a').map((a) => [a.getAttribute('href'), a.querySelector('span').textContent,
                                                  (a.querySelector('.count') || {}).textContent || null, a.getAttribute('aria-current')]);
      const shown = () => all('main .sec').filter((s) => !s.hidden).map((s) => s.id);
      const out = {first: nav(), shownFirst: shown(), side: [$('#side .side-search').textContent, !!$('#side .kb'), !!$('#ver'), $('#side').textContent]};
      out.views = [];
      for (const a of all('#nav a')) { a.click(); await wait(500); out.views.push([location.hash, shown(), ($('#nav [aria-current]') || {}).textContent]); }
      out.about = $('#about').textContent;
      done(out);
""")
        self.assertEqual([x[:2] for x in got['first']], [['#accounts', 'Accounts'], ['#chats', 'Chats'], ['#artifacts', 'Artifacts'],
                                                         ['#activity', 'Activity'], ['#settings', 'Settings']])   # Chats
        counts = [x[2] for x in got['first']]
        self.assertEqual([counts[0], counts[2]], ['5', str(len(demo.STATE['artifacts']))])   # five accounts, nine artifacts (Chats between)
        self.assertEqual(counts[3:], [None, None])                                        # Activity and Settings carry no counts
        self.assertEqual([x[3] for x in got['first']], ['page', None, None, None, None])
        self.assertEqual(got['shownFirst'], ['sec-accounts'])
        self.assertEqual(got['side'][0], 'Search⌘K')
        self.assertEqual(got['side'][1:3], [False, False])                                # no footer (no shortcuts row, no version)
        self.assertNotIn(core.VERSION, got['side'][3])
        self.assertTrue(re.match(r'^Switchboard %s · demo · localhost:\d+$' % re.escape(core.VERSION), got['about']), got['about'])   # About, in Settings
        self.assertEqual([v[:2] for v in got['views']], [['#accounts', ['sec-accounts']], ['#chats', ['sec-chats']], ['#artifacts', ['sec-artifacts']],
                                                         ['#activity', ['sec-activity']], ['#settings', ['sec-settings']]])
        for (hash, shown, current), name in zip(got['views'], ('Accounts', 'Chats', 'Artifacts', 'Activity', 'Settings')):
            self.assertTrue(current.startswith(name), current)               # the item of the view shown is marked

    def test_a_link_opens_its_view_and_back_works(self):
        got = self.run_page("""
      await wait(600);
      const shown = () => all('main .sec').filter((s) => !s.hidden).map((s) => s.id)[0];
      const out = {start: [location.hash, shown(), $('#h-set') && !$('#sec-settings').hidden]};
      location.hash = '#artifacts'; await wait(500);
      $('#files-list .folder').click(); await wait(500);
      out.folder = [location.hash, $('#h-art').textContent];
      history.back(); await wait(500);
      out.back1 = [location.hash, all('#files-list .folder').length];
      history.back(); await wait(500);
      out.back2 = [location.hash, shown()];
      location.hash = '#artifacts/work'; await wait(500);
      document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})); await wait(500);
      out.esc = [location.hash, all('#files-list .folder').length];
      location.hash = '#nowhere'; await wait(400);
      out.unknown = shown();
      location.hash = '#settings/links'; await wait(600);
      out.group = [shown(), Math.round($('#g-links').getBoundingClientRect().top)];
      done(out);
""", path='#settings')
        self.assertEqual(got['start'], ['#settings', 'sec-settings', True])     # an address opens its view
        self.assertEqual(got['folder'], ['#artifacts/%s' % NORTHWIND.replace('@', '%40'), 'Artifacts›' + NORTHWIND])   # a folder's address is its email
        self.assertEqual(got['back1'], ['#artifacts', 6])                         # Back: the folders again
        self.assertEqual(got['back2'], ['#settings', 'sec-settings'])             # and the view before
        self.assertEqual(got['esc'], ['#artifacts', 6])                           # Esc leaves a folder
        self.assertEqual(got['unknown'], 'sec-accounts')
        self.assertEqual(got['group'][0], 'sec-settings')
        self.assertLess(got['group'][1], 200)                                     # #settings/<group> scrolls to it

    def test_a_folder_address_lands_in_its_artifacts_view(self):
        got = self.run_page("""
      await wait(1500);
      done(location.hash);
""", path='?folder=' + FERNHILL)
        self.assertEqual(got, '#artifacts/%s' % FERNHILL.replace('@', '%40'))


# ---------- the accounts: no slot numbers, the switch line over the usage, marks and red ----------

@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class NoSlotNumbers(ChromePage):
    def test_no_one_to_five_anywhere_on_the_page(self):
        got = self.run_page("""
      await wait(600);
      const head = all('#acc-board .ahead [role="columnheader"]').map((x) => x.textContent);
      // "2 chats" under an email is a count of chats, not a place: read the rows without it.
      const bare = (el) => { const c = el.cloneNode(true); c.querySelectorAll('.chn').forEach((x) => x.remove()); return c; };
      const words = (el) => { const w = document.createTreeWalker(bare(el), NodeFilter.SHOW_TEXT), out = []; while (w.nextNode()) out.push(w.currentNode.textContent.trim()); return out.filter(Boolean); };
      const rows = all('#acc .arow').map((b) => ({slot: b.dataset.slot, who: b.querySelector('.c-who').textContent,
        first: words(b)[0], bits: words(b), cells: [...bare(b).querySelectorAll('.c')].map((c) => c.textContent.trim())}));
      $('[data-do="menu"]').click(); await wait(300);
      const menu = $('#menu').textContent;
      document.body.click();
      const keys = all('#kbd kbd').map((k) => k.textContent);
      location.hash = '#settings'; await wait(900);
      const settings = {slots: all('#sec-settings .slot').length, text: $('#sec-settings').textContent};
      done({head, rows, menu, keys, settings});
""")
        self.assertEqual(got['head'], ['Account', '5-hour', 'Weekly', 'State'])   # no # column; names for a screen reader, not on the page
        for r in got['rows']:
            with self.subTest(row=r['who']):
                self.assertIn('@', r['first'])                                             # a row starts with its email
                self.assertFalse([b for b in r['bits'] if re.fullmatch(r'#?[1-9]\.?', b)])  # no number of its own
                self.assertFalse([c for c in r['cells'] if re.fullmatch(r'[1-9]', c)])     # no cell that is a bare digit
        self.assertNotRegex(got['menu'], r'\b[1-9]\b')
        self.assertTrue(set(got['keys']) <= {'⌘', 'K', '↵', 'G', 'A', 'W', 'C', 'P', 'L', 'S', '/', 'R', 'esc', '?'}, got['keys'])   # W: the week; C: chats
        self.assertEqual(got['settings']['slots'], 0)
        self.assertNotIn('Move up', got['settings']['text'])                                # order lives in each row's menu

    def test_number_keys_do_nothing(self):
        got = self.run_page("""
      await wait(400);
      for (const k of ['1', '2', '3', '4', '5', '9']) document.dispatchEvent(new KeyboardEvent('keydown', {key: k, bubbles: true}));
      await wait(800);
      done({sent: sent.filter((x) => x[0] === '/api/switch').length, h1: $('#h-live').textContent});
""")
        self.assertEqual(got, {'sent': 0, 'h1': 'Claude Code is on %s' % NORTHWIND})

    def test_the_text_has_no_number_keys(self):
        html = read()
        self.assertNotIn("/^[1-9]$/.test(e.key)", html)
        self.assertNotIn('key: k', html.split('// ---------- keys')[1].split('// ---------- first draw')[0])
        self.assertIn('// ---------- keys (no number keys: switching by keyboard is ⌘K and a short name) ----------', html)


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class TheSwitchLineOverTheUsage(ChromePage):
    def test_the_fable_field_writes_model_at_from_settings(self):
        # (the field sat on the switch line; that line now names only what the rows show, and a model's week has no
        # line in the rows, so its field is in Settings, Switch at, alone)
        got = self.run_page("""
      await wait(600);
      const onStrip = !!document.querySelector('#auto [data-key="model_at"]');
      location.hash = '#settings'; await wait(900);
      const m = document.querySelector('#row-lines input[data-key="model_at"]');
      m.focus(); m.value = '92'; m.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
      await wait(900);
      done({onStrip, label: m.closest('label').textContent.trim(), saved: $('#lines-say').textContent,
            sent: sent.filter((x) => x[0] === '/api/auto').map((x) => x[1])});
""")
        self.assertFalse(got['onStrip'])
        self.assertEqual(got['label'], 'Fable’s week')
        self.assertEqual(got['sent'], [{'model_at': 92}])                       # the API already takes model_at
        self.assertEqual(got['saved'], 'Saved')

    def test_with_no_model_counted_its_field_and_name_go(self):
        demo._ready()
        demo.FACTS['auto'].update(model=None, models=[])
        got = self.run_page("""
      await wait(600);
      location.hash = '#settings'; await wait(900);
      done({strip: !!document.querySelector('#auto [data-key="model_at"]'), settings: all('#row-lines label').map((x) => x.textContent)});
""")
        self.assertEqual(got, {'strip': False, 'settings': ['5-hour', 'Weekly']})

    def test_fifty_to_ninety_nine_point_nine_esc_restores_and_leaving_keeps(self):
        got = self.run_page("""
      await wait(600);
      const b = () => $('#line5'), key = (k) => b().dispatchEvent(new KeyboardEvent('keydown', {key: k, bubbles: true}));
      b().focus(); b().value = '49.9'; key('Enter'); await wait(300);
      const low = [b().value, $('#strip-say').textContent];
      b().focus(); b().value = '99.9'; key('Enter'); await wait(800);
      const top = [b().value, $('#strip-say').textContent];
      b().focus(); b().value = '77'; key('Escape'); await wait(300);
      const esc = b().value;
      b().focus(); b().value = '88'; b().dispatchEvent(new Event('change', {bubbles: true})); b().blur(); await wait(800);
      done({low, top, esc, left: b().value, sent: sent.filter((x) => x[0] === '/api/auto').map((x) => x[1])});
""")
        self.assertEqual(got['low'], ['90', 'Pick a number from 50 to 99.9.'])
        self.assertEqual(got['top'], ['99.9', 'Saved'])
        self.assertEqual(got['esc'], '99.9')
        self.assertEqual(got['left'], '88')
        self.assertEqual(got['sent'], [{'session_at': 99.9}, {'session_at': 88}])


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class MarksAndRedAtTheLine(ChromePage):
    def test_each_bar_has_its_mark_and_turns_red_at_or_past_it(self):
        got = self.run_page("""
      await wait(600);
      const row = (e) => all('#acc .arow').find((b) => b.querySelector('.em').textContent === e);
      const bar = (e, kind) => row(e).querySelector('.c[data-kind="' + kind + '"] .bar');
      const look = (e, kind) => { const b = bar(e, kind); return {mark: b.querySelector('b').style.left, red: b.classList.contains('over'),
        fill: getComputedStyle(b.querySelector('i')).backgroundColor, pct: getComputedStyle(b.parentNode.querySelector('.pct')).color}; };
      const before = {work: look('%s', 'five'), studio: look('%s', 'week')};
      const box = $('#line5'); box.focus(); box.value = '60';
      box.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
      const after = {work: look('%s', 'five'), ivy: look('%s', 'five')};
      done({before, after, marks: all('#acc .bar > b').length, bars: all('#acc .c[data-kind] .bar').length});
""" % (NORTHWIND, FERNHILL, NORTHWIND, LAB))
        self.assertEqual(got['marks'], got['bars'])                           # every usage bar has its mark
        self.assertEqual((got['before']['work']['mark'], got['before']['studio']['mark']), ('90%', '95%'))
        self.assertFalse(got['before']['work']['red'])                        # 62% under its 90% line: blue, in use
        self.assertEqual(got['before']['work']['fill'], 'rgb(0, 106, 214)')
        self.assertEqual(got['after']['work']['mark'], '60%')                  # the mark follows the line at once
        self.assertTrue(got['after']['work']['red'])                           # 62% at or past 60%: red
        self.assertEqual((got['after']['work']['fill'], got['after']['work']['pct']), ('rgb(220, 38, 38)', 'rgb(220, 38, 38)'))
        self.assertFalse(got['after']['ivy']['red'])                           # ivy's 20% stays under it

    def test_dark_has_its_own_red_and_mark(self):
        got = self.run_page("""
      await wait(600);
      document.documentElement.dataset.theme = 'dark';
      const box = $('#line5'); box.focus(); box.value = '60';
      box.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
      const b = $('#acc .arow.is-on .c[data-kind="five"] .bar');
      done({fill: getComputedStyle(b.querySelector('i')).backgroundColor, mark: getComputedStyle(b.querySelector('b')).backgroundColor,
            muted: getComputedStyle($('#acc .sub')).color, bg: getComputedStyle(document.body).backgroundColor});
""")
        self.assertEqual(got['fill'], 'rgb(248, 113, 113)')                    # #f87171
        self.assertEqual(got['mark'], 'rgb(212, 212, 216)')                    # #d4d4d8
        self.assertEqual(got['muted'], 'rgb(161, 161, 170)')
        self.assertEqual(got['bg'], 'rgb(9, 9, 11)')


class SecondaryTextContrast(unittest.TestCase):
    """Secondary text is #6b6b74 in light: at least 4.5:1 on white and on the grey of chips and hovered rows."""

    @staticmethod
    def ratio(a, b):
        def lum(h):
            c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
            return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
        x, y = sorted((lum(a), lum(b)), reverse=True)
        return (x + 0.05) / (y + 0.05)

    def test_the_tokens(self):
        html = read()
        self.assertIn('--muted-fg: #6b6b74;', html)
        for ground in ('#ffffff', '#fafafa', '#f4f4f5'):
            self.assertGreaterEqual(self.ratio('#6b6b74', ground), 4.5, ground)
        self.assertGreaterEqual(self.ratio('#a1a1aa', '#09090b'), 4.5)         # dark's on its ground
        self.assertGreaterEqual(self.ratio('#52525b', '#e4e4e7'), 3.0)         # the mark on its track, light


# ---------- artifacts: folders, tiles, the picture or the title, one click as the owner ----------

def stand_in_previews(test, ids, pictures):
    """The artifacts the server says have a picture (`ids`), and what GET /api/preview/<id> answers (`pictures`: a
    JPEG, or a 404 for an id not in it): the server's side of it, stood in for."""
    real_files, real_get = demo.files, server.Handler._get

    def files():
        d = real_files()
        for f in d['folders']:
            for x in f['files']:
                x['preview'] = x['id'] in ids
        return d

    def _get(handler):
        route = handler.path.split('?')[0]
        if route.startswith('/api/preview/'):
            aid = route[len('/api/preview/'):]
            if aid in pictures:
                return handler._send(200, pictures[aid], 'image/jpeg')
            return handler._send(404, {'error': 'not found'})
        return real_get(handler)
    for target, name, value in ((demo, 'files', files), (server.Handler, '_get', _get)):
        p = mock.patch.object(target, name, value)
        p.start()
        test.addCleanup(p.stop)


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class FoldersAndTiles(ChromePage):
    def test_one_folder_per_account_and_not_matched_only_while_it_holds_something(self):
        got = self.run_page("""
      await wait(600);
      location.hash = '#artifacts'; await wait(800);
      done(all('#files-list .folder').map((f) => [f.querySelector('.fname').textContent, f.querySelector('.fcount').textContent,
                                                  f.classList.contains('quiet'), f.getAttribute('href')]));
""")
        def at(e):                                                          # each folder by its email, and its address
            return '#artifacts/' + e.replace('@', '%40')

        self.assertEqual(got, [[NORTHWIND, '4 artifacts', False, at(NORTHWIND)], [MAIL, '2 artifacts', False, at(MAIL)],
                               [FERNHILL, '1 artifact', False, at(FERNHILL)], [HARBOR, '1 artifact', False, at(HARBOR)],
                               [LAB, '0 artifacts', False, at(LAB)],
                               ['Not matched yet', '1 artifact', True, '#artifacts/not-matched']])
        demo._ready()
        for x in demo.STATE['artifacts']:
            x['owner'] = x['owner'] or NORTHWIND                           # every artifact's owner known
        got = self.run_page("""
      await wait(600);
      location.hash = '#artifacts'; await wait(800);
      done(all('#files-list .fname').map((f) => f.textContent));
""")
        self.assertEqual(got, [NORTHWIND, MAIL, FERNHILL, HARBOR, LAB])

    def test_a_folder_shows_its_tiles_newest_first_with_a_picture_or_the_title(self):
        demo._ready()
        mine = own(NORTHWIND)
        stand_in_previews(self, {mine[0]['id'], mine[1]['id']}, {mine[0]['id']: PICTURE})   # the second's picture is gone
        got = self.run_page("""
      await wait(600);
      location.hash = '#artifacts/work'; await wait(1500);
      done({crumb: $('#h-art').textContent, lede: $('#art-lede').textContent,
            tiles: all('#files-list a.tile').map((t) => {
              const img = t.querySelector('img');
              return {title: t.querySelector('.tt span').textContent, img: img ? [img.getAttribute('src'), img.getAttribute('loading'),
                img.getAttribute('alt'), img.naturalWidth] : null, leaf: (t.querySelector('.leaf .pt') || {}).textContent || null};
            })});
""")
        self.assertEqual(got['crumb'], 'Artifacts›' + NORTHWIND)
        self.assertIn('4 artifacts · each opens as %s, in Chrome profile “Work”' % NORTHWIND, got['lede'])
        self.assertEqual([t['title'] for t in got['tiles']], [x['title'] for x in mine])          # newest first
        first, second = got['tiles'][0], got['tiles'][1]
        self.assertEqual(first['img'], ['/api/preview/' + mine[0]['id'], 'lazy', '', 48])         # the picture of the page
        self.assertIsNone(first['leaf'])
        self.assertIsNone(second['img'])                                     # its picture failed: the title tile instead
        self.assertEqual(second['leaf'], mine[1]['title'])
        for t in got['tiles'][2:]:                                           # no picture: the title set in a plain page
            self.assertEqual((t['img'], t['leaf']), (None, t['title']))

    def test_search_covers_every_folder_and_a_pasted_link_opens_as_its_owner(self):
        got = self.run_page(STAND_IN + """
      await wait(600);
      location.hash = '#artifacts/ivy'; await wait(800);
      const f = $('#files-find'), type = async (v) => { f.value = v; f.dispatchEvent(new Event('input', {bubbles: true})); await wait(300); };
      await type('i');
      const found = [$('#art-lede').textContent, ($('.found') || {}).textContent, all('#files-list a.tile .tm').map((x) => x.textContent.split(' · ')[0])];
      await type('https://claude.ai/artifact/0f1e2d3c4b5a69788796a5b4c3d2e1f0');
      const tile = $('#files-list [data-k="open:pasted"]');
      tile.click(); await wait(300);
      done({found, asked: window.__go.slice()});
""")
        self.assertEqual(got['found'][0], 'Across every folder.')
        self.assertTrue(set(got['found'][2]) > {NORTHWIND}, got['found'])     # not only the folder it was typed in
        self.assertEqual(got['asked'], [{'url': 'https://claude.ai/artifact/0f1e2d3c4b5a69788796a5b4c3d2e1f0'}])


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class OneClickOpensAsItsOwner(ChromePage):
    def test_one_click_opening_and_a_real_link_for_the_rest(self):
        demo._ready()
        mine = own(NORTHWIND)
        got = self.run_page(STAND_IN + """
      await wait(600);
      location.hash = '#artifacts/work'; await wait(900);
      const tile = () => $('#files-list a.tile');
      tile().dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, metaKey: true}));   // a new tab: the browser's
      await wait(200);
      const meta = window.__go.length;
      tile().click(); await wait(150);
      const during = [tile().querySelector('.tm').textContent, tile().classList.contains('busy'), $('#toasts').textContent];
      tile().click(); await wait(150);
      const asked = window.__go.slice();
      window.__let('%s'); await wait(600);
      done({meta, during, asked, after: tile().querySelector('.tm').textContent, href: tile().getAttribute('href')});
""" % NORTHWIND)
        self.assertEqual(got['meta'], 0)                                       # ⌘-click is left to the browser
        self.assertEqual(got['during'][:2], ['Opening as %s…' % NORTHWIND, True])
        self.assertIn('switchboard open ' + mine[0]['url'], got['during'][2])  # the command that does the same
        self.assertEqual(got['asked'], [{'url': mine[0]['url']}])             # one request, through POST /api/go
        self.assertNotEqual(got['after'], 'Opening as %s…' % NORTHWIND)
        self.assertEqual(got['href'], mine[0]['url'])

    def test_it_goes_through_the_open_path_that_exists(self):
        html = read()
        body = html[html.index('  open({url}) {'):html.index('  openFolder({id})')]
        self.assertIn("post('/api/go', {url})", body)
        self.assertIn('if (!url || busy[url]) return;', body)               # a second press sends nothing


# ---------- short names, and switching by keyboard ----------

@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class ShortNamesAndCmdK(ChromePage):
    def names(self):
        # A row and a ⌘K item each name an account by its whole email; an alias is only what ⌘K also finds it by
        return self.run_page("""
      await wait(600);
      const tags = all('#acc .arow').map((b) => b.querySelector('.c-who .who').textContent === b.querySelector('.em').textContent);
      document.dispatchEvent(new KeyboardEvent('keydown', {key: 'k', metaKey: true, bubbles: true})); await wait(100);
      const q = $('#pal-q'); q.value = '@'; q.dispatchEvent(new Event('input', {bubbles: true})); await wait(50);
      let grp = '';                                       // the Accounts group's rows only (the chat rows name their home's email too)
      const items = all('#pal-list > *').filter((x) => { if (x.classList.contains('pal-g')) grp = x.textContent; return grp === 'Accounts' && x.classList.contains('pal-it'); })
        .map((x) => (x.querySelector('.lb') || {}).textContent || '').filter((t) => /^(Use |In use: |Sign in again: )/.test(t));
      if (!tags.every(Boolean)) done({error: 'a row shows more than its email'});
      done(items.map((t) => t.replace(/^(Use |In use: |Sign in again: )/, '')));
""")

    def test_every_account_by_its_email_with_an_alias_or_without(self):
        demo._ready()
        demo.STATE['accounts'][MAIL]['alias'] = ''
        demo.STATE['accounts'][LAB]['alias'] = ''
        lab2 = dict(demo.STATE['accounts'][LAB], email='ana@ivy.example', slot=6, alias='')
        demo.STATE['accounts']['ana@ivy.example'] = lab2                     # two at ivy.example: each by its email still
        demo.STATE['order'].append('ana@ivy.example')
        self.assertEqual(sorted(self.names()), sorted([NORTHWIND, MAIL, FERNHILL, HARBOR, LAB, 'ana@ivy.example']))

    def test_cmd_k_finds_accounts_folders_artifacts_and_settings_and_a_short_name_switches(self):
        demo._ready()
        demo.STATE['accounts'][MAIL]['alias'] = ''
        demo.STATE['accounts'][MAIL].update(needs_login=False, status='ok')     # the demo's ended sign-in: one to use here
        got = self.run_page("""
      await wait(800);
      const key = (k, more, t) => (t || document).dispatchEvent(new KeyboardEvent('keydown', Object.assign({key: k, bubbles: true}, more || {})));
      key('k', {metaKey: true}); await wait(200);
      const groups = all('#pal-list .pal-g').map((g) => g.textContent);
      const q = $('#pal-q'), type = async (v) => { q.value = v; q.dispatchEvent(new Event('input', {bubbles: true})); await wait(150); };
      await type('brand');
      const art = all('#pal-list .pal-it .lb').map((x) => x.textContent);
      await type('order');
      const set = all('#pal-list .pal-it .lb').map((x) => x.textContent);
      await type('mail');
      const top = $('.pal-it[aria-selected="true"]').textContent;
      key('Enter', {}, q); await wait(1500);
      const switched = sent.filter((x) => x[0] === '/api/switch').map((x) => x[1]);
      key('k', {metaKey: true}); await wait(200);
      key('Escape', {}, $('#pal-q')); await wait(200);
      done({groups, art, set, top, switched, closed: $('#pal').hidden, h1: $('#h-live').textContent});
""")
        self.assertEqual(got['groups'], ['Go to', 'Accounts', 'Artifacts'])     # the first look: where to go, who to use
        self.assertIn('Brand guide draft', got['art'])
        self.assertIn('Order', got['set'])
        self.assertTrue(got['top'].startswith('Use %s' % MAIL), got['top'])    # part of its email finds it, first
        self.assertEqual(got['switched'], [{'email': MAIL}])
        self.assertTrue(got['closed'])                                          # Esc closes it
        self.assertEqual(got['h1'], 'Claude Code is on %s' % MAIL)

    def test_slash_finds_in_the_view_and_question_mark_opens_the_shortcuts(self):
        got = self.run_page("""
      await wait(600);
      const key = (k) => document.dispatchEvent(new KeyboardEvent('keydown', {key: k, bubbles: true}));
      location.hash = '#settings'; await wait(700);
      key('/'); await wait(100);
      const set = document.activeElement.id;
      document.activeElement.blur();
      location.hash = '#artifacts'; await wait(700);
      key('/'); await wait(100);
      const art = document.activeElement.id;
      document.activeElement.blur();
      key('?'); await wait(200);
      const sheet = !$('#kbd').hidden;
      key('Escape'); await wait(200);
      done({set, art, sheet, closed: $('#kbd').hidden});
""")
        self.assertEqual(got, {'set': 'set-q', 'art': 'files-find', 'sheet': True, 'closed': True})


# ---------- the links line says what is true ----------

HOLDERS = {'router': {'name': 'Link Router', 'kind': 'handler'}, 'chrome': {'name': 'Google Chrome', 'kind': 'browser'},
           'safari': {'name': 'Safari', 'kind': 'browser'}}
BANNER = {'text': 'Links no longer open as their owner.', 'action': 'make_default'}


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class TheLinksLineSaysWhatIsTrue(ChromePage):
    def line(self, links):
        return self.run_page(STATE_EXTRA + """
      await wait(400);
      extra = {links: Object.assign({installed: true, on: false, browser: null, home: '', key: 'demo', banner: null, handlers: null,
                                     holder: null, forwards: false}, %s)};
      await look(900);
      location.hash = '#artifacts'; await wait(600);
      const art = ($('#art-needs') || {}).textContent || '';
      location.hash = '#settings'; await wait(900);
      const set = ($('#g-links .needs[data-k="links-line"]') || {}).textContent || '';
      const needs = ($('#g-links [data-k="links-needs"]') || {}).textContent || '';
      const btn = $('#g-links [data-do="links"]');
      if (btn) { btn.click(); await wait(500); }
      done({art, set, needs, sent: sent.filter((x) => x[0] === '/api/links').map((x) => x[1]),
            asking: ($('#g-links .needs') || {}).textContent || ''});
""" % json.dumps(links))

    def test_switchboard_links_is_the_default_nothing_to_say(self):
        got = self.line({'on': True, 'browser': 'Switchboard Links'})
        self.assertEqual((got['art'], got['set'], got['needs'], got['sent']), ('', '', '', []))

    def test_another_app_that_sends_links_here(self):
        got = self.line({'browser': 'Link Router', 'banner': BANNER, 'holder': HOLDERS['router'], 'forwards': True})
        want = ('Link Router takes clicked links and sends them here: they open as their owner while this page runs. '
                'Switchboard Links also works when the page is closed.')
        for text in (got['art'], got['set']):
            self.assertEqual(text, want + 'Make Switchboard Links the default')
        self.assertEqual(got['sent'], [{'on': True}])
        self.assertIn('macOS is asking…', got['asking'])                     # while macOS asks, no second button

    def test_a_browser_holds_the_seat(self):
        for who in ('chrome', 'safari'):
            with self.subTest(browser=who):
                h = HOLDERS[who]
                got = self.line({'browser': h['name'], 'banner': BANNER, 'holder': h})
                want = '%s is the default browser, so links you click in other apps open there, not as their owner.' % h['name']
                self.assertEqual(got['art'], want + 'Make Switchboard Links the default')
                self.assertEqual(got['set'], got['art'])
                self.assertEqual(got['sent'], [{'on': True}])

    def test_another_app_not_seen_sending_links_here(self):
        got = self.line({'browser': 'Link Router', 'banner': BANNER, 'holder': HOLDERS['router'], 'forwards': False})
        self.assertEqual(got['art'], 'Link Router takes clicked links now. They open as their owner only if it sends them to '
                                     'Switchboard.Make Switchboard Links the default')

    def test_turned_off_on_purpose_or_no_link_app(self):
        got = self.line({'browser': 'Safari', 'banner': None, 'holder': HOLDERS['safari']})   # `switchboard links off`
        self.assertEqual((got['art'], got['set'], got['sent']), ('', '', []))
        got = self.line({'installed': False, 'browser': None})
        self.assertEqual((got['art'], got['set']), ('', ''))
        self.assertEqual(got['needs'], 'Needs the link app: switchboard update --link-app (it needs Apple’s command line tools)')

    def test_the_page_never_says_links_from_other_apps_dont_open_as_their_owner(self):
        self.assertNotIn('so links from other apps don’t open as their owner', read())


class TheServerKnowsWhoHoldsTheSeat(unittest.TestCase):
    """The two facts behind the links line: the seat's holder (from the link app's status, read in the background) and
    whether that app has passed a clicked link to this page (Handler._old_router's requests, noted)."""

    def setUp(self):
        p = mock.patch.dict(server.FORWARDED, {'at': None, 'by': None, 'read': False, 'saved': None, 'saved_by': None}, clear=True)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.dict(server.LINKS, {'holder': None})
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(core.set_setting, server.FORWARDED_KEY, None)
        core.set_setting(server.FORWARDED_KEY, None)

    def test_a_browser_never_passes_links_on(self):
        server.LINKS['holder'] = {'name': 'Safari', 'kind': 'browser', 'id': 'com.apple.Safari', 'path': '/x'}
        server.note_forwarded()
        self.assertEqual(server.link_extras()['holder'], {'name': 'Safari', 'kind': 'browser'})
        self.assertFalse(server.link_extras()['forwards'])

    def test_a_handler_that_passed_a_link_on_and_another_that_did_not(self):
        server.LINKS['holder'] = {'name': 'Link Router', 'kind': 'handler', 'id': 'com.example.router', 'path': '/x'}
        self.assertFalse(server.link_extras()['forwards'])                  # nothing passed on yet
        server.note_forwarded(now=1000.0)
        self.assertTrue(server.link_extras()['forwards'])
        self.assertEqual(core.settings()[server.FORWARDED_KEY], {'at': 1000, 'by': 'com.example.router'})
        server.LINKS['holder'] = {'name': 'Other', 'kind': 'handler', 'id': 'com.example.other', 'path': '/y'}
        self.assertFalse(server.link_extras()['forwards'])                  # another app took the seat since

    def test_a_page_started_again_still_knows(self):
        core.set_setting(server.FORWARDED_KEY, {'at': 1000, 'by': 'com.example.router'})
        server.LINKS['holder'] = {'name': 'Link Router', 'kind': 'handler', 'id': 'com.example.router', 'path': '/x'}
        self.assertTrue(server.link_extras()['forwards'])

    def test_settings_are_written_at_most_once_an_hour_for_it(self):
        server.LINKS['holder'] = {'name': 'Link Router', 'kind': 'handler', 'id': 'com.example.router', 'path': '/x'}
        writes = []
        with mock.patch.object(server, 'set_setting', lambda k, v: writes.append((k, v))):
            for t in (1000.0, 1010.0, 2000.0, 1000.0 + server.FORWARDED_EVERY + 1):
                server.note_forwarded(now=t)
        self.assertEqual([v['at'] for k, v in writes], [1000, 1000 + server.FORWARDED_EVERY + 1])

    def test_the_old_routers_request_is_noted(self):
        import threading
        import urllib.request
        from http.server import ThreadingHTTPServer
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = True
        demo.STATE.clear()
        demo.FACTS.clear()
        self.addCleanup(demo.STATE.clear)
        self.addCleanup(demo.FACTS.clear)
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        port = httpd.server_address[1]
        server.LINKS['holder'] = {'name': 'Link Router', 'kind': 'handler', 'id': 'com.example.router', 'path': '/x'}
        with mock.patch.dict(os.environ, {'SWITCHBOARD_PORT': str(port)}), mock.patch.object(demo, 'act', lambda p, b: {'demo': True}):
            for header in ({'X-Claude-Switch': '1'}, {'X-Switchboard': '1'}):
                req = urllib.request.Request('http://127.0.0.1:%d/api/go' % port, method='POST',
                                             data=json.dumps({'url': 'https://claude.ai/artifact/0f1e2d3c4b5a69788796a5b4c3d2e1f0'}).encode(),
                                             headers=dict({'Content-Type': 'application/json'}, **header))
                urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=10).close()
                if 'X-Claude-Switch' in header:
                    self.assertIsNotNone(server.FORWARDED['at'])            # the old router's: noted
                    server.FORWARDED['at'] = None
        self.assertIsNone(server.FORWARDED['at'])                           # the page's own: not another app's


# ---------- both themes, and the layout at 390 px ----------

PHONE = r"""
    const d = doc(frames[0]), w = frames[0].contentWindow.innerWidth, out = {width: w, found: [], views: []};
    const look = (what) => {
      for (const theme of ['light', 'dark']) {
        d.documentElement.dataset.theme = theme;
        if (d.documentElement.scrollWidth > w) out.found.push(theme + ', ' + what + ': ' + d.documentElement.scrollWidth);
        for (const el of d.querySelectorAll('main *, #side *')) {
          if (el.closest('.chips')) continue;                      // the group chips scroll sideways, on purpose
          const r = el.getClientRects().length ? el.getBoundingClientRect() : null;
          if (r && r.width && r.right > w + 0.5 && getComputedStyle(el).position !== 'fixed') out.found.push(theme + ', ' + what + ': ' + (el.className || el.tagName) + ' ends at ' + Math.round(r.right));
        }
      }
      delete d.documentElement.dataset.theme;
      out.views.push(what);
    };
    const go = async (hash, ms) => { frames[0].contentWindow.location.hash = hash; await wait(ms); };
    const side = d.getElementById('side');
    out.bar = {head: Math.round(d.querySelector('.side-head').getBoundingClientRect().height), nav: getComputedStyle(d.getElementById('side-body')).display,
               menu: getComputedStyle(d.getElementById('menu-btn')).display, top: getComputedStyle(side).position};
    d.getElementById('menu-btn').click(); await wait(300);
    out.menu = {shown: getComputedStyle(d.getElementById('side-body')).display, items: [...d.querySelectorAll('#nav a span:first-of-type')].map((x) => x.textContent),
                search: !!d.querySelector('#side-body .side-search'), keys: !!d.querySelector('#side-body .kb'), ver: !!d.getElementById('ver')};
    d.querySelector('#nav a[href="#artifacts"]').click(); await wait(600);
    out.closed = getComputedStyle(d.getElementById('side-body')).display;
    await go('#accounts', 600);
    out.rows = [...d.querySelectorAll('#acc .arow')].map((r) => getComputedStyle(r).gridTemplateAreas);
    out.strip = [...d.querySelectorAll('#auto input.line')].map((i) => Math.round(i.getBoundingClientRect().right) <= w);
    look('Accounts');
    await go('#artifacts', 700); look('Artifacts');
    out.folders = getComputedStyle(d.querySelector('#files-list .folders')).gridTemplateColumns.split(' ').length;
    await go('#artifacts/work', 800); look('a folder');
    out.tiles = getComputedStyle(d.querySelector('#files-list .tiles')).gridTemplateColumns.split(' ').length;
    await go('#activity', 700); look('Activity');
    await go('#settings', 1100); look('Settings');
    done(out);
"""


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class ThePhoneLayout(FramedPage):
    def test_390_px_a_top_bar_with_a_menu_each_account_a_block_and_nothing_past_the_edge(self):
        got = self.in_frames(PHONE, widths=(390,), budget=30000)
        self.assertEqual(got['width'], 390)
        self.assertEqual(got['found'], [])
        self.assertEqual(got['views'], ['Accounts', 'Artifacts', 'a folder', 'Activity', 'Settings'])
        self.assertEqual(got['bar'], {'head': 56, 'nav': 'none', 'menu': 'flex', 'top': 'sticky'})   # a top bar, its menu button
        self.assertEqual(got['menu']['shown'], 'flex')                       # its menu holds the same items
        self.assertEqual(got['menu']['items'], ['Accounts', 'Chats', 'Artifacts', 'Activity', 'Settings'])
        self.assertTrue(got['menu']['search'])
        self.assertFalse(got['menu']['keys'] or got['menu']['ver'])          # no footer, in the menu either
        self.assertEqual(got['closed'], 'none')                              # a choice closes it
        self.assertTrue(all('"who state"' in a for a in got['rows']), got['rows'])   # each account a block
        self.assertEqual(got['strip'], [True, True])                         # the line (5-hour, weekly) fits on the phone
        self.assertEqual((got['folders'], got['tiles']), (2, 2))             # two to a row: room for a whole email


class TheTitleAndTheLogo(unittest.TestCase):
    def test_title_tab_icon_and_mark(self):
        html = read()
        self.assertIn('<title>Switchboard</title>', html)
        self.assertIn("M16.3 7H10.05a2.5 2.5 0 0 0 0 5h4.25a2.5 2.5 0 0 1 0 5H7.55", html.split('</head>')[0])   # the tab icon
        self.assertIn("$('brand').innerHTML = BRAND;", html)                 # the sidebar's header
        self.assertNotIn('M15.5 8.6c-.4-1.5-1.8-2.6-3.6-2.6', html)          # never the mockup's first-draft S


if __name__ == '__main__':
    unittest.main()
