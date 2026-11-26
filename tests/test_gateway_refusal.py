import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The rule that cannot bend: the gateway is for API keys only. A request that carries a Claude
# subscription login is refused, and nothing is forwarded.
#
# No real key, no real login: every credential here is made up.
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import core, gateway as G
from fakes_gateway import GatewayCase, call, claude_headers, lane, request_body, stream_of

SECRET = 'sbgw-made-up-secret-for-tests-0123456789'
OAUTH = G.OAUTH_TOKEN_PREFIX + '01-made-up-not-a-real-token'      # built here, so no token-shaped text is in the file
KEY = 'sk-ant-' + 'api03-made-up-key-for-refusal-tests'


class SubscriptionLogin(unittest.TestCase):
    def test_oauth_token_in_authorization_is_a_subscription_login(self):
        self.assertTrue(G.subscription_login([('Authorization', 'Bearer ' + OAUTH)]))

    def test_oauth_token_in_x_api_key_is_a_subscription_login(self):
        self.assertTrue(G.subscription_login([('x-api-key', OAUTH)]))

    def test_oauth_capability_in_anthropic_beta_is_a_subscription_login(self):
        for beta in ('oauth-2025-04-20', 'context-1m-2025-08-07, oauth-2025-04-20', ' OAuth-anything '):
            self.assertTrue(G.subscription_login([('anthropic-beta', beta)]), beta)

    def test_it_counts_even_next_to_the_right_secret(self):
        pairs = [('Authorization', 'Bearer ' + SECRET), ('anthropic-beta', 'oauth-2025-04-20')]
        self.assertTrue(G.subscription_login(pairs))
        self.assertEqual(G.secret_problem(pairs, SECRET), '')     # the secret is right; the login still refuses

    def test_every_header_is_looked_at_not_only_the_first(self):
        pairs = [('anthropic-beta', 'interleaved-thinking-2025-05-14'), ('Anthropic-Beta', 'oauth-2025-04-20')]
        self.assertTrue(G.subscription_login(pairs))
        pairs = [('Authorization', 'Bearer ' + SECRET), ('authorization', 'Bearer ' + OAUTH)]
        self.assertTrue(G.subscription_login(pairs))

    def test_an_api_key_request_is_not_a_subscription_login(self):
        pairs = [('Authorization', 'Bearer ' + SECRET), ('anthropic-version', '2023-06-01'),
                 ('anthropic-beta', 'interleaved-thinking-2025-05-14,context-management-2025-06-27')]
        self.assertEqual(G.subscription_login(pairs), '')

    def test_the_message_says_why_and_what_to_do(self):
        text = G.SUBSCRIPTION_REFUSAL % G.subscription_login([('Authorization', 'Bearer ' + OAUTH)])
        for words in ('subscription login', 'never sent', 'API keys', 'switchboard claude'):
            self.assertIn(words, text)
        self.assertNotIn(OAUTH, text)


class TheRuleCannotBeSwitchedOff(unittest.TestCase):
    def test_the_check_takes_nothing_but_the_headers(self):
        import inspect
        self.assertEqual(list(inspect.signature(G.subscription_login).parameters), ['pairs'])

    def test_no_switch_exists_in_the_module(self):
        with open(G.__file__) as f:
            source = f.read().lower()
        for word in ('allow_subscription', 'allow_oauth', 'skip_refusal', 'subscription_ok', 'unsafe'):
            self.assertNotIn(word, source)

    def test_the_module_never_names_claude_codes_stored_login(self):
        with open(G.__file__) as f:
            source = f.read()
        for place in ('Claude Code-credentials', '.credentials.json', 'find-generic-password'):
            self.assertNotIn(place, source)


class Secret(unittest.TestCase):
    def test_missing(self):
        self.assertIn('no credential', G.secret_problem([('anthropic-version', '2023-06-01')], SECRET))

    def test_wrong(self):
        self.assertIn('not the gateway secret', G.secret_problem([('Authorization', 'Bearer nope')], SECRET))
        self.assertTrue(G.secret_problem([('Authorization', 'Basic ' + SECRET)], SECRET))
        self.assertTrue(G.secret_problem([('x-api-key', SECRET + 'x')], SECRET))

    def test_a_tab_or_spaces_after_bearer(self):
        self.assertEqual(G.secret_problem([('Authorization', 'Bearer\t' + SECRET)], SECRET), '')
        self.assertTrue(G.subscription_login([('Authorization', 'bearer \t ' + OAUTH)]))

    def test_right_in_either_header(self):
        self.assertEqual(G.secret_problem([('Authorization', 'Bearer ' + SECRET)], SECRET), '')
        self.assertEqual(G.secret_problem([('x-api-key', SECRET)], SECRET), '')
        self.assertEqual(G.secret_problem([('Authorization', 'bearer  ' + SECRET), ('X-Api-Key', SECRET)], SECRET), '')

    def test_every_credential_must_be_the_secret(self):
        self.assertTrue(G.secret_problem([('Authorization', 'Bearer ' + SECRET), ('x-api-key', 'sk-other')], SECRET))

    def test_compared_as_fixed_length_digests(self):
        import inspect
        source = inspect.getsource(G.secret_problem)
        self.assertIn('compare_digest', source)
        self.assertIn('_digest(token)', source)


# What this process opens or starts while the recorder is on (an audit hook cannot be removed, so it stays
# installed and only records between start and stop, for the thread that turned it on and the gateway's threads).
RECORDING = threading.Event()
SEEN = []


def _recorder(event, args):
    if RECORDING.is_set() and event in ('open', 'subprocess.Popen', 'os.system', 'os.posix_spawn', 'os.exec'):
        SEEN.append((event, str(args[0])[:300] if args else ''))


sys.addaudithook(_recorder)


def answer_stream(h, record):
    h.send_events(stream_of('fine'))


class RefusedOverHttp(GatewayCase):
    """Each refusal, through the real server: the right status and words, and not one request upstream."""

    def setUp(self):
        self.up = self.upstream(answer_stream)
        self.gw = self.gateway([lane('a', self.up)])

    def refused(self, status, tag, **kw):
        got_status, headers, body = call(self.gw.port, **kw)
        self.assertEqual(got_status, status)
        self.assertEqual(headers.get('x-switchboard-refused'), tag)
        self.assertEqual(headers.get('x-should-retry'), 'false')
        self.assertEqual(self.up.count(), 0)                 # nothing was forwarded
        if kw.get('method') != 'HEAD':
            err = json.loads(body)
            self.assertEqual(err['type'], 'error')
            return err['error']['message']
        return ''

    def test_a_subscription_login_in_authorization(self):
        message = self.refused(403, 'subscription-login', body=request_body(),
                               headers=[('Authorization', 'Bearer ' + OAUTH), ('anthropic-version', '2023-06-01')])
        self.assertIn('subscription login', message)
        self.assertNotIn(OAUTH, message)

    def test_a_subscription_login_in_x_api_key(self):
        self.refused(403, 'subscription-login', body=request_body(), headers=[('x-api-key', OAUTH)])

    def test_the_oauth_capability_even_with_the_right_secret(self):
        headers = claude_headers(extra=[('anthropic-beta', 'oauth-2025-04-20')])
        self.refused(403, 'subscription-login', body=request_body(), headers=headers)

    def test_on_every_path_and_method_before_anything_else(self):
        login = [('Authorization', 'Bearer ' + OAUTH)]
        for method, path in (('POST', '/v1/messages/count_tokens'), ('GET', '/v1/models?limit=1000'),
                             ('HEAD', '/api/hello'), ('GET', '/anything'), ('OPTIONS', '/v1/messages')):
            with self.subTest(method=method, path=path):
                self.refused(403, 'subscription-login', method=method, path=path, headers=login,
                             body=b'{}' if method == 'POST' else None)
        # before the address check too: a login from a web page is named as a login
        self.refused(403, 'subscription-login', body=b'{}', headers=login + [('Origin', 'https://site.example')])

    def test_a_tab_after_bearer_changes_nothing(self):
        self.refused(403, 'subscription-login', body=request_body(), headers=[('Authorization', 'Bearer\t' + OAUTH)])

    def test_refused_before_100_continue(self):
        import socket
        for auth in ('Bearer ' + OAUTH, 'Bearer wrong-secret-000000000000'):
            with self.subTest(auth=auth[:12]):
                raw = ('POST /v1/messages HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nAuthorization: %s\r\n'
                       'Content-Length: 10\r\nExpect: 100-continue\r\n\r\n' % (self.gw.port, auth)).encode()
                with socket.create_connection(('127.0.0.1', self.gw.port), timeout=5) as s:
                    s.sendall(raw)
                    first = s.recv(200)
                self.assertFalse(first.startswith(b'HTTP/1.1 100'), first)
                self.assertTrue(first.startswith(b'HTTP/1.1 403') or first.startswith(b'HTTP/1.1 401'), first)
        self.assertEqual(self.up.count(), 0)

    def test_100_continue_still_works_for_claude_code(self):
        import socket
        body = request_body()
        raw = ('POST /v1/messages HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nAuthorization: Bearer %s\r\n'
               'Content-Type: application/json\r\nContent-Length: %d\r\nExpect: 100-continue\r\n\r\n'
               % (self.gw.port, SECRET, len(body))).encode()
        with socket.create_connection(('127.0.0.1', self.gw.port), timeout=5) as s:
            s.sendall(raw)
            self.assertTrue(s.recv(100).startswith(b'HTTP/1.1 100'))
            s.sendall(body)
            got = b''
            while b'message_stop' not in got:
                more = s.recv(65536)
                if not more:
                    break
                got += more
        self.assertIn(b'HTTP/1.1 200', got)
        self.assertEqual(self.up.count(), 1)

    def test_missing_secret(self):
        message = self.refused(401, 'secret', body=request_body(), headers=[('anthropic-version', '2023-06-01')])
        self.assertIn('no credential', message)

    def test_wrong_secret(self):
        message = self.refused(401, 'secret', body=request_body(), headers=claude_headers(secret=SECRET + 'x'))
        self.assertIn('not the gateway secret', message)
        self.refused(401, 'secret', body=request_body(), headers=[('x-api-key', 'sk-made-up-other')])

    def test_another_origin(self):
        for extra in (('Origin', 'https://site.example'), ('Origin', 'null'), ('Sec-Fetch-Site', 'cross-site'),
                      ('Sec-Fetch-Mode', 'no-cors')):
            with self.subTest(header=extra[0]):
                self.refused(403, 'not-local', body=request_body(), headers=claude_headers(extra=[extra]))

    def test_another_host_name(self):
        for host in ('evil.example:%d' % self.gw.port, '127.0.0.1', '127.0.0.1:1', 'localhost.evil.example'):
            with self.subTest(host=host):
                self.refused(403, 'not-local', body=request_body(), host=host)

    def test_the_lane_key_is_never_used_for_a_refused_request(self):
        used = []
        gw = G.Gateway(lambda: [lane('a', self.up)], SECRET, lambda name: used.append(name) or KEY, port=0)
        gw.start(poll_interval=0.05)
        self.addCleanup(gw.stop)
        call(gw.port, body=request_body(), headers=[('Authorization', 'Bearer ' + OAUTH)])
        call(gw.port, body=request_body(), headers=claude_headers(secret='wrong-secret-0000000000'))
        self.assertEqual(used, [])

    def test_the_right_secret_goes_through(self):
        status, headers, _ = call(self.gw.port, body=request_body())
        self.assertEqual((status, headers['x-switchboard-lane']), (200, 'a'))


class NoSwitchAnywhere(GatewayCase):
    def test_the_gateway_takes_no_option_to_allow_it(self):
        for option in ('allow_subscription', 'allow_oauth', 'refuse_subscription', 'trust_login'):
            with self.assertRaises(TypeError):
                G.Gateway([], SECRET, lambda name: KEY, **{option: True})

    def test_settings_and_environment_change_nothing(self):
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home, True)
        env = {'SWITCHBOARD_HOME': os.path.join(home, 'data'), 'SWITCHBOARD_ALLOW_SUBSCRIPTION': '1',
               'SWITCHBOARD_GATEWAY_UNSAFE': '1'}
        with mock.patch.dict(os.environ, env):
            for key in ('gateway_allow_subscription', 'allow_oauth', 'gateway_refuse_subscription'):
                core.set_setting(key, True)
            up = self.upstream(answer_stream)
            gw = self.gateway([lane('a', up)])
            status, _, _ = call(gw.port, body=request_body(), headers=[('Authorization', 'Bearer ' + OAUTH)])
            self.assertEqual((status, up.count()), (403, 0))

    def test_it_never_opens_claude_codes_files_or_starts_a_program(self):
        up = self.upstream(answer_stream)
        gw = self.gateway([lane('a', up)])
        del SEEN[:]
        RECORDING.set()
        try:
            call(gw.port, body=request_body())
            call(gw.port, body=request_body(), headers=[('Authorization', 'Bearer ' + OAUTH)])
            call(gw.port, method='GET', path='/v1/models?limit=1000')
            probe = os.path.join(tempfile.gettempdir(), 'switchboard-recorder-probe')
            with open(probe, 'w'):
                pass                                          # proves the recorder sees opens at all
            os.remove(probe)
        finally:
            RECORDING.clear()
        self.assertIn(probe, [what for _, what in SEEN])
        claude_dir = os.environ.get('CLAUDE_CONFIG_DIR', '')
        for event, what in SEEN:
            self.assertNotIn(event, ('subprocess.Popen', 'os.system', 'os.posix_spawn', 'os.exec'), what)
            self.assertNotIn('.claude', what)
            if claude_dir:
                self.assertNotIn(claude_dir, what)


if __name__ == '__main__':
    unittest.main()
