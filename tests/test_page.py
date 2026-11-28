import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Tests for the page itself (switchboard/web/index.html).
#
# They read the file as text: no browser, no network. What the page draws is checked in headless Chrome by
# tests/test_app.py (TheFinalPage and others), tests/test_app_memory_and_keys.py (MemoryAndKeysInThePage), tests/test_app_setup_and_more.py and
# tests/test_page_layout.py (the sidebar, the views and their addresses).
import os
import re
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
PAGE = os.path.join(ROOT, 'switchboard', 'web', 'index.html')
SERVER = os.path.join(ROOT, 'switchboard', 'server.py')
LICENSE = os.path.join(ROOT, 'switchboard', 'web', 'Geist-OFL.txt')


def read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def actions(html):
    start = html.index('const actions = {')
    return html[start:html.index('\n};', start)]


class NothingLeavesThisMac(unittest.TestCase):
    """The page asks only its own server. The only outside addresses in it are claude.ai links it hands
    to the server to open in a browser profile when you click. Its typefaces are carried in the page; the only picture
    it shows is an artifact's preview, from its own server (GET /api/preview/<id>)."""

    def setUp(self):
        self.html = read(PAGE)

    def test_no_scripts_styles_fonts_or_media_from_anywhere_else(self):
        h = self.html
        self.assertNotRegex(h, r'<script[^>]*\bsrc\s*=')
        self.assertEqual(re.findall(r'<link\b[^>]*>', h), re.findall(r'<link rel="icon" href="data:[^>]*>', h))
        self.assertNotRegex(h, r'@import')
        self.assertNotRegex(h, r'url\(\s*(?![\'"]?(?:data:|#))')          # data, or a gradient defined in the page
        self.assertNotRegex(h, r'<(iframe|frame|video|audio|source|embed|object|base|form[^>]*action)\b')
        imgs = re.findall(r'<img\b[^>]*>', h)
        self.assertTrue(imgs)
        for img in imgs:                                                    # a preview, from this server only
            self.assertIn('src="/api/preview/\' + encodeURIComponent(x.id) + \'"', img)
        self.assertNotRegex(h, r'\b(XMLHttpRequest|WebSocket|EventSource|sendBeacon|importScripts|navigator\.serviceWorker)\b')
        self.assertNotRegex(h, r'(?:src|href|action)\s*=\s*["\']//')

    def test_the_typefaces_are_in_the_page_with_their_license_beside_them(self):
        # Geist only: no monospace anywhere (keys, commands, the config view are the page's sans)
        faces = re.findall(r'@font-face\s*\{[^}]*\}', self.html)
        self.assertEqual(len(faces), 1)
        self.assertIn('font-family: "Geist"', faces[0])
        self.assertRegex(faces[0], r'src: url\(data:font/woff2;base64,[A-Za-z0-9+/=]{20000,}\) format\("woff2"\)')
        self.assertIn('--font: "Geist", -apple-system', self.html)
        for gone in ('Geist Mono', '--mono', 'monospace'):
            self.assertNotIn(gone, self.html)
        license_text = read(LICENSE)
        self.assertTrue(license_text.startswith('Copyright 2024 The Geist Project Authors'))
        self.assertIn('SIL OPEN FONT LICENSE Version 1.1', license_text)
        self.assertIn('SIL Open Font License 1.1, in Geist-OFL.txt beside this file', self.html)

    def test_every_full_address_is_a_claude_ai_link_opened_on_a_click(self):
        for line in self.html.splitlines():
            for url in re.findall(r'https?://[^\s\'"<>)]+', line):
                if url.startswith('http://www.w3.org/2000/svg'):
                    continue                    # the SVG namespace of the tab icon, never fetched
                # the page's own address, said in a comment or where the page moves itself there (its key is kept
                # for one address): it opens nothing else
                if url == 'http://127.0.0.1:':
                    self.assertTrue(line.lstrip().startswith('//') or
                                    "location.replace('http://127.0.0.1:' + location.port + " in line, line)
                    continue
                self.assertTrue(url.startswith('https://claude.ai/'), url)
                self.assertIn("post('/api/open'", line, 'a claude.ai link must only be handed to /api/open: ' + url)

    def test_every_request_goes_to_this_server(self):
        calls = re.findall(r'\bfetch\(\s*([^,)]+)', self.html)
        self.assertTrue(calls)
        for arg in calls:
            self.assertTrue(arg.startswith("'/api/") or arg.strip() in ('path', 'url'), arg)
        for helper in ('post', 'getJSON'):
            for path in re.findall(r'\b%s\(\s*\'([^\']*)\'' % helper, self.html):
                self.assertTrue(path.startswith('/api/'), path)

    def test_one_page_for_everyone_and_nothing_to_talk_to_but_its_server(self):
        # The menu bar is its own program now, reading GET /api/state: the page has no panel mode (?panel=1) and no
        # bridge to an app around it.
        for gone in ('panel', 'postMessage', 'messageHandlers', 'webkit.', '__sbPanel', 'openPage'):
            self.assertNotIn(gone, self.html)


class EveryActionHasAControl(unittest.TestCase):
    # Each action a person can take, by a short name (the key), and the page's action that does it (the value).
    # 'all' (Show all pages) is opening an account's folder: it lists every artifact of that account.
    OLD = {'refresh': 'refresh', 'use': 'use', 'open': 'open', 'all': 'openFolder', 'signin': 'site', 'relogin': 'relogin',
           'cancel': 'cancel', 'rename': 'rename', 'add': 'add', 'closeAdd': 'closeAdd',
           'linksOn': 'links', 'linksOff': 'links', 'auto': 'auto', 'check': 'check',
           'addCurrent': 'addCurrent'}

    def setUp(self):
        self.html = read(PAGE)
        self.actions = actions(self.html)

    def test_each_action_is_handled_and_has_a_control(self):
        for old, new in self.OLD.items():
            with self.subTest(old=old, new=new):
                self.assertRegex(self.actions, r'\n  %s\(' % new)
                self.assertRegex(self.html, r'data-do="%s"|\b(?:q|goBtn|go|sw|setRow|b)\(\s*\'%s\'|\'relogin\' : \'use\'' % (new, new)
                                 if new in ('use', 'relogin') else r'data-do="%s"|\b(?:q|goBtn|go|sw|setRow|b)\(\s*\'%s\'' % (new, new))

    def test_an_account_row_has_the_button_that_uses_it(self):
        # Each row's Use (or Sign in again) button; the account in use has none, until its sign-in has ended
        body = self.html[self.html.index('function stateHTML('):self.html.index('function useCell(')]
        self.assertIn("if (isOn(a)) return '<span class=\"inuse\"><i></i>In use</span>' + (sig.ended ? ", body)
        self.assertIn("if (a.unmanaged) return '';", body)
        self.assertIn('data-do="relogin" data-email="\' + e + \'"', body)
        self.assertIn('data-do="use" data-email="\' + e + \'"', body)

    def test_the_page_asks_for_nothing_the_server_retired(self):
        # server.RETIRED: the requests the server answers with a plain refusal
        retired = re.findall(r"'(/api/[^']+)'", re.search(r'^RETIRED = \(([^)]*)\)', read(SERVER), re.M).group(1))
        self.assertTrue(retired)
        for path in retired:
            self.assertNotIn("'%s'" % path, self.html)

    def test_menus_fields_and_the_sign_in_form(self):
        for kind in ('profile', 'home', 'auto'):
            with self.subTest(kind=kind):
                self.assertIn('data-change="%s"' % kind, self.html)
                self.assertIn("kind === '%s'" % kind, self.html)
        self.assertIn('data-form="add"', self.html)
        self.assertIn("post('/api/connect', {email: f.elements.email.value.trim(), profile: f.elements.profile ? f.elements.profile.value : ''})", self.html)
        # Without Chrome (no browser profiles to pick) the form has no profile menu, and Sign in still sends.
        self.assertIn("(!(S.browser || {}).present ? '' : '<span class=\"select pick\"><select name=\"profile\"", self.html)
        self.assertIn('data-key="\' + key', self.html)          # both thresholds of automatic switching
        self.assertIn("'session_at'", self.html)
        self.assertIn("'weekly_at'", self.html)

    # Server actions only the terminal asks for: `switchboard setup` and `add` follow the sign-in they started
    # (firstrun.py).
    TERMINAL_ONLY = ('/api/connect-look',)

    def test_every_action_the_server_takes_is_asked_for_somewhere(self):
        paths = set(re.findall(r"path == '(/api/[a-z-]+)'", read(SERVER)))
        self.assertGreaterEqual(len(paths), 14)
        for path in sorted(paths - set(self.TERMINAL_ONLY)):
            with self.subTest(path=path):
                self.assertIn("post('%s'" % path, self.html)
        for path in self.TERMINAL_ONLY:
            self.assertIn(path, paths)
            self.assertNotIn(path, self.html)
        self.assertIn("api('/api/state', {signal: timed(8000)})", self.html)     # through api(), the one sender

    def test_changes_carry_the_x_switchboard_header(self):
        self.assertIn("headers: {'Content-Type': 'application/json', 'X-Switchboard': '1'}", self.html)
        # two places send a POST, each with the header: post() for every change, and the one trade of a ticket for
        # the page key (pagekey.py), made before the key is held
        self.assertEqual(self.html.count("method: 'POST'"), 2)
        self.assertEqual(self.html.count("method: 'POST', headers: {'Content-Type': 'application/json', "
                                         "'X-Switchboard': '1'}"), 2)
        self.assertIn("fetch('/api/ticket', {method: 'POST', headers:", self.html)


def style(html):
    """(the page's style block, its rules without the typefaces' data): a selector search over 55 KB of base64 takes
    minutes."""
    raw = max(re.findall(r'<style>(.*?)</style>', html, re.S), key=len)
    return raw, re.sub(r'@font-face\s*\{[^}]*\}', '', raw)


class TheLook(unittest.TestCase):
    """The tokens and rules (shadcn/ui's zinc, one accent), as written in the page."""

    def setUp(self):
        self.html = read(PAGE)
        self.raw, self.css = style(self.html)

    def test_no_alert_boxes_and_no_dialogs_in_dialogs(self):
        for word in ('alert(', 'confirm(', 'prompt(', '<dialog', 'showModal'):
            self.assertNotIn(word, self.html)

    def test_light_dark_and_calm_motion(self):
        self.assertIn('@media (prefers-color-scheme: dark)', self.html)
        self.assertIn('@media (prefers-reduced-motion: reduce)', self.html)
        self.assertIn('font-variant-numeric: tabular-nums', self.html)
        self.assertIn('-apple-system', self.html)

    def test_the_tokens_are_the_specs(self):
        # The design tokens: secondary text #6b6b74 in light (5.3:1 on white, 4.8:1 on chips)
        light = {'bg': '#ffffff', 'fg': '#09090b', 'side': '#fafafa', 'muted': '#f4f4f5', 'muted-fg': '#6b6b74', 'border': '#e4e4e7',
                 'input': '#dcdce0', 'ring': '#a1a1aa', 'hover': '#f4f4f5', 'row': '#fafafa', 'accent': '#006ad6', 'track': '#e4e4e7',
                 'fill': '#7c7c85', 'tick': '#52525b', 'red': '#dc2626', 'sw-on': '#18181b', 'sw-ring': '#85858d', 'knob-on': '#ffffff'}
        dark = {'bg': '#09090b', 'fg': '#fafafa', 'side': '#0c0c0e', 'muted': '#27272a', 'muted-fg': '#a1a1aa', 'border': '#27272a',
                'input': '#34343a', 'ring': '#71717a', 'hover': '#18181b', 'row': '#111113', 'accent': '#3b9eff', 'track': '#27272a',
                'fill': '#7c7c85', 'tick': '#d4d4d8', 'red': '#f87171', 'sw-on': '#fafafa', 'sw-ring': '#7c7c85', 'knob-on': '#09090b'}
        root = re.search(r':root \{(.*?)\}', self.html, re.S).group(1)
        night = re.search(r':root\[data-theme="dark"\] \{(.*?)\}', self.html, re.S).group(1)
        system = re.search(r':root:not\(\[data-theme="light"\]\) \{(.*?)\}', self.html, re.S).group(1)
        for name, want in light.items():
            self.assertIn('--%s: %s;' % (name, want), root)
        for name, want in dark.items():
            self.assertIn('--%s: %s;' % (name, want), night)
            self.assertIn('--%s: %s;' % (name, want), system)               # the Mac's dark mode, the same tokens

    def test_blue_means_in_use_and_red_only_a_line(self):
        # The accent is drawn by the account in use (its bar, In use and its dot, the header email), a tile opening, and
        # the one right action (the links line's button, a row's Set up, the first run's Add); red only by a % and its
        # bar at or over a switch line (a model's week, under the weekly bar, too). Focus rings are the ring grey.
        self.assertNotIn('var(--accent)', self.html.replace(self.raw, ''))     # every use of it is a style rule
        accent = sorted(set(u.strip() for u in re.findall(r'([^{}]+)\{[^{}]*var\(--accent\)', self.css)))
        # (and "now" on the week's chart, and its key in the legend)
        self.assertEqual(accent, ['.act', '.bar.on > i', '.btn.primary', '.chart svg .now', '.inuse', '.inuse i', '.k-now', '.tile.busy .tm', 'h1 .on'])
        self.assertNotIn('var(--red)', self.html.replace(self.raw, ''))
        self.assertEqual(sorted(set(u.strip() for u in re.findall(r'([^{}]+)\{[^{}]*var\(--red\)', self.css))),
                         ['.bar.over > i', '.chart svg .ev-lim', '.hist [data-kind="limit"] .w::before', '.k-lim', '.mnote',
                          '.note.over', '.over > .pct', '.tip .t-x .lim::before'])     # a limit reached, on the week and in History
        self.assertIn(':focus-visible { outline: 2px solid var(--ring);', self.html)
        # (The page draws shadcn's shadows: popovers, inputs, the focus ring, so 'box-shadow: 0' is not refused)
        for word in ('yellow', 'gradient', 'text-transform: uppercase', 'letter-spacing: .1'):
            self.assertNotIn(word, self.html)

    def test_another_assistant_is_drawn_once_as_a_row_and_settings_keeps_its_look(self):
        # The row after the accounts is the one drawing: no section of its own, no styles of its own, and no rule
        # that reaches Settings' profile cells (.pairs .pv) from outside their list.
        for gone in ('id="providers"', 'drawProviders', 'providersHTML', 'PV_WINDOWS', 'pv-'):
            self.assertNotIn(gone, self.html)
        rules = [u.strip() for u in re.findall(r'([^{}]+)\{[^{}]*\}', self.css)]
        reach = [r for r in rules for sel in r.split(',')
                 if re.search(r'\.pv\b', sel) and not sel.strip().startswith('.pairs .pv')]
        self.assertEqual(reach, [])
        self.assertIn('<div id="acc-mk"></div><div id="acc"></div><div id="acc-other"></div>', self.html)

    def test_the_sections_are_labelled(self):
        for needle in ('<h1 id="h-live">', 'role="table" aria-label="Accounts"', '<h1 id="h-set">Settings', '<h1 id="h-art">Artifacts</h1>',
                       '<h1 id="h-act">Activity</h1>', 'aria-label="Sections"', 'aria-label="Search past chats"',
                       '<h2 class="h2" id="h-history">History</h2>', '<aside class="side" id="side" aria-label="Switchboard">'):
            self.assertIn(needle, self.html)


if __name__ == '__main__':
    unittest.main()


class EveryActionHasOneHandler(unittest.TestCase):
    """Two handlers with one name: the later one silently replaces the first (e.g. a new `line` would replace
    opening an account's line)."""

    def test_no_action_name_twice(self):
        names = re.findall(r'\n  (\w+)\(', actions(read(PAGE)))
        self.assertEqual(sorted(n for n in set(names) if names.count(n) > 1), [])

    def test_every_data_do_has_a_handler(self):
        html = read(PAGE)
        names = set(re.findall(r'\n  (\w+)\(', actions(html)))
        used = set(re.findall(r'data-do="(\w+)"', html)) | \
            set(re.findall(r"(?<![.\w])(?:q|goBtn|go|sw|setRow|b|yes)\('(\w+)'", html)) | {'use', 'relogin'}
        self.assertEqual(sorted(used - names), [])
