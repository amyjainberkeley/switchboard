import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# One TLS context for every secure connection to another machine (switchboard/trust.py): Python's defaults, the files
# named in SSL_CERT_FILE and NODE_EXTRA_CA_CERTS, and the roots this Mac trusts for TLS as macOS answers, kept in an
# owner-only file. Certificates here are throwaway ones made by /usr/bin/openssl for this run. macOS's `security` is a
# stand-in script at trust.TOOL (tests/_isolate.py refuses the real one, and so does trust.py in a test run); servers
# are made-up ones on 127.0.0.1.
import json
import os
import re
import shutil
import ssl
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)

from switchboard import core, gateway, newer, trust
from switchboard.accounts import net, usage
from fakes_gateway import Upstream

CERTS = {}
CONFIG = """[req]
distinguished_name = dn
prompt = no
[dn]
CN = %s
[root]
basicConstraints = critical,CA:TRUE
keyUsage = critical,keyCertSign,cRLSign
subjectKeyIdentifier = hash
[leaf]
basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature,keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = %s
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid
"""


def openssl(folder, *args):
    subprocess.run(['/usr/bin/openssl'] + list(args), cwd=folder, check=True, capture_output=True, timeout=60)


def make_root(folder, name, cn):
    cfg = os.path.join(folder, name + '.cnf')
    with open(cfg, 'w') as f:
        f.write(CONFIG % (cn, 'DNS:unused.example'))
    openssl(folder, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', name + '.key', '-out', name + '.pem',
            '-days', '2', '-config', cfg, '-extensions', 'root')
    return os.path.join(folder, name + '.pem')


def make_leaf(folder, name, cn, alt, ca):
    cfg = os.path.join(folder, name + '.cnf')
    with open(cfg, 'w') as f:
        f.write(CONFIG % (cn, alt))
    openssl(folder, 'req', '-new', '-newkey', 'rsa:2048', '-nodes', '-keyout', name + '.key', '-out', name + '.csr',
            '-config', cfg)
    openssl(folder, 'x509', '-req', '-in', name + '.csr', '-CA', ca + '.pem', '-CAkey', ca + '.key', '-set_serial',
            str(abs(hash(name)) % 100000 + 2), '-days', '2', '-out', name + '.pem', '-extfile', cfg, '-extensions',
            'leaf')
    return os.path.join(folder, name + '.pem'), os.path.join(folder, name + '.key')


def setUpModule():
    folder = tempfile.mkdtemp(prefix='switchboard-certs-')
    CERTS['folder'] = folder
    CERTS['a'] = make_root(folder, 'a', 'Made-up Root A')               # trusted by the stand-in macOS
    CERTS['b'] = make_root(folder, 'b', 'Made-up Root B')               # in a keychain, never trusted
    CERTS['c'], _ = make_leaf(folder, 'c', 'Made-up Leaf C', 'DNS:c.example', 'a')   # not a root
    CERTS['server'] = make_leaf(folder, 'server', 'localhost', 'DNS:localhost,IP:127.0.0.1', 'a')
    CERTS['elsewhere'] = make_leaf(folder, 'elsewhere', 'elsewhere.example', 'DNS:elsewhere.example', 'a')


def tearDownModule():
    shutil.rmtree(CERTS.get('folder') or '/nonexistent', True)


def text(path):
    with open(path) as f:
        return f.read()


def canonical(path):
    return ssl.DER_cert_to_PEM_cert(ssl.PEM_cert_to_DER_cert(text(path)))


def subjects(ctx):
    return set(dict(x[0] for x in c['subject']).get('commonName') for c in ctx.get_ca_certs())


def server_tls(which='server'):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(*CERTS[which])
    return ctx


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-trust-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, 'home')
        self.data = os.path.join(self.tmp, 'data')
        self.login = os.path.join(self.home, 'Library', 'Keychains', 'login.keychain-db')
        os.makedirs(os.path.dirname(self.login))
        with open(self.login, 'w') as f:
            f.write('made-up keychain\n')
        self.system = os.path.join(self.tmp, 'System.keychain')
        with open(self.system, 'w') as f:
            f.write('made-up keychain\n')
        env = mock.patch.dict(os.environ, {'HOME': self.home, 'SWITCHBOARD_HOME': self.data})
        env.start()
        self.addCleanup(env.stop)
        for k in ('SSL_CERT_FILE', 'SSL_CERT_DIR', 'NODE_EXTRA_CA_CERTS'):
            if k in os.environ:
                p = mock.patch.dict(os.environ)
                p.start()
                self.addCleanup(p.stop)
                del os.environ[k]
        self.calls = os.path.join(self.tmp, 'security.calls')
        self.keychain_pems = os.path.join(self.tmp, 'keychain.pem')
        with open(self.keychain_pems, 'w') as f:
            f.write('\n'.join(text(CERTS[k]) for k in ('a', 'b', 'c')) + text(CERTS['a']))     # A twice
        self.trusted = os.path.join(self.tmp, 'trusted')
        os.makedirs(self.trusted)
        for k in ('a', 'c'):                    # the stand-in calls C trusted too: it is never asked, never added
            with open(os.path.join(self.trusted, k), 'w') as f:
                f.write(canonical(CERTS[k]))
        self.tool = os.path.join(self.tmp, 'sec-standin')
        with open(self.tool, 'w') as f:
            f.write('#!/bin/sh\nprintf "%%s\\n" "$*" >> "%s"\ncase "$1" in\n'
                    '  find-certificate) cat "%s" ;;\n'
                    '  verify-cert) f=""; prev=""; for a in "$@"; do [ "$prev" = "-c" ] && f="$a"; prev="$a"; done\n'
                    '    for t in "%s"/*; do cmp -s "$f" "$t" && exit 0; done; exit 1 ;;\n'
                    '  *) exit 2 ;;\nesac\n' % (self.calls, self.keychain_pems, self.trusted))
        os.chmod(self.tool, 0o755)
        for name, value in (('TOOL', self.tool), ('SYSTEM_KEYCHAIN', self.system),
                            ('ADMIN_TRUST', os.path.join(self.tmp, 'Admin.plist'))):
            p = mock.patch.object(trust, name, value)
            p.start()
            self.addCleanup(p.stop)
        trust._BUILT.clear()
        trust._FAILED['at'] = None
        self.addCleanup(trust._BUILT.clear)
        trust._SAID.clear()
        self.addCleanup(trust._SAID.clear)

    def asked(self):
        try:
            return text(self.calls).splitlines()
        except OSError:
            return []

    def verified(self):
        return [x for x in self.asked() if x.startswith('verify-cert')]


class Sources(Case):
    def test_python_defaults_alone_without_anything_named(self):
        trust.set_mode('python')
        ctx = trust.context()
        self.assertGreater(len(ctx.get_ca_certs()), 10)
        self.assertFalse({'Made-up Root A', 'Made-up Root B'} & subjects(ctx))
        self.assertEqual(self.asked(), [])                          # python: macOS is never asked

    def test_ssl_cert_file_is_honoured_and_only_adds(self):
        trust.set_mode('python')
        os.environ['SSL_CERT_FILE'] = CERTS['a']
        ctx = trust.context()
        self.assertIn('Made-up Root A', subjects(ctx))
        self.assertGreater(len(ctx.get_ca_certs()), 10)             # Python's own are still there
        self.assertEqual(trust.FACTS['ssl_cert_file'], 'loaded')

    def test_node_extra_ca_certs_is_added_when_it_is_there_and_parses(self):
        trust.set_mode('python')
        os.environ['NODE_EXTRA_CA_CERTS'] = CERTS['a']
        ctx = trust.context()
        self.assertIn('Made-up Root A', subjects(ctx))
        self.assertGreater(len(ctx.get_ca_certs()), 10)
        self.assertEqual(trust.FACTS['node_extra'], 'loaded')
        for value, said in ((os.path.join(self.tmp, 'gone.pem'), 'missing'), (self.calls + '.x', 'unreadable')):
            if said == 'unreadable':
                with open(value, 'w') as f:
                    f.write('not a certificate\n')
            os.environ['NODE_EXTRA_CA_CERTS'] = value
            ctx = trust.context()
            self.assertNotIn('Made-up Root A', subjects(ctx))
            self.assertGreater(len(ctx.get_ca_certs()), 10)         # a bad file takes nothing away
            self.assertEqual(trust.FACTS['node_extra'], said)

    def test_the_roots_this_mac_trusts_and_never_one_it_does_not(self):
        ctx = trust.context()
        self.assertIn('Made-up Root A', subjects(ctx))
        self.assertNotIn('Made-up Root B', subjects(ctx))           # in a keychain, not trusted
        self.assertNotIn('Made-up Leaf C', subjects(ctx))           # not a root: never even asked about
        self.assertEqual(trust.FACTS['mac'], 1)
        self.assertEqual(self.asked()[0], 'find-certificate -a -p %s %s' % (self.login, self.system))
        verified = self.verified()
        self.assertEqual(len(verified), 2)                          # A once (it was listed twice) and B
        for line in verified:
            self.assertTrue(re.match(r'^verify-cert -c \S+ -p ssl -l -L -q$', line), line)
        self.assertGreater(len(ctx.get_ca_certs()), 10)

    def test_no_keychain_no_question(self):
        os.remove(self.login)
        os.remove(self.system)
        self.assertNotIn('Made-up Root A', subjects(trust.context()))
        self.assertEqual(self.asked(), [])

    def test_macos_not_answering_adds_nothing_and_is_not_asked_again_at_once(self):
        with open(self.tool, 'w') as f:
            f.write('#!/bin/sh\nprintf "%%s\\n" "$*" >> "%s"\nexit 1\n' % self.calls)
        self.assertNotIn('Made-up Root A', subjects(trust.context()))
        trust.context()
        self.assertEqual(len(self.asked()), 1)
        self.assertFalse(os.path.exists(os.path.join(self.data, trust.KEPT)))

    def test_the_real_security_is_never_run_in_a_test(self):
        with mock.patch.object(trust, 'TOOL', trust.REAL_TOOL), \
                mock.patch.object(subprocess, 'run', side_effect=AssertionError('ran a program')):
            self.assertEqual(trust.mac_roots(), [])


class Kept(Case):
    def path(self):
        return os.path.join(self.data, trust.KEPT)

    def test_kept_owner_only_with_what_it_depends_on_and_used_after(self):
        trust.context()
        n = len(self.asked())
        self.assertEqual(stat.S_IMODE(os.stat(self.path()).st_mode), 0o600)
        kept = json.loads(text(self.path()))
        self.assertEqual(kept['trusted'], [trust._digest(ssl.PEM_cert_to_DER_cert(text(CERTS['a'])))])   # no PEM
        self.assertEqual(sorted(kept['depends_on']), sorted(trust._places()))
        self.assertIsNotNone(kept['depends_on'][self.login])
        for i in range(5):
            trust._BUILT.clear()
            self.assertIn('Made-up Root A', subjects(trust.context()))
        self.assertEqual(len(self.asked()), n)                      # not asked again

    def test_asked_again_when_a_keychain_changes(self):
        trust.context()
        n = len(self.asked())
        os.remove(os.path.join(self.trusted, 'a'))                  # A no longer trusted, and the keychain changed
        st = os.stat(self.login)
        os.utime(self.login, ns=(st.st_atime_ns, st.st_mtime_ns + 5 * 10 ** 9))
        ctx = trust.context()
        self.assertGreater(len(self.asked()), n)
        self.assertNotIn('Made-up Root A', subjects(ctx))

    def test_asked_again_a_day_later(self):
        trust.context()
        n = len(self.asked())
        kept = json.loads(text(self.path()))
        kept['made_at'] -= trust.KEPT_FOR + 1
        core.write_json(self.path(), kept)
        trust.forget()                                              # a new process, a day later
        trust.context()
        self.assertGreater(len(self.asked()), n)
        self.assertEqual(len([x for x in self.asked()[n:] if x.startswith('verify-cert')]), 2)    # every root again

    def test_a_kept_file_that_was_tampered_with_is_not_used(self):
        core.write_json(self.path(), {'schema': 1, 'made_at': 'soon', 'depends_on': trust._stamp(),
                                      'roots': [text(CERTS['b'])]})
        self.assertNotIn('Made-up Root B', subjects(trust.context()))


class NeverOff(Case):
    def test_every_setting_and_variable_keeps_both_checks(self):
        junk = os.path.join(self.tmp, 'junk.pem')
        with open(junk, 'w') as f:
            f.write('-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n')
        values = (None, CERTS['a'], junk, os.path.join(self.tmp, 'gone.pem'))
        for how in trust.MODES + ('off', 'none'):
            core.set_setting(trust.MODE_KEY, how)
            for cert_file in values:
                for extra in values:
                    for name, value in (('SSL_CERT_FILE', cert_file), ('NODE_EXTRA_CA_CERTS', extra)):
                        if value is None:
                            os.environ.pop(name, None)
                        else:
                            os.environ[name] = value
                    ctx = trust.context()
                    self.assertEqual((ctx.verify_mode, ctx.check_hostname), (ssl.CERT_REQUIRED, True),
                                     (how, cert_file, extra))
                    self.assertIsInstance(ctx, ssl.SSLContext)

    def test_no_check_is_turned_off_anywhere_but_the_look_at_a_refused_certificate(self):
        for rel in ('switchboard/trust.py', 'switchboard/newer.py', 'switchboard/accounts/net.py',
                    'switchboard/accounts/usage.py', 'switchboard/gateway.py'):
            source = text(os.path.join(ROOT, rel))
            if rel == 'switchboard/trust.py':
                look = source[source.index('def _presented_issuer'):]
                look = look[:look.index('\ndef ', 1)]
                source = source.replace(look, '')
                self.assertIn('CERT_NONE', look)
                self.assertNotIn('return look', look)
            for word in ('CERT_NONE', 'CERT_OPTIONAL', 'check_hostname = False', '_create_unverified_context',
                         'verify_mode = ssl.CERT_N'):
                self.assertNotIn(word, source, rel)


class Wired(Case):
    """Each outgoing caller uses the one context: the made-up server's root, named only in NODE_EXTRA_CA_CERTS (or
    trusted only by the stand-in macOS), is enough for each."""

    def answer_version(self, h, record):
        h.send_json(200, None, raw=b"VERSION = '9.0.0'\n")

    def test_net_http_trusts_the_file_and_the_mac(self):
        trust.set_mode('python')
        up = Upstream(self.answer_version, tls=server_tls())
        self.addCleanup(up.stop)
        with self.assertRaises(net.NetError) as e:
            net.Http().request('GET', up.url + '/x', timeout_s=20)
        self.assertEqual(e.exception.kind, 'tls-cert')
        os.environ['NODE_EXTRA_CA_CERTS'] = CERTS['a']
        self.assertEqual(net.Http().request('GET', up.url + '/x', timeout_s=20)[0], 200)
        del os.environ['NODE_EXTRA_CA_CERTS']
        trust.set_mode('mac')
        self.assertEqual(net.Http().request('GET', up.url + '/x', timeout_s=20)[0], 200)

    def test_the_check_for_a_newer_version_on_the_wire_carries_three_headers(self):
        os.environ['NODE_EXTRA_CA_CERTS'] = CERTS['a']
        up = Upstream(self.answer_version, tls=server_tls())
        self.addCleanup(up.stop)
        os.makedirs(os.path.join(self.data, 'app'))
        with open(os.path.join(self.data, 'app', 'source'), 'w') as f:
            f.write(up.url + '/sb/main/install.sh\n')
        found = newer.look()
        self.assertEqual((found['problem'], found['newest_version'], found['newer']), (None, '9.0.0', True))
        self.assertEqual([(r['method'], r['path']) for r in up.requests], [('GET', '/sb/main/switchboard/core.py')])
        self.assertEqual(sorted(up.requests[0]['headers']),
                         sorted([('Host', up.url.split('//')[1]), ('Accept-Encoding', 'identity'),
                                 ('User-Agent', 'Switchboard/%s' % core.VERSION)]))

    def test_usage_reads_use_it(self):
        trust.set_mode('python')
        up = Upstream(self.answer_version, tls=server_tls())
        self.addCleanup(up.stop)
        env = mock.patch.dict(os.environ, {'no_proxy': '*'})
        env.start()
        self.addCleanup(env.stop)
        with self.assertRaises(usage.NetError) as e:
            usage.http_get(up.url + '/x', {}, 20)
        self.assertEqual(e.exception.kind, 'tls-cert')
        os.environ['NODE_EXTRA_CA_CERTS'] = CERTS['a']
        self.assertEqual(usage.http_get(up.url + '/x', {}, 20).status, 200)

    def test_the_gateway_upstream_uses_it(self):
        trust.set_mode('python')
        os.environ['NODE_EXTRA_CA_CERTS'] = CERTS['a']
        made = []
        real = trust.context

        def counted():
            made.append(1)
            return real()
        with mock.patch.object(trust, 'context', counted):
            gw = gateway.Gateway(lambda: [], 'made-up-secret-for-tests-0000000000', lambda name: None, port=0)
            up = Upstream(self.answer_version, tls=server_tls())
            self.addCleanup(up.stop)
            conn = gw._open({'name': 'a', 'kind': 'anthropic', 'base_url': up.url, 'auth': 'x-api-key'}, 'GET',
                            '/v1/models', b'', [], 'made-up-key', 10)
            conn.resp.read()
            conn.conn.close()
        self.assertEqual(made, [1])


if __name__ == '__main__':
    unittest.main()
