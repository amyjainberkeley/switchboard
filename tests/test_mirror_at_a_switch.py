import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# A switch keeps the standing copy in step. Under Claude Code's save lock it writes the new login into the item, then
# the copy in Claude Code's plain login file, and only then oauthAccount in Claude Code's settings: when that name
# changes, the VS Code extension asks every panel's `claude auth status` at once, and each of those reads finds the
# new login in the item or, when its Keychain read gives nothing, in the file. The settings file is replaced in one
# rename and always holds oauthAccount (a moment without it reads as "signed out" to the extension). A switch that
# is rolled back puts the copy back with the item.
# The account engine on its stand-ins (fakes_accounts) with a fake clock; made-up *.example accounts.
import json
import os
import unittest
from unittest import mock

from hold_fakes import Base
from switchboard.accounts import ccfiles, live, putback, switcher


class Mirror(Base):
    MIRROR = True

    def account_of(self, uuid):
        return [k for k in self.rig.doc()['accounts'] if k.startswith(uuid)][0]

    def watched_switch(self, target):
        """switcher.switch, with each write of Claude Code's item, of its plain login file and of its settings noted in
        order, and the settings on disk read at each of them. Returns (steps, settings had oauthAccount at each)."""
        steps, named = [], []
        cfg = self.rig.config_path()
        kc_write = self.rig.kc.write
        real_replace, real_link, real_atomic = os.replace, os.link, ccfiles.atomic_write

        def on_disk():
            try:
                with open(cfg) as f:
                    named.append('oauthAccount' in json.load(f))
            except (OSError, ValueError):
                named.append(False)

        def write(service, account, value, **kw):
            done = kc_write(service, account, value, **kw)
            if service == self.rig.service and done.ok:
                steps.append('item')
                on_disk()
            return done

        def replace(src, dst, *a, **kw):
            out = real_replace(src, dst, *a, **kw)
            if dst == self.cred:
                steps.append('file')
                on_disk()
            elif os.path.abspath(dst) == os.path.abspath(cfg):
                steps.append('settings')
                on_disk()
            return out

        def link(src, dst, *a, **kw):
            out = real_link(src, dst, *a, **kw)
            if dst == self.cred:
                steps.append('file')
                on_disk()
            return out

        def atomic(path, data, *a, **kw):
            if os.path.abspath(path) == os.path.abspath(cfg):
                self.assertIn('oauthAccount', json.loads(data.decode('utf-8')))
            return real_atomic(path, data, *a, **kw)
        with mock.patch.object(self.rig.kc, 'write', write), mock.patch.object(os, 'replace', replace), \
                mock.patch.object(putback, '_link', link), mock.patch.object(ccfiles, 'atomic_write', atomic):
            res = switcher.switch(self.ctx, target)
        self.assertTrue(res.ok, res.reason)
        return steps, named


class ASwitch(Mirror):
    def test_writes_the_item_then_the_copy_then_the_settings(self):
        self.look(keychain=True)
        self.assertTrue(self.ours())
        a = self.account_of('uuid-a')
        steps, named = self.watched_switch(a)
        self.assertEqual(steps[:3], ['item', 'file', 'settings'])
        self.assertTrue(all(named), (steps, named))
        self.assertEqual(self.file_text(), self.rig.live_text())
        self.assertEqual(live.parse(self.file_text()).fingerprint, live.parse(self.rig.vault(a)).fingerprint)
        self.assertTrue(self.ours())
        self.assertEqual(os.stat(self.cred).st_mode & 0o777, 0o600)
        self.assertEqual(self.rig.config()['oauthAccount']['accountUuid'], 'uuid-a')

    def test_there_is_never_a_moment_without_the_copy(self):
        # live.write_live leaves a copy of ours alone (putback.hold_drop) and the switch replaces it in one rename
        self.look(keychain=True)
        unlinked = []
        real_unlink = os.unlink

        def unlink(path, *a, **kw):
            unlinked.append(path) if path == self.cred else None
            return real_unlink(path, *a, **kw)
        with mock.patch.object(os, 'unlink', unlink):
            res = switcher.switch(self.ctx, self.account_of('uuid-a'))
        self.assertTrue(res.ok, res.reason)
        self.assertEqual(unlinked, [])
        self.assertEqual(self.file_text(), self.rig.live_text())

    def test_with_no_file_the_switch_makes_the_copy_before_the_settings(self):
        self.assertIsNone(self.file_text())
        steps, named = self.watched_switch(self.account_of('uuid-a'))
        self.assertEqual(steps[:3], ['item', 'file', 'settings'])
        self.assertTrue(all(named))
        self.assertEqual(self.file_text(), self.rig.live_text())
        self.assertTrue(self.ours())

    def test_a_file_of_claude_codes_own_is_rewritten_and_kept_as_the_copy(self):
        self.cc_writes(self.rig.live_text(), age=5.0)
        steps, _ = self.watched_switch(self.account_of('uuid-a'))
        self.assertEqual(steps[:3], ['item', 'file', 'settings'])
        self.assertEqual(self.file_text(), self.rig.live_text())
        self.assertTrue(self.ours())

    def test_the_switch_record_knows_the_copy_so_it_is_never_taken_for_claude_codes_write(self):
        self.look(keychain=True)
        self.watched_switch(self.account_of('uuid-a'))
        st = os.stat(self.cred)
        last = self.rig.doc()['switches'][-1]
        self.assertEqual(last.get('file'), [st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size])
        out = self.hold('changed')
        self.assertEqual(out.get('ours'), True)

    def test_and_back_again(self):
        self.look(keychain=True)
        self.watched_switch(self.account_of('uuid-a'))
        steps, named = self.watched_switch(self.account_of('uuid-b'))
        self.assertEqual(steps[:3], ['item', 'file', 'settings'])
        self.assertTrue(all(named))
        self.assertEqual(self.file_text(), self.rig.live_text())
        self.look(after=1.0, keychain=True, settings=True)
        self.assertEqual(self.file_text(), self.rig.live_text())


class ACopyThatFails(Mirror):
    def test_never_stops_the_switch(self):
        self.look(keychain=True)

        def boom(ctx, text):
            raise OSError('made-up failure')
        with mock.patch.object(putback, 'mirror_put', boom):
            res = switcher.switch(self.ctx, self.account_of('uuid-a'))
        self.assertTrue(res.ok, res.reason)
        self.assertEqual(self.rig.config()['oauthAccount']['accountUuid'], 'uuid-a')
        # the settings name A now: a copy of B's login must not stay beside them
        self.assertIsNone(self.file_text())
        self.look(after=1.0, keychain=True, settings=True)
        self.assertEqual(self.file_text(), self.rig.live_text())    # the next look brings the copy up to it


class ARolledBackSwitch(Mirror):
    def test_puts_the_copy_back_with_the_item(self):
        self.look(keychain=True)
        before = self.rig.live_text()

        def refuse(*a, **kw):
            raise ccfiles.CCFileError('changed', "Claude Code's settings file did not keep the account.")
        with mock.patch.object(ccfiles, 'write_identity', refuse):
            res = switcher.switch(self.ctx, self.account_of('uuid-a'))
        self.assertFalse(res.ok)
        self.assertTrue(res.rolled_back)
        self.assertEqual(self.rig.live_text(), before)
        self.assertEqual(self.file_text(), before)
        self.assertTrue(self.ours())


class WithTheSettingOff(Base):
    def test_a_switch_makes_no_file(self):
        steps_before = self.file_text()
        self.assertIsNone(steps_before)
        res = switcher.switch(self.ctx, [k for k in self.rig.doc()['accounts'] if k.startswith('uuid-a')][0])
        self.assertTrue(res.ok)
        self.assertIsNone(self.file_text())


if __name__ == '__main__':
    unittest.main()
