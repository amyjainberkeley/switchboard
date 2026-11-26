"""`switchboard claude` starts Claude Code with the gateway's secret only when the program answering the gateway's
port is Switchboard's own gateway, told by the header its answers carry. Anything else that answers there is refused
in one sentence, and Claude Code is not started."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import io
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from switchboard import __main__ as cli
from switchboard import connect, core, gateway, lanes


class _Other(BaseHTTPRequestHandler):
    """Another program on the port: it answers the probe with 200, as anything could."""
    def do_HEAD(self):
        self.send_response(200)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def log_message(self, *args):
        pass


class GatewayIsOurs(unittest.TestCase):
    def serve_other(self):
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), _Other)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd.server_address[1]

    def serve_ours(self):
        gw = gateway.Gateway([], 'x' * 40, lambda name: None, port=0)
        port = gw.start(poll_interval=0.05)
        self.addCleanup(gw.stop)
        return port

    def test_the_probe_tells_the_gateway_from_another_program(self):
        ours, other = self.serve_ours(), self.serve_other()
        self.assertTrue(lanes.answers(ours))
        self.assertFalse(lanes.answers(other))
        self.assertEqual(lanes.hello(ours), 'ours')
        self.assertEqual(lanes.hello(other), 'other')

    def test_claude_refuses_another_program_and_starts_nothing(self):
        other = self.serve_other()
        started = []
        with mock.patch.object(lanes, 'lanes', lambda: [{'name': 'console', 'kind': 'anthropic', 'last4': 'abcd'}]), \
                mock.patch.object(gateway, 'port', lambda: other), \
                mock.patch.object(connect, 'claude_bin', lambda: '/made/up/claude'), \
                mock.patch.object(os, 'execve', lambda *a: started.append(a)), \
                mock.patch.object(sys, 'stderr', io.StringIO()):
            with self.assertRaises(core.SwitchError) as e:
                cli.claude_on_keys([])
        self.assertEqual(started, [])
        self.assertEqual(str(e.exception), "Another program answers on 127.0.0.1:%d, not Switchboard's keys gateway, "
                                           "so Claude Code was not started." % other)


if __name__ == '__main__':
    unittest.main()
