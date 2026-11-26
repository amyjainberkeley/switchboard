import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# `switchboard doctor` says three more things, each from the module that knows it: logins kept aside
# (leftovers.doctor_line), what secure connections trust (trust.doctor_line), and each key in the settings file that
# nothing reads (config.not_read_lines). Each is asked through a stand-in here; one that fails never stops doctor.
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import __main__ as cli
from switchboard import config, leftovers, trust
from test_cli_commands import Cli


class Doctor(Cli):
    def doctor(self, **stand_ins):
        lines = {'leftovers': lambda: None, 'trust': lambda: (True, "Secure connections: Python's certificates", ''),
                 'config': lambda s=None: []}
        lines.update(stand_ins)
        self.two()
        with mock.patch.object(cli, 'page_running', lambda: False), \
                mock.patch.object(cli.linkapp, 'status', lambda fresh=False: {'installed': False, 'on': False}), \
                mock.patch.object(cli.browser, 'present', lambda: False), \
                mock.patch.object(cli.browser, 'profiles', lambda: {}), \
                mock.patch.object(leftovers, 'doctor_line', lines['leftovers']), \
                mock.patch.object(trust, 'doctor_line', lines['trust']), \
                mock.patch.object(config, 'not_read_lines', lines['config']):
            return self.run_cli('doctor')[1]

    def test_logins_kept_aside(self):
        out = self.doctor(leftovers=lambda: '2 leftover logins (switchboard leftovers)')
        self.assertIn(' --  2 leftover logins (switchboard leftovers)', out)
        self.assertLess(out.index('Accounts: 2 kept'), out.index('leftover logins'))
        self.assertNotIn('leftover', self.doctor())

    def test_what_secure_connections_trust(self):
        out = self.doctor()
        self.assertIn(" ok  Secure connections: Python's certificates", out)
        out = self.doctor(trust=lambda: (False, "Secure connections: Python's certificates",
                                         'The file in SSL_CERT_FILE is not there.'))
        self.assertIn(" NO  Secure connections: Python's certificates\n       The file in SSL_CERT_FILE is not there.",
                      out)

    def test_a_key_nothing_reads(self):
        said = 'awak is in ~/.switchboard/settings.json, and nothing reads it. Did you mean awake?'
        out = self.doctor(config=lambda s=None: [said])
        self.assertIn(' --  ' + said, out)

    def test_one_that_fails_never_stops_doctor(self):
        def broken(*a):
            raise RuntimeError('made up')
        out = self.doctor(trust=broken, config=broken)
        self.assertIn('Claude Code', out)
        self.assertNotIn('Secure connections', out)


if __name__ == '__main__':
    unittest.main()
