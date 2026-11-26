"""`switchboard run NAME` carries the person's Claude Code context, and taking its private folder
down never reaches through a link (runas.py, runfolder.py).

The real `claude` is never run (fakes_run' stand-in records what it was given and does what a test asks inside
its folder); the Keychain is a stand-in in a file."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import io
import json
import os
import signal
import stat
import subprocess
import sys
import time
import unittest
from unittest import mock

from fakes_run import LINK_NAMES, NEVER, SHARED, KeepRig, fill, snapshot
from switchboard.accounts import api, context, facts, runas, runfolder, vault
from switchboard.accounts.api import AccountsError

HERE = os.path.dirname(os.path.abspath(__file__))
CHAT = 'projects/-work-site/0aa0aa0a-0000-4000-8000-000000000001.jsonl'


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


class WhatARunShares(Case):
    def test_it_links_what_every_account_shares_and_nothing_tied_to_one(self):
        self.full()
        self.assertEqual(self.run_as(), 0)
        folder = self.k.seen()['folder']
        want = dict((name, ['link', os.path.join(self.cc, name)]) for name in LINK_NAMES)
        want['skills'] = ['dir', {'graph': ['link', os.path.join(self.cc, 'skills', 'graph')],
                                  'notes': ['link', os.path.join(self.cc, 'skills', 'notes')]}]
        want['plugins'] = ['dir', {'cache': ['link', os.path.join(self.cc, 'plugins', 'cache')],
                                   'marketplaces': ['link', os.path.join(self.cc, 'plugins', 'marketplaces')],
                                   'data': ['link', os.path.join(self.cc, 'plugins', 'data')],
                                   'installed_plugins.json': ['file'], 'known_marketplaces.json': ['file']}]
        want['.claude.json'] = ['file']
        self.assertEqual(folder, want)               # no login, no backups, no synced items, no org caches, no sessions

    def test_the_plugin_records_are_copies_and_a_rewrite_in_the_run_stays_there(self):
        before = self.full()
        self.k.spec(do=[['file', 'plugins/installed_plugins.json', '{"plugins": {"synced-in-run": {}}}']])
        self.run_as()
        self.assertEqual(snapshot(self.cc), before)

    def test_only_what_is_there_is_linked(self):
        fill(self.cc, [('projects/-x/a.jsonl', '{}\n'), ('CLAUDE.md', 'rules')])
        self.run_as()
        self.assertEqual(self.k.seen()['folder'], {'projects': ['link', os.path.join(self.cc, 'projects')],
                                                   'CLAUDE.md': ['link', os.path.join(self.cc, 'CLAUDE.md')],
                                                   '.claude.json': ['file']})

    def test_something_gone_between_the_look_and_the_making_is_left_out(self):
        fill(self.cc, SHARED)
        items = runfolder.plan(self.ctx)
        os.unlink(os.path.join(self.cc, 'plugins', 'installed_plugins.json'))
        folder = os.path.join(self.rig.data, 'run', 'run-late')
        os.makedirs(folder)
        runfolder.build(folder, items)
        self.assertFalse(os.path.lexists(os.path.join(folder, 'plugins', 'installed_plugins.json')))
        self.assertTrue(os.path.isfile(os.path.join(folder, 'plugins', 'known_marketplaces.json')))

    def test_no_claude_code_folder_yet_is_an_empty_run(self):
        os.rmdir(self.cc)
        self.assertEqual(self.run_as(), 0)
        self.assertEqual(self.k.seen()['folder'], {'.claude.json': ['file']})

    def test_bare_gives_an_empty_folder_with_only_the_login_and_theme(self):
        self.full()
        self.rig.set_config(self.rig.config().get('oauthAccount'), theme='light', mcpServers={'m': {'command': 'x'}})
        self.assertEqual(self.run_as(bare=True), 0)
        seen = self.k.seen()
        self.assertEqual(seen['folder'], {'.claude.json': ['file']})
        self.assertEqual(sorted(seen['claude_json']), ['hasCompletedOnboarding', 'oauthAccount', 'theme'])
        self.assertEqual(seen['claude_json']['theme'], 'light')
        self.assertEqual(seen['claude_json']['oauthAccount']['accountUuid'], 'uuid-a')

    def test_the_run_starts_in_the_folder_it_was_given_with_its_own_login(self):
        self.full()
        self.run_as(argv=['-p', 'hi'])
        seen = self.k.seen()
        self.assertEqual(seen['argv'], ['-p', 'hi'])
        self.assertEqual(os.path.realpath(seen['cwd']), os.path.realpath(self.work))
        cfg = seen['env']['CLAUDE_CONFIG_DIR']
        self.assertTrue(cfg.startswith(self.k.run_folder() + os.sep), cfg)
        self.assertNotEqual(os.path.realpath(cfg), os.path.realpath(self.cc))


class TheSeededConfig(Case):
    def person(self, **other):
        block = self.rig.config().get('oauthAccount')
        root = os.path.realpath(self.work)
        projects = {root: {'hasTrustDialogAccepted': True, 'allowedTools': ['Bash(make:*)'],
                           'mcpServers': {'local': {'command': 'l'}}},
                    '/elsewhere/other': {'hasTrustDialogAccepted': True, 'allowedTools': ['Bash(rm:*)']}}
        self.rig.set_config(block, theme='dark-daltonized', mcpServers={'everywhere': {'command': 'e'}},
                            projects=projects, primaryApiKey='sk-ant-api03-never', userID='u-never',
                            customApiKeyResponses={'approved': ['x']}, s1mAccessCache={'org-b': 1},
                            groveConfigCache={'org-b': 2}, cachedChangelog='...', numStartups=7, **other)
        return projects, root

    def test_it_copies_the_mcp_servers_and_this_folders_entry_and_nothing_else(self):
        projects, root = self.person()
        self.run_as()
        seed = self.k.seen()['claude_json']
        self.assertEqual(sorted(seed), ['hasCompletedOnboarding', 'mcpServers', 'oauthAccount', 'projects', 'theme'])
        self.assertEqual(seed['oauthAccount']['accountUuid'], 'uuid-a')         # the target's identity
        self.assertEqual(seed['oauthAccount']['emailAddress'], 'a@example.com')
        self.assertEqual(seed['theme'], 'dark-daltonized')
        self.assertEqual(seed['mcpServers'], {'everywhere': {'command': 'e'}})
        self.assertEqual(seed['projects'], {root: projects[root]})

    def test_the_git_top_and_a_worktrees_checkout_are_the_same_folder(self):
        repo = os.path.join(self.rig.root, 'repo')
        os.makedirs(os.path.join(repo, '.git', 'worktrees', 'wt1'))
        wt = os.path.join(self.rig.root, 'wt1')
        os.makedirs(os.path.join(wt, 'sub'))
        with open(os.path.join(wt, '.git'), 'w') as f:
            f.write('gitdir: %s\n' % os.path.join(repo, '.git', 'worktrees', 'wt1'))
        rrepo, rwt = os.path.realpath(repo), os.path.realpath(wt)
        self.rig.set_config(self.rig.config().get('oauthAccount'),
                            projects={rrepo: {'allowedTools': ['A']}, rwt: {'allowedTools': ['B']},
                                      os.path.realpath(self.work): {'allowedTools': ['C']}})
        self.run_as(cwd=os.path.join(wt, 'sub'))
        self.assertEqual(self.k.seen()['claude_json']['projects'], {rrepo: {'allowedTools': ['A']},
                                                                    rwt: {'allowedTools': ['B']}})

    def test_the_persons_config_is_not_changed_by_a_run(self):
        self.person()
        before = self.rig.config()
        self.run_as()
        self.assertEqual(self.rig.config(), before)
        self.assertEqual(self.rig.config()['oauthAccount']['accountUuid'], 'uuid-b')


class TakingItDown(Case):
    def test_after_a_normal_end_every_shared_file_is_there_with_what_the_run_wrote_through_the_link(self):
        before = self.full()
        self.k.spec(do=[['write', 'projects/-work-site/0bb0bb0b-0000-4000-8000-000000000002.jsonl', '{"new":1}\n'],
                        ['append', CHAT, '{"more":1}\n'], ['write', 'projects/-work-site/memory/new.md', 'kept'],
                        ['write', 'skills/graph/notes.md', 'added'],
                        ['write', 'backups/.claude.json.backup.run', '{"oauthAccount": "run"}'],
                        ['write', '.credentials.json', '{"claudeAiOauth": {}}']])
        self.assertEqual(self.run_as(), 0)
        want = dict(before)
        want['projects/-work-site/0bb0bb0b-0000-4000-8000-000000000002.jsonl'] = ('file', b'{"new":1}\n')
        want[CHAT] = ('file', before[CHAT][1] + b'{"more":1}\n')
        want['projects/-work-site/memory/new.md'] = ('file', b'kept')
        want['skills/graph/notes.md'] = ('file', b'added')
        self.assertEqual(snapshot(self.cc), want)    # byte for byte; the run's backup and login stayed in its folder
        self.assertEqual(self.k.leftovers(), [])
        self.assertEqual(self.rig.doc()['claims'], {})
        cfg = self.k.seen()['env']['CLAUDE_CONFIG_DIR']
        self.assertIsNone(self.rig.kc.get(facts.keychain_service({'CLAUDE_CONFIG_DIR': cfg}), 'tester'))
        self.assertEqual(self.rig.live_text(), self.k.b_text)             # the live login never moved

    def test_a_link_replaced_by_a_real_folder_is_not_emptied_and_that_is_said(self):
        before = self.full()
        said = []
        self.k.spec(do=[['unlink', 'projects'], ['dir', 'projects'], ['write', '.credentials.json', '{"x": 1}']])
        self.assertEqual(self.run_as(say=said.append), 0)
        cfg = self.k.seen()['env']['CLAUDE_CONFIG_DIR']
        self.assertEqual(snapshot(self.cc), before)
        self.assertTrue(os.path.isfile(os.path.join(cfg, 'projects', 'inside.txt')))      # kept, with what is in it
        self.assertFalse(os.path.lexists(os.path.join(cfg, '.credentials.json')))        # but not its login
        self.assertTrue(os.path.islink(os.path.join(cfg, 'CLAUDE.md')))
        self.assertEqual(self.rig.doc()['claims'], {})                                    # the account is free again
        text = ('The folder of the run as a@example.com was kept at %s: projects in it is no longer the link to your '
                'Claude Code folder that the run made, so nothing in it was removed. Take what you need from it, then '
                'remove it.' % cfg)
        self.assertEqual(said, [text])
        self.assertIn((text, 'warn', {'h': 'problem'}), self.rig.events)

    def test_a_file_link_replaced_by_a_file_is_kept_too(self):
        before = self.full()
        said = []
        self.k.spec(do=[['unlink', 'settings.json'], ['file', 'settings.json', '{"model": "changed in run"}']])
        self.run_as(say=said.append)
        self.assertEqual(snapshot(self.cc), before)
        self.assertEqual(len(said), 1)
        self.assertIn('settings.json in it is no longer the link', said[0])

    def test_a_real_folder_above_its_links_that_became_a_link_is_never_looked_through(self):
        fill(self.cc, SHARED + NEVER)
        os.symlink(os.path.join(self.cc, 'skills', 'notes'), os.path.join(self.cc, 'skills', 'graph-alias'))
        before = snapshot(self.cc)
        said, holder = [], []
        original = runfolder.build

        def build(folder, items):
            original(folder, items)
            holder.append(folder)

        def swap(plain):
            # skills/ is the run's own folder with a link per skill: something puts a link to the person's in its place
            skills = os.path.join(holder[0], 'skills')
            for name in os.listdir(skills):
                os.unlink(os.path.join(skills, name))
            os.rmdir(skills)
            os.symlink(os.path.join(self.cc, 'skills'), skills)
        with mock.patch.object(runfolder, 'build', build):
            self.run_as(say=said.append, before=swap)
        self.assertEqual(snapshot(self.cc), before)              # every skill of the person's is still there, links too
        self.assertEqual(len(said), 1)
        self.assertIn('skills/graph, skills/graph-alias, skills/notes in it are no longer the link', said[0])

    def test_the_folder_itself_replaced_by_a_link_is_left_alone(self):
        before = self.full()
        said = []
        holder = []
        original = runfolder.build

        def build(folder, items):
            original(folder, items)
            holder.append(folder)

        def swap(plain):
            folder = holder[0]
            os.rename(folder, folder + '.moved')
            os.symlink(self.cc, folder)
        with mock.patch.object(runfolder, 'build', build):
            self.run_as(say=said.append, before=swap)
        self.assertEqual(snapshot(self.cc), before)
        self.assertTrue(os.path.islink(holder[0]))
        self.assertEqual(said, ['The folder of the run as a@example.com, %s, is a link now, so it was left as it is.'
                                % holder[0]])

    def test_a_folder_that_becomes_a_link_while_it_is_being_removed_is_removed_as_a_link(self):
        before = self.full()
        folder = os.path.join(self.rig.data, 'run', 'run-race')
        fill(folder, [('own/deeper/f.txt', 'the run made this')])
        real_scandir = os.scandir
        swapped = []

        class Listed(list):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def scandir(arg):
            entries = Listed(real_scandir(arg))          # listed now: 'own' is a real folder
            if not swapped:
                swapped.append(1)                         # then, before it is opened, it becomes a link to the chats
                os.rename(os.path.join(folder, 'own'), os.path.join(self.rig.root, 'own-moved'))
                os.symlink(os.path.join(self.cc, 'projects'), os.path.join(folder, 'own'))
            return entries
        with mock.patch.object(os, 'scandir', scandir):
            self.assertEqual(runfolder.take_down(folder, [], self.k.run_folder()), ('gone', None))
        self.assertTrue(swapped)
        self.assertFalse(os.path.lexists(folder))
        self.assertEqual(snapshot(self.cc), before)

    def test_after_a_crash_reaped_later_every_shared_file_is_there(self):
        before = self.full()
        started = os.path.join(self.rig.root, 'started')
        go = os.path.join(self.rig.root, 'go')
        done = os.path.join(self.rig.root, 'done')
        self.k.spec(do=[['write', 'projects/-work-site/crash.jsonl', 'written before the crash\n']],
                    started=started, wait=go, done=done)
        spec = {'env': self.k.env(), 'home': self.rig.home, 'data': self.rig.data, 'keychain': self.k.kc_path,
                'claude': self.k.claude, 'cwd': self.work}
        child = subprocess.Popen([sys.executable, '-c', CHILD, json.dumps(spec)], cwd=HERE,
                                 env=dict(os.environ, PYTHONPATH=os.pathsep.join([HERE, os.path.dirname(HERE)])))
        try:
            end = time.time() + 90
            while not os.path.exists(started) and time.time() < end and child.poll() is None:
                time.sleep(0.05)
            self.assertTrue(os.path.exists(started), 'the stand-in claude never started')
            os.kill(child.pid, signal.SIGKILL)                  # Switchboard dies mid-run: no finish()
            child.wait(30)
        finally:
            open(go, 'w').close()
        end = time.time() + 30
        while not os.path.exists(done) and time.time() < end:
            time.sleep(0.05)
        claims = self.rig.doc()['claims']
        self.assertIn('uuid-a', claims)                         # left behind by the crash
        self.assertEqual(len(self.k.leftovers()), 1)
        runas.reap_stale_claims(self.ctx)                       # its processes are gone (not in the process table)
        want = dict(before)
        want['projects/-work-site/crash.jsonl'] = ('file', b'written before the crash\n')
        self.assertEqual(snapshot(self.cc), want)
        self.assertEqual(self.k.leftovers(), [])
        self.assertEqual(self.rig.doc()['claims'], {})

    def test_after_an_uninstall_every_shared_file_is_there(self):
        self.full()
        folder = self.dead_run()
        kept = self.dead_run(account='uuid-b', replace='rules')
        before = snapshot(self.cc)
        self.assertEqual(before['projects/-work-site/from-dead-run.jsonl'], ('file', b'x\n'))   # added through a link
        with context.use(self.ctx):
            api.remove_all()                                    # the uninstaller's tidy --all settles every run first
        self.assertFalse(os.path.lexists(folder))
        self.assertTrue(os.path.isdir(os.path.join(kept, 'rules')))             # kept: its link became a folder
        self.assertEqual(snapshot(self.cc), before)
        subprocess.run(['/bin/rm', '-rf', self.rig.data], check=True)            # what uninstall.sh --all does last
        self.assertFalse(os.path.exists(self.rig.data))
        self.assertEqual(snapshot(self.cc), before)

    def dead_run(self, account='uuid-a', replace=None):
        """A run folder made by the real code, whose processes are gone (a crash), as the reaper finds it."""
        items = runfolder.plan(self.ctx)
        folder = os.path.join(self.rig.data, 'run', 'run-dead-%s' % account)
        os.makedirs(folder)
        runfolder.build(folder, items)
        with open(os.path.join(folder, 'projects', '-work-site', 'from-dead-run.jsonl'), 'w') as f:
            f.write('x\n')
        if replace:
            os.unlink(os.path.join(folder, replace))
            os.makedirs(os.path.join(folder, replace))
        with vault.changing(self.ctx) as doc:
            doc['claims'][account] = {'pid': 4242, 'start': 'gone', 'dir': folder, 'at': time.time(),
                                      'links': runfolder.links_of(items)}
        return folder


class Refusals(Case):
    def refused(self, words):
        with self.assertRaises(AccountsError) as e:
            self.run_as()
        self.assertEqual(str(e.exception), words)
        self.assertEqual(self.rig.doc()['claims'], {})
        self.assertEqual(self.k.leftovers(), [])
        self.assertFalse(os.path.exists(self.k.record))          # Claude Code never started
        self.assertEqual(self.run_as(bare=True), 0)              # --bare still runs
        os.unlink(self.k.record)

    def test_an_entry_that_leads_back_to_the_folder_or_above_it_is_refused_before_anything_is_made(self):
        fill(self.cc, SHARED)
        for name, target in (('agents', self.cc), ('hooks', self.rig.home), ('rules', self.rig.root),
                             ('CLAUDE.md', os.path.join(self.cc, '.credentials.json'))):
            with self.subTest(name=name):
                fill(self.cc, [('.credentials.json', '{}')])
                kept = os.path.join(self.rig.root, 'set-aside-' + name)
                os.rename(os.path.join(self.cc, name), kept)
                os.symlink(target, os.path.join(self.cc, name))
                self.refused('%s in your Claude Code folder leads to %s: through it a run could reach your own login '
                             'and identity, so it would not link it. Add --bare to run without your Claude Code folder.'
                             % (name, os.path.realpath(target)))
                os.unlink(os.path.join(self.cc, name))
                os.rename(kept, os.path.join(self.cc, name))

    def test_an_entry_that_leads_into_switchboards_folder_is_refused(self):
        fill(self.cc, SHARED)
        os.makedirs(os.path.join(self.rig.data, 'elsewhere'))
        os.rename(os.path.join(self.cc, 'commands'), os.path.join(self.rig.root, 'set-aside'))
        os.symlink(os.path.join(self.rig.data, 'elsewhere'), os.path.join(self.cc, 'commands'))
        self.refused("commands in your Claude Code folder leads into Switchboard's own folder (%s), so a run would not "
                     "link it. Add --bare to run without your Claude Code folder."
                     % os.path.realpath(os.path.join(self.rig.data, 'elsewhere')))

    def test_settings_that_put_claude_code_on_a_key_are_refused(self):
        fill(self.cc, SHARED)
        for settings, key in (({'env': {'ANTHROPIC_API_KEY': 'sk-ant-api03-made-up'}}, 'ANTHROPIC_API_KEY'),
                              ({'env': {'CLAUDE_CODE_OAUTH_TOKEN': 'made-up'}}, 'CLAUDE_CODE_OAUTH_TOKEN'),
                              ({'apiKeyHelper': '/bin/echo x'}, 'apiKeyHelper')):
            with self.subTest(key=key):
                with open(os.path.join(self.cc, 'settings.json'), 'w') as f:
                    json.dump(settings, f)
                self.refused("Your Claude Code settings set %s, so a run would use that, not the account's own "
                             "login. Add --bare to run without your Claude Code folder." % key)

    def test_the_persons_own_links_elsewhere_are_linked_as_they_are_and_never_followed_when_taken_down(self):
        fill(self.cc, SHARED)
        dotfiles = os.path.join(self.rig.root, 'dotfiles')
        fill(dotfiles, [('rules/style.md', 'from dotfiles'), ('graph/SKILL.md', 'a managed skill'),
                        ('settings.json', '{"model": "y"}')])
        for name, target in (('rules', 'rules'), ('skills/graph', 'graph'), ('settings.json', 'settings.json')):
            p = os.path.join(self.cc, *name.split('/'))
            os.rename(p, os.path.join(self.rig.root, 'old-' + name.replace('/', '-')))
            os.symlink(os.path.join(dotfiles, target), p)
        os.symlink(os.path.join(self.rig.root, 'nowhere'), os.path.join(self.cc, 'skills', 'dead'))
        before, outside = snapshot(self.cc), snapshot(dotfiles)
        self.k.spec(do=[['write', 'rules/added.md', 'written in the run']])
        self.assertEqual(self.run_as(), 0)
        folder = self.k.seen()['folder']
        self.assertEqual(folder['rules'], ['link', os.path.join(self.cc, 'rules')])
        self.assertEqual(sorted(folder['skills'][1]), ['graph', 'notes'])              # the dead link is left out
        self.assertEqual(snapshot(self.cc), before)
        outside['rules/added.md'] = ('file', b'written in the run')
        self.assertEqual(snapshot(dotfiles), outside)

    def test_a_projects_folder_not_there_yet_is_made_so_the_runs_chats_stay(self):
        fill(self.cc, [('CLAUDE.md', 'rules')])
        self.k.spec(do=[['write', 'projects/-work-site/0cc0cc0c-0000-4000-8000-000000000003.jsonl', '{"a":1}\n']])
        self.assertEqual(self.run_as(), 0)
        self.assertEqual(self.k.seen()['folder']['projects'], ['link', os.path.join(self.cc, 'projects')])
        with open(os.path.join(self.cc, 'projects', '-work-site', '0cc0cc0c-0000-4000-8000-000000000003.jsonl')) as f:
            self.assertEqual(f.read(), '{"a":1}\n')
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.cc, 'projects')).st_mode), 0o700)

    def test_a_claude_code_folder_inside_switchboards_own_is_refused(self):
        inside = os.path.join(self.rig.data, 'claude')
        os.makedirs(os.path.join(inside, 'projects'))
        self.rig.env['CLAUDE_CONFIG_DIR'] = inside
        with self.assertRaises(AccountsError) as e:
            runfolder.plan(self.ctx)
        self.assertEqual(str(e.exception), "Claude Code's folder here is %s, which a run can't share. Add --bare to "
                                           "run without your Claude Code folder." % inside)

    def test_a_run_inside_a_run_or_on_a_conflicted_login_is_refused(self):
        self.full()
        self.rig.env['CLAUDE_CONFIG_DIR'] = os.path.join(self.rig.data, 'run', 'run-inner')
        os.makedirs(self.rig.env['CLAUDE_CONFIG_DIR'])
        with self.assertRaises(AccountsError):
            self.run_as()                                       # inside a run
        del self.rig.env['CLAUDE_CONFIG_DIR']
        os.rmdir(os.path.join(self.rig.data, 'run', 'run-inner'))
        with vault.changing(self.ctx) as doc:
            doc['accounts']['uuid-a']['conflict'] = True
        with self.assertRaises(AccountsError):
            self.run_as()                                       # its saved login is another account's
        self.assertEqual(self.k.leftovers(), [])


class HooksInARun(Case):
    """Switchboard's own hooks and status line come into a run through the linked settings. There they see a chat on
    its own login: one line at its start, and nothing about switches."""

    def folder(self):
        from switchboard import session_hooks
        fill(self.cc, SHARED)
        settings = {'hooks': {'SessionStart': [{'hooks': [{'type': 'command', 'command': session_hooks.command()}]}]},
                    'statusLine': {'type': 'command', 'command': 'switchboard-status'}}
        with open(os.path.join(self.cc, 'settings.json'), 'w') as f:
            json.dump(settings, f)
        folder = os.path.join(self.rig.data, 'run', 'run-hooks')
        os.makedirs(folder)
        runfolder.build(folder, runfolder.plan(self.ctx))
        return folder, settings

    def hook(self, folder, event):
        from switchboard import session_hooks
        out = io.StringIO()
        env = {'CLAUDE_CONFIG_DIR': folder, 'HOME': self.rig.home}
        with mock.patch.dict(os.environ, {'HOME': self.rig.home, 'CLAUDE_CONFIG_DIR': folder}):
            session_hooks.hook(stdin=io.BytesIO(json.dumps(event).encode()), env=env, now=time.time(), out=out)
        return out.getvalue()

    def test_the_linked_settings_carry_the_hooks_and_they_say_one_line_and_stay_quiet(self):
        from switchboard import session_hooks, statusline
        folder, settings = self.folder()
        with mock.patch.dict(os.environ, {'HOME': self.rig.home, 'CLAUDE_CONFIG_DIR': folder}):
            path = session_hooks.settings_path()
            self.assertEqual(path, os.path.join(folder, 'settings.json'))
            with open(path) as f:
                self.assertEqual(json.load(f), settings)          # the person's own file, through the link
            self.assertTrue(statusline.own_profile({'CLAUDE_CONFIG_DIR': folder}))
        said = self.hook(folder, {'hook_event_name': 'SessionStart', 'session_id': 's-run', 'source': 'resume'})
        ctx = json.loads(said)['hookSpecificOutput']['additionalContext']
        self.assertTrue(ctx.startswith('Switchboard: this chat runs on its own login'), ctx)
        quiet = self.hook(folder, {'hook_event_name': 'UserPromptSubmit', 'session_id': 's-run', 'prompt': 'hi'})
        self.assertEqual(quiet, '')


CHILD = r'''
import json, os, sys
import fakes_accounts
from switchboard.accounts import context, net, runas
spec = json.loads(sys.argv[1])
class NoNetwork:
    def resolve(self, host, deadline_s):
        return False
    def request(self, *a, **kw):
        raise net.NetError('dns', False, 'no network in this child')
ctx = context.Context(env=spec['env'], home=spec['home'], data_dir=spec['data'],
                      keychain=fakes_accounts.FileKeychain(spec['keychain']), http=NoNetwork(),
                      events=lambda *a: None, log=lambda text: None, disk_free=lambda p: 10 ** 12,
                      proc_start=lambda pid: 'child-start', pid_alive=lambda pid: True, claude=spec['claude'])
runas.run_as(ctx, 'uuid-a', [], cwd=spec['cwd'], env=spec['env'], timeout=120)
'''


if __name__ == '__main__':
    unittest.main()
