import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The page answers only its own person (switchboard/pagekey.py): every POST, and every GET under /api/ except
# /api/state, must carry the page key in a header; the page's script trades a one-use ticket for it (never a
# cookie), and `open` is handed a private file that holds the ticket. Run with:
#   SWITCHBOARD_HOME="$(mktemp -d)/data" SWITCHBOARD_PORT=8061 python3 -m unittest discover -s tests -p 'test_pagekey.py'
# The server listens on 127.0.0.1 at $SWITCHBOARD_PORT (8062 when unset); every answer behind it is a stand-in.
import hashlib
import hmac
import http.client
import json
import os
import re
import stat
import subprocess
import sys
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)

from switchboard import core, memorypage, server  # noqa: E402
try:
    from switchboard import pagekey  # noqa: E402
except ImportError:                     # before the page key existed: each test below fails on its own line
    pagekey = None
import pagekey_help  # noqa: E402

REAL_PAGES = (7798, 7799, 7800)
DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))
SENTENCE = ('The page needs to be opened again: type `switchboard page` in a terminal, or use Open Switchboard in your '
            'menu bar.')
LOCKED = ('/api/memory?q=x', '/api/chats', '/api/artifacts', '/api/preview/abc', '/api/history', '/api/usage',
          '/api/keys', '/api/claudecode', '/api/models', '/api/more')


def need():
    if pagekey is None:
        raise AssertionError('switchboard/pagekey.py is missing')


class Page(unittest.TestCase):
    """The real handler on this run's own port, with stand-ins for what its routes read, its log lines kept."""

    def setUp(self):
        self.asked, self.lines, self.acted = [], [], []
        stand_ins = {'models_view': {'m': 1}, 'more_view': {'more': 1}, 'claudecode_view': {'c': 1},
                     'keys_view': {'k': 1}, 'history_view': {'h': 1}, 'artifacts_view': {'a': 1},
                     'chats_view': {'chats': 1}}
        for target, name, value in (
                [(server.SNAPSHOT, 'answer', lambda: self.asked.append('state') or (200, self.state())),
                 (server, 'usage_view', lambda fresh=False: self.asked.append('usage') or {'usage': 1}),
                 (memorypage, 'view', lambda q, fresh: self.asked.append('memory') or {'memory': 1}),
                 (server, 'act', lambda path, body: self.acted.append((path, body)) or {}),
                 (server, 'log', self.lines.append), (core, 'log', self.lines.append)]
                + [(server, n, (lambda v: lambda *a, **k: v)(v)) for n, v in stand_ins.items()]):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.port = int(os.environ.get('SWITCHBOARD_PORT') or 8062)
        self.assertNotIn(self.port, REAL_PAGES)
        env = mock.patch.dict(os.environ, {'SWITCHBOARD_PORT': str(self.port)})
        env.start()
        self.addCleanup(env.stop)
        httpd = server.PageServer(('127.0.0.1', self.port), server.Handler)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def state(self):
        return {'accounts': [], 'version': core.VERSION, 'demo': False, 'folder': core.folder_mark()}

    def ask(self, method, path, headers=None, body=None):
        """(status, headers, body bytes), never following a redirect."""
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        try:
            h = dict(headers or {})
            data = None
            if method == 'POST':
                data = json.dumps(body or {}).encode()
                h.setdefault('Content-Type', 'application/json')
            c.request(method, path, body=data, headers=h)
            r = c.getresponse()
            return r.status, r.msg, r.read()
        finally:
            c.close()

    def json_of(self, method, path, headers=None, body=None):
        code, _, data = self.ask(method, path, headers, body)
        return code, json.loads(data.decode('utf-8'))


# ---------- the key ----------

class TheKey(unittest.TestCase):
    def test_it_is_made_from_the_install_secret_and_is_never_that_secret(self):
        need()
        secret = core.key()
        want = hmac.new(secret.encode(), b'switchboard page key', hashlib.sha256).hexdigest()
        self.assertEqual(pagekey.key(), want)
        self.assertNotEqual(pagekey.key(), secret)
        self.assertNotIn(secret, pagekey.key())

    def test_every_address_a_browser_is_sent_to_is_the_literal_127_0_0_1(self):
        need()
        port = core.port()
        self.assertEqual(pagekey.page_url(), 'http://127.0.0.1:%d/' % port)
        self.assertEqual(pagekey.link('T'), 'http://127.0.0.1:%d/?t=T' % port)
        from switchboard import menubar
        self.assertEqual(menubar.add_url(), 'http://127.0.0.1:%d/#accounts/add' % port)
        with open(os.path.join(ROOT, 'switchboard', 'web', 'index.html'), encoding='utf-8') as f:
            page = f.read()
        self.assertNotIn("'//localhost:'", page)
        self.assertIn("location.protocol + '//127.0.0.1:' + location.port + '/go?k='", page)     # the bookmark
        self.assertIn("const AT_HOME = location.hostname === '127.0.0.1';", page)
        self.assertIn("location.replace('http://127.0.0.1:' + location.port + location.pathname + location.hash)", page)
        self.assertIn("AT_HOME && pageKey && !bare ? {'X-Switchboard-Key': pageKey} : {}", page)
        self.assertIn("api('/api/state', {signal: timed(8000)}).then(", page)      # its own state whole: with the key
        self.assertIn("encodeURIComponent(bookmarkKey || l.key || '')", page)       # the secret from its own route
        self.assertIn('function loadExtras(force) {\n  if (locked) return;', page)      # a locked page asks no more
        self.assertIn('function loadChats() {\n  if (chatsLoading || locked) return;', page)
        self.assertIn("keyReady = !(AT_HOME && ASK.get('t'))", page)
        # it locks itself only where a key exists: a server that traded the ticket for no key (the demo) is keyless
        self.assertIn("if (!s.demo && !pageKey && !keyless) { lock(); return; }", page)
        self.assertIn("} else if (d && typeof d === 'object' && !('key' in d)) {\n        keyless = true;", page)
        self.assertIn("const api = (path, o = {}, bare = false) => (keyReady ? send(path, o, bare) : "
                      "traded.then(() => send(path, o, bare)));", page)    # waits while a trade is under way

    def test_the_one_helper_puts_a_proof_where_the_page_asks_and_nothing_elsewhere(self):
        need()
        pagekey._USED.clear()
        k = pagekey.key()
        h = pagekey.headers('/api/switch')
        self.assertEqual(sorted(h), ['Content-Type', 'X-Switchboard', 'X-Switchboard-Key'])
        self.assertEqual((h['Content-Type'], h['X-Switchboard']), ('application/json', '1'))
        self.assertNotIn(k, h['X-Switchboard-Key'])                 # never the key itself
        self.assertTrue(pagekey.proved(h['X-Switchboard-Key'], 'POST', '/api/switch'))
        self.assertEqual(pagekey.headers('/api/state', 'GET'), {})
        self.assertEqual(pagekey.headers('/api/state?x=1', 'GET'), {})
        for path in ('/api/usage?fresh=1', '/api/some-new-thing'):
            got = pagekey.headers(path, 'GET')
            self.assertEqual(list(got), ['X-Switchboard-Key'])
            self.assertTrue(pagekey.proved(got['X-Switchboard-Key'], 'GET', path))


# ---------- proofs: what Switchboard's own programs send in place of the key ----------

class Proofs(unittest.TestCase):
    def setUp(self):
        need()
        pagekey._USED.clear()
        self.addCleanup(pagekey._USED.clear)

    def test_good_once_for_its_own_method_and_path_only(self):
        p = pagekey.proof('POST', '/api/switch')
        self.assertFalse(pagekey.proved(p, 'POST', '/api/remove'))
        self.assertFalse(pagekey.proved(p, 'GET', '/api/switch'))
        self.assertFalse(pagekey.proved(p, 'POST', '/api/switch?x=1'))
        self.assertFalse(pagekey.proved(p, 'POST', '/api/switch', b'{"email": "a@one.example"}'))     # another body
        self.assertTrue(pagekey.proved(p, 'POST', '/api/switch'))
        self.assertFalse(pagekey.proved(p, 'POST', '/api/switch'))           # taken
        q = pagekey.proof('POST', '/api/switch', b'{"a": 1}')
        self.assertFalse(pagekey.proved(q, 'POST', '/api/switch', b'{"a": 2}'))
        self.assertTrue(pagekey.proved(q, 'POST', '/api/switch', b'{"a": 1}'))

    def test_an_old_one_or_one_from_the_future_is_refused(self):
        self.assertFalse(pagekey.proved(pagekey.proof('POST', '/api/a', now=1000.0), 'POST', '/api/a', now=1061.0))
        self.assertFalse(pagekey.proved(pagekey.proof('POST', '/api/a', now=1061.0), 'POST', '/api/a', now=1000.0))
        self.assertTrue(pagekey.proved(pagekey.proof('POST', '/api/a', now=1000.0), 'POST', '/api/a', now=1059.0))

    def test_one_changed_character_or_another_key_is_refused(self):
        p = pagekey.proof('POST', '/api/a')
        for i, ch in enumerate(p):
            if ch != '.':
                self.assertFalse(pagekey.proved(p[:i] + ('0' if ch != '0' else '1') + p[i + 1:], 'POST', '/api/a'), i)
        with mock.patch.object(pagekey, 'key', lambda: 'another key'):
            other = pagekey.proof('POST', '/api/a')
        self.assertFalse(pagekey.proved(other, 'POST', '/api/a'))
        self.assertFalse(pagekey.proved(pagekey.key(), 'POST', '/api/a'))   # the key is not a proof
        self.assertTrue(pagekey.proved(p, 'POST', '/api/a'))


class ProofsAtThePage(Page):
    def test_the_page_takes_a_proof_once_for_its_own_route(self):
        need()
        pagekey._USED.clear()
        h = pagekey.headers('/api/refresh', body=b'{}')            # what ask() sends for no body
        self.assertEqual(self.json_of('POST', '/api/refresh', h), (200, {'ok': True}))
        self.assertEqual(self.json_of('POST', '/api/refresh', h), (401, {'error': SENTENCE}))      # replayed
        self.assertEqual(self.json_of('POST', '/api/refresh', pagekey.headers('/api/switch')),
                         (401, {'error': SENTENCE}))
        self.assertEqual(self.json_of('GET', '/api/usage', pagekey.headers('/api/usage', 'GET')), (200, {'usage': 1}))
        self.assertEqual(self.acted, [('/api/refresh', {})])

    def test_a_proof_with_another_body_is_refused(self):
        need()
        pagekey._USED.clear()
        h = pagekey.headers('/api/switch', body=json.dumps({'email': 'a@one.example'}).encode())
        self.assertEqual(self.json_of('POST', '/api/switch', h, {'email': 'b@two.example'}), (401, {'error': SENTENCE}))
        self.assertEqual(self.acted, [])
        h = pagekey.headers('/api/switch', body=json.dumps({'email': 'a@one.example'}).encode())
        self.assertEqual(self.json_of('POST', '/api/switch', h, {'email': 'a@one.example'}), (200, {'ok': True}))
        self.assertEqual(self.acted, [('/api/switch', {'email': 'a@one.example'})])

    def test_a_link_route_with_a_proof_gets_its_whole_answer(self):
        need()
        url = 'https://claude.ai/artifact/abcdefghijklmnop1234'
        p = mock.patch.object(server, 'act', lambda path, body: {'email': 'a@one.example', 'checked': True})
        p.start()
        self.addCleanup(p.stop)
        h = pagekey.headers('/api/go', body=json.dumps({'url': url}).encode())
        self.assertEqual(self.json_of('POST', '/api/go', h, {'url': url}),
                         (200, {'email': 'a@one.example', 'checked': True, 'ok': True}))


# ---------- tickets ----------

class Tickets(unittest.TestCase):
    def setUp(self):
        need()
        pagekey._SPENT.clear()
        self.addCleanup(pagekey._SPENT.clear)

    def test_good_once_and_refused_the_second_time(self):
        t = pagekey.ticket()
        self.assertTrue(pagekey.redeem(t))
        self.assertFalse(pagekey.redeem(t))

    def test_an_expired_one_is_refused(self):
        t = pagekey.ticket(now=1000.0)
        self.assertFalse(pagekey.redeem(t, now=1000.0 + pagekey.TICKET_FOR))
        self.assertFalse(pagekey.redeem(t, now=1000.0 + pagekey.TICKET_FOR + 3600))
        self.assertTrue(pagekey.redeem(pagekey.ticket(now=1000.0), now=1000.0 + pagekey.TICKET_FOR - 1))

    def test_one_changed_character_anywhere_is_refused(self):
        t = pagekey.ticket()
        for i, ch in enumerate(t):
            if ch == '.':
                continue
            other = '0' if ch != '0' else '1'
            self.assertFalse(pagekey.redeem(t[:i] + other + t[i + 1:]), i)
        self.assertTrue(pagekey.redeem(t))                      # untouched, it still works: nothing above spent it

    def test_one_signed_with_another_key_or_far_ahead_or_shaped_wrong_is_refused(self):
        t = pagekey.ticket()
        with mock.patch.object(pagekey, 'key', lambda: 'another key'):
            self.assertFalse(pagekey.redeem(t))
            bookmark = pagekey.ticket()
        self.assertFalse(pagekey.redeem(bookmark))
        self.assertFalse(pagekey.redeem(pagekey.ticket(ttl=pagekey.LINK_FOR + 60)))
        for bad in ('', None, 12, 'x', t + '.', '.' + t, t.replace('.', '', 1), t + 'a'):
            self.assertFalse(pagekey.redeem(bad), bad)

    def test_spent_ones_are_held_in_a_bounded_list_and_a_full_list_refuses(self):
        with mock.patch.object(pagekey, 'SPENT_MOST', 3):
            for _ in range(3):
                self.assertTrue(pagekey.redeem(pagekey.ticket(now=1000.0), now=1001.0))
            self.assertFalse(pagekey.redeem(pagekey.ticket(now=1000.0), now=1001.0))      # full of unexpired ones
            self.assertTrue(pagekey.redeem(pagekey.ticket(now=2000.0), now=2001.0))       # they expired: room again
            self.assertEqual(len(pagekey._SPENT), 1)

    def test_many_at_once_take_one_ticket_once(self):
        t = pagekey.ticket()
        got = []
        threads = [threading.Thread(target=lambda: got.append(pagekey.redeem(t))) for _ in range(16)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(sorted(got), [False] * 15 + [True])


# ---------- the server: who must carry it ----------

class DefaultClosed(Page):
    def test_state_stays_open_while_every_other_api_route_needs_the_key(self):
        self.assertEqual(self.json_of('GET', '/api/state')[0], 200)
        for path in LOCKED:
            self.assertEqual(self.json_of('GET', path), (401, {'error': SENTENCE}), path)
        self.assertEqual(self.asked, ['state'])                 # nothing behind a locked route was read

    def test_the_open_state_never_hands_out_the_secret_the_key_is_made_from(self):
        need()
        secret = core.key()
        body = server.to_json({'accounts': [], 'version': core.VERSION, 'demo': False, 'first_name': 'Sam',
                               'links': {'on': True, 'home': '', 'key': secret},
                               'provider_rows': [{'provider': 'codex', 'chat': {'name': 'A private plan', 'mark': 'm1'}},
                                                 {'provider': 'other', 'chat': None}]})
        p = mock.patch.object(server.SNAPSHOT, 'answer', lambda: (200, body))
        p.start()
        self.addCleanup(p.stop)
        code, got = self.json_of('GET', '/api/state')
        self.assertEqual((code, got['links']), (200, {'on': True, 'home': '', 'key': ''}))
        self.assertIsNone(got['first_name'])                                    # the Mac account's first name
        self.assertEqual(got['provider_rows'], [{'provider': 'codex', 'chat': {'name': '', 'mark': 'm1'}},
                                                {'provider': 'other', 'chat': None}])     # no chat's title
        code, _, raw = self.ask('GET', '/api/state')
        self.assertNotIn(secret.encode(), raw)
        self.assertNotIn(pagekey.key().encode(), raw)
        code, got = self.json_of('GET', '/api/state', pagekey_help.with_key())
        self.assertEqual(got['links']['key'], secret)            # the page itself makes the bookmark with it
        self.assertEqual((got['first_name'], got['provider_rows'][0]['chat']['name']), ('Sam', 'A private plan'))
        self.assertEqual(self.json_of('GET', '/api/state', {'Cookie': 'sbk_%d=%s' % (self.port, pagekey.key())}
                                      )[1]['links']['key'], '')

    def test_the_bookmarks_secret_comes_from_a_keyed_route_of_its_own(self):
        need()
        self.assertEqual(self.json_of('GET', '/api/bookmark'), (401, {'error': SENTENCE}))
        self.assertEqual(self.json_of('GET', '/api/bookmark', pagekey_help.with_key()), (200, {'key': core.key()}))

    def test_a_route_nobody_has_written_yet_is_locked_not_missing(self):
        self.assertEqual(self.json_of('GET', '/api/some-new-thing'), (401, {'error': SENTENCE}))
        self.assertEqual(self.json_of('GET', '/api/some-new-thing', pagekey_help.with_key())[0], 404)

    def test_an_old_menu_bar_or_page_tab_gets_the_one_sentence_on_get_and_post(self):
        self.assertEqual(self.json_of('POST', '/api/switch', {'X-Switchboard': '1'}, {'email': 'a@one.example'}),
                         (401, {'error': SENTENCE}))
        self.assertEqual(self.json_of('GET', '/api/usage', {'X-Switchboard': '1'}), (401, {'error': SENTENCE}))
        self.assertEqual(self.acted, [])

    def test_the_header_works(self):
        need()
        k = pagekey.key()
        self.assertEqual(self.json_of('GET', '/api/usage', {'X-Switchboard-Key': k}), (200, {'usage': 1}))
        self.assertEqual(self.json_of('POST', '/api/refresh', {'X-Switchboard': '1', 'X-Switchboard-Key': k}),
                         (200, {'ok': True}))
        self.assertEqual(self.acted, [('/api/refresh', {})])

    def test_the_old_cookie_alone_gets_the_one_sentence(self):
        need()
        k = pagekey.key()                           # a cookie is sent to every port of localhost: it never counts
        for c in ('sbk_%d=%s' % (self.port, k), 'other=1; sbk_%d=%s; x=%%' % (self.port, k)):
            self.assertEqual(self.json_of('GET', '/api/usage', {'Cookie': c}), (401, {'error': SENTENCE}))
            self.assertEqual(self.json_of('POST', '/api/refresh', {'X-Switchboard': '1', 'Cookie': c}),
                             (401, {'error': SENTENCE}))
        self.assertEqual(self.acted, [])

    def test_a_wrong_key_the_bookmarks_secret_or_another_ports_cookie_do_not(self):
        need()
        k = pagekey.key()
        for h in ({'X-Switchboard-Key': k[:-1] + ('0' if k[-1] != '0' else '1')},
                  {'X-Switchboard-Key': core.key()},
                  {'X-Switchboard-Key': ''},
                  {'Cookie': 'sbk_%d=%s' % (self.port, core.key())},
                  {'Cookie': 'sbk_%d=%s' % (self.port + 1, k)},
                  {'Cookie': 'sbk_%d' % self.port},
                  {'X-Switchboard': '1'}):
            self.assertEqual(self.json_of('GET', '/api/chats', h), (401, {'error': SENTENCE}), h)
            self.assertEqual(self.json_of('POST', '/api/refresh', dict(h, **{'X-Switchboard': '1'}))[0], 401, h)
        self.assertEqual(self.acted, [])

    def test_the_key_still_needs_the_pages_own_header_on_a_post(self):
        need()
        self.assertEqual(self.json_of('POST', '/api/refresh', {'X-Switchboard-Key': pagekey.key()}),
                         (403, {'error': 'forbidden'}))

    def test_another_websites_request_is_refused_in_its_own_words_first(self):
        self.assertEqual(self.json_of('GET', '/api/usage', {'Sec-Fetch-Site': 'cross-site'}),
                         (403, {'error': 'forbidden'}))
        self.assertEqual(self.json_of('POST', '/api/refresh', {'X-Switchboard': '1', 'Origin': 'https://a.example'}),
                         (403, {'error': 'forbidden'}))

    def test_the_old_link_router_is_answered_as_before(self):
        url = 'https://claude.ai/artifact/abcdefghijklmnop1234'
        self.assertEqual(self.json_of('POST', '/api/go', {'X-Claude-Switch': '1'}, {'url': url}),
                         (200, {'ok': True}))
        self.assertEqual(self.acted, [('/api/go', {'url': url, 'wait': False})])
        self.assertEqual(self.json_of('POST', '/api/check', {'X-Claude-Switch': '1'})[0], 403)

    def test_the_page_key_opens_nothing_at_go_and_the_bookmark_still_does(self):
        need()
        _, _, mine = self.ask('GET', '/go?k=%s' % core.key())
        _, _, page = self.ask('GET', '/go?k=%s' % pagekey.key())
        self.assertIn(b'const auto = true;', mine)
        self.assertIn(b'const auto = false;', page)


# ---------- the two link routes: a clicked link and the /go page, which can hold no key ----------

LINK = 'https://claude.ai/artifact/abcdefghijklmnop1234'
LINK_APP = {'Content-Type': 'application/json', 'X-Switchboard': '1'}       # router/SwitchboardLinks.swift askPage


class LinkRoutes(Page):
    def answer_with(self, value):
        p = mock.patch.object(server, 'act', lambda path, body: self.acted.append((path, body)) or value)
        p.start()
        self.addCleanup(p.stop)

    def test_the_link_apps_clicked_link_is_answered_as_before(self):
        body = {'url': LINK, 'wait': False, 'sent_at': 1700000000.5, 'fallback_after': 1.5}
        self.assertEqual(self.json_of('POST', '/api/go', LINK_APP, body), (200, {'ok': True}))
        self.assertEqual(self.acted, [('/api/go', body)])

    def test_the_go_pages_open_and_set_up_are_answered_without_the_key(self):
        self.assertEqual(self.json_of('POST', '/api/go', LINK_APP, {'url': LINK}), (200, {'ok': True}))
        self.assertEqual(self.json_of('POST', '/api/setup-profile', LINK_APP, {'email': 'a@one.example'}),
                         (200, {'ok': True}))
        self.assertEqual(self.acted, [('/api/go', {'url': LINK}), ('/api/setup-profile', {'email': 'a@one.example'})])

    def test_without_the_key_their_answers_name_nobody(self):
        need()
        self.answer_with({'email': 'a@one.example', 'name': 'One', 'profile': 'Profile 3', 'profile_name': 'One',
                          'title': 'A private page', 'checked': True, 'signin': False, 'trouble': False})
        self.assertEqual(self.json_of('POST', '/api/go', LINK_APP, {'url': LINK}),
                         (200, {'checked': True, 'signin': False, 'trouble': False, 'ok': True}))
        code, full = self.json_of('POST', '/api/go', pagekey_help.with_key(LINK_APP), {'url': LINK})
        self.assertEqual((code, full['email'], full['title']), (200, 'a@one.example', 'A private page'))
        self.answer_with({'profile': 'Profile 3', 'profile_name': 'One', 'opened': True})
        self.assertEqual(self.json_of('POST', '/api/setup-profile', LINK_APP, {'email': 'a@one.example'}),
                         (200, {'opened': True, 'ok': True}))

    def test_without_the_key_a_refusal_keeps_its_fix_and_loses_the_address(self):
        need()
        from switchboard import artifacts

        def refuse(path, body):
            raise artifacts.NoProfile('a@one.example', LINK)
        p = mock.patch.object(server, 'act', refuse)
        p.start()
        self.addCleanup(p.stop)
        code, said = self.json_of('POST', '/api/go', LINK_APP, {'url': LINK})
        self.assertEqual((code, said['fix']), (400, 'no_profile'))
        self.assertNotIn('@', said['error'])
        self.assertEqual(said['error'], "No browser profile is signed in as this page's owner yet. Set one up on "
                                        "Switchboard's page (Set up), then open it again.")
        code, said = self.json_of('POST', '/api/go', pagekey_help.with_key(LINK_APP), {'url': LINK})
        self.assertIn('a@one.example', said['error'])

    def test_every_other_post_with_the_same_headers_gets_the_one_sentence(self):
        for route in ('/api/switch', '/api/profile', '/api/auto', '/api/links', '/api/open', '/api/extension',
                      '/api/continue', '/api/signins', '/api/refresh', '/api/remove', '/api/models', '/api/check',
                      '/api/some-new-thing', '/api/go/', '/api/go?x=1'):
            self.assertEqual(self.json_of('POST', route, LINK_APP, {'email': 'a@one.example', 'profile': '+new'}),
                             (401, {'error': SENTENCE}), route)
        self.assertEqual(self.acted, [])

    def test_they_still_need_the_pages_own_header(self):
        self.assertEqual(self.json_of('POST', '/api/go', {'Content-Type': 'application/json'}, {'url': LINK})[0], 403)
        self.assertEqual(self.json_of('POST', '/api/setup-profile', dict(LINK_APP, Origin='https://a.example'),
                                      {'email': 'a@one.example'})[0], 403)
        self.assertEqual(self.acted, [])


# ---------- the page itself, and a ticket traded for the key: never a cookie ----------

class TheDoor(Page):
    def setUp(self):
        Page.setUp(self)
        if pagekey is not None:
            pagekey._SPENT.clear()
            self.addCleanup(pagekey._SPENT.clear)

    def trade(self, t, headers=None):
        return self.json_of('POST', '/api/ticket', {'X-Switchboard': '1'} if headers is None else headers, {'t': t})

    def test_the_page_holds_no_data_and_is_answered_to_anyone(self):
        for path in ('/', '/index.html', '/?t=nonsense'):
            code, h, body = self.ask('GET', path)
            self.assertEqual(code, 200, path)
            self.assertIn(b'name="switchboard-page"', body)       # the page itself, as before the key
            self.assertIsNone(h.get('Set-Cookie'))
        self.assertEqual(self.asked, [])                          # nothing of yours was read to answer it

    def test_a_ticket_is_traded_once_for_the_key(self):
        need()
        t = pagekey.ticket()
        self.assertEqual(self.trade(t), (200, {'key': pagekey.key()}))
        self.assertEqual(self.trade(t), (401, {'error': SENTENCE}))
        self.assertIn('refused a link to the page: its ticket was spent, expired or changed', self.lines)

    def test_an_expired_changed_or_missing_ticket_gets_401(self):
        need()
        t = pagekey.ticket()
        for bad in (pagekey.ticket(now=1000.0), t[:-1] + ('0' if t[-1] != '0' else '1'), 'nonsense', '', None, 5):
            self.assertEqual(self.trade(bad), (401, {'error': SENTENCE}), bad)
        self.assertEqual(self.json_of('POST', '/api/ticket', {'X-Switchboard': '1'}), (401, {'error': SENTENCE}))
        self.assertEqual(self.trade(t), (200, {'key': pagekey.key()}))      # none of the above spent it

    def test_trading_still_needs_the_pages_own_header_and_this_mac(self):
        need()
        t = pagekey.ticket()
        self.assertEqual(self.trade(t, {})[0], 403)
        self.assertEqual(self.trade(t, {'X-Switchboard': '1', 'Origin': 'https://a.example'})[0], 403)
        self.assertEqual(self.trade(t), (200, {'key': pagekey.key()}))

    def test_no_answer_of_the_page_ever_sets_a_cookie(self):
        need()
        t = pagekey.ticket()
        k = pagekey.key()
        for method, path, h, body in (('GET', '/', {}, None), ('GET', '/?t=' + t, {}, None),
                                      ('POST', '/api/ticket', {'X-Switchboard': '1'}, {'t': t}),
                                      ('POST', '/api/ticket', {'X-Switchboard': '1'}, {'t': t}),
                                      ('GET', '/api/state', {}, None), ('GET', '/api/usage', {}, None),
                                      ('GET', '/api/usage', {'X-Switchboard-Key': k}, None),
                                      ('POST', '/api/refresh', {'X-Switchboard': '1', 'X-Switchboard-Key': k}, {}),
                                      ('POST', '/api/go', {'X-Switchboard': '1'}, {'url': LINK}),
                                      ('GET', '/go', {}, None)):
            code, got, _ = self.ask(method, path, h, body)
            self.assertIsNone(got.get('Set-Cookie'), (method, path))

    def test_the_pages_script_trades_the_ticket_and_sends_the_key_in_a_header_only(self):
        with open(os.path.join(ROOT, 'switchboard', 'web', 'index.html'), encoding='utf-8') as f:
            page = f.read()
        script = page[page.index('<script>'):]
        self.assertIn("fetch('/api/ticket'", script)
        self.assertIn('localStorage.setItem(KEY_AT, d.key)', script)
        self.assertIn("history.replaceState(null, '', location.pathname + (String(rest) ? '?' + rest : '')", script)
        self.assertIn("'X-Switchboard-Key': pageKey", script)
        self.assertNotIn('document.cookie', page)
        # a picture can't send a header: with a key it is fetched; by its address only in the demo (no key there)
        self.assertIn("(pageKey ? 'data-preview=\"' + esc(x.id) + '\"' : 'src=\"/api/preview/'", script)
        self.assertIn("api('/api/preview/' + encodeURIComponent(id))", script)
        calls = [m.start() for m in re.finditer(r'\bfetch\(', script)]
        allowed = [script.index('const send = (path, o, bare) => fetch('), script.index("fetch('/api/ticket'")]
        self.assertEqual(len(calls), 2)                           # every other request goes through api()
        self.assertTrue(all(any(0 <= c - a < 80 for a in allowed) for c in calls))
        self.assertIn('To open this page, type <code>switchboard page</code> in a terminal, or use Open Switchboard in '
                      'your menu bar.', script)
        self.assertIn('It can show your chats, so it opens only for you.', script)

    def test_no_log_line_holds_the_key_the_bookmarks_secret_or_a_ticket(self):
        need()
        k, secret, t = pagekey.key(), core.key(), pagekey.ticket()
        pagekey._SAID.clear()                                   # refusals are said once a minute per route
        self.ask('GET', '/api/usage')
        self.ask('POST', '/api/refresh', {'X-Switchboard': '1', 'X-Switchboard-Key': 'wrong'})
        self.trade(t)
        self.trade(t)                                           # spent: refused, and said
        self.ask('GET', '/api/usage?fresh=1', {'X-Switchboard-Key': k})
        self.ask('POST', '/api/refresh', {'X-Switchboard': '1', 'X-Switchboard-Key': k})
        self.assertTrue(self.lines)
        for line in self.lines:
            for secret_text in (k, secret, t, t.split('.')[2]):
                self.assertNotIn(secret_text, line)
        self.assertIn('refused GET /api/usage: it did not carry the page key', self.lines)
        self.assertIn('refused a link to the page: its ticket was spent, expired or changed', self.lines)

    def test_an_old_tab_asking_every_few_seconds_is_logged_once_a_minute(self):
        need()
        pagekey._SAID.clear()
        for _ in range(5):
            self.ask('GET', '/api/history')
        self.assertEqual(self.lines.count('refused GET /api/history: it did not carry the page key'), 1)

    def test_the_demo_needs_no_key(self):
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = True
        self.assertEqual(self.json_of('GET', '/api/usage'), (200, {'usage': 1}))
        code, _, body = self.ask('GET', '/')
        self.assertEqual(code, 200)
        self.assertIn(b'name="switchboard-page"', body)
        self.assertEqual(self.trade(pagekey.ticket()) if pagekey else (200, {}), (200, {}))    # no key to hand out


# ---------- opening the page: a file, never the key ----------

class Opening(unittest.TestCase):
    def setUp(self):
        need()
        pagekey._SPENT.clear()
        self.addCleanup(pagekey._SPENT.clear)
        self.port = int(os.environ.get('SWITCHBOARD_PORT') or 8062)
        self.seen, self.said = [], []
        folder = core.data_path('page-open')
        for name in os.listdir(folder) if os.path.isdir(folder) else ():
            os.unlink(os.path.join(folder, name))
        p = mock.patch.object(subprocess, 'call', lambda argv, **kw: self.seen.append(argv) or 0)
        p.start()
        self.addCleanup(p.stop)

    def say(self, sentence, url):
        self.said.append((sentence, url))

    def test_open_is_handed_a_private_file_and_never_the_key_or_a_ticket(self):
        self.assertTrue(pagekey.open_page(pagekey.launcher(True), self.say, wait=False))
        self.assertEqual(len(self.seen), 1)
        argv = self.seen[0]
        self.assertEqual(argv[0], '/usr/bin/open')
        path = argv[1]
        folder = core.data_path('page-open')
        self.assertEqual(os.path.dirname(path), folder)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(folder).st_mode), 0o700)
        t = pagekey_help.ticket_in(path)
        joined = ' '.join(argv)
        for secret_text in (pagekey.key(), core.key(), t, t.split('.')[2]):
            self.assertNotIn(secret_text, joined)
        with open(path, encoding='utf-8') as f:
            self.assertIn('http://127.0.0.1:%d/?t=%s' % (self.port, t), f.read())
        self.assertEqual(self.said, [])

    def test_the_file_goes_when_its_ticket_is_taken(self):
        pagekey.open_page(pagekey.launcher(True), self.say, wait=False)
        path = self.seen[0][1]
        self.assertTrue(pagekey.redeem(pagekey_help.ticket_in(path)))
        self.assertFalse(os.path.exists(path))

    def test_a_file_whose_ticket_expired_is_swept(self):
        path = pagekey.opener(self.port, now=1000.0)
        self.assertTrue(os.path.exists(path))
        self.assertEqual(pagekey.sweep(now=1000.0 + pagekey.TICKET_FOR - 1), 0)
        self.assertEqual(pagekey.sweep(now=1000.0 + pagekey.TICKET_FOR), 1)
        self.assertFalse(os.path.exists(path))

    def test_open_failing_says_a_fresh_link_that_works_once_and_leaves_no_file(self):
        self.assertFalse(pagekey.open_page(lambda target: False, self.say, wait=True))
        self.assertEqual(len(self.said), 1)
        sentence, url = self.said[0]
        self.assertEqual(sentence, 'Open %s in your browser. It works once, for five minutes.' % url)
        self.assertTrue(url.startswith('http://127.0.0.1:%d/?t=' % self.port))
        self.assertEqual(os.listdir(core.data_path('page-open')), [])
        t = url.split('?t=', 1)[1]
        self.assertTrue(pagekey.redeem(t))
        self.assertFalse(pagekey.redeem(t))

    def test_a_file_nobody_takes_gets_the_same_line_after_a_short_wait(self):
        path = pagekey.opener(self.port)
        clock = iter(range(0, 1000, 5))
        self.assertFalse(pagekey.watch_taken(path, self.say, self.port, patience=15, sleep=lambda s: None,
                                             clock=lambda: next(clock)))
        sentence, url = self.said[0]
        self.assertEqual(sentence, 'If the page did not open, open %s in your browser. It works once, for five '
                                   'minutes.' % url)
        self.assertTrue(os.path.exists(path))                   # a browser that is only slow still finds it

    def test_a_file_the_page_takes_ends_the_wait_and_nothing_is_said(self):
        path = pagekey.opener(self.port)
        taken = []

        def sleep(_):
            if not taken:
                taken.append(pagekey.redeem(pagekey_help.ticket_in(path)))
        self.assertTrue(pagekey.watch_taken(path, self.say, self.port, patience=15, sleep=sleep))
        self.assertEqual((taken, self.said), ([True], []))

    def test_a_place_on_the_page_rides_after_the_ticket_and_nothing_else_does(self):
        with open(pagekey.opener(self.port, part='#accounts/add'), encoding='utf-8') as f:
            self.assertRegex(f.read(), r'"http://127.0.0.1:%d/\?t=[^"#]+#accounts/add"' % self.port)
        for odd in ('#a"><script>', 'accounts', '#' + 'a' * 80, '#a b'):
            with open(pagekey.opener(self.port, part=odd), encoding='utf-8') as f:
                self.assertRegex(f.read(), r'"http://127.0.0.1:%d/\?t=[0-9a-f.A-Za-z_-]+"' % self.port, odd)
        self.assertFalse(pagekey.open_page(lambda target: False, self.say, part='#accounts/add'))
        self.assertTrue(self.said[0][1].endswith('#accounts/add'))

    def test_the_demo_opens_its_plain_address_and_writes_nothing(self):
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = True
        got = []
        self.assertTrue(pagekey.open_page(lambda target: got.append(target) or True, self.say, wait=True))
        self.assertEqual(got, ['http://127.0.0.1:%d' % self.port])
        self.assertFalse(os.path.exists(core.data_path('page-open')) and os.listdir(core.data_path('page-open')))


# ---------- every caller goes through the one helper ----------

class Callers(Page):
    def test_no_code_builds_the_pages_headers_by_itself(self):
        found = []
        for top, _, files in os.walk(os.path.join(ROOT, 'switchboard')):
            for name in files:
                if not name.endswith('.py') or name == 'pagekey.py':
                    continue
                path = os.path.join(top, name)
                with open(path, encoding='utf-8') as f:
                    for n, line in enumerate(f, 1):
                        if re.search(r"""['"]X-Switchboard['"]\s*:""", line) and 'POSTERS' not in line \
                                and not re.match(r"\s*'[a-z ]+': \{'X-", line):
                            found.append('%s:%d' % (os.path.relpath(path, ROOT), n))
        self.assertEqual(found, [])

    def test_the_command_line_reaches_a_locked_route(self):
        from switchboard import __main__ as cli
        self.assertEqual(cli.ask_page('/api/refresh', {'a': 1}, timeout=10), {'ok': True})
        self.assertEqual(self.acted, [('/api/refresh', {'a': 1})])

    def test_the_menu_bar_reaches_a_locked_route(self):
        from switchboard import menubar
        self.assertEqual(menubar.post('/api/auto', {'enabled': True}, timeout=10), (True, {'ok': True}))
        self.assertEqual(self.acted, [('/api/auto', {'enabled': True})])

    def test_the_link_code_reaches_a_locked_route(self):
        from switchboard import linkapp
        url = 'https://claude.ai/artifact/abcdefghijklmnop1234'
        self.assertEqual(linkapp.ask_page('/api/go', {'url': url}, timeout=10), {'ok': True})
        self.assertEqual(self.acted, [('/api/go', {'url': url})])

    def test_page_answers_asks_the_open_route_and_sends_no_key_there(self):
        need()
        seen = []
        real = pagekey.headers
        with mock.patch.object(pagekey, 'headers', lambda *a: seen.append(a) or real(*a)):
            self.assertTrue(server.page_answers(self.port))
        self.assertEqual(seen, [('/api/state', 'GET')])


# ---------- a new key ----------

class NewKey(Page):
    def test_switchboard_page_new_key_makes_a_new_one_and_the_old_one_stops_working(self):
        need()
        import io
        from contextlib import redirect_stdout
        from switchboard import __main__ as cli
        old_secret, old = core.key(), pagekey.key()
        self.assertEqual(self.json_of('GET', '/api/usage', {'X-Switchboard-Key': old})[0], 200)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(['page', '--new-key']), 0)
        self.assertEqual(out.getvalue(), 'Made a new key for the page. Open it again with Open Switchboard in your menu '
                                         'bar, or type `switchboard page`. Add the Open as its owner bookmark again '
                                         'from the page.\n')
        self.assertNotEqual(core.key(), old_secret)
        self.assertNotEqual(pagekey.key(), old)
        self.assertEqual(self.json_of('GET', '/api/usage', {'X-Switchboard-Key': old}), (401, {'error': SENTENCE}))
        self.assertEqual(self.json_of('GET', '/api/usage', pagekey_help.with_key())[0], 200)
        self.assertNotIn(old_secret, out.getvalue())
        self.assertNotIn(pagekey.key(), out.getvalue())


if __name__ == '__main__':
    unittest.main()
