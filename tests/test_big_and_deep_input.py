import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Input from outside that is nested too deep or is too big is a plain refusal, never a Python error and never a long
# wait: an accounts file's header or payload, a readings document, the answer of the update check (read no further
# than its limit, its version no longer than a version is), and the rounds an accounts file asks for (a cap that keeps
# opening it to a few seconds).
import hashlib
import io
import json
import os
import re
import sys
import time
import unittest
from unittest import mock

import test_trust as T
from fakes_gateway import Upstream
from switchboard import backup, core, newer, readings
from switchboard.accounts import net
from test_backup_export import PASS

DEEP = 200000                   # brackets: far past Python's recursion limit


def summed(head):
    """A file whose plain last line is right for `head` (anyone can work it out)."""
    return head + ('sum %s\n' % hashlib.sha256(head).hexdigest()).encode('ascii')


class TooDeep(unittest.TestCase):
    def test_an_accounts_files_header(self):
        data = summed(backup.MAGIC + b'[' * DEEP + b'\n' + b'x' * 64 + b'\n')
        with self.assertRaises(backup.FileError) as e:
            backup.read_header(data)
        self.assertEqual(e.exception.kind, backup.CHANGED)

    def test_an_accounts_files_payload(self):
        for raw in (b'[' * DEEP, b'{"a":' * DEEP):
            with self.assertRaises(backup.FileError) as e:
                backup._payload(raw)
            self.assertEqual(e.exception.kind, backup.CHANGED)

    def test_a_readings_document(self):
        fake = mock.Mock()
        fake.buffer = io.BytesIO(b'[' * DEEP)
        fake.isatty = lambda: False
        with mock.patch.object(sys, 'stdin', fake), self.assertRaises(core.SwitchError) as e:
            readings.main(['take', '-'], out=io.StringIO(), err=io.StringIO())
        self.assertEqual(str(e.exception), "This isn't what `switchboard readings give` prints. Nothing was taken.")


class TooMany(unittest.TestCase):
    def file_with_rounds(self, rounds):
        """A file as it would be with this many rounds in its header (only the plain parts can be checked)."""
        data = backup.seal(b'{"schema": 1, "accounts": []}', PASS)
        head = data[:data.rindex(b'\nsum ') + 1]
        header = json.loads(head[len(backup.MAGIC):head.index(b'\n', len(backup.MAGIC))])
        header['rounds'] = rounds
        line = json.dumps(header, ensure_ascii=True, separators=(', ', ': ')).encode('ascii')
        start = len(backup.MAGIC)
        return summed(head[:start] + line + head[head.index(b'\n', start):])

    def test_rounds_past_the_cap_read_as_newer_and_the_cap_is_a_few_seconds(self):
        self.assertLessEqual(backup.ROUNDS_MAX, 3000000)
        self.assertEqual(backup.read_header(self.file_with_rounds(backup.ROUNDS_MAX))[0]['rounds'], backup.ROUNDS_MAX)
        with self.assertRaises(backup.FileError) as e:
            backup.read_header(self.file_with_rounds(backup.ROUNDS_MAX + 1))
        self.assertEqual(e.exception.kind, backup.NEWER)

    def test_a_version_has_a_length_cap(self):
        self.assertEqual(newer.compare('9.0.0', '0'), 1)
        self.assertIsNone(newer.compare('1' * 33, '0'))
        self.assertIsNone(newer.compare('1' * (1 << 20), '0'))
        self.assertIsNone(newer.compare('0', '1.' * 20 + '1'))
        self.assertIsNone(newer.version_of_text("VERSION = '%s'\n" % ('9' * 100)))


def setUpModule():
    T.setUpModule()


def tearDownModule():
    T.tearDownModule()


class TheUpdateChecksAnswer(T.Case):
    """The answer is read no further than newer.MOST: a server that sends more, slowly, is not waited for."""

    def stall(self, up):
        def answer(h, record):
            h.send_response(200)
            h.send_header('Content-Length', str(newer.MOST * 8))
            h.end_headers()
            h.wfile.write(b"VERSION = '9.0.0'\n" + b'#' * (newer.MOST + 4096))
            h.wfile.flush()
            up.release.wait(60)                      # the rest never comes
        return answer

    def test_net_reads_up_to_its_limit_and_stops(self):
        os.environ['NODE_EXTRA_CA_CERTS'] = T.CERTS['a']
        up = Upstream(None, tls=T.server_tls())
        up.script = self.stall(up)
        self.addCleanup(up.stop)
        start = time.monotonic()
        status, _, body = net.Http(most=1000).request('GET', up.url + '/x', timeout_s=30)
        self.assertEqual((status, len(body)), (200, 1001))
        self.assertLess(time.monotonic() - start, 20)

    def test_the_update_check_says_no_version_instead_of_waiting(self):
        os.environ['NODE_EXTRA_CA_CERTS'] = T.CERTS['a']
        up = Upstream(None, tls=T.server_tls())
        up.script = self.stall(up)
        self.addCleanup(up.stop)
        os.makedirs(os.path.join(self.data, 'app'))
        with open(os.path.join(self.data, 'app', 'source'), 'w') as f:
            f.write(up.url + '/sb/main/install.sh\n')
        start = time.monotonic()
        found = newer.look()
        self.assertLess(time.monotonic() - start, newer.TIMEOUT_S)
        self.assertTrue(re.search(r'sent no version Switchboard could read', found['problem'] or ''), found)


if __name__ == '__main__':
    unittest.main()
