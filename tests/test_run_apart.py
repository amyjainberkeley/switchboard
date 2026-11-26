"""`switchboard run NAME --no-history` and `--strict` (runas.py, runfolder.py).

An apart run links everything a usual run links except the chat history, so what it writes there stays in its
private folder and goes with it; a strict run never starts on the person's own folder. The real `claude` is never run
(fakes_run's stand-in records what it was given and does what a test asks inside its folder); the Keychain is a
stand-in in a file."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import os
import unittest

from fakes_run import LINK_NAMES, NEVER, SHARED, KeepRig, fill, snapshot
from switchboard.accounts import runas, runfolder
from switchboard.accounts.api import AccountsError

HISTORY = ['projects', 'history.jsonl', 'paste-cache', 'file-history', 'tasks', 'plans']


class Case(unittest.TestCase):
    def setUp(self):
        self.k = KeepRig()
        self.rig, self.ctx, self.cc = self.k.rig, self.k.ctx, self.k.cc
        self.work = os.path.join(self.rig.root, 'work', 'site')
        os.makedirs(self.work)

    def tearDown(self):
        self.k.close()

    def full(self):
        fill(self.cc, SHARED + NEVER)
        return snapshot(self.cc)

    def run_as(self, target='uuid-a', argv=(), **kw):
        kw.setdefault('env', self.k.env())
        kw.setdefault('timeout', 120)
        kw.setdefault('cwd', self.work)
        return runas.run_as(self.ctx, target, list(argv), **kw)

    def started(self):
        return os.path.exists(self.k.record)


class WhatAnApartRunHas(Case):
    def want(self, history):
        names = [n for n in LINK_NAMES if history or n not in HISTORY]
        want = dict((name, ['link', os.path.join(self.cc, name)]) for name in names)
        want['skills'] = ['dir', {'graph': ['link', os.path.join(self.cc, 'skills', 'graph')],
                                  'notes': ['link', os.path.join(self.cc, 'skills', 'notes')]}]
        want['plugins'] = ['dir', {'cache': ['link', os.path.join(self.cc, 'plugins', 'cache')],
                                   'marketplaces': ['link', os.path.join(self.cc, 'plugins', 'marketplaces')],
                                   'data': ['link', os.path.join(self.cc, 'plugins', 'data')],
                                   'installed_plugins.json': ['file'], 'known_marketplaces.json': ['file']}]
        want['.claude.json'] = ['file']
        return want

    def test_it_links_all_a_usual_run_does_but_the_history(self):
        self.full()
        self.assertEqual(self.run_as(apart=True), 0)
        self.assertEqual(self.k.seen()['folder'], self.want(history=False))

    def test_beside_a_usual_run_and_a_bare_one(self):
        self.full()
        got = {}
        for name, kw in (('usual', {}), ('apart', {'apart': True}), ('bare', {'bare': True})):
            self.run_as(**kw)
            got[name] = self.k.seen()['folder']
        self.assertEqual(got['usual'], self.want(history=True))
        self.assertEqual(sorted(set(got['usual']) - set(got['apart'])), sorted(HISTORY))
        self.assertEqual(set(got['apart']) - set(got['usual']), set())
        self.assertEqual(got['bare'], {'.claude.json': ['file']})

    def test_its_mcp_servers_and_this_folders_entry_still_come(self):
        root = os.path.realpath(self.work)
        self.rig.set_config(self.rig.config().get('oauthAccount'), mcpServers={'everywhere': {'command': 'e'}},
                            projects={root: {'hasTrustDialogAccepted': True}})
        self.run_as(apart=True)
        seed = self.k.seen()['claude_json']
        self.assertEqual(seed['mcpServers'], {'everywhere': {'command': 'e'}})
        self.assertEqual(seed['projects'], {root: {'hasTrustDialogAccepted': True}})

    def test_the_plan_leaves_out_exactly_the_history(self):
        fill(self.cc, SHARED)
        usual = [rel for rel, _, _ in runfolder.plan(self.ctx)]
        apart = [rel for rel, _, _ in runfolder.plan(self.ctx, history=False)]
        self.assertEqual([rel for rel in usual if rel not in apart], HISTORY)
        self.assertEqual(sorted(runfolder.HISTORY), sorted(HISTORY))


class NothingOfItIsLeft(Case):
    def test_what_it_writes_goes_when_it_ends_and_your_folder_is_as_it_was(self):
        before = self.full()
        self.k.spec(do=[['write', 'projects/-work-site/0cc0cc0c-0000-4000-8000-000000000003.jsonl', '{"new":1}\n'],
                        ['write', 'projects/-work-site/memory/run.md', 'apart'],
                        ['append', 'history.jsonl', '{"display":"apart"}\n'],
                        ['write', 'file-history/s9/f@v1', 'x'], ['write', 'tasks/l9/1.json', '{}'],
                        ['found', 'CLAUDE.md'], ['found', 'settings.json'], ['found', 'skills/graph/SKILL.md']])
        self.assertEqual(self.run_as(apart=True), 0)
        self.assertEqual(self.k.seen()['found'], {'CLAUDE.md': True, 'settings.json': True,
                                                  'skills/graph/SKILL.md': True})
        self.assertEqual(snapshot(self.cc), before)                 # byte for byte
        self.assertEqual(self.k.leftovers(), [])
        self.assertEqual(self.rig.doc()['claims'], {})

    def test_no_projects_folder_is_made_in_yours(self):
        fill(self.cc, [('CLAUDE.md', 'rules')])
        self.k.spec(do=[['write', 'projects/-x/a.jsonl', '{}\n']])
        self.assertEqual(self.run_as(apart=True), 0)
        self.assertFalse(os.path.lexists(os.path.join(self.cc, 'projects')))
        self.assertEqual(self.k.leftovers(), [])


class Strict(Case):
    def test_on_the_account_claude_code_is_on_it_starts_nothing(self):
        self.full()
        for kw in ({'strict': True}, {'strict': True, 'apart': True}):
            with self.subTest(**kw):
                with self.assertRaises(AccountsError) as e:
                    self.run_as(target='uuid-b', **kw)
                self.assertEqual(e.exception.kind, 'in_use')
                self.assertIn('b@example.com is the account Claude Code is on', str(e.exception))
                self.assertFalse(self.started())
                self.assertEqual(self.k.leftovers(), [])
                self.assertEqual(self.rig.doc()['claims'], {})
        self.assertIn('kept', str(e.exception))                     # apart: it says the chat would be kept

    def test_on_another_account_it_runs_apart_as_usual(self):
        self.full()
        self.assertEqual(self.run_as(target='uuid-a', strict=True), 0)
        cfg = self.k.seen()['env']['CLAUDE_CONFIG_DIR']
        self.assertTrue(cfg.startswith(self.k.run_folder() + os.sep), cfg)

    def test_without_it_the_account_in_use_runs_on_your_own_folder_as_before(self):
        self.full()
        plain = []
        self.assertEqual(self.run_as(target='uuid-b', apart=True, before=plain.append), 0)
        self.assertIsNone(self.k.seen()['env']['CLAUDE_CONFIG_DIR'])
        self.assertEqual(plain, [True])


if __name__ == '__main__':
    unittest.main()
