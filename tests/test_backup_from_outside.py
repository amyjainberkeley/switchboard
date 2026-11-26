import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# An accounts file comes from outside: whoever has its passphrase chose every byte in it. So it is trusted no further
# than that. A login from it replaces a saved account's login only when Anthropic says that login is that account's;
# when that can't be asked (its access token has run out, or no answer), --replace leaves the account alone and says
# why. Its settings block is kept only as the keys Claude Code's own block has, of sane sizes, and an account is
# saved under the email the person was shown. Every id and email in it is checked for shape before it names a
# Keychain item or a file, so a quote, a backslash, a line break or a control character never reaches one.
import json
import os
import unittest

from fakes_accounts import login_text
from switchboard import backup, keys as K, lanes
from switchboard.accounts import net
from test_backup_export import PASS
from test_backup_import import A, B, KEY, Base
from test_keys import FakeKeychain as FakeKeysKeychain

FAR = 4102444800000          # 2100: a refresh expiry later than any real sign-in's


class Crafted(Base):
    def craft(self, change, name='crafted.sbx'):
        """A file made on the first Mac with the passphrase, then changed by `change(items)` before it is sealed."""
        payload, _ = backup.gather(self.one.rig.ctx)
        payload['accounts'] = change(payload['accounts']) or payload['accounts']
        dest = os.path.join(self.one.folder, name)
        with open(dest, 'wb') as f:
            f.write(backup.seal(json.dumps(payload).encode(), PASS))
        return dest

    def b_kept_on_two(self):
        """The second Mac keeps B (idle: Claude Code is back on its own account)."""
        self.two.rig.keep('uuid-b', B, 'org-f')
        self.two.rig.keep('uuid-z', 'zed@harbor.example', 'org-z')
        return self.two.rig.vault(self.ids[B])

    def only(self, email, **over):
        def change(items):
            out = [dict(x) for x in items if x['email'] == email]
            for k, v in over.items():
                out[0][k] = v
            return out
        return change


class ReplaceNeedsAnthropicsWord(Crafted):
    def test_a_login_naming_another_account_with_its_access_run_out_is_not_put_over_it(self):
        before = self.b_kept_on_two()
        other = login_text('sk-ant-oat01-MADEUP-other-access', 'sk-ant-ort01-MADEUP-other-refresh', 1000, FAR)
        dest = self.craft(self.only(B, login=other))
        code, out, err = self.take(dest, '--replace')
        self.assertEqual(code, 0, err)
        self.assertEqual(self.two.rig.vault(self.ids[B]), before)
        self.assertIn("skip: --replace takes only a login Anthropic says is this account's, and its access token has "
                      "run out, so Anthropic can't be asked\n", out)
        self.assertNotIn('Replaced', out)

    def test_when_anthropic_cant_be_asked_the_login_here_stays(self):
        before = self.b_kept_on_two()
        dest = self.craft(self.only(B, login=login_text('sk-ant-oat01-MADEUP-fresh-access',
                                                        'sk-ant-ort01-MADEUP-fresh-refresh', FAR, FAR)))
        self.two.rig.server.script.append(('profile', net.NetError('timeout')))
        code, out, err = self.take(dest, '--replace')
        self.assertEqual(code, 0, err)
        self.assertEqual(self.two.rig.vault(self.ids[B]), before)
        self.assertIn("Did not keep %s (--replace takes only a login Anthropic says is this account's, and Anthropic "
                      "could not be asked just now)." % B, out)

    def test_a_newer_login_anthropic_says_is_its_own_still_replaces_the_one_here(self):
        self.assertEqual(self.take(self.export('--account', B))[0], 0)       # B came from the first Mac before
        before = self.two.rig.vault(self.ids[B])
        newer = self.renew_on_one(B)
        dest = self.export('--account', B)
        code, out, err = self.take(dest, '--replace')
        self.assertEqual(code, 0, err)
        self.assertIn('Replaced the older login of %s: it works.' % B, out)
        self.assertNotEqual(before, newer)
        self.assertEqual(self.two.rig.vault(self.ids[B]), newer)


class TheSettingsBlock(Crafted):
    def test_only_the_known_keys_of_sane_size_are_kept(self):
        def change(items):
            x = [dict(i) for i in items if i['email'] == A]
            s = dict(x[0]['settings'])
            s.update({'x': 'y' * 300000, 'nested': {'a': [1, 2, {'b': 3}]}, 'displayName': 'z' * 5000,
                      'organizationName': 'Northwind', 'hasExtraUsageEnabled': True, 'billingType': ['a']})
            x[0]['settings'] = s
            return x
        code, out, err = self.take(self.craft(change))
        self.assertEqual(code, 0, err)
        kept = self.two.rig.doc()['accounts'][self.ids[A]]['identity']
        self.assertEqual(sorted(kept), ['accountUuid', 'emailAddress', 'hasExtraUsageEnabled', 'organizationName',
                                        'organizationUuid'])
        self.assertEqual(kept['organizationName'], 'Northwind')

    def test_an_account_is_saved_under_the_email_shown_or_not_at_all(self):
        def change(items):
            x = [dict(i) for i in items if i['email'] == A]
            x[0]['settings'] = dict(x[0]['settings'], emailAddress='boss@bank.example')
            return x
        code, out, err = self.take(self.craft(change))
        self.assertEqual(code, 0, err)
        self.assertIn(': skip: the file names two different emails for it\n', out)
        self.assertNotIn(self.ids[A], self.two.rig.doc()['accounts'])
        self.assertNotIn('boss@bank.example', json.dumps(self.two.rig.doc()))


class EveryNameIsCheckedForShape(Crafted):
    BAD = ('uuid-a"', 'uuid-a\\', 'uuid-a\nx', 'uuid-a\x1b[2J', 'uuid-a\x00', "uuid-a'", 'uuid a', '', 'u' * 200)

    def no_odd_write(self, before_writes):
        for verb, service, account in self.two.rig.kc.calls[before_writes:]:
            self.assertTrue(account is None or all(c.isalnum() or c in '._-/ ' for c in account), (verb, account))

    def test_an_account_id_from_the_file(self):
        for bad in self.BAD:
            with self.subTest(bad=bad):
                def change(items):
                    x = [dict(i) for i in items if i['email'] == A]
                    s = dict(x[0]['settings'])
                    s.pop('accountUuid')
                    x[0].update(settings=s, account=bad)
                    return x
                n = len(self.two.rig.kc.calls)
                code, out, err = self.take(self.craft(change))
                self.assertEqual(code, 0, err)
                self.assertIn(': skip: it names its account in a way an account is never named\n', out)
                self.no_odd_write(n)
                self.assertEqual(sorted(self.two.rig.doc()['accounts']), ['uuid-z'])

    def test_the_ids_in_its_settings_and_its_organization(self):
        for where in ('accountUuid', 'organizationUuid', 'organization_id'):
            for bad in ('org"x', 'org\nx', 'org\\x', 'org\x07x'):
                with self.subTest(where=where, bad=bad):
                    def change(items):
                        x = [dict(i) for i in items if i['email'] == A]
                        if where == 'organization_id':
                            s = dict(x[0]['settings'])
                            s.pop('accountUuid')
                            s.pop('organizationUuid')
                            x[0].update(settings=s, organization_id=bad)
                        else:
                            x[0]['settings'] = dict(x[0]['settings'], **{where: bad})
                        return x
                    n = len(self.two.rig.kc.calls)
                    code, out, err = self.take(self.craft(change))
                    self.assertEqual(code, 0, err)
                    self.assertIn('skip: it names its account in a way an account is never named', out)
                    self.no_odd_write(n)
                    self.assertEqual(sorted(self.two.rig.doc()['accounts']), ['uuid-z'])

    def test_an_email_from_the_file(self):
        for bad in ('maya@northwind.example\x1b]0;x\x07', 'a"b@x.example', 'a\\b@x.example', 'a\nb@x.example',
                    'no-at-sign', 'x' * 400 + '@x.example', 'a‮b@x.example'):
            with self.subTest(bad=bad):
                def change(items):
                    x = [dict(i) for i in items if i['email'] == A]
                    x[0].update(email=bad, settings=dict(x[0]['settings'], emailAddress=bad))
                    return x
                code, out, err = self.take(self.craft(change))
                self.assertEqual(code, 0, err)
                self.assertIn('skip: its email is not one an account can have', out)
                self.assertEqual(sorted(self.two.rig.doc()['accounts']), ['uuid-z'])


class AKeysName(Crafted):
    def test_a_key_named_with_a_quote_or_a_line_break_is_skipped_before_the_keychain(self):
        kc = FakeKeysKeychain()
        lanes.KEYS['store'] = K.Keys(runner=kc, path=os.path.join(self.two.folder, 'lanes.json'))
        self.addCleanup(lanes.KEYS.update, store=None)
        payload, _ = backup.gather(self.one.rig.ctx)
        payload['accounts'] = []
        payload['keys'] = [{'name': bad, 'kind': 'anthropic', 'base_url': None, 'auth': None, 'names': None,
                            'key': KEY} for bad in ('sp"are', 'sp\nare', 'sp\\are', 'sp\x1bare')]
        dest = os.path.join(self.one.folder, 'keys.sbx')
        with open(dest, 'wb') as f:
            f.write(backup.seal(json.dumps(payload).encode(), PASS))
        code, out, err = self.take(dest)
        self.assertEqual(code, 0, err)
        self.assertEqual(out.count('skip: a key is never named that way'), 4, out)
        self.assertEqual(kc.calls, [])
        self.assertEqual(lanes.lanes(), [])


class TheHelpers(unittest.TestCase):
    def test_ids(self):
        for good in ('uuid-a', '0d2c7a1e-1f2b-4c3d-9e8f-001122334455', 'uuid-a/org-f', 'A.b_c-9'):
            self.assertTrue(backup.plain_id(good), good)
        for bad in EveryNameIsCheckedForShape.BAD + ('a/b/c', '/x', 'x/', None, 7):
            self.assertFalse(backup.plain_id(bad), bad)


if __name__ == '__main__':
    unittest.main()
