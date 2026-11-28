import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# An item without a login beside Claude Code's plain login file that holds the account's login: Claude Code is signed
# out (an item that is there wins over the file), so the login goes into the item. The marks on an account (refused,
# ended, a possible conflict, ...) describe its SAVED copy: they do not stop the file's own login going in (as they do
# not stop a promote), and they do stop the saved copy going in when the file holds an older generation. The account
# engine on its stand-ins (fakes_accounts) with a fake clock. Made-up *.example accounts.
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
        self.tmp = tempfile.mkdtemp(prefix='repair-marks-')
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

    def look(self, after):
        """The loop's look (no keep step, so the saved copy stays as the test left it)."""
        self.clock.advance(after)
        return putback.look(self.ctx, dict(sw.no_hint(), keychain=True))

    def beside_the_file(self, text):
        with open(self.cred, 'w') as f:
            f.write(text)
        os.chmod(self.cred, 0o600)
        t = self.clock.wall() - 3600
        os.utime(self.cred, (t, t))
        self.rig.set_live(json.dumps(dict(MCP, discoveryState={'made-up': 7}), separators=(',', ':')))

    def mark(self, **fields):
        with vault.changing(self.ctx) as doc:
            doc['accounts']['uuid-b'].update(fields)

    def oauth(self):
        text = self.rig.live_text()
        return json.loads(text).get('claudeAiOauth') if text else None


class TheFilesOwnLogin(Base):
    def newer_in_the_file(self):
        self.clock.advance(600)
        newer = self.rig.server.refresh_for(self.b_text)        # Claude Code renewed it; only the file kept it
        self.beside_the_file(newer)
        return newer

    def test_a_refused_saved_copy_does_not_stop_the_files_newer_login(self):
        newer = self.newer_in_the_file()
        self.mark(strike={'fp': live.parse(self.b_text).fingerprint, 'at': self.clock.wall()})
        self.assertEqual(self.look(0.2).action, 'wait')
        res = self.look(putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, 'put_back', res.reason)
        self.assertEqual(self.oauth(), json.loads(newer)['claudeAiOauth'])

    def test_no_mark_of_the_saved_copy_stops_it(self):
        for fields in ({'conflict': {'profile_uuid': 'uuid-z', 'at': 1}}, {'proof': {'kind': 'label', 'at': 1}}):
            with self.subTest(mark=sorted(fields)):
                self.tearDown()
                self.setUp()
                newer = self.newer_in_the_file()
                self.mark(**fields)
                self.look(0.2)
                res = self.look(putback.PUT_BACK_AFTER_S)
                self.assertEqual(res.action, 'put_back', res.reason)
                self.assertEqual(self.oauth(), json.loads(newer)['claudeAiOauth'])


class AnOlderGenerationInTheFile(Base):
    def test_a_marked_saved_copy_is_not_written_and_it_is_said_once(self):
        old = self.b_text
        self.clock.advance(600)
        self.rig.set_live(self.rig.server.refresh_for(old))
        self.assertIn('Saved the newer login', api.keep_pass())         # the saved copy is the newer one
        self.beside_the_file(old)
        before = self.rig.live_text()
        self.mark(strike={'fp': live.parse(self.rig.vault('uuid-b')).fingerprint, 'at': self.clock.wall()})
        res = [self.look(putback.PUT_BACK_AFTER_S) for _ in range(5)]
        self.assertEqual(set(r.action for r in res), {'leave'})
        self.assertEqual(len([r for r in res if r.line]), 1)
        self.assertEqual(self.rig.live_text(), before)


if __name__ == '__main__':
    unittest.main()
