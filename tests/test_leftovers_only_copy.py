"""A safety copy that is the only copy of a login that was in use is never cleared by accident.

A switch keeps the login Claude Code was on in a safety copy before it writes over it. When that login is no saved
account's and Claude Code's own login item no longer holds it, the copy is the only one left on the Mac: clearing it
signs that login out for good. `switchboard leftovers` says so, `leftovers clear --all` never clears it, and
`leftovers clear ID` keeps it and says what frees it. Only `leftovers clear ID --only-copy` clears it, after a
question that says what is given up (`--yes` answers that question, never the flag). Removing an account judges its
copies exactly as before (tests/test_accounts_recovery_leftovers.py pins that); this rule is the list's own, and a
check over many made-up cases holds it to its definition."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import io
import json
import random
import unittest
from contextlib import redirect_stdout
from unittest import mock

from fakes_accounts import Rig, login_text
from switchboard import __main__ as cli
from switchboard import core, leftovers
from switchboard.accounts import api, context, live, net, recovery, switcher, vault
from switchboard.accounts.api import ABSENT, PRESENT, UNREADABLE, Read

A, B, X = 'a@example.com', 'b@example.com', 'x@example.com'
SECRET_A, SECRET_R = 'sk-ant-oat01-MADEUP-access-0c7d1', 'sk-ant-ort01-MADEUP-refresh-6e2b9'


class OnlyCopy(unittest.TestCase):
    """Claude Code was on x, a login nobody added and whose owner could not be proved; a switch to a kept account set
    it aside in a safety copy, the one copy of x's login left."""

    def setUp(self):
        self.rig = Rig()
        self.addCleanup(self.rig.close)
        self.ctx = self.rig.ctx
        use = context.use(self.ctx)
        use.__enter__()
        self.addCleanup(use.__exit__, None, None, None)
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = False
        self.rig.keep('uuid-a', A, 'org-a')
        self.rig.keep('uuid-b', B, 'org-b')
        self.x_text = self.rig.sign_in('uuid-x', X, 'org-x')
        self.rig.set_config(None)
        self.rig.server.script.append(('profile', net.NetError('timeout')))
        r = switcher.switch(self.ctx, 'uuid-a', kind='manual', source='cli', reason='test', interactive=True)
        self.assertTrue(r.ok)
        self.assertIn('kept aside in a safety copy', r.note or '')
        rows = [x for x in recovery.listing(self.ctx) if x['key'].startswith('safety:')]
        self.assertEqual(len(rows), 1)
        self.row = rows[0]

    def x_is_somewhere(self):
        return self.rig.holds_valid('uuid-x')

    def run_cli(self, *args, terminal=False, answer=None):
        out, asked = io.StringIO(), []

        def ask(prompt=''):
            asked.append(prompt)
            print(prompt, end='')
            return answer
        with redirect_stdout(out), mock.patch.object(cli, 'terminal', lambda: terminal), \
                mock.patch('builtins.input', ask):
            code = cli.main(list(args))
        return code, out.getvalue(), asked


class TheEngine(OnlyCopy):
    def test_it_is_listed_as_needed_the_only_copy(self):
        self.assertEqual((self.row['needed'], self.row['need'], self.row['needed_by']), (True, 'only_copy', None))

    def test_clearing_every_one_keeps_it_and_the_login_stays_on_the_mac(self):
        res = recovery.clear(self.ctx, everything=True)
        self.assertEqual(([r['key'] for r in res['kept']], res['cleared']), ([self.row['key']], []))
        self.assertTrue(self.x_is_somewhere())

    def test_naming_it_keeps_it(self):
        for ask in ([self.row['id']], [self.row['key']], [self.row['id'].upper()]):
            with self.subTest(ask=ask):
                res = recovery.clear(self.ctx, ask)
                self.assertEqual(([r['key'] for r in res['kept']], res['cleared']), ([self.row['key']], []))
        self.assertTrue(self.x_is_somewhere())

    def test_only_naming_it_with_the_flag_clears_it(self):
        res = recovery.clear(self.ctx, everything=True, only_copy=True)       # the flag never widens --all
        self.assertEqual(res['cleared'], [])
        res = recovery.clear(self.ctx, [self.row['id']], only_copy=True)
        self.assertEqual([r['key'] for r in res['cleared']], [self.row['key']])
        self.assertFalse(self.x_is_somewhere())

    def test_through_the_api(self):
        self.assertEqual(api.clear_leftovers([self.row['id']])['cleared'], [])
        self.assertEqual(len(api.clear_leftovers([self.row['id']], only_copy=True)['cleared']), 1)

    def test_it_is_safe_once_claude_codes_item_holds_that_login_again(self):
        self.rig.set_live(self.x_text)
        row = recovery.listing(self.ctx)[0]
        self.assertEqual((row['needed'], row['need']), (False, None))

    def test_it_stays_needed_while_claude_codes_item_cant_be_read(self):
        self.rig.kc.rule('read', service=self.rig.service, account=self.rig.account, why='locked', times=-1)
        row = recovery.listing(self.ctx)[0]
        self.assertEqual((row['needed'], row['need']), (True, 'only_copy'))
        self.assertTrue(row['unsure'])

    def test_a_saved_account_that_holds_it_frees_it(self):
        fp = live.parse(self.x_text).fingerprint
        with vault.changing(self.ctx) as doc:
            doc['accounts']['uuid-b']['fp'] = fp
        self.assertFalse(recovery.listing(self.ctx)[0]['needed'])
        with vault.changing(self.ctx) as doc:                   # renewed past it: its successor is saved
            doc['accounts']['uuid-b']['fp'] = 'fp-later'
            doc['accounts']['uuid-b']['previous_fps'] = [fp]
        self.assertFalse(recovery.listing(self.ctx)[0]['needed'])

    def test_its_known_account_signing_in_again_frees_it(self):
        made_up = login_text(SECRET_A, SECRET_R, 4102444800000)
        vault.stash(self.ctx, 'safety:made-up', made_up, 'uuid-b', 'before switching to %s' % A)
        rows = dict((r['key'], r) for r in recovery.listing(self.ctx))
        self.assertEqual((rows['safety:made-up']['need'], rows['safety:made-up']['needed_by']), ('only_copy', B))
        self.rig.keep('uuid-b', B, 'org-b')                       # a new login saved for b after the copy was kept
        rows = dict((r['key'], r) for r in recovery.listing(self.ctx))
        self.assertFalse(rows['safety:made-up']['needed'])

    def test_nothing_secret_in_the_list(self):
        blob = json.loads(self.x_text)['claudeAiOauth']
        for secret in (blob['accessToken'], blob['refreshToken']):
            self.assertNotIn(secret, json.dumps(recovery.listing(self.ctx)))
        said = self.run_cli('leftovers')[1] + self.run_cli('leftovers', '--json')[1] + \
            self.run_cli('leftovers', 'clear', self.row['id'], '--yes')[1]
        for secret in (blob['accessToken'], blob['refreshToken'], live.parse(self.x_text).fingerprint):
            self.assertNotIn(secret, said)


class TheCommand(OnlyCopy):
    def test_the_list_says_it_is_the_only_copy(self):
        code, out, _ = self.run_cli('leftovers')
        self.assertEqual(code, 0)
        self.assertIn('        needed: the only copy of a login that was in use; its account is not known',
                      out.splitlines())
        doc = json.loads(self.run_cli('leftovers', '--json')[1])
        self.assertEqual(doc['leftovers'][0]['what_to_do'],
                         'needed: the only copy of a login that was in use; its account is not known')
        self.assertEqual(doc['leftovers'][0]['needed'], True)

    def test_all_never_clears_it(self):
        code, out, _ = self.run_cli('leftovers', 'clear', '--all', '--yes')
        self.assertEqual(code, 0, out)
        self.assertNotIn('Cleared', out)
        self.assertTrue(self.x_is_somewhere())

    def test_naming_it_keeps_it_and_says_what_frees_it(self):
        i = self.row['id']
        code, out, _ = self.run_cli('leftovers', 'clear', i, '--yes')
        self.assertEqual(code, 1)
        self.assertEqual(out, 'Kept %s: it is the only copy of a login that was in use, and its account is not known. '
                              'To keep that account, add it with switchboard add. '
                              'To give this copy up for good: switchboard leftovers clear %s --only-copy.\n'
                         % (i, i))
        self.assertTrue(self.x_is_somewhere())

    def test_the_flag_asks_a_question_that_says_it_plainly(self):
        i = self.row['id']
        with self.assertRaises(core.SwitchError) as e:
            self.run_cli('leftovers', 'clear', i, '--only-copy')
        self.assertEqual(str(e.exception), 'Add --yes to clear without being asked.')
        code, out, asked = self.run_cli('leftovers', 'clear', i, '--only-copy', terminal=True, answer='n')
        self.assertEqual(code, 1)
        self.assertEqual(asked, ['%s is the only copy of a login that was in use; its account is not known. Once it is '
                                 'cleared, that login is gone from this Mac for good. Clear it? [y/N] ' % i])
        self.assertTrue(out.endswith('Nothing cleared.\n'))
        self.assertTrue(self.x_is_somewhere())
        self.assertEqual(self.run_cli('leftovers', 'clear', i, '--only-copy', '--yes')[:2], (0, 'Cleared %s.\n' % i))
        self.assertFalse(self.x_is_somewhere())

    def test_the_flag_goes_with_ids_only(self):
        for args in (('clear', '--all', '--only-copy'), ('clear', '--only-copy'), ('clear', '--only-copy', '--yes'),
                     ('clear', self.row['id'], '--only-copy', '--only-copy')):
            with self.subTest(args=args):
                code, out, _ = self.run_cli('leftovers', *args)
                self.assertEqual((code, out), (2, leftovers.FORMS + '\n'))
        self.assertTrue(self.x_is_somewhere())


# ---------- every sentence `clear` says for a copy it keeps ----------

class EveryKeptSentence(unittest.TestCase):
    """One whole sentence (or two) for each kind of need, word for word."""

    def said(self, row):
        rows = [dict(row, id='3f9a1c', key='safety:k', needed=True)]
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(leftovers, 'rows', lambda: rows), \
                mock.patch.object(leftovers.api, 'clear_leftovers'):
            code = leftovers.clear(['3f9a1c', '--yes'], cli)
        self.assertEqual(code, 1)
        return out.getvalue()

    def test_each(self):
        cases = [
            ({'need': 'only_login', 'needed_by': 'maya@northwind.example'},
             'Kept 3f9a1c: it is the only copy of the login for maya@northwind.example until that account signs in '
             'again. To free it, sign in to maya@northwind.example again: switchboard add maya@northwind.example'),
            ({'need': 'only_login', 'needed_by': None},
             'Kept 3f9a1c: it is the only copy of the login for an account until that account signs in again. Run '
             'switchboard doctor.'),
            ({'need': 'switch', 'needed_by': None},
             'Kept 3f9a1c: a switch that was cut off may still put it back in Claude Code. It is free once Switchboard '
             'settles that switch, at its next start or switch.'),
            ({'need': 'journal', 'needed_by': None},
             "Kept 3f9a1c: the record of a switch can't be read, and it may name this copy. It is free once "
             'Switchboard puts that record away, at its next start or switch.'),
            ({'need': 'unknown', 'needed_by': None},
             "Kept 3f9a1c: Switchboard's account list can't be read, so whether anything needs it can't be told. Run "
             'switchboard doctor.'),
            ({'need': 'only_copy', 'needed_by': 'maya@northwind.example'},
             'Kept 3f9a1c: it is the only copy of a login maya@northwind.example was using. To free it, sign in to '
             'maya@northwind.example again: switchboard add maya@northwind.example. To give this '
             'copy up for good: switchboard leftovers clear 3f9a1c --only-copy.'),
            ({'need': 'only_copy', 'needed_by': None},
             'Kept 3f9a1c: it is the only copy of a login that was in use, and its account is not known. To keep that '
             'account, add it with switchboard add. To give this copy up for '
             'good: switchboard leftovers clear 3f9a1c --only-copy.'),
            ({'need': 'only_copy', 'needed_by': None, 'unsure': True},
             "Kept 3f9a1c: Claude Code's login can't be read now, so whether this is the only copy of a login that was "
             'in use can\'t be told. List it again once the Keychain answers. To give this copy up for good: '
             'switchboard leftovers clear 3f9a1c --only-copy.'),
        ]
        for row, want in cases:
            with self.subTest(row=row):
                self.assertEqual(self.said(row), want + '\n')

    def test_each_what_to_do_is_one_phrase_after_needed(self):
        for row, want in [
                ({'need': 'only_copy', 'needed_by': 'maya@northwind.example'},
                 'needed: the only copy of a login maya@northwind.example was using'),
                ({'need': 'only_copy', 'needed_by': None},
                 'needed: the only copy of a login that was in use; its account is not known'),
                ({'need': 'only_copy', 'needed_by': None, 'unsure': True},
                 "needed: it may be the only copy of a login that was in use (Claude Code's login can't be read now)")]:
            with self.subTest(row=row):
                self.assertEqual(leftovers.what_to_do(dict(row, needed=True)), want)


# ---------- the rule, over many made-up cases ----------

def oracle(doc, entries, journal, live_fp):
    """The keys the list must call needed, written apart from the code: needed()'s, plus every safety copy that no
    saved account's login is (now or renewed past it), that Claude Code's item is not (or can't be read), and that is
    not a known account's that saved a login after the copy was kept, when the copy is known to hold no token (it has
    a refresh fingerprint, or it was marked as no token when it was made)."""
    r, j = journal
    pending = j.get('safety_key') if r.kind == PRESENT and isinstance(j, dict) else None
    out = set(recovery.needed(mock.Mock(), doc, entries, journal))
    if doc is None:
        return out
    for e in entries:
        k = e['key']
        if k in out or not k.startswith('safety:') or k == pending:
            continue
        fp = e.get('fp') or k[len('safety:'):]
        held = any(a.get('fp') == fp or fp in (a.get('previous_fps') or []) for a in doc['accounts'].values())
        a = doc['accounts'].get(e.get('account_id'))
        resaved = a is not None and isinstance(e.get('at'), (int, float)) and (a.get('saved_at') or 0) > e['at'] and \
            (bool(e.get('fp')) or e.get('token') is False)
        if not held and not resaved and live_fp != fp:
            out.add(k)
    return out


class TheRuleOverManyCases(unittest.TestCase):
    def test_the_list_follows_the_rule_and_clear_all_never_takes_a_needed_one(self):
        rnd = random.Random(20261126)
        seen = {'only': 0, 'freed': 0}
        ctx = mock.Mock()
        for case in range(4000):
            ids = ['uuid-%d' % i for i in range(rnd.randint(1, 4))]
            fps = ['fp-%d' % i for i in range(6)]
            doc = {'accounts': dict((i, {'uuid': i, 'fp': rnd.choice(fps + [None]), 'email': '%s@made-up.example' % i,
                                         'previous_fps': rnd.sample(fps, rnd.randint(0, 2)),
                                         'saved_at': rnd.choice([None, 100.0, 200.0, 300.0])}) for i in ids),
                   'removed': {}}
            entries = {}
            for n in range(rnd.randint(0, 6)):
                if rnd.random() < 0.6:
                    fp = rnd.choice(fps + [None])
                    key = 'safety:' + (fp or 'text-%d' % n)
                    owner = rnd.choice(ids + ['uuid-x', None])
                else:
                    owner, fp = rnd.choice(ids + ['uuid-x']), rnd.choice(fps)
                    key = 'successor:' + owner
                entries[key] = {'key': key, 'account_id': owner, 'fp': fp, 'at': rnd.choice([None, 150.0, 250.0]),
                                'reason': rnd.choice(('before switching to b', 'save failed: locked')),
                                'supersedes': rnd.choice(fps + [None]), 'names': rnd.choice(ids + [None, None])}
                mark = rnd.choice((None, False, True)) if fp is None else None     # None: listed before the mark
                if mark is not None:
                    entries[key]['token'] = mark
            entries = list(entries.values())
            journal = rnd.choice((Read(ABSENT), Read(UNREADABLE, why='unparseable'), Read(PRESENT, value='x')))
            j = {'safety_key': rnd.choice([e['key'] for e in entries] + [None])} if journal.kind == PRESENT else None
            live_fp = rnd.choice(fps + [None, 'unreadable'])
            if rnd.random() < 0.05:
                doc = None
            live_said = (frozenset(), True) if live_fp == 'unreadable' else (frozenset([live_fp] if live_fp else []),
                                                                              False)
            with mock.patch.object(switcher, 'read_journal', lambda c: (journal, j)), \
                    mock.patch.object(recovery, '_claude_codes_login', lambda c: live_said), \
                    mock.patch.object(vault, 'load', side_effect=vault.VaultError('unreadable', 'made up')):
                rows = recovery.listing(ctx, doc, entries)
            want = oracle(doc, entries, (journal, j), live_fp)
            got = set(r['key'] for r in rows if r['needed'])
            self.assertEqual(got, want, (case, doc, entries, journal, j, live_fp))
            base = recovery.needed(ctx, doc, entries, (journal, j))
            for r in rows:                              # what removing an account keeps is said as it was
                if r['key'] in base:
                    self.assertEqual(r['need'], base[r['key']][0], case)
            # clear --all, the way the engine does it: never a needed one
            cleared = set(r['key'] for r in rows if not r['needed'])
            self.assertFalse(cleared & want, case)
            seen['only'] += len(want - set(base))
            seen['freed'] += sum(1 for r in rows if r['key'].startswith('safety:') and not r['needed'])
        self.assertGreater(seen['only'], 500)          # the cases reach both answers, many times
        self.assertGreater(seen['freed'], 500)


if __name__ == '__main__':
    unittest.main()
