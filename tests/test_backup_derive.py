import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The slow step of an accounts file's protection costs a guesser what it costs the person: one PBKDF2 output of 32
# bytes, its whole cost, and the cipher secret and the check key both come from it (HMAC-SHA256 under two labels). Had
# PBKDF2 been asked for 64 bytes, the check key would be its second block alone, and a guess could be tested against
# the passphrase check for half the rounds the person pays.
import hashlib
import hmac
import unicodedata
import unittest
from unittest import mock

from switchboard import backup

SALT = bytes(range(16))


class OneOutput(unittest.TestCase):
    def test_pbkdf2_is_asked_once_for_32_bytes(self):
        asked = []
        real = hashlib.pbkdf2_hmac

        def spy(name, password, salt, rounds, dklen=None):
            asked.append((name, rounds, dklen))
            return real(name, password, salt, rounds, dklen)
        with mock.patch.object(backup.hashlib, 'pbkdf2_hmac', spy):
            backup.derive('a made-up passphrase', SALT, 1000)
        self.assertEqual(asked, [('sha256', 1000, 32)])

    def test_both_keys_come_from_it_under_two_labels(self):
        words = 'café au lait, please'
        dk = hashlib.pbkdf2_hmac('sha256', unicodedata.normalize('NFC', words).encode('utf-8'), SALT, 1000, 32)
        secret, check_key = backup.derive(words, SALT, 1000)
        self.assertEqual(secret, hmac.new(dk, b'switchboard-accounts cipher', hashlib.sha256).digest())
        self.assertEqual(check_key, hmac.new(dk, b'switchboard-accounts check', hashlib.sha256).digest())
        self.assertEqual((len(secret), len(check_key)), (32, 32))
        self.assertNotEqual(secret, check_key)

    def test_the_format_is_still_version_1_and_a_file_round_trips(self):
        data = backup.seal(b'{"schema": 1, "accounts": []}', 'a made-up passphrase')
        self.assertEqual(backup.read_header(data)[0]['version'], 1)
        self.assertEqual(backup.open_sealed(data, 'a made-up passphrase'), b'{"schema": 1, "accounts": []}')


if __name__ == '__main__':
    unittest.main()
