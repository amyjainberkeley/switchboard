import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The Memory page, the Claude Code switches, and the keys and the gateway wired into the page's server; the commands
# `switchboard keys` and `switchboard claude`; doctor and tidy.
# Every folder, key, login and chat here is made up; the Keychain is a fake one and the lane a local one.
import http.client
import inspect
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from http.server import ThreadingHTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, 'fixtures', 'packs', 'memory'))

import make_history as MH
import test_app
from switchboard import __main__ as cli
from switchboard import browser, claudecode, connect, core, demo, gateway as G, keys as K, lanes, linkapp
from switchboard import memorypage, server
from fakes_accounts_api import install as fake_engine
from switchboard.packs import common as PC, houserules, memory as PM
from fakes_gateway import Upstream, call, claude_headers, message_json, request_body
from test_keys import FakeKeychain

KEY = 'sk-ant-' + 'api03-made-up-key-0001'     # built from pieces: no key-shaped text in this file
KEY2 = 'sk-ant-' + 'api03-made-up-key-0002'
OAUTH = G.OAUTH_TOKEN_PREFIX + '01-made-up-login-token-0003'


def no_lanes():
    try:
        os.remove(core.data_path('gateway-lanes.json'))
    except FileNotFoundError:
        pass


def fresh_dir(prefix):
    d = os.path.realpath(tempfile.mkdtemp(prefix=prefix))
    return d


class Served(unittest.TestCase):
    """The real handler on a random loopback port, with the page's own headers."""

    def setUp(self):
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.port = self.httpd.server_address[1]
        env = mock.patch.dict(os.environ, {'SWITCHBOARD_PORT': str(self.port)})
        env.start()
        self.addCleanup(env.stop)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def http(self, method, path, body=None):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=20)
        try:
            headers = {'Host': '127.0.0.1:%d' % self.port}
            data = None
            if body is not None:
                data = json.dumps(body).encode()
                headers.update({'Content-Type': 'application/json', 'X-Switchboard': '1'})
            c.request(method, path, body=data, headers=headers)
            r = c.getresponse()
            raw = r.read().decode()
            return r.status, json.loads(raw), raw
        finally:
            c.close()


# ---------- 1. Memory: a fourth page, one search box ----------

class MemoryPage(Served):
    def setUp(self):
        super().setUp()
        self.cd = fresh_dir('switchboard-claude-')
        self.addCleanup(shutil.rmtree, self.cd, True)
        self.ids, _ = MH.build(os.path.join(self.cd, 'projects'))
        self.addCleanup(memorypage.KW.clear)
        memorypage.KW['claude_dir'] = self.cd
        PM.search_off()
        self.addCleanup(PM.search_off)
        memorypage.STATE.update(at=0, running=False, failed=False)
        self.addCleanup(memorypage.STATE.update, at=0, running=False, failed=False)

    def built(self):
        PM.update_index(os.path.join(self.cd, 'projects'))
        memorypage.STATE.update(at=time.time(), running=False)

    def test_the_first_search_never_waits_for_the_index(self):
        gate = threading.Event()
        real = PM.update_index
        self.addCleanup(gate.set)

        def slow(*a, **k):
            gate.wait(10)
            return real(*a, **k)
        with mock.patch.object(PM, 'update_index', slow):
            t = time.time()
            v = memorypage.view('redirect after signing in')
            self.assertLess(time.time() - t, 1.0)
            self.assertEqual((v['state'], v['results']), ('reading', []))
            self.assertTrue(memorypage.STATE['running'])
            gate.set()
            for _ in range(200):
                if not memorypage.STATE['running']:
                    break
                time.sleep(0.05)
        v = memorypage.view('redirect after signing in')
        self.assertEqual(v['state'], 'ready')
        self.assertTrue(v['results'])

    def test_rows_hold_title_date_folder_two_snippets_and_the_resume_command(self):
        self.built()
        status, v, raw = self.http('GET', '/api/memory?q=' + 'where%20did%20we%20fix%20the%20endless%20redirect%20after%20signing%20in%20with%20Google')
        self.assertEqual(status, 200)
        top = v['results'][0]
        self.assertEqual(top['title'], 'Login loops back to sign-in page')
        self.assertEqual(top['folder'], '/Users/x/code/webshop')
        self.assertRegex(top['date'], r'^\d{4}-\d{2}-\d{2}$')
        self.assertEqual(top['resume'], 'cd /Users/x/code/webshop && claude --resume %s' % self.ids['c01'])
        self.assertLessEqual(len(top['snippets']), 2)
        self.assertTrue(any('\x02' in s and '\x03' in s for s in top['snippets']))
        self.assertLessEqual(len(v['results']), memorypage.LIMIT)
        self.assertNotIn(MH.NOISE, raw)                         # tool output, thinking and helpers are not searched
        self.assertIsNone(v['health'])

    def test_no_query_no_rows_and_nothing_matches(self):
        self.built()
        self.assertEqual(memorypage.view('')['results'], [])
        self.assertEqual(memorypage.view('qwertyuiopasdf')['results'], [])

    def test_an_index_that_cannot_be_read_says_so(self):
        self.built()
        with mock.patch.object(PM, 'search', mock.Mock(side_effect=OSError('made up'))):
            self.assertEqual(memorypage.view('redirect')['state'], 'failed')
        memorypage.STATE.update(failed=True)
        self.assertEqual(memorypage.view('redirect')['state'], 'failed')

    def test_the_resume_command_is_quoted_and_refuses_odd_ids(self):
        self.assertEqual(memorypage.resume_command('/a b/c', 'abc-123'), "cd '/a b/c' && claude --resume abc-123")
        self.assertIsNone(memorypage.resume_command('relative/folder', 'abc'))
        self.assertIsNone(memorypage.resume_command('/x', '-rf'))
        self.assertIsNone(memorypage.resume_command('/x', 'a;b'))
        self.assertIsNone(memorypage.resume_command('', 'abc'))

    def test_the_health_line_only_when_a_memory_file_is_over_its_limit(self):
        self.assertIsNone(memorypage.health_line())
        folder = os.path.join(self.cd, 'projects', MH.project_dir(os.path.join(core.home(), 'work', 'site')), 'memory')
        os.makedirs(folder)
        with open(os.path.join(folder, 'MEMORY.md'), 'w') as f:
            f.write('\n'.join('- note %d' % i for i in range(150)) + '\n')
        self.assertIsNone(memorypage.health_line())             # under the limit: no line at all
        with open(os.path.join(folder, 'MEMORY.md'), 'w') as f:
            f.write('\n'.join('- note %d' % i for i in range(230)) + '\n')
        line = memorypage.health_line()
        self.assertIn("Claude Code's memory for work-site is over its limit: 30 lines of it do not load", line)
        self.assertNotIn(core.home(), line)

    def test_the_demo_answers_in_the_same_shape(self):
        with mock.patch.object(core, 'DEMO', True):
            v = memorypage.view('login release')
        self.assertEqual(v['state'], 'ready')
        self.assertTrue(all(r['resume'].startswith('cd ') and ' && claude --resume ' in r['resume'] for r in v['results']))
        for r in v['results']:
            self.assertTrue({'matched', 'alike', 'alike_last'} <= set(r), r)
        self.assertTrue(any(r['alike'] > 0 for r in v['results']))                    # one folded row
        self.assertTrue(any(r['matched'] and r['matched'] != r['date'] for r in v['results']))   # one long chat

    def test_rows_carry_where_the_match_is_and_how_many_were_folded(self):
        self.built()
        v = memorypage.view('where did we fix the endless redirect after signing in with Google')
        self.assertRegex(v['results'][0]['matched'], r'^\d{4}-\d{2}-\d{2}$')
        self.assertEqual(v['results'][0]['alike'], 0)
        folded = [{'session': 's1', 'title': 'Nightly check', 'date': '2026-11-01', 'last': '2026-11-12',
                   'folder': '/Users/x/ops', 'project': '-Users-x-ops', 'snippets': ['ran \x02check\x03'], 'score': 1.0,
                   'matched': '2026-11-03', 'alike': 4, 'alike_last': '2026-11-11'}]
        with mock.patch.object(PM, 'search', lambda *a, **k: folded):
            r = memorypage.view('check')['results'][0]
        self.assertEqual((r['date'], r['matched'], r['alike'], r['alike_last']), ('2026-11-12', '2026-11-03', 4, '2026-11-11'))


# ---------- 2. The Claude Code switches ----------

class ClaudeCodeSwitches(unittest.TestCase):
    def setUp(self):
        self.home = fresh_dir('switchboard-home-')
        self.addCleanup(shutil.rmtree, self.home, True)
        self.cd = os.path.join(self.home, '.claude')
        os.makedirs(self.cd)
        claudecode.KW.update(claude_dir=self.cd, home=self.home, environ={}, path='', battery=(True, 100),
                             free=10 ** 12)
        self.addCleanup(claudecode.KW.clear)
        from switchboard.packs import fetch, install
        dirs = mock.patch.object(fetch, 'COMMON_DIRS', ())        # never this Mac's own node, bun or uv
        dirs.start()
        self.addCleanup(dirs.stop)
        self.addCleanup(install.KW.clear)
        shutil.rmtree(PC.packs_dir(), True)
        self.addCleanup(shutil.rmtree, PC.packs_dir(), True)

    def person_installed_claude_mem(self):
        os.makedirs(os.path.join(self.cd, 'plugins'), exist_ok=True)
        with open(os.path.join(self.cd, 'plugins', 'installed_plugins.json'), 'w') as f:
            json.dump({'plugins': {PM.CM_PLUGIN: [{'scope': 'user'}]}}, f)

    def act(self, body):
        return server.act('/api/pack', body)

    def test_the_view_has_the_status_line_and_three_switches(self):
        v = server.claudecode_view()
        self.assertEqual(sorted(v), ['claudemem', 'codemap', 'house', 'statusline'])
        self.assertFalse(v['house']['on'])
        self.assertIn('How to work here', v['house']['text'])
        self.assertFalse(v['claudemem']['on'])
        self.assertNotIn('unproven', v['claudemem'])            # the honest sentences live in docs/how-it-works.md, Limits
        self.assertNotIn('unproven', v['codemap'])

    def test_house_rules_on_then_off_leaves_the_file_byte_for_byte(self):
        path = os.path.join(self.cd, 'CLAUDE.md')
        with open(path, 'wb') as f:
            f.write(b'# mine\r\nkeep this\r\n')
        r = self.act({'pack': 'house', 'on': True})
        self.assertIn('Adds the House rules block', r['says'])
        with open(path, 'rb') as f:
            data = f.read()
        self.assertTrue(data.startswith(houserules.START.encode()))
        self.assertTrue(server.claudecode_view()['house']['on'])
        self.act({'pack': 'house', 'on': False})
        with open(path, 'rb') as f:
            self.assertEqual(f.read(), b'# mine\r\nkeep this\r\n')

    def test_code_map_offers_its_install_plan_and_changes_nothing_until_agreed(self):
        # With no map tool here, the switch answers with codegraph's install plan; only that plan sent back
        # as consent installs it (tests/test_packs_install.py runs the install itself, on fakes).
        with mock.patch('subprocess.run') as run, mock.patch('subprocess.Popen') as popen:
            v = server.claudecode_view()['codemap']
            self.assertEqual(v['found'], [])
            self.assertEqual(v['add'], [])
            r = self.act({'pack': 'codemap', 'on': True})
            self.assertEqual(r['state'], 'plan')
            self.assertIn('Installs codegraph v1.6.2.', r['lines'])
            self.assertFalse(os.path.exists(os.path.join(self.cd, 'CLAUDE.md')))
            self.assertFalse(os.path.exists(os.path.join(self.cd, '.claude.json')))
            self.assertFalse(server.claudecode_view()['codemap']['on'])
            skill = os.path.join(self.cd, 'skills', 'graphify')
            os.makedirs(skill)
            with open(os.path.join(skill, 'SKILL.md'), 'w') as f:
                f.write('---\nname: graphify\n---\nmade up\n')
            self.assertEqual(server.claudecode_view()['codemap']['found'], ['graphify (skill)'])
            self.act({'pack': 'codemap', 'on': True})
            with open(os.path.join(self.cd, 'CLAUDE.md')) as f:
                self.assertIn('graphify', f.read())
            self.act({'pack': 'codemap', 'on': False})
            self.assertFalse(os.path.exists(os.path.join(self.cd, 'CLAUDE.md')))     # made for the block, so gone again
        run.assert_not_called()
        popen.assert_not_called()

    def test_claude_mem_writes_only_its_switch_keeps_a_cap_and_offers_the_install(self):
        settings = os.path.join(self.home, '.claude-mem', 'settings.json')
        r = self.act({'pack': 'claudemem', 'on': True, 'cap': 7})      # not installed: its plan, nothing written
        self.assertEqual(r['state'], 'plan')
        self.assertEqual(r['lines'][0], 'Installs claude-mem 15.18.3 and starts it.')
        self.assertFalse(os.path.exists(settings))
        self.person_installed_claude_mem()                             # installed by the person: on writes one key
        r = self.act({'pack': 'claudemem', 'on': True, 'cap': 7})
        with open(settings) as f:
            self.assertEqual(json.load(f), {'CLAUDE_MEM_SKIP_SUBAGENT_OBSERVATIONS': 'true'})
        v = server.claudecode_view()['claudemem']
        self.assertEqual((v['on'], v['installed'], v['cap']), (True, True, 7))
        self.act({'pack': 'claudemem', 'cap': 12})
        self.assertEqual(server.claudecode_view()['claudemem']['cap'], 12)
        for bad in (0, 101, 'ten', True):
            with self.assertRaises(core.SwitchError):
                self.act({'pack': 'claudemem', 'cap': bad})
        self.act({'pack': 'claudemem', 'on': False})
        self.assertFalse(os.path.exists(settings))                # Switchboard made the file, so it goes
        self.assertFalse(server.claudecode_view()['claudemem']['on'])

    def test_a_new_cap_needs_the_switch_on(self):
        with self.assertRaises(core.SwitchError):
            self.act({'pack': 'claudemem', 'cap': 5})

    def test_on_or_off_must_be_said(self):
        for body in ({'pack': 'house'}, {'pack': 'house', 'on': 'yes'}, {'pack': 'house', 'cap': 5},
                     {'pack': 'codemap', 'cap': 5}):
            with self.assertRaises(core.SwitchError):
                self.act(body)
        self.assertFalse(os.path.exists(os.path.join(self.cd, 'CLAUDE.md')))

    def test_the_cap_check_waits_for_a_click_and_never_runs_twice(self):
        claudecode.CAP.update(at=0, running=True)
        self.addCleanup(claudecode.CAP.update, at=0, running=False)
        with claudecode.LOCK:
            self.assertIsNone(claudecode.cap_tick())          # a click holds the lock: this pass steps aside
        self.assertEqual(claudecode.CAP, {'at': 0, 'running': False})

    def test_its_share_is_a_usage_line_only_while_managed_and_installed(self):
        self.assertIsNone(claudecode.share())
        self.act({'pack': 'claudemem', 'on': True})
        self.assertIsNone(claudecode.share())                     # not installed: only its plan was shown
        self.person_installed_claude_mem()
        self.assertIsNone(claudecode.share())                     # installed, but not managed
        self.act({'pack': 'claudemem', 'on': True})
        self.assertEqual(claudecode.share(), {'share': 0.0, 'cap': PM.CAP_DEFAULT, 'paused': False})

    def test_tidy_puts_everything_back(self):
        self.act({'pack': 'house', 'on': True})
        self.person_installed_claude_mem()
        self.act({'pack': 'claudemem', 'on': True})
        out = io.StringIO()
        with redirect_stdout(out):
            cli.tidy()
        self.assertIn('House rules: put back as it was.', out.getvalue())
        self.assertIn('claude-mem settings: put back as it was.', out.getvalue())
        v = server.claudecode_view()
        self.assertFalse(v['house']['on'] or v['claudemem']['on'])


# ---------- 3. Keys and the gateway, inside the page's server ----------

class KeysAndGateway(Served):
    def setUp(self):
        super().setUp()
        self.kc = FakeKeychain()
        lanes.KEYS['store'] = K.Keys(runner=self.kc)
        self.addCleanup(lanes.KEYS.update, store=None)
        lanes.KW.update(port=0, options={'hold': 2, 'connect_timeout': 2})
        self.addCleanup(lanes.KW.clear)
        self.addCleanup(lanes.stop)
        self.addCleanup(core.set_setting, 'gateway_on', False)
        no_lanes()
        self.addCleanup(no_lanes)
        real_turn = lanes.turn

        def turn(on):                                     # a test lane is always this Mac: nothing leaves it
            assert all(x['base_url'].startswith('http://127.0.0.1:') for x in lanes.lanes()) or not on
            return real_turn(on)
        patch = mock.patch.object(lanes, 'turn', turn)
        patch.start()
        self.addCleanup(patch.stop)

    def lane_file(self):
        with open(core.data_path('gateway-lanes.json')) as f:
            return f.read()

    def add_local_lane(self, up):
        lanes.store().add('local', KEY, kind='custom', base_url=up.url, auth='x-api-key')

    def test_a_key_goes_once_to_the_keychain_on_stdin_and_is_never_shown(self):
        status, v, raw = self.http('POST', '/api/keys', {'name': 'console', 'key': KEY})
        self.assertEqual(status, 200, raw)
        self.assertEqual((v['name'], v['last4']), ('console', KEY[-4:]))
        self.assertNotIn(KEY, raw)
        adds = [c for c in self.kc.calls if c[0] == [K.SECURITY, '-i']]
        self.assertEqual(len(adds), 1)
        self.assertIn(KEY, adds[0][1])                            # the key travelled on standard input
        self.assertTrue(all(KEY not in ' '.join(c[0]) for c in self.kc.calls))     # never as an argument
        _, listed, raw = self.http('GET', '/api/keys')
        self.assertNotIn(KEY, raw)
        self.assertEqual([(x['name'], x['last4']) for x in listed['lanes']], [('console', KEY[-4:])])
        self.assertNotIn(KEY, self.lane_file())
        self.assertNotIn(KEY, json.dumps(core.load()))
        _, st, raw = self.http('GET', '/api/state')
        self.assertEqual(st['keys']['count'], 1)
        self.assertNotIn(KEY, raw)

    def test_a_login_token_is_refused_as_a_key(self):
        status, v, raw = self.http('POST', '/api/keys', {'name': 'login', 'key': OAUTH})
        self.assertEqual(status, 400)
        self.assertIn('subscription login token', v['error'])
        self.assertNotIn(OAUTH, raw)
        self.assertEqual(self.kc.items, {})

    def test_remove_asks_first_and_reorder_moves_one_place(self):
        self.http('POST', '/api/keys', {'name': 'one', 'key': KEY})
        self.http('POST', '/api/keys', {'name': 'two', 'key': KEY2})
        _, v, _ = self.http('POST', '/api/keys', {'name': 'two', 'step': -1})
        self.assertEqual([x['name'] for x in v['keys']['lanes']], ['two', 'one'])
        _, v, _ = self.http('POST', '/api/keys', {'name': 'one', 'remove': True, 'confirm': True})
        self.assertEqual(v['state'], 'confirm')                   # a yes the server did not ask for counts as a question
        self.assertIn('Remove the key one?', v['line'])
        _, v, _ = self.http('POST', '/api/keys', {'name': 'one', 'remove': True, 'confirm': True})
        self.assertEqual([x['name'] for x in v['keys']['lanes']], ['two'])
        self.assertNotIn((K.SERVICE, 'one'), self.kc.items)

    def test_the_gateway_needs_a_key(self):
        status, v, _ = self.http('POST', '/api/gateway', {'on': True})
        self.assertEqual((status, v['error']), (400, 'Add a key first.'))

    def test_removing_the_last_key_turns_the_gateway_off(self):
        lanes.store().add('local', KEY, kind='custom', base_url='http://127.0.0.1:9', auth='x-api-key')
        self.http('POST', '/api/gateway', {'on': True})
        self.http('POST', '/api/keys', {'name': 'local', 'remove': True})
        _, v, _ = self.http('POST', '/api/keys', {'name': 'local', 'remove': True, 'confirm': True})
        self.assertEqual((v['keys']['lanes'], v['keys']['on']), ([], False))
        self.assertFalse(core.settings().get('gateway_on'))

    def test_the_switch_runs_the_gateway_in_the_server_and_counts_each_lane(self):
        up = Upstream(lambda h, rec: h.send_json(200, message_json()))
        self.addCleanup(up.stop)
        self.add_local_lane(up)
        _, v, _ = self.http('POST', '/api/gateway', {'on': True})
        port = v['keys']['port']
        self.assertTrue(v['keys']['on'])
        status, headers, _ = call(port, body=request_body(stream=False), headers=claude_headers(secret=G.secret()))
        self.assertEqual((status, headers.get('x-switchboard-lane')), (200, 'local'))
        self.assertEqual(up.count(), 1)
        for _ in range(100):
            _, v, _ = self.http('GET', '/api/keys')
            if v['lanes'][0]['counts']['answered']:
                break
            time.sleep(0.02)
        self.assertEqual(v['lanes'][0]['counts']['answered'], 1)
        self.assertTrue(lanes.answers(port))
        _, st, _ = self.http('GET', '/api/state')
        self.assertEqual(st['keys'], {'count': 1, 'on': True, 'command': 'switchboard claude'})
        self.http('POST', '/api/gateway', {'on': False})
        self.assertFalse(lanes.answers(port))
        self.assertFalse(core.settings().get('gateway_on'))

    def test_a_subscription_login_is_refused_end_to_end_and_nothing_is_forwarded(self):
        up = Upstream(lambda h, rec: h.send_json(200, message_json()))
        self.addCleanup(up.stop)
        self.add_local_lane(up)
        for k in ('gateway_allow_subscription', 'allow_subscription', 'subscription', 'refuse_subscription'):
            core.set_setting(k, k != 'refuse_subscription')    # nothing a settings file says can open it
        _, v, _ = self.http('POST', '/api/gateway', {'on': True})
        port = v['keys']['port']
        secret = G.secret()
        for headers in ([('Authorization', 'Bearer ' + OAUTH)], [('x-api-key', OAUTH)],
                        claude_headers(secret=secret, extra=[('anthropic-beta', 'oauth-2025-04-20')]),
                        claude_headers(secret=OAUTH)):
            status, got, body = call(port, body=request_body(), headers=headers)
            self.assertEqual(status, 403, headers)
            self.assertEqual(got.get('x-switchboard-refused'), 'subscription-login')
            self.assertIn('never sent through the gateway', json.loads(body)['error']['message'])
        self.assertEqual(up.count(), 0)
        params = ' '.join(inspect.signature(G.Gateway).parameters).lower()
        for word in ('subscription', 'oauth', 'login', 'allow'):
            self.assertNotIn(word, params)                       # and nothing in the code can be asked to

    def test_it_comes_back_after_a_restart_while_on(self):
        lanes.store().add('local', KEY, kind='custom', base_url='http://127.0.0.1:9', auth='x-api-key')
        core.set_setting('gateway_on', True)
        lanes.resume()
        self.assertTrue(lanes.view()['on'])
        lanes.stop()
        self.assertFalse(lanes.view()['on'])


# ---------- the command line: keys, claude, doctor ----------

class CommandLine(unittest.TestCase):
    def setUp(self):
        fake_engine(self)           # the account engine's fake: doctor and tidy --all never reach the real one
        self.kc = FakeKeychain()
        lanes.KEYS['store'] = K.Keys(runner=self.kc)
        self.addCleanup(lanes.KEYS.update, store=None)
        no_lanes()
        self.addCleanup(no_lanes)

    def run_cli(self, *args, stdin=''):
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(sys, 'stdin', io.StringIO(stdin)):
            code = cli.main(list(args))
        return code, out.getvalue()

    def test_keys_add_list_remove(self):
        code, out = self.run_cli('keys', 'add', 'console', stdin=KEY + '\n')
        self.assertEqual(code, 0)
        self.assertEqual(out, 'Added console (ends in %s) to your Keychain.\n' % KEY[-4:])
        code, out = self.run_cli('keys', 'list')
        self.assertEqual(out, '1. console  (anthropic, ends in %s)\n' % KEY[-4:])
        self.assertNotIn(KEY, out)
        with self.assertRaises(core.SwitchError):
            self.run_cli('keys', 'remove', 'console')            # not a terminal: needs --yes
        code, out = self.run_cli('keys', 'remove', 'console', '--yes')
        self.assertIn('Removed console from your Keychain.', out)
        self.assertEqual(self.kc.items, {})

    def test_claude_needs_a_key_and_the_gateway_then_starts_on_it(self):
        with self.assertRaises(core.SwitchError) as e:
            self.run_cli('claude')
        self.assertIn('No keys yet', str(e.exception))
        lanes.add('console', KEY)
        with mock.patch.object(lanes, 'answers', lambda port=None, timeout=2: False), self.assertRaises(core.SwitchError) as e:
            self.run_cli('claude')
        self.assertIn('The keys gateway is off', str(e.exception))
        seen = {}

        def execve(exe, argv, env):
            seen.update(exe=exe, argv=argv, env=env)
        env = {'ANTHROPIC_API_KEY': 'made-up', 'CLAUDE_CODE_OAUTH_TOKEN': 'made-up', 'PATH': '/usr/bin'}
        with mock.patch.object(lanes, 'answers', lambda port=None, timeout=2: True), \
                mock.patch.object(connect, 'claude_bin', lambda: '/made/up/claude'), \
                mock.patch.object(os, 'execve', execve), mock.patch.dict(os.environ, env), \
                mock.patch.object(sys, 'stderr', io.StringIO()):
            self.run_cli('claude', '--resume', 'abc')
        self.assertEqual(seen['argv'], ['/made/up/claude', '--resume', 'abc'])
        self.assertEqual(seen['env']['ANTHROPIC_BASE_URL'], 'http://127.0.0.1:%d' % G.port())
        self.assertEqual(seen['env']['ANTHROPIC_AUTH_TOKEN'], G.secret())
        self.assertNotIn('ANTHROPIC_API_KEY', seen['env'])
        self.assertNotIn('CLAUDE_CODE_OAUTH_TOKEN', seen['env'])

    def test_doctor_covers_memory_the_switches_the_keys_and_the_apps(self):
        lanes.add('console', KEY)
        before = len(self.kc.calls)
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(linkapp, 'status', lambda fresh=False: {'installed': False, 'on': False}), \
                mock.patch.object(browser, 'present', lambda: False), mock.patch.object(cli, 'page_running', lambda: False), \
                mock.patch.object(connect, 'claude_bin', lambda: None), mock.patch.object(lanes, 'answers', lambda *a, **k: False):
            cli.doctor()
        text = out.getvalue()
        for words in ('Memory search: not built yet', 'House rules: off', 'Code map: off', 'Watching chats with claude-mem: off',
                      ' ok  Key console (ends in %s) in your Keychain' % KEY[-4:], 'Keys gateway: off',
                      'Menu bar: not installed', 'Link app (~/Applications/Switchboard Links.app)',
                      'It needs the link app, above. Until then, use `switchboard open LINK`.'):
            self.assertIn(words, text)
        self.assertNotIn('Turn it on with: switchboard links on', text)    # nothing to turn on without the link app
        self.assertNotIn(KEY, text)
        self.assertEqual([c[0][1:] for c in self.kc.calls[before:]],
                         [['find-generic-password', '-s', K.SERVICE, '-a', 'console']])   # never the key itself (-w)

    def test_tidy_all_takes_the_keys_out_of_the_keychain(self):
        lanes.add('console', KEY)
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main(['tidy', '--all'])
        self.assertIn('Key console: removed from your Keychain.', out.getvalue())
        self.assertEqual((self.kc.items, lanes.lanes()), ({}, []))

    def test_tidy_all_names_a_key_it_could_not_remove_and_fails(self):
        lanes.add('console', KEY)
        self.kc.fail_delete = True
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(['tidy', '--all'])
        self.assertEqual(code, 1)
        self.assertIn('Key console: still in your Keychain', out.getvalue())
        self.assertIn('switchboard keys remove console --yes', out.getvalue())
        self.assertEqual([x['name'] for x in lanes.lanes()], ['console'])     # its record stays, so it can be found

    def test_version(self):
        self.assertRegex(core.VERSION, r'^\d+\.\d+\.\d+\Z')
        self.assertEqual(server.render(*demo_state())['version'], core.VERSION)


def demo_state():
    demo.STATE.clear()
    demo.FACTS.clear()
    demo._ready()
    return demo.STATE, dict(demo.FACTS, now=time.time())


# ---------- the page itself ----------

def day_words(iso):
    """The page's day for a past chat: Today, Yesterday, a weekday this week, else "Nov 4"."""
    import datetime as dt
    d = dt.date.fromisoformat(iso)
    days = (dt.date.today() - d).days
    return 'Today' if days == 0 else 'Yesterday' if days == 1 else d.strftime('%a') if 1 < days < 7 else d.strftime('%b ') + str(d.day)


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class MemoryAndKeysInThePage(test_app.ChromePage):
    def search(self, words):
        return self.run_page("""
      location.hash = '#activity'; await wait(800);              // past chats are in Activity
      const q = $('#mq'); q.focus(); q.value = %s; q.dispatchEvent(new Event('input', {bubbles: true})); await wait(1500);
      done(all('#results .res').map((r) => ({title: r.querySelector('.res-title').textContent, meta: r.querySelector('.res-meta').textContent,
        buttons: r.querySelectorAll('button').length, copy: (r.querySelector('.copy') || {}).textContent || null,
        cmd: r.querySelector('.copy') ? r.querySelector('.copy').dataset.text : null})));
""" % json.dumps(words))

    def test_past_chats_are_one_search_field_with_rows_and_one_copy_action_each(self):
        rows = self.search('login release')
        mem = demo.memory('login release')['results']
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]['title'], 'Fix the login loop on Safari')
        self.assertTrue(rows[0]['meta'].startswith('~/work/website \u00b7 '))
        self.assertEqual(set(r['buttons'] for r in rows), {1})                      # one action per row
        self.assertEqual(set(r['copy'] for r in rows), {'Copy resume command'})
        self.assertTrue(rows[0]['cmd'].startswith('cd /Users/me/work/website && claude --resume '))
        self.assertEqual([r['cmd'] for r in rows], [m['resume'] for m in mem])

    def test_a_row_says_the_day_of_its_best_match(self):
        rows = dict((r['title'], r) for r in self.search('login release'))
        long_chat = next(r for r in demo.memory('login release')['results'] if r['matched'] != r['date'])
        self.assertEqual(rows[long_chat['title']]['meta'], '%s \u00b7 %s' % (long_chat['folder'], day_words(long_chat['matched'])))
        self.assertNotIn(day_words(long_chat['date']), rows[long_chat['title']]['meta'])

    def test_copy_says_copied_for_a_moment_and_no_match_says_so(self):
        v = self.run_page("""
      if (!navigator.clipboard) Object.defineProperty(navigator, 'clipboard', {value: {}});
      navigator.clipboard.writeText = (t) => { window.__copied = t; return Promise.resolve(); };
      location.hash = '#activity'; await wait(800);
      const q = $('#mq'); q.focus(); q.value = 'login'; q.dispatchEvent(new Event('input', {bubbles: true})); await wait(1500);
      const b = $('#results .copy'); b.click(); await wait(200);
      const now = b.textContent; await wait(1600);
      const later = $('#results .copy').textContent;
      q.value = 'zzzz nothing'; q.dispatchEvent(new Event('input', {bubbles: true})); await wait(1500);
      const none = $('#results').textContent;
      q.value = ''; q.dispatchEvent(new Event('input', {bubbles: true})); await wait(800);
      done({now, later, copied: window.__copied, none, empty: $('#results').innerHTML});
""")
        self.assertEqual((v['now'], v['later']), ('Copied', 'Copy resume command'))
        self.assertTrue(v['copied'].startswith('cd /Users/me/work/website && claude --resume '))
        self.assertEqual(v['none'], 'No past chats match')
        self.assertEqual(v['empty'], '')

    def test_the_claude_code_switches_live_in_settings_and_more_settings(self):
        # Settings is a view of its own, grouped by what each setting is for, each with one short line
        v = self.run_page("""
      await wait(300);
      const hidden = $('#sec-settings').hidden;
      location.hash = '#settings'; await wait(1100);
      const main = all('#g-working .r-title').map((r) => r.textContent), more = all('#g-code .r-title').map((r) => r.textContent);
      const items = all('#chips .chip').map((b) => b.textContent);
      $('[data-do="houseView"]').click(); await wait(300);
      done({main, hidden, more, items, current: ($('#nav [aria-current="page"]') || {}).textContent || '',
            replaces: $('#row-statusline .r-ctl').textContent, rules: ($('#row-house pre.view') || {}).textContent || '',
            cap: ($('#cmCap') || {}).value, text: $('#sec-settings').textContent, titles: all('#g-code .r-desc').map((x) => x.textContent)});
""")
        self.assertEqual(v['main'], ['Keep Mac awake', 'Notifications', 'Claude in Chrome'])
        self.assertTrue(v['hidden'])                                     # its own view, shown when asked for
        self.assertTrue(v['current'].startswith('Settings'), v['current'])
        self.assertIn('Replaces yours', v['replaces'])
        # the Claude Code switches (opening links as their owner is always on, so it has no switch here)
        self.assertEqual(v['more'], ['Status line', 'Sonnet for helpers', 'House rules', 'Code map', 'claude-mem'])
        self.assertEqual(v['items'], ['All', 'Switching', 'While Claude works', 'In Claude Code', 'Links', 'Keys', 'Accounts'])
        self.assertIn('## How to work here', v['rules'])
        self.assertEqual(v['cap'], '10')
        for gone in ('Not yet tried', 'Found by looking at files only', 'tested on recorded hook inputs', 'Beta'):
            self.assertNotIn(gone, v['text'])
        for title in v['titles']:                                        # one short line saying what it does
            self.assertTrue(title and title.count('.') <= 2 and len(title) < 110, title)

    def test_api_keys_adds_a_key_once_and_clears_the_field(self):
        v = self.run_page("""
      location.hash = '#settings'; await wait(1100);
      const lanes = all('#row-apikeys .klist li > .lab').map((x) => x.textContent);
      const box = $('[data-k="kkey"]'), name = $('[data-k="kname"]');
      const kind = [box.type, box.getAttribute('autocomplete')];
      name.value = 'spare'; box.value = 'made-up-value-for-the-page';
      $('[data-do="keyAdd"]').click();
      const after = box.value;
      await wait(500);
      done({lanes, kind, after, sent: sent.filter((x) => x[0] === '/api/keys').map((x) => Object.keys(x[1]).sort()),
            gw: !!$('#sec-settings [data-do="gateway"]'), open: !!$('#g-keys') && !$('#sec-settings').hidden});
""")
        self.assertTrue(v['open'])
        self.assertEqual(v['lanes'][0], 'console \u20269f2c 41 answered, 2 passed on')   # name, last four, counts
        self.assertEqual(v['kind'], ['password', 'off'])
        self.assertEqual(v['after'], '')                                  # the field is empty as soon as it is sent
        self.assertEqual(v['sent'], [['key', 'name']])                    # sent once
        self.assertTrue(v['gw'])

    def test_the_keys_command_shows_only_when_every_account_is_at_its_line_and_a_key_is_added(self):
        demo._ready()
        for a in demo.STATE['accounts'].values():
            a['usage']['weekly']['pct'] = 97
        script = """
      await wait(500);                                       // the line is drawn with the accounts
      done({below: ($('#below') || {}).textContent || '', code: ($('#below code') || {}).textContent || ''});
"""
        v = self.run_page(script)
        self.assertEqual(v['code'], 'switchboard claude')
        self.assertIn('Every account is at its line. On your keys:', v['below'])
        demo.FACTS['keys']['count'] = 0
        v = self.run_page(script)
        self.assertEqual(v['code'], '')
        self.assertNotIn('On your keys', v['below'])


if __name__ == '__main__':
    unittest.main()
