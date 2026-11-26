"""Whose login is this: evidence about
the credential first (fingerprint, refresh-expiry stamp), Anthropic's answer next, the label last; label and login
disagreeing is a state, never resolved by guessing."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import hashlib
import json
import unittest

from fakes_accounts import Rig, identity_block, login_text
from switchboard.accounts import identity, live, vault


def doc_with(*records):
    doc = vault.blank()
    for r in records:
        doc['accounts'][r['id']] = dict(r)
        doc['order'].append(r['id'])
    return doc


class Fingerprints(unittest.TestCase):
    def test_a_fingerprint_is_the_first_16_hex_of_the_refresh_tokens_sha256_and_never_the_token(self):
        text = login_text('sk-ant-oat01-a', 'sk-ant-ort01-secret', 1)
        got = live.parse(text)
        self.assertEqual(got.fingerprint, hashlib.sha256(b'sk-ant-ort01-secret').hexdigest()[:16])
        self.assertNotIn('secret', got.fingerprint)
        self.assertEqual(got.access_fp, hashlib.sha256(b'sk-ant-oat01-a').hexdigest()[:16])

    def test_every_kind_of_store_content_is_told_apart(self):
        kinds = {
            login_text('a', 'r', 1): 'oauth',
            json.dumps({'claudeAiOauth': {'accessToken': '', 'refreshToken': '', 'expiresAt': 0}}): 'wiped',
            json.dumps({'mcpOAuth': {}}): 'no_login',
            'sk-ant-api03-abc': 'api_key',
            'sk-ant-oat01-abc': 'unparseable',
            '': 'empty',
            '{"torn": ': 'unparseable',
            '[1]': 'unparseable',
            '{"claudeAiOauth": {"expiresAt": NaN}}': 'unparseable',
        }
        for text, kind in kinds.items():
            self.assertEqual(live.parse(text).kind, kind, text)
        self.assertFalse(live.parse(json.dumps({'claudeAiOauth': {'accessToken': 'a'}})).full_pair)


class OwnerProof(unittest.TestCase):
    def setUp(self):
        self.a = live.parse(login_text('acc-a', 'ref-a', 10, rx_ms=5000))
        self.doc = doc_with({'id': 'uuid-a', 'uuid': 'uuid-a', 'email': 'a@x.example', 'org_id': 'org-a',
                             'fp': self.a.fingerprint, 'rx_ms': 5000},
                            {'id': 'uuid-b', 'uuid': 'uuid-b', 'email': 'b@x.example', 'org_id': 'org-b',
                             'fp': 'f' * 16, 'rx_ms': 6000})

    def test_the_owner_is_found_by_fingerprint_then_by_the_refresh_expiry_stamp(self):
        self.assertEqual(identity.offline(self.a, self.doc), identity.Proof('uuid-a', 'fingerprint'))
        rotated = live.parse(login_text('acc-a2', 'ref-a2', 20, rx_ms=5000))     # one sign-in, renewed
        self.assertEqual(identity.offline(rotated, self.doc), identity.Proof('uuid-a', 'refresh_expiry'))
        stranger = live.parse(login_text('x', 'y', 30, rx_ms=7000))
        self.assertEqual(identity.offline(stranger, self.doc).kind, 'none')
        self.doc['accounts']['uuid-b']['rx_ms'] = 5000                           # a stamp two accounts share: no proof
        self.assertEqual(identity.offline(rotated, self.doc).kind, 'none')

    def test_the_label_never_beats_the_credential_and_disagreement_is_reported(self):
        p = identity.prove(None, self.a, self.doc, identity_block('uuid-b', 'b@x.example', 'org-b'), network=False)
        self.assertEqual((p.account_id, p.kind, p.disagree, p.label_id), ('uuid-a', 'fingerprint', True, 'uuid-b'))
        p = identity.prove(None, self.a, self.doc, identity_block('uuid-a', 'a@x.example', 'org-a'), network=False)
        self.assertFalse(p.disagree)

    def test_a_matching_account_id_never_excuses_a_different_organization(self):
        rec = self.doc['accounts']['uuid-a']
        self.assertTrue(identity.agrees(rec, 'uuid-a', 'org-a'))
        self.assertTrue(identity.agrees(rec, 'uuid-a', ''))                      # one side says nothing: fine
        self.assertFalse(identity.agrees(rec, 'uuid-a', 'org-other'))
        self.assertFalse(identity.agrees(rec, 'uuid-b', 'org-a'))

    def test_one_email_in_two_organizations_gets_two_ids(self):
        self.assertEqual(vault.account_key(self.doc, 'uuid-a', 'org-a'), 'uuid-a')
        self.assertEqual(vault.account_key(self.doc, 'uuid-a', 'org-team'), 'uuid-a/org-team')
        self.assertEqual(vault.account_key(self.doc, 'uuid-new', 'org-n'), 'uuid-new')

    def test_bytes_that_moved_under_the_lock_keep_their_owner_only_when_the_sign_in_is_the_same(self):
        before = identity.Proof(None, 'profile', new_uuid='uuid-c', new_org='org-c', new_email='c@x.example')
        pre = live.parse(login_text('c1', 'rc1', 10, rx_ms=9000))
        same = live.parse(login_text('c2', 'rc2', 20, rx_ms=9000))
        other = live.parse(login_text('d', 'rd', 20, rx_ms=9100))
        self.assertEqual(identity.reprove(same, self.doc, None, before, pre).new_uuid, 'uuid-c')
        self.assertEqual(identity.reprove(other, self.doc, None, before, pre).kind, 'none')


class WhichIsNewer(unittest.TestCase):
    def test_newer_means_a_later_sign_in_then_a_later_access_expiry_never_a_guess(self):
        old_login = live.parse(login_text('a1', 'r1', 1000, rx_ms=50))
        rotated = live.parse(login_text('a2', 'r2', 2000, rx_ms=50))
        relogin = live.parse(login_text('a3', 'r3', 500, rx_ms=120090))   # stamps over 120 s apart: two sign-ins
        self.assertEqual(live.compare(rotated, old_login), 'newer')
        self.assertEqual(live.compare(old_login, rotated), 'older')
        self.assertEqual(live.compare(relogin, rotated), 'newer')          # a later sign-in, whatever its expiry
        self.assertEqual(live.compare(old_login, old_login), 'same')
        no_times = live.parse(json.dumps({'claudeAiOauth': {'accessToken': 'x', 'refreshToken': 'y'}}))
        self.assertIsNone(live.compare(no_times, live.parse(json.dumps({'claudeAiOauth': {
            'accessToken': 'p', 'refreshToken': 'q'}}))))


class AnthropicsAnswer(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        identity._PROFILE_CACHE.clear()

    def tearDown(self):
        self.rig.close()

    def test_a_401_means_expired_not_another_owner_and_any_failure_is_unresolved(self):
        text = self.rig.server.add('uuid-a', 'a@x.example', 'org-a')
        tok = json.loads(text)['claudeAiOauth']['accessToken']
        ans = identity.lookup_profile(self.rig.ctx, tok)
        self.assertEqual((ans.status, ans.uuid, ans.org), ('resolved', 'uuid-a', 'org-a'))
        self.assertEqual(identity.lookup_profile(self.rig.ctx, 'sk-ant-oat01-unknown').status, 'expired')
        self.rig.server.script.append(('profile', (500, {})))
        self.assertEqual(identity.lookup_profile(self.rig.ctx, 'sk-ant-oat01-other').status, 'unresolved')
        self.rig.server.script.append(('profile', (200, {'account': {}})))
        self.assertEqual(identity.lookup_profile(self.rig.ctx, 'sk-ant-oat01-third').why, 'bad-response')

    def test_a_resolved_answer_that_disagrees_with_the_label_is_reported(self):
        doc = vault.blank()
        ans = identity.ProfileAnswer('resolved', uuid='uuid-c', email='c@x.example', org='org-c')
        p = identity.from_profile(doc, ans, identity_block('uuid-a', 'a@x.example', 'org-a'))
        self.assertTrue(p.disagree)
        self.assertEqual((p.account_id, p.new_uuid), (None, 'uuid-c'))


if __name__ == '__main__':
    unittest.main()
