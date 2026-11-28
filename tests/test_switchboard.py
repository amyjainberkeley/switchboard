import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Tests for the parts that decide things.
#
# Everything runs in a throwaway folder. Nothing here opens a browser, runs
# claude, or reads your real accounts: the engine (accounts.api) is a fake where one is needed.
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from unittest import mock

TMP = tempfile.mkdtemp(prefix='switchboard-test-')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import artifacts, browser, connect, core, demo, engine, server
import pagekey_help  # noqa: E402  (the page key, as Switchboard's own programs send it)
from switchboard.accounts import api
from fakes_accounts_api import install

A, B, C = 'ana@work.example', 'ana@home.example', 'ana@side.example'
T0 = 1_800_000_000
LINK = 'https://claude.ai/artifact/%s'
ID1, ID2, ID3, ID4, ID5 = ('a' * 20, 'b' * 20, 'c' * 20, 'd' * 20, 'e' * 20)


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace('+00:00', 'Z')


def result(t, text, **extra):
    block = dict({'type': 'tool_result', 'tool_use_id': 'tu1', 'content': text}, **extra)
    ask = json.dumps({'type': 'assistant', 'message': {'content': [
        {'type': 'tool_use', 'id': 'tu1', 'name': 'Artifact'}]}})       # a publish is only the Artifact tool's answer
    return ask + '\n' + json.dumps({'type': 'user', 'timestamp': iso(t), 'message': {'content': [block]}}) + '\n'


def state(**accounts):
    st = core.blank_state()
    for i, (email, more) in enumerate(accounts.items()):
        a = core.ensure_account(st, email)
        a.update(connected=True, slot=i + 1, **more)
    return st


class Timeline(unittest.TestCase):
    def test_owner_is_only_a_guess_near_a_switch(self):
        st = core.blank_state()
        st['timeline'] = [[A, T0], [B, T0 + 1000]]
        self.assertEqual(core.owner_seg(st, T0 - 5), (None, False))
        self.assertEqual(core.owner_seg(st, T0 + 500), (A, True))
        self.assertEqual(core.owner_seg(st, T0 + 950), (A, False))     # a switch is about to happen
        self.assertEqual(core.owner_seg(st, T0 + 1030), (B, False))    # a switch just happened
        self.assertEqual(core.owner_seg(st, T0 + 5000), (B, True))

    def test_an_account_is_named_by_its_email(self):
        # Short names ("work", or a part of the email) read as made-up titles; every surface shows the email
        st = state(**{A: {}, B: {'alias': 'home'}})
        self.assertEqual(core.name_of(st, A), A)
        self.assertEqual(core.name_of(st, B), B)                  # never the alias
        self.assertEqual(core.name_of(st, 'ana.lee@mail.example'), 'ana.lee@mail.example')
        self.assertEqual(core.name_of(st, None), '')


class History(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(dir=TMP)
        os.makedirs(os.path.join(self.root, 'proj'))
        self.file = os.path.join(self.root, 'proj', 'chat.jsonl')
        self.st = core.blank_state()
        self.st['timeline'] = [[A, T0], [B, T0 + 1000]]

    def write(self, *lines):
        with open(self.file, 'a') as f:
            f.writelines(lines)

    def owners(self):
        artifacts.scan(self.st, root=self.root)
        return {x['id']: x for x in self.st['artifacts']}

    def test_a_publish_names_the_account_that_was_active(self):
        self.write(result(T0 + 500, 'Published /tmp/site/index.html at ' + LINK % ID1))
        got = self.owners()[ID1]
        self.assertEqual((got['owner'], got['src'], got['name']), (A, 'published', 'site'))

    def test_a_publish_right_after_a_switch_is_a_guess(self):
        self.write(result(T0 + 1010, 'Published /tmp/notes.html at ' + LINK % ID1))
        got = self.owners()[ID1]
        self.assertEqual((got['owner'], got['src'], got['name']), (B, 'guess', 'notes'))

    def test_a_listing_beats_a_publish_and_brings_titles(self):
        self.write(result(T0 + 500, 'Published /tmp/a.html at ' + LINK % ID1),
                   result(T0 + 3000, '2 published artifacts\n- (mine) Budget — %s — updated 2027-01-02\n'
                                     '- (mine) Plan — %s\n' % (LINK % ID1, LINK % ID2)))
        got = self.owners()
        self.assertEqual((got[ID1]['owner'], got[ID1]['src'], got[ID1]['title']), (B, 'listed', 'Budget'))
        self.assertEqual((got[ID2]['owner'], got[ID2]['title']), (B, 'Plan'))

    def test_old_history_printed_in_a_chat_is_not_a_publish(self):
        self.write(result(T0 + 500, 'Here is the log:\nPublished /tmp/a.html at ' + LINK % ID3),
                   result(T0 + 500, 'Published /tmp/b.html at ' + LINK % ID4, is_error=True))
        self.assertEqual(self.owners(), {})

    def test_a_claude_doc_gets_its_title_from_the_request_that_made_it(self):
        ask = json.dumps({'type': 'assistant', 'message': {'content': [{
            'type': 'tool_use', 'id': 'tu1', 'name': 'mcp__claude_ai_Claude_Docs__batch',
            'input': {'container': {'kind': 'project', 'create': {'name': 'Quarter plan'}}}}]}}) + '\n'
        answer = '{"verdict":"applied","created":{"id":"x"},"frame":{"slug":"quarter-plan","url":"%s"}}' % (LINK % ID5)
        self.write(ask, result(T0 + 400, answer))
        got = self.owners()[ID5]
        self.assertEqual((got['owner'], got['title'], got['src']), (A, 'Quarter plan', 'published'))

    def test_only_new_lines_are_read_the_second_time(self):
        self.write(result(T0 + 500, 'Published /tmp/a.html at ' + LINK % ID1))
        self.owners()
        first = self.st['scan'][self.file]
        self.write(result(T0 + 600, 'Published /tmp/b.html at ' + LINK % ID2))
        got = self.owners()
        self.assertEqual(set(got), {ID1, ID2})
        self.assertGreater(self.st['scan'][self.file], first)

    def test_a_proven_owner_is_not_overwritten_by_history(self):
        item = artifacts.blank_item(ID1, T0)
        item.update(owner=C, src='seen', ot=T0)
        self.st['artifacts'] = [item]
        self.st['scan_v'] = artifacts.SCAN_V
        self.write(result(T0 + 500, 'Published /tmp/a.html at ' + LINK % ID1))
        self.assertEqual(self.owners()[ID1]['owner'], C)


class Pages(unittest.TestCase):
    def test_reading_a_tab(self):
        v = browser.page_verdict
        self.assertEqual(v('https://claude.ai/artifact/x', 'Page not found – Claude'), 'wrong')
        self.assertEqual(v('https://claude.ai/login?returnTo=%2Fartifact%2Fx', 'Claude'), 'signin')
        self.assertEqual(v('https://claude.ai/artifact/x', 'Claude'), None)          # still loading
        self.assertEqual(v('https://claude.ai/artifact/x', ''), None)
        self.assertEqual(v('https://claude.ai/artifact/x', 'Budget – Claude'), 'ok')
        self.assertEqual(v('https://claude.ai/code/artifact/x', 'Error budget – Claude'), 'ok')
        # A security check or an outage page that sits still is not a loaded page.
        self.assertEqual(v('https://claude.ai/artifact/x', 'Just a moment...'), None)
        self.assertEqual(v('https://claude.ai/artifact/x', 'Something went wrong'), None)
        self.assertEqual(v('https://claude.ai/artifact/x', 'Error'), None)
        self.assertEqual(v('https://claude.ai/new', 'Budget – Claude'), None)         # it wandered off
        self.assertEqual(browser.clean_title('Budget – Claude'), 'Budget')

    def test_artifact_links(self):
        self.assertEqual(artifacts.art_id(LINK % ID1), ID1)
        self.assertEqual(artifacts.art_id('https://claude.ai/code/artifact/' + ID2 + '?x=1'), ID2)
        self.assertIsNone(artifacts.art_id('https://claude.ai/chat/' + ID1))
        self.assertIsNone(artifacts.art_id('https://example.com/artifact/' + ID1))


class Profiles(unittest.TestCase):
    """Matching accounts to browser profiles from the browser's own claude.ai data."""

    def setUp(self):
        self.support = tempfile.mkdtemp(dir=TMP)
        self.chrome = os.path.join(self.support, 'Google', 'Chrome')
        browser.HINTS.update(at=0, key=None, value={})
        self.patch = mock.patch.object(browser, 'support', lambda: self.support)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def profile(self, folder, name, *orgs, default_name=False):
        d = os.path.join(self.chrome, folder, 'Local Storage', 'leveldb')
        os.makedirs(d)
        with open(os.path.join(d, '000003.log'), 'wb') as f:
            f.write(b'\x00_https://claude.ai\x00' + b'\x01'.join(o.encode() for o in orgs))
        path = os.path.join(self.chrome, 'Local State')
        cache = core.read_json(path)
        cache.setdefault('profile', {}).setdefault('info_cache', {})[folder] = {
            'name': name, 'is_using_default_name': default_name}
        with open(path, 'w') as f:
            json.dump(cache, f)

    def test_profile_names(self):
        self.profile('Profile 1', 'Work', 'org-work-0000000000')
        self.profile('Claude side', 'Person 2', 'org-side-0000000000', default_name=True)
        self.assertEqual(browser.profiles(), {'Profile 1': 'Work', 'Claude side': 'Claude side'})
        self.assertEqual(browser.new_profile_name('side'), 'Claude side 2')
        self.assertEqual(browser.new_profile_name('né w!'), 'Claude n w')

    def test_a_profile_used_for_one_account_goes_to_it(self):
        self.profile('Profile 1', 'Work', 'org-work-0000000000')
        self.profile('Profile 2', 'Home', 'org-home-0000000000')
        self.profile('Profile 3', 'Other', 'someone-elses-org-00')
        links = browser.guess_links({A: 'org-work-0000000000', B: 'org-home-0000000000', C: 'org-side-0000000000'}, set())
        self.assertEqual(links, {A: 'Profile 1', B: 'Profile 2'})

    def test_a_shared_profile_goes_to_the_account_left_over(self):
        self.profile('Profile 1', 'Everything', 'org-work-0000000000', 'org-home-0000000000')
        self.profile('Profile 2', 'Home', 'org-home-0000000000')
        links = browser.guess_links({A: 'org-work-0000000000', B: 'org-home-0000000000'}, set())
        self.assertEqual(links, {B: 'Profile 2', A: 'Profile 1'})

    def test_a_profile_already_linked_is_left_alone(self):
        self.profile('Profile 1', 'Work', 'org-work-0000000000')
        self.assertEqual(browser.guess_links({A: 'org-work-0000000000'}, {'Profile 1'}), {})


class Learning(unittest.TestCase):
    """What Switchboard writes down after a page loads, or doesn't."""

    def setUp(self):
        self.names = {'P1': 'Work', 'P2': 'Home', 'P3': 'Spare'}
        for target, value in ((browser, 'profiles'), (browser, 'hints')):
            p = mock.patch.object(target, value, return_value=self.names if value == 'profiles' else {})
            p.start()
            self.addCleanup(p.stop)

    def save(self, st, **item):
        it = artifacts.blank_item(ID1, T0)
        it.update(item)
        st['artifacts'] = [it]
        core.save(st)

    def test_the_known_owners_profile_is_tried_first(self):
        st = state(**{A: {'profile': 'P1'}, B: {'profile': 'P2'}})
        st['artifacts'] = [dict(artifacts.blank_item(ID1, T0), owner=B, src='published')]
        self.assertEqual(artifacts.candidates(st, st['artifacts'][0])[0], ('P2', B))
        # For a link nobody has seen, the account with the most pages is the best first try.
        self.assertEqual([p for p, _ in artifacts.candidates(st, None)], ['P2', 'P1'])
        st['artifacts'] = []
        st['active'] = A
        self.assertEqual([p for p, _ in artifacts.candidates(st, None)], ['P1', 'P2'])

    def test_a_page_that_loads_proves_the_owner(self):
        self.save(state(**{A: {'profile': 'P1', 'profile_src': 'hint'}}))
        self.assertEqual(artifacts.learn_ok(ID1, 'P1', A, 'Budget', []), A)
        st = core.load()
        it = st['artifacts'][0]
        self.assertEqual((it['owner'], it['src'], it['profile'], it['title']), (A, 'seen', 'P1', 'Budget'))
        self.assertEqual(st['accounts'][A]['profile_src'], 'checked')

    def test_history_corrects_a_guessed_profile(self):
        # Chat history says B published it, but it loaded in the profile we had guessed was A's.
        self.save(state(**{A: {'profile': 'P1', 'profile_src': 'hint'}, B: {}}), owner=B, src='published')
        self.assertEqual(artifacts.learn_ok(ID1, 'P1', A, 'Budget', []), B)
        st = core.load()
        self.assertEqual((st['accounts'][B]['profile'], st['accounts'][B]['profile_src']), ('P1', 'checked'))
        self.assertIsNone(st['accounts'][A]['profile'])

    def test_a_profile_you_chose_is_trusted_over_history(self):
        self.save(state(**{A: {'profile': 'P1', 'profile_src': 'you'}, B: {}}), owner=B, src='published')
        self.assertEqual(artifacts.learn_ok(ID1, 'P1', A, 'Budget', []), A)
        self.assertEqual(core.load()['accounts'][A]['profile'], 'P1')

    def test_history_never_moves_a_profile_you_chose_for_the_other_account(self):
        # B already has a profile of its own. Its page loading in A's guessed profile must not move B.
        self.save(state(**{A: {'profile': 'P1', 'profile_src': 'hint'}, B: {'profile': 'P2', 'profile_src': 'you'}}),
                  owner=B, src='listed')
        self.assertEqual(artifacts.learn_ok(ID1, 'P1', A, 'Budget', []), B)
        st = core.load()
        self.assertEqual((st['accounts'][B]['profile'], st['accounts'][B]['profile_src']), ('P2', 'you'))
        self.assertEqual(st['accounts'][A]['profile'], 'P1')
        self.assertEqual(st['artifacts'][0]['profile'], 'P1')

    def test_page_not_found_forgets_a_weak_owner_but_keeps_history(self):
        self.save(state(**{A: {'profile': 'P1', 'profile_src': 'hint'}}), owner=A, src='guess')
        artifacts.learn_wrong(ID1, 'P1', A)
        self.assertIsNone(core.load()['artifacts'][0]['owner'])
        self.save(state(**{A: {'profile': 'P1', 'profile_src': 'hint'}}), owner=A, src='listed')
        artifacts.learn_wrong(ID1, 'P1', A)
        self.assertEqual(core.load()['artifacts'][0]['owner'], A)

    def test_an_unlinked_profile_that_works_is_remembered(self):
        self.save(state(**{A: {}}))
        self.assertIsNone(artifacts.learn_ok(ID1, 'P3', None, 'Notes', []))
        it = core.load()['artifacts'][0]
        self.assertEqual((it['profile'], it['src']), ('P3', 'seen'))

    def test_a_clicked_link_opens_in_the_owners_profile_even_when_the_check_fails(self):
        # B published it, so B's profile is the likeliest. When the check itself breaks, open it there, not in
        # whichever window was used last.
        self.save(state(**{A: {'profile': 'P1'}, B: {'profile': 'P2'}}), owner=B, src='published')
        opened = []
        with mock.patch.object(artifacts, 'go', side_effect=RuntimeError('the browser did not answer')), \
                mock.patch.object(browser, 'open_url', lambda p, u, background=False: opened.append((p, u))):
            artifacts.go_quietly('https://claude.ai/artifact/' + ID1)
        self.assertEqual(opened, [('P2', 'https://claude.ai/artifact/' + ID1)])


class Engine(unittest.TestCase):
    def test_usage_in_the_pages_shape(self):
        reading = api.Reading('acct-1', (api.Window('5-hour', 12.0, T0 + 3600), api.Window('weekly', 85.0, T0 + 86400),
                                         api.Window('model:Opus', 65.0, T0 + 86400), api.Window('model:Idle', 0.0, None),
                                         api.Window('model:', 50.0, None)), T0 - 30, 'endpoint')
        usage = engine.usage_of(reading)
        self.assertEqual(dict(usage['session'], resets=core.parse_iso(usage['session']['resets'])),
                         {'pct': 12.0, 'resets': T0 + 3600, 'pace': None, 'lasts': None})
        self.assertEqual(usage['weekly']['pct'], 85.0)
        self.assertEqual([(x['name'], x['pct']) for x in usage['scoped']], [('Opus', 65.0), ('Idle', 0.0)])
        self.assertIsNone(usage['spend'])
        self.assertEqual(engine.models_of(reading), ['Opus', 'Idle'])
        self.assertIsNone(engine.usage_of(None))
        self.assertIsNone(engine.usage_of(api.Reading('acct-1', (), T0, 'none')))

    def test_with_the_login_unreadable_the_signed_in_account_still_shows(self):
        install(self).live_state = api.Live(state=api.UNREADABLE, account_id=None, email=None, proof='none')
        path = os.path.join(TMP, '.claude.json')
        with open(path, 'w') as f:
            json.dump({'oauthAccount': {'emailAddress': 'Ana@Work.example', 'organizationUuid': 'org-1'}}, f)
        with mock.patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': TMP}):
            rows = engine.rows()
        self.assertEqual([(r['email'], r['active'], r['unmanaged']) for r in rows], [('ana@work.example', True, True)])


class Page(unittest.TestCase):
    FACTS = {'profiles': {}, 'browser': {'name': 'Google Chrome', 'present': False, 'tabs_blocked': False},
             'engine': {'installed': False, 'claude': True},
             'links': {'installed': False, 'on': False, 'browser': None, 'home': ''},
             'auto': {'available': False, 'service': False, 'enabled': False, 'session_at': 95, 'weekly_at': 95},
             'setup_hidden': False, 'now': T0, 'demo': False}

    def test_a_brand_new_mac_shows_every_setup_step_undone(self):
        view = server.render(core.blank_state(), self.FACTS)
        self.assertEqual(view['accounts'], [])
        # the link app is an extra, added with --link-app: without it there is nothing to do for links, and browser
        # profiles are a step only while links are in use
        self.assertEqual(view['setup'], {'engine': False, 'accounts': False, 'profiles': True, 'links': True,
                                         'done': False, 'hidden': False})
        self.assertNotIn('repair', view)            # the engine finishes a cut-off switch by itself

    def test_setup_is_done_without_the_link_app_and_waits_for_it_once_it_is_there(self):
        st = state(**{A: {'profile': 'P1'}})
        facts = dict(self.FACTS, engine={'installed': True, 'claude': True}, profiles={'P1': 'Work'})
        self.assertTrue(server.render(st, facts)['setup']['done'])
        facts['links'] = dict(self.FACTS['links'], installed=True)          # there, not yet the default browser
        self.assertEqual(server.render(st, facts)['setup']['links'], False)
        self.assertFalse(server.render(st, facts)['setup']['done'])
        facts['links'] = dict(self.FACTS['links'], installed=True, on=True)
        self.assertTrue(server.render(st, facts)['setup']['done'])

    def test_accounts_that_left_are_not_shown(self):
        st = state(**{A: {'profile': 'P1'}, B: {}})
        st['accounts'][B]['connected'] = False
        self.assertEqual([a['email'] for a in server.render(st, self.FACTS)['accounts']], [A])

    def test_a_switch_you_just_made_is_not_undone_by_an_older_reading(self):
        st = state(**{A: {}, B: {}})
        st['active'], st['timeline'] = B, [[A, T0], [B, T0 + 500]]
        rows = [engine._row(id='acct-1', number=1, email=A, active=True), engine._row(id='acct-2', number=2, email=B)]
        server.apply_rows(st, rows, trust_active=False)
        self.assertEqual((st['active'], len(st['timeline'])), (B, 2))
        server.apply_rows(st, rows)
        self.assertEqual((st['active'], len(st['timeline'])), (A, 3))

    def test_the_week_is_cut_to_seven_days(self):
        st = core.blank_state()
        st['timeline'] = [[A, T0 - 20 * 86400], [B, T0 - 3600]]
        self.assertEqual(server.segments(st, T0), [[A, T0 - 7 * 86400, T0 - 3600], [B, T0 - 3600, T0]])

    def test_the_demo_has_the_same_shape_as_the_real_page(self):
        self.assertEqual(set(demo.view()), set(server.render(core.blank_state(), self.FACTS)))


class Server(unittest.TestCase):
    """The page answers itself and nobody else."""

    @classmethod
    def setUpClass(cls):
        core.DEMO = True
        cls.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        os.environ['SWITCHBOARD_PORT'] = str(cls.port)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        core.DEMO = False
        os.environ.pop('SWITCHBOARD_PORT', None)

    def ask(self, path, body=None, **headers):
        req = urllib.request.Request('http://127.0.0.1:%d%s' % (self.port, path),
                                     data=None if body is None else json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_the_page_and_its_state_load(self):
        self.assertEqual(self.ask('/')[0], 200)
        self.assertEqual(self.ask('/go')[0], 200)
        code, body = self.ask('/api/state')
        self.assertEqual(code, 200)
        self.assertTrue(json.loads(body)['demo'])
        self.assertEqual(self.ask('/nope')[0], 404)

    def test_only_your_own_bookmark_opens_a_link_without_a_click(self):
        self.assertIn(b'const auto = false;', self.ask('/go')[1])
        self.assertIn(b'const auto = false;', self.ask('/go?k=guess')[1])
        self.assertIn(b'const auto = true;', self.ask('/go?k=' + core.key())[1])

    def test_bad_input_is_refused_politely(self):
        core.DEMO = False                       # the real checks, which run before anything is touched
        try:
            ok = pagekey_help.with_key({'Content-Type': 'application/json', 'X-Switchboard': '1'})   # the real key
            self.assertEqual(self.ask('/api/go', {'url': 5}, **ok)[0], 400)
            self.assertEqual(self.ask('/api/go', {'url': 'https://example.com/artifact/' + ID1}, **ok)[0], 400)
            self.assertEqual(self.ask('/api/switch', {'email': ['x']}, **ok)[0], 400)
            self.assertEqual(self.ask('/api/switch', {'email': 'nobody@nowhere.example'}, **ok)[0], 400)
            self.assertEqual(self.ask('/api/alias', {'email': 'nobody@nowhere.example', 'alias': '--unset'}, **ok)[0], 400)
            self.assertEqual(self.ask('/api/auto', {'session_at': 'ninety'}, **ok)[0], 400)
            self.assertEqual(self.ask('/api/nothing', {'email': 'a@b.example'}, **ok)[0], 400)
        finally:
            core.DEMO = True

    def test_other_websites_are_refused(self):
        self.assertEqual(self.ask('/api/state', Host='evil.example')[0], 403)
        ok = {'Content-Type': 'application/json', 'X-Switchboard': '1'}
        self.assertEqual(self.ask('/api/switch', {'email': 'x@y.example'})[0], 403)                  # no header
        self.assertEqual(self.ask('/api/switch', {'email': 'x@y.example'}, Origin='https://evil.example', **ok)[0], 403)
        self.assertEqual(self.ask('/api/switch', {'email': 'maya@fernhill.example'}, **ok)[0], 200)
        self.assertEqual(json.loads(self.ask('/api/state')[1])['active'], 'maya@fernhill.example')

    def test_the_link_routers_header_may_open_links_and_nothing_else(self):
        old = {'Content-Type': 'application/json', 'X-Claude-Switch': '1'}
        core.DEMO = False                       # the real checks: a bad link gets past the door and is refused as a link
        try:
            self.assertEqual(self.ask('/api/go', {'url': 'https://example.com/artifact/' + ID1}, **old)[0], 400)
        finally:
            core.DEMO = True
        self.assertEqual(self.ask('/api/switch', {'email': 'maya@fernhill.example'}, **old)[0], 403)
        self.assertEqual(self.ask('/api/go', {'url': 'https://claude.ai/artifact/' + ID1},
                                  Origin='https://evil.example', **old)[0], 403)
        self.assertEqual(self.ask('/api/go', {'url': 'https://claude.ai/artifact/' + ID1},
                                  **{'Content-Type': 'application/json', 'X-Claude-Switch': '0'})[0], 403)


class PortTaken(unittest.TestCase):
    """Another program on the page's port: one plain line that says what to do, not a Python traceback."""

    def test_the_page_and_the_demo_say_the_port_is_taken(self):
        import socket
        holder = socket.socket()
        holder.bind(('127.0.0.1', 0))
        holder.listen(1)
        self.addCleanup(holder.close)
        taken = holder.getsockname()[1]
        for demo_on, words in ((False, 'Port %d is in use by another program, so the page can\'t start. Pick a free '
                                       'one: switchboard update --port 7801' % taken),
                               (True, 'Port %d is in use, so the demo can\'t start.' % taken)):
            with self.subTest(demo=demo_on), mock.patch.dict(os.environ, {'SWITCHBOARD_PORT': str(taken)}), \
                    mock.patch.object(core, 'DEMO', demo_on), mock.patch.object(server, 'loop', lambda: self.fail('ran')):
                with self.assertRaises(core.SwitchError) as said:
                    server.serve()
                self.assertTrue(str(said.exception).startswith(words), str(said.exception))


class SignIn(unittest.TestCase):
    """Add account on the page: Claude Code's own sign-in, opened in the account's browser profile, or in the default
    browser on a Mac where no Chrome, Brave or Edge has been opened (someone on Safari, Arc or Firefox)."""
    URL = 'https://claude.ai/oauth/authorize?code=true'

    def setUp(self):
        self.fake = install(self)               # the engine keeps what a sign-in folder holds (its own Keychain read)

    def test_without_chrome_the_page_asks_for_a_sign_in_in_the_default_browser(self):
        started = []
        with mock.patch.object(core, 'DEMO', False), mock.patch.object(browser, 'present', lambda: False), \
                mock.patch.object(connect, 'start', lambda email, profile: started.append((email, profile)) or 'new'):
            self.assertEqual(server.act('/api/connect', {'email': '', 'profile': '+new'})['key'], 'new')
            self.assertEqual(server.act('/api/connect', {'email': B, 'profile': 'No such profile'})['key'], 'new')
        self.assertEqual(started, [('', None), (B, None)])       # never a profile that no browser could open
        said = [e['text'] for e in core.load()['events']]
        self.assertIn('Opened the sign-in in your browser', said)
        self.assertIn('Opened the sign-in for %s in your browser' % B, said)

    def test_with_chrome_a_profile_must_exist(self):
        with mock.patch.object(core, 'DEMO', False), mock.patch.object(browser, 'present', lambda: True), \
                mock.patch.object(browser, 'profiles', lambda: {'P1': 'Work'}), \
                mock.patch.object(connect, 'start', lambda email, profile: self.fail('started a sign-in')):
            with self.assertRaises(core.SwitchError):
                server.act('/api/connect', {'email': '', 'profile': 'No such profile'})

    def test_the_link_opens_in_the_profile_or_else_in_the_default_browser(self):
        opened = []
        with mock.patch.object(browser, 'open_url', lambda p, u, background=False: opened.append(('profile', p, u))), \
                mock.patch.object(connect.subprocess, 'Popen', lambda args, **k: opened.append(('default', args))):
            with mock.patch.object(browser, 'present', lambda: True):
                connect.open_sign_in('Claude work', self.URL)
                connect.open_sign_in(None, self.URL)
            with mock.patch.object(browser, 'present', lambda: False):
                connect.open_sign_in('Claude work', self.URL)    # a profile, but no browser left to open it in
        self.assertEqual(opened, [('profile', 'Claude work', self.URL), ('default', ['/usr/bin/open', self.URL]),
                                  ('default', ['/usr/bin/open', self.URL])])

    def hand_over(self, st, email, profile):
        d = tempfile.mkdtemp(dir=TMP)
        with open(os.path.join(d, '.claude.json'), 'w') as f:
            json.dump({'oauthAccount': {'emailAddress': email}}, f)
        done = connect._hand_over(st, {'dir': d, 'email': '', 'profile': profile})
        self.assertIn(('add_login', d), self.fake.calls)
        return done

    def test_a_sign_in_in_the_default_browser_adds_the_account_and_keeps_its_profile(self):
        st = state(**{A: {'profile': 'P1', 'profile_src': 'you'}})
        self.assertTrue(self.hand_over(st, A, None))             # signed in again, in the default browser
        self.assertEqual((st['accounts'][A]['profile'], st['accounts'][A]['profile_src']), ('P1', 'you'))
        self.assertTrue(self.hand_over(st, B, None))             # a new account
        self.assertTrue(st['accounts'][B]['connected'])
        self.assertEqual(st['accounts'][B]['slot'], 2)
        self.assertFalse(st['accounts'][B].get('profile'))
        self.assertEqual(st['accounts'][A]['profile'], 'P1')     # nobody's profile was taken away
        self.assertTrue(self.hand_over(st, C, 'P1'))             # in a profile: that profile is this account's now
        self.assertEqual((st['accounts'][C]['profile'], st['accounts'][A]['profile']), ('P1', 'P1'))   # A chose it


if __name__ == '__main__':
    unittest.main()
