import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# What a run of put-backs says, and how often. While Claude Code keeps losing its sign-in a put-back can land up to
# EPISODE_WRITES times in one episode. The History line is said for the episode's first put-back only (as the promote's
# line is); the later ones are counted in the log. A put-back whose writes all fail is said once per episode in the log,
# whichever process tries (the sign-in watch, the loop and the page each look). The account engine on its stand-ins
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
from switchboard.accounts import api, autoloop, context, putback
from switchboard.accounts import signin_watch as sw

MCP = {'mcpOAuth': {'docs-server': {'accessToken': 'mcp-made-up-1', 'expiresAt': 1}}}
LINE = 'Claude Code lost its sign-in; Switchboard put ana@home.example back'


class Base(unittest.TestCase):
    def setUp(self):
        self._old_home = os.environ.get('SWITCHBOARD_HOME')
        self.tmp = tempfile.mkdtemp(prefix='put-back-said-')
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
        self.told = []
        patcher = mock.patch.object(autoloop, 'notify_put_back', self.told.append)
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

    def watch(self, after=0.0, **hint):
        self.clock.advance(after)
        h = dict(sw.no_hint())
        h.update(hint)
        h['login_file_written'] = h['login_file'] in ('appeared', 'changed')
        return api.watch_look(h)

    def lose_login(self):
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.rig.set_live(json.dumps(dict(MCP, discoveryState={'made-up': 1}), separators=(',', ':')))

    def history(self):
        return [e[0] for e in self.rig.events if e[2].get('h') == 'repair']

    def logged(self, words):
        return [t for t in self.rig.logs if words in t]

    def put_backs_in_one_episode(self, n):
        """n put-backs within one episode: Claude Code's fallback seen by the watch, then the login lost again a few
        seconds after each put-back."""
        out = []
        self.lose_login()
        self.assertEqual(self.watch(0.0, login_file='appeared').action, 'wait')
        out.append(self.watch(putback.FAULT_AFTER_S))
        for _ in range(n - 1):
            self.clock.advance(3.0)
            self.lose_login()
            self.assertEqual(self.watch(0.0, keychain=True).action, 'wait')
            out.append(self.watch(putback.FAULT_AFTER_S))
        self.assertEqual([r.action for r in out], ['put_back'] * n, [r.reason for r in out])
        return out


class TheHistoryLineOfARunOfPutBacks(Base):
    def test_said_for_the_episodes_first_put_back_only_and_the_rest_counted_in_the_log(self):
        res = self.put_backs_in_one_episode(4)
        self.assertEqual(self.history(), [LINE])
        self.assertEqual([r.line for r in res], [LINE, None, None, None])
        self.assertEqual(len(self.logged('Put the saved login of ana@home.example back')), 1)
        # the later ones came from the copy of the sign-in the first left in the plain login file (the same login)
        self.assertEqual(len(self.logged("Put the login of ana@home.example from Claude Code's plain login file")), 3)
        counted = self.logged('writes of its login since the first one')
        self.assertEqual(len(counted), 3, self.rig.logs)
        self.assertIn('4 writes of its login since the first one', counted[-1])
        self.assertEqual(len({r.at for r in res}), 1)               # one episode: one notification
        self.assertEqual(len(self.told), 4)                        # each told; notify shows one per episode

    def test_without_proof_each_put_back_says_it_as_there_are_four_an_hour_at_most(self):
        for wait in putback.PUT_BACK_WAITS[:2]:
            self.lose_login()
            self.watch(0.2, keychain=True)
            self.assertEqual(self.watch(wait).action, 'put_back')
            self.clock.advance(2.0)
        self.assertEqual(self.history(), [LINE, LINE])

    def test_a_new_episode_says_it_again(self):
        self.put_backs_in_one_episode(2)
        self.clock.advance(putback.EPISODE_MAX_S)
        self.put_backs_in_one_episode(1)
        self.assertEqual(self.history(), [LINE, LINE])

    def test_a_promote_in_the_same_episode_keeps_its_own_line(self):
        self.put_backs_in_one_episode(1)
        self.clock.advance(3.0)
        path = os.path.join(self.rig.home, '.claude', '.credentials.json')
        with open(path, 'w') as f:
            f.write(self.b_text)
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.assertEqual(self.watch(0.0, login_file='appeared').action, 'wait')
        self.assertEqual(self.watch(putback.FAULT_AFTER_S).action, 'promote')
        self.clock.advance(3.0)
        self.lose_login()
        self.watch(0.0, keychain=True)
        self.assertEqual(self.watch(putback.FAULT_AFTER_S).action, 'put_back')
        self.assertEqual(self.history(), [LINE, putback.PROMOTE_LINE])


class TheFailedLine(Base):
    def refuse_every_write(self):
        self.rig.kc.rule('write', service=self.rig.service, why='denied', times=-1)

    def test_said_once_per_episode_whichever_process_tries(self):
        self.lose_login()
        self.refuse_every_write()
        self.watch(0.2)
        answers = []
        for wait in putback.PUT_BACK_WAITS[:3]:
            putback.reset()                         # each try from another process: the watch, the loop, the page
            answers.append(self.watch(wait))
        self.assertEqual([r.action for r in answers], ['failed'] * 3)
        self.assertEqual(len([r for r in answers if r.line]), 1)
        self.assertEqual(len(self.logged('putting ana@home.example back did not work')), 1)

    def test_said_again_once_the_login_was_back_in_between(self):
        self.lose_login()
        self.refuse_every_write()
        self.watch(0.2)
        self.assertEqual(self.watch(putback.PUT_BACK_AFTER_S).action, 'failed')
        self.rig.set_live(self.b_text)                              # the login is back (Claude Code's own save)
        putback.reset()
        self.assertEqual(self.watch(1.0).action, 'leave')
        self.clock.advance(putback.PUT_BACK_SPAN_S)
        self.lose_login()
        putback.reset()
        self.watch(0.2)
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, 'failed')
        self.assertIsNotNone(res.line)
        self.assertEqual(len(self.logged('putting ana@home.example back did not work')), 2)

    def test_a_promote_that_keeps_failing_is_said_once_whichever_process_tries(self):
        path = os.path.join(self.rig.home, '.claude', '.credentials.json')
        with open(path, 'w') as f:
            f.write(self.b_text)
        t = self.clock.wall() - 3600
        os.utime(path, (t, t))
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.refuse_every_write()
        self.watch(0.2)
        answers = []
        for wait in putback.PUT_BACK_WAITS[:3]:
            putback.reset()
            answers.append(self.watch(wait))
        self.assertEqual([r.action for r in answers], ['failed'] * 3)
        self.assertEqual(len([r for r in answers if r.line]), 1)


if __name__ == '__main__':
    unittest.main()
