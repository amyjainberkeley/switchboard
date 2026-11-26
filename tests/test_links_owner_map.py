import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""The owner map. The page writes ~/.switchboard/owners.json; the link app, the command line and a go
helper read it, so a link opens as its owner when the page is slow or down, never in the window used last. Also the
header contract every poster keeps with the page, and the /go bookmark's key."""
import json
import os
import stat
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

from fakes_links import A, B, C, G, ID1, ID2, ID3, ID4, LINK, FakeChrome, FakePage, Home, free_port, time_bound

from switchboard import artifacts, core, linkapp


def owners_file():
    with open(artifacts.map_path()) as f:
        return json.load(f)


class TheMapIsWritten(Home):
    def setUp(self):
        super().setUp()
        self.chrome = FakeChrome(self, {'P1': 'Work', 'P2': 'Home', 'P3': 'Side'}, extensions=('P1',),
                                 logins={'P1': A, 'P2': B})
        now = time.time()
        self.state(active=A, **{A: {'profile': 'P1', 'profile_src': 'checked', 'proof': {'profile': 'P1', 'at': now}},
                                B: {'profile': 'P2', 'profile_src': 'you'},
                                G: {'profile': 'P3', 'profile_src': 'checked',
                                    'doubt': {'profile': 'P3', 'at': now, 'why': 'other_account', 'by': C}}})
        self.page(ID1, owner=A, src='seen', profile='P1')
        self.page(ID2, owner=B, src='published')
        self.page(ID3, owner=G, src='listed', profile='P3')
        self.page(ID4)

    def test_every_tick_writes_who_owns_what_and_where_it_opens(self):
        artifacts.refresh()
        m = owners_file()
        self.assertEqual((m['v'], m['active'], m['browser']), (1, A, 'Google Chrome'))
        self.assertEqual(m['artifacts'][ID1], {'o': A, 'p': 'P1', 's': 'seen'})
        self.assertEqual(m['artifacts'][ID2], {'o': B, 'p': 'P2', 's': 'published'})
        self.assertEqual(m['artifacts'][ID3], {'o': G, 'p': None, 's': 'listed'})     # its profile is someone else's
        self.assertEqual(m['artifacts'][ID4], {'o': None, 'p': None, 's': None})
        self.assertEqual(m['fallback'], 'P1')                # the account in use's profile
        self.assertEqual((m['accounts'][A]['link'], m['accounts'][B]['link'], m['accounts'][G]['link']),
                         ('proved', 'unproved', 'suspect'))
        self.assertEqual((m['accounts'][A]['extension'], m['accounts'][B]['extension']), (True, False))
        self.assertEqual(m['chrome']['profiles']['P1'], {'extension': True, 'account': A})
        self.assertEqual(m['timeline'][-1][0], A)
        self.assertEqual(stat.S_IMODE(os.stat(artifacts.map_path()).st_mode), 0o600)
        with open(artifacts.map_path()) as f:
            text = f.read()
        self.assertNotIn('title', text)                      # owners and profiles only: no titles, no tokens

    def test_it_is_written_only_when_something_in_it_changed(self):
        self.assertTrue(artifacts.write_owner_map())
        self.assertFalse(artifacts.write_owner_map())
        with core.locked() as st:
            st['active'] = B
        self.assertTrue(artifacts.write_owner_map())
        self.assertEqual(owners_file()['fallback'], 'P2')

    def test_a_page_load_rewrites_it_at_once(self):
        artifacts.write_owner_map()
        artifacts.learn_ok(ID4, 'P2', B, 'Notes', [])
        self.assertEqual(owners_file()['artifacts'][ID4], {'o': B, 'p': 'P2', 's': 'seen'})

    def test_the_lookup_never_sends_a_known_owners_page_to_another_account(self):
        artifacts.refresh()
        m = owners_file()
        self.assertEqual(artifacts.map_lookup(m, ID1)['profile'], 'P1')
        self.assertEqual(artifacts.map_lookup(m, ID3), {'email': G, 'profile': None, 'src': 'listed', 'from': None})
        self.assertEqual(artifacts.map_lookup(m, 'z' * 20)['profile'], 'P1')     # unknown: the fallback
        self.assertEqual(artifacts.map_lookup({}, ID1)['profile'], None)


class ClickedLinksAlwaysOpenAsTheirOwner(Home):
    def setUp(self):
        super().setUp()
        self.chrome = FakeChrome(self, {'P1': 'Work', 'P2': 'Home'}, logins={'P1': A, 'P2': B})
        self.state(active=A, **{A: {'profile': 'P1', 'profile_src': 'checked'}, B: {'profile': 'P2', 'profile_src': 'you'}})
        self.page(ID2, owner=B, src='published')

    def test_when_the_check_fails_the_owner_map_opens_it_in_the_owners_profile(self):
        artifacts.write_owner_map()
        with mock.patch.object(artifacts, 'go', side_effect=RuntimeError('the browser did not answer')):
            r = artifacts.go_quietly(LINK % ID2)
        self.assertEqual((r['by'], r['profile'], r['email']), ('map', 'P2', B))
        self.assertEqual(self.chrome.opened, [('P2', LINK % ID2, False)])

    def test_with_no_map_on_disk_the_saved_state_stands_in(self):
        with mock.patch.object(artifacts, 'go', side_effect=RuntimeError('x')):
            artifacts.go_quietly(LINK % ID2)
        self.assertEqual(self.chrome.opened, [('P2', LINK % ID2, False)])

    def test_an_unknown_link_opens_in_the_account_in_uses_profile_never_the_other_links_profile(self):
        with open(core.data_path('home-profile.txt'), 'w') as f:
            f.write('P2')                                    # "other links open in" P2
        artifacts.write_owner_map()
        with mock.patch.object(artifacts, 'go', side_effect=RuntimeError('x')):
            artifacts.go_quietly(LINK % ('n' * 20))
        self.assertEqual(self.chrome.opened, [('P1', LINK % ('n' * 20), False)])

    def test_a_request_that_arrives_after_the_poster_opened_it_is_not_opened_twice(self):
        clock = mock.patch('switchboard.artifacts.time')
        frozen = clock.start()
        self.addCleanup(clock.stop)
        frozen.time.return_value = 5000.0
        with mock.patch.object(artifacts, 'go') as go:
            r = artifacts.go_quietly(LINK % ID2, sent_at=4990.0, fallback_after=1.5)
            self.assertEqual(r['by'], 'poster')
            r = artifacts.go_quietly(LINK % ID2, sent_at=5000.0 - 1.75, fallback_after=1.5)   # past the app's timer
            self.assertEqual(r['by'], 'poster')
            go.assert_not_called()
            artifacts.go_quietly(LINK % ID2, sent_at=5000.0 - 1.65, fallback_after=1.5)      # its answer still counts
            artifacts.go_quietly(LINK % ID2, sent_at=5000.0, fallback_after=1.5)
            self.assertEqual(go.call_count, 2)
        self.assertEqual(self.chrome.opened, [])

    def test_a_whole_click_through_the_fake_browser(self):
        self.chrome.owners[ID2] = B
        r = artifacts.go_quietly(LINK % ID2)
        self.assertEqual((r['email'], r['profile'], r['checked']), (B, 'P2', True))
        self.assertEqual(owners_file()['artifacts'][ID2]['s'], 'seen')


class TheCommandLine(Home):
    def setUp(self):
        super().setUp()
        self.chrome = FakeChrome(self, {'P1': 'Work', 'P2': 'Home'}, logins={'P1': A, 'P2': B})
        self.state(active=A, **{A: {'profile': 'P1', 'profile_src': 'checked'}, B: {'profile': 'P2', 'profile_src': 'you'},
                                G: {}})
        self.page(ID2, owner=B, src='seen', profile='P2')
        self.page(ID3, owner=G, src='published')
        artifacts.write_owner_map()

    def point_at(self, page_or_port):
        port = page_or_port if isinstance(page_or_port, int) else page_or_port.port
        p = mock.patch.dict(os.environ, {'SWITCHBOARD_PORT': str(port)})
        p.start()
        self.addCleanup(p.stop)

    def test_open_asks_the_running_page_with_switchboards_header(self):
        page = FakePage(self, answer={'email': B, 'name': B, 'profile': 'P2', 'checked': True, 'title': 'Roadmap'})
        self.point_at(page)
        r = artifacts.open_link('<' + LINK % ID2 + '>.')
        self.assertEqual((r['by'], r['said']), ('page', 'Opened as %s: Roadmap.' % B))
        got = page.got[0]
        self.assertEqual((got['path'], got['body'], got['headers'].get('x-switchboard')), ('/api/go', {'url': LINK % ID2}, '1'))
        self.assertEqual(self.chrome.opened, [])            # the page opened it, not this command

    def test_with_the_page_down_it_opens_from_the_owner_map_alone(self):
        self.point_at(free_port())
        r = artifacts.open_link(LINK % ID2)
        self.assertEqual((r['by'], r['profile']), ('map', 'P2'))
        self.assertIn('saved owner list', r['said'])
        self.assertEqual(self.chrome.opened, [('P2', LINK % ID2, False)])

    def test_with_the_page_down_a_known_owner_without_a_profile_opens_nothing_and_says_set_up(self):
        self.point_at(free_port())
        r = artifacts.open_link(LINK % ID3)
        self.assertTrue(r['no_profile'])
        self.assertIn('press Set up for %s' % G, r['said'])
        self.assertEqual(self.chrome.opened, [])

    def test_a_slow_page_loses_the_link_to_the_map_quickly(self):
        page = FakePage(self, mode='slow', delay=30)
        self.point_at(page)
        began = time.monotonic()
        r = artifacts.open_link(LINK % ID2, wait=False, timeout=1.0)
        self.assertLess(time.monotonic() - began, time_bound(3.0))
        self.assertEqual(r['by'], 'map')
        self.assertEqual(self.chrome.opened, [('P2', LINK % ID2, False)])
        for _ in range(100):
            if page.got:
                break
            time.sleep(0.05)
        body = page.got[0]['body']
        self.assertEqual((body['wait'], body['fallback_after']), (False, 1.0))
        self.assertAlmostEqual(body['sent_at'], time.time(), delta=10)

    def test_a_page_fault_or_refusal_still_opens_from_the_map_and_its_words_reach_the_person(self):
        self.point_at(FakePage(self, mode='error'))
        r = artifacts.open_link(LINK % ID2)                # a 500 is "didn't take it": the map opens it
        self.assertEqual(r['by'], 'map')
        page = FakePage(self, mode='said', answer={'error': 'No Google Chrome profile is signed in as it.'})
        self.point_at(page)
        with self.assertRaises(core.SwitchError) as said:
            artifacts.open_link(LINK % ID2)
        self.assertEqual(str(said.exception), 'No Google Chrome profile is signed in as it.')
        self.point_at(FakePage(self, mode='refused'))
        self.assertEqual(artifacts.open_link(LINK % ID2)['by'], 'map')     # a 403 is the page not taking it

    def test_owner_names_the_owner_where_it_opens_and_the_exact_commands(self):
        r = artifacts.owner_of(LINK % ID2, scan=False)
        self.assertEqual((r['email'], r['profile'], r['src']), (B, 'P2', 'seen'))
        self.assertIn('switchboard open %s' % (LINK % ID2), r['said'])
        self.assertIn('switchboard run %s -- -p' % B, r['said'])
        self.assertIn('Opens in the Google Chrome profile "Home"', r['said'])
        no = artifacts.owner_of(LINK % ID3, scan=False)
        self.assertIn('No Google Chrome profile is signed in as it', no['said'])
        unknown = artifacts.owner_of(LINK % ('q' * 20), scan=False)
        self.assertIsNone(unknown['email'])
        self.assertTrue(unknown['said'].startswith('Not known yet.'))
        for said in (r['said'], no['said'], unknown['said']):
            self.assertNotIn('sign in', said.lower())
            self.assertNotIn('deleted', said.lower())
        with self.assertRaises(core.SwitchError):
            artifacts.owner_of('https://example.com/artifact/' + ID2)


class TheHeaderContract(Home):
    """Every poster against the real page handler (server.Handler): who may open links, and nobody else."""

    def setUp(self):
        super().setUp()
        from switchboard import server
        self.server_mod = server
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        p = mock.patch.dict(os.environ, {'SWITCHBOARD_PORT': str(self.port)})
        p.start()
        self.addCleanup(p.stop)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.clicked = []
        self.patch(artifacts, 'go_quietly', lambda url, *a, **k: self.clicked.append(url))
        self.patch(artifacts, 'go', lambda url, quiet=False, wait=None: {'email': B, 'profile': 'P2', 'checked': True})

    def post(self, path, body, headers):
        req = urllib.request.Request('http://127.0.0.1:%d%s' % (self.port, path), data=json.dumps(body).encode(),
                                     headers=dict({'Content-Type': 'application/json'}, **headers))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=10) as r:
                return r.status
        except urllib.error.HTTPError as e:
            e.close()
            return e.code

    def test_each_poster(self):
        url = LINK % ID2
        for who, headers in linkapp.POSTERS.items():
            with self.subTest(poster=who):
                body = {'url': url, 'wait': False} if who == 'link app' else {'url': url}
                self.assertEqual(self.post('/api/go', body, headers), 200)
        self.assertEqual(self.post('/api/go', {'url': url}, {}), 403)                       # a website, no header
        self.assertEqual(self.post('/api/switch', {'email': A}, {'X-Claude-Switch': '1'}), 403)   # the old router: links only
        self.assertEqual(self.post('/api/go', {'url': url}, {'X-Switchboard': '1', 'Origin': 'https://evil.example'}), 403)
        for _ in range(250):
            if len(self.clicked) >= 2:
                break
            time.sleep(0.02)
        # The link app (wait: false) and a Link Router (answered at once too) open it in the
        # background; the page's own and the command line's posts wait for the check.
        self.assertEqual(self.clicked, [url, url])

    def test_the_sources_send_the_header_the_contract_names(self):
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, '..', 'router', 'SwitchboardLinks.swift')) as f:
            swift = f.read()
        self.assertIn('request.setValue("1", forHTTPHeaderField: "X-Switchboard")', swift)
        self.assertIn('request.setValue("application/json", forHTTPHeaderField: "Content-Type")', swift)
        with open(os.path.join(here, '..', 'switchboard', 'web', 'go.html')) as f:
            page = f.read()
        self.assertIn("'X-Switchboard': '1'", page)
        self.assertNotIn('innerHTML', page)
        self.assertNotIn('insertAdjacentHTML', page)
        self.assertNotIn('document.write', page)


class TheBookmarksKey(Home):
    """Only a visit with this install's key opens by itself; a lost settings file makes a new key."""

    def setUp(self):
        super().setUp()
        from switchboard import server
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        p = mock.patch.dict(os.environ, {'SWITCHBOARD_PORT': str(self.port)})
        p.start()
        self.addCleanup(p.stop)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def get(self, path, host=None):
        req = urllib.request.Request('http://127.0.0.1:%d%s' % (self.port, path))
        if host:
            req.add_header('Host', host)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            body = e.read()
            e.close()
            return e.code, body

    def test_a_wrong_key_never_opens_and_a_lost_settings_file_makes_a_new_key(self):
        key = core.key()
        self.assertIn(b'const auto = true;', self.get('/go?k=' + key)[1])
        for wrong in ('', 'guess', key[:-1], key + 'x', '%00' + key):
            self.assertIn(b'const auto = false;', self.get('/go?k=' + wrong)[1])
        self.assertEqual(self.get('/go?k=' + key, host='evil.example:%d' % self.port)[0], 403)
        os.unlink(core.data_path('settings.json'))
        self.assertIn(b'const auto = false;', self.get('/go?k=' + key)[1])     # the old bookmark asks first now
        new = core.key()
        self.assertNotEqual(new, key)
        self.assertIn(b'const auto = true;', self.get('/go?k=' + new)[1])


if __name__ == '__main__':
    unittest.main()
