"""`switchboard leftovers`: the logins Switchboard keeps aside (its recovery copies), each with its id, the account it
was kept for, why, when, where, and what to do with it; `--json` for scripts; `leftovers clear ID` and `--all`, which
ask first (`--yes` answers) and never clear one still needed. The real engine runs on a made-up Mac (fakes_accounts.Rig:
a fake Keychain, a throwaway home); a made-up login is fed through and searched for in every output."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import io
import json
import re
import unittest
from contextlib import redirect_stdout
from unittest import mock

from fakes_accounts import Rig, login_text
from switchboard import __main__ as cli
from switchboard import core, leftovers
from switchboard.accounts import context, live, vault

N, F = 'maya@northwind.example', 'maya@fernhill.example'
SECRET_A, SECRET_R = 'sk-ant-oat01-MADEUP-access-5b1d0', 'sk-ant-ort01-MADEUP-refresh-c44a7'
USAGE = ('switchboard leftovers [--json]\nswitchboard leftovers clear ID...|--all [--yes]\n'
         'switchboard leftovers clear ID... --only-copy [--yes]\n')


class Leftovers(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.addCleanup(self.rig.close)
        self.ctx = self.rig.ctx
        use = context.use(self.ctx)
        use.__enter__()
        self.addCleanup(use.__exit__, None, None, None)
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = False
        self.rig.keep('uuid-n', N, 'org-n')
        self.f_text = self.rig.keep('uuid-f', F, 'org-f')
        self.made_up = login_text(SECRET_A, SECRET_R, 4102444800000)

    def run_cli(self, *args, terminal=False, answer=None):
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(cli, 'terminal', lambda: terminal), \
                mock.patch('builtins.input', lambda prompt='': (print(prompt, end=''), answer)[1]):
            code = cli.main(list(args))
        return code, out.getvalue()

    def three(self):
        """One needed (N's renewed login, its only working one), one safe in the Keychain, one safe in a file."""
        fp = vault.load(self.ctx)['accounts']['uuid-n']['fp']
        vault.stash(self.ctx, 'successor:uuid-n', self.made_up, 'uuid-n', 'save failed: locked', supersedes=fp)
        vault.stash(self.ctx, 'safety:old-f', self.f_text, 'uuid-f', 'before switching to %s' % N)  # F's slot has it
        self.rig.kc.rule('write', service=vault.SERVICE_RECOVERY, account='successor:uuid-gone', why='locked', times=1)
        vault.stash(self.ctx, 'successor:uuid-gone', self.made_up, 'uuid-gone', 'removed while refreshing')
        return dict((r['key'], r['id']) for r in leftovers.rows())

    def no_secret(self, text):
        for secret in (SECRET_A, SECRET_R, self.made_up, live.parse(self.made_up).fingerprint):
            self.assertNotIn(secret, text)

    def keys(self):
        return sorted(e['key'] for e in vault.stashed(self.ctx))


class TheList(Leftovers):
    def test_none_is_one_line(self):
        self.assertEqual(self.run_cli('leftovers'), (0, 'Switchboard holds no leftover logins.\n'))
        self.assertEqual(json.loads(self.run_cli('leftovers', '--json')[1]), {'schema': 1, 'leftovers': []})

    def test_each_with_its_id_account_why_when_where_and_what_to_do(self):
        ids = self.three()
        code, out = self.run_cli('leftovers')
        self.assertEqual(code, 0)
        self.no_secret(out)
        lines = out.splitlines()
        self.assertEqual(lines[0], '3 leftover logins: copies Switchboard kept aside, none of them a login in use.')
        self.assertIn('%s  %s' % (ids['successor:uuid-n'], N), lines)
        self.assertIn('        needed: the only copy of the login for %s until that account signs in again' % N, lines)
        self.assertIn('%s  account not known' % ids['successor:uuid-gone'], lines)
        self.assertEqual(sum(1 for x in lines if x == '        safe to clear'), 2)
        self.assertTrue(any(re.match(r'        the login in use before a switch to %s; kept \d{4}-\d\d-\d\d \d\d:\d\d, '
                                     r'in the Keychain$' % re.escape(N), x) for x in lines), out)
        self.assertTrue(any(x.endswith(', in a file') for x in lines), out)
        self.assertEqual(lines[-1], 'To clear one: switchboard leftovers clear ID (--all clears every one that is safe '
                                    'to clear).')

    def test_json_for_scripts(self):
        ids = self.three()
        code, out = self.run_cli('leftovers', '--json')
        self.assertEqual(code, 0)
        self.no_secret(out)
        doc = json.loads(out)
        self.assertEqual(list(doc), ['schema', 'leftovers'])
        self.assertEqual(doc['schema'], 1)
        by = dict((x['id'], x) for x in doc['leftovers'])
        for x in doc['leftovers']:
            self.assertEqual(list(x), ['id', 'account', 'why', 'kept_at', 'kept_in', 'needed', 'needed_by',
                                       'what_to_do'])
            self.assertRegex(x['kept_at'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')
        n = by[ids['successor:uuid-n']]
        self.assertEqual((n['account'], n['kept_in'], n['needed'], n['needed_by']), (N, 'keychain', True, N))
        self.assertEqual(n['what_to_do'], 'needed: the only copy of the login for %s until that account signs in '
                                          'again' % N)
        g = by[ids['successor:uuid-gone']]
        self.assertEqual((g['account'], g['kept_in'], g['needed'], g['needed_by'], g['what_to_do']),
                         (None, 'file', False, None, 'safe to clear'))

    def test_wrong_words_exit_2_with_the_forms(self):
        for args in (('bogus',), ('clear',), ('clear', '--all', 'abc'), ('clear', '--yes')):
            with self.subTest(args=args):
                self.assertEqual(self.run_cli('leftovers', *args), (2, USAGE))

    def test_the_demo_refuses_it(self):
        core.DEMO = True
        with self.assertRaises(core.SwitchError) as e:
            self.run_cli('leftovers')
        self.assertIn('The demo has made-up accounts', str(e.exception))


class Clearing(Leftovers):
    def test_one_by_its_id_then_again(self):
        ids = self.three()
        safe = ids['safety:old-f']
        self.assertEqual(self.run_cli('leftovers', 'clear', safe, '--yes'), (0, 'Cleared %s.\n' % safe))
        self.assertNotIn('safety:old-f', self.keys())
        self.assertEqual(self.run_cli('leftovers', 'clear', safe, '--yes'),
                         (1, 'No leftover login has the id %s; switchboard leftovers lists them.\n' % safe))

    def test_a_needed_one_stays_and_it_says_who_needs_it_and_what_frees_it(self):
        ids = self.three()
        needed = ids['successor:uuid-n']
        for args in ((needed, '--yes'), (needed.upper(), '--yes'), (needed, ids['safety:old-f'], '--yes')):
            with self.subTest(args=args):
                code, out = self.run_cli('leftovers', 'clear', *args)
                self.assertEqual(code, 1)
                self.assertIn('Kept %s: it is the only copy of the login for %s until that account signs in again. '
                              'To free it, sign in to %s again: switchboard add %s\n'
                              % (needed, N, N, N), out)
        self.assertIn('successor:uuid-n', self.keys())
        self.assertEqual(self.rig.kc.get(vault.SERVICE_RECOVERY, 'successor:uuid-n'), self.made_up)

    def test_all_clears_every_safe_one_and_keeps_the_needed(self):
        ids = self.three()
        code, out = self.run_cli('leftovers', 'clear', '--all', '--yes')
        self.assertEqual(code, 0, out)
        first = out.splitlines()[0]
        self.assertTrue(first.startswith('Cleared 2 leftover logins: ') and first.endswith('.'), out)
        self.assertEqual(sorted(first[len('Cleared 2 leftover logins: '):-1].split(', ')),
                         sorted([ids['safety:old-f'], ids['successor:uuid-gone']]))
        self.assertIn('Kept %s: ' % ids['successor:uuid-n'], out)
        self.assertEqual(self.keys(), ['successor:uuid-n'])
        self.no_secret(out)
        self.assertEqual(self.run_cli('leftovers', 'clear', '--all', '--yes')[0], 0)       # twice: safe
        self.assertEqual(self.keys(), ['successor:uuid-n'])

    def test_it_asks_first(self):
        ids = self.three()
        safe = ids['safety:old-f']
        with self.assertRaises(core.SwitchError) as e:
            self.run_cli('leftovers', 'clear', safe)
        self.assertEqual(str(e.exception), 'Add --yes to clear without being asked.')
        self.assertEqual(self.run_cli('leftovers', 'clear', safe, terminal=True, answer='n'),
                         (1, 'Clear %s (the login in use before a switch to %s)? [y/N] Nothing cleared.\n'
                          % (safe, N)))
        self.assertIn('safety:old-f', self.keys())
        code, out = self.run_cli('leftovers', 'clear', '--all', terminal=True, answer='y')
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith('Clear 2 leftover logins? 1 more is still needed and stays. [y/N] Cleared 2 '),
                        out)

    def test_a_keychain_that_refuses_is_said(self):
        ids = self.three()
        self.rig.kc.rule('delete', service=vault.SERVICE_RECOVERY, account='safety:old-f', why='locked', times=1)
        self.assertEqual(self.run_cli('leftovers', 'clear', ids['safety:old-f'], '--yes'),
                         (1, 'Could not clear %s: the Keychain is locked. Unlock the Mac, then try again.\n'
                          % ids['safety:old-f']))
        self.assertIn('safety:old-f', self.keys())


class ForDoctor(Leftovers):
    def test_one_line_or_nothing(self):
        self.assertIsNone(leftovers.doctor_line())
        vault.stash(self.ctx, 'safety:old-f', self.made_up, 'uuid-f', 'before switching to %s' % N)
        self.assertEqual(leftovers.doctor_line(), '1 leftover login (switchboard leftovers)')
        vault.stash(self.ctx, 'safety:old-n', self.made_up, 'uuid-n', 'before switching to %s' % F)
        self.assertEqual(leftovers.doctor_line(), '2 leftover logins (switchboard leftovers)')

    def test_it_never_raises(self):
        with mock.patch.object(leftovers.api, 'leftovers', side_effect=OSError('made-up')):
            self.assertIsNone(leftovers.doctor_line())


if __name__ == '__main__':
    unittest.main()
