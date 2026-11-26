import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The rules of newer.py and trust.py over many made-up cases from a fixed seed: versions compare as numbers; a look by
# itself never happens twice in a day and never while it is off; only roots macOS called trusted are ever added, each
# once; and with nothing set, the context trusts exactly what it trusted before.
import os
import random
import shutil
import ssl
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)

from switchboard import core, newer, trust
import test_trust
from test_trust import CERTS

SEED = 20261125


def setUpModule():
    test_trust.setUpModule()


def tearDownModule():
    test_trust.tearDownModule()


class Versions(unittest.TestCase):
    def test_compare_is_the_order_of_the_numbers(self):
        rnd = random.Random(SEED)
        for _ in range(5000):
            a = [rnd.randint(0, 12) for _ in range(rnd.randint(1, 4))]
            b = [rnd.randint(0, 12) for _ in range(rnd.randint(1, 4))]
            got = newer.compare('.'.join(map(str, a)), '.'.join(map(str, b)))
            pa, pb = a + [0] * (4 - len(a)), b + [0] * (4 - len(b))
            self.assertEqual(got, (pa > pb) - (pa < pb), (a, b))
            self.assertEqual(newer.compare('.'.join(map(str, b)), '.'.join(map(str, a))), -got)


class LooksByThemselves(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-rules-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = mock.patch.dict(os.environ, {'SWITCHBOARD_HOME': os.path.join(self.tmp, 'data')})
        env.start()
        self.addCleanup(env.stop)
        self.looks = []
        p = mock.patch.object(newer, 'look', lambda find_brew=None: self.looks.append(1) or {
            'newest_version': '9.0.0', 'problem': None})
        p.start()
        self.addCleanup(p.stop)

    def run_ticks(self, times):
        at = []
        for t in times:
            th = newer.tick(now=t)
            if th is not None:
                th.join(10)
                at.append(t)
        return at

    def test_never_twice_in_a_day_while_on(self):
        rnd = random.Random(SEED)
        newer.set_check('daily')
        for case in range(300):
            try:
                os.remove(core.data_path(newer.KEPT))
            except FileNotFoundError:
                pass
            t, times = 1.79e9, []
            for _ in range(rnd.randint(1, 40)):
                t += rnd.choice((1, 59, 600, 3600, 6 * 3600, newer.DAY - 1, newer.DAY, 3 * newer.DAY))
                times.append(t)
            at = self.run_ticks(times)
            self.assertEqual(at[:1], times[:1], case)                    # the first pass looks at once
            for x, y in zip(at, at[1:]):
                self.assertGreaterEqual(y - x, newer.DAY, (case, at))
            for t in times:                                              # and no day goes by without one
                self.assertTrue(any(0 <= t - x < newer.DAY for x in at), (case, t, at))

    def test_never_while_off_or_unset(self):
        rnd = random.Random(SEED)
        for value in (None, 'off', 'weekly', True, 1, ''):
            if value is None:
                core.update_setting(newer.CHECK_KEY, lambda v: core.UNSET)
            else:
                core.set_setting(newer.CHECK_KEY, value)
            self.assertEqual(self.run_ticks([1.79e9 + rnd.randint(0, 10 ** 7) for _ in range(200)]), [])
            self.assertIsNone(newer.line())
        self.assertEqual(self.looks, [])


class OnlyTrustedRoots(test_trust.Case):
    """ask_mac over random keychains: the stand-in macOS's answers are drawn at random, through trust._run."""

    def test_only_what_macos_called_trusted_and_only_roots_each_once(self):
        rnd = random.Random(SEED)
        made = {k: test_trust.canonical(CERTS[k] if k in ('a', 'b', 'c') else CERTS[k][0])
                for k in ('a', 'b', 'c', 'server', 'elsewhere')}
        roots = {'a', 'b'}                                               # the two that signed themselves
        junk = '-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n'
        for case in range(1500):
            listed = [rnd.choice(sorted(made) + ['junk']) for _ in range(rnd.randint(0, 8))]
            trusted = {k for k in made if rnd.random() < 0.5}
            asked = []

            def run(args, listed=listed, trusted=trusted, asked=asked):
                if args[0] == 'find-certificate':
                    out = ''.join(junk if k == 'junk' else made[k] for k in listed)
                    return subprocess.CompletedProcess(args, 0, out.encode(), b'')
                with open(args[args.index('-c') + 1]) as f:
                    pem = f.read()
                asked.append(pem)
                return subprocess.CompletedProcess(args, 0 if any(made[k] == pem for k in trusted) else 1, b'', b'')
            with mock.patch.object(trust, '_run', run):
                got = trust.ask_mac()
            want = [made[k] for k in dict.fromkeys(listed) if k in roots and k in trusted]
            self.assertEqual(got, want, (case, listed, sorted(trusted)))
            self.assertTrue(all(any(made[k] == pem for k in roots) for pem in asked), case)   # roots alone asked
            self.assertEqual(len(asked), len(set(asked)), case)


class AsBeforeWithNothingSet(test_trust.Case):
    def before(self):
        """The context as it was built before trust.py (net.tls_context's own rule)."""
        ctx = ssl.create_default_context()
        try:
            empty = not ctx.cert_store_stats().get('x509_ca') and not ssl.get_default_verify_paths().capath
        except (AttributeError, ssl.SSLError):
            empty = False
        if empty:
            for path in ('/etc/ssl/cert.pem', '/private/etc/ssl/cert.pem'):
                if os.path.exists(path):
                    try:
                        ctx.load_verify_locations(cafile=path)
                        break
                    except (OSError, ssl.SSLError):
                        pass
        return ctx

    def test_the_same_certificates_and_the_same_checks(self):
        os.remove(self.login)                                            # a Mac with no root of its own
        os.remove(self.system)
        old, new = self.before(), trust.context()
        key = lambda c: sorted(repr(sorted(x.items())) for x in c.get_ca_certs())     # noqa: E731
        self.assertEqual(key(new), key(old))
        self.assertEqual((new.verify_mode, new.check_hostname, new.options, new.verify_flags),
                         (old.verify_mode, old.check_hostname, old.options, old.verify_flags))
        self.assertEqual(new.minimum_version, old.minimum_version)


if __name__ == '__main__':
    unittest.main()
