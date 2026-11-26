"""The uninstaller's "remove everything" also finds Switchboard's Keychain items that no record names any more (an
account list that was lost, a recovery copy whose manifest line was never written): after the pass over the records,
every item left under "Switchboard login", "Switchboard login (previous)" and "Switchboard recovery" goes. Claude
Code's own item and its per-folder items are never looked at."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import dataclasses
import os
import sys
import tempfile
import shutil
import time
import unittest

from fakes_accounts import FakeKeychain, Rig
from switchboard.accounts import api, context, keychain, tokens, uninstall, vault

SERVICES = (vault.SERVICE_CURRENT, vault.SERVICE_PREVIOUS, vault.SERVICE_RECOVERY)


class SweepableKeychain(FakeKeychain):
    """FakeKeychain plus keychain.Keychain.first_account: one account name under a service, found by the service."""

    def first_account(self, service):
        self._match('first', service, '')
        for (svc, acct) in sorted(self.store):
            if svc == service:
                return api.Read(api.PRESENT, value=acct)
        return api.Read(api.ABSENT)


def usual_rig(usual=True):
    """A Rig whose data folder is the usual ~/.switchboard of its made-up home (or another folder)."""
    rig = Rig()
    kc = SweepableKeychain()
    rig.sec = rig.kc = kc
    if usual:
        rig.data = os.path.join(rig.home, '.switchboard')
    rig.ctx = dataclasses.replace(rig.ctx, data_dir=rig.data, keychain=kc)
    return rig


class TheSweep(unittest.TestCase):
    def make(self, usual=True):
        self.rig = usual_rig(usual)
        self.addCleanup(self.rig.close)
        self.ctx = self.rig.ctx
        use = context.use(self.ctx)
        use.__enter__()
        self.addCleanup(use.__exit__, None, None, None)
        self.rig.two_accounts()                               # Claude Code is on B
        self.live = self.rig.live_text()
        self.rig.kc.put('Claude Code-credentials-0a1b2c3d', self.rig.account, 'a run or sign-in folder item')
        for svc, acct in ((vault.SERVICE_CURRENT, 'uuid-lost'), (vault.SERVICE_PREVIOUS, 'uuid-lost'),
                          (vault.SERVICE_RECOVERY, 'safety:unlisted'), (vault.SERVICE_RECOVERY, 'successor:gone')):
            self.rig.kc.put(svc, acct, '{"claudeAiOauth": {}}')     # no record names any of these

    def tearDown(self):
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)

    def ours(self):
        return sorted(k for k in self.rig.kc.items() if k[0] in SERVICES)

    def test_items_no_record_names_are_removed_and_claude_codes_stay(self):
        self.make()
        said = uninstall.remove_all(self.ctx)
        self.assertEqual(self.ours(), [])
        self.assertEqual(self.rig.live_text(), self.live)
        self.assertEqual(self.rig.kc.get('Claude Code-credentials-0a1b2c3d', self.rig.account),
                         'a run or sign-in folder item')
        self.assertTrue(any('Earlier logins and recovery copies' in s for s in said), said)
        looked = set(c[1] for c in self.rig.kc.calls if c[0] == 'first')
        self.assertEqual(looked, set(SERVICES))                 # only Switchboard's own services were searched

    def test_a_data_folder_elsewhere_sweeps_nothing(self):
        self.make(usual=False)
        uninstall.remove_all(self.ctx)
        self.assertEqual(len(self.ours()), 4)                   # another install's items may be these
        self.assertEqual([c for c in self.rig.kc.calls if c[0] == 'first'], [])

    def test_a_record_that_could_not_be_removed_stops_the_sweep(self):
        self.make()
        self.rig.kc.rule('delete', service=vault.SERVICE_CURRENT, account='uuid-a', why='locked', times=5)
        with self.assertRaises(api.AccountsError):
            uninstall.remove_all(self.ctx)
        self.assertIn((vault.SERVICE_CURRENT, 'uuid-lost'), self.ours())
        self.assertEqual([c for c in self.rig.kc.calls if c[0] == 'first'], [])

    def test_an_item_that_wont_go_is_said(self):
        self.make()
        self.rig.kc.rule('delete', service=vault.SERVICE_CURRENT, account='uuid-lost', why='denied', times=5)
        with self.assertRaises(api.AccountsError) as e:
            uninstall.remove_all(self.ctx)
        self.assertIn("uuid-lost's saved login", str(e.exception))
        self.assertIn((vault.SERVICE_CURRENT, 'uuid-lost'), self.ours())


LISTING = (b'keychain: "/Users/tester/Library/Keychains/login.keychain-db"\nversion: 512\nclass: "genp"\n'
           b'attributes:\n    0x00000007 <blob>="Switchboard recovery"\n    0x00000008 <blob>=<NULL>\n'
           b'    "acct"<blob>=%s\n    "cdat"<timedate>=0x32303236313131393030303030305A00  "20261119000000Z\\000"\n'
           b'    "svce"<blob>="Switchboard recovery"\n')


class TheAccountNameInAListing(unittest.TestCase):
    def test_plain_hex_and_missing(self):
        self.assertEqual(keychain.item_account(LISTING % b'"safety:ab12"'), 'safety:ab12')
        self.assertEqual(keychain.item_account(LISTING % b'0x737563636573736F723A78C3A9  "successor:x\\303\\251"'),
                         'successor:xé')
        self.assertIsNone(keychain.item_account(LISTING % b'<NULL>'))
        self.assertIsNone(keychain.item_account(LISTING % b'""'))
        self.assertIsNone(keychain.item_account(b''))


_TOOL = r'''#!%(python)s
import sys
args = sys.argv[1:]
if args[:1] == ['list-keychains']:
    print('    "/Users/tester/Library/Keychains/login.keychain-db"'); sys.exit(0)
if args == ['find-generic-password', '-s', 'Switchboard recovery']:
    sys.stdout.buffer.write(%(listing)r); sys.exit(0)
if args == ['find-generic-password', '-s', 'Switchboard login']:
    sys.exit(44)
sys.exit(1)
'''


class TheRealWrapper(unittest.TestCase):
    def test_first_account_asks_by_service_alone_and_reads_no_secret(self):
        root = tempfile.mkdtemp(prefix='sweep-tool-')
        self.addCleanup(shutil.rmtree, root, True)
        tool = os.path.join(root, 'fake-sec')
        with open(tool, 'w') as f:
            f.write(_TOOL % {'python': sys.executable, 'listing': LISTING % b'"successor:uuid-x"'})
        os.chmod(tool, 0o700)
        kc = keychain.Keychain(tool=tool, read_deadline=60.0, write_deadline=60.0)
        got = kc.first_account(vault.SERVICE_RECOVERY)
        self.assertEqual((got.kind, got.value), (api.PRESENT, 'successor:uuid-x'))
        self.assertEqual(kc.first_account(vault.SERVICE_CURRENT).kind, api.ABSENT)
        self.assertEqual(kc.first_account(vault.SERVICE_PREVIOUS).kind, api.UNREADABLE)


if __name__ == '__main__':
    unittest.main()
