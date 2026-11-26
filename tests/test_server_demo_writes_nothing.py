"""`switchboard demo` writes nothing: a click in the demo and a request the page refuses leave no data folder behind
(no switchboard.log, no ~/.switchboard)."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import http.client
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

from switchboard import core, demo, server


class DemoWritesNothing(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = True
        demo.STATE.clear()
        demo.FACTS.clear()
        self.addCleanup(demo.STATE.clear)
        self.addCleanup(demo.FACTS.clear)
        self.data = os.path.join(tempfile.mkdtemp(prefix='switchboard-demo-'), 'data')    # not made: must stay so
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.port = self.httpd.server_address[1]
        env = mock.patch.dict(os.environ, {'SWITCHBOARD_PORT': str(self.port), 'SWITCHBOARD_HOME': self.data})
        env.start()
        self.addCleanup(env.stop)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def post(self, path, body, ours=True):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        try:
            headers = {'Content-Type': 'application/json', 'Host': 'localhost:%d' % self.port}
            if ours:
                headers['X-Switchboard'] = '1'
            c.request('POST', path, json.dumps(body), headers)
            r = c.getresponse()
            return r.status, r.read()
        finally:
            c.close()

    def test_a_click_and_a_refused_request_leave_no_data_folder(self):
        demo._ready()
        email = next(iter(demo.STATE['accounts']))
        status, _ = self.post('/api/switch', {'email': email})
        self.assertEqual(status, 200)
        self.assertEqual(demo.STATE['active'], email)               # the click did what it does in the demo
        status, _ = self.post('/api/switch', {'email': email}, ours=False)
        self.assertEqual(status, 403)
        core.log('anything at all')
        core.log_flush()
        self.assertFalse(os.path.exists(self.data), os.listdir(self.data) if os.path.isdir(self.data) else '')


if __name__ == '__main__':
    unittest.main()
