import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The keep step of the sign-in watch's look: when Claude Code holds a login of the account its settings name that is
# not the saved copy (Claude Code renewed it), the keep pass is asked at once, so a later put-back never writes an older
# generation. A pass that returned nothing because it could not keep (a lock busy, a renewal under way) is asked again
# at the next look; an answer (a note, or nothing left to keep) stands for 60 s for the same login. The account engine
# on its stand-ins (fakes_accounts) with a fake clock. Made-up *.example accounts.
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from fakes_autoswitch import FakeClock
from fakes_accounts import Rig
from switchboard.accounts import api, autoloop, capture, context, live, putback
from switchboard.accounts import signin_watch as sw

MCP = {'mcpOAuth': {'docs-server': {'accessToken': 'mcp-made-up-1', 'expiresAt': 1}}}


class Base(unittest.TestCase):
    def setUp(self):
        self._old_home = os.environ.get('SWITCHBOARD_HOME')
        self.tmp = tempfile.mkdtemp(prefix='put-back-keep-')
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
        self.asked = []
        self.real_keep = capture.keep_pass

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

    def keep_pass(self, answers):
        """capture.keep_pass, counted; each of `answers` in turn first: 'nothing' returns None without doing
        anything (a lock busy), 'real' runs the real pass."""
        answers = list(answers)

        def keep(ctx, force=False):
            self.asked.append(self.clock.wall())
            if answers and answers.pop(0) == 'nothing':
                return None
            return self.real_keep(ctx, force=force)
        patcher = mock.patch.object(capture, 'keep_pass', keep)
        patcher.start()
        self.addCleanup(patcher.stop)

    def watch(self, after=1.0):
        self.clock.advance(after)
        return api.watch_look(dict(sw.no_hint(), keychain=True))

    def saved_fp(self):
        return live.parse(self.rig.vault('uuid-b')).fingerprint


class TheKeepStep(Base):
    def test_a_keep_pass_that_returned_nothing_is_asked_again_and_the_newer_login_is_kept(self):
        self.clock.advance(600)
        newer = self.rig.claude_renews()
        self.keep_pass(['nothing', 'real'])
        self.assertEqual(self.watch().action, 'leave')
        self.assertNotEqual(self.saved_fp(), live.parse(newer).fingerprint)
        res = self.watch()
        self.assertEqual(res.action, 'kept', res.reason)
        self.assertEqual(self.saved_fp(), live.parse(newer).fingerprint)
        self.assertEqual(len(self.asked), 2)

    def test_then_a_loss_puts_back_the_newer_login(self):
        self.clock.advance(600)
        newer = self.rig.claude_renews()
        self.keep_pass(['nothing', 'real'])
        self.watch()
        self.watch()
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.rig.set_live(json.dumps(MCP, separators=(',', ':')))
        self.watch(0.2)
        self.assertEqual(self.watch(putback.PUT_BACK_AFTER_S).action, 'put_back')
        self.assertEqual(live.parse(self.rig.live_text()).fingerprint, live.parse(newer).fingerprint)

    def test_nothing_left_to_keep_stands_for_sixty_seconds_then_it_is_asked_again(self):
        old = self.b_text
        self.clock.advance(600)
        newer = self.rig.claude_renews()
        self.assertIn('Saved the newer login', api.keep_pass())
        self.rig.set_live(old)                       # an older generation written back by a process: nothing to keep
        self.keep_pass([])
        self.watch()
        self.assertEqual(len(self.asked), 1)
        self.watch(1.0)
        self.watch(putback.KEPT_FOR_S - 3.0)
        self.assertEqual(len(self.asked), 1)
        self.watch(3.0)
        self.assertEqual(len(self.asked), 2)
        self.assertEqual(self.saved_fp(), live.parse(newer).fingerprint)     # the newer one stays saved

    def test_a_pass_that_keeps_failing_is_asked_at_each_look_and_never_more(self):
        self.clock.advance(600)
        self.rig.claude_renews()
        self.keep_pass(['nothing'] * 5)
        for _ in range(5):
            self.watch()
        self.assertEqual(len(self.asked), 5)


if __name__ == '__main__':
    unittest.main()
