import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Tests for switchboard/gateway.py against made-up lanes on random loopback ports.
#
# Everything is proven against a fake upstream on this Mac: no test calls api.anthropic.com or openrouter.ai,
# and no key is real.
import http.client
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import core, gateway as G
from fakes_gateway import (BLOCK, BLOCK_STOP, KEY_A, KEY_B, MSG_DELTA, PING, SECRET, START, STOP,
                                GatewayCase, api_error, call, claude_headers, closed_port, delta, error_event,
                                lane, message_json, request_body, stream_of, wait_for_log)


def ok_stream(text='Hello', headers=()):
    def script(h, record):
        h.send_events(stream_of(text), headers=headers)
    return script


def ok_json(text='Hello', headers=()):
    def script(h, record):
        h.send_json(200, message_json(text), headers=headers)
    return script


def answer(status, obj, headers=()):
    def script(h, record):
        h.send_json(status, obj, headers=headers)
    return script


class Forwarding(GatewayCase):
    def test_normal_reply(self):
        up = self.upstream(ok_json('Hi there'))
        gw = self.gateway([lane('a', up)])
        status, headers, body = call(gw.port, body=request_body(stream=False))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), message_json('Hi there'))
        self.assertEqual(headers['x-switchboard-lane'], 'a')
        self.assertNotIn('x-switchboard-skipped', headers)

    def test_the_path_query_body_and_headers_go_on_unchanged(self):
        up = self.upstream(ok_json())
        gw = self.gateway([lane('a', up)])
        body = b'{ "model" : "claude-opus-5-5", "max_tokens":64,\n "messages":[{"role":"user","content":"caf\xc3\xa9 \\u00e9"}]}'
        extra = [('anthropic-workspace-id', 'wrkspc_made_up'), ('x-claude-code-session-id', 'session-made-up'),
                 ('x-claude-code-agent-id', 'agent-made-up'), ('User-Agent', 'claude-cli/9.9.9 (made up)'),
                 ('anthropic-beta', 'context-management-2025-06-27,effort-2025-11-24'), ('X-Something-New', 'kept')]
        status, _, _ = call(gw.port, body=body, headers=claude_headers(extra=extra))
        self.assertEqual(status, 200)
        got = up.requests[0]
        self.assertEqual(got['path'], '/v1/messages?beta=true')
        self.assertEqual(got['body'], body)                              # byte for byte
        sent = got['headers']
        for k, v in claude_headers(extra=extra)[1:]:
            self.assertIn((k, v), sent)                                  # name, value and both beta headers
        names = [k.lower() for k, _ in sent]
        self.assertEqual(names.count('anthropic-beta'), 2)
        self.assertIn(('x-api-key', KEY_A), sent)
        self.assertNotIn('authorization', names)                         # the gateway secret never goes upstream
        self.assertNotIn(SECRET, json.dumps(sent))
        self.assertIn(('Accept-Encoding', 'identity'), sent)
        self.assertEqual(dict((k.lower(), v) for k, v in sent)['host'], up.url.split('//')[1])

    def test_an_openrouter_lane_gets_a_bearer_key(self):
        up = self.upstream(ok_json())
        gw = self.gateway([lane('b', up, kind='openrouter', names={'claude-opus-5-5': 'anthropic/claude-opus-5.5'})])
        call(gw.port, body=request_body(stream=False))
        sent = up.requests[0]['headers']
        self.assertIn(('Authorization', 'Bearer ' + KEY_B), sent)
        self.assertNotIn('x-api-key', [k.lower() for k, _ in sent])

    def test_a_base_url_with_a_path_keeps_it(self):
        up = self.upstream(ok_json())
        gw = self.gateway([lane('a', up, url=up.url + '/api')])
        call(gw.port, body=request_body(stream=False))
        self.assertEqual(up.requests[0]['path'], '/api/v1/messages?beta=true')

    def test_response_headers_pass_through(self):
        keep = [('retry-after', '3'), ('x-should-retry', 'true'), ('anthropic-ratelimit-unified-status', 'allowed'),
                ('anthropic-ratelimit-unified-5h-utilization', '0.5'), ('request-id', 'req_made_up')]
        up = self.upstream(ok_stream(headers=keep))
        gw = self.gateway([lane('a', up)])
        status, headers, _ = call(gw.port, body=request_body())
        self.assertEqual(status, 200)
        for k, v in keep:
            self.assertEqual(headers[k], v)
        self.assertEqual(headers['content-type'], 'text/event-stream')

    def test_error_headers_and_body_pass_through_unmodified(self):
        raw = b'{"type":"error","error":{"type":"invalid_request_error","message":"messages: roles must alternate"}}'
        up = self.upstream(lambda h, r: h.send_json(400, None, headers=[('x-should-retry', 'false')], raw=raw))
        gw = self.gateway([lane('a', up)])
        status, headers, body = call(gw.port, body=request_body())
        self.assertEqual((status, body, headers['x-should-retry']), (400, raw, 'false'))

    def test_a_client_body_sent_in_chunks(self):
        up = self.upstream(ok_json())
        gw = self.gateway([lane('a', up)])
        body = request_body(stream=False)
        conn = http.client.HTTPConnection('127.0.0.1', gw.port, timeout=10)
        conn.request('POST', '/v1/messages?beta=true', body=iter([body[:10], body[10:]]),
                     headers=dict(claude_headers()), encode_chunked=True)
        resp = conn.getresponse()
        resp.read()
        conn.close()
        self.assertEqual(resp.status, 200)
        self.assertEqual(up.requests[0]['body'], body)

    def test_a_badly_chunked_body_is_refused(self):
        import socket
        up = self.upstream(ok_json())
        gw = self.gateway([lane('a', up)])
        raw = ('POST /v1/messages HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nAuthorization: Bearer %s\r\n'
               'Transfer-Encoding: chunked\r\n\r\n5\r\nhelloXX0\r\n\r\n' % (gw.port, SECRET)).encode()
        with socket.create_connection(('127.0.0.1', gw.port), timeout=5) as s:
            s.sendall(raw)
            first = s.recv(200)
        self.assertTrue(first.startswith(b'HTTP/1.1 400'), first)
        self.assertEqual(up.count(), 0)

    def test_too_large_a_body_is_refused_before_any_lane(self):
        up = self.upstream(ok_json())
        gw = self.gateway([lane('a', up)], max_body=100)
        status, _, body = call(gw.port, body=b'x' * 200)
        self.assertEqual(status, 413)
        self.assertEqual(json.loads(body)['error']['type'], 'request_too_large')
        self.assertEqual(up.count(), 0)

    def test_no_lanes(self):
        gw = self.gateway([])
        status, headers, body = call(gw.port, body=request_body())
        self.assertEqual((status, headers['x-should-retry']), (503, 'false'))
        self.assertIn('no API key lanes', json.loads(body)['error']['message'])

    def test_it_listens_on_loopback_only(self):
        gw = self.gateway([])
        self.assertEqual(gw.address()[0], '127.0.0.1')
        import inspect
        self.assertNotIn('host', inspect.signature(G.Gateway).parameters)


class Streaming(GatewayCase):
    def test_a_streamed_reply_arrives_byte_for_byte(self):
        pieces = stream_of('Streamed') + [b': a comment line\n\n']
        up = self.upstream(lambda h, r: h.send_events(pieces))
        gw = self.gateway([lane('a', up)])
        status, _, body = call(gw.port, body=request_body())
        self.assertEqual(status, 200)
        self.assertEqual(body, b''.join(pieces))

    def test_each_event_passes_on_as_it_arrives(self):
        go = threading.Event()
        pieces = stream_of('first-words')
        up = self.upstream(lambda h, r: h.send_events(pieces, wait=(4, go)))   # stops after the first delta
        gw = self.gateway([lane('a', up)])
        conn = http.client.HTTPConnection('127.0.0.1', gw.port, timeout=10)
        conn.request('POST', '/v1/messages?beta=true', body=request_body(), headers=dict(claude_headers()))
        resp = conn.getresponse()
        got = b''
        while b'first-words' not in got:
            got += resp.read1(65536)
        seen_before_release = not go.is_set()
        go.set()
        got += resp.read()
        conn.close()
        self.assertTrue(seen_before_release)            # the gateway did not wait for the whole reply
        self.assertEqual(got, b''.join(pieces))

    def test_pings_before_content_pass_too_once_content_comes(self):
        pieces = [START, PING, PING, BLOCK, delta('x'), BLOCK_STOP, MSG_DELTA, STOP]
        up = self.upstream(lambda h, r: h.send_events(pieces))
        gw = self.gateway([lane('a', up)])
        self.assertEqual(call(gw.port, body=request_body())[2], b''.join(pieces))

    def test_a_zero_content_reply_is_a_reply(self):
        pieces = [START, MSG_DELTA, STOP]              # e.g. max_tokens 0: no content block at all, then the end
        up = self.upstream(lambda h, r: h.send_events(pieces))
        other = self.upstream(ok_stream())
        gw = self.gateway([lane('a', up), lane('c', other)])
        status, headers, body = call(gw.port, body=request_body())
        self.assertEqual((status, body, headers['x-switchboard-lane']), (200, b''.join(pieces), 'a'))
        self.assertEqual(other.count(), 0)

    def test_a_slow_upstream_within_its_time_is_waited_for(self):
        def slow(h, r):
            time.sleep(0.4)
            h.send_events(stream_of('slow'), gap=0.1)
        up = self.upstream(slow)
        other = self.upstream(ok_stream())
        gw = self.gateway([lane('a', up), lane('c', other)], stream_first_byte=3)
        status, headers, body = call(gw.port, body=request_body())
        self.assertEqual((status, headers['x-switchboard-lane']), (200, 'a'))
        self.assertEqual(body, b''.join(stream_of('slow')))
        self.assertEqual(other.count(), 0)

    def test_an_upstream_that_closes_mid_stream(self):
        pieces = [START, BLOCK, delta('half a')]
        up = self.upstream(lambda h, r: h.send_events(pieces, finish=False))
        other = self.upstream(ok_stream())
        gw = self.gateway([lane('a', up), lane('c', other)])
        conn = http.client.HTTPConnection('127.0.0.1', gw.port, timeout=10)
        conn.request('POST', '/v1/messages?beta=true', body=request_body(), headers=dict(claude_headers()))
        resp = conn.getresponse()
        with self.assertRaises(http.client.IncompleteRead) as e:
            resp.read()
        conn.close()
        self.assertEqual(e.exception.partial, b''.join(pieces))      # what came, then a broken reply (as upstream)
        self.assertEqual(other.count(), 0)                           # no fail-over after the first content
        self.assertEqual([x['outcome'] for x in wait_for_log(gw, 1)], ['broke mid-reply'])

    def test_no_fail_over_after_the_first_content_byte(self):
        pieces = [START, BLOCK, delta('started'), error_event('overloaded_error')]
        up = self.upstream(lambda h, r: h.send_events(pieces))
        other = self.upstream(ok_stream())
        gw = self.gateway([lane('a', up), lane('c', other)])
        status, headers, body = call(gw.port, body=request_body())
        self.assertEqual((status, body, headers['x-switchboard-lane']), (200, b''.join(pieces), 'a'))
        self.assertEqual(other.count(), 0)

    def test_after_the_hold_time_the_reply_is_committed(self):
        def slow_start(h, r):
            h.send_response(200)
            h.send_header('Content-Type', 'text/event-stream')
            h.send_header('Transfer-Encoding', 'chunked')
            h.end_headers()
            h.wfile.write(b'%X\r\n' % len(START) + START + b'\r\n')
            h.wfile.flush()
            time.sleep(0.8)                              # longer than the hold
            h.wfile.write(b'0\r\n\r\n')                  # then it ends without content
            h.wfile.flush()
        up = self.upstream(slow_start)
        other = self.upstream(ok_stream())
        gw = self.gateway([lane('a', up), lane('c', other)], hold=0.3)
        conn = http.client.HTTPConnection('127.0.0.1', gw.port, timeout=10)
        started = time.time()
        conn.request('POST', '/v1/messages?beta=true', body=request_body(), headers=dict(claude_headers()))
        resp = conn.getresponse()
        first = resp.read1(65536)
        waited = time.time() - started
        rest = resp.read()
        conn.close()
        self.assertLess(waited, 0.75)                     # sent at the hold time, before the lane went on
        self.assertEqual(first + rest, START)
        self.assertEqual(other.count(), 0)                # committed: never switched


class Https(GatewayCase):
    """Real lanes are https. A throwaway certificate made by /usr/bin/openssl for this test only stands in."""

    @classmethod
    def setUpClass(cls):
        import ssl
        import subprocess
        cls.folder = tempfile.mkdtemp()
        cls.cert, key = os.path.join(cls.folder, 'cert.pem'), os.path.join(cls.folder, 'key.pem')
        subprocess.run(['/usr/bin/openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', key, '-out',
                        cls.cert, '-days', '1', '-subj', '/CN=localhost', '-addext',
                        'subjectAltName=DNS:localhost,IP:127.0.0.1'], check=True, capture_output=True, timeout=60)
        cls.server_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cls.server_tls.load_cert_chain(cls.cert, key)
        cls.trusting = ssl.create_default_context(cafile=cls.cert)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.folder, True)

    def test_a_streamed_reply_over_https(self):
        up = self.upstream(ok_stream('secure'), tls=self.server_tls)
        gw = self.gateway([lane('a', up)], tls=self.trusting)
        status, headers, body = call(gw.port, body=request_body())
        self.assertEqual((status, body), (200, b''.join(stream_of('secure'))))
        sent = dict((k.lower(), v) for k, v in up.requests[0]['headers'])
        self.assertEqual((sent['x-api-key'], sent['host']), (KEY_A, up.url.split('//')[1]))

    def test_a_certificate_that_is_not_trusted_is_a_lane_that_cannot_be_reached(self):
        up = self.upstream(ok_stream('never'), tls=self.server_tls)
        good = self.upstream(ok_stream('plain'))
        gw = self.gateway([lane('a', up), lane('c', good)])          # the system's trust store: not this certificate
        status, headers, body = call(gw.port, body=request_body())
        self.assertEqual((status, headers['x-switchboard-lane']), (200, 'c'))
        self.assertEqual(headers['x-switchboard-skipped'], 'a: unreachable: TLS failed')
        self.assertEqual(up.count(), 0)


class Concurrency(GatewayCase):
    def test_several_requests_at_once(self):
        def slowish(h, r):
            time.sleep(0.5)
            h.send_events(stream_of(json.loads(r['body'])['messages'][0]['content']))
        up = self.upstream(slowish)
        gw = self.gateway([lane('a', up)])
        results = {}

        def one(i):
            results[i] = call(gw.port, body=request_body(text='request %d' % i))

        threads = [threading.Thread(target=one, args=(i,)) for i in range(8)]
        started = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join(20)
        elapsed = time.time() - started
        self.assertEqual(len(results), 8)
        for i, (status, _, body) in results.items():
            self.assertEqual(status, 200)
            self.assertEqual(body, b''.join(stream_of('request %d' % i)))   # each got its own reply
        self.assertLess(elapsed, 3.0)                     # 8 x 0.5 s one after another would be 4 s


# ---------------------------------------------------------------------------------------------------------------
# Fail-over
# ---------------------------------------------------------------------------------------------------------------

FAIL_OVER = [
    # (what lane a answers, kind of lane a, the category the skip header names)
    ((429, api_error('rate_limit_error', 'Number of request tokens has exceeded your per-minute rate limit'),
      [('retry-after', '30')]), 'anthropic', 'rate limited (429)'),
    ((429, api_error('rate_limit_error', 'ramp', details={'error_code': 'ramp_limit_exceeded'}), []), 'anthropic',
     'rate limited (429)'),
    ((429, api_error('rate_limit_error', 'You have reached your API usage limits',
                     details={'error_code': 'enforced_spend_limit_reached'}), []), 'anthropic', 'out of credit (429)'),
    ((402, api_error('billing_error'), []), 'anthropic', 'out of credit (402)'),
    ((400, api_error('invalid_request_error', 'You have reached your specified API usage limits. You will regain '
                     'access on 2026-12-01 at 00:00 UTC.'), []), 'anthropic', 'out of credit (400)'),
    ((400, api_error('invalid_request_error', 'You have reached your specified workspace API usage limits.'), []),
     'anthropic', 'out of credit (400)'),
    ((529, api_error('overloaded_error', 'Overloaded'), []), 'anthropic', 'overloaded (529)'),
    ((500, api_error('api_error', 'Internal server error'), []), 'anthropic', 'server error (500)'),
    ((504, api_error('timeout_error', 'Request timed out'), []), 'anthropic', 'server error (504)'),
    ((401, api_error('authentication_error', 'invalid x-api-key'), []), 'anthropic', 'key rejected (401)'),
    ((403, api_error('permission_error', 'not allowed'), []), 'anthropic', 'key not allowed (403)'),
    ((402, {'error': {'code': 402, 'message': 'Insufficient credits'}}, []), 'openrouter', 'out of credit (402)'),
    ((403, {'error': {'code': 403, 'message': 'Budget limit exceeded (weekly limit).',
                      'metadata': {'limit_source': 'openrouter_key_limit'}}}, []), 'openrouter',
     'out of credit (403)'),
    ((408, {'error': {'code': 408, 'message': 'Your request timed out'}}, []), 'openrouter', 'server error (408)'),
    ((502, api_error('api_error', 'Provider returned error', error_type='provider_unavailable'), []), 'openrouter',
     'server error (502)'),
    ((503, {'error': {'code': 503, 'message': 'No allowed providers'}}, []), 'openrouter', 'server error (503)'),
    ((200, {'type': 'error', 'error': {'type': 'overloaded_error', 'message': 'Provider is temporarily overloaded',
                                       'error_type': 'provider_overloaded'}}, []), 'openrouter', 'overloaded (200)'),
    ((200, {'error': {'code': 429, 'message': 'Rate limit exceeded'}}, []), 'openrouter', 'rate limited (200)'),
]

NO_FAIL_OVER = [
    ((400, api_error('invalid_request_error', 'prompt is too long: 1000001 tokens > 1000000 maximum'), []),
     'anthropic'),
    ((404, api_error('not_found_error', 'model: claude-made-up'), []), 'anthropic'),
    ((413, api_error('request_too_large', 'Request exceeds the maximum allowed number of bytes.'), []), 'anthropic'),
    ((422, api_error('invalid_request_error', 'unprocessable'), []), 'custom'),
    ((403, {'type': 'error', 'error': {'type': 'permission_error', 'message': 'Request blocked: prompt injection '
                                       'patterns detected'}}, []), 'openrouter'),
    ((403, api_error('permission_error', 'blocked'), []), 'custom'),
]

MAP = {'claude-opus-5-5': 'anthropic/claude-opus-5.5'}


class FailOver(GatewayCase):
    def test_each_documented_condition_moves_to_the_next_lane(self):
        for (status, obj, headers), kind, why in FAIL_OVER:
            with self.subTest(why=why, kind=kind, status=status):
                bad = self.upstream(answer(status, obj, headers))
                good = self.upstream(ok_stream('from c'))
                names = MAP if kind == 'openrouter' else None
                gw = self.gateway([lane('a', bad, kind=kind, names=names), lane('c', good)],
                                  keys={'a': KEY_B if kind == 'openrouter' else KEY_A, 'c': KEY_A})
                got_status, got_headers, body = call(gw.port, body=request_body())
                self.assertEqual(got_status, 200)
                self.assertEqual(body, b''.join(stream_of('from c')))
                self.assertEqual(got_headers['x-switchboard-lane'], 'c')
                self.assertEqual(got_headers['x-switchboard-skipped'], 'a: ' + why)
                self.assertEqual((bad.count(), good.count()), (1, 1))

    def test_errors_another_lane_would_repeat_go_back_unchanged(self):
        for (status, obj, headers), kind in NO_FAIL_OVER:
            with self.subTest(status=status, kind=kind):
                bad = self.upstream(answer(status, obj, headers))
                good = self.upstream(ok_stream())
                url = None if kind != 'custom' else bad.url
                gw = self.gateway([dict(lane('a', bad, kind=kind, names=MAP if kind == 'openrouter' else None),
                                        base_url=url or bad.url), lane('c', good)],
                                  keys={'a': KEY_A, 'c': KEY_A})
                got_status, got_headers, body = call(gw.port, body=request_body())
                self.assertEqual((got_status, json.loads(body)), (status, obj))
                self.assertEqual(got_headers['x-switchboard-lane'], 'a')
                self.assertEqual(good.count(), 0)

    def test_an_error_event_before_any_content(self):
        for kind in ('overloaded_error', 'rate_limit_error', 'api_error', 'billing_error'):
            with self.subTest(kind=kind):
                bad = self.upstream(lambda h, r, k=kind: h.send_events([START, PING, error_event(k)]))
                good = self.upstream(ok_stream('rescued'))
                gw = self.gateway([lane('a', bad), lane('c', good)])
                status, headers, body = call(gw.port, body=request_body())
                self.assertEqual(body, b''.join(stream_of('rescued')))   # not one byte from lane a
                self.assertIn('error event before any content', headers['x-switchboard-skipped'])

    def test_an_error_event_another_lane_would_repeat_is_passed_on(self):
        pieces = [START, error_event('invalid_request_error', 'bad')]
        bad = self.upstream(lambda h, r: h.send_events(pieces))
        good = self.upstream(ok_stream())
        gw = self.gateway([lane('a', bad), lane('c', good)])
        self.assertEqual(call(gw.port, body=request_body())[2], b''.join(pieces))
        self.assertEqual(good.count(), 0)

    def test_a_stream_that_ends_before_content(self):
        for pieces in ([START, PING], []):
            with self.subTest(n=len(pieces)):
                bad = self.upstream(lambda h, r, p=pieces: h.send_events(p))
                good = self.upstream(ok_stream('rescued'))
                gw = self.gateway([lane('a', bad), lane('c', good)])
                status, headers, body = call(gw.port, body=request_body())
                self.assertEqual(body, b''.join(stream_of('rescued')))
                self.assertEqual(headers['x-switchboard-skipped'], 'a: reply ended before any content')

    def test_a_stream_that_breaks_before_content(self):
        bad = self.upstream(lambda h, r: h.send_events([START], finish=False))
        good = self.upstream(ok_stream('rescued'))
        gw = self.gateway([lane('a', bad), lane('c', good)])
        status, headers, body = call(gw.port, body=request_body())
        self.assertEqual(body, b''.join(stream_of('rescued')))
        self.assertEqual(headers['x-switchboard-skipped'], 'a: reply broke before any content')

    def test_unreachable_lanes(self):
        good = self.upstream(ok_stream('rescued'))
        gw = self.gateway([lane('a', None, url='http://127.0.0.1:%d' % closed_port()), lane('c', good)])
        status, headers, body = call(gw.port, body=request_body())
        self.assertEqual((status, headers['x-switchboard-lane']), (200, 'c'))
        self.assertEqual(headers['x-switchboard-skipped'], 'a: unreachable: connection refused')

    def test_no_reply_within_the_first_byte_time(self):
        bad = self.upstream(lambda h, r: bad.release.wait(10))
        good = self.upstream(ok_stream('rescued'))
        gw = self.gateway([lane('a', bad), lane('c', good)], stream_first_byte=0.5)
        status, headers, body = call(gw.port, body=request_body())
        self.assertEqual((status, headers['x-switchboard-lane']), (200, 'c'))
        self.assertEqual(headers['x-switchboard-skipped'], 'a: unreachable: no reply within 0.5 s')

    def test_a_reply_shorter_than_its_content_length_is_a_broken_reply(self):
        def short(h, r):
            h.send_response(200)
            h.send_header('Content-Type', 'application/json')
            h.send_header('Content-Length', '100')
            h.end_headers()
            h.wfile.write(b'{"id":"msg","content":[')
            h.close_connection = True
        bad = self.upstream(short)
        good = self.upstream(ok_json('whole'))
        gw = self.gateway([lane('a', bad), lane('c', good)])
        status, headers, body = call(gw.port, body=request_body(stream=False))
        self.assertEqual((status, json.loads(body)), (200, message_json('whole')))
        self.assertEqual(headers['x-switchboard-skipped'], 'a: unreachable: connection broke during the reply')
        gw = self.gateway([lane('a', bad)])
        status, _, body = call(gw.port, body=request_body(stream=False))
        self.assertEqual(status, 502)                        # never the cut body as if it were whole

    def test_a_stream_shorter_than_its_content_length(self):
        def short(h, r, pieces):
            data = b''.join(pieces)
            h.send_response(200)
            h.send_header('Content-Type', 'text/event-stream')
            h.send_header('Content-Length', str(len(data) + 50))
            h.end_headers()
            h.wfile.write(data)
            h.close_connection = True
        before = self.upstream(lambda h, r: short(h, r, [START, PING]))
        good = self.upstream(ok_stream('rescued'))
        gw = self.gateway([lane('a', before), lane('c', good)])
        status, headers, body = call(gw.port, body=request_body())
        self.assertEqual(body, b''.join(stream_of('rescued')))
        self.assertEqual(headers['x-switchboard-skipped'], 'a: reply broke before any content')
        after = self.upstream(lambda h, r: short(h, r, [START, BLOCK, delta('cut')]))
        gw = self.gateway([lane('a', after), lane('c', good)])
        conn = http.client.HTTPConnection('127.0.0.1', gw.port, timeout=10)
        conn.request('POST', '/v1/messages?beta=true', body=request_body(), headers=dict(claude_headers()))
        resp = conn.getresponse()
        with self.assertRaises(http.client.IncompleteRead):
            resp.read()                                      # broken toward Claude Code too, never a clean end
        conn.close()
        self.assertEqual(good.count(), 1)                    # only the first request; none after content

    def test_a_key_with_a_trailing_newline_is_not_usable(self):
        good = self.upstream(ok_stream())
        gw = self.gateway([lane('a', good), lane('c', good)], keys={'a': KEY_A + '\n', 'c': KEY_A})
        status, headers, _ = call(gw.port, body=request_body())
        self.assertEqual(headers['x-switchboard-skipped'], 'a: no usable key in the Keychain')

    def test_a_redirect_is_never_followed(self):
        bad = self.upstream(answer(307, {}, [('Location', 'https://elsewhere.example.com/v1/messages')]))
        good = self.upstream(ok_stream())
        gw = self.gateway([lane('a', bad), lane('c', good)])
        status, headers, _ = call(gw.port, body=request_body())
        self.assertEqual((status, headers['x-switchboard-lane']), (200, 'c'))
        self.assertNotIn('location', headers)
        self.assertIn('redirect (307), not followed', headers['x-switchboard-skipped'])

    def test_a_lane_without_a_usable_key_is_skipped(self):
        good = self.upstream(ok_stream())
        gw = self.gateway([lane('a', good), lane('c', good)], keys={'c': KEY_A})
        status, headers, _ = call(gw.port, body=request_body())
        self.assertEqual((status, headers['x-switchboard-lane']), (200, 'c'))
        self.assertEqual(headers['x-switchboard-skipped'], 'a: no usable key in the Keychain')

    def test_one_pass_and_the_most_useful_error_unchanged(self):
        retry = [('retry-after', '7'), ('x-should-retry', 'true')]
        credit = self.upstream(answer(402, api_error('billing_error', 'Your credit balance is too low')))
        limited = self.upstream(answer(429, api_error('rate_limit_error', 'slow down'), retry))
        gw = self.gateway([lane('a', credit), lane('c', limited)])
        status, headers, body = call(gw.port, body=request_body())
        self.assertEqual((status, json.loads(body)), (429, api_error('rate_limit_error', 'slow down')))
        self.assertEqual((headers['retry-after'], headers['x-should-retry']), ('7', 'true'))
        self.assertEqual((headers['x-switchboard-lane'], headers['x-switchboard-error-from']), ('none', 'c'))
        self.assertEqual(headers['x-switchboard-skipped'], 'a: out of credit (402); c: rate limited (429)')
        self.assertEqual((credit.count(), limited.count()), (1, 1))     # each lane once, never a second round

    def test_ranking(self):
        cases = [
            # lane a, lane c -> the one returned
            (answer(401, api_error('authentication_error')), answer(402, api_error('billing_error')), 402),
            (answer(402, api_error('billing_error')), answer(529, api_error('overloaded_error')), 529),
            (answer(529, api_error('overloaded_error')), answer(500, api_error('api_error')), 529),   # tie: first
            (lambda h, r: h.send_events([START]), answer(401, api_error('authentication_error')), 200),
        ]
        for a, c, want in cases:
            with self.subTest(want=want):
                ua, uc = self.upstream(a), self.upstream(c)
                gw = self.gateway([lane('a', ua), lane('c', uc)])
                status, headers, body = call(gw.port, body=request_body())
                self.assertEqual(status, want)
                self.assertEqual(headers['x-switchboard-lane'], 'none')

    def test_an_ended_stream_is_returned_as_it_came_when_nothing_else_answers(self):
        bad = self.upstream(lambda h, r: h.send_events([START, PING]))
        gw = self.gateway([lane('a', bad), lane('c', None, url='http://127.0.0.1:%d' % closed_port())])
        status, headers, body = call(gw.port, body=request_body())
        self.assertEqual((status, body), (200, START + PING))
        self.assertEqual(headers['x-switchboard-error-from'], 'a')

    def test_every_lane_unreachable(self):
        gw = self.gateway([lane('a', None, url='http://127.0.0.1:%d' % closed_port()),
                           lane('c', None, url='http://127.0.0.1:%d' % closed_port())])
        status, headers, body = call(gw.port, body=request_body())
        self.assertEqual(status, 502)
        err = json.loads(body)
        self.assertEqual((err['type'], err['error']['type']), ('error', 'api_error'))
        self.assertIn('a: unreachable: connection refused; c: unreachable: connection refused', err['error']['message'])

    def test_no_new_lane_after_the_time_budget(self):
        slow = self.upstream(lambda h, r: (time.sleep(0.6), h.send_json(529, api_error('overloaded_error'))))
        good = self.upstream(ok_stream())
        gw = self.gateway([lane('a', slow), lane('c', good)], budget=0.3)
        status, headers, _ = call(gw.port, body=request_body())
        self.assertEqual(status, 529)
        self.assertEqual(good.count(), 0)
        self.assertIn('c: not tried: out of time', headers['x-switchboard-skipped'])


class ModelNames(GatewayCase):
    def test_a_name_map_changes_only_the_model_value(self):
        up = self.upstream(ok_stream())
        gw = self.gateway([lane('b', up, kind='openrouter', names=dict(G.OPENROUTER_NAMES))])
        body = (b'{"messages": [{"role": "user", "content": "keep \\"model\\": \\"claude-opus-5-5\\" here \xc3\xa9"}],'
                b'\n  "model"  :  "claude-opus-5-5" , "tools": [{"name": "t", "input_schema": {"model": "x"}}],'
                b' "stream": true}')
        status, headers, _ = call(gw.port, body=body)
        self.assertEqual(status, 200)
        sent = up.requests[0]['body']
        want = body.replace(b'"model"  :  "claude-opus-5-5"', b'"model"  :  "anthropic/claude-opus-5.5"')
        self.assertEqual(sent, want)
        self.assertEqual(headers['x-switchboard-model'],
                         'claude-opus-5-5 -> anthropic/claude-opus-5.5 (name map of lane b)')

    def test_without_a_map_the_model_never_changes(self):
        up = self.upstream(ok_stream())
        gw = self.gateway([lane('a', up)])
        status, headers, _ = call(gw.port, body=request_body(model='claude-made-up-9'))
        self.assertEqual(json.loads(up.requests[0]['body'])['model'], 'claude-made-up-9')
        self.assertNotIn('x-switchboard-model', headers)

    def test_a_lane_that_cannot_serve_the_model_is_skipped_and_says_so(self):
        mapped = self.upstream(ok_stream())
        plain = self.upstream(ok_stream('plain'))
        gw = self.gateway([lane('b', mapped, kind='openrouter', names=MAP), lane('a', plain)])
        body = request_body(model='claude-sonnet-5-5')
        status, headers, _ = call(gw.port, body=body)
        self.assertEqual((status, headers['x-switchboard-lane']), (200, 'a'))
        self.assertEqual(headers['x-switchboard-skipped'], 'b: its name map has no entry for claude-sonnet-5-5')
        self.assertEqual(mapped.count(), 0)
        self.assertEqual(plain.requests[0]['body'], body)

    def test_when_no_lane_can_serve_the_model(self):
        mapped = self.upstream(ok_stream())
        gw = self.gateway([lane('b', mapped, kind='openrouter', names=MAP)])
        status, headers, body = call(gw.port, body=request_body(model='claude-sonnet-5-5'))
        self.assertEqual((status, headers['x-should-retry']), (404, 'false'))
        self.assertIn('no lane that can serve', json.loads(body)['error']['message'])
        self.assertEqual(mapped.count(), 0)

    def test_an_ambiguous_body_is_not_guessed_at(self):
        mapped = self.upstream(ok_stream())
        gw = self.gateway([lane('b', mapped, kind='openrouter', names=MAP)])
        status, headers, _ = call(gw.port, body=b'{"model": "claude-opus-5-5", "model": "claude-opus-5-5"}')
        self.assertEqual(status, 404)
        self.assertIn('could not be set exactly', headers['x-switchboard-skipped'])

    def test_the_shipped_openrouter_table_is_data(self):
        for asked, sent in G.OPENROUTER_NAMES.items():
            self.assertTrue(asked.startswith('claude-'))
            self.assertTrue(sent.startswith('anthropic/claude-'))
        self.assertEqual(G.OPENROUTER_NAMES['claude-opus-5-5'], 'anthropic/claude-opus-5.5')

    def test_replace_model(self):
        self.assertEqual(G.replace_model(b' {"a":1,"model":"x"} ', 'y'), b' {"a":1,"model":"y"} ')
        for bad in (b'[]', b'{"model": 5}', b'{"a": 1}', b'{"model": "x"} trailing', b'\xff', b'{"model":"x"'):
            self.assertIsNone(G.replace_model(bad, 'y'), bad)


class OtherEndpoints(GatewayCase):
    def test_token_counting(self):
        up = self.upstream(answer(200, {'input_tokens': 2095}))
        gw = self.gateway([lane('a', up)])
        body = json.dumps({'model': 'claude-opus-5-5', 'messages': [{'role': 'user', 'content': 'hi'}]}).encode()
        status, _, got = call(gw.port, path='/v1/messages/count_tokens?beta=true', body=body)
        self.assertEqual((status, json.loads(got)), (200, {'input_tokens': 2095}))
        self.assertEqual(up.requests[0]['path'], '/v1/messages/count_tokens?beta=true')

    def test_token_counting_skips_openrouter(self):
        router = self.upstream(answer(200, {'input_tokens': 1}))
        direct = self.upstream(answer(200, {'input_tokens': 2}))
        gw = self.gateway([lane('b', router, kind='openrouter', names=MAP), lane('a', direct)])
        body = request_body(stream=False)
        status, headers, got = call(gw.port, path='/v1/messages/count_tokens', body=body)
        self.assertEqual((status, json.loads(got)), (200, {'input_tokens': 2}))
        self.assertEqual(headers['x-switchboard-skipped'], 'b: OpenRouter lists no token counting endpoint')
        self.assertEqual(router.count(), 0)

    def test_token_counting_with_only_openrouter_says_not_found(self):
        router = self.upstream(answer(200, {'input_tokens': 1}))
        gw = self.gateway([lane('b', router, kind='openrouter', names=MAP)])
        status, _, _ = call(gw.port, path='/v1/messages/count_tokens', body=request_body(stream=False))
        self.assertEqual(status, 404)                      # Claude Code then estimates (protocol page)

    def test_models_list_from_an_anthropic_lane(self):
        listing = {'data': [{'type': 'model', 'id': 'claude-opus-5-5', 'display_name': 'Claude Opus 5.5'}],
                   'has_more': False, 'first_id': 'claude-opus-5-5', 'last_id': 'claude-opus-5-5'}
        up = self.upstream(answer(200, listing))
        gw = self.gateway([lane('a', up)])
        status, _, got = call(gw.port, method='GET', path='/v1/models?limit=1000')
        self.assertEqual((status, json.loads(got)), (200, listing))
        self.assertEqual((up.requests[0]['method'], up.requests[0]['path'], up.requests[0]['body']),
                         ('GET', '/v1/models?limit=1000', b''))

    def test_models_list_from_a_name_map_needs_no_request(self):
        router = self.upstream(answer(200, {}))
        gw = self.gateway([lane('b', router, kind='openrouter', names=dict(G.OPENROUTER_NAMES))])
        status, headers, got = call(gw.port, method='GET', path='/v1/models?limit=1000')
        listing = json.loads(got)
        self.assertEqual(status, 200)
        self.assertEqual([m['id'] for m in listing['data']], list(G.OPENROUTER_NAMES))
        self.assertIn('as anthropic/claude-opus-5.5', listing['data'][1]['description'])
        self.assertEqual(router.count(), 0)
        self.assertEqual(headers['x-switchboard-lane'], 'b')

    def test_the_warming_probe(self):
        up = self.upstream(ok_json())
        gw = self.gateway([lane('a', up)])
        status, _, body = call(gw.port, method='HEAD', path='/api/hello', headers=[])
        self.assertEqual((status, body, up.count()), (200, b'', 0))

    def test_other_paths(self):
        up = self.upstream(ok_json())
        gw = self.gateway([lane('a', up)])
        for method, path in (('GET', '/v1/messages'), ('POST', '/v1/complete'), ('DELETE', '/v1/models'),
                             ('POST', '/v1/messages/batches'), ('GET', '/')):
            status, headers, _ = call(gw.port, method=method, path=path, body=b'{}')
            self.assertEqual((status, headers['x-switchboard-refused']), (404, 'path'))
        self.assertEqual(up.count(), 0)


class TheLog(GatewayCase):
    def test_no_content_and_no_keys_only_numbers(self):
        prompt, reply = 'PROMPT-MARKER-7f3a', 'REPLY-MARKER-91c2'
        up = self.upstream(ok_stream(reply))
        gw = self.gateway([lane('a', up)])
        call(gw.port, body=request_body(text=prompt))
        wait_for_log(gw, 1)
        text = json.dumps([gw.log(), gw.stats()])
        for secret in (prompt, reply, KEY_A, KEY_B, SECRET, 'Say hello'):
            self.assertNotIn(secret, text)
        entry = gw.log()[0]
        self.assertEqual(sorted(entry), sorted(['time', 'request', 'lane', 'route', 'model', 'status', 'outcome',
                                                'reason'] + list(G.USAGE_FIELDS)))
        self.assertEqual((entry['lane'], entry['model'], entry['status'], entry['outcome']),
                         ('a', 'claude-opus-5-5', 200, 'answered'))
        self.assertEqual((entry['input_tokens'], entry['output_tokens']), (25, 15))   # message_delta is cumulative

    def test_usage_from_a_whole_reply(self):
        up = self.upstream(ok_json())
        gw = self.gateway([lane('a', up)])
        call(gw.port, body=request_body(stream=False))
        entry = wait_for_log(gw, 1)[0]
        self.assertEqual((entry['input_tokens'], entry['output_tokens'], entry['cache_read_input_tokens']), (12, 18, 3))

    def test_stats_per_lane(self):
        bad = self.upstream(answer(529, api_error('overloaded_error')))
        good = self.upstream(ok_stream())
        gw = self.gateway([lane('a', bad), lane('c', good)])
        for _ in range(3):
            call(gw.port, body=request_body())
        wait_for_log(gw, 6)
        stats = gw.stats()
        self.assertEqual((stats['a']['attempts'], stats['a']['failed_over'], stats['a']['last_status']), (3, 3, 529))
        self.assertEqual((stats['c']['answered'], stats['c']['input_tokens'], stats['c']['output_tokens']),
                         (3, 75, 45))

    def test_the_log_is_small_and_rolling(self):
        up = self.upstream(ok_json())
        gw = self.gateway([lane('a', up)], log_size=3)
        for _ in range(5):
            call(gw.port, body=request_body(stream=False))
        wait_for_log(gw, 5)
        time.sleep(0.05)
        self.assertEqual([e['request'] for e in gw.log()], [3, 4, 5])

    def test_nothing_reaches_the_client_but_the_reply(self):
        up = self.upstream(ok_json())
        gw = self.gateway([lane('a', up)])
        status, headers, body = call(gw.port, body=request_body(stream=False))
        self.assertNotIn(KEY_A, json.dumps(headers) + body.decode())


class StartingClaudeCode(unittest.TestCase):
    def test_the_environment_and_command(self):
        base = {'PATH': '/usr/bin:/bin', 'HOME': '/Users/x', 'TERM': 'xterm', 'ANTHROPIC_API_KEY': 'sk-made-up',
                'CLAUDE_CODE_OAUTH_TOKEN': 'made-up', 'CLAUDE_CODE_USE_BEDROCK': '1', 'CLAUDE_CODE_USE_VERTEX': '1',
                'CLAUDE_CODE_USE_FOUNDRY': '1', 'CLAUDE_CODE_USE_MANTLE': '1', 'CLAUDE_CODE_USE_ANTHROPIC_AWS': '1',
                'ANTHROPIC_BASE_URL': 'https://elsewhere.example.com'}
        before = dict(base)
        argv, env = G.claude_command(['--model', 'opus', 'fix the tests'], base, gateway_port=7797,
                                     gateway_secret=SECRET)
        self.assertEqual(argv, ['claude', '--model', 'opus', 'fix the tests'])
        self.assertEqual(env, {'PATH': '/usr/bin:/bin', 'HOME': '/Users/x', 'TERM': 'xterm',
                               'ANTHROPIC_BASE_URL': 'http://127.0.0.1:7797', 'ANTHROPIC_AUTH_TOKEN': SECRET})
        self.assertEqual(base, before)                     # the terminal's own environment is not touched

    def test_it_writes_nothing(self):
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home, True)
        with mock.patch.dict(os.environ, {'SWITCHBOARD_HOME': os.path.join(home, 'data')}):
            G.claude_command([], {}, gateway_port=7797, gateway_secret=SECRET)
            self.assertEqual(os.listdir(home), [])


class SecretAndPort(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        patch = mock.patch.dict(os.environ, {'SWITCHBOARD_HOME': os.path.join(self.home, 'data')})
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_secret_is_random_private_and_kept(self):
        first = G.secret()
        self.assertEqual(os.listdir(core.data_dir()), ['gateway-secret'])      # no draft left behind
        self.assertTrue(first.startswith('sbgw-') and len(first) > 40)
        self.assertEqual(G.secret(), first)
        self.assertEqual(stat.S_IMODE(os.stat(core.data_path('gateway-secret')).st_mode), 0o600)
        os.remove(core.data_path('gateway-secret'))
        self.assertNotEqual(G.secret(), first)

    def test_port_from_settings(self):
        self.assertEqual(G.port(), G.DEFAULT_PORT)
        core.set_setting('gateway_port', 7123)
        self.assertEqual(G.port(), 7123)
        core.set_setting('gateway_port', 'nope')
        self.assertEqual(G.port(), G.DEFAULT_PORT)

    def test_claude_command_uses_them(self):
        core.set_setting('gateway_port', 7123)
        argv, env = G.claude_command([], {})
        self.assertEqual(env['ANTHROPIC_BASE_URL'], 'http://127.0.0.1:7123')
        self.assertEqual(env['ANTHROPIC_AUTH_TOKEN'], G.secret())


class TablesAndLimits(unittest.TestCase):
    def test_the_fail_over_table_is_data_with_its_sources(self):
        self.assertGreaterEqual(len(G.FAILOVER), 15)
        for row in G.FAILOVER:
            when, category, moves, url, quote = row
            self.assertTrue(url.startswith('https://'), row)
            self.assertTrue(quote and when, row)
            self.assertIn(category, set(G.RANK) | {'by type', 'never'}, row)
            self.assertEqual(moves, category != 'never', row)

    def test_classify(self):
        self.assertEqual(G.classify(200, json.dumps(message_json()).encode()), '')
        self.assertEqual(G.classify(200, b'{"input_tokens": 3}'), '')
        self.assertEqual(G.classify(200, b'not json'), '')
        self.assertEqual(G.classify(403, b'{}', 'anthropic'), '')
        self.assertEqual(G.classify(403, json.dumps(api_error('permission_error')).encode(), 'openrouter'), '')
        self.assertEqual(G.classify(400, json.dumps(api_error('invalid_request_error', 'x')).encode()), '')

    def test_limits_each_have_a_doc_sentence(self):
        rows = G.limits()
        self.assertGreaterEqual(len(rows), 10)
        for row in rows:
            self.assertEqual(sorted(row), ['limit', 'quote', 'url'])
            self.assertTrue(row['url'].startswith('https://'))
            self.assertGreater(len(row['quote']), 20)
        text = ' '.join(r['limit'] for r in rows)
        for words in ('Claude models only', 'API keys only', 'OpenRouter', 'Remote Control', 'settings file'):
            self.assertIn(words, text)
        self.assertIn('LIMITS', G.__doc__)


if __name__ == '__main__':
    unittest.main()
