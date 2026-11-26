import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# A secure connection refused over a certificate says, in one sentence, which issuer was not trusted and the one
# thing to do, instead of Python's error text. Made-up servers on 127.0.0.1 with throwaway certificates (test_trust's),
# and the stand-in macOS of test_trust, which here trusts nothing unless a test says so.
import io
import os
import ssl
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)

from switchboard import __main__ as cli
from switchboard import trust
from switchboard.accounts import net, usage
from fakes_gateway import Upstream
import test_trust
from test_trust import CERTS, server_tls

PYTHON_TEXT = ('CERTIFICATE_VERIFY_FAILED', 'certificate verify failed', '[SSL', 'SSLCertVerificationError', 'errno')


def setUpModule():
    test_trust.setUpModule()


def tearDownModule():
    test_trust.tearDownModule()


def answer(h, record):
    h.send_json(200, None, raw=b"VERSION = '9.0.0'\n")


class Refused(test_trust.Case):
    def setUp(self):
        super().setUp()
        for name in os.listdir(self.trusted):                    # this Mac trusts none of the made-up roots
            os.remove(os.path.join(self.trusted, name))
        self.up = Upstream(answer, tls=server_tls())
        self.addCleanup(self.up.stop)
        self.where = self.up.url.split('//')[1]

    def refused(self):
        with self.assertRaises(net.NetError) as e:
            net.Http().request('GET', self.up.url + '/x', timeout_s=20)
        self.assertEqual(e.exception.kind, 'tls-cert')
        said = str(e.exception)[len('tls-cert: '):]
        for word in PYTHON_TEXT:
            self.assertNotIn(word, said)
        return said

    def test_it_names_the_issuer_and_the_one_thing_to_do(self):
        self.assertEqual(self.refused(), 'Switchboard did not trust the certificate %s showed (issued by "Made-up '
                                         'Root A"). If this network inspects secure connections, add its root '
                                         'certificate to the Keychain and set it to Always Trust.' % self.where)

    def test_with_python_alone_the_thing_to_do_is_the_file(self):
        trust.set_mode('python')
        self.assertTrue(self.refused().endswith('If this network inspects secure connections, name its root '
                                                'certificate file in NODE_EXTRA_CA_CERTS.'))

    def test_a_certificate_for_another_name(self):
        os.environ['NODE_EXTRA_CA_CERTS'] = CERTS['a']
        other = Upstream(answer, tls=server_tls('elsewhere'))
        self.addCleanup(other.stop)
        with self.assertRaises(net.NetError) as e:
            net.Http().request('GET', other.url + '/x', timeout_s=20)
        self.assertEqual(str(e.exception), 'tls-cert: The certificate %s showed is for another name, so Switchboard '
                                           'did not connect. Try again on another network.' % other.url.split('//')[1])

    def test_out_of_date_or_a_wrong_clock(self):
        error = ssl.SSLCertVerificationError('made-up')
        for code in (9, 10):
            error.verify_code = code
            trust._SAID.clear()
            self.assertEqual(trust.refusal('api.example', 443, error),
                             "The certificate api.example showed is out of date, or this Mac's clock is wrong. Check "
                             "the date and time in System Settings.")

    def test_usage_reads_say_the_same(self):
        env = mock.patch.dict(os.environ, {'no_proxy': '*'})
        env.start()
        self.addCleanup(env.stop)
        with self.assertRaises(usage.NetError) as e:
            usage.http_get(self.up.url + '/x', {}, 20)
        self.assertEqual((e.exception.kind, e.exception.detail), ('tls-cert', self.refused()))

    def test_the_check_for_a_newer_version_says_it(self):
        os.makedirs(os.path.join(self.data, 'app'))
        with open(os.path.join(self.data, 'app', 'source'), 'w') as f:
            f.write(self.up.url + '/sb/main/install.sh\n')
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(['update', '--check'])
        self.assertEqual(code, 1)
        self.assertEqual(out.getvalue(), 'Could not look for a newer version: Switchboard did not trust the '
                                         'certificate %s showed (issued by "Made-up Root A"). If this network '
                                         'inspects secure connections, add its root certificate to the Keychain and '
                                         'set it to Always Trust.\n' % self.where)
        self.assertEqual(self.up.requests, [])                  # nothing was sent: not the request, not the look

    def test_one_look_per_server_while_its_sentence_holds(self):
        looks = []
        real = trust._presented_issuer
        with mock.patch.object(trust, '_presented_issuer', lambda h, p: looks.append(h) or real(h, p)):
            first = self.refused()
            self.assertEqual(self.refused(), first)
        self.assertEqual(looks, ['localhost'])

    def test_no_issuer_to_name_when_the_look_finds_nothing(self):
        with mock.patch.object(trust, '_presented_issuer', lambda h, p: None):
            self.assertTrue(self.refused().startswith('Switchboard did not trust the certificate %s showed. If'
                                                      % self.where))


def der(tag, body):
    n = len(body)
    size = bytes([n]) if n < 128 else bytes([0x82, n >> 8, n & 0xff])
    return bytes([tag]) + size + body


class TheIssuersName(unittest.TestCase):
    """The issuer's name comes from a certificate anyone can make: only printable text, at most 80 characters."""

    def name(self, text, tag=0x0c, oid=b'\x55\x04\x03'):
        value = text.encode('utf-16-be' if tag == 0x1e else 'utf-8')
        return der(0x30, der(0x31, der(0x30, der(0x06, oid) + der(tag, value))))

    def test_plain_text_only(self):
        self.assertEqual(trust.name_said(self.name('Corp\x1b[31m Proxy\x07 CA\n')), 'Corp[31m Proxy CA')
        self.assertEqual(len(trust.name_said(self.name('x' * 500))), 80)
        self.assertEqual(trust.name_said(self.name('Corp CA', tag=0x1e)), 'Corp CA')
        self.assertEqual(trust.name_said(self.name('Corp Org', oid=b'\x55\x04\x0a')), 'Corp Org')
        self.assertIsNone(trust.name_said(self.name('Nowhere', oid=b'\x55\x04\x07')))

    def test_what_cannot_be_read_is_none(self):
        for junk in (b'', b'\x30', b'\x30\x85\x01\x02\x03\x04\x05', b'\x30\x05\x31\x03', b'\xff' * 40,
                     self.name('Corp')[:-3]):
            self.assertIsNone(trust.name_said(junk), junk)
            self.assertIsNone(trust.names(junk), junk)

    def test_a_real_certificate(self):
        with open(CERTS['server'][0]) as f:
            pair = trust.names(ssl.PEM_cert_to_DER_cert(f.read()))
        self.assertEqual([trust.name_said(x) for x in pair], ['Made-up Root A', 'localhost'])


if __name__ == '__main__':
    unittest.main()
