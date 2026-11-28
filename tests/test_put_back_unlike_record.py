import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# A saved login that is not the one its record describes. The two differ only when Switchboard stopped between saving
# a login and noting it, and then the Keychain copy is the newer one: refusing it would leave Claude Code signed out
# until something notes the record again. So a saved login unlike its record is written when it is a full login whose
# access expiry is later than the record's, and the rule decides to write from it as read; else it is left, said once.
# After such a write the keep step notes the record as for any login. The account engine on its stand-ins
# (fakes_accounts) with a fake clock. Made-up *.example accounts.
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from fakes_autoswitch import FakeClock
from fakes_accounts import Rig
from switchboard.accounts import api, autoloop, context, live, putback, vault
from switchboard.accounts import signin_watch as sw

MCP = {'mcpOAuth': {'docs-server': {'accessToken': 'mcp-made-up-1', 'expiresAt': 1}}}


class Base(unittest.TestCase):
    def setUp(self):
        self._old_home = os.environ.get('SWITCHBOARD_HOME')
        self.tmp = tempfile.mkdtemp(prefix='put-back-unlike-')
        os.environ['SWITCHBOARD_HOME'] = os.path.join(self.tmp, 'data')
        os.makedirs(os.environ['SWITCHBOARD_HOME'])
        self.clock = FakeClock(wall=time.time(), mono=time.monotonic())
        self.rig = Rig(wall=self.clock.wall, mono=self.clock.mono, sleep=self.clock.sleep)
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        putback.reset()
        putback._KEPT.clear()
        self.rig.keep('uuid-a', 'ana@work.example', 'org-a')
        self.clock.advance(1.0)
        self.b_text = self.rig.keep('uuid-b', 'ana@home.example', 'org-b', siblings=MCP)   # Claude Code is on B
        self.saved_b = self.rig.vault('uuid-b')
        patcher = mock.patch.object(autoloop, 'notify_put_back', lambda res: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.use.__exit__(None, None, None)
        putback.reset()
        putback._KEPT.clear()
        self.rig.close()
        if self._old_home is None:
            os.environ.pop('SWITCHBOARD_HOME', None)
        else:
            os.environ['SWITCHBOARD_HOME'] = self._old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    @property
    def cred(self):
        return os.path.join(self.rig.home, '.claude', '.credentials.json')

    def watch(self, after=0.0, **hint):
        self.clock.advance(after)
        h = dict(sw.no_hint())
        h.update(hint)
        h['login_file_written'] = h['login_file'] in ('appeared', 'changed')
        return api.watch_look(h)

    def lose_login(self):
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.rig.set_live(json.dumps(dict(MCP, discoveryState={'made-up': 1}), separators=(',', ':')))

    def saved_without_its_record(self, text):
        """Switchboard saved this login of B in the Keychain and stopped before noting it in B's record."""
        self.rig.sec.put(vault.SERVICE_CURRENT, 'uuid-b', text)

    def changed(self, **oauth):
        """B's saved login with another refresh token and the given changes to its account login (None removes)."""
        blob = json.loads(self.saved_b)
        o = dict(blob['claudeAiOauth'], refreshToken='sk-ant-ort01-made-up-unrecorded')
        for k, v in oauth.items():
            if v is None:
                o.pop(k, None)
            else:
                o[k] = v
        blob['claudeAiOauth'] = o
        return json.dumps(blob, separators=(',', ':'))

    def holds(self, text):
        live_text = self.rig.live_text()
        return bool(live_text) and json.loads(live_text).get('claudeAiOauth') == json.loads(text)['claudeAiOauth']

    def record_fp(self):
        return vault.load(self.ctx)['accounts']['uuid-b'].get('fp')

    def left_once(self, text):
        self.saved_without_its_record(text)
        self.lose_login()
        res = [self.watch(0.2, keychain=True)] + [self.watch(putback.PUT_BACK_AFTER_S) for _ in range(3)]
        self.assertEqual([r.action for r in res[1:]], ['leave'] * 3)
        self.assertEqual(len([r for r in res if r.line]), 1, [r.line for r in res])
        self.assertFalse(self.holds(text))
        self.assertIsNone(self.rig.live_text() and json.loads(self.rig.live_text()).get('claudeAiOauth'))
        return [r.line for r in res if r.line][0]


class ANewerSavedLoginIsWritten(Base):
    def test_a_newer_sign_in_saved_without_its_record_is_put_back_and_then_noted(self):
        self.clock.advance(600)
        newer = self.rig.server.add('uuid-b', 'ana@home.example', 'org-b')
        self.saved_without_its_record(newer)
        self.lose_login()
        self.assertEqual(self.watch(0.2, keychain=True).action, 'wait')
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, 'put_back', res.reason)
        self.assertTrue(self.holds(newer))
        self.watch(1.0, keychain=True)                               # the next look's keep step
        self.assertEqual(self.record_fp(), live.parse(newer).fingerprint)

    def test_a_newer_saved_login_goes_in_beside_an_older_generation_in_the_plain_file(self):
        self.clock.advance(600)
        noted = self.rig.server.refresh_for(self.b_text)             # renewed, saved and noted
        with vault.changing(self.ctx) as doc:
            self.assertTrue(vault.save_login(self.ctx, 'uuid-b', noted).ok)
            vault.note_login(doc['accounts']['uuid-b'], live.parse(noted), None, self.clock.wall())
        self.clock.advance(600)
        newer = self.rig.server.refresh_for(noted)                   # renewed and saved; the record not noted
        self.saved_without_its_record(newer)
        with open(self.cred, 'w') as f:
            f.write(self.b_text)                                     # the file holds an older generation
        os.chmod(self.cred, 0o600)
        t = self.clock.wall() - 3600
        os.utime(self.cred, (t, t))
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.assertEqual(self.watch(0.2, keychain=True).action, 'wait')
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, putback.PROMOTE, res.reason)
        self.assertTrue(self.holds(newer))


class OtherSavedLoginsUnlikeTheirRecordAreLeft(Base):
    def test_an_older_one(self):
        rec = vault.load(self.ctx)['accounts']['uuid-b']
        line = self.left_once(self.changed(expiresAt=rec['expires_ms'] - 60000))
        self.assertIn("is not the one Switchboard's record of it describes", line)

    def test_one_with_no_access_expiry(self):
        line = self.left_once(self.changed(expiresAt=None))
        self.assertIn("is not the one Switchboard's record of it describes", line)

    def test_one_that_is_another_saved_accounts_own_login_however_new(self):
        # a full login with a later access expiry, but its fingerprint is the one A's record carries: the saved
        # accounts prove it to be A's, so it is never written under B's name
        other = json.loads(self.rig.vault('uuid-a'))
        rec = vault.load(self.ctx)['accounts']['uuid-b']
        other['claudeAiOauth']['expiresAt'] = rec['expires_ms'] + 600000
        text = json.dumps(other, separators=(',', ':'))
        self.assertEqual(live.parse(text).fingerprint, vault.load(self.ctx)['accounts']['uuid-a']['fp'])
        self.assertTrue(live.parse(text).full_pair)
        line = self.left_once(text)
        self.assertIn("is not the one Switchboard's record of it describes", line)

    def test_one_that_is_not_a_full_login(self):
        rec = vault.load(self.ctx)['accounts']['uuid-b']
        line = self.left_once(self.changed(accessToken=None, expiresAt=rec['expires_ms'] + 600000))
        self.assertIn('ana@home.example', line)


if __name__ == '__main__':
    unittest.main()
