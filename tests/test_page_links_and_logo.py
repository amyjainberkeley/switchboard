import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The page and the product's marks:
#   - the auto-switch numbers are drawn as small fields you type into;
#   - one click on an artifact opens it as its owner;
#   - opening as the owner is always on: no switch on the page, `switchboard links on|off` as the way out;
#   - the logo (a dark tile, a white S, a blue dot) everywhere the product draws one.
# The page parts run in headless Chrome (a throwaway profile, no window) on the demo's made-up accounts.
import html as htmllib
import json
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)

from switchboard import __main__ as cli
from switchboard import core, demo, keep, linkapp, server, term
from test_app import CHROME, ChromePage
import test_keep  # its sandbox: a throwaway home, the account engine's fake, the command line

PAGE = os.path.join(ROOT, 'switchboard', 'web', 'index.html')


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


# The page in one frame per width (headless Chrome keeps its own window at least 500 px wide; a frame is laid out at
# its own width, media queries included). `%(body)s` runs once every frame has drawn, with frames, doc(f) and done().
FRAMES = r"""<!doctype html><meta charset="utf-8"><body style="margin:0"><main hidden></main>%(frames)s<pre id="result"></pre><script>
(async () => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const frames = [...document.querySelectorAll('iframe')], doc = (f) => f.contentDocument;
  const done = (x) => { document.getElementById('result').textContent = JSON.stringify(x); };
  try {
    for (let i = 0; i < 300 && !frames.every((f) => doc(f) && doc(f).querySelector('main[data-drawn]')); i++) await wait(50);
    await Promise.all(frames.map((f) => doc(f).fonts.ready));
    await wait(1200);
%(body)s
  } catch (e) { done({error: String(e && e.stack || e)}); }
})();
</script>"""


class FramedPage(ChromePage):
    def in_frames(self, body, widths=(390,), budget=20000):
        def page(handler, name):
            if 'frames' in handler.path:
                data = (FRAMES % {'body': body, 'frames': ''.join(
                    '<iframe src="/?w=%d" style="display:block;width:%dpx;height:900px;border:0"></iframe>' % (w, w)
                    for w in widths)}).encode()
            else:
                with open(os.path.join(core.WEB, name), 'rb') as f:
                    data = f.read()
            handler.send_response(200)                  # framed here only: the real page refuses to be framed
            handler.send_header('Content-Type', 'text/html; charset=utf-8')
            handler.send_header('Content-Length', str(len(data)))
            handler.end_headers()
            handler.wfile.write(data)
        with mock.patch.object(server.Handler, '_page', page):
            dom = self.dom('?frames=1', budget=budget)
        m = re.search(r'<pre id="result">(.*?)</pre>', dom, re.S)
        if not m or not m.group(1).strip():
            self.fail('the frames did not finish')
        got = json.loads(htmllib.unescape(m.group(1)))
        if isinstance(got, dict) and got.get('error'):
            self.fail(got['error'])
        return got


# ---------- the auto-switch numbers: fields you can see are fields ----------

LOOK = r"""
      const look = (d, theme) => {
        d.documentElement.dataset.theme = theme;
        const page = getComputedStyle(d.body).backgroundColor;
        const out = [...d.querySelectorAll('#auto input.line')].map((b) => {
          const c = getComputedStyle(b), r = b.getBoundingClientRect();
          return {id: b.id, bg: c.backgroundColor, page, edge: [c.borderTopWidth, c.borderTopStyle, c.borderTopColor],
                  height: r.height, value: b.value, pct: getComputedStyle(b.parentNode, '::after').content,
                  label: b.getAttribute('aria-label'), mode: b.inputMode, enter: b.getAttribute('enterkeyhint'),
                  tag: b.tagName, type: b.type, disabled: b.disabled, tab: b.tabIndex};
        });
        delete d.documentElement.dataset.theme;
        return out;
      };
"""


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class NumbersAreFields(FramedPage):
    def check_look(self, boxes):
        # The component is drawn as: "at [90 %] 5-hour [95 %] weekly", each a field with a hairline edge and its %
        # inside the box (the model's field, Fable here, is in Settings: the line names only what the rows show)
        self.assertEqual([b['id'] for b in boxes], ['line5', 'lineW'])
        for b in boxes:
            with self.subTest(box=b['id']):
                self.assertNotIn(b['bg'], ('rgba(0, 0, 0, 0)', 'transparent'))   # a field's ground, always
                self.assertEqual(b['edge'][:2], ['1px', 'solid'])                  # a hairline edge, always
                self.assertNotIn(b['edge'][2], (b['bg'], b['page'], 'rgba(0, 0, 0, 0)'))
                self.assertGreaterEqual(b['height'], 28)                           # a field to type into
                self.assertNotIn('%', b['value'])                                  # the number alone in the value
                self.assertEqual(b['pct'], '"%"')                                  # its % drawn inside the box
                self.assertTrue(b['label'].startswith('Switch at this'), b['label'])
                self.assertEqual((b['tag'], b['type'], b['mode'], b['enter']), ('INPUT', 'text', 'decimal', 'done'))
                self.assertEqual((b['disabled'], b['tab']), (False, 0))            # reached with Tab

    def test_each_number_is_a_field_light_and_dark(self):
        got = self.run_page(LOOK + """
      await wait(300);
      done({light: look(document, 'light'), dark: look(document, 'dark')});
""")
        self.check_look(got['light'])
        self.check_look(got['dark'])
        self.assertNotEqual(got['light'][0]['bg'], got['dark'][0]['bg'])

    def test_on_a_phone_they_are_the_same_fields_and_nothing_runs_off_the_edge(self):
        got = self.in_frames(LOOK + """
    const d = doc(frames[0]);
    done({width: frames[0].contentWindow.innerWidth, light: look(d, 'light'), dark: look(d, 'dark'),
          wide: d.documentElement.scrollWidth});
""")
        self.assertEqual(got['width'], 390)
        self.check_look(got['light'])
        self.check_look(got['dark'])
        self.assertLessEqual(got['wide'], 390)

    def test_enter_or_leaving_keeps_escape_and_anything_else_put_the_old_number_back(self):
        got = self.run_page("""
      await wait(300);
      const box = (id) => document.getElementById(id);
      const key = (b, k) => b.dispatchEvent(new KeyboardEvent('keydown', {key: k, bubbles: true}));
      const out = {};
      let b = box('line5'); b.focus(); out.focused = document.activeElement === b;
      b.value = '85'; b.dispatchEvent(new Event('input', {bubbles: true})); key(b, 'Enter'); await wait(800);
      out.enter = box('line5').value;
      out.saved = ($('#strip-say') || {}).textContent || '';
      b = box('lineW'); b.focus(); b.value = '92.5'; b.dispatchEvent(new Event('input', {bubbles: true}));
      b.dispatchEvent(new Event('change', {bubbles: true})); b.blur(); await wait(800);   // leaving the field
      out.left = [box('lineW').value, ($('#sec-settings input.line[data-key="weekly_at"]') || {}).value || null];
      b = box('lineW'); b.focus(); b.value = '9'; key(b, 'Escape'); await wait(300);
      out.escape = box('lineW').value;
      b = box('line5'); b.focus(); b.value = '120'; key(b, 'Enter'); await wait(300);
      out.refused = [box('line5').value, ($('#strip-say') || {}).textContent || '', document.getElementById('live').textContent];
      done(Object.assign(out, {sent: sent.filter((x) => x[0] === '/api/auto').map((x) => x[1])}));
""")
        self.assertTrue(got['focused'])
        self.assertEqual(got['enter'], '85')
        self.assertEqual(got['saved'], 'Saved')                             # said quietly beside the fields
        self.assertEqual(got['left'][0], '92.5')
        self.assertEqual(got['escape'], '92.5')
        self.assertEqual(got['refused'], ['85', 'Pick a number from 50 to 99.9.', 'Pick a number from 50 to 99.9.'])
        self.assertEqual(got['sent'], [{'session_at': 85}, {'weekly_at': 92.5}])


# ---------- one press on an artifact opens it as its owner ----------

class Pipe:
    """Chrome's DevTools protocol over --remote-debugging-pipe: JSON messages ending in a NUL, Chrome reading fd 3 and
    writing fd 4. Only what these tests need: a command, and its answer."""

    def __init__(self, rd, wr):
        self.rd, self.wr, self.buf, self.n = rd, wr, b'', 0

    def send(self, method, params=None, session=None, timeout=60):
        self.n += 1
        msg = dict({'id': self.n, 'method': method, 'params': params or {}}, **({'sessionId': session} if session else {}))
        os.write(self.wr, json.dumps(msg).encode() + b'\0')
        end = time.time() + timeout
        while time.time() < end:
            while b'\0' in self.buf:
                raw, self.buf = self.buf.split(b'\0', 1)
                got = json.loads(raw)
                if got.get('id') == self.n:
                    if 'error' in got:
                        raise AssertionError('%s: %s' % (method, got['error']))
                    return got.get('result', {})
            if not select.select([self.rd], [], [], max(0.1, end - time.time()))[0]:
                continue
            chunk = os.read(self.rd, 1 << 20)
            if not chunk:
                raise AssertionError('Chrome closed its pipe')
            self.buf += chunk
        raise AssertionError('Chrome did not answer %s in %d s' % (method, timeout))


class KeyPage(ChromePage):
    """The real page driven over Chrome's DevTools pipe, for what only a real key press does: Enter on a focused
    button presses it (a script's own KeyboardEvent never does). A throwaway profile, removed after; Chrome is stopped
    by the pid recorded here."""

    def open_page(self, path=''):
        profile = tempfile.mkdtemp(prefix='switchboard-chrome-')
        self.addCleanup(shutil.rmtree, profile, True)
        to_r, to_w = os.pipe()
        from_r, from_w = os.pipe()

        def child():                            # the pipe's ends become Chrome's fds 3 and 4
            import fcntl
            r, w = fcntl.fcntl(to_r, fcntl.F_DUPFD, 10), fcntl.fcntl(from_w, fcntl.F_DUPFD, 10)
            os.dup2(r, 3)
            os.dup2(w, 4)
            os.set_inheritable(3, True)
            os.set_inheritable(4, True)
        p = subprocess.Popen([CHROME, '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
                              '--disable-extensions', '--use-mock-keychain', '--password-store=basic',
                              '--user-data-dir=' + profile, '--remote-debugging-pipe', 'about:blank'],
                             preexec_fn=child, close_fds=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
        os.close(to_r)
        os.close(from_w)

        def stop():
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
            p.wait()
            os.close(to_w)
            os.close(from_r)
        self.addCleanup(stop)
        self.pipe = Pipe(from_r, to_w)
        target = self.pipe.send('Target.createTarget', {'url': 'about:blank'})['targetId']
        self.session = self.pipe.send('Target.attachToTarget', {'targetId': target, 'flatten': True})['sessionId']
        self.pipe.send('Page.navigate', {'url': self.url + path}, self.session)
        self.js("""(async () => { for (let i = 0; i < 300 && !document.querySelector('main[data-drawn]'); i++)
                     await new Promise((r) => setTimeout(r, 50)); })()""", timeout=90)

    def js(self, expr, timeout=60):
        r = self.pipe.send('Runtime.evaluate', {'expression': expr, 'awaitPromise': True, 'returnByValue': True},
                           self.session, timeout)
        if r.get('exceptionDetails'):
            raise AssertionError(json.dumps(r['exceptionDetails'])[:600])
        return r.get('result', {}).get('value')

    def press(self, key, code, vk, text=''):
        for kind in ('keyDown', 'keyUp'):
            self.pipe.send('Input.dispatchKeyEvent', dict({'type': kind, 'key': key, 'code': code, 'windowsVirtualKeyCode': vk,
                                                          'nativeVirtualKeyCode': vk}, **({'text': text} if kind == 'keyDown' and text else {})),
                           self.session)


# The open request, stood in for: every POST /api/go is noted in window.__go and held until window.__let() answers
# it as the server does for a link opened as its owner.
STAND_IN = r"""
      window.__go = []; window.__held = [];
      const fetchBefore = window.fetch;                // (not `before`: a test's own words may use that name)
      window.fetch = function (u, o) {
        if (String(u) === '/api/go') {
          window.__go.push(JSON.parse(o.body || '{}'));
          return new Promise((r) => window.__held.push(r)).then((email) => new Response(JSON.stringify({email, checked: true}),
            {status: 200, headers: {'Content-Type': 'application/json'}}));
        }
        return fetchBefore.apply(this, arguments);
      };
      window.__let = (email) => { window.__held.splice(0).forEach((r) => r(email)); };
"""

NORTHWIND = 'maya@northwind.example'


def own(email):
    return sorted((x for x in demo.STATE['artifacts'] if x['owner'] == email), key=lambda x: -x['t'])


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class OnePressOpens(ChromePage):
    # A folder per account (named by its email; its Chrome profile in its tooltip, and the email again in the line
    # under the breadcrumb), its artifacts as tiles, each a real link that one click opens as its owner. What is said goes to a
    # toast at the corner (#toasts).
    def test_a_row_in_a_folder_opens_it_as_its_owner_once_with_opening_while_it_does(self):
        got = self.run_page(STAND_IN + """
      await wait(1200);
      location.hash = '#artifacts'; await wait(400);
      all('#files-list .folder').find((b) => (b.title || '').startsWith('%s')).click(); await wait(400);
      const row = () => $('#files-list a.tile'), say = () => $('#toasts').textContent;
      const first = {title: row().querySelector('.tt span').textContent, when: row().querySelector('.tm').textContent,
                     owner: $('#art-lede').textContent, kind: row().tagName, href: row().getAttribute('href'),
                     expanded: row().hasAttribute('aria-expanded'), copy: (row().nextElementSibling || {}).textContent};
      row().click(); await wait(200);
      const during = {when: row().querySelector('.tm').textContent, busy: row().getAttribute('aria-busy'),
                      off: row().getAttribute('aria-disabled'), say: say()};
      row().click(); row().click(); await wait(200);
      const asked = window.__go.slice();
      window.__let('%s'); await wait(600);
      done({first, during, asked, after: {when: row().querySelector('.tm').textContent, busy: row().getAttribute('aria-busy'),
            say: say()}, preview: !!$('#files-list .preview')});
""" % (NORTHWIND, NORTHWIND))
        mine = own(NORTHWIND)
        self.assertEqual(got['first']['title'], mine[0]['title'])
        self.assertIn('each opens as %s' % NORTHWIND, got['first']['owner'])    # a folder is its owner's, said above it
        self.assertNotEqual(got['first']['when'], '')                       # the tile still says when it changed
        self.assertEqual((got['first']['kind'], got['first']['href']), ('A', mine[0]['url']))   # a real link: Enter opens it
        self.assertFalse(got['first']['expanded'])                          # nothing unfolds
        self.assertEqual(got['first']['copy'], 'Copy link')
        self.assertEqual({k: got['during'][k] for k in ('when', 'busy', 'off')}, {'when': 'Opening as %s…' % NORTHWIND, 'busy': 'true', 'off': 'true'})
        self.assertIn('Opening as %s, in Chrome profile “Work”.' % NORTHWIND, got['during']['say'])
        self.assertEqual(got['asked'], [{'url': mine[0]['url']}])           # the tile's own request, sent once
        self.assertNotIn(got['after']['when'], ('', 'Opening as %s…' % NORTHWIND))  # its date again (the demo's clock moves)
        self.assertIsNone(got['after']['busy'])
        self.assertIn('Opened as %s.' % NORTHWIND, got['after']['say'])
        self.assertFalse(got['preview'])

    def test_a_found_row_keeps_its_owner_while_it_opens(self):
        got = self.run_page(STAND_IN + """
      await wait(1200);
      location.hash = '#artifacts'; await wait(400);
      const f = $('#files-find'); f.value = 'brand'; f.dispatchEvent(new Event('input', {bubbles: true})); await wait(300);
      const row = () => $('#files-list a.tile');
      const before = [row().querySelector('.tt span').textContent, row().querySelector('.tm').textContent];
      row().click(); await wait(200);
      const during = row().querySelector('.tm').textContent;
      row().click(); await wait(200);
      const asked = window.__go.slice();
      window.__let('maya@fernhill.example'); await wait(500);
      done({before, during, asked, tip: row().title, label: row().getAttribute('aria-label')});
""")
        brand = next(x for x in demo.STATE['artifacts'] if x['title'] == 'Brand guide draft')
        self.assertEqual(got['before'][0], 'Brand guide draft')
        self.assertTrue(got['before'][1].startswith('maya@fernhill.example · '), got['before'])   # its owner, by its email
        self.assertEqual(got['during'], 'Opening as maya@fernhill.example…')   # the owner stays on the tile
        self.assertEqual(got['asked'], [{'url': brand['url']}])
        self.assertTrue(got['tip'].startswith('Open as maya@fernhill.example, '), got['tip'])
        self.assertIn('changed ', got['tip'])                              # when it changed is still one look away
        self.assertEqual(got['label'], 'Open Brand guide draft as maya@fernhill.example')

    def test_folders_still_unfold_and_a_pasted_link_still_opens_as_its_owner(self):
        link = 'https://claude.ai/artifact/0f1e2d3c4b5a69788796a5b4c3d2e1f0'
        got = self.run_page(STAND_IN + """
      await wait(1200);
      location.hash = '#artifacts'; await wait(400);
      const folders = all('#files-list .folder').length;
      $('#files-list .folder').click(); await wait(400);
      const files = all('#files-list a.tile').length, inside = !!$('#h-art .crumb'), where = location.hash;
      $('#h-art .crumb').click(); await wait(400);
      const back = [location.hash, all('#files-list .folder').length];
      const f = $('#files-find'); f.value = 'see %s please'; f.dispatchEvent(new Event('input', {bubbles: true})); await wait(300);
      const b = () => $('#files-list [data-k="open:pasted"]');
      const label = [b().getAttribute('href'), b().querySelector('.tm').textContent];
      b().click(); await wait(200);
      const during = [b().querySelector('.tm').textContent, b().getAttribute('aria-busy')];
      b().click(); await wait(200);
      const asked = window.__go.slice();
      window.__let('maya@northwind.example'); await wait(500);
      done({folders, files, inside, where, back, label, during, asked, after: b().querySelector('.tm').textContent});
""" % link)
        self.assertEqual(got['folders'], 6)                                 # five accounts, and Not matched yet
        self.assertGreater(got['files'], 0)
        self.assertTrue(got['inside'])
        self.assertEqual(got['where'], '#artifacts/maya%40northwind.example')   # each folder has its own address
        self.assertEqual(got['back'], ['#artifacts', 6])
        self.assertEqual(got['label'][0], link)
        self.assertTrue(got['label'][1].startswith('Not matched yet · '), got['label'])
        self.assertEqual(got['during'], ['Opening as its owner…', 'true'])
        self.assertEqual(got['asked'], [{'url': link}])
        self.assertTrue(got['after'].startswith('Not matched yet · '), got['after'])

    def test_copy_link_copies_the_link_without_opening_anything(self):
        got = self.run_page(STAND_IN + """
      if (!navigator.clipboard) Object.defineProperty(navigator, 'clipboard', {value: {}});
      navigator.clipboard.writeText = (t) => { window.__copied = t; return Promise.resolve(); };
      await wait(1200);
      location.hash = '#artifacts'; await wait(400);
      $('#files-list .folder').click(); await wait(400);
      $('#files-list .tcopy').click(); await wait(300);
      done({copied: window.__copied || null, asked: window.__go.length, say: $('#toasts').textContent});
""")
        self.assertEqual(got['copied'], own(NORTHWIND)[0]['url'])
        self.assertEqual(got['asked'], 0)
        self.assertIn('Copied.', got['say'])


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class EnterOpens(KeyPage):
    def test_enter_on_a_focused_row_opens_it(self):
        self.open_page('?folder=' + NORTHWIND)
        self.js('(async () => { %s })()' % STAND_IN)
        self.js("""(async () => {
          for (let i = 0; i < 100 && !document.querySelector('#files-list a.tile'); i++) await new Promise((r) => setTimeout(r, 50));
          document.querySelector('#files-list a.tile').focus(); })()""")
        self.press('Enter', 'Enter', 13, '\r')
        got = self.js("""(async () => { await new Promise((r) => setTimeout(r, 300));
          const row = document.querySelector('#files-list a.tile');
          return {asked: window.__go.slice(), focused: document.activeElement === row, when: row.querySelector('.tm').textContent,
                  where: location.hash}; })()""")
        self.assertEqual(got['where'], '#artifacts/maya%40northwind.example')   # ?folder=EMAIL lands in its folder
        self.assertEqual(got['asked'], [{'url': own(NORTHWIND)[0]['url']}])
        self.assertTrue(got['focused'])                                     # the tile keeps the focus while it opens
        self.assertEqual(got['when'], 'Opening as %s…' % NORTHWIND)
        self.press('Enter', 'Enter', 13, '\r')                              # again, while it opens: nothing more
        self.assertEqual(self.js('(async () => { await new Promise((r) => setTimeout(r, 300)); return window.__go.length; })()'), 1)


# ---------- opening as the owner is always on ----------

class LinksHaveNoSwitch(unittest.TestCase):
    """The page: no switch for it; the Links group (with the Chrome profiles) holds its rows; the links line's
    one button."""

    def setUp(self):
        self.html = read('switchboard', 'web', 'index.html')
        self.links = self.html[self.html.index("{id: 'links', title: 'Links'"):self.html.index("{id: 'keys', title: 'Keys'")]

    def test_no_switch_and_no_item_of_its_own(self):
        for gone in ("sw('links'", 'function linksRow(', "zone || 'more:links'", 'more:links', 'BODY.links', 'Open links as their owner'):
            self.assertNotIn(gone, self.html)
        groups = re.findall(r"^  \{id: '(\w+)', title: '([^']+)'", self.html, re.M)
        self.assertEqual(groups, [('switching', 'Switching'), ('working', 'While Claude works'), ('code', 'In Claude Code'),
                                  ('links', 'Links'), ('keys', 'Keys'), ('accounts', 'Accounts')])
        self.assertNotIn('sw(', self.links)                                   # no switch in Links at all

    def test_its_rows_are_under_chrome_setup_with_the_same_function(self):
        rows = self.links
        self.assertEqual(re.findall(r"\{id: '(\w+)', title: '", rows), ['links', 'profiles', 'home', 'bookmark'])
        self.assertIn('data-change="home" data-zone="set"', rows)             # where other links open: POST {home}
        self.assertIn('Open as owner</a>', rows)                              # the bookmark
        self.assertIn("'/go?k='", rows)
        self.assertIn('data-k="handlers"', rows)
        self.assertIn('switchboard update --link-app', rows)                 # without the link app: how to add it
        self.assertIn("hint(esc(ck.line), ' data-k=\"check-line\"')", rows)  # Check's answer, under the profiles

    def test_the_banner_has_one_button_that_asks_macos(self):
        self.assertIn('data-do="links" data-on="1" data-zone="links" data-k="links:default">Make Switchboard Links the default</button>',
                      self.html)
        self.assertIn("if (!d.demo && d.asked !== false) say(z, on ? 'macOS is asking. Choose “Use Switchboard Links”.'",
                      self.html)


HOLDER = {'holder': {'path': '/Applications/Google Chrome.app', 'id': 'com.google.Chrome', 'name': 'Google Chrome',
                     'kind': 'browser'}, 'loop': False}


def stand_in_link_app(test):
    """linkapp's status and turn stood in for: test.state is what --status says, test.turned what macOS was asked
    (True: make it the default, False: give the seat back). Without the link app, turn says what it needs, as the real
    one does."""
    test.turned, test.state = [], {'installed': True, 'on': False, 'browser': 'Safari'}

    def turn(on):
        if not test.state['installed']:
            raise core.SwitchError('Needs the link app: switchboard update --link-app (it needs Apple’s command line tools)')
        test.turned.append(on)
    for target, name, value in ((linkapp, 'status', lambda fresh=False: dict(test.state)), (linkapp, 'turn', turn),
                                (linkapp, 'seat', lambda fresh=False, look=True: HOLDER)):
        p = mock.patch.object(target, name, value)
        p.start()
        test.addCleanup(p.stop)


class TheBanner(unittest.TestCase):
    """Never set up, and reset (macOS or another app took the seat): both offered, in the page's words for whoever
    holds the seat; an off said on purpose, never."""

    def setUp(self):
        for k in (server.LINKS_WANTED, 'links_by_itself'):
            core.set_setting(k, None)
        p = mock.patch.object(linkapp, 'seat', lambda fresh=False, look=True: HOLDER)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(core.set_setting, server.LINKS_WANTED, None)

    def banner(self, wanted, installed=True, on=False):
        core.set_setting(server.LINKS_WANTED, wanted)
        return server._links_banner({'installed': installed, 'on': on, 'browser': 'Google Chrome'})

    def test_never_set_up(self):
        b = self.banner(None)
        self.assertEqual(b, {'text': 'Google Chrome is the default browser, so links you click in other apps open there, '
                                     'not as their owner.',
                             'action': 'make_default'})

    def test_reset(self):
        b = self.banner(True)
        self.assertEqual(b['action'], 'make_default')
        self.assertEqual(b['text'], 'Google Chrome is the default browser, so links you click in other apps open there, '
                                    'not as their owner.')

    def test_turned_off_on_purpose_or_nothing_to_offer(self):
        self.assertIsNone(self.banner(False))                               # `switchboard links off`
        self.assertIsNone(self.banner(None, on=True))                       # links open as their owner
        self.assertIsNone(self.banner(None, installed=False))               # no link app: Chrome setup says how


class LinksRoute(unittest.TestCase):
    """POST /api/links: macOS is asked only when the seat would change hands."""

    def setUp(self):
        stand_in_link_app(self)
        core.set_setting(server.LINKS_WANTED, None)
        self.addCleanup(core.set_setting, server.LINKS_WANTED, None)

    def test_on_asks_macos_once_and_off_gives_the_seat_back_only_when_held(self):
        self.assertEqual(server.act('/api/links', {'on': True}), {'asked': True})
        self.assertEqual((self.turned, core.settings()[server.LINKS_WANTED]), ([True], True))
        self.state['on'] = True
        self.assertEqual(server.act('/api/links', {'on': True}), {'asked': False})    # the default already
        self.assertEqual(server.act('/api/links', {'on': False}), {'asked': True})    # macOS asks who gets it back
        self.assertEqual((self.turned, core.settings()[server.LINKS_WANTED]), ([True, False], False))
        self.state['on'] = False
        self.assertEqual(server.act('/api/links', {'on': False}), {'asked': False})   # nothing to give back
        self.assertEqual(self.turned, [True, False])
        self.assertIs(core.settings()[server.LINKS_WANTED], False)

    def test_without_the_link_app_on_says_what_it_needs_and_off_is_only_noted(self):
        self.state.update(installed=False)
        with self.assertRaises(core.SwitchError) as said:
            server.act('/api/links', {'on': True})
        self.assertIn('switchboard update --link-app', str(said.exception))
        self.assertIsNone(core.settings().get(server.LINKS_WANTED))         # nothing changed
        self.assertEqual(server.act('/api/links', {'on': False}), {'asked': False})
        self.assertIs(core.settings()[server.LINKS_WANTED], False)

    def test_home_sets_where_other_links_open(self):
        with mock.patch.object(linkapp, 'set_home_profile', lambda p: self.turned.append(('home', p))):
            self.assertIsNone(server.act('/api/links', {'home': 'Profile 1'}))
        self.assertEqual(self.turned, [('home', 'Profile 1')])


class OffStaysOff(unittest.TestCase):
    """While macOS asks who gets the seat back after `switchboard links off`, the link app still holds it: the look
    that sees it on must not take that for a yes."""

    def test_an_off_said_on_purpose_is_not_undone_by_the_seat_still_held(self):
        value = {'installed': True, 'on': True, 'browser': 'Switchboard Links'}
        for target, name, v in ((linkapp, 'status', lambda fresh=False: dict(value)),
                                (linkapp, 'handlers_note', lambda found=None, **k: None)):
            p = mock.patch.object(target, name, v)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.dict(server.LINKS, {'loop': True, 'at': None, 'running': False, 'value': None, 'banner': None})
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(core.set_setting, server.LINKS_WANTED, None)

        def look():
            self.assertTrue(server.refresh_link_status(force=True))
            for _ in range(500):
                if not server.LINKS['running']:
                    return
                time.sleep(0.01)
            self.fail('the look did not end')
        core.set_setting(server.LINKS_WANTED, None)
        look()
        self.assertIs(core.settings()[server.LINKS_WANTED], True)           # seen on, never set: the person's yes
        core.set_setting(server.LINKS_WANTED, False)                         # `switchboard links off`
        look()
        self.assertIs(core.settings()[server.LINKS_WANTED], False)


class LinksCommand(test_keep.Sandbox):
    """`switchboard links [on|off]`: the way out, and back in, from the terminal (the page not running here: the same
    change is made by the command itself)."""

    def setUp(self):
        super().setUp()
        stand_in_link_app(self)

    def test_on_and_off(self):
        self.assertEqual(self.run_cli('links'), (0, 'Links open as their owner: off (Google Chrome is the default '
                                                    'browser). Turn it on with: switchboard links on\n'))
        self.assertEqual(self.run_cli('links', 'on'), (0, term.LINKS_ASKING[True] + '\n'))
        self.assertEqual(self.turned, [True])
        self.state['on'] = True
        self.assertEqual(self.run_cli('links'), (0, 'Links open as their owner: on. Switchboard Links is the default '
                                                    'browser.\n'))
        self.assertEqual(self.run_cli('links', 'off'), (0, term.LINKS_ASKING[False] + '\n'))
        self.assertEqual(self.turned, [True, False])
        self.assertIs(core.settings()[server.LINKS_WANTED], False)
        self.state['on'] = False                                              # the person chose their browser
        self.assertEqual(self.run_cli('links', 'off'), (0, 'Links open as their owner: off, as you set it. Turn it on '
                                                           'with: switchboard links on\n'))
        self.assertEqual(self.turned, [True, False])                          # nothing asked again
        self.assertEqual(term.LINKS_ASKING[False], 'macOS is asking. Choose your usual browser. Switchboard won’t '
                                                   'offer it again; switchboard links on turns it back on.')

    def test_without_the_link_app(self):
        self.state.update(installed=False)
        self.assertEqual(self.run_cli('links'), (0, 'Links open as their owner: off. Add the link app with: switchboard '
                                                    "update --link-app (it needs Apple's command line tools).\n"))
        with self.assertRaises(core.SwitchError):
            self.run_cli('links', 'on')
        self.assertEqual(self.turned, [])

    def test_words_it_does_not_know_and_its_help(self):
        self.assertEqual(self.run_cli('links', 'maybe'), (2, 'switchboard links [on|off]\n'))
        code, out = self.run_cli('links', '--help')
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith('switchboard links [on|off]  whether links you click anywhere open as their owner'),
                        out)
        self.assertEqual(self.turned, [])

    def test_the_help_and_the_reference_list_it(self):
        self.assertIn('switchboard links [on|off]', cli.__doc__)
        ref = read('docs', 'commands.md')
        for row in ('| `switchboard links` |', '| `switchboard links on` |', '| `switchboard links off` |'):
            self.assertIn(row, ref)
        self.assertIn('| `--no-link-app` |', ref)
        self.assertNotIn('| `--link-app` |', ref)                            # the default now: nothing to ask for


class FirstRunAsksOnce(test_keep.Sandbox):
    """At a terminal the first run asks macOS once to make the link app the default browser (macOS asks the person)."""

    def setUp(self):
        super().setUp()
        stand_in_link_app(self)

    def test_the_first_run_asks_and_says_so_before_its_last_line(self):
        self.job()
        self.signed_in(test_keep.A)
        code, out = self.run_cli('setup', tty=test_keep.FakeTty([]))
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertEqual(lines[-2], term.LINKS_FIRST_RUN)
        self.assertTrue(lines[-1].startswith('Sign in to your other accounts'), lines)
        self.assertEqual(self.turned, [True])
        self.assertTrue(core.settings().get('links_by_itself'))
        code, out = self.run_cli('setup', tty=test_keep.FakeTty([]))         # once: never again by itself
        self.assertNotIn(term.LINKS_FIRST_RUN, out)
        self.assertEqual(self.turned, [True])

    def test_never_without_a_terminal_and_never_over_a_choice(self):
        self.job()
        self.signed_in(test_keep.A)
        code, out = self.run_cli('setup')                                    # no terminal: nothing is asked
        self.assertNotIn(term.LINKS_FIRST_RUN, out)
        core.set_setting(server.LINKS_WANTED, False)                         # turned off on purpose
        self.assertFalse(keep.links_by_itself())
        core.set_setting(server.LINKS_WANTED, None)
        self.state.update(on=True)                                           # the default already
        self.assertFalse(keep.links_by_itself())
        self.state.update(on=False, installed=False)                         # no link app
        self.assertFalse(keep.links_by_itself())
        self.assertEqual(self.turned, [])


class NewInstalls(unittest.TestCase):
    """install.sh builds the link app unless --no-link-app (kept for updates, and read back for Homebrew)."""

    def setUp(self):
        self.sh = read('install.sh')

    def test_the_link_app_is_the_default_and_no_link_app_is_kept(self):
        self.assertIn('\nLINKAPP=1 ', self.sh)
        self.assertIn('    --no-link-app) LINKAPP=0; shift ;;\n', self.sh)
        self.assertIn('[ "$LINKAPP" = 1 ] || CHOICES="$CHOICES --no-link-app"\n', self.sh)
        self.assertIn('case "$saved" in *" --no-link-app "*) LINKAPP=0 ;; esac', self.sh)       # Homebrew's runs
        self.assertIn('[--no-link-app]', self.sh.split('-h|--help)')[1].split('\n')[0])
        self.assertNotIn('LINKAPP_SAID', self.sh)

    def choices(self, text):
        path = core.data_path(os.path.join('app', 'choices'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            f.write(text + '\n')
        self.addCleanup(shutil.rmtree, os.path.dirname(path), True)

    def test_update_keeps_the_choice_and_link_app_adds_it_back(self):
        with mock.patch.object(linkapp, 'installed', lambda: False), mock.patch.object(cli, 'job_installed', lambda: True), \
                mock.patch.object(cli, 'python_record', lambda: {}):
            self.choices('--no-link-app')
            flags = cli.install_choices()
            self.assertIn('--no-link-app', flags)
            self.assertNotIn('--link-app', flags)
            back = cli.update_flags({'link': True, 'menu': None, 'port': None})     # switchboard update --link-app
            self.assertIn('--link-app', back)
            self.assertNotIn('--no-link-app', back)
            self.choices('--no-menu-app')
            self.assertFalse({'--link-app', '--no-link-app'} & set(cli.install_choices()))   # the default: built


# ---------- the logo: a dark tile, a white S from a white end to a blue dot ----------

S_PATH = 'M16.3 7H10.05a2.5 2.5 0 0 0 0 5h4.25a2.5 2.5 0 0 1 0 5H7.55'
OLD_MARKS = ('M9 10h4c5 0 5 12 10 12',           # the page's tab icon before: cords between three dots
             'dot(352, 652',                      # the link app's icon before: the same cords
             'M15.5 8.6c-.4-1.5-1.8-2.6-3.6-2.6')  # an earlier, typographic S


def product_files():
    """What the product draws or ships, and the docs: the program, the page, the link app, the installers."""
    out = ['install.sh', 'uninstall.sh', 'README.md', os.path.join('router', 'icon.swift'),
           os.path.join('router', 'build.sh'), os.path.join('router', 'SwitchboardLinks.swift')]
    for top in ('switchboard', 'docs'):
        for folder, dirs, names in os.walk(os.path.join(ROOT, top)):
            dirs[:] = [d for d in dirs if d != '__pycache__']
            out += [os.path.relpath(os.path.join(folder, n), ROOT) for n in names
                    if n.endswith(('.py', '.html', '.svg', '.md', '.swift', '.sh'))]
    return out


class TheLogo(unittest.TestCase):
    def setUp(self):
        self.html = read('switchboard', 'web', 'index.html')

    def test_the_tab_icon_is_the_s_tile_on_the_page_and_the_go_page(self):
        icon = re.search(r'<link rel="icon" href="([^"]+)">', self.html).group(1)
        self.assertIn(S_PATH, icon)
        self.assertIn("r='2.3' fill='%233b9eff'", icon)                      # the blue dot
        self.assertIn("rx='5.5' fill='%231d1d1f'", icon)                     # the dark tile
        self.assertIn('prefers-color-scheme:dark)%7B.r%7Bstroke:%233a3a3c', icon)   # its edge on dark tab strips
        self.assertIn('<link rel="icon" href="%s">' % icon, read('switchboard', 'web', 'go.html'))

    def test_the_header_lockup(self):
        brand = re.search(r"const BRAND = ('.*?');\n", self.html, re.S).group(1)
        for part in ('class="mark"', 'rx="5.5"', 'd="%s"' % S_PATH, 'cx="7.55" cy="17" r="2.2"', 'cx="16.3" cy="7" r="2.3"',
                     'aria-hidden="true"', '</svg>Switchboard</span>'):
            self.assertIn(part, brand)
        self.assertIn("$('brand').innerHTML = BRAND;", self.html)            # the sidebar's header, every view
        self.assertIn('<a class="brand-link" id="brand" href="#accounts" aria-label="Switchboard, accounts"></a>', self.html)
        self.assertIn("'<p class=\"hello\" id=\"hello\">' + esc(welcome())", self.html)   # the first run's welcome
        self.assertIn('--mark-rim: transparent;', self.html)
        self.assertEqual(self.html.count('--mark-rim: #3a3a3c;'), 2)          # both dark blocks
        for rule in ('.mark-tile { fill: #1d1d1f; }', '.mark-dot { fill: #3b9eff; }',
                     '.mark-rim { fill: none; stroke: var(--mark-rim); stroke-width: 1px; vector-effect: non-scaling-stroke; }'):
            self.assertIn(rule, self.html)
        self.assertRegex(self.html, r'\.brand \{[^}]*font-weight: 600;')       # Geist SemiBold, carried in the page

    def test_the_readme_logo_for_light_and_dark(self):
        light, dark = read('docs', 'logo.svg'), read('docs', 'logo-dark.svg')
        for svg in (light, dark):
            self.assertIn('aria-label="Switchboard"', svg)
            self.assertIn('d="%s"' % S_PATH, svg)
            self.assertIn('<circle cx="16.3" cy="7" r="2.3" fill="#3b9eff"/>', svg)
            self.assertNotRegex(svg, r'<text|font-family|href=')                # outlines: nothing to load on GitHub
        self.assertIn('<path fill="#1d1d1f" d="M', light)
        self.assertIn('<path fill="#f5f5f7" d="M', dark)
        self.assertNotIn('#3a3a3c', light)
        self.assertIn('stroke="#3a3a3c"', dark)                               # the tile's edge on dark grounds
        self.assertEqual(light.replace('#1d1d1f" d', '#f5f5f7" d').split('<path fill=')[1],
                         dark.split('<path fill=')[1])                         # the same word
        self.assertTrue(read('README.md').startswith('# <picture><source media="(prefers-color-scheme: dark)" '
                                                     'srcset="docs/logo-dark.svg"><img src="docs/logo.svg" alt="Switchboard"'))

    def test_the_link_app_icon_is_the_s_tile(self):
        swift = read('router', 'icon.swift')
        for part in ('p(16.3, 7)', 'p(10.05, 7)', 'p(7.55, 9.5)', 'p(16.8, 14.5)', 'p(7.55, 17)', 'radius: 2.5 * k',
                     'setLineWidth(2.2 * k)', 'dot(7.55, 17, 2.2', 'dot(16.3, 7, 2.3, rgb(0x3b, 0x9e, 0xff))',
                     'rgb(0x1d, 0x1d, 0x1f)', 'CGColorSpace.sRGB'):
            self.assertIn(part, swift)
        with open(os.path.join(ROOT, 'router', 'SwitchboardLinks.icns'), 'rb') as f:
            icns = f.read()
        self.assertEqual(icns[:4], b'icns')
        self.assertEqual(int.from_bytes(icns[4:8], 'big'), len(icns))
        kinds, at = [], 8
        while at < len(icns):
            kinds.append(icns[at:at + 4])
            at += int.from_bytes(icns[at + 4:at + 8], 'big')
        for kind in (b'ic04', b'ic11', b'ic05', b'ic12', b'ic07', b'ic13', b'ic08', b'ic14', b'ic09', b'ic10'):   # 16 to 1024 px
            self.assertIn(kind, kinds)
        build = read('router', 'build.sh')
        self.assertIn('if [ "${1:-}" = "--icon" ]; then', build)
        self.assertIn('swiftc -module-cache-path "$WORK/cache" -o "$WORK/icon" icon.swift', build)
        self.assertIn('iconutil -c icns', build)
        self.assertIn('cp SwitchboardLinks.icns "$NEW/Contents/Resources/SwitchboardLinks.icns"', build)

    def test_the_cords_and_the_typographic_s_are_drawn_nowhere(self):
        for name in product_files():
            text = read(name)
            for old in OLD_MARKS:
                with self.subTest(file=name, mark=old):
                    self.assertNotIn(old, text)


class TheMenuBarsApp(unittest.TestCase):
    """Switchboard.app (the menu bar's app in Applications) carries the mark too. The menu bar item itself draws the
    account's two bars, a template image in one colour: no logo there, so nothing to change."""

    def test_its_icon_is_the_mark(self):
        from switchboard import menubar_wrap
        tmp = tempfile.mkdtemp(prefix='switchboard-page-')
        self.addCleanup(shutil.rmtree, tmp, True)
        exe = os.path.join(tmp, 'python', 'bin', 'python3.12')
        os.makedirs(os.path.dirname(exe))
        with open(exe, 'w') as f:
            f.write('#!/bin/sh\necho made-up\n')
        os.chmod(exe, 0o755)
        os.makedirs(os.path.join(tmp, 'python', 'lib'))
        env = os.path.join(tmp, 'env')
        os.makedirs(os.path.join(env, 'lib', 'python3.12', 'site-packages'))
        where = os.path.join(tmp, 'apps')
        menubar_wrap.make(where, executable=exe, prefix=env)
        app = os.path.join(where, 'Switchboard.app')
        self.assertEqual(menubar_wrap.info(app)['CFBundleIconFile'], 'Switchboard')
        with open(os.path.join(app, 'Contents', 'Resources', 'Switchboard.icns'), 'rb') as a, \
                open(os.path.join(ROOT, 'router', 'SwitchboardLinks.icns'), 'rb') as b:
            self.assertEqual(a.read(), b.read())
        menu = read('switchboard', 'menubar.py')
        self.assertIn('image.setTemplate_(not red)', menu)                    # the item: bars, one colour


LOCKUP = r"""
      const lockup = (d, theme) => {
        d.documentElement.dataset.theme = theme;
        const side = d.getElementById('side'), brand = side.querySelector('#brand .brand'), mark = brand.querySelector('svg.mark');
        const r = (el) => el.getBoundingClientRect(), cs = (el) => getComputedStyle(el);
        const out = {first: side.firstElementChild.contains(brand), brand: brand.textContent,
                     left: Math.round(r(brand).left), top: Math.round(r(brand).top),
                     mark: [r(mark).width, r(mark).height],
                     font: [cs(brand).fontFamily.split(',')[0].replace(/"/g, ''), cs(brand).fontWeight],
                     tile: cs(mark.querySelector('.mark-tile')).fill, rim: cs(mark.querySelector('.mark-rim')).stroke,
                     dot: cs(mark.querySelector('.mark-dot')).fill, wide: d.documentElement.scrollWidth,
                     width: d.defaultView.innerWidth};
        delete d.documentElement.dataset.theme;
        return out;
      };
"""


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class TheLockupInThePage(FramedPage):
    def check(self, got):
        self.assertTrue(got['first'])                                       # the sidebar's header (the top bar on a phone)
        self.assertLessEqual(got['left'], 20)                               # at the top left of the page
        self.assertLessEqual(got['top'], 30)
        self.assertEqual(got['brand'], 'Switchboard')
        self.assertEqual(got['mark'], [26, 26])                             # a 26 px tile
        self.assertEqual(got['font'], ['Geist', '600'])
        self.assertEqual(got['tile'], 'rgb(29, 29, 31)')
        self.assertEqual(got['dot'], 'rgb(59, 158, 255)')
        self.assertLessEqual(got['wide'], got['width'])

    def test_at_the_top_left_light_and_dark_wide_and_on_a_phone(self):
        got = self.in_frames(LOCKUP + """
    done(frames.map((f) => ({light: lockup(doc(f), 'light'), dark: lockup(doc(f), 'dark')})));
""", widths=(1440, 390))
        self.assertEqual([g['light']['width'] for g in got], [1440, 390])
        for g in got:
            with self.subTest(width=g['light']['width']):
                self.check(g['light'])
                self.check(g['dark'])
                self.assertEqual(g['light']['rim'], 'rgba(0, 0, 0, 0)')    # no edge on a light ground
                self.assertEqual(g['dark']['rim'], 'rgb(58, 58, 60)')       # #3a3a3c on a dark one

    def test_the_first_run_has_it_too(self):
        demo._ready()
        for e, a in demo.STATE['accounts'].items():
            a['connected'] = e == 'maya@northwind.example'
        demo.STATE['accounts']['maya@northwind.example']['unmanaged'] = True
        demo.FACTS.update(setup_hidden=False)
        got = self.run_page("""
      await wait(800);
      done({brand: $('#brand .brand').textContent, hello: $('#hello').textContent, fr: !!$('.fr')});
""")
        self.assertEqual(got, {'brand': 'Switchboard', 'hello': 'Welcome, Maya', 'fr': True})


class NumbersInThePageText(unittest.TestCase):
    def test_the_fields_look_and_keep_their_rules(self):
        html = read('switchboard', 'web', 'index.html')
        rule = re.search(r'\n\.num input \{([^}]*)\}', html).group(1)
        for part in ('border: 1px solid var(--input)', 'background: var(--bg)', 'height: 28px', 'text-align: right'):
            self.assertIn(part, rule)
        self.assertIn('.num::after { content: "%";', html)                   # the % inside the box
        self.assertIn('inputmode="decimal"', html)
        self.assertIn("said('Pick a number from 50 to 99.9.')", html)        # the message that was there, beside the fields
        self.assertIn('--input: #dcdce0;', html)
        self.assertEqual(html.count('--input: #34343a;'), 2)                   # both dark blocks


if __name__ == '__main__':
    unittest.main()
