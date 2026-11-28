import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The two settings of Claude Code's plain login file, `auto_policy.hold_signin_file` (the hold: a spare copy after a
# put-back) and `auto_policy.keep_signin_file` (the standing copy of the sign-in), are read by accounts.putback, not by
# policy.settings_from. `switchboard config` and doctor list the keys in the settings file that nothing reads
# (config.not_read): these two must never be listed there, nor be offered as a typing mistake of another key, and a
# real typing mistake of either still names it.
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import config

ROOT = os.path.join(HERE, '..')


class TheSigninFileKeys(unittest.TestCase):

    def test_both_turned_off_are_not_said_to_be_read_by_nothing(self):
        s = {'auto_policy': {'keep_signin_file': False, 'hold_signin_file': False}}
        self.assertEqual(config.not_read(s), [])

    def test_both_turned_on_are_not_said_to_be_read_by_nothing(self):
        s = {'auto_policy': {'keep_signin_file': True, 'hold_signin_file': True, 'weekly_at': 90}}
        self.assertEqual(config.not_read(s), [])

    def test_a_typing_mistake_names_the_real_key(self):
        found = dict(config.not_read({'auto_policy': {'keep_signin_fle': False}}))
        self.assertEqual(found, {'auto_policy.keep_signin_fle': 'auto_policy.keep_signin_file'})

    def test_every_auto_policy_key_putback_reads_is_known(self):
        """The keys of `auto_policy` that accounts/putback.py reads (`policy.get('...')`): never said to be read by
        nothing."""
        with open(os.path.join(ROOT, 'switchboard', 'accounts', 'putback.py'), encoding='utf-8') as f:
            text = f.read()
        found = set(re.findall(r"\bpolicy\.get\('([a-z_]+)'\)", text))
        self.assertEqual(found, {'hold_signin_file', 'keep_signin_file'})
        known = set(config.POLICY_KEYS) | set(config.SIGNIN_FILE_KEYS)
        self.assertEqual(sorted(found - known), [])


if __name__ == '__main__':
    unittest.main()
