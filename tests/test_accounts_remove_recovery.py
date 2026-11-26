"""Removing an account removes its recovery copies too: the safety copies taken of it as the login in use and its
set-aside successor, in the Keychain or in a file, with their manifest lines. Nothing of another account is touched,
and neither is a copy a cut-off switch may still need."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import os
import unittest

from fakes_accounts import Rig
from switchboard.accounts import live, roster, switcher, tokens, vault


class RemovingAnAccountTakesItsRecoveryCopies(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.addCleanup(self.rig.close)
        self.a, self.b = self.rig.two_accounts()            # Claude Code is on B
        self.ctx = self.rig.ctx

    def keys(self):
        return sorted(e['key'] for e in vault.stashed(self.ctx))

    def recovery_items(self):
        return sorted(acct for (svc, acct) in self.rig.kc.items() if svc == vault.SERVICE_RECOVERY)

    def files(self):
        folder = os.path.join(self.rig.data, 'recovery')
        return sorted(n for n in os.listdir(folder) if n.endswith('.json') and n != 'manifest.json')

    def stash_in_a_file(self, key, text, aid, reason):
        self.rig.kc.rule('write', service=vault.SERVICE_RECOVERY, account=key, why='locked', times=1)
        ok, where = vault.stash(self.ctx, key, text, aid, reason)
        self.assertEqual((ok, where), (True, 'file'))

    def test_its_safety_copies_and_successor_go_and_the_others_stay(self):
        fa, fb = live.parse(self.a).fingerprint, live.parse(self.b).fingerprint
        vault.stash(self.ctx, 'safety:' + fa, self.a, 'uuid-a', 'before switching')
        self.stash_in_a_file('safety:old-a', self.a, 'uuid-a', 'before switching')
        self.stash_in_a_file('successor:uuid-a', self.a, 'uuid-a', 'save failed')
        vault.stash(self.ctx, 'safety:' + fb, self.b, 'uuid-b', 'before switching')
        vault.stash(self.ctx, 'successor:uuid-b', self.b, 'uuid-b', 'save failed')
        roster.remove(self.ctx, 'uuid-a')
        self.assertEqual(self.keys(), sorted(['safety:' + fb, 'successor:uuid-b']))
        self.assertEqual(self.recovery_items(), sorted(['safety:' + fb, 'successor:uuid-b']))
        self.assertEqual(self.files(), [])
        self.assertFalse(any(self.a in v for v in self.rig.kc.items().values()))
        self.assertIsNotNone(self.rig.kc.get(vault.SERVICE_CURRENT, 'uuid-b'))

    def test_a_successor_kept_only_in_the_keychain_and_not_listed_goes_too(self):
        self.rig.kc.put(vault.SERVICE_RECOVERY, 'successor:uuid-a', self.a)
        roster.remove(self.ctx, 'uuid-a')
        self.assertEqual(self.recovery_items(), [])

    def test_a_login_set_aside_for_another_kept_account_stays(self):
        b_fp = vault.load(self.ctx)['accounts']['uuid-b']['fp']
        vault.stash(self.ctx, 'successor:uuid-a', self.b, 'uuid-a', tokens.CONFLICT, supersedes=b_fp, names='uuid-b')
        roster.remove(self.ctx, 'uuid-a')
        self.assertEqual(self.keys(), ['successor:uuid-a'])
        self.assertEqual(self.rig.kc.get(vault.SERVICE_RECOVERY, 'successor:uuid-a'), self.b)

    def test_the_safety_copy_of_a_cut_off_switch_stays(self):
        fa = live.parse(self.a).fingerprint
        vault.stash(self.ctx, 'safety:' + fa, self.a, 'uuid-a', 'before switching')
        switcher._write_journal(self.ctx, {'from_id': 'uuid-a', 'to_id': 'uuid-b', 'safety_key': 'safety:' + fa,
                                           'step': 'safety'})
        roster.remove(self.ctx, 'uuid-a')
        self.assertEqual(self.keys(), ['safety:' + fa])
        self.assertEqual(self.rig.kc.get(vault.SERVICE_RECOVERY, 'safety:' + fa), self.a)


if __name__ == '__main__':
    unittest.main()
