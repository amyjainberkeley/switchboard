import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# A switch rewrites Claude Code's plain login file when one exists (so a running Claude Code notices the new login at
# once), which gives that file a fresh write time. The put-back takes a fresh write of that file for proof that a save
# of Claude Code's failed, so each switch's record in the accounts file carries the file's stamp right after the
# switch's own write, taken while Claude Code's save lock is still held: 'file' = [device, inode, mtime ns, size], the
# four numbers putback._stamp gives; no key at all when there is no file. Stand-ins only (fakes_accounts), a fake
# clock, made-up *.example accounts.
import os
import shutil
import tempfile
import time
import unittest

from fakes_autoswitch import FakeClock
from fakes_accounts import Rig
from switchboard.accounts import context, facts, putback, switcher


class Cut(BaseException):
    """The process stops right here (as a kill would), leaving the switch's journal behind."""


class Base(unittest.TestCase):
    def setUp(self):
        self._old_home = os.environ.get('SWITCHBOARD_HOME')
        self.tmp = tempfile.mkdtemp(prefix='switch-file-stamp-')
        os.environ['SWITCHBOARD_HOME'] = os.path.join(self.tmp, 'data')
        os.makedirs(os.environ['SWITCHBOARD_HOME'])
        self.clock = FakeClock(wall=time.time(), mono=time.monotonic())
        self.rig = Rig(wall=self.clock.wall, mono=self.clock.mono, sleep=self.clock.sleep)
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        putback.reset()
        self.a_text = self.rig.keep('uuid-a', 'ana@work.example', 'org-a')
        self.clock.advance(1.0)
        self.b_text = self.rig.keep('uuid-b', 'ana@home.example', 'org-b')          # Claude Code is on B
        self.path = facts.credentials_file(self.rig.env, self.rig.home)

    def tearDown(self):
        switcher._HOOK = None
        self.use.__exit__(None, None, None)
        putback.reset()
        self.rig.close()
        if self._old_home is None:
            os.environ.pop('SWITCHBOARD_HOME', None)
        else:
            os.environ['SWITCHBOARD_HOME'] = self._old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def plain_file(self, text):
        with open(self.path, 'w') as f:
            f.write(text)
        os.chmod(self.path, 0o600)
        t = time.time() - 3600                              # an old file: its stamp moves only by a rewrite
        os.utime(self.path, (t, t))

    def stamp(self):
        st = os.stat(self.path)
        return [st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size]

    def last(self):
        return self.rig.doc()['switches'][-1]

    def assert_stamp(self, rec):
        self.assertIn('file', rec)
        self.assertEqual(rec['file'], self.stamp())
        self.assertEqual(rec['file'], list(putback._stamp(self.path)))
        self.assertEqual(len(rec['file']), 4)
        self.assertTrue(all(type(n) is int for n in rec['file']), rec['file'])

    def cut_at(self, step):
        def hook(name):
            if name == step:
                raise Cut(name)
        switcher._HOOK = hook


class TheSwitchsRecord(Base):
    def test_a_switch_beside_a_plain_file_notes_its_stamp_after_the_switchs_rewrite(self):
        self.plain_file(self.b_text)
        old = self.stamp()
        res = switcher.switch(self.ctx, 'uuid-a')
        self.assertTrue(res.ok, res.note)
        self.assertNotEqual(self.stamp(), old)              # the switch rewrote it
        rec = self.last()
        self.assertEqual((rec['from'], rec['to']), ('uuid-b', 'uuid-a'))
        self.assert_stamp(rec)

    def test_without_a_plain_file_the_record_has_no_such_key(self):
        res = switcher.switch(self.ctx, 'uuid-a')
        self.assertTrue(res.ok, res.note)
        self.assertFalse(os.path.exists(self.path))
        self.assertNotIn('file', self.last())

    def test_the_stamp_is_the_one_right_after_the_write_not_a_later_rewrite(self):
        self.plain_file(self.b_text)
        seen = {}

        def hook(name):
            if name == 'credential_written':
                seen['after_write'] = self.stamp()
            elif name == 'config_lock':                     # Claude Code writes the file again a moment later
                os.unlink(self.path)
                with open(self.path, 'w') as f:
                    f.write(self.b_text + ' ')
        switcher._HOOK = hook
        res = switcher.switch(self.ctx, 'uuid-a')
        switcher._HOOK = None
        self.assertTrue(res.ok, res.note)
        rec = self.last()
        self.assertEqual(rec['file'], seen['after_write'])
        self.assertNotEqual(rec['file'], self.stamp())

    def test_every_switch_notes_its_own(self):
        self.plain_file(self.b_text)
        self.assertTrue(switcher.switch(self.ctx, 'uuid-a').ok)
        first = self.last()['file']
        self.clock.advance(5.0)
        self.assertTrue(switcher.switch(self.ctx, 'uuid-b').ok)
        self.assert_stamp(self.last())
        self.assertNotEqual(self.last()['file'], first)


class ASwitchFinishedFromItsJournal(Base):
    def test_a_cut_off_switch_beside_a_plain_file_is_finished_with_its_stamp(self):
        self.plain_file(self.b_text)
        self.cut_at('config_lock')
        with self.assertRaises(Cut):
            switcher.switch(self.ctx, 'uuid-a')
        switcher._HOOK = None
        after_write = self.stamp()
        self.assertTrue(os.path.exists(switcher.journal_path(self.ctx)))
        note = switcher.resolve_journal(self.ctx)
        self.assertEqual(note, 'Repaired the cut-off switch: Claude Code is on ana@work.example')
        rec = self.last()
        self.assertEqual((rec['to'], rec['source']), ('uuid-a', 'repair'))
        self.assert_stamp(rec)
        self.assertEqual(rec['file'], after_write)

    def test_a_cut_off_switch_without_a_plain_file_is_finished_without_the_key(self):
        self.cut_at('config_lock')
        with self.assertRaises(Cut):
            switcher.switch(self.ctx, 'uuid-a')
        switcher._HOOK = None
        self.assertTrue(switcher.resolve_journal(self.ctx))
        rec = self.last()
        self.assertEqual((rec['to'], rec['source']), ('uuid-a', 'repair'))
        self.assertNotIn('file', rec)


if __name__ == '__main__':
    unittest.main()
