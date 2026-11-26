import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Shared pieces for the gateway tests (no tests of its own): a made-up upstream lane on a random loopback
# port, made-up replies in the Claude API's shapes, and a client that talks to the gateway as Claude Code would.
#
# Nothing here reaches the network: every server is on 127.0.0.1 with a port the system picks, and every key,
# secret and reply is made up.
import http.client
import json
import os
import socket
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import gateway as G

SECRET = 'sbgw-made-up-secret-for-tests-0123456789'
# Built from pieces, so no key-shaped text sits in this file.
KEY_A = 'sk-ant-' + 'api03-made-up-key-0001'
KEY_B = 'sk-or-' + 'v1-made-up-key-0002'
KEY_C = 'sk-ant-' + 'api03-made-up-key-0003'
KEYS = {'a': KEY_A, 'b': KEY_B, 'c': KEY_C}
OAUTH = G.OAUTH_TOKEN_PREFIX + '01-made-up-login-token-0004'


def event(kind, obj):
    return ('event: %s\ndata: %s\n\n' % (kind, json.dumps(obj))).encode()


START = event('message_start', {'type': 'message_start', 'message': {
    'id': 'msg_made_up', 'type': 'message', 'role': 'assistant', 'content': [], 'model': 'claude-opus-5-5',
    'stop_reason': None, 'stop_sequence': None, 'usage': {'input_tokens': 25, 'output_tokens': 1}}})
PING = event('ping', {'type': 'ping'})
BLOCK = event('content_block_start', {'type': 'content_block_start', 'index': 0,
                                      'content_block': {'type': 'text', 'text': ''}})


def delta(text):
    return event('content_block_delta', {'type': 'content_block_delta', 'index': 0,
                                         'delta': {'type': 'text_delta', 'text': text}})


BLOCK_STOP = event('content_block_stop', {'type': 'content_block_stop', 'index': 0})
MSG_DELTA = event('message_delta', {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn',
                                                                        'stop_sequence': None},
                                    'usage': {'output_tokens': 15}})
STOP = event('message_stop', {'type': 'message_stop'})


def error_event(kind, message='made-up'):
    return event('error', {'type': 'error', 'error': {'type': kind, 'message': message}})


def stream_of(text='Hello'):
    return [START, PING, BLOCK, delta(text), BLOCK_STOP, MSG_DELTA, STOP]


def message_json(text='Hello', model='claude-opus-5-5'):
    return {'id': 'msg_made_up', 'type': 'message', 'role': 'assistant', 'model': model,
            'content': [{'type': 'text', 'text': text}], 'stop_reason': 'end_turn', 'stop_sequence': None,
            'usage': {'input_tokens': 12, 'output_tokens': 18, 'cache_read_input_tokens': 3,
                      'cache_creation_input_tokens': 0}}


def api_error(kind, message='made-up', **more):
    err = {'type': kind, 'message': message}
    err.update(more)
    return {'type': 'error', 'error': err, 'request_id': 'req_made_up'}


# ---------------------------------------------------------------------------------------------------------------
# A made-up lane
# ---------------------------------------------------------------------------------------------------------------

class _UpHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def _any(self):
        n = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(n) if n else b''
        up = self.server.up
        record = {'method': self.command, 'path': self.path, 'headers': list(self.headers.items()), 'body': body}
        with up.lock:
            up.requests.append(record)
        try:
            up.script(self, record)
        except (BrokenPipeError, ConnectionResetError):
            pass

    do_GET = do_POST = do_HEAD = _any

    # the ways a lane answers
    def send_json(self, status, obj, headers=(), raw=None):
        data = raw if raw is not None else json.dumps(obj).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        for k, v in headers:
            self.send_header(k, v)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_events(self, pieces, headers=(), gap=0.0, wait=None, finish=True):
        """Each piece is one chunk. `wait` = (index, Event): stop before that piece until the Event is set."""
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        for k, v in headers:
            self.send_header(k, v)
        self.send_header('Transfer-Encoding', 'chunked')
        self.end_headers()
        self.wfile.flush()
        for i, piece in enumerate(pieces):
            if wait and wait[0] == i:
                wait[1].wait(10)
            if gap:
                time.sleep(gap)
            self.wfile.write(b'%X\r\n' % len(piece) + piece + b'\r\n')
            self.wfile.flush()
        if finish:
            self.wfile.write(b'0\r\n\r\n')
            self.wfile.flush()
        else:                                            # break the connection without finishing the reply
            self.close_connection = True
            self.connection.shutdown(socket.SHUT_RDWR)


class _UpServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass


class Upstream(object):
    """A made-up lane on 127.0.0.1 with a port the system picks. script(handler, record) answers each request."""

    def __init__(self, script, tls=None):
        self.script = script
        self.requests = []
        self.lock = threading.Lock()
        self.release = threading.Event()             # set at the end of a test, so nothing waits forever
        self.server = _UpServer(('127.0.0.1', 0), _UpHandler)
        self.server.up = self
        if tls is not None:                          # a server-side ssl.SSLContext: the lane speaks https
            self.server.socket = tls.wrap_socket(self.server.socket, server_side=True)
        threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True).start()
        self.url = '%s://%s:%d' % ('https' if tls else 'http', 'localhost' if tls else '127.0.0.1',
                                   self.server.server_address[1])

    def stop(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()

    def count(self):
        with self.lock:
            return len(self.requests)


def closed_port():
    """A loopback port where nothing listens (a lane that cannot be reached)."""
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def lane(name, up, kind='anthropic', names=None, url=None):
    auth = 'x-api-key' if kind == 'anthropic' else 'bearer'
    return {'name': name, 'kind': kind, 'base_url': url or up.url, 'auth': auth, 'names': names}


# ---------------------------------------------------------------------------------------------------------------
# Talking to the gateway as Claude Code does
# ---------------------------------------------------------------------------------------------------------------

def request_body(model='claude-opus-5-5', stream=True, text='Say hello'):
    return json.dumps({'model': model, 'max_tokens': 64, 'stream': stream,
                       'messages': [{'role': 'user', 'content': text}]}).encode()


def claude_headers(secret=SECRET, extra=()):
    headers = [('Authorization', 'Bearer ' + secret), ('anthropic-version', '2023-06-01'),
               ('content-type', 'application/json'), ('anthropic-beta', 'interleaved-thinking-2025-05-14')]
    return headers + list(extra)


def call(port, method='POST', path='/v1/messages?beta=true', body=None, headers=None, host=None, timeout=15):
    """(status, headers dict in lower case, body bytes)."""
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=timeout)
    try:
        conn.putrequest(method, path, skip_host=host is not None, skip_accept_encoding=True)
        if host is not None:
            conn.putheader('Host', host)
        for k, v in (claude_headers() if headers is None else headers):
            conn.putheader(k, v)
        if body is not None:
            conn.putheader('Content-Length', str(len(body)))
        conn.endheaders(body)
        resp = conn.getresponse()
        data = resp.read()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, data
    finally:
        conn.close()


class GatewayCase(unittest.TestCase):
    """Starts made-up lanes and a gateway on random loopback ports, and stops them after the test."""

    gateway_options = {}

    def upstream(self, script, tls=None):
        up = Upstream(script, tls)
        self.addCleanup(up.stop)
        return up

    def gateway(self, lanes, keys=None, **options):
        merged = dict(self.gateway_options)
        merged.update(options)
        store = KEYS if keys is None else keys
        gw = G.Gateway(lambda: lanes, SECRET, lambda name: store[name], port=0, **merged)
        gw.start(poll_interval=0.05)
        self.addCleanup(gw.stop)
        return gw


def wait_for_log(gw, count, seconds=5):
    """The log, once it has `count` entries (the entry is written just after the reply is sent)."""
    end = time.time() + seconds
    while len(gw.log()) < count and time.time() < end:
        time.sleep(0.01)
    return gw.log()
