import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Our own write of Claude Code's plain login file is known by its exact stamp. A write of the item rewrites an existing
# plain file too (a temporary file renamed over it), and the put-back, the promote, the repair and the switch note the
# file's stamp, so that rewrite is never taken for Claude Code's fallback. On a busy Mac another process can replace the
# file the moment after our rename, so the stamp noted is the one of the file our write put there (taken from the
# temporary file before the rename: a rename keeps device, inode, write time and size), never asked of the file system
# afterwards. Stand-ins only (fakes_accounts), a fake clock, made-up *.example accounts.
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from fakes_autoswitch import FakeClock
from fakes_accounts import Rig
from switchboard.accounts import api, autoloop, ccfiles, context, facts, live, putback, roster, switcher, vault
from switchboard.accounts import signin_watch as sw

MCP = {'mcpOAuth': {'docs-server': {'accessToken': 'mcp-made-up-1', 'expiresAt': 1}}}
REAL_REPLACE = os.replace


class Base(unittest.TestCase):
    def setUp(self):
        self._old_home = os.environ.get('SWITCHBOARD_HOME')
        self.tmp = tempfile.mkdtemp(prefix='own-file-stamp-')
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
        self.path = facts.credentials_file(self.rig.env, self.rig.home)
        patcher = mock.patch.object(autoloop, 'notify_put_back', lambda res: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.ours = []                  # the stamp of each file our rename put in place, taken right after it

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

    def plain_file(self, text, age=3600.0):
        with open(self.path, 'w') as f:
            f.write(text)
        os.chmod(self.path, 0o600)
        t = self.clock.wall() - age
        os.utime(self.path, (t, t))

    def stamp(self):
        st = os.stat(self.path)
        return (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size)

    def another_writer_after_our_rename(self, text):
        """Patch os.replace: right after the first rename onto Claude Code's plain file, another process replaces that
        file with `text` (as a Claude Code process whose save was refused writes its fallback)."""
        target = os.path.realpath(self.path)

        def replace(src, dst, *a, **k):
            REAL_REPLACE(src, dst, *a, **k)
            if os.path.realpath(dst) == target and not self.ours:
                self.ours.append(self.stamp())
                other = self.path + '.other'
                with open(other, 'w') as f:
                    f.write(text)
                os.chmod(other, 0o600)
                t = self.clock.wall()
                os.utime(other, (t, t))
                REAL_REPLACE(other, self.path)
        patcher = mock.patch.object(os, 'replace', replace)
        patcher.start()
        self.addCleanup(patcher.stop)

    def watch(self, after=0.0, **hint):
        self.clock.advance(after)
        h = dict(sw.no_hint())
        h.update(hint)
        h['login_file_written'] = h['login_file'] in ('appeared', 'changed')
        return api.watch_look(h)

    def lose_login(self):
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.rig.set_live(json.dumps(dict(MCP, discoveryState={'made-up': 1}), separators=(',', ':')))


class TheFileWrite(Base):
    def test_it_returns_the_stamp_of_the_file_it_put_there(self):
        self.assertIsNone(ccfiles.bump_credentials_file(self.rig.env, self.rig.home, '{"new": 1}'))
        self.plain_file('{"old": 1}')
        got = ccfiles.bump_credentials_file(self.rig.env, self.rig.home, '{"new": 1}')
        self.assertEqual(got, self.stamp())
        with open(self.path) as f:
            self.assertEqual(f.read(), '{"new": 1}')

    def test_it_returns_its_own_stamp_when_another_writer_replaces_the_file_at_once(self):
        self.plain_file('{"old": 1}')
        self.another_writer_after_our_rename('{"another": "process"}')
        got = ccfiles.bump_credentials_file(self.rig.env, self.rig.home, '{"new": 1}')
        self.assertEqual(got, self.ours[0])
        self.assertNotEqual(got, self.stamp())

    def test_write_live_hands_the_stamp_on(self):
        self.plain_file('{"old": 1}')
        wrote = {}
        self.assertTrue(live.write_live(self.ctx, self.b_text, wrote=wrote).ok)
        self.assertEqual(wrote['file'], self.stamp())
        wrote = {}
        os.unlink(self.path)
        self.assertTrue(live.write_live(self.ctx, self.b_text, wrote=wrote).ok)
        self.assertIsNone(wrote['file'])                            # no file: nothing was rewritten


class ThePutBackNotesItsOwnStamp(Base):
    def test_a_put_back_beside_a_file_another_writer_replaces_at_once(self):
        self.plain_file(json.dumps(MCP))                            # an old fallback file, no login in it
        self.lose_login()
        self.assertEqual(self.watch(0.2, keychain=True).action, 'wait')
        self.another_writer_after_our_rename(json.dumps(dict(MCP, discoveryState={'theirs': 1})))
        self.assertEqual(self.watch(putback.PUT_BACK_AFTER_S).action, 'put_back')
        written = putback.load_record(self.ctx)['written']
        self.assertEqual(tuple(written['file']), self.ours[0])
        self.assertNotEqual(tuple(written['file']), self.stamp())
        # Claude Code's fallback then deletes the item: the other writer's file is proof, not our rewrite
        self.rig.sec.drop(self.rig.service, self.rig.account)
        first = self.watch(2.0, keychain=True)
        self.assertEqual((first.action, first.next_s), ('wait', putback.FAULT_AFTER_S))

    def test_a_promote_never_takes_a_file_another_writer_put_there_with_the_same_bytes_for_its_own(self):
        self.plain_file(self.b_text)                                # the login only in the plain file
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.assertEqual(self.watch(0.2, keychain=True).action, 'wait')
        real = putback.hold_adopt

        def adopt(ctx, path, stamp):
            # the promote's write landed with the file as it was (stamp); this moment another process replaces it
            self.ours.append(tuple(stamp))
            other = self.path + '.other'
            with open(other, 'w') as f:
                f.write(self.b_text)
            os.chmod(other, 0o600)
            t = self.clock.wall()
            os.utime(other, (t, t))
            REAL_REPLACE(other, self.path)
            return real(ctx, path, stamp)
        with mock.patch.object(putback, 'hold_adopt', adopt):
            self.assertEqual(self.watch(putback.PUT_BACK_AFTER_S).action, putback.PROMOTE)
        self.assertTrue(os.path.exists(self.path))                  # a fresh fallback write of Claude Code's stays
        self.assertNotEqual(self.stamp(), self.ours[0])
        theirs = self.stamp()
        self.watch(putback.HOLD_TIDY_S + 1.0, keychain=True)        # and no later look takes it for one of ours
        self.assertEqual(self.stamp(), theirs)


class TheSwitchNotesItsOwnStamp(Base):
    def test_the_switchs_record_keeps_the_stamp_of_the_file_its_write_put_there(self):
        self.plain_file(self.b_text)
        self.clock.advance(5.0)
        self.another_writer_after_our_rename(json.dumps(dict(MCP, discoveryState={'theirs': 1})))
        self.assertTrue(switcher.switch(self.ctx, 'uuid-a', source='auto', kind='forced').ok)
        roster.forget_live()
        rec = vault.load(self.ctx)['switches'][-1]
        self.assertEqual(rec.get('file'), list(self.ours[0]))
        self.assertNotEqual(rec.get('file'), list(self.stamp()))


if __name__ == '__main__':
    unittest.main()
