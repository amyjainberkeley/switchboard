import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# `switchboard import FILE`: it opens the file, shows what it holds and what it would do with each account, asks, then
# keeps each account like any other and checks that it works. It never puts an older login over a newer one, with
# any flag; never touches the login Claude Code is on; and a wrong passphrase, a changed byte, a cut file or a file
# from a newer version each has its own line and code, with nothing kept. Two made-up Macs (tests/fakes_accounts.Rig)
# share one made-up Anthropic.
import io
import hashlib
import itertools
import json
import os
import random
import sys
import threading
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import __main__ as cli
from switchboard import backup, core, keys as K, lanes
from switchboard.accounts import autoloop, context, live, roster, tokens, vault
from switchboard.accounts.api import ABSENT, PRESENT, Read
from fakes_accounts import login_text
from test_backup_export import Mac, PASS, pass_file, read
from test_keys import FakeKeychain as FakeKeysKeychain

KEY = 'sk-ant-' + 'api03-made-up-key-for-import-0002'
A, B, C = 'maya@northwind.example', 'maya@fernhill.example', 'sam@fernhill.example'


class Base(unittest.TestCase):
    def setUp(self):
        self.one = Mac(self)
        self.two = Mac(self, server=self.one.rig.server)
        self.ids = {}
        for uuid, email, org in (('uuid-a', A, 'org-n'), ('uuid-b', B, 'org-f'), ('uuid-c', C, 'org-f')):
            self.one.rig.keep(uuid, email, org)
            self.ids[email] = vault.account_key(self.one.rig.doc(), uuid, org)
        with context.use(self.one.rig.ctx):
            roster.set_alias(self.one.rig.ctx, self.ids[A], 'work')
            autoloop.save_settings(rule=(self.ids[B], 'last'))
        self.addCleanup(lambda: core.update_setting(autoloop.POLICY_KEY, lambda old: core.UNSET))
        self.two.rig.keep('uuid-z', 'zed@harbor.example', 'org-z')        # the second Mac's own account, in use
        self.pf_one, self.pf_two = pass_file(self.one.folder), pass_file(self.two.folder)
        self.n = 0

    def export(self, *args):
        self.n += 1
        dest = os.path.join(self.one.folder, 'accounts-%d.sbx' % self.n)
        code, _, _, err = self.one.run('export', dest, '--passphrase-file', self.pf_one, *args)
        self.assertEqual(code, 0, err)
        return dest

    def take(self, path, *args, **kw):
        """(exit code, standard output, standard error) of `switchboard import` on the second Mac."""
        yes = kw.pop('yes', True)
        words = [path, '--passphrase-file', self.pf_two] + list(args) + (['--yes'] if yes else [])
        code, out, _, err = self.two.run('import', *words)
        return code, out, err

    def emails_two(self):
        doc = self.two.rig.doc()
        return [doc['accounts'][aid]['email'] for aid in doc['order']]

    def renew_on_one(self, email):
        """The first Mac renews an account's saved login: a newer generation of the same sign-in."""
        res = tokens.refresh(self.one.rig.ctx, self.ids[email], when_due=False)
        self.assertEqual(res.outcome, 'refreshed', res)
        return self.one.rig.vault(self.ids[email])


class TheRoundTrip(Base):
    def test_every_account_kept_with_its_name_place_rule_and_login_and_it_works(self):
        dest = self.export()
        code, out, err = self.take(dest)
        self.assertEqual(code, 0, err + out)
        self.assertEqual(self.emails_two(), ['zed@harbor.example', A, B, C])
        doc = self.two.rig.doc()
        for email, aid in self.ids.items():
            self.assertEqual(self.two.rig.vault(aid), self.one.rig.vault(aid), email)
            self.assertEqual(doc['accounts'][aid]['identity']['emailAddress'], email)
            self.assertEqual(vault.status_of(self.two.rig.ctx, doc, aid), 'ok')
            self.assertIn('Added %s: it works.' % email, out)
        self.assertEqual(doc['accounts'][self.ids[A]].get('alias'), 'work')
        with context.use(self.two.rig.ctx):
            self.assertEqual(autoloop.settings_dict().get('rules'), {self.ids[B]: 'last'})

    def test_it_shows_what_the_file_holds_and_asks_before_keeping_anything(self):
        dest = self.export()
        before = read(vault.store_path(self.two.rig.ctx))
        with mock.patch.object(backup, '_ask_yes', return_value=False) as asked:
            code, out, err = self.take(dest, yes=False)
        self.assertEqual(code, 1)
        self.assertEqual(asked.call_args[0][0], 'Keep 3 of them? [y/N] ')
        self.assertIn('The file holds 3 accounts', out)
        for email in (A, B, C):
            self.assertRegex(out, r'  %s, login saved \w+ \d+ \d+:\d\d [AP]M, sign-in ends by \w+ \d+ \d+:\d\d [AP]M: '
                                  r'add\n' % email.replace('.', r'\.'))
        self.assertIn('Nothing was kept.', out)
        self.assertEqual(read(vault.store_path(self.two.rig.ctx)), before)
        self.assertEqual(self.emails_two(), ['zed@harbor.example'])

    def test_without_a_terminal_and_without_yes_nothing_is_kept(self):
        with mock.patch.object(backup, 'open', side_effect=OSError('no terminal'), create=True):
            self.assertFalse(backup._ask_yes('Keep 3 of them? [y/N] '))

    def test_the_same_file_twice_skips_what_is_here(self):
        dest = self.export()
        self.assertEqual(self.take(dest)[0], 0)
        code, out, err = self.take(dest)
        self.assertEqual(code, 0, err)
        self.assertEqual(out.count('skip: it is here already'), 3)
        self.assertIn('Nothing to keep.', out)

    def test_from_standard_input(self):
        dest = self.export()
        stdin = mock.Mock()
        stdin.buffer = io.BytesIO(read(dest))
        with mock.patch.object(sys, 'stdin', stdin):
            code, out, err = self.take('-')
        self.assertEqual(code, 0, err)
        self.assertEqual(self.emails_two(), ['zed@harbor.example', A, B, C])

    def test_keys_come_with_the_accounts_and_one_of_that_name_is_kept(self):
        kc = FakeKeysKeychain()
        lanes.KEYS['store'] = K.Keys(runner=kc, path=os.path.join(self.one.folder, 'lanes.json'))
        lanes.store().add('spare', KEY)
        dest = self.export('--with-keys')
        lanes.KEYS['store'] = K.Keys(runner=FakeKeysKeychain(), path=os.path.join(self.two.folder, 'lanes.json'))
        self.addCleanup(lanes.KEYS.update, store=None)
        code, out, err = self.take(dest)
        self.assertEqual(code, 0, err)
        self.assertEqual([x['name'] for x in lanes.lanes()], ['spare'])
        self.assertIn('Added the key spare (ends in 0002).', out)
        self.assertNotIn(KEY, out + err)
        code, out, _ = self.take(dest)
        self.assertIn('key spare (ends in 0002): skip: a key of that name is here', out)

    def test_through_main(self):
        dest = self.export()
        with context.use(self.two.rig.ctx), mock.patch.object(sys, 'stdout', io.StringIO()):
            code = cli.main(['import', dest, '--yes', '--passphrase-file', self.pf_two])
        self.assertEqual(code, 0)
        self.assertEqual(len(self.emails_two()), 4)


class OlderNeverOverNewer(Base):
    def test_every_order_of_two_generations_of_one_login(self):
        old_file = self.export('--account', A)
        old = self.one.rig.vault(self.ids[A])
        new = self.renew_on_one(A)
        new_file = self.export('--account', A)
        cases = {(0, 1, False): old, (0, 1, True): new, (1, 0, False): new, (1, 0, True): new}
        files, texts = (old_file, new_file), (old, new)
        for (first, second, replace), want in cases.items():
            with self.subTest(first=first, second=second, replace=replace):
                with vault.changing(self.two.rig.ctx) as doc:      # the second Mac forgets A between cases
                    doc['accounts'].pop(self.ids[A], None)
                    doc['order'] = [x for x in doc['order'] if x != self.ids[A]]
                vault.delete_logins(self.two.rig.ctx, self.ids[A])
                self.assertEqual(self.take(files[first])[0], 0)
                self.assertEqual(self.two.rig.vault(self.ids[A]), texts[first])
                code, out, err = self.take(files[second], *(['--replace'] if replace else []))
                self.assertEqual(code, 0, err)
                self.assertEqual(self.two.rig.vault(self.ids[A]), want)
                if first == 1:
                    self.assertIn('skip: the login here is newer', out)
                elif not replace:
                    self.assertIn('skip: the login here is older; --replace takes the newer one from the file', out)
                else:
                    self.assertIn('Replaced the older login of %s' % A, out)

    def test_a_rule_over_many_made_up_pairs_and_replace_never_puts_older_over_newer(self):
        rnd = random.Random(20261125)
        base_ms = 1795000000000
        seen = set()
        for _ in range(4000):
            same_lineage = rnd.random() < 0.5
            rt_a = 'sk-ant-ort01-a%d' % rnd.randrange(3)
            rt_b = rt_a if same_lineage else 'sk-ant-ort01-b%d' % rnd.randrange(3)

            def made(rt):
                exp = base_ms + rnd.choice([0, 1, 2]) * 3600000 if rnd.random() < 0.9 else None
                rx = base_ms + rnd.choice([0, 1, 2]) * 86400000 if rnd.random() < 0.8 else None
                return login_text('sk-ant-oat01-%d' % rnd.randrange(10 ** 6), rt, exp, rx)
            incoming, here = live.parse(made(rt_a)), made(rt_b)
            stored = Read(PRESENT, value=here) if rnd.random() < 0.9 else Read(ABSENT)
            verdict = live.compare(incoming, live.parse(here)) if stored.kind == PRESENT else 'absent'
            for replace, in_use in itertools.product((False, True), (False, True)):
                got = backup.decide(incoming, stored, in_use, replace)
                seen.add((verdict, replace, got))
                if in_use:
                    self.assertEqual(got, 'in_use')
                elif stored.kind == ABSENT:
                    self.assertEqual(got, backup.ADD)
                elif got == backup.REPLACE:
                    self.assertTrue(replace)
                    self.assertEqual(verdict, 'newer', (incoming.text, here))
                else:
                    self.assertNotEqual(got, backup.ADD)
        self.assertIn(('newer', True, backup.REPLACE), seen)
        self.assertIn(('older', True, 'newer_here'), seen)
        self.assertIn((None, True, 'unknown'), seen)

    def test_two_imports_at_once_keep_the_newer_login(self):
        old_file = self.export('--account', A)
        new = self.renew_on_one(A)
        new_file = self.export('--account', A)
        for order in ((old_file, new_file), (new_file, old_file)):
            with self.subTest(first=order[0] == new_file):
                with vault.changing(self.two.rig.ctx) as doc:
                    doc['accounts'].pop(self.ids[A], None)
                    doc['order'] = [x for x in doc['order'] if x != self.ids[A]]
                vault.delete_logins(self.two.rig.ctx, self.ids[A])
                go, codes = threading.Barrier(2), []

                def run(path):
                    go.wait()
                    codes.append(self.take(path, '--replace')[0])
                threads = [threading.Thread(target=run, args=(p,)) for p in order]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(120)
                self.assertEqual(codes, [0, 0])
                self.assertEqual(self.two.rig.vault(self.ids[A]), new)


class NeverTheLoginInUse(Base):
    def test_the_account_claude_code_is_on_here_is_skipped_and_its_login_untouched(self):
        self.two.rig.keep('uuid-a', A, 'org-n')            # the second Mac is on A, a sign-in of its own
        live_before = self.two.rig.live_text()
        saved_before = self.two.rig.vault(self.ids[A])
        dest = self.export('--account', A)
        self.renew_on_one(A)
        newer = self.export('--account', A)
        for path in (dest, newer):
            code, out, err = self.take(path, '--replace')
            self.assertEqual(code, 0, err)
            self.assertIn('skip: Claude Code is on it here', out)
        self.assertEqual(self.two.rig.live_text(), live_before)
        self.assertEqual(self.two.rig.vault(self.ids[A]), saved_before)


class CheckedOnceKept(Base):
    def test_a_refused_login_is_kept_and_marked_as_needing_a_sign_in(self):
        self.one.rig.set_saved_expiry(self.ids[A], 1000.0)          # its access ran out: a renewal is the check
        dest = self.export('--account', A)
        self.one.rig.server.revoke('uuid-a')
        code, out, err = self.take(dest)
        self.assertEqual(code, 0, err)
        self.assertIn('Added %s: Anthropic refused its login: it needs a fresh sign-in.' % A, out)
        doc = self.two.rig.doc()
        self.assertIn(self.ids[A], doc['accounts'])
        self.assertEqual(vault.status_of(self.two.rig.ctx, doc, self.ids[A]), 'needs_login')

    def test_a_login_with_its_access_run_out_is_renewed_once_to_see_it_works(self):
        self.one.rig.set_saved_expiry(self.ids[B], 1000.0)
        dest = self.export('--account', B)
        posts = len(self.one.rig.server.token_posts())
        code, out, err = self.take(dest)
        self.assertEqual(code, 0, err)
        self.assertIn('Added %s: it works.' % B, out)
        self.assertEqual(len(self.one.rig.server.token_posts()), posts + 1)

    def test_a_file_that_names_one_account_and_holds_anothers_login_keeps_nothing_of_it(self):
        with context.use(self.one.rig.ctx):
            payload, _ = backup.gather(self.one.rig.ctx)
        by = dict((a['email'], a) for a in payload['accounts'])
        by[B]['login'] = by[A]['login']                        # B's entry with A's login
        payload['accounts'] = [by[B]]
        dest = os.path.join(self.one.folder, 'crafted.sbx')
        with open(dest, 'wb') as f:
            f.write(backup.seal(json.dumps(payload).encode(), PASS))
        code, out, err = self.take(dest)
        self.assertEqual(code, 0, err)
        self.assertIn("Did not keep %s (its login is another account's)." % B, out)
        self.assertNotIn(self.ids[B], self.two.rig.doc()['accounts'])


class BadFiles(Base):
    def kept_nothing(self, path, code_wanted, line, *args):
        before = read(vault.store_path(self.two.rig.ctx))
        writes = self.two.rig.kc.count('write')
        code, out, err = self.take(path, *args)
        self.assertEqual((code, err), (code_wanted, line + '\n'), out)
        self.assertEqual(read(vault.store_path(self.two.rig.ctx)), before)
        self.assertEqual(self.two.rig.kc.count('write'), writes)

    def test_each_kind_has_its_own_line_and_code_and_nothing_is_kept(self):
        dest = self.export()
        data = read(dest)
        bad = os.path.join(self.two.folder, 'bad.sbx')

        def put(b):
            with open(bad, 'wb') as f:
                f.write(b)
        wrong = pass_file(self.two.folder, text='not the passphrase')
        before = read(vault.store_path(self.two.rig.ctx))
        code, _, _, err = self.two.run('import', dest, '--passphrase-file', wrong, '--yes')
        self.assertEqual((code, err), (3, 'That passphrase does not open this file. Nothing was kept.\n'))
        self.assertEqual(read(vault.store_path(self.two.rig.ctx)), before)
        middle = data.index(b'\ntag ') - 10
        put(data[:middle] + bytes([data[middle] ^ 1]) + data[middle + 1:])
        self.kept_nothing(bad, 4, 'This file was changed or damaged after it was made, so Switchboard will not use '
                                  'it. Nothing was kept.')
        put(data[:len(data) // 2])
        self.kept_nothing(bad, 5, 'This file is cut short: part of it is missing. Copy it again from the other Mac. '
                                  'Nothing was kept.')
        lines = data.split(b'\n')
        newer = b'\n'.join([lines[0], json.dumps(dict(json.loads(lines[1]), version=2)).encode()] + lines[2:-2])
        head = newer + b'\n'
        put(head + ('sum %s\n' % hashlib.sha256(head).hexdigest()).encode())
        self.kept_nothing(bad, 6, 'This file was made by a newer Switchboard. Update this one (switchboard update), '
                                  'then try again. Nothing was kept.')

    def test_a_wrong_passphrase_is_asked_for_only_after_the_file_is_seen_whole(self):
        dest = self.export()
        bad = os.path.join(self.two.folder, 'cut.sbx')
        with open(bad, 'wb') as f:
            f.write(read(dest)[:100])
        with mock.patch.object(backup, '_ask_hidden', side_effect=AssertionError('asked')):
            code, _, _, err = self.two.run('import', bad, '--yes')
        self.assertEqual(code, 5)

    def test_words_it_does_not_know(self):
        code, _, _, err = self.two.run('import', 'x.sbx', '--force')
        self.assertEqual(code, 2)
        self.assertIn('Unknown word: --force.', err)
        code, _, _, err = self.two.run('import', '-', '--passphrase-fd', '0')
        self.assertEqual(code, 2)
        self.assertIn('Standard input holds the file', err)


if __name__ == '__main__':
    unittest.main()
