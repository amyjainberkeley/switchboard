import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# A root is trusted because macOS says so now, never because a file in the data folder says so. mac-trust.json only
# names which certificates to ask macOS about; each process asks macOS (the stand-in security at trust.TOOL) before
# its first secure connection and keeps the answer in memory. Anything planted in the file adds no root.
import hashlib
import json
import os
import ssl
import time
import unittest

import test_trust as T
from switchboard import core, trust


def setUpModule():
    T.setUpModule()


def tearDownModule():
    T.tearDownModule()


def digest(path):
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(T.text(path))).hexdigest()


class Case(T.Case):
    def setUp(self):
        super().setUp()
        trust.forget()
        self.addCleanup(trust.forget)

    def path(self):
        return os.path.join(self.data, trust.KEPT)

    def new_process(self):
        trust.forget()
        trust._BUILT.clear()


class APlantedRootIsNotTrusted(Case):
    def test_a_root_written_into_the_file_the_old_way(self):
        core.write_json(self.path(), {'schema': 1, 'made_at': time.time(), 'depends_on': trust._stamp(),
                                      'roots': [T.text(T.CERTS['b'])]})
        self.assertNotIn(T.canonical(T.CERTS['b']), trust.mac_roots())
        self.assertNotIn('Made-up Root B', T.subjects(trust.context()))

    def test_a_root_named_in_the_file_that_macos_does_not_trust(self):
        core.write_json(self.path(), {'schema': 2, 'made_at': time.time(), 'depends_on': trust._stamp(),
                                      'trusted': [digest(T.CERTS['b'])]})
        self.assertNotIn('Made-up Root B', T.subjects(trust.context()))
        self.assertEqual(len(self.verified()), 1)                  # macOS was asked about B, and said no

    def test_a_root_named_in_the_file_that_is_in_no_keychain(self):
        made = T.make_root(self.tmp, 'planted', 'Made-up Planted Root')
        core.write_json(self.path(), {'schema': 2, 'made_at': time.time(), 'depends_on': trust._stamp(),
                                      'trusted': [digest(made)], 'roots': [T.text(made)]})
        self.assertNotIn('Made-up Planted Root', T.subjects(trust.context()))


class MacOSIsAskedInEachProcess(Case):
    def test_the_file_holds_no_certificate_only_what_to_ask_about(self):
        trust.context()
        kept = json.loads(T.text(self.path()))
        self.assertEqual(kept['trusted'], [digest(T.CERTS['a'])])
        self.assertNotIn('BEGIN CERTIFICATE', T.text(self.path()))

    def test_a_new_process_asks_again_about_the_roots_listed_and_no_others(self):
        trust.context()
        n = len(self.asked())
        for i in range(3):                                          # the same process: never asked again
            trust._BUILT.clear()
            self.assertIn('Made-up Root A', T.subjects(trust.context()))
        self.assertEqual(len(self.asked()), n)
        self.new_process()
        self.assertIn('Made-up Root A', T.subjects(trust.context()))
        again = self.asked()[n:]
        self.assertEqual(again[0], 'find-certificate -a -p %s %s' % (self.login, self.system))
        self.assertEqual(len([x for x in again if x.startswith('verify-cert')]), 1)   # A only: B is not listed

    def test_a_root_macos_stops_trusting_is_dropped_by_the_next_process(self):
        trust.context()
        os.remove(os.path.join(self.trusted, 'a'))                  # the trust setting changed, the files did not
        self.new_process()
        self.assertNotIn('Made-up Root A', T.subjects(trust.context()))


if __name__ == '__main__':
    unittest.main()
