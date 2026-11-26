import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# What the page's server answers to a request it can't use, and the headers every answer carries. Run with:
#   SWITCHBOARD_HOME="$(mktemp -d)/data" taskpolicy -b python3 -m unittest discover -s tests -p 'test_server_answers.py'
# The server listens on a free port of 127.0.0.1; every answer behind it is a stand-in, so nothing real is read.
import json
import os
import sys
import unittest
import urllib.error
import urllib.request
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import server
from test_server_cross_site import DIRECT, PageServer


class ABodyThatIsNotJson(PageServer):
    def setUp(self):
        PageServer.setUp(self)
        self.acted = []
        p = mock.patch.object(server, 'act', lambda path, body: self.acted.append((path, body)) or {})
        p.start()
        self.addCleanup(p.stop)

    def post(self, data):
        req = urllib.request.Request('http://127.0.0.1:%d/api/refresh' % self.port, data=data,
                                     headers={'Content-Type': 'application/json', 'X-Switchboard': '1'})
        try:
            with DIRECT.open(req, timeout=10) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.load(e)

    def test_is_a_400_with_a_plain_sentence_and_nothing_is_done(self):
        for data in (b'{not json', b'\xff\xfe', b'[1, 2'):
            self.assertEqual(self.post(data), (400, {'error': 'That request is not JSON.'}), data)
        self.assertEqual(self.acted, [])
        self.assertEqual(self.lines, [])

    def test_a_json_body_is_still_acted_on(self):
        self.assertEqual(self.post(b'{"a": 1}'), (200, {'ok': True}))
        self.assertEqual(self.post(b''), (200, {'ok': True}))
        self.assertEqual(self.acted, [('/api/refresh', {'a': 1}), ('/api/refresh', {})])


class EveryAnswersHeaders(PageServer):
    def headers(self, path, data=None):
        req = urllib.request.Request('http://127.0.0.1:%d%s' % (self.port, path), data=data,
                                     headers={'X-Switchboard': '1'} if data is not None else {})
        try:
            with DIRECT.open(req, timeout=10) as r:
                return r.status, r.headers
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.headers

    def test_say_nosniff_once_and_keep_the_others(self):
        for path, data, code in (('/', None, 200), ('/api/state', None, 200), ('/api/nothing', None, 404),
                                 ('/api/refresh', b'{oops', 400)):
            got, h = self.headers(path, data)
            self.assertEqual(got, code, path)
            self.assertEqual(h.get_all('X-Content-Type-Options'), ['nosniff'], path)
            self.assertEqual(h.get_all('X-Frame-Options'), ['DENY'], path)
            self.assertEqual(h.get_all('Cache-Control'), ['no-store'], path)
            self.assertIsNone(h.get('Content-Security-Policy'), path)


if __name__ == '__main__':
    unittest.main()
