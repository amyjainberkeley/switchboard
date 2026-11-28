import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The note of our last write and the take-back that reads it. A landed put-back, promote or repair is noted in the
# record for TAKE_BACK_S (how it was made, what the item held right after it, the stamp of the plain login file it
# left). The take-back reads the note to undo a write that met a chosen sign-out, and the proof rule reads its stamp so
# our own rewrite of the plain file is never taken for Claude Code's fallback. So the note lives its full minute,
# whatever the item holds meanwhile; a write taken back at a later look does not count toward the limits, and History
# says so when the put-back was said; and a take-back deletes nothing without the save lock it was given. The account
# engine on its stand-ins (fakes_accounts) with a fake clock. Made-up *.example accounts.
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from fakes_autoswitch import FakeClock
from fakes_accounts import Rig, identity_block
from switchboard.accounts import api, autoloop, context, facts, putback
from switchboard.accounts import signin_watch as sw

MCP = {'mcpOAuth': {'docs-server': {'accessToken': 'mcp-made-up-1', 'expiresAt': 1}}}
B_BLOCK = identity_block('uuid-b', 'ana@home.example', 'org-b')


class Base(unittest.TestCase):
    def setUp(self):
        self._old_home = os.environ.get('SWITCHBOARD_HOME')
        self.tmp = tempfile.mkdtemp(prefix='take-back-note-')
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

    @property
    def cred(self):
        return os.path.join(self.rig.home, '.claude', '.credentials.json')

    def write_file(self, text, age=0.0):
        """A write of the plain login file as Claude Code's fallback makes it: a new file renamed into place."""
        tmp = self.cred + '.cc'
        with open(tmp, 'w') as f:
            f.write(text)
        os.chmod(tmp, 0o600)
        t = self.clock.wall() - age
        os.utime(tmp, (t, t))
        os.replace(tmp, self.cred)

    def watch(self, after=0.0, written=None, **hint):
        self.clock.advance(after)
        h = dict(sw.no_hint())
        h.update(hint)
        h['login_file_written'] = h['login_file'] in ('appeared', 'changed') if written is None else written
        return api.watch_look(h)

    def lose_login(self):
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.rig.set_live(json.dumps(dict(MCP, discoveryState={'made-up': 1}), separators=(',', ':')))

    def record(self):
        return putback.load_record(self.ctx)

    def repairs(self):
        return [e[0] for e in self.rig.events if e[2].get('h') == 'repair']

    def put_back_beside_an_old_file(self):
        """An old plain file beside the item (no login in it); the login lost; our put-back lands, and its write
        rewrites the file (its stamp noted)."""
        self.write_file(json.dumps(MCP), age=3600.0)
        self.lose_login()
        self.assertEqual(self.watch(0.2, keychain=True).action, 'wait')
        self.assertEqual(self.watch(putback.PUT_BACK_AFTER_S).action, 'put_back')
        self.assertIsNotNone(self.record()['written']['file'])


class TheNoteLivesItsMinute(Base):
    """The order of the fourth review: our put-back rewrites an old plain file; 2 s later the item is deleted and the
    file stays. The look that finds the item gone keeps the note, so our own rewrite is still not proof."""

    def test_the_item_gone_keeps_the_note_and_our_own_rewrite_is_no_proof(self):
        self.put_back_beside_an_old_file()
        written = self.record()['written']
        self.rig.sec.drop(self.rig.service, self.rig.account)      # the item deleted; our file stays
        first = self.watch(2.0, keychain=True)
        self.assertEqual((first.action, first.next_s), ('wait', putback.PUT_BACK_AFTER_S))
        self.assertEqual(self.record()['written'], written)
        second = self.watch(putback.FAULT_AFTER_S)
        self.assertEqual((second.action, second.next_s), ('wait', putback.PUT_BACK_AFTER_S))
        self.assertFalse(putback.fault(self.record(), self.clock.wall()))

    def test_a_fallback_write_by_claude_code_after_it_is_still_proof(self):
        self.put_back_beside_an_old_file()
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.assertEqual(self.watch(2.0, keychain=True).action, 'wait')
        self.write_file(self.rig.live_text() or self.b_text)       # another stamp: Claude Code wrote it anew
        res = self.watch(putback.FAULT_AFTER_S, login_file='changed')
        self.assertTrue(putback.fault(self.record(), self.clock.wall()))
        self.assertEqual(res.action, putback.PROMOTE, res.reason)

    def test_another_login_in_the_item_keeps_the_note_and_takes_nothing_back(self):
        self.rig.sec.drop(self.rig.service, self.rig.account)      # no item at all: ours is created
        self.watch(0.2, keychain=True)
        self.assertEqual(self.watch(putback.PUT_BACK_AFTER_S).action, 'put_back')
        written = self.record()['written']
        renewed = self.rig.claude_renews()                          # a renewal: not our login any more
        self.watch(1.0, keychain=True)
        self.assertEqual(self.record()['written'], written)
        self.rig.set_config(None)                                   # then a sign-out's settings, the item kept
        self.watch(0.5, settings=True)
        self.assertEqual(self.rig.live_text(), renewed)

    def test_a_take_back_that_finds_another_login_under_the_save_lock_leaves_the_note(self):
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.watch(0.2, keychain=True)
        self.assertEqual(self.watch(putback.PUT_BACK_AFTER_S).action, 'put_back')
        written = self.record()['written']
        self.rig.set_config(None)
        lock = facts.storage_lock(self.ctx.env, self.ctx.home)
        renewed = []

        def renew(verb, service, account):
            if verb == 'read' and service == self.rig.service and os.path.isdir(lock) and not renewed:
                renewed.append(self.rig.claude_renews())            # just before the read under the save lock
        self.rig.kc.on_call = renew
        self.watch(0.5, settings=True)
        self.assertEqual(self.rig.live_text(), renewed[0])
        self.assertEqual(self.record()['written'], written)


class AWriteTakenBackLaterDoesNotCount(Base):
    """A chosen sign-out that met a quick put-back: the item deleted beside a fresh fallback write (proof), our put-back
    a moment later, then the settings cleared and the write taken back at the next look."""

    def sign_out_that_meets_a_put_back(self):
        self.write_file(json.dumps(MCP))                            # a chat's save fell back: proof of the fault
        self.rig.sec.drop(self.rig.service, self.rig.account)      # the sign-out deletes the item, removes the file
        os.unlink(self.cred)
        first = self.watch(0.1, written=True, keychain=True, login_file='gone')
        self.assertEqual((first.action, first.next_s), ('wait', putback.FAULT_AFTER_S))
        self.assertEqual(self.watch(putback.FAULT_AFTER_S).action, 'put_back')
        self.rig.set_config(None)                                   # and clears the settings a moment later
        self.watch(1.0, settings=True)
        self.assertIsNone(self.rig.live_text())

    def sign_in_again(self, after=600.0):
        self.clock.advance(after)
        self.rig.sign_in('uuid-b', 'ana@home.example', 'org-b', siblings=MCP)
        self.assertIn(self.watch(0.5, keychain=True, settings=True).action, ('kept', 'leave'))

    def test_four_chosen_sign_outs_in_an_hour_leave_the_engine_free_for_a_real_loss(self):
        for n in range(4):
            self.sign_out_that_meets_a_put_back()
            self.sign_in_again()
        rec = self.record()
        self.assertEqual(rec['done'], [])
        self.assertEqual(rec['episodes'], [])
        self.lose_login()                                           # a real loss, no proof
        self.assertEqual(self.watch(0.2, keychain=True).action, 'wait')
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, 'put_back', res.reason)
        line = 'Claude Code lost its sign-in; Switchboard put ana@home.example back'
        self.assertEqual(self.repairs(), [line, putback.TAKEN_BACK_LINE % 'ana@home.example'] * 4 + [line])

    def test_a_real_loss_with_proof_after_them_is_put_back_on_the_quick_path(self):
        for n in range(putback.EPISODES_PER_HOUR):
            self.sign_out_that_meets_a_put_back()
            self.sign_in_again()
        self.write_file(json.dumps(MCP))
        self.lose_login()
        os.unlink(self.cred)
        self.assertEqual(self.watch(0.1, written=True, keychain=True, login_file='gone').action, 'wait')
        self.assertEqual(self.watch(putback.FAULT_AFTER_S).action, 'put_back')

    def test_a_take_back_in_the_same_hold_says_nothing_in_history(self):
        self.write_file(json.dumps(MCP))
        self.rig.sec.drop(self.rig.service, self.rig.account)
        os.unlink(self.cred)
        self.watch(0.1, written=True, keychain=True, login_file='gone')

        def on_call(verb, service, account):
            if verb == 'write' and service == self.rig.service:
                self.rig.set_config(None)                           # the settings cleared while we write
        self.rig.kc.on_call = on_call
        res = self.watch(putback.FAULT_AFTER_S)
        self.assertEqual(res.action, 'leave', res.reason)
        self.assertIsNone(self.rig.live_text())
        self.assertEqual(self.repairs(), [])
        self.assertEqual(self.record()['done'], [])

    def test_a_promote_taken_back_later_is_said_in_history_too(self):
        self.write_file(self.b_text)                                # the login only in the plain file
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.assertEqual(self.watch(0.1, keychain=True, login_file='changed').action, 'wait')
        self.assertEqual(self.watch(putback.FAULT_AFTER_S).action, putback.PROMOTE)
        self.rig.set_config(None)
        self.watch(1.0, settings=True)
        self.assertIsNone(self.rig.live_text())
        self.assertEqual(self.repairs(), [putback.PROMOTE_LINE, putback.TAKEN_BACK_LINE % 'ana@home.example'])
        self.assertIsNone(self.record()['episode'])


class TheTakeBackKeepsToItsLock(Base):
    def test_a_save_lock_let_go_meanwhile_deletes_nothing(self):
        self.rig.sec.drop(self.rig.service, self.rig.account)      # no item at all: ours is created
        self.watch(0.2, keychain=True)
        self.assertEqual(self.watch(putback.PUT_BACK_AFTER_S).action, 'put_back')
        ours = self.rig.live_text()
        self.rig.set_config(None)
        lock = facts.storage_lock(self.ctx.env, self.ctx.home)

        def let_go(verb, service, account):
            if verb == 'read' and service == self.rig.service and os.path.isdir(lock):
                os.rmdir(lock)                                      # taken over, or let go on the way out
        self.rig.kc.on_call = let_go
        res = self.watch(0.5, settings=True)
        self.assertEqual(res.action, 'wait', res.reason)
        self.assertEqual(self.rig.live_text(), ours)
        self.rig.kc.on_call = None
        self.watch(0.5, settings=True)
        self.assertIsNone(self.rig.live_text())


if __name__ == '__main__':
    unittest.main()
