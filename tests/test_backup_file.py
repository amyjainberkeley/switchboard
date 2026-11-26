import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The protected accounts file (switchboard/backup.py): a round trip through the real /usr/bin/openssl (it reads and
# writes only pipes here); a wrong passphrase; one changed byte in each part; a file cut at every byte; a file from a
# newer version. Each refusal has its own line and code, and nothing is decrypted before the tag holds. A stand-in
# openssl records what it was handed: no secret in an argument or the environment.
import base64
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import backup

PASS = 'correct horse battery staple'
PAYLOAD = json.dumps({'schema': 1, 'accounts': [{'email': 'maya@northwind.example', 'login': 'x' * 300}]}).encode()

_STAND_IN = r'''#!%(python)s
import json, os, sys
fds = sorted(int(n) for n in os.listdir('/dev/fd') if n.isdigit())
with open(%(log)r, 'a') as f:
    f.write(json.dumps({'argv': sys.argv[1:], 'env': dict(os.environ), 'fds': fds}) + '\n')
os.execv('/usr/bin/openssl', ['/usr/bin/openssl'] + sys.argv[1:])
'''


def stand_in(root):
    """A program that notes its arguments, environment and open descriptors, then runs the real openssl."""
    tool, log = os.path.join(root, 'stand-in-openssl'), os.path.join(root, 'openssl-calls.jsonl')
    with open(tool, 'w') as f:
        f.write(_STAND_IN % {'python': sys.executable, 'log': log})
    os.chmod(tool, 0o700)
    return tool, log


def calls(log):
    try:
        with open(log) as f:
            return [json.loads(line) for line in f if line.strip()]
    except OSError:
        return []


def resum(data):
    """The file with its last line worked out again: what someone who edits it on purpose would do."""
    head = data[:data.rindex(b'\nsum ') + 1]
    return head + ('sum %s\n' % hashlib.sha256(head).hexdigest()).encode('ascii')


def parts(data):
    """Where each part of a file starts and ends: {'magic', 'header', 'salt', 'body', 'tag', 'sum'}: (start, end)."""
    first = data.index(b'\n') + 1
    second = data.index(b'\n', first) + 1
    header = json.loads(data[first:second])
    salt_at = data.index(header['salt'].encode(), first)
    tag_at = data.rindex(b'\ntag ') + 1
    sum_at = data.rindex(b'\nsum ') + 1
    return {'magic': (0, first - 1), 'header': (first, second - 1), 'salt': (salt_at, salt_at + len(header['salt'])),
            'body': (second, tag_at - 1), 'tag': (tag_at + 4, sum_at - 1), 'sum': (sum_at + 4, len(data) - 1)}


def flip(data, i):
    b = bytearray(data)
    b[i] ^= 0x01
    return bytes(b)


class Scheme(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix='sb-backup-file-')
        cls.data = backup.seal(PAYLOAD, PASS)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def refused(self, data, kind, passphrase=PASS):
        with self.assertRaises(backup.FileError) as got:
            backup.open_sealed(data, passphrase)
        self.assertEqual((got.exception.kind, got.exception.code), (kind, backup.CODES[kind]), str(got.exception))
        self.assertIn('Nothing was kept', str(got.exception))
        return str(got.exception)

    def test_a_round_trip_through_the_real_openssl(self):
        self.assertEqual(backup.open_sealed(self.data, PASS), PAYLOAD)

    def test_the_file_is_ascii_with_its_first_and_last_lines_and_a_clear_header(self):
        self.data.decode('ascii')
        lines = self.data.split(b'\n')
        self.assertEqual(lines[0], b'switchboard-accounts')
        self.assertTrue(lines[-2].startswith(b'sum ') and lines[-3].startswith(b'tag '), lines[-3:])
        header = json.loads(lines[1])
        self.assertEqual((header['format'], header['version'], header['derive'], header['rounds'], header['cipher']),
                         ('switchboard-accounts', 1, 'pbkdf2-sha256', 600000, 'aes-256-cbc'))
        self.assertEqual(len(base64.b64decode(header['salt'])), 16)
        self.assertNotIn(b'maya@northwind.example', self.data)
        self.assertNotIn(b'xxxxxxxx', self.data)

    def test_two_files_of_one_payload_share_no_salt_and_no_ciphertext(self):
        other = backup.seal(PAYLOAD, PASS)
        a, b = backup.read_header(self.data), backup.read_header(other)
        self.assertNotEqual(a[0]['salt'], b[0]['salt'])
        self.assertNotEqual(a[0]['cipher_salt'], b[0]['cipher_salt'])
        self.assertNotEqual(a[1], b[1])

    def test_the_key_and_iv_are_the_ones_the_scheme_says(self):
        header, ct, _, _ = backup.read_header(self.data)
        secret, _ = backup.derive(PASS, base64.b64decode(header['salt']), header['rounds'])
        key, iv = backup.cipher_key_iv(secret, bytes.fromhex(header['cipher_salt']))
        # made-up data in a test: the key may go in arguments here, never in the program
        p = subprocess.run(['/usr/bin/openssl', 'enc', '-d', '-aes-256-cbc', '-nosalt', '-K', key.hex(), '-iv',
                            iv.hex()], input=ct, capture_output=True, env={'PATH': '/usr/bin:/bin'})
        self.assertEqual((p.returncode, p.stdout), (0, PAYLOAD), p.stderr)

    def test_a_wrong_passphrase(self):
        said = self.refused(self.data, backup.PASSPHRASE, 'correct horse battery stapler')
        self.assertEqual(said, 'That passphrase does not open this file. Nothing was kept.')

    def test_the_passphrase_is_read_as_the_same_text_however_it_was_typed(self):
        composed, decomposed = 'café au lait, please', 'café au lait, please'
        data = backup.seal(PAYLOAD, composed)
        self.assertEqual(backup.open_sealed(data, decomposed), PAYLOAD)

    def test_one_changed_byte_in_each_part(self):
        where = parts(self.data)
        for part in ('header', 'salt', 'body', 'tag', 'sum'):
            start, end = where[part]
            for i in sorted(set([start, (start + end) // 2, end - 1])):
                with self.subTest(part=part, at=i):
                    said = self.refused(flip(self.data, i), backup.CHANGED)
                    self.assertTrue(said.startswith('This file was changed or damaged'), said)
        for i in range(*where['magic']):
            with self.subTest(part='magic', at=i):
                self.assertIn("isn't a Switchboard accounts file", self.refused(flip(self.data, i), backup.CHANGED))

    def test_a_body_or_tag_changed_on_purpose_is_refused_before_openssl_sees_it(self):
        tool, log = stand_in(self.root)
        where = parts(self.data)
        for part in ('body', 'tag'):
            start, end = where[part]
            i = (start + end) // 2
            data = bytearray(self.data)
            data[i] = ord('A') if data[i] != ord('A') else ord('B')
            with self.subTest(part=part), self.assertRaises(backup.FileError) as got:
                backup.open_sealed(resum(bytes(data)), PASS, tool=tool)
            self.assertEqual(got.exception.kind, backup.CHANGED)
        self.assertEqual(calls(log), [])

    def test_a_salt_changed_on_purpose_reads_as_a_wrong_passphrase_and_keeps_nothing(self):
        start, end = parts(self.data)['salt']
        data = bytearray(self.data)
        data[start] = ord('A') if data[start] != ord('A') else ord('B')
        self.refused(resum(bytes(data)), backup.PASSPHRASE)

    def test_cut_at_every_byte(self):
        for n in range(len(self.data)):
            with self.subTest(cut_at=n):
                said = self.refused(self.data[:n], backup.CUT)
                self.assertTrue(said.startswith('This file is cut short'), said)

    def test_bytes_added_at_the_end(self):
        self.refused(self.data + b'\n', backup.CHANGED)
        self.refused(self.data + self.data, backup.CHANGED)

    def test_not_an_accounts_file(self):
        for data in (PAYLOAD, b'hello\n', b'\x00' * 100):
            with self.subTest(data=data[:10]):
                self.assertIn("isn't a Switchboard accounts file", self.refused(data, backup.CHANGED))

    def test_a_file_from_a_newer_version(self):
        lines = self.data.split(b'\n')
        header = json.loads(lines[1])
        for change in ({'version': 2}, {'rounds': 50000000}, {'derive': 'something-newer', 'version': 2}):
            with self.subTest(change=change):
                newer = dict(header, **change)
                data = b'\n'.join([lines[0], json.dumps(newer).encode()] + lines[2:])
                said = self.refused(resum(data), backup.NEWER)
                self.assertTrue(said.startswith('This file was made by a newer Switchboard'), said)
        other = b'\n'.join([lines[0], b'a header of another shape'] + lines[2:])
        self.refused(resum(other), backup.NEWER)

    def test_an_older_scheme_that_derives_too_few_rounds_is_refused(self):
        lines = self.data.split(b'\n')
        weak = dict(json.loads(lines[1]), rounds=1000)
        self.refused(resum(b'\n'.join([lines[0], json.dumps(weak).encode()] + lines[2:])), backup.CHANGED)

    def test_each_kind_has_its_own_code(self):
        self.assertEqual(sorted(backup.CODES.values()), [3, 4, 5, 6])


class WhatOpensslIsHanded(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='sb-backup-tool-')
        self.addCleanup(shutil.rmtree, self.root, True)
        self.tool, self.log = stand_in(self.root)

    def test_no_secret_in_any_argument_or_the_environment(self):
        data = backup.seal(PAYLOAD, PASS, tool=self.tool)
        self.assertEqual(backup.open_sealed(data, PASS, tool=self.tool), PAYLOAD)
        header = json.loads(data.split(b'\n')[1])
        secret, mac_key = backup.derive(PASS, base64.b64decode(header['salt']), header['rounds'])
        key, iv = backup.cipher_key_iv(secret, bytes.fromhex(header['cipher_salt']))
        got = calls(self.log)
        self.assertEqual([c['argv'][1] for c in got], ['-e', '-d'])
        for c in got:
            words = ' '.join(c['argv'] + ['%s=%s' % kv for kv in c['env'].items()])
            for said in (PASS, secret.hex(), mac_key.hex(), key.hex(), iv.hex()):
                self.assertNotIn(said, words)
            # macOS itself adds __CF_USER_TEXT_ENCODING to every process it starts (a text encoding number)
            self.assertEqual(set(c['env']) - {'__CF_USER_TEXT_ENCODING'}, {'PATH', 'LC_ALL'})
            fd = c['argv'][c['argv'].index('-pass') + 1]
            self.assertRegex(fd, r'^fd:\d+$')
            self.assertIn(int(fd[3:]), c['fds'])
            self.assertNotIn('-K', c['argv'])
            self.assertNotIn('-iv', c['argv'])
            self.assertNotIn('-k', c['argv'])

    def test_openssl_not_there_is_said_plainly(self):
        with self.assertRaises(backup.ToolError) as got:
            backup.seal(PAYLOAD, PASS, tool=os.path.join(self.root, 'no-such-openssl'))
        self.assertIn('is not there', str(got.exception))

    def test_the_stand_in_is_owner_only(self):
        self.assertEqual(stat.S_IMODE(os.stat(self.tool).st_mode), 0o700)


if __name__ == '__main__':
    unittest.main()
