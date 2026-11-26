"""Fakes for the keep tests: a made-up Mac (fakes_accounts.Rig, with a Keychain in a file so a child process
shares it), a made-up Claude Code folder with every kind of thing Claude Code keeps there, and a stand-in `claude`
that records what it was given and what its config folder holds, and does what a test asks inside that folder (write
through a link, replace a link with a real folder, wait to be killed). The real `claude` is never run."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import os
import sys

from fakes_accounts import FileKeychain, Rig

ORG_A = 'org-aaaa-0000-4000-8000-00000000000a'      # long enough to be an organization id to chatfile.py
ORG_B = 'org-bbbb-0000-4000-8000-00000000000b'

FAKE_CLAUDE = r'''#!%(python)s
import json, os, shutil, sys, time
spec = json.load(open(%(spec)r))
cfg = os.environ.get('CLAUDE_CONFIG_DIR')
rec = {'argv': sys.argv[1:], 'cwd': os.getcwd(), 'pid': os.getpid(),
       'env': dict((k, os.environ.get(k)) for k in ('CLAUDE_CONFIG_DIR', 'CLAUDE_SECURESTORAGE_CONFIG_DIR',
                                                    'ANTHROPIC_API_KEY', 'CLAUDE_CODE_OAUTH_TOKEN'))}
def listing(top, depth):
    out = {}
    for name in sorted(os.listdir(top)):
        p = os.path.join(top, name)
        if os.path.islink(p):
            out[name] = ['link', os.readlink(p)]
        elif os.path.isdir(p):
            out[name] = ['dir', listing(p, depth - 1) if depth > 1 else None]
        else:
            out[name] = ['file']
    return out
if cfg:
    rec['folder'] = listing(cfg, 2)
    try:
        rec['claude_json'] = json.load(open(os.path.join(cfg, '.claude.json')))
    except (OSError, ValueError):
        rec['claude_json'] = None
base = cfg or os.path.join(os.environ.get('HOME', '/nonexistent'), '.claude')
rec['found'] = {}
for op in spec.get('do', []):
    kind, rel = op[0], op[1]
    p = os.path.join(base, rel)
    if kind == 'write':
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w') as f:
            f.write(op[2])
    elif kind == 'append':
        with open(p, 'a') as f:
            f.write(op[2])
    elif kind == 'found':
        rec['found'][rel] = os.path.isfile(p)
    elif kind == 'unlink':
        os.unlink(p)
    elif kind == 'dir':                       # a real folder where the link was, with a file in it
        os.makedirs(p)
        with open(os.path.join(p, 'inside.txt'), 'w') as f:
            f.write('made in the run')
    elif kind == 'file':
        with open(p, 'w') as f:
            f.write(op[2])
    elif kind == 'link':
        os.symlink(op[2], p)
with open(spec['record'], 'w') as f:
    json.dump(rec, f)
if spec.get('started'):
    open(spec['started'], 'w').close()
end = time.time() + 60
while spec.get('wait') and not os.path.exists(spec['wait']) and time.time() < end:
    time.sleep(0.05)
if spec.get('done'):
    open(spec['done'], 'w').close()
sys.exit(spec.get('exit', 0))
'''

# What a person's Claude Code folder can hold: (relative path, contents); a path ending in / is an empty folder.
SHARED = [('projects/-work-site/0aa0aa0a-0000-4000-8000-000000000001.jsonl', '{"type":"user"}\n'),
          ('projects/-work-site/memory/MEMORY.md', '- a note\n'),
          ('history.jsonl', '{"display":"hi"}\n'), ('paste-cache/p1.txt', 'pasted'),
          ('file-history/s1/f@v1', 'before'), ('tasks/list1/1.json', '{}'), ('plans/plan.md', '# plan'),
          ('CLAUDE.md', '# house rules\n'), ('rules/style.md', 'short words'), ('settings.json', '{"model": "x"}'),
          ('keybindings.json', '{}'), ('themes/calm.json', '{}'), ('agents/reviewer.md', 'agent'),
          ('commands/ship.md', 'ship'), ('output-styles/plain.md', 'plain'), ('workflows/w.js', '//'),
          ('agent-memory/reviewer/MEMORY.md', 'm'), ('hooks/check.sh', '#!/bin/sh\n'),
          ('skills/graph/SKILL.md', 'skill one'), ('skills/notes/SKILL.md', 'skill two'),
          ('plugins/cache/p/1.0/plugin.json', '{}'), ('plugins/marketplaces/m/x.json', '{}'),
          ('plugins/data/p/state.json', '{}'),
          ('plugins/installed_plugins.json', '{"plugins": {}}'), ('plugins/known_marketplaces.json', '{}')]
NEVER = [('.credentials.json', '{"claudeAiOauth": {"refreshToken": "sk-ant-ort01-never"}}'),
         ('backups/.claude.json.backup.1', '{}'), ('remote-settings.json', '{}'), ('policy-limits.json', '{}'),
         ('policy-limits.json.stamp.json', '{}'), ('stats-cache.json', '{}'), ('mcp-needs-auth-cache.json', '{}'),
         ('sessions/123.json', '{}'), ('daemon/roster.json', '{}'), ('jobs/j1/state.json', '{}'),
         ('chrome/host.json', '{}'), ('telemetry/t.json', '{}'), ('shell-snapshots/s.sh', ''),
         ('session-env/s/env', ''), ('debug/d.txt', ''), ('cache/changelog.md', ''), ('ide/1.lock', '{}'),
         ('statsig/s', ''), ('todos/t.json', '[]'), ('skills/synced/org_acct/SKILL.md', 'synced skill'),
         ('skills/.trash/old/SKILL.md', 'trashed'), ('plugins/synced/s/plugin.json', '{}'),
         ('plugins/.trash/x', ''), ('plugins/store/x', ''), ('plugins/blocklist.json', '{}'),
         ('plugins/plugin-directory-cache-v2.json', '{}')]

# The folder a run gets: name -> ('link', what it leads to under the person's folder) or ('dir', {...}) / ('file',)
LINK_NAMES = ['projects', 'history.jsonl', 'paste-cache', 'file-history', 'tasks', 'plans', 'CLAUDE.md', 'rules',
              'settings.json', 'keybindings.json', 'themes', 'agents', 'commands', 'output-styles', 'workflows',
              'agent-memory', 'hooks']


# ---------- chat files, in the shapes chatfile.py reads (made-up ids and folders) ----------

def owner_line(org, account, cid):
    return json.dumps({'type': 'session-owner', 'organizationUuid': org, 'accountUuid': account, 'sessionId': cid})


def org_line(org, cid, cwd, ts='2026-11-18T02:47:45.569Z'):
    return json.dumps({'type': 'attachment', 'attachment': {'type': 'credential_org', 'organizationUuid': org},
                       'timestamp': ts, 'sessionId': cid, 'cwd': cwd})


def user_line(cid, cwd, text='hello'):
    return json.dumps({'type': 'user', 'sessionId': cid, 'cwd': cwd, 'message': {'role': 'user', 'content': text}})


def reply_line(cid, cwd, stop='end_turn'):
    content = [{'type': 'text', 'text': 'ok'}]
    if stop == 'tool_use':
        content.append({'type': 'tool_use', 'id': 'toolu_1', 'name': 'Bash', 'input': {}})
    return json.dumps({'type': 'assistant', 'sessionId': cid, 'cwd': cwd,
                       'message': {'role': 'assistant', 'stop_reason': stop, 'content': content}})


def write_chat(cc, project, cid, lines, age=3600.0):
    """A chat file under <cc>/projects/<project>/<cid>.jsonl, last written `age` seconds ago."""
    import time
    folder = os.path.join(cc, 'projects', project)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, cid + '.jsonl')
    with open(path, 'w') as f:
        f.write(''.join(line + '\n' for line in lines))
    t = time.time() - age
    os.utime(path, (t, t))
    return path


def fill(folder, items):
    for rel, text in items:
        p = os.path.join(folder, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if rel.endswith('/'):
            continue
        with open(p, 'w') as f:
            f.write(text)


def snapshot(top):
    """Everything under a folder, never following a link: {path: ('file', bytes) | ('link', target) | ('dir',)}."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(top, followlinks=False):
        for name in dirnames + filenames:
            p = os.path.join(dirpath, name)
            rel = os.path.relpath(p, top)
            if os.path.islink(p):
                out[rel] = ('link', os.readlink(p))
            elif os.path.isdir(p):
                out[rel] = ('dir',)
            else:
                with open(p, 'rb') as f:
                    out[rel] = ('file', f.read())
    return out


class KeepRig(object):
    """A Rig with two kept accounts (Claude Code on B), a Keychain in a file and a stand-in claude."""

    def __init__(self):
        self.rig = Rig()
        self.kc_path = os.path.join(self.rig.root, 'keychain.json')
        self.rig.kc = self.rig.sec = FileKeychain(self.kc_path)
        self.rig.ctx.keychain = self.rig.kc
        self.ctx = self.rig.ctx
        self.a_text = self.rig.keep('uuid-a', 'a@example.com', ORG_A)          # kept, idle
        self.b_text = self.rig.keep('uuid-b', 'b@example.com', ORG_B)          # Claude Code is on B
        self.cc = os.path.join(self.rig.home, '.claude')          # the person's Claude Code folder
        self.spec_path = os.path.join(self.rig.root, 'claude-spec.json')
        self.record = os.path.join(self.rig.root, 'claude-record.json')
        self.claude = os.path.join(self.rig.root, 'fake-claude')
        with open(self.claude, 'w') as f:
            f.write(FAKE_CLAUDE % {'python': sys.executable, 'spec': self.spec_path})
        os.chmod(self.claude, 0o700)
        self.ctx.claude = self.claude
        self.spec(record=self.record)

    def close(self):
        self.rig.close()

    def spec(self, **kw):
        try:
            with open(self.spec_path) as f:
                spec = json.load(f)
        except (OSError, ValueError):
            spec = {}
        spec.update(kw)
        with open(self.spec_path, 'w') as f:
            json.dump(spec, f)

    def seen(self):
        with open(self.record) as f:
            return json.load(f)

    def env(self, **extra):
        e = {'PATH': '/usr/bin:/bin', 'HOME': self.rig.home, 'USER': 'tester'}
        e.update(extra)
        return e

    def run_folder(self):
        return os.path.join(self.rig.data, 'run')

    def leftovers(self):
        try:
            return sorted(os.listdir(self.run_folder()))
        except OSError:
            return []
