import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""Link forms and the link app itself, built from router/SwitchboardLinks.swift into a
temporary folder and run only with its pure commands (--route, --owner, --page) and --decide, which routes a link as a
click does against a fake page but prints what it did instead of opening anything. Python and Swift must agree on
every link and on where the page is."""
import json
import os
import subprocess
import time
import unittest
from unittest import mock

from fakes_links import ID1, ID2, LINK, FakePage, Home, free_port, link_app, time_bound

from switchboard import artifacts, linkapp

UUID = '0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0'
# (text, owner link or None, id, the link app would be handed it as a URL)
FORMS = (
    (LINK % ID1, LINK % ID1, ID1, True),
    ('https://claude.ai/code/artifact/' + UUID, 'https://claude.ai/code/artifact/' + UUID, UUID, True),
    (LINK % ID1 + '?version=3', LINK % ID1 + '?version=3', ID1, True),
    (LINK % ID1 + '#comments', LINK % ID1 + '#comments', ID1, True),
    (LINK % ID1 + '/', LINK % ID1 + '/', ID1, True),
    ('https://claude.ai/code/artifact/%s/edit?x=1' % UUID, 'https://claude.ai/code/artifact/%s/edit?x=1' % UUID, UUID, True),
    ('HTTPS://CLAUDE.AI/artifact/' + ID1, LINK % ID1, ID1, True),
    ('https://Claude.AI:443/artifact/' + ID1, LINK % ID1, ID1, True),
    ('https://claude.ai:8443/artifact/' + ID1, None, None, True),
    ('http://claude.ai/artifact/' + ID1, None, None, True),
    ('https://claude.ai.evil.example/artifact/' + ID1, None, None, True),
    ('https://evil.example/claude.ai/artifact/' + ID1, None, None, True),
    ('https://claude.ai@evil.example/artifact/' + ID1, None, None, True),
    ('https://user:pw' + '@' + 'claude.ai/artifact/' + ID1, None, None, True),     # a login part: refused
    ('https://www.claude.ai/artifact/' + ID1, None, None, True),
    ('https://claude.ai/artifact/short', None, None, True),
    ('https://claude.ai/chat/' + ID1, None, None, True),
    ('https://claude.ai/public/artifacts/' + UUID, None, None, True),
    ('https://claude.ai/artifact/abc%2Fdefghijklmnopqrstu', None, None, True),
    ('https://nam12.safelinks.protection.outlook.com/?url=https%3A%2F%2Fclaude.ai%2Fartifact%2F' + ID1 + '&data=05',
     LINK % ID1, ID1, True),
    ('https://www.google.com/url?q=https://claude.ai/artifact/%s&sa=D' % ID1, LINK % ID1, ID1, True),
    ('https://slack-redir.net/link?url=https%3A%2F%2Fclaude.ai%2Fartifact%2F' + ID1, LINK % ID1, ID1, True),
    ('https://nam12.safelinks.protection.outlook.com/?url=https%3A%2F%2Fexample.com%2Fartifact%2F' + ID1, None, None, True),
    ('javascript:alert(1)', None, None, True),
    ('see ' + LINK % ID1 + '.', LINK % ID1, ID1, False),
    ('<' + LINK % ID1 + '|Budget>', LINK % ID1, ID1, False),
    ('[Budget](' + LINK % ID1 + ')', LINK % ID1, ID1, False),
    ('"' + LINK % ID1 + '"', LINK % ID1, ID1, False),
    (LINK % ID1 + '\n', LINK % ID1, ID1, False),
    ('claude.ai/artifact/' + ID1, None, None, False),
    ('', None, None, False),
    ('x' * 10000, None, None, False),
)


class LinkForms(unittest.TestCase):
    def test_thirty_real_and_malformed_forms(self):
        self.assertGreaterEqual(len(FORMS), 30)
        for text, url, aid, _ in FORMS:
            with self.subTest(text=text[:80]):
                got = artifacts.parse_link(text)
                self.assertEqual(got, {'url': url, 'id': aid} if url else None)
        self.assertIsNone(artifacts.parse_link(None))
        self.assertIsNone(artifacts.parse_link(b'https://claude.ai/artifact/' + ID1.encode()))

    def test_go_takes_every_form_parse_link_takes_and_refuses_the_rest(self):
        for text in ('https://claude.ai.evil.example/artifact/' + ID1, 'https://claude.ai/chat/' + ID1, ''):
            with self.assertRaises(artifacts.SwitchError):
                artifacts.go(text)


class TheLinkAppItself(Home):
    @classmethod
    def setUpClass(cls):
        cls.exe = link_app()

    def setUp(self):
        if not self.exe:
            self.skipTest('no Swift compiler here')
        super().setUp()

    def ask(self, *args, env=None):
        e = dict(os.environ, SWITCHBOARD_HOME=self.data)
        e.pop('SWITCHBOARD_PORT', None)
        e.update(env or {})
        began = time.monotonic()
        r = subprocess.run([self.exe] + list(args), capture_output=True, text=True, timeout=60, env=e)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip(), time.monotonic() - began

    def owners(self, m):
        os.makedirs(self.data, exist_ok=True)
        with open(os.path.join(self.data, 'owners.json'), 'w') as f:
            json.dump(m, f)

    def test_python_and_swift_agree_on_every_link_the_app_can_be_handed(self):
        for text, url, _, shaped in FORMS:
            if not shaped:
                continue
            with self.subTest(text=text[:80]):
                self.assertEqual(self.ask('--route', text)[0], 'owner' if url else 'plain')

    def test_the_owner_map_alone_decides_where_a_link_opens(self):
        self.owners({'v': 1, 'fallback': 'Claude Work', 'artifacts': {
            ID1: {'o': 'ana@work.example', 'p': 'Profile 4', 's': 'seen'},
            ID2: {'o': 'ana@gmail.example', 'p': None, 's': 'published'}}})
        self.assertEqual(self.ask('--owner', LINK % ID1)[0], 'Profile 4')
        self.assertEqual(self.ask('--owner', 'HTTPS://CLAUDE.AI/artifact/%s?x=1' % ID1)[0], 'Profile 4')
        self.assertEqual(self.ask('--owner', LINK % ID2)[0], '')                 # known owner, no profile: never another's
        self.assertEqual(self.ask('--owner', LINK % ('n' * 20))[0], 'Claude Work')   # nobody knows it: the fallback
        self.owners({'v': 1, 'fallback': '../Default', 'artifacts': {ID1: {'o': None, 'p': 'Work\n--x', 's': None}}})
        self.assertEqual(self.ask('--owner', LINK % ID1)[0], '')                 # never a name that isn't a profile's
        self.owners({'v': 2, 'artifacts': {ID1: {'o': 'x', 'p': 'Profile 4'}}})
        self.assertEqual(self.ask('--owner', LINK % ID1)[0], '')                 # a map it doesn't know the shape of

    def test_a_running_page_takes_the_link_with_switchboards_header(self):
        page = FakePage(self)
        out, _ = self.ask('--decide', LINK % ID1, env={'SWITCHBOARD_PORT': str(page.port)})
        if time_bound(1) == 1:
            self.assertEqual(out, 'page')
        else:                                   # a starved test process may answer after the app's 1.7 s: the app
            self.assertIn(out, ('page', 'plain'))   # then falls back (to plain: no owner map here), as it should
        for _ in range(200):
            if page.got:
                break
            time.sleep(0.05)
        got = page.got[0]
        self.assertEqual((got['path'], got['headers'].get('x-switchboard'), got['headers'].get('content-type')),
                         ('/api/go', '1', 'application/json'))
        body = got['body']
        self.assertEqual((body['url'], body['wait'], body['fallback_after']), (LINK % ID1, False, 1.5))
        self.assertAlmostEqual(body['sent_at'], time.time(), delta=30)

    def test_with_the_page_down_it_opens_in_the_owners_profile_from_the_file_alone(self):
        self.owners({'v': 1, 'fallback': 'Claude Work', 'artifacts': {ID1: {'o': 'a', 'p': 'Profile 4', 's': 'seen'}}})
        port = free_port()
        self.assertEqual(self.ask('--decide', LINK % ID1, env={'SWITCHBOARD_PORT': str(port)})[0], 'open Profile 4')
        self.assertEqual(self.ask('--decide', LINK % ('n' * 20), env={'SWITCHBOARD_PORT': str(port)})[0],
                         'open Claude Work')
        os.unlink(os.path.join(self.data, 'owners.json'))
        self.assertEqual(self.ask('--decide', LINK % ID1, env={'SWITCHBOARD_PORT': str(port)})[0], 'plain')

    def test_a_slow_page_loses_the_link_within_three_seconds(self):
        self.owners({'v': 1, 'artifacts': {ID1: {'o': 'a', 'p': 'Profile 4', 's': 'seen'}}})
        page = FakePage(self, mode='slow', delay=30)
        out, took = self.ask('--decide', LINK % ID1, env={'SWITCHBOARD_PORT': str(page.port)})
        self.assertEqual(out, 'open Profile 4')
        self.assertLess(took, time_bound(3.0))

    def test_python_finds_the_page_exactly_where_the_link_app_does(self):
        cases = (
            ({}, None, None),
            ({'SWITCHBOARD_PORT': '59001'}, '59002', 59003),
            ({}, '59002', 59003),
            ({}, None, 59003),
            ({}, ' 59004 ', '59005'),
            ({}, None, '59005'),                    # a string in settings.json is not a port
            ({}, None, True),
            ({}, None, 59005.0),
            ({}, None, 59005.5),
            ({'SWITCHBOARD_PORT': 'abc'}, '70000', 0),
            ({'SWITCHBOARD_PORT': '+59006'}, None, None),
            ({'SWITCHBOARD_PORT': ' 59007\t'}, None, None),
            ({'SWITCHBOARD_PORT': '0'}, '\n59008\n', None),
        )
        for env, port_file, settings in cases:
            with self.subTest(env=env, port_file=port_file, settings=settings):
                for name in ('port', 'settings.json'):
                    try:
                        os.unlink(os.path.join(self.data, name))
                    except FileNotFoundError:
                        pass
                os.makedirs(self.data, exist_ok=True)
                if port_file is not None:
                    with open(os.path.join(self.data, 'port'), 'w') as f:
                        f.write(port_file)
                if settings is not None:
                    with open(os.path.join(self.data, 'settings.json'), 'w') as f:
                        json.dump({'port': settings}, f)
                swift = self.ask('--page', env=env)[0]
                with mock.patch.dict(os.environ, env):
                    python = 'http://127.0.0.1:%d/api/go' % linkapp.page_port()
                self.assertEqual(python, swift)

    def test_the_link_app_decides_at_one_and_a_half_seconds_and_falls_back_to_the_map(self):
        with open(os.path.join(os.path.dirname(__file__), '..', 'router', 'SwitchboardLinks.swift')) as f:
            src = f.read()
        self.assertIn('let pageWait = 1.5', src)
        self.assertIn('DispatchQueue.global().asyncAfter(deadline: .now() + pageWait + 0.2) { once(false) }', src)
        self.assertIn('openFromMap(link, url)', src)
        self.assertNotIn('DispatchQueue.main.async { openPlain(url) }', src)      # the old plain fallback
        self.assertEqual(artifacts.POSTER_SLACK, 0.2)


if __name__ == '__main__':
    unittest.main()
