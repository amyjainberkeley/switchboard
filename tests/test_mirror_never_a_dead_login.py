import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The standing copy never holds a login that can't work: a blanked one (Claude Code empties both tokens after Anthropic
# refused a renewal), one without an access or a refresh token, or one whose refresh token the saved accounts have
# struck (Anthropic refused it). Such a login in the item makes no copy, and a copy of ours beside it goes at the look
# that finds it (the item, which wins over the file, holds nothing that works either).
# The account engine on its stand-ins (fakes_accounts) with a fake clock; made-up *.example accounts.
import json
import unittest

from hold_fakes import Base
from switchboard.accounts import live, vault


class Mirror(Base):
    MIRROR = True

    def with_login(self, **change):
        blob = json.loads(self.rig.live_text())
        blob['claudeAiOauth'].update(change)
        text = json.dumps(blob, separators=(',', ':'))
        self.rig.set_live(text)
        return text

    def strike(self, text):
        fp = live.parse(text).fingerprint
        with vault.changing(self.ctx) as doc:
            aid = [k for k in doc['accounts'] if k.startswith('uuid-b')][0]
            doc['accounts'][aid]['strike'] = {'fp': fp, 'at': self.clock.wall()}


class NoCopyOf(Mirror):
    def test_a_blanked_login(self):
        self.with_login(accessToken='', refreshToken='', expiresAt=0)
        self.look(keychain=True)
        self.assertIsNone(self.file_text())

    def test_a_login_without_an_access_token(self):
        self.with_login(accessToken='')
        self.look(keychain=True)
        self.assertIsNone(self.file_text())

    def test_a_login_without_a_refresh_token(self):
        self.with_login(refreshToken='')
        self.look(keychain=True)
        self.assertIsNone(self.file_text())

    def test_a_struck_login(self):
        self.strike(self.rig.live_text())
        self.look(keychain=True)
        self.assertIsNone(self.file_text())


class ACopyBeside(Mirror):
    def setUp(self):
        super().setUp()
        self.look(keychain=True)
        self.assertTrue(self.ours())

    def test_a_blanked_login_goes(self):
        self.with_login(accessToken='', refreshToken='', expiresAt=0)
        self.look(after=1.0, keychain=True)
        self.assertIsNone(self.file_text())
        self.assertIsNone(self.note())

    def test_a_struck_renewal_is_never_copied_and_the_copy_goes(self):
        self.clock.advance(600.0)
        renewed = self.rig.claude_renews()
        self.strike(renewed)
        self.look(after=1.0, keychain=True)
        self.assertIsNone(self.file_text())

    def test_a_struck_login_goes(self):
        self.strike(self.rig.live_text())
        self.look(after=1.0, keychain=True)
        self.assertIsNone(self.file_text())


if __name__ == '__main__':
    unittest.main()
