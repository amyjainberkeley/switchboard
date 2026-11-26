"""The recovery copies a person can see and clear (`switchboard leftovers`): listed from the Keychain and from the file
fallback, with no secret; each judged by the one rule removing an account keeps (recovery.needed); a copy that is still
needed is never cleared, however it is asked for; and removing an account drops exactly what it dropped before that
rule had a name (a check over thousands of made-up cases from a fixed seed)."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import os
import random
import unittest
from unittest import mock

from fakes_accounts import Rig, login_text
from switchboard.accounts import api, context, live, recovery, roster, switcher, tokens, vault
from switchboard.accounts.api import ABSENT, PRESENT, UNREADABLE, Read

N, F = 'maya@northwind.example', 'maya@fernhill.example'
SECRET_A, SECRET_R = 'sk-ant-oat01-MADEUP-access-7f3e9', 'sk-ant-ort01-MADEUP-refresh-91c2d'


def unrestorable(ctx):
    """The list and every copy it could be restored from (vault.restore): written over, the list can't be read."""
    path = vault.store_path(ctx)
    return [path, ctx.path(vault.LAST_GOOD)] + ['%s.%d' % (path, n) for n in range(1, vault.BACKUPS + 1)]


class Copies(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.addCleanup(self.rig.close)
        self.ctx = self.rig.ctx
        self.n_text = self.rig.keep('uuid-n', N, 'org-n')
        self.f_text = self.rig.keep('uuid-f', F, 'org-f')           # Claude Code is on F
        self.made_up = login_text(SECRET_A, SECRET_R, 4102444800000)

    def fp(self, aid):
        return vault.load(self.ctx)['accounts'][aid]['fp']

    def in_a_file(self, key, text, aid, reason, **kw):
        self.rig.kc.rule('write', service=vault.SERVICE_RECOVERY, account=key, why='locked', times=1)
        self.assertEqual(vault.stash(self.ctx, key, text, aid, reason, **kw), (True, 'file'))

    def rows(self):
        return dict((r['key'], r) for r in recovery.listing(self.ctx))

    def keys(self):
        return sorted(e['key'] for e in vault.stashed(self.ctx))


class TheList(Copies):
    def test_from_the_keychain_and_from_a_file_with_no_secret(self):
        vault.stash(self.ctx, 'safety:old-n', self.n_text, 'uuid-n', 'before switching to %s' % F)  # N's slot has it
        self.in_a_file('successor:uuid-gone', self.made_up, 'uuid-gone', 'removed while refreshing')
        rows = self.rows()
        self.assertEqual(sorted(rows), ['safety:old-n', 'successor:uuid-gone'])
        s, g = rows['safety:old-n'], rows['successor:uuid-gone']
        self.assertEqual((s['where'], g['where']), ('keychain', 'file'))
        self.assertEqual((s['email'], g['email']), (N, None))
        self.assertEqual(s['why'], 'the login in use before a switch to %s' % F)
        self.assertEqual(g['why'], 'a renewed login of an account removed as it renewed')
        self.assertTrue(all(len(r['id']) == 6 and int(r['id'], 16) >= 0 for r in rows.values()))
        self.assertTrue(all(isinstance(r['at'], float) for r in rows.values()))
        self.assertEqual((s['needed'], g['needed']), (False, False))
        said = json.dumps(recovery.listing(self.ctx))
        for secret in (SECRET_A, SECRET_R, self.made_up, live.parse(self.made_up).fingerprint):
            self.assertNotIn(secret, said)

    def test_a_removed_accounts_copy_names_it_by_its_email(self):
        roster.remove(self.ctx, 'uuid-n')
        self.in_a_file('successor:uuid-n', self.made_up, 'uuid-n', 'removed while refreshing')
        self.assertEqual(self.rows()['successor:uuid-n']['email'], N)

    def test_none(self):
        self.assertEqual(recovery.listing(self.ctx), [])
        self.assertEqual(api.leftovers.__name__, 'leftovers')


class WhatIsNeeded(Copies):
    def test_the_safety_copy_a_cut_off_switch_names(self):
        vault.stash(self.ctx, 'safety:old-n', self.made_up, 'uuid-n', 'before switching to %s' % F)
        switcher._write_journal(self.ctx, {'from_id': 'uuid-n', 'to_id': 'uuid-f', 'safety_key': 'safety:old-n',
                                           'step': 'safety'})
        r = self.rows()['safety:old-n']
        self.assertEqual((r['needed'], r['need']), (True, 'switch'))

    def test_every_safety_copy_while_the_journal_cant_be_read(self):
        vault.stash(self.ctx, 'safety:old-n', self.made_up, 'uuid-n', 'before switching to %s' % F)
        vault.stash(self.ctx, 'successor:uuid-gone', self.made_up, 'uuid-gone', 'removed while refreshing')
        with open(switcher.journal_path(self.ctx), 'w') as f:
            f.write('{not json')
        rows = self.rows()
        self.assertEqual((rows['safety:old-n']['need'], rows['successor:uuid-gone']['need']), ('journal', None))

    def test_a_login_set_aside_for_the_account_anthropic_named(self):
        vault.stash(self.ctx, 'successor:uuid-n', self.f_text, 'uuid-n', tokens.CONFLICT, supersedes=self.fp('uuid-f'),
                    names='uuid-f')
        r = self.rows()['successor:uuid-n']
        self.assertEqual((r['need'], r['needed_by'], r['email']), ('only_login', F, N))

    def test_a_kept_accounts_successor_to_the_login_it_holds_until_it_signs_in_again(self):
        vault.stash(self.ctx, 'successor:uuid-n', self.made_up, 'uuid-n', 'save failed: locked',
                    supersedes=self.fp('uuid-n'))
        r = self.rows()['successor:uuid-n']
        self.assertEqual((r['need'], r['needed_by']), ('only_login', N))
        self.rig.keep('uuid-n', N, 'org-n')                 # it signs in again: a new saved login
        self.assertFalse(self.rows()['successor:uuid-n']['needed'])

    def test_nothing_can_be_told_while_the_account_list_cant_be_read(self):
        vault.stash(self.ctx, 'successor:uuid-gone', self.made_up, 'uuid-gone', 'removed while refreshing')
        for path in unrestorable(self.ctx):
            with open(path, 'w') as f:
                f.write('{not json')
        self.assertEqual(self.rows()['successor:uuid-gone']['need'], 'unknown')


class Clearing(Copies):
    def setUp(self):
        super().setUp()
        vault.stash(self.ctx, 'successor:uuid-n', self.made_up, 'uuid-n', 'save failed: locked',
                    supersedes=self.fp('uuid-n'))                                        # needed by N
        vault.stash(self.ctx, 'safety:old-n', self.n_text, 'uuid-n', 'before switching to %s' % F)    # safe: held
        self.in_a_file('successor:uuid-gone', self.made_up, 'uuid-gone', 'removed while refreshing')   # safe
        self.ids = dict((k, r['id']) for k, r in self.rows().items())

    def test_one_by_its_id_goes_from_the_keychain_and_the_list(self):
        res = recovery.clear(self.ctx, [self.ids['safety:old-n']])
        self.assertEqual([r['key'] for r in res['cleared']], ['safety:old-n'])
        self.assertEqual(self.keys(), ['successor:uuid-gone', 'successor:uuid-n'])
        self.assertIsNone(self.rig.kc.get(vault.SERVICE_RECOVERY, 'safety:old-n'))

    def test_one_in_a_file_goes_with_its_file(self):
        res = recovery.clear(self.ctx, [self.ids['successor:uuid-gone'].upper()])
        self.assertEqual([r['key'] for r in res['cleared']], ['successor:uuid-gone'])
        folder = os.path.join(self.rig.data, 'recovery')
        self.assertEqual([n for n in os.listdir(folder) if n.endswith('.json') and n != 'manifest.json'], [])

    def test_a_needed_one_is_never_cleared_however_it_is_asked_for(self):
        needed = self.ids['successor:uuid-n']
        for ask in ([needed], [needed.upper()], [' %s ' % needed], ['successor:uuid-n'], [needed, needed]):
            with self.subTest(ask=ask):
                res = recovery.clear(self.ctx, ask)
                self.assertEqual(([r['key'] for r in res['kept']], res['cleared']), (['successor:uuid-n'], []))
        res = recovery.clear(self.ctx, everything=True)
        self.assertEqual([r['key'] for r in res['kept']], ['successor:uuid-n'])
        self.assertEqual(sorted(r['key'] for r in res['cleared']), ['safety:old-n', 'successor:uuid-gone'])
        self.assertEqual(self.keys(), ['successor:uuid-n'])
        self.assertEqual(self.rig.kc.get(vault.SERVICE_RECOVERY, 'successor:uuid-n'), self.made_up)

    def test_it_is_judged_again_when_it_is_cleared(self):
        listed = recovery.listing(self.ctx)                 # safe when listed ...
        switcher._write_journal(self.ctx, {'from_id': 'uuid-n', 'to_id': 'uuid-f', 'safety_key': 'safety:old-n',
                                           'step': 'safety'})          # ... and a switch named it since
        res = recovery.clear(self.ctx, [r['id'] for r in listed if r['key'] == 'safety:old-n'])
        self.assertEqual(([r['key'] for r in res['kept']], res['cleared']), (['safety:old-n'], []))
        self.assertIn('safety:old-n', self.keys())

    def test_an_id_that_names_none_clears_nothing(self):
        res = recovery.clear(self.ctx, [self.ids['safety:old-n'], 'zzzzzz'])
        self.assertEqual((res['unknown'], res['cleared']), (['zzzzzz'], []))
        self.assertEqual(len(self.keys()), 3)

    def test_a_keychain_that_refuses_keeps_it_listed(self):
        self.rig.kc.rule('delete', service=vault.SERVICE_RECOVERY, account='safety:old-n', why='locked', times=1)
        res = recovery.clear(self.ctx, [self.ids['safety:old-n']])
        self.assertEqual([(r['key'], why) for r, why in res['failed']], [('safety:old-n', 'locked')])
        self.assertIn('safety:old-n', self.keys())

    def test_nothing_is_cleared_while_the_account_list_cant_be_read(self):
        for path in unrestorable(self.ctx):
            with open(path, 'w') as f:
                f.write('{not json')
        res = recovery.clear(self.ctx, everything=True)
        self.assertEqual((len(res['kept']), res['cleared']), (3, []))

    def test_through_the_api(self):
        with context.use(self.ctx):
            rows = api.leftovers()
            self.assertEqual(len(rows), 3)
            res = api.clear_leftovers([self.ids['safety:old-n']])
        self.assertEqual([r['key'] for r in res['cleared']], ['safety:old-n'])


# ---------- removing an account drops what it dropped before the rule had a name ----------

def dropped_before(doc, aid, entries, journal):
    """roster._drop_recovery as it was written before recovery.needed existed: the keys it dropped."""
    r, j = journal
    pending = j.get('safety_key') if r.kind == PRESENT and isinstance(j, dict) else None
    theirs = set(e.get('key') for e in entries if e.get('names') and tokens._held_by_a_slot(doc, e))
    keys = set(e['key'] for e in entries if e.get('account_id') == aid and isinstance(e.get('key'), str))
    keys.add('successor:' + aid)
    if r.kind == UNREADABLE:
        keys = set(k for k in keys if not k.startswith('safety:'))
    return sorted(keys - theirs - set([pending]))


class TheOneRule(unittest.TestCase):
    def test_removing_an_account_drops_the_same_copies_over_many_cases(self):
        rnd = random.Random(20261125)
        ctx = mock.Mock()
        for case in range(4000):
            ids = ['uuid-%d' % i for i in range(rnd.randint(1, 4))]
            fps = ['fp-%d' % i for i in range(5)]
            doc = {'accounts': dict((i, {'uuid': i, 'fp': rnd.choice(fps + [None])}) for i in ids)}
            entries = {}                # as the vault keeps them: one entry a key; a successor's key names its account
            for n in range(rnd.randint(0, 6)):
                if rnd.random() < 0.5:
                    key, owner = 'safety:' + rnd.choice(fps), rnd.choice(ids + ['uuid-x', None])
                else:
                    owner = rnd.choice(ids + ['uuid-x'])
                    key = 'successor:' + owner
                entries[key] = {'key': key, 'account_id': owner,
                                'reason': rnd.choice((tokens.CONFLICT, 'save failed: locked', 'before switching')),
                                'supersedes': rnd.choice(fps + [None]), 'names': rnd.choice(ids + [None, None])}
            entries = list(entries.values())
            journal = rnd.choice((Read(ABSENT), Read(UNREADABLE, why='unparseable'),
                                  Read(PRESENT, value='x')))
            j = {'safety_key': rnd.choice([e['key'] for e in entries] + ['safety:fp-0', None])} \
                if journal.kind == PRESENT else None
            aid = rnd.choice(ids)
            doc['accounts'].pop(aid)                            # remove() takes it off the list first
            want = dropped_before(doc, aid, entries, (journal, j))
            got = []
            with mock.patch.object(vault, 'stashed', lambda c: list(entries)), \
                    mock.patch.object(vault, 'drop_stash', lambda c, key: got.append(key)), \
                    mock.patch.object(switcher, 'read_journal', lambda c: (journal, j)):
                roster._drop_recovery(ctx, doc, aid)
            self.assertEqual(got, want, (case, doc, aid, entries, journal, j))


if __name__ == '__main__':
    unittest.main()
