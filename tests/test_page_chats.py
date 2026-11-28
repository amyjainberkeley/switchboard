import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Every account's chats on the page. The route behind "Reopen at home" (POST /api/resume), and, in
# headless Chrome (a throwaway profile each run, no window) on the demo's made-up accounts: the sidebar's Chats, the
# view grouped by the account each chat started under, each row's states and its second line, the action and its
# menu, the chats count under each account's email, ⌘K, the chord, 390 px, dark, contrast, no monospace and no request that
# leaves the Mac.
import json
import os
import re
import stat
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)

from switchboard import core, demo, more, resume, server
from switchboard.core import SwitchError
from test_app import CHROME, ChromePage
from test_page_links_and_logo import FramedPage

ID = '4a7e0c11-2b3d-4e5f-8a9b-0c1d2e3f4a5b'           # a made-up chat id


# ---------- the route: POST /api/resume opens Terminal on `switchboard resume <id>` ----------

class TheResumeRoute(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = False
        self.command = core.data_path(os.path.join('bin', 'switchboard'))
        os.makedirs(os.path.dirname(self.command), exist_ok=True)
        with open(self.command, 'w') as f:
            f.write('#!/bin/sh\nexit 0\n')
        os.chmod(self.command, 0o700)
        self.addCleanup(os.unlink, self.command)
        folder = os.path.join(core.projects(), '-Users-me-work-billing')
        os.makedirs(folder, exist_ok=True)
        self.chat = os.path.join(folder, ID + '.jsonl')
        with open(self.chat, 'w') as f:
            f.write(json.dumps({'type': 'user', 'sessionId': ID, 'cwd': '/Users/me/work/billing',
                                'timestamp': '2026-11-18T01:00:00.000Z', 'message': {'role': 'user', 'content': 'hi'}}) + '\n')
        old = time.time() - 600                    # last written ten minutes ago: no window is writing it
        os.utime(self.chat, (old, old))
        self.addCleanup(os.unlink, self.chat)
        self.opened = []
        p = mock.patch.object(more.subprocess, 'Popen', lambda cmd, **kw: self.opened.append(cmd))
        p.start()
        self.addCleanup(p.stop)

    def refused(self, body):
        with self.assertRaises(SwitchError) as cm:
            server.act('/api/resume', body)
        return str(cm.exception)

    def test_it_opens_terminal_on_switchboard_resume_with_the_chat(self):
        self.assertEqual(server.act('/api/resume', {'chat': ID.upper()}), {})
        path = core.data_path(os.path.join('terminal', 'chat-%s.command' % ID))
        self.assertEqual(self.opened, [more.OPEN + [path]])                    # the real function, as it is
        with open(path) as f:
            self.assertIn("exec '%s' resume '%s'\n" % (self.command, ID), f.read())
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o700)
        os.unlink(path)

    def test_only_a_whole_chat_id(self):
        for bad in ({}, {'chat': ''}, {'chat': ID[:8]}, {'chat': ID + ';rm -rf ~'}, {'chat': '../' + ID}, {'chat': 7}):
            with self.subTest(body=bad):
                self.assertEqual(self.refused(bad), 'That is not a chat id.')
        self.assertEqual(self.opened, [])

    def test_a_chat_a_window_is_writing_is_refused_here_in_resumes_words(self):
        os.utime(self.chat, None)                  # written just now: open in a window
        said = self.refused({'chat': ID})
        self.assertEqual(said, 'That chat is open and working in another window. Two windows writing one chat can '
                               'lose messages. Close it there first.')
        self.assertTrue(said.startswith(resume.TWO_WRITERS.split(' Close')[0]))
        self.assertEqual(self.opened, [])                                      # no window opens only to say so

    def test_a_chat_that_is_not_on_this_mac(self):
        other = ID[:-1] + ('0' if ID[-1] != '0' else '1')
        self.assertIn('No chat on this Mac has an id that starts with', self.refused({'chat': other}))
        self.assertEqual(self.opened, [])

    def test_without_the_command_it_says_so(self):
        os.unlink(self.command)
        self.addCleanup(lambda: open(self.command, 'w').close())
        self.assertEqual(self.refused({'chat': ID}), 'The switchboard command is missing. Run the installer again.')

    def test_the_demo_does_nothing_and_says_so(self):
        core.DEMO = True
        demo.STATE.clear()
        demo.FACTS.clear()
        self.addCleanup(demo.STATE.clear)
        self.addCleanup(demo.FACTS.clear)
        self.assertEqual(server.act('/api/resume', {'chat': ID}), {'demo': True})
        self.assertEqual(self.opened, [])

    def test_over_http_only_from_this_macs_own_page(self):
        core.DEMO = True                           # nothing opens, whatever gets through
        demo.STATE.clear()
        demo.FACTS.clear()
        self.addCleanup(demo.STATE.clear)
        self.addCleanup(demo.FACTS.clear)
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        port = httpd.server_address[1]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def post(headers):
            req = urllib.request.Request('http://127.0.0.1:%d/api/resume' % port, method='POST',
                                         data=json.dumps({'chat': ID}).encode(),
                                         headers=dict({'Content-Type': 'application/json'}, **headers))
            try:
                with opener.open(req, timeout=10) as r:
                    return r.status, json.load(r)
            except urllib.error.HTTPError as e:
                return e.code, json.load(e)
        with mock.patch.dict(os.environ, {'SWITCHBOARD_PORT': str(port)}):
            self.assertEqual(post({})[0], 403)                                   # no page header: refused
            self.assertEqual(post({'X-Switchboard': '1', 'Origin': 'https://example.invalid'})[0], 403)
            self.assertEqual(post({'X-Switchboard': '1'}), (200, {'demo': True, 'ok': True}))
        self.assertEqual(self.opened, [])



# ---------- the page, in headless Chrome, on the demo's made-up accounts and chats ----------

PAGE = os.path.join(ROOT, 'switchboard', 'web', 'index.html')
NORTHWIND, MAIL, FERNHILL, HARBOR, IVY = ('maya@northwind.example', 'maya.reyes@mail.example', 'maya@fernhill.example',
                                          'm.reyes@harbor.example', 'maya@ivy.example')
CHAT = dict((title or folder, cid) for cid, title, folder, *_ in demo.DEMO_CHATS)
BILLING, NUMBERS, VENDOR, TRIP = CHAT['Billing export fix'], CHAT['Quarterly numbers'], CHAT['Vendor comparison'], CHAT['Trip budget']
BUSY = 'This chat is open and working in another window. Two windows writing one chat can lose messages.'
ROWS = r"""
      const row = (id) => document.querySelector('#ch-list [data-chat="' + id + '"]');
      const text = (el, q) => { const x = el && el.querySelector(q); return x ? x.textContent : null; };
      const look = (id) => { const r = row(id); if (!r) return null; const a = r.querySelector('.reopen');
        return {title: text(r, '.cr-t'), where: text(r, '.cr-w'), holds: text(r, '.cr-h'), when: text(r, '.cr-when'), reading: r.classList.contains('reading'),
                act: a ? [a.textContent, a.getAttribute('aria-disabled'), a.getAttribute('title')] : null, menu: !!r.querySelector('[data-do="chatMenu"]')}; };
"""


def view_now():
    """The demo's chats view, as GET /api/chats answers it now."""
    demo._ready()
    return demo.chats()


def recent(v):
    return [c for c in v['chats'] if v['now'] - c['last'] < 86400]


def stand_in(test, v):
    """GET /api/chats answers `v` (a view in chats.view's shape) for the rest of the test."""
    p = mock.patch.object(server, 'chats_view', lambda: v)
    p.start()
    test.addCleanup(p.stop)


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class TheSidebarHasChats(ChromePage):
    def test_between_accounts_and_artifacts_with_its_count_its_address_and_its_chord(self):
        v = view_now()
        got = self.run_page("""
      await wait(900);
      const nav = all('#nav a').map((a) => [a.getAttribute('href'), a.querySelector('span').textContent, (a.querySelector('.count') || {}).textContent || null]);   // no count, no span
      $('#nav a[href="#chats"]').click(); await wait(500);
      const shown = all('main .sec').filter((s) => !s.hidden).map((s) => s.id);
      const current = $('#nav [aria-current]').textContent;
      location.hash = '#accounts'; await wait(300);
      const key = (k) => document.dispatchEvent(new KeyboardEvent('keydown', {key: k, bubbles: true}));
      key('g'); key('c'); await wait(400);
      const chord = location.hash;
      key('?'); await wait(200);
      const sheet = all('#kbd dt').map((d, i) => [d.textContent, all('#kbd dd')[i].textContent]);
      done({nav, shown, current, chord, sheet, title: $('#nav a[href="#chats"]').getAttribute('title')});
""")
        self.assertEqual([x[:2] for x in got['nav']], [['#accounts', 'Accounts'], ['#chats', 'Chats'], ['#artifacts', 'Artifacts'],
                                                       ['#activity', 'Activity'], ['#settings', 'Settings']])
        self.assertEqual(got['nav'][1][2], str(len(recent(v))))                # chats written in the last 24 hours
        self.assertEqual(len(recent(v)), len(v['chats']) - 1)                   # (the demo's one older chat is not counted)
        self.assertEqual(got['shown'], ['sec-chats'])
        self.assertTrue(got['current'].startswith('Chats'))
        self.assertEqual(got['chord'], '#chats')                                # G then C, in the family of the others
        self.assertIn(['G then C', 'Chats'], got['sheet'])
        self.assertEqual(got['title'], 'Chats · G C')


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class GroupedByHome(ChromePage):
    def test_one_group_per_home_the_account_claude_code_is_on_first_the_rest_last(self):
        v = view_now()
        got = self.run_page(ROWS + """
      await wait(900);
      location.hash = '#chats'; await wait(600);
      const groups = all('#ch-list .cg').map((g) => ({head: g.querySelector('.cg-head h2').textContent, inuse: text(g, '.cg-head .inuse'),
        rows: [...g.querySelectorAll('.cr')].map((r) => r.dataset.chat), earlier: text(g, '.cr-more'), quiet: g.classList.contains('quiet')}));
      $('#ch-list .cr-more').click(); await wait(300);
      const opened = all('#ch-list .cg')[1].querySelectorAll('.cr').length;
      done({groups, opened, lede: $('#ch-lede').textContent, tools: $('#ch-tools .tok > span:last-child').textContent, copy: $('#ch-tools .tok').dataset.copy});
""")
        homes = {}
        for c in v['chats']:
            home = (c['started'] or {}).get('email') or 'Other accounts'
            if v['now'] - c['last'] < 86400:
                homes.setdefault(home, []).append(c['id'])
        self.assertEqual([g['head'] for g in got['groups']], [NORTHWIND, MAIL, FERNHILL, HARBOR, IVY, 'Other accounts'])
        self.assertEqual([g['inuse'] for g in got['groups']], ['In use', None, None, None, None, None])   # Claude Code is on the first
        for g in got['groups']:
            with self.subTest(group=g['head']):
                self.assertEqual(g['rows'], homes[g['head']])                   # each chat under the account it started under
        self.assertEqual([g['quiet'] for g in got['groups']][-1], True)
        self.assertEqual(got['groups'][1]['earlier'], 'Earlier · 1')            # older than a day: folded, one line per group
        self.assertNotIn(TRIP, got['groups'][1]['rows'])
        self.assertEqual([g['earlier'] for g in got['groups'] if g['earlier']], ['Earlier · 1'])
        self.assertEqual(got['opened'], 2)                                      # unfolded: Trip budget too
        self.assertEqual(got['lede'], 'Each chat under its home, the account it started in.')
        self.assertEqual((got['tools'], got['copy']), ('switchboard chats', 'switchboard chats'))   # the command token, as on Artifacts

    def test_each_row_says_only_what_it_needs(self):
        got = self.run_page(ROWS + """
      await wait(900);
      location.hash = '#chats'; await wait(600);
      const ids = %s, out = {};
      for (const k in ids) out[k] = look(ids[k]);
      done(out);
""" % json.dumps(CHAT))
        on_work = 'on %s since ' % NORTHWIND
        billing, numbers, vendor = got['Billing export fix'], got['Quarterly numbers'], got['Vendor comparison']
        self.assertTrue(billing['where'].startswith(on_work), billing)          # away from home: where it is, and since when
        self.assertEqual(billing['holds'], 'Google Drive answers on %s · 1 artifact is on %s' % (MAIL, FERNHILL))
        self.assertEqual(billing['when'], 'Working')                            # the In use mark, nothing new
        self.assertEqual(billing['act'], ['Reopen at home', 'true', BUSY])      # working: the action waits, its reason on hover
        self.assertTrue(numbers['where'].startswith(on_work))
        self.assertEqual(numbers['holds'], 'Notion answers on %s, and 1 artifact is there' % FERNHILL)
        self.assertEqual(numbers['when'], '48 min ago')
        self.assertEqual(numbers['act'][:2], ['Reopen at home', None])
        self.assertTrue(numbers['act'][2].startswith('Opens it in Terminal as %s, the account it started under. Claude Code '
                                                     'stays on %s everywhere else.' % (FERNHILL, NORTHWIND)), numbers['act'])
        self.assertIsNone(vendor['holds'])                                      # holds nothing out of reach: no second line
        self.assertEqual(vendor['act'][:2], ['Reopen at home', None])
        for title in ('Onboarding copy', 'Brand guide, second pass', 'Fable eval run'):     # at home: no where, no action
            with self.subTest(chat=title):
                self.assertEqual((got[title]['where'], got[title]['holds'], got[title]['act']), (None, None, None))
                self.assertTrue(got[title]['menu'])
        self.assertEqual(got['Onboarding copy']['when'], '3 hours ago')
        self.assertEqual(got['Fable eval run']['when'], 'Working')
        self.assertEqual(got['Notes from the old laptop']['where'], 'an account Switchboard doesn’t keep')
        self.assertIsNone(got['Notes from the old laptop']['act'])
        self.assertEqual((got['scratch']['title'], got['scratch']['where']), ('scratch', 'account not known'))   # untitled: its folder
        self.assertIsNone(got['Trip budget'])                                    # folded under Earlier
        for title, r in got.items():
            if r and not r['reading']:
                self.assertFalse(re.search(r'\b(moved|session|\d+ times)\b', r['where'] or ''), r)


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class TheActionAndItsMenu(ChromePage):
    def test_reopen_at_home_sends_the_chat_and_waits_while_it_works(self):
        got = self.run_page(ROWS + """
      await wait(900);
      location.hash = '#chats'; await wait(600);
      row('%s').querySelector('.reopen').click(); await wait(600);
      const idle = [sent.filter((x) => x[0] === '/api/resume').map((x) => x[1]), $('#toasts').textContent];
      row('%s').querySelector('.reopen').click(); await wait(400);
      done({idle, busy: [sent.filter((x) => x[0] === '/api/resume').length, $('#toasts').textContent]});
""" % (NUMBERS, BILLING))
        self.assertEqual(got['idle'][0], [{'chat': NUMBERS}])                   # POST /api/resume with the whole id
        self.assertIn('Demo: nothing changed on this Mac.', got['idle'][1])    # the demo does nothing, and says so
        self.assertEqual(got['busy'][0], 1)                                     # working: nothing more is sent
        self.assertIn(BUSY, got['busy'][1])                                     # and why, for a press without a hover

    def test_the_menu_holds_the_action_and_copy_command(self):
        got = self.run_page(ROWS + """
      await wait(900);
      location.hash = '#chats'; await wait(600);
      if (!navigator.clipboard) Object.defineProperty(navigator, 'clipboard', {value: {}});
      navigator.clipboard.writeText = (t) => { window.__copied = t; return Promise.resolve(); };
      const items = () => all('#menu button').map((b) => [b.children[0].textContent, b.children[1].textContent, b.getAttribute('aria-disabled')]);
      const open = async (id) => { row(id).querySelector('[data-do="chatMenu"]').click(); await wait(250); return {label: text($('#menu'), '.m-label'), items: items()}; };
      const away = await open('%s');
      $('#menu [data-do="chatCopy"]').click(); await wait(400);
      const copied = [$('#toasts').textContent, window.__copied];
      const busy = await open('%s');
      document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})); await wait(150);
      const closed = [$('#menu').hidden, document.activeElement.dataset.do];
      const home = await open('%s');
      done({away, copied, busy, closed, home, font: getComputedStyle($('#menu button span:last-child')).fontFamily});
""" % (NUMBERS, BILLING, CHAT['Onboarding copy']))
        self.assertEqual(got['away']['label'], 'Quarterly numbers')
        self.assertEqual(got['away']['items'], [['Reopen at home', FERNHILL, None], ['Copy command', 'switchboard resume d0c5f00d', None]])
        self.assertEqual(got['copied'][1], 'switchboard resume ' + NUMBERS)     # the whole id: it never matches two chats
        self.assertIn('Copied', got['copied'][0])
        self.assertIn('switchboard resume ' + NUMBERS, got['copied'][0])
        self.assertEqual(got['busy']['items'][0], ['Reopen at home', MAIL, 'true'])
        self.assertEqual(got['closed'], [True, 'chatMenu'])                     # Esc closes it, back on its button
        self.assertEqual(got['home']['items'], [['Copy command', 'switchboard resume 77b0e4d1', None]])   # at home: only the command
        self.assertNotIn('Mono', got['font'])


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class TheChatsUnderTheEmail(ChromePage):
    def test_the_plan_line_is_the_plan_alone_with_no_chat_count(self):
        # (it said "Team · 2 chats" under the email, and a click opened Chats at that account: the left side of a row
        # is now the email and the plan under it, and nothing else. The Chats section keeps its own numbers.)
        view_now()
        got = self.run_page("""
      await wait(900);
      done(all('#acc .arow').map((r) => [r.dataset.email, r.querySelector('.sub').textContent, r.querySelector('.sub').title,
        r.querySelectorAll('.sub .chn, .sub a').length]));
""")
        fern = dict((e, (line, tip, n)) for e, line, tip, n in got)[FERNHILL]
        self.assertEqual((fern[0], fern[2]), ('Team', 0))                      # the plan, alone
        self.assertNotIn('chat', fern[1])                                      # nor on hover
        self.assertEqual([e for e, line, _, n in got if n or 'chat' in line], [])

    def test_no_count_before_the_first_look_has_read_every_chat(self):
        v = dict(view_now(), state='reading', reading={'done': False, 'read': 4, 'of': 9})
        stand_in(self, v)
        got = self.run_page("""
      await wait(900);
      done({nav: ($('#nav a[href="#chats"] .count') || {textContent: ''}).textContent,   // no count, no span
            sub: all('#acc .sub .chn').length});
""")
        self.assertEqual(got, {'nav': '', 'sub': 0})                            # never a count part of the way through


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class ReadingEmptyAndNotKnown(ChromePage):
    def chats(self, v):
        stand_in(self, v)
        return self.run_page(ROWS + """
      await wait(900);
      location.hash = '#chats'; await wait(600);
      done({body: $('#ch-list').textContent, groups: all('#ch-list .cg h2').map((h) => h.textContent), ids: all('#ch-list [data-chat]').map((r) => r.dataset.chat),
            rows: Object.fromEntries(all('#ch-list [data-chat]').map((r) => [r.dataset.chat, look(r.dataset.chat)]))});
""")

    def test_before_the_first_look(self):
        got = self.chats({'state': 'reading', 'accounts': [], 'chats': []})       # what GET /api/chats says before it
        self.assertEqual(got['body'], 'Reading your chats…')

    def test_while_reading_a_chat_not_read_through_shows_its_name_and_nothing_else(self):
        v = view_now()
        v.update(state='reading', reading={'done': False, 'read': 3, 'of': 9})
        for c in v['chats']:
            if c['id'] in (BILLING, CHAT['scratch']):
                c['read'] = False
        got = self.chats(v)
        self.assertTrue(got['body'].startswith('Reading your chats… 3 of 9'), got['body'])
        self.assertEqual(got['rows'][BILLING], {'title': 'Billing export fix', 'where': None, 'holds': None, 'when': 'Reading…',
                                                'reading': True, 'act': None, 'menu': False})   # never "moved 9 times" half read
        self.assertNotIn(CHAT['scratch'], got['ids'])                         # its home not read yet: not under "not known"
        self.assertEqual(got['groups'][-1], 'Other accounts')                  # the old laptop's chat, read through, still there

    def test_no_chats(self):
        got = self.chats(dict(view_now(), chats=[]))
        self.assertEqual(got['body'], 'No chats in the last 7 days')

    def test_cant_read_them(self):
        def broken():
            raise OSError('disk')
        p = mock.patch.object(server, 'chats_view', broken)
        p.start()
        self.addCleanup(p.stop)
        got = self.run_page("""
      await wait(900);
      location.hash = '#chats'; await wait(600);
      done($('#ch-list').textContent);
""")
        self.assertEqual(got, 'Can’t read the chats')

    def test_an_old_look_says_its_age_and_two_accounts_of_one_organization_are_not_split(self):
        v = view_now()
        v['at'] = v['now'] - 1800                                               # the look stalled (a busy Mac, a full disk)
        shared = {'email': None, 'kept': True, 'org': 'abcd1234'}
        for c in v['chats']:
            if c['id'] == VENDOR:
                c['started'] = c['on'] = shared
        stand_in(self, v)
        got = self.run_page(ROWS + """
      await wait(900);
      location.hash = '#chats'; await wait(600);
      done({lede: $('#ch-lede').textContent, vendor: look('%s'), last: [...all('#ch-list .cg')].pop().querySelector('h2').textContent});
""" % VENDOR)
        self.assertEqual(got['lede'], 'Each chat under its home, the account it started in. As of 30 min ago.')
        self.assertEqual(got['vendor']['where'], 'one of the accounts that share an organization')
        self.assertIsNone(got['vendor']['act'])                                 # its home is not one account: nothing to reopen as
        self.assertEqual(got['last'], 'Other accounts')

    def test_a_connector_not_known_yet_is_left_out(self):
        v = view_now()
        for c in v['chats']:
            for h in c['holds']['connectors']:
                h['here'] = 'not known'
        got = self.chats(v)
        self.assertEqual(got['rows'][BILLING]['holds'], '1 artifact is on %s' % FERNHILL)
        self.assertEqual(got['rows'][NUMBERS]['holds'], '1 artifact is on %s' % FERNHILL)


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class CmdKAndAddresses(ChromePage):
    def test_cmd_k_finds_a_chat_and_goes_to_it(self):
        got = self.run_page("""
      await wait(900);
      const key = (k, more, t) => (t || document).dispatchEvent(new KeyboardEvent('keydown', Object.assign({key: k, bubbles: true}, more || {})));
      key('k', {metaKey: true}); await wait(200);
      const first = all('#pal-list .pal-g').map((g) => g.textContent);
      const q = $('#pal-q'), type = async (v) => { q.value = v; q.dispatchEvent(new Event('input', {bubbles: true})); await wait(150); };
      await type('quarterly');
      const found = all('#pal-list .pal-it').map((b) => [b.closest('.pal-list') && b.previousElementSibling ? '' : '', b.querySelector('.lb').textContent, (b.querySelector('.hn') || {}).textContent]);
      const groups = all('#pal-list .pal-g').map((g) => g.textContent);
      key('Enter', {}, q); await wait(900);
      const r = $('#ch-list [data-chat="%s"]');
      await type('');
      key('k', {metaKey: true}); await wait(200);
      await type('chats');
      const go = all('#pal-list .pal-it .lb').map((x) => x.textContent);
      done({first, found: found.map((x) => x.slice(1)), groups, hash: location.hash, lit: r.classList.contains('flash'),
            mid: Math.round(r.getBoundingClientRect().top), go});
""" % NUMBERS)
        self.assertEqual(got['first'], ['Go to', 'Accounts', 'Artifacts'])        # its first look
        self.assertEqual(got['groups'], ['Chats'])
        self.assertEqual(got['found'], [['Quarterly numbers', FERNHILL]])
        self.assertEqual(got['hash'], '#chats/' + NUMBERS)
        self.assertTrue(got['lit'])
        self.assertIn('Chats', got['go'])
        self.assertIn('switchboard chats', got['go'])                          # and the command, under Copy a command

    def test_an_address_to_an_older_chat_opens_its_fold(self):
        got = self.run_page("""
      await wait(900);
      location.hash = '#chats/%s'; await wait(900);
      const r = $('#ch-list [data-chat="%s"]');
      done({there: !!r, lit: r && r.classList.contains('flash'), open: $('#ch-list .cr-more').getAttribute('aria-expanded')});
""" % (TRIP, TRIP))
        self.assertEqual(got, {'there': True, 'lit': True, 'open': 'true'})


# ---------- 390 px, both themes, contrast, no monospace, no script errors, nothing that leaves the Mac ----------

LOOK = r"""
    const parse = (c) => { const m = c && c.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(/[\s,\/]+/).filter(Boolean).map(parseFloat); return {r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1}; };
    const lin = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
    const lum = (c) => 0.2126 * lin(c.r) + 0.7152 * lin(c.g) + 0.0722 * lin(c.b);
    const mix = (t, b) => ({r: t.r * t.a + b.r * (1 - t.a), g: t.g * t.a + b.g * (1 - t.a), b: t.b * t.a + b.b * (1 - t.a), a: 1});
    const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
    const ground = (d, el) => { const st = []; for (let e = el; e; e = e.parentElement) { const c = parse(d.defaultView.getComputedStyle(e).backgroundColor); if (c && c.a > 0) { st.push(c); if (c.a >= 1) break; } }
      let bg = parse(d.defaultView.getComputedStyle(d.body).backgroundColor); if (st.length && st[st.length - 1].a >= 1) bg = st.pop(); while (st.length) bg = mix(st.pop(), bg); return bg; };
    const opacity = (d, el) => { let o = 1; for (let e = el; e; e = e.parentElement) o *= parseFloat(d.defaultView.getComputedStyle(e).opacity); return o; };
    const MINE = '#sec-chats, .chn, #menu.ch-menu';
    const check = async (d, w, what, out) => {
      if (!d.getElementById('no-ease')) {                // colours ease in over .12 s, and Chrome's virtual clock never lets
        const st = d.createElement('style');             // them arrive: measured at their end, not part of the way
        st.id = 'no-ease'; st.textContent = '*, *::before, *::after { transition: none !important; animation: none !important; }';
        d.head.appendChild(st);
      }
      for (const theme of ['light', 'dark']) {
        d.documentElement.dataset.theme = theme;
        await wait(100);
        if (d.documentElement.scrollWidth > w) out.found.push(theme + ', ' + what + ': page ' + d.documentElement.scrollWidth);
        for (const el of d.querySelectorAll('main *, #side *, #menu *')) {
          const cs = d.defaultView.getComputedStyle(el);
          if (cs.display === 'none' || el.closest('[hidden]') || el.closest('.chips')) continue;
          const r = el.getClientRects().length ? el.getBoundingClientRect() : null;
          if (r && r.width && r.right > w + 0.5 && cs.position !== 'fixed') out.found.push(theme + ', ' + what + ': ' + (el.className || el.tagName) + ' ends at ' + Math.round(r.right));
          if (!el.closest(MINE)) continue;
          if (/mono/i.test(cs.fontFamily)) out.mono.push(theme + ', ' + what + ': ' + el.textContent.trim().slice(0, 30));
          if (!Array.from(el.childNodes).some((t) => t.nodeType === 3 && t.textContent.trim()) || el.closest('.sr') || el.closest('[aria-disabled="true"]')) continue;
          const fg = parse(cs.color), bg = ground(d, el), op = opacity(d, el);
          if (!fg || op < 0.05) continue;
          const rt = ratio(mix({r: fg.r, g: fg.g, b: fg.b, a: fg.a * op}, bg), bg);
          out.text = Math.min(out.text, rt);
          if (rt < 4.5) out.found.push(theme + ', ' + what + ': "' + el.textContent.trim().slice(0, 30) + '" ' + rt.toFixed(2));
        }
        for (const i of d.querySelectorAll('#sec-chats .inuse i')) {
          if (!i.getClientRects().length) continue;
          const rt = ratio(parse(d.defaultView.getComputedStyle(i).backgroundColor), ground(d, i.parentElement));
          out.marks = Math.min(out.marks, rt);
        }
      }
      delete d.documentElement.dataset.theme;
      out.views.push(what);
    };
"""


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class PhoneDarkAndContrast(FramedPage):
    def test_1600_to_360_light_and_dark_nothing_cut_no_monospace_and_contrast(self):
        got = self.in_frames(LOOK + """
    const out = {found: [], mono: [], views: [], text: 99, marks: 99, sizes: {}};
    for (const f of frames) {
      const d = doc(f), win = f.contentWindow, w = win.innerWidth;
      const go = async (hash, ms) => { win.location.hash = hash; await wait(ms); };
      await go('#chats', 900);
      await check(d, w, w + ' Chats', out);
      d.querySelector('#ch-list .cr-more').click(); await wait(300);
      d.querySelector('#ch-list [data-do="chatMenu"]').click(); await wait(300);
      await check(d, w, w + ' Chats, folds open and a menu', out);
      d.body.click(); await wait(200);
      await go('#accounts', 700);
      await check(d, w, w + ' Accounts', out);
      for (const r of d.querySelectorAll('#acc .arow')) {      // no email cut short, no cell spilling into the next
        const em = r.querySelector('.em');
        if (em.scrollWidth > em.clientWidth) out.found.push(w + ' Accounts: ' + em.textContent + ' is cut');
        for (const c of r.querySelectorAll(':scope > .c')) {
          const cut = d.defaultView.getComputedStyle(c).overflowX !== 'visible';   // Plan ends in "…" by design, with its tooltip
          if (c.getClientRects().length && !cut && c.scrollWidth > c.clientWidth + 1) out.found.push(w + ' Accounts: ' + c.className + ' spills, ' + em.textContent);
        }
      }
      for (const r of d.querySelectorAll('#acc .arow')) {      // an account's limits are one under another at every width
        if (!r.querySelector('.c-five')) continue;            // (no 5-hour number to show: the weekly line alone)
        const five = r.querySelector('.c-five').getBoundingClientRect(), week = r.querySelector('.c-week').getBoundingClientRect();
        if (week.top < five.bottom - 0.5 || Math.abs(week.left - five.left) > 1) out.found.push(w + ' Accounts: weekly is not under 5-hour, ' + r.dataset.email);
      }
      out.sizes[w] = [...d.querySelectorAll('#ch-list .cr')].length;
    }
    done(out);
""", widths=(360, 390, 640, 900, 1180, 1399, 1400, 1440, 1600), budget=60000)
        self.assertEqual(got['found'], [], '\n'.join(got['found']))
        self.assertEqual(got['mono'], [])
        self.assertEqual(len(got['views']), 27)                                 # 9 widths, 1600 down to 360
        self.assertGreaterEqual(got['text'], 4.5)                               # text at least 4.5:1, light and dark
        self.assertGreaterEqual(got['marks'], 3.0)                              # the blue dots at least 3:1


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class NoErrorsAndNothingLeavesTheMac(ChromePage):
    def test_every_view_draws_with_no_script_error_and_asks_only_this_mac(self):
        got = self.run_page("""
      const errors = [], log = console.error;
      console.error = function () { errors.push([].map.call(arguments, String).join(' ')); return log.apply(console, arguments); };
      window.addEventListener('error', (e) => errors.push(String(e.message)));
      await wait(600);
      for (const h of ['#chats', '#accounts', '#chats/maya%%40fernhill.example', '#artifacts', '#activity', '#settings', '#chats/%s']) { location.hash = h; await wait(500); }
      $('#ch-list .cr-more').click(); await wait(200);
      const asked = performance.getEntriesByType('resource').map((e) => e.name).filter((n) => !n.startsWith(location.origin + '/') && !n.startsWith('data:'));
      done({errors, asked, broken: /Something went wrong/.test($('main').textContent), chats: performance.getEntriesByType('resource').filter((e) => e.name.endsWith('/api/chats')).length});
""" % NUMBERS)
        self.assertEqual(got['errors'], [])
        self.assertEqual(got['asked'], [])
        self.assertFalse(got['broken'])
        self.assertGreaterEqual(got['chats'], 1)


class TheWordsAndTheText(unittest.TestCase):
    def setUp(self):
        with open(PAGE, encoding='utf-8') as f:
            self.html = f.read()

    def test_the_busy_reason_is_resumes_own(self):
        self.assertIn("const CHAT_BUSY = '%s';" % BUSY, self.html)
        self.assertEqual(BUSY.replace('This chat', 'That chat'), resume.TWO_WRITERS.split(' Close')[0])

    def test_a_chat_is_a_chat_and_nothing_names_a_slot(self):
        block = self.html[self.html.index('// ---------- Chats:'):self.html.index('// ---------- Settings:')]
        self.assertNotRegex(block, r"'[^']*\bsession\b[^']*'")              # a chat is a "chat" in every word a person reads
        self.assertNotIn('.slot', block)


if __name__ == '__main__':
    unittest.main()
