import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# trust.doctor_line(): the one line `switchboard doctor` can print about what secure connections trust, as
# (good, text, what to do), the shape of doctor's own line(). The stand-in macOS of test_trust.
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)

from switchboard import trust
import test_trust
from test_trust import CERTS


def setUpModule():
    test_trust.setUpModule()


def tearDownModule():
    test_trust.tearDownModule()


class DoctorLine(test_trust.Case):
    def test_this_mac_and_the_file(self):
        os.environ['NODE_EXTRA_CA_CERTS'] = CERTS['a']
        self.assertEqual(trust.doctor_line(), (True, "Secure connections: Python's certificates, this Mac's trusted "
                                                     'certificates (1 added on this Mac), and the file in '
                                                     'NODE_EXTRA_CA_CERTS', ''))

    def test_each_source_by_its_name(self):
        self.assertEqual(trust.doctor_line()[1], "Secure connections: Python's certificates and this Mac's trusted "
                                                 'certificates (1 added on this Mac)')
        os.environ['SSL_CERT_FILE'] = CERTS['b']
        trust.set_mode('python')
        self.assertEqual(trust.doctor_line()[1], "Secure connections: Python's certificates and the file in "
                                                 'SSL_CERT_FILE')
        del os.environ['SSL_CERT_FILE']
        self.assertEqual(trust.doctor_line(), (True, "Secure connections: Python's certificates", ''))
        trust.set_mode('mac')
        os.remove(os.path.join(self.trusted, 'a'))
        os.remove(self.login)
        self.assertEqual(trust.doctor_line()[1], "Secure connections: Python's certificates and this Mac's trusted "
                                                 'certificates (none added on this Mac)')

    def test_a_named_file_that_does_not_load_is_a_line_to_look_at(self):
        trust.set_mode('python')
        os.environ['NODE_EXTRA_CA_CERTS'] = os.path.join(self.tmp, 'gone.pem')
        self.assertEqual(trust.doctor_line(), (False, "Secure connections: Python's certificates",
                                               'NODE_EXTRA_CA_CERTS names a file that is not there.'))
        junk = os.path.join(self.tmp, 'junk.pem')
        with open(junk, 'w') as f:
            f.write('not a certificate\n')
        os.environ['SSL_CERT_FILE'] = junk
        del os.environ['NODE_EXTRA_CA_CERTS']
        self.assertEqual(trust.doctor_line()[2], 'SSL_CERT_FILE names a file with no certificate in it that can be '
                                                 'read.')


if __name__ == '__main__':
    unittest.main()
