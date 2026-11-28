import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Right after a switch away from an account, settings that name it may be a stale write by a Claude Code session that
# was open during the switch, so a loss under that name is left for SWITCHED_AWAY_S. The rule ends once the account is
# signed in again after the switch: its record carries a sign-in end stamp that is not the sign-in the switch left, or
# Claude Code's plain login file holds a login proven to be its newest (the promote's own proof). The account engine
# on its stand-ins (fakes_accounts) with a fake clock, through the real switch. Made-up *.example accounts.
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from fakes_autoswitch import FakeClock
from fakes_accounts import Rig, identity_block
from switchboard.accounts import api, autoloop, context, live, putback, roster, switcher, vault
from switchboard.accounts import signin_watch as sw

MCP = {'mcpOAuth': {'docs-server': {'accessToken': 'mcp-made-up-1', 'expiresAt': 1}}}
B_BLOCK = identity_block('uuid-b', 'ana@home.example', 'org-b')


class Base(unittest.TestCase):
    def setUp(self):
        self._old_home = os.environ.get('SWITCHBOARD_HOME')
        self.tmp = tempfile.mkdtemp(prefix='put-back-switch-')
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

    def switch_to_a(self):
        self.clock.advance(5.0)
        self.assertTrue(switcher.switch(self.ctx, 'uuid-a', source='auto', kind='forced').ok)
        roster.forget_live()
        self.clock.advance(5.0)

    def watch(self, after=1.0, **hint):
        self.clock.advance(after)
        h = dict(sw.no_hint())
        h.update(hint)
        h['login_file_written'] = h['login_file'] in ('appeared', 'changed')
        return api.watch_look(h)

    def lose_login(self):
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.rig.set_live(json.dumps(dict(MCP, discoveryState={'made-up': 1}), separators=(',', ':')))

    def in_file_only(self, text, age=3600.0):
        with open(self.cred, 'w') as f:
            f.write(text)
        os.chmod(self.cred, 0o600)
        t = self.clock.wall() - age
        os.utime(self.cred, (t, t))
        self.rig.sec.drop(self.rig.service, self.rig.account)

    def live_fp(self):
        text = self.rig.live_text()
        return live.parse(text).fingerprint if text else None

    def live_writes(self):
        return sum(1 for c in self.rig.kc.calls if c[0] == 'write' and c[1] == self.rig.service)


class SignedInAgainAfterTheSwitch(Base):
    def test_a_sign_in_kept_after_the_switch_is_put_back_when_it_is_lost(self):
        self.switch_to_a()
        self.clock.advance(180.0)               # a sign-in within two minutes can't be told from a renewal by its stamp
        new = self.rig.sign_in('uuid-b', 'ana@home.example', 'org-b', siblings=MCP)   # /login of B in Claude Code
        res = self.watch(2.0, keychain=True)
        self.assertEqual(res.action, 'kept', res.reason)                       # the keep step keeps it
        self.lose_login()
        self.assertEqual(self.watch(0.2, keychain=True).action, 'wait')
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.assertEqual((res.action, res.account), ('put_back', 'ana@home.example'), res.reason)
        self.assertEqual(self.live_fp(), live.parse(new).fingerprint)

    def test_a_newer_login_of_the_account_in_the_plain_file_is_promoted(self):
        self.switch_to_a()
        self.rig.set_config(B_BLOCK)
        renewed = self.rig.server.refresh_for(self.b_text)    # a chat still on B renewed it; its save failed
        self.in_file_only(renewed)
        self.clock.advance(0.2)                       # the loop's looks: no keep step, so only the file's proof ends it
        self.assertEqual(putback.look(self.ctx).action, 'wait')
        self.clock.advance(putback.PUT_BACK_AFTER_S)
        res = putback.look(self.ctx)
        self.assertEqual(res.action, putback.PROMOTE, res.reason)
        self.assertEqual(self.live_fp(), live.parse(renewed).fingerprint)

    def test_through_the_watch_the_keep_step_saves_it_first_and_it_is_promoted(self):
        self.switch_to_a()
        self.rig.set_config(B_BLOCK)
        renewed = self.rig.server.refresh_for(self.b_text)
        self.in_file_only(renewed)
        self.assertIn(self.watch(0.2, keychain=True).action, ('wait', 'kept'))
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, putback.PROMOTE, res.reason)
        self.assertEqual(self.live_fp(), live.parse(renewed).fingerprint)


class StillLeftRightAfterTheSwitch(Base):
    def test_a_stale_name_with_no_sign_in_since_is_left_and_said_once(self):
        self.switch_to_a()
        self.rig.set_config(B_BLOCK)
        self.lose_login()
        res = [self.watch(putback.PUT_BACK_AFTER_S) for _ in range(10)]
        self.assertEqual(set(r.action for r in res), {'leave'})
        self.assertEqual(len([r for r in res if r.line]), 1)
        self.assertEqual(res[0].key, 'switched')
        self.assertEqual(self.live_writes(), 1)                                   # the switch's own

    def test_the_switchs_own_rewrite_of_the_plain_file_does_not_end_it(self):
        with open(self.cred, 'w') as f:
            f.write(self.b_text)                                                   # an old fallback file
        self.switch_to_a()                                                         # rewritten with A's login
        self.rig.set_config(B_BLOCK)
        self.rig.sec.drop(self.rig.service, self.rig.account)
        res = [self.watch(putback.PUT_BACK_AFTER_S) for _ in range(6)]
        self.assertEqual(set(r.action for r in res), {'leave'}, [r.reason for r in res])
        self.assertIsNone(self.rig.live_text())

    def test_a_keep_before_the_switch_does_not_end_it(self):
        self.clock.advance(600)
        self.rig.claude_renews()
        self.assertIn('Saved the newer login', api.keep_pass())                  # B kept, then the switch
        self.switch_to_a()
        self.rig.set_config(B_BLOCK)
        self.lose_login()
        res = [self.watch(putback.PUT_BACK_AFTER_S) for _ in range(4)]
        self.assertEqual(set(r.action for r in res), {'leave'})

    def test_the_rule_with_no_io(self):
        seen = putback.Seen(live=api.PRESENT, login='no_login', named='uuid-b', saved='ok', left_at=1000.0)
        self.assertEqual(putback.decide(seen, ('uuid-b', 1005.0), [], 1010.0).key, 'switched')
        filed = putback.Seen(live=api.PRESENT, login='oauth', from_file=True, full=True, owner=True, named='uuid-b',
                             saved='ok', left_at=1000.0)
        self.assertEqual(putback.decide(filed, ('uuid-b', 1005.0), [], 1010.0).action, putback.PROMOTE)
        older = putback.Seen(live=api.PRESENT, login='oauth', from_file=True, full=True, older=True,
                             named='uuid-b', saved='ok', left_at=1000.0)
        self.assertEqual(putback.decide(older, ('uuid-b', 1005.0), [], 1010.0).key, 'switched')


class TheSwitchsOwnRewriteOfThePlainFile(Base):
    """A switch rewrites an existing plain login file with the login it writes, and its record in the accounts file
    keeps the file's stamp right after that write ({'file': [device, inode, mtime ns, size]}; absent when there was no
    file). A file with exactly that stamp is the switch's own, never proof of Claude Code's fallback. Most of these
    tests put the switch's record in by hand; the last two go through the real switch, which writes it itself."""

    def switched_beside_an_old_file(self, stamp=True):
        """A switch to B (Claude Code's login stays B's) beside an old plain file: the file rewritten with the item's
        text, and the switch's record, with the file's stamp when `stamp`."""
        with open(self.cred, 'w') as f:
            f.write(self.b_text)
        t = self.clock.wall() - 3600
        os.utime(self.cred, (t, t))
        self.clock.advance(5.0)
        tmp = self.cred + '.switch'
        with open(tmp, 'w') as f:
            f.write(self.rig.live_text())
        os.replace(tmp, self.cred)
        st = os.stat(self.cred)
        rec = {'at': self.clock.wall(), 'from': 'uuid-a', 'to': 'uuid-b', 'kind': 'manual', 'source': 'cli'}
        if stamp:
            rec['file'] = [st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size]
        with vault.changing(self.ctx) as doc:
            doc['switches'] = (doc.get('switches') or []) + [rec]

    def chats_save(self):
        """The pile-up after a switch: a chat saves its own part, so the item no longer matches the file."""
        blob = json.loads(self.rig.live_text())
        blob['discoveryState'] = {'made-up': 3}
        self.rig.set_live(json.dumps(blob, separators=(',', ':')))

    def sign_out_starts(self):
        """A chosen sign-out's first step: the item deleted; the settings still name the account."""
        self.rig.sec.drop(self.rig.service, self.rig.account)

    def looks_after(self):
        first = self.watch(0.1, keychain=True)
        quick = self.watch(putback.FAULT_AFTER_S + 0.1)
        return first, quick

    def test_then_a_sign_out_takes_the_slow_path(self):
        self.switched_beside_an_old_file()
        self.chats_save()
        self.assertEqual(self.watch(0.2, keychain=True, login_file='changed').action, 'leave')
        self.assertFalse(putback.fault(putback.load_record(self.ctx), self.clock.wall()))
        self.sign_out_starts()
        first, quick = self.looks_after()
        self.assertEqual((first.action, first.next_s), ('wait', putback.PUT_BACK_AFTER_S))
        self.assertEqual(quick.action, 'wait', quick.reason)
        self.assertNotIn(('write', self.rig.service, self.rig.account), self.rig.kc.calls[-6:])

    def test_also_when_the_watch_sees_only_the_item_go(self):
        self.switched_beside_an_old_file()
        self.sign_out_starts()
        first, quick = self.looks_after()
        self.assertEqual((first.action, first.next_s), ('wait', putback.PUT_BACK_AFTER_S))
        self.assertEqual(quick.action, 'wait', quick.reason)

    def test_a_fallback_write_by_claude_code_after_the_switch_still_opens_the_quick_path(self):
        self.switched_beside_an_old_file()
        self.clock.advance(1.0)
        with open(self.cred + '.cc', 'w') as f:                     # its own save failed: the file written anew
            f.write(self.rig.live_text())
        os.replace(self.cred + '.cc', self.cred)
        self.sign_out_starts()                                      # and the item deleted
        first = self.watch(0.1, keychain=True, login_file='changed')
        self.assertEqual((first.action, first.next_s), ('wait', putback.FAULT_AFTER_S))
        self.assertEqual(self.watch(putback.FAULT_AFTER_S).action, putback.PROMOTE)

    def test_a_switch_record_without_the_stamp_changes_nothing(self):
        self.switched_beside_an_old_file(stamp=False)
        self.sign_out_starts()
        first = self.watch(0.1, keychain=True)
        self.assertEqual((first.action, first.next_s), ('wait', putback.FAULT_AFTER_S))   # as before: taken for proof

    def switched_by_the_real_switch(self):
        """The real switch to A beside an old plain file: it rewrites the file and records the file's stamp itself."""
        with open(self.cred, 'w') as f:
            f.write(self.b_text)
        t = self.clock.wall() - 3600
        os.utime(self.cred, (t, t))
        self.clock.advance(5.0)
        self.assertTrue(switcher.switch(self.ctx, 'uuid-a', source='auto', kind='forced').ok)
        roster.forget_live()
        st = os.stat(self.cred)
        self.assertEqual(vault.load(self.ctx)['switches'][-1].get('file'),
                         [st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size])
        with open(self.cred) as f:
            self.assertEqual(f.read(), self.rig.live_text())            # the switch's own rewrite: the item's text

    def test_through_the_real_switch_a_sign_out_takes_the_slow_path(self):
        self.switched_by_the_real_switch()
        self.chats_save()
        self.assertEqual(self.watch(0.2, keychain=True, login_file='changed').action, 'leave')
        self.assertFalse(putback.fault(putback.load_record(self.ctx), self.clock.wall()))
        self.sign_out_starts()
        first, quick = self.looks_after()
        self.assertEqual((first.action, first.next_s), ('wait', putback.PUT_BACK_AFTER_S))
        self.assertEqual(quick.action, 'wait', quick.reason)
        self.assertNotIn(('write', self.rig.service, self.rig.account), self.rig.kc.calls[-6:])

    def test_through_the_real_switch_a_fallback_write_after_it_still_opens_the_quick_path(self):
        self.switched_by_the_real_switch()
        self.clock.advance(1.0)
        with open(self.cred + '.cc', 'w') as f:                     # Claude Code's own save failed: the file anew
            f.write(self.rig.live_text())
        os.replace(self.cred + '.cc', self.cred)
        self.sign_out_starts()                                      # and the item deleted
        first = self.watch(0.1, keychain=True, login_file='changed')
        self.assertEqual((first.action, first.next_s), ('wait', putback.FAULT_AFTER_S))
        self.assertEqual(self.watch(putback.FAULT_AFTER_S).action, putback.PROMOTE)


if __name__ == '__main__':
    unittest.main()
