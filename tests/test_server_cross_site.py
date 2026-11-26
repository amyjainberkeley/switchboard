import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The page's server refuses a GET to /api/... that the browser says another website started (Sec-Fetch-Site), as it
# already did for /api/preview; a request with no such header (the command line, the menu bar, the link app) and the
# page itself are served as before. Run with:
#   SWITCHBOARD_HOME="$(mktemp -d)/data" taskpolicy -b python3 -m unittest discover -s tests -p 'test_server_cross_site.py'
# The server listens on a free port of 127.0.0.1; every answer behind it is a stand-in, so nothing real is read.
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import memorypage, server

REAL_PAGES = (7798, 7799)
DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))
ROUTES = ('/api/state', '/api/usage?fresh=1', '/api/memory?q=x&fresh=1')


class PageServer(unittest.TestCase):
    """The real handler on a free port, with stand-ins for what its routes read, and its log lines kept."""

    def setUp(self):
        self.asked, self.lines = [], []
        for target, name, value in (
                (server.SNAPSHOT, 'answer', lambda: self.asked.append('state') or (200, {'accounts': []})),
                (server, 'usage_view', lambda fresh: self.asked.append(('usage', fresh)) or {'ok': True}),
                (memorypage, 'view', lambda q, fresh: self.asked.append(('memory', q, fresh)) or {'ok': True}),
                (server, 'log', self.lines.append)):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        httpd = server.PageServer(('127.0.0.1', 0), server.Handler)
        self.port = httpd.server_address[1]
        self.assertNotIn(self.port, REAL_PAGES)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        env = mock.patch.dict(os.environ, {'SWITCHBOARD_PORT': str(self.port)})
        env.start()
        self.addCleanup(env.stop)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def get(self, path, headers=None):
        req = urllib.request.Request('http://127.0.0.1:%d%s' % (self.port, path), headers=headers or {})
        try:
            with DIRECT.open(req, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.read()


class AnotherWebsitesRequest(PageServer):
    def test_cross_site_and_same_site_are_refused_and_nothing_is_read(self):
        for site in ('cross-site', 'same-site'):
            for path in ROUTES:
                code, body = self.get(path, {'Sec-Fetch-Site': site})
                self.assertEqual((code, json.loads(body)), (403, {'error': 'forbidden'}), (site, path))
        self.assertEqual(self.asked, [])
        self.assertEqual(self.lines, ['refused GET %s: another website asked for it' % p.split('?')[0]
                                      for _ in range(2) for p in ROUTES])

    def test_the_same_page_a_typed_address_and_no_header_are_served(self):
        for headers in ({'Sec-Fetch-Site': 'same-origin'}, {'Sec-Fetch-Site': 'none'}, {}):
            for path in ROUTES:
                code, body = self.get(path, headers)
                self.assertEqual(code, 200, (headers, path))
        self.assertEqual(self.asked, [x for _ in range(3) for x in ('state', ('usage', True), ('memory', 'x', True))])
        self.assertEqual(self.lines, [])

    def test_the_page_itself_is_not_refused(self):
        code, body = self.get('/', {'Sec-Fetch-Site': 'cross-site'})
        self.assertEqual(code, 200)
        self.assertIn(b'<main', body)


if __name__ == '__main__':
    unittest.main()
