"""A saved login keeps only what belongs to its account: the Mac-wide parts of Claude Code's item
(facts.SHARED_SIBLINGS: MCP and plugin sign-ins, the design grant) are never written back from a saved copy, so they
are not kept in it.

A saved copy written by an older build (with those parts) still reads and switches exactly as before, and is written
in the new form only when it would be saved anyway. Safety copies still hold the live item whole."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import time
import unittest
from unittest import mock

from fakes_accounts import Rig
from switchboard.accounts import capture, context, facts, live, switcher, tokens, vault

MAC = {'mcpOAuth': {'srv': 'sk-mcp-secret'}, 'pluginSecrets': {'p': 'sk-plugin'}, 'designOauth': {'grant': 'g'}}


def has_mac_parts(text):
    return any(k in json.loads(text) for k in facts.SHARED_SIBLINGS)


class SavedLoginsAreAccountBound(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()

    def tearDown(self):
        self.use.__exit__(None, None, None)
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        self.rig.close()

    def writes(self, aid):
        return sum(1 for c in self.rig.kc.calls if c == ('write', vault.SERVICE_CURRENT, aid))

    def test_keeping_a_login_keeps_its_account_parts_only(self):
        text = self.rig.keep('uuid-a', 'a@example.com', 'org-a', siblings=MAC)
        saved = self.rig.vault('uuid-a')
        self.assertFalse(has_mac_parts(saved))
        self.assertEqual(json.loads(saved)['claudeAiOauth'], json.loads(text)['claudeAiOauth'])
        self.assertTrue(has_mac_parts(self.rig.live_text()))           # Claude Code's own item is left whole

    def test_a_login_without_mac_parts_is_kept_byte_for_byte(self):
        text = self.rig.keep('uuid-a', 'a@example.com', 'org-a')
        self.assertEqual(self.rig.vault('uuid-a'), text)

    def test_the_keep_pass_does_not_save_again_on_every_pass(self):
        self.rig.keep('uuid-a', 'a@example.com', 'org-a', siblings=MAC)
        before = self.writes('uuid-a')
        for _ in range(3):
            capture.keep_pass(self.ctx, force=True)
        self.assertEqual(self.writes('uuid-a'), before)

    def test_an_older_saved_copy_with_mac_parts_reads_and_switches_as_before_and_is_not_rewritten(self):
        a, b = self.rig.two_accounts()
        old = json.loads(self.rig.vault('uuid-a'))
        old.update({'mcpOAuth': {'srv': 'stale'}, 'mcpXaaIdp': {'idp': 'old'}})
        old_text = json.dumps(old, separators=(',', ':'))
        self.rig.sec.put(vault.SERVICE_CURRENT, 'uuid-a', old_text)
        live_blob = json.loads(self.rig.live_text())
        live_blob.update(MAC)
        self.rig.set_live(live.dump(live_blob))
        expect = live.dump(live.compose(old, live_blob))
        for _ in range(2):
            capture.keep_pass(self.ctx, force=True)
        self.assertEqual(self.rig.vault('uuid-a'), old_text)            # nothing to save: left as it was
        switcher.switch(self.ctx, 'uuid-a')
        self.assertEqual(self.rig.live_text(), expect)                  # what an older build wrote too
        self.assertFalse(has_mac_parts(self.rig.vault('uuid-b')))       # the outgoing login, saved now

    def test_a_renewal_saves_the_new_form(self):
        self.rig.keep('uuid-a', 'a@example.com', 'org-a')
        self.rig.keep('uuid-b', 'b@example.com', 'org-b')
        old = json.loads(self.rig.vault('uuid-a'))
        old.update(MAC)
        self.rig.sec.put(vault.SERVICE_CURRENT, 'uuid-a', json.dumps(old, separators=(',', ':')))
        self.rig.set_saved_expiry('uuid-a', time.time() - 60)
        res = tokens.refresh(self.ctx, 'uuid-a', budget_s=45.0)
        self.assertEqual(res.outcome, 'refreshed')
        saved = self.rig.vault('uuid-a')
        self.assertFalse(has_mac_parts(saved))
        self.assertEqual(live.parse(saved).fingerprint, res.new_fp)

    def test_a_safety_copy_still_holds_the_live_item_whole(self):
        self.rig.two_accounts()
        live_blob = json.loads(self.rig.live_text())
        live_blob.update(MAC)
        whole = live.dump(live_blob)
        self.rig.set_live(whole)
        seen = []
        real = vault.stash

        def spy(ctx, key, text, *a, **kw):
            seen.append((key, text))
            return real(ctx, key, text, *a, **kw)
        with mock.patch.object(vault, 'stash', spy):
            switcher.switch(self.ctx, 'uuid-a')
        self.assertIn(whole, [t for k, t in seen if k.startswith('safety:')])


if __name__ == '__main__':
    unittest.main()
