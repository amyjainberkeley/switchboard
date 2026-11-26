"""A usage read never follows a redirect: following one would send the account's Bearer token to the address the
answer names. The 3xx comes back as the answer, a failed read."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from switchboard.accounts import usage


def serve(handler):
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


class AReadNeverFollowsARedirect(unittest.TestCase):
    def setUp(self):
        self.seen = []
        seen = self.seen

        class Elsewhere(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.headers.get('Authorization'))
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{}')

            def log_message(self, *a):
                pass
        self.other = serve(Elsewhere)
        to = 'http://127.0.0.1:%d/elsewhere' % self.other.server_address[1]

        class Moved(BaseHTTPRequestHandler):
            code = 302

            def do_GET(self):
                self.send_response(self.code)
                self.send_header('Location', to)
                self.send_header('Content-Length', '0')
                self.end_headers()

            def log_message(self, *a):
                pass
        self.moved_cls = Moved
        self.moved = serve(Moved)
        for s in (self.other, self.moved):
            self.addCleanup(s.server_close)
            self.addCleanup(s.shutdown)
        patch = mock.patch.dict(os.environ, {'no_proxy': '*', 'NO_PROXY': '*'})    # straight to the local servers
        patch.start()
        self.addCleanup(patch.stop)

    def get(self):
        url = 'http://127.0.0.1:%d/api/oauth/usage' % self.moved.server_address[1]
        return usage.http_get(url, {'Authorization': 'Bearer sk-ant-oat01-secret'}, 10)

    def test_a_302_is_the_answer_and_the_token_goes_nowhere_else(self):
        resp = self.get()
        self.assertEqual(resp.status, 302)
        self.assertEqual(self.seen, [])

    def test_every_redirect_status_is_a_failed_read(self):
        for code in (301, 303, 307, 308):
            self.moved_cls.code = code
            self.assertEqual(self.get().status, code)
        self.assertEqual(self.seen, [])


if __name__ == '__main__':
    unittest.main()
