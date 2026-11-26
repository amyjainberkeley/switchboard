import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# `switchboard export FILE`: the accounts on this Mac in one protected file. It holds each account's saved login,
# whole email, short name, place and rule, and no more; asks for the passphrase twice, or takes it from an owner-only
# file or a descriptor; writes the file owner-only from its first byte, whole; refuses a folder a sync service copies
# off the Mac; writes unprotected only to standard output; reads under the switch's locks and changes nothing.
# The engine runs on tests/fakes_accounts.Rig (a made-up Keychain and Anthropic); the file goes through the real
# /usr/bin/openssl, which reads and writes only pipes here.
import io
import json
import os
import shutil
import stat
import sys
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import __main__ as cli
from switchboard import backup, core, keys as K, lanes
from switchboard.accounts import autoloop, context, roster, vault
from fakes_accounts import Rig
from test_keys import FakeKeychain as FakeKeysKeychain

PASS = 'a passphrase for tests'
KEY = 'sk-ant-' + 'api03-made-up-key-for-export-0001'


class Out(io.StringIO):
    """Standard output with a byte side, as sys.stdout has."""

    def __init__(self):
        super().__init__()
        self.buffer = io.BytesIO()


def read(path, mode='rb'):
    with open(path, mode) as f:
        return f.read()


def bytes_of(out):
    return out.buffer.getvalue() + out.getvalue().encode('utf-8')


class Mac(object):
    """One made-up Mac: a Rig, its accounts, a folder to write in."""

    def __init__(self, test, server=None):
        self.rig = Rig()
        test.addCleanup(self.rig.close)
        if server is not None:
            self.rig.server = server
            self.rig.ctx.http = server
        self.folder = os.path.join(self.rig.root, 'out')
        os.makedirs(self.folder)

    def run(self, verb, *args, **kw):
        """(exit code, standard output text, its bytes, standard error) of `switchboard <verb> ...`."""
        out, err = Out(), io.StringIO()
        fn = backup.export_main if verb == 'export' else backup.import_main
        with context.use(self.rig.ctx):
            code = fn(list(args), ctx=self.rig.ctx, out=out, err=err, **kw)
        return code, out.getvalue(), bytes_of(out), err.getvalue()


def pass_file(folder, text=PASS, mode=0o600):
    path = os.path.join(folder, 'pass-%d.txt' % len(os.listdir(folder)))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    os.write(fd, (text + '\n').encode('utf-8'))
    os.close(fd)
    os.chmod(path, mode)
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self.mac = Mac(self)
        self.rig = self.mac.rig
        self.ids = []
        for uuid, email, org in (('uuid-a', 'maya@northwind.example', 'org-n'), ('uuid-b', 'maya@fernhill.example',
                                                                                 'org-f'),
                                 ('uuid-c', 'sam@fernhill.example', 'org-f')):
            self.rig.keep(uuid, email, org)
            self.ids.append(vault.account_key(self.rig.doc(), uuid, org))
        with context.use(self.rig.ctx):
            roster.set_alias(self.rig.ctx, self.ids[0], 'work')
            autoloop.save_settings(rule=(self.ids[1], 'last'))
        self.addCleanup(lambda: core.update_setting(autoloop.POLICY_KEY, lambda old: core.UNSET))
        self.pf = pass_file(self.mac.folder)

    def export(self, *args):
        return self.mac.run('export', *args)

    def opened(self, data, passphrase=PASS):
        return json.loads(backup.open_sealed(data, passphrase))


class WhatTheFileHolds(Base):
    def test_every_account_with_its_login_email_short_name_place_and_rule(self):
        dest = os.path.join(self.mac.folder, 'accounts.sbx')
        code, out, _, err = self.export(dest, '--passphrase-file', self.pf)
        self.assertEqual(code, 0, err)
        doc = self.opened(read(dest))
        self.assertEqual(doc['schema'], 1)
        self.assertRegex(doc['made_at'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')
        got = dict((a['email'], a) for a in doc['accounts'])
        self.assertEqual([a['email'] for a in doc['accounts']],
                         ['maya@northwind.example', 'maya@fernhill.example', 'sam@fernhill.example'])
        self.assertEqual([a['place'] for a in doc['accounts']], [1, 2, 3])
        self.assertEqual(got['maya@northwind.example']['short_name'], 'work')
        self.assertEqual(got['maya@fernhill.example']['rule'], 'last')
        self.assertIsNone(got['sam@fernhill.example']['rule'])
        for aid, a in zip(self.ids, doc['accounts']):
            self.assertEqual(a['login'], self.rig.vault(aid))
            self.assertEqual(a['settings']['emailAddress'], a['email'])
        self.assertNotIn('keys', doc)
        for word in ('usage', 'readings', 'history', 'folder', 'settings.json'):
            self.assertNotIn(word, json.dumps(doc).replace('"settings":', ''))
        self.assertIn('Wrote 3 accounts to %s, protected with your passphrase.' % dest, out)

    def test_the_line_after_says_to_delete_the_file_and_that_switchboard_never_does(self):
        dest = os.path.join(self.mac.folder, 'accounts.sbx')
        code, out, _, err = self.export(dest, '--passphrase-file', self.pf)
        self.assertEqual(code, 0, err)
        self.assertIn('It holds working logins: delete it once the other Mac has taken it. Switchboard never deletes '
                      'it.', out)
        self.assertTrue(os.path.exists(dest))

    def test_some_accounts_by_name(self):
        dest = os.path.join(self.mac.folder, 'two.sbx')
        code, _, _, err = self.export(dest, '--account', 'work', '--account', 'sam@fernhill.example',
                                      '--account', '1', '--passphrase-file', self.pf)
        self.assertEqual(code, 0, err)
        doc = self.opened(read(dest))
        self.assertEqual([a['email'] for a in doc['accounts']], ['maya@northwind.example', 'sam@fernhill.example'])

    def test_an_account_not_here_is_refused_and_nothing_is_written(self):
        dest = os.path.join(self.mac.folder, 'none.sbx')
        with self.assertRaises(Exception) as got:
            self.export(dest, '--account', 'nobody@nowhere.example', '--passphrase-file', self.pf)
        self.assertIn('nobody@nowhere.example', str(got.exception))
        self.assertFalse(os.path.exists(dest))

    def test_keys_only_with_with_keys(self):
        kc = FakeKeysKeychain()
        lanes.KEYS['store'] = K.Keys(runner=kc, path=os.path.join(self.mac.folder, 'lanes.json'))
        self.addCleanup(lanes.KEYS.update, store=None)
        lanes.store().add('spare', KEY)
        plain = os.path.join(self.mac.folder, 'no-keys.sbx')
        self.assertEqual(self.export(plain, '--passphrase-file', self.pf)[0], 0)
        self.assertNotIn('keys', self.opened(read(plain)))
        dest = os.path.join(self.mac.folder, 'keys.sbx')
        code, out, _, err = self.export(dest, '--with-keys', '--passphrase-file', self.pf)
        self.assertEqual(code, 0, err)
        keys = self.opened(read(dest))['keys']
        self.assertEqual([(k['name'], k['kind'], k['key']) for k in keys], [('spare', 'anthropic', KEY)])
        self.assertIn('Wrote 3 accounts and 1 key', out)
        self.assertNotIn(KEY, out + err)

    def test_an_account_that_needs_a_fresh_sign_in_is_left_out_and_said(self):
        with vault.changing(self.rig.ctx) as doc:
            doc['accounts'][self.ids[2]]['strike'] = {'fp': doc['accounts'][self.ids[2]]['fp'], 'at': 1}
        dest = os.path.join(self.mac.folder, 'two.sbx')
        code, _, _, err = self.export(dest, '--passphrase-file', self.pf)
        self.assertEqual(code, 0, err)
        self.assertIn('Left out sam@fernhill.example: it needs a fresh sign-in here.', err)
        self.assertEqual(len(self.opened(read(dest))['accounts']), 2)


class HowItIsProtected(Base):
    def test_owner_only_from_its_first_byte_and_written_whole(self):
        dest = os.path.join(self.mac.folder, 'accounts.sbx')
        seen = []
        real_write = os.write

        def write(fd, data):
            st = os.fstat(fd)
            if stat.S_ISREG(st.st_mode):
                seen.append(stat.S_IMODE(st.st_mode))
            return real_write(fd, data)
        old_umask = os.umask(0)                     # the loosest umask a shell can have: the mode still holds
        try:
            with mock.patch.object(core.os, 'write', write):
                code, _, _, err = self.export(dest, '--passphrase-file', self.pf)
        finally:
            os.umask(old_umask)
        self.assertEqual(code, 0, err)
        self.assertTrue(seen)
        self.assertEqual(set(seen), {0o600})
        self.assertEqual(stat.S_IMODE(os.stat(dest).st_mode), 0o600)
        self.assertEqual([n for n in os.listdir(self.mac.folder) if n.endswith('.tmp')], [])

    def test_asked_twice_at_the_terminal(self):
        dest = os.path.join(self.mac.folder, 'asked.sbx')
        asked = []

        def ask(prompt):
            asked.append(prompt)
            return PASS
        with mock.patch.object(backup, '_ask_hidden', ask):
            code, _, _, err = self.export(dest)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(asked), 2)
        self.assertEqual(len(self.opened(read(dest))['accounts']), 3)

    def test_two_passphrases_that_differ_or_one_too_short_write_nothing(self):
        dest = os.path.join(self.mac.folder, 'nope.sbx')
        answers = iter([PASS, PASS + 'x'])
        with mock.patch.object(backup, '_ask_hidden', lambda prompt: next(answers)):
            with self.assertRaises(core.SwitchError) as got:
                self.export(dest)
        self.assertIn('not the same', str(got.exception))
        with mock.patch.object(backup, '_ask_hidden', lambda prompt: 'short'):
            with self.assertRaises(core.SwitchError) as got:
                self.export(dest)
        self.assertIn('at least 8 characters', str(got.exception))
        self.assertFalse(os.path.exists(dest))

    def test_a_passphrase_file_others_can_read_is_refused(self):
        for mode in (0o644, 0o640, 0o604):
            loose = pass_file(self.mac.folder, mode=mode)
            dest = os.path.join(self.mac.folder, 'loose.sbx')
            with self.subTest(mode=oct(mode)), self.assertRaises(core.SwitchError) as got:
                self.export(dest, '--passphrase-file', loose)
            self.assertIn('can be read by others', str(got.exception))
            self.assertFalse(os.path.exists(dest))

    def test_a_passphrase_file_that_is_a_link_is_refused(self):
        link = os.path.join(self.mac.folder, 'link.txt')
        os.symlink(self.pf, link)
        with self.assertRaises(core.SwitchError):
            self.export(os.path.join(self.mac.folder, 'x.sbx'), '--passphrase-file', link)

    def test_a_passphrase_on_a_descriptor(self):
        r, w = os.pipe()
        os.write(w, (PASS + '\n').encode())
        os.close(w)
        dest = os.path.join(self.mac.folder, 'fd.sbx')
        try:
            code, _, _, err = self.export(dest, '--passphrase-fd', str(r))
        finally:
            os.close(r)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.opened(read(dest))['accounts']), 3)

    def test_never_a_passphrase_argument_or_variable(self):
        dest = os.path.join(self.mac.folder, 'x.sbx')
        for words in (['--passphrase', PASS], ['--password', PASS], ['-p', PASS]):
            with self.subTest(words=words):
                code, out, _, err = self.export(dest, *words)
                self.assertEqual(code, 2)
                self.assertIn('Unknown word: %s.' % words[0], err)
        with mock.patch.dict(os.environ, {'SWITCHBOARD_PASSPHRASE': PASS}), \
                mock.patch.object(backup, '_ask_hidden', side_effect=core.SwitchError('asked')):
            with self.assertRaises(core.SwitchError) as got:
                self.export(dest)
        self.assertEqual(str(got.exception), 'asked')
        self.assertFalse(os.path.exists(dest))

    def test_standard_output_is_protected_too_and_the_words_go_to_standard_error(self):
        code, out, raw, err = self.export('-', '--passphrase-file', self.pf)
        self.assertEqual((code, out), (0, ''))
        self.assertTrue(raw.startswith(b'switchboard-accounts\n'), raw[:40])
        self.assertEqual(len(self.opened(raw)['accounts']), 3)
        self.assertIn('Wrote 3 accounts to standard output', err)
        self.assertIn('delete it once the other Mac has taken it', err)

    def test_no_secret_in_any_output(self):
        dest = os.path.join(self.mac.folder, 'x.sbx')
        code, out, _, err = self.export(dest, '--passphrase-file', self.pf)
        self.assertEqual(code, 0, err)
        data = read(dest)
        for aid in self.ids:
            blob = json.loads(self.rig.vault(aid))['claudeAiOauth']
            for secret in (blob['accessToken'], blob['refreshToken']):
                self.assertNotIn(secret, out + err)
                self.assertNotIn(secret.encode(), data)
        self.assertNotIn(PASS, out + err)
        for line in self.rig.logs:
            self.assertNotIn(PASS, line)

    def test_a_file_already_there_is_never_written_over(self):
        dest = os.path.join(self.mac.folder, 'there.sbx')
        with open(dest, 'w') as f:
            f.write('mine')
        with self.assertRaises(core.SwitchError) as got:
            self.export(dest, '--passphrase-file', self.pf)
        self.assertIn('is there already', str(got.exception))
        self.assertEqual(read(dest, 'r'), 'mine')


class Unprotected(Base):
    def test_plain_only_to_standard_output_with_one_warning_line(self):
        code, out, raw, err = self.export('-', '--plain')
        self.assertEqual(code, 0)
        doc = json.loads(raw)
        self.assertEqual(len(doc['accounts']), 3)
        self.assertEqual(err.count('\n'), 1, err)
        self.assertTrue(err.startswith('Unprotected:'), err)

    def test_plain_to_a_file_is_refused(self):
        dest = os.path.join(self.mac.folder, 'plain.json')
        code, out, _, err = self.export(dest, '--plain')
        self.assertEqual(code, 2)
        self.assertIn('--plain writes only to standard output', err)
        self.assertFalse(os.path.exists(dest))


class SyncFolders(Base):
    def test_folders_a_sync_service_copies_are_told_by_the_path(self):
        home = os.path.join(self.rig.root, 'home-of-someone')
        for rel, said in (('Library/Mobile Documents/com~apple~CloudDocs/x', 'iCloud Drive'),
                          ('Library/CloudStorage/Dropbox/x', 'Dropbox'),
                          ('Library/CloudStorage/GoogleDrive-someone@mail.example/My Drive', 'Google Drive'),
                          ('Library/CloudStorage/OneDrive-Personal', 'OneDrive'),
                          ('Dropbox/backups', 'Dropbox'), ('Google Drive', 'Google Drive'),
                          ('OneDrive - Northwind', 'OneDrive'),
                          ('Desktop', None), ('Documents/x', None), ('Downloads', None), ('Library', None)):
            folder = os.path.join(home, rel)
            os.makedirs(folder, exist_ok=True)
            with self.subTest(rel=rel):
                self.assertEqual(backup.synced_folder(os.path.join(folder, 'f.sbx'), home=home), said)
        self.assertIsNone(backup.synced_folder('/tmp/f.sbx', home=home))

    def test_desktop_and_documents_count_when_icloud_copies_them(self):
        home = os.path.join(self.rig.root, 'home-with-desktop-in-icloud')
        os.makedirs(os.path.join(home, 'Library', 'Mobile Documents', 'com~apple~CloudDocs', 'Desktop'))
        os.makedirs(os.path.join(home, 'Desktop'))
        os.makedirs(os.path.join(home, 'Documents'))
        self.assertEqual(backup.synced_folder(os.path.join(home, 'Desktop', 'f.sbx'), home=home), 'iCloud Drive')
        self.assertIsNone(backup.synced_folder(os.path.join(home, 'Documents', 'f.sbx'), home=home))

    def test_refused_inside_one_and_written_with_anyway(self):
        folder = os.path.join(core.home(), 'Library', 'Mobile Documents', 'com~apple~CloudDocs')
        os.makedirs(folder, exist_ok=True)
        self.addCleanup(shutil.rmtree, os.path.join(core.home(), 'Library'), True)
        dest = os.path.join(folder, 'accounts.sbx')
        with self.assertRaises(core.SwitchError) as got:
            self.export(dest, '--passphrase-file', self.pf)
        self.assertIn('iCloud Drive copies off this Mac', str(got.exception))
        self.assertFalse(os.path.exists(dest))
        code, _, _, err = self.export(dest, '--anyway', '--passphrase-file', self.pf)
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.exists(dest))


class ChangesNothing(Base):
    def test_no_keychain_write_and_the_account_list_as_it_was(self):
        before = read(vault.store_path(self.rig.ctx))
        writes = self.rig.kc.count('write')
        code, _, _, err = self.export(os.path.join(self.mac.folder, 'x.sbx'), '--passphrase-file', self.pf)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.rig.kc.count('write'), writes)
        self.assertEqual(read(vault.store_path(self.rig.ctx)), before)

    def test_it_waits_for_a_switch_under_way(self):
        held = threading.Event()
        released = []

        def switch_under_way():
            with vault.switch_lock(self.rig.ctx, timeout=5.0):
                held.set()
                time.sleep(1.0)
                released.append(time.monotonic())
        t = threading.Thread(target=switch_under_way)
        t.start()
        held.wait(5)
        code, _, _, err = self.export(os.path.join(self.mac.folder, 'x.sbx'), '--passphrase-file', self.pf)
        done = time.monotonic()
        t.join()
        self.assertEqual(code, 0, err)
        self.assertGreaterEqual(done, released[0])


class TheCommand(Base):
    def test_through_main_and_its_help(self):
        dest = os.path.join(self.mac.folder, 'main.sbx')
        out = io.StringIO()
        with context.use(self.rig.ctx), mock.patch.object(sys, 'stdout', out):
            code = cli.main(['export', dest, '--passphrase-file', self.pf])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(dest))
        with mock.patch.object(sys, 'stdout', io.StringIO()) as said:
            self.assertEqual(cli.main(['export', '--help']), 0)
        self.assertIn('switchboard export FILE', said.getvalue())
        with mock.patch.object(sys, 'stdout', io.StringIO()) as said:
            self.assertEqual(cli.main(['export']), 2)
        self.assertIn('switchboard export FILE', said.getvalue())

    def test_an_unknown_word_is_never_swallowed(self):
        code, _, _, err = self.export(os.path.join(self.mac.folder, 'x.sbx'), '--with-key')
        self.assertEqual(code, 2)
        self.assertIn('Unknown word: --with-key.', err)


if __name__ == '__main__':
    unittest.main()
