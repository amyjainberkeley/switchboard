"""A `switchboard run`'s private Claude Code folder: what it shares with the person's own folder, and taking it down.

A run starts Claude Code with CLAUDE_CONFIG_DIR set to a private folder under <data>/run/, so the login it uses is
that folder's own (runas.py). Everything else a chat needs is the person's, and the same on every account: so the
folder holds links to the person's Claude Code folder (CLAUDE_CONFIG_DIR, else ~/.claude), and the run reads and
writes the same chats and memory, and follows the same instructions, settings and hooks.

Linked, each only when it is there (from Claude Code's own description of its folder, code.claude.com/docs/en/
claude-directory.md, checked 2026-11-18 against Claude Code 2.1.309–2.1.325):
  projects                 chats and auto memory: what a run writes there is the point
  history.jsonl            prompt history (up-arrow, Ctrl+R), and paste-cache, the pasted text it refers to
  file-history             checkpoints, so /rewind works in a reopened chat
  tasks, plans             a chat's task lists and the plans it wrote
  CLAUDE.md, rules         the person's instructions
  settings.json            permissions, hooks, status line, environment, model
  keybindings.json, themes the person's keys and themes
  agents, commands, output-styles, workflows, agent-memory   the person's own subagents, commands, styles,
                           workflows and subagent memory
  hooks                    not one of Claude Code's names: where hook scripts are often kept
  skills/<each>            one link per skill, never skills/synced (claude.ai's skills, in a folder named for the
                           account) or skills/.trash
  plugins/cache, plugins/marketplaces, plugins/data   the installed plugins' files
Copied, never linked (a run's rewrite must not reach the person's file):
  plugins/installed_plugins.json, plugins/known_marketplaces.json   the person's plugin records
Never linked or copied: .credentials.json (a login), .claude.json (it carries the identity; runas.py seeds its own,
see seed()), backups/ (copies of .claude.json), plugins/synced and the .trash folders (claude.ai's, per account),
remote-settings.json and policy-limits.json (an organization's, removed at logout), stats-cache.json,
mcp-needs-auth-cache.json, plugins' fetched lists, chrome, telemetry, sessions/ (one file per running window: a run
must not tidy the other windows' files), daemon/ and jobs/ (background chats under the run's own login), and
per-process files, caches and logs (shell-snapshots, session-env, debug, cache, ide, downloads, state, todos, logs).

Every link a run makes points at <the person's folder>/<one of the names above>. A name there that is the person's
own link to somewhere else (a settings file kept with their dotfiles, a skill a skills manager put in place) is
linked as they set it up: Claude Code follows it in every window. A run is refused, before anything is made, only
when that would let it reach what it must not: an entry that leads back to the folder itself, to a folder above it,
to its login or identity file, or into Switchboard's own folder; or settings that would run it on an API key
instead of the account's login. A projects folder that isn't there yet is made first, so a run's chats are never
left in its private folder.

Taking it down (take_down) never follows a link: each folder is opened with O_NOFOLLOW and emptied through its own
descriptor, so a link found anywhere, or put there meanwhile, is removed as a link. The run's record lists what was
linked. If any of those is no longer a link (something put a real file or folder there), nothing is emptied: only
the run's own login file goes, and the folder is kept for the person to look through.

What Claude Code 2.1.309 does with these links: its file writer refuses to write
through a link ("Refusing to write through symlink") unless the caller allows it, and its settings writer then
writes the real file it leads to. So its own writes never put a real file where a link was.
"""
import errno
import copy
import os
import stat

from . import ccfiles, facts
from .api import AccountsError

LINKED = ('projects', 'history.jsonl', 'paste-cache', 'file-history', 'tasks', 'plans', 'CLAUDE.md', 'rules',
          'settings.json', 'keybindings.json', 'themes', 'agents', 'commands', 'output-styles', 'workflows',
          'agent-memory', 'hooks')
EACH = ('skills',)                                  # a real folder in the run, one link per entry
NOT_EACH = ('synced', '.trash')                     # claude.ai's synced items and what its sync set aside
PLUGINS = 'plugins'
PLUGINS_LINKED = ('cache', 'marketplaces', 'data')
PLUGINS_COPIED = ('installed_plugins.json', 'known_marketplaces.json')
MADE = ('projects',)                               # made in the person's folder when missing, then linked
# The chat history, of LINKED: chats and the auto memory kept with them, prompt history and the pasted text it points
# to, checkpoints, and a chat's task lists and plans. A run with its history apart (`run --no-history`) links none of
# them, so Claude Code writes them in the run's own folder, and they go when it is taken down.
HISTORY = ('projects', 'history.jsonl', 'paste-cache', 'file-history', 'tasks', 'plans')
AUTH_KEYS = ('apiKeyHelper',)                       # settings that put Claude Code on a key instead of the login
COPY_MAX = 16 << 20
OWN_FILES = (facts.CREDENTIALS_FILE,)               # the run's own login: removed even from a folder that is kept
BARE = 'Add --bare to run without your Claude Code folder.'


def source(ctx):
    """The person's own Claude Code folder: CLAUDE_CONFIG_DIR, else ~/.claude."""
    return os.path.abspath(facts.config_home(ctx.env, ctx.home))


def _inside(path, folder):
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def plan(ctx, history=True):
    """What a new run folder gets from the person's Claude Code folder: [(name in the run folder, 'link', 'make' (a
    folder to make there, then link) or 'copy', its source)], only for what is there. Raises AccountsError, before
    anything is made, when the run must not share it (see the module's docstring). history False: nothing of
    HISTORY."""
    src = source(ctx)
    try:
        st = os.stat(src)
    except OSError:
        return []                                   # no Claude Code folder yet: nothing to share
    if not stat.S_ISDIR(st.st_mode):
        return []
    real_src = os.path.realpath(src)
    data = os.path.realpath(ctx.data_dir)
    home = os.path.realpath(ctx.home)
    if real_src in (os.sep, home) or _inside(real_src, data) or _inside(data, real_src):
        raise AccountsError('other', "Claude Code's folder here is %s, which a run can't share. %s" % (src, BARE))
    never = set(os.path.realpath(os.path.join(src, n)) for n in (facts.CREDENTIALS_FILE, '.claude.json', 'backups'))
    never.add(os.path.realpath(facts.global_config(ctx.env, ctx.home)))
    out = []

    def take(rel, how):
        path = os.path.join(src, *rel.split('/'))
        if not _inside(os.path.normpath(path), os.path.normpath(src)) or path == src:
            raise AccountsError('other', '%s is not a name in your Claude Code folder. %s' % (rel, BARE))
        try:
            os.lstat(path)
        except OSError:
            if how == 'make':
                out.append((rel, how, path))
            return
        if not os.path.exists(path):
            return                                  # the person's own link that leads nowhere: nothing to share
        real = os.path.realpath(path)
        if _inside(real, data):
            raise AccountsError('other', "%s in your Claude Code folder leads into Switchboard's own folder (%s), so "
                                         "a run would not link it. %s" % (rel, real, BARE))
        if _inside(real_src, real) or real in never:
            raise AccountsError('other', '%s in your Claude Code folder leads to %s: through it a run could reach your '
                                         'own login and identity, so it would not link it. %s' % (rel, real, BARE))
        if how == 'copy' and not os.path.isfile(path):
            return
        out.append((rel, 'link' if how == 'make' else how, path))

    for name in LINKED:
        if not history and name in HISTORY:
            continue
        take(name, 'make' if name in MADE else 'link')
    for name in EACH:
        try:
            entries = sorted(os.listdir(os.path.join(src, name)))
        except OSError:
            entries = []
        for entry in entries:
            if entry in NOT_EACH or entry.startswith('.'):
                continue
            take(name + '/' + entry, 'link')
    for name in PLUGINS_LINKED:
        take(PLUGINS + '/' + name, 'link')
    for name in PLUGINS_COPIED:
        take(PLUGINS + '/' + name, 'copy')
    _no_key(out)
    return out


def _no_key(items):
    """Refuse settings that would put the run on an API key, a token, a cloud provider or a profile instead of the
    account's own login."""
    for rel, how, path in items:
        if rel != 'settings.json':
            continue
        try:
            with open(path, 'rb') as f:
                doc = ccfiles.loads_strict(f.read(4 << 20).decode('utf-8'))
        except (OSError, ValueError, UnicodeDecodeError):
            return                                  # Claude Code says itself when it can't read its settings
        env = doc.get('env') if isinstance(doc, dict) else None
        env = env if isinstance(env, dict) else {}
        found = [k for k in facts.AUTH_OVERRIDES + facts.PROFILES if env.get(k)]
        found += [k for k in facts.PROVIDERS if facts.turned_on(env.get(k))]
        found += [k for k in AUTH_KEYS if isinstance(doc, dict) and doc.get(k)]
        if found:
            raise AccountsError('other', "Your Claude Code settings set %s, so a run would use that, not the "
                                         "account's own login. %s" % (found[0], BARE))


def links_of(items):
    """The names a run folder links, as its record keeps them."""
    return [rel for rel, how, _ in items if how in ('link', 'make')]


def build(folder, items):
    """Make a new run folder's links and copies. Raises OSError (the caller takes the folder down again)."""
    for rel, how, path in items:
        parts = rel.split('/')
        if len(parts) > 1:
            os.makedirs(os.path.join(folder, *parts[:-1]), mode=0o700, exist_ok=True)
        dest = os.path.join(folder, *parts)
        if how == 'make':
            os.makedirs(path, mode=0o700, exist_ok=True)
            os.symlink(path, dest)
        elif how == 'link':
            os.symlink(path, dest)
        else:
            try:
                with open(path, 'rb') as f:
                    raw = f.read(COPY_MAX + 1)
            except FileNotFoundError:
                continue                            # gone since the plan: nothing to copy
            if len(raw) <= COPY_MAX:
                ccfiles.atomic_write(dest, raw, mode=0o600, follow=False)


# ---------- the run's own .claude.json ----------

def _project_keys(cwd):
    """The keys Claude Code may file the folder a run starts in under, in its .claude.json: the folder as given and
    resolved, the top of its git checkout, and, for a git worktree, the checkout it belongs to."""
    keys = []

    def add(p):
        if p and p not in keys:
            keys.append(p)
    if not cwd:
        return keys
    given = os.path.normpath(os.path.abspath(cwd))
    real = os.path.realpath(given)
    add(given)
    add(real)
    p = real
    while True:
        dot = os.path.join(p, '.git')
        if os.path.isdir(dot):
            add(p)
            break
        if os.path.isfile(dot):
            add(p)
            try:
                with open(dot, 'r', encoding='utf-8', errors='replace') as f:
                    line = f.read(4096).strip()
            except OSError:
                line = ''
            if line.startswith('gitdir:'):
                gd = line[len('gitdir:'):].strip()
                gd = gd if os.path.isabs(gd) else os.path.normpath(os.path.join(p, gd))
                marker = os.sep + '.git' + os.sep + 'worktrees' + os.sep
                if marker in gd:
                    main = gd.split(marker, 1)[0]
                    add(main)
                    add(os.path.realpath(main))
            break
        up = os.path.dirname(p)
        if up == p:
            break
        p = up
    return keys


def seed(ctx, identity_block, cwd, bare):
    """The run's .claude.json: the target account's identity block, onboarding done and the person's theme (bare or
    not), and, unless bare, copies of what a chat needs from the person's own: the MCP servers they added for
    every folder (mcpServers), and the entry for the folder the run starts in (projects[<folder>]: trust, allowed
    tools, that folder's MCP servers). Nothing else from that file: no other folder, no cache, no key, no identity."""
    out = {'oauthAccount': identity_block, 'hasCompletedOnboarding': True, 'theme': 'dark'}
    try:
        _, _, cfg = ccfiles.read_config(ctx.env, ctx.home)
    except Exception:  # noqa: BLE001 - the person's config is a nicety here, never a reason to stop
        cfg = None
    if not isinstance(cfg, dict):
        return out
    if isinstance(cfg.get('theme'), str):
        out['theme'] = cfg['theme']
    if bare:
        return out
    if isinstance(cfg.get('mcpServers'), dict) and cfg['mcpServers']:
        out['mcpServers'] = copy.deepcopy(cfg['mcpServers'])
    projects = cfg.get('projects')
    if isinstance(projects, dict):
        mine = dict((k, copy.deepcopy(projects[k])) for k in _project_keys(cwd) if isinstance(projects.get(k), dict))
        if mine:
            out['projects'] = mine
    return out


# ---------- taking it down ----------

_DIR = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | os.O_NOFOLLOW


def _remove_tree(top):
    """Remove a folder and all in it without ever following a link. Each folder is opened with O_NOFOLLOW and emptied
    through its own descriptor (scandir, unlink and rmdir relative to it), so a link, found or put in place meanwhile,
    is only ever removed as a link."""
    parent = os.open(os.path.dirname(os.path.abspath(top)), os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        _remove_in(parent, os.path.basename(os.path.abspath(top)))
    finally:
        os.close(parent)


def _remove_in(parent, name):
    try:
        fd = os.open(name, _DIR, dir_fd=parent)
    except OSError as e:
        if e.errno in (errno.ELOOP, errno.ENOTDIR):
            os.unlink(name, dir_fd=parent)          # a link or a file now: removed as what it is
            return
        raise
    try:
        with os.scandir(fd) as it:
            entries = [(e.name, e.is_dir(follow_symlinks=False)) for e in it]
        for child, is_dir in entries:
            if is_dir:
                _remove_in(fd, child)
            else:
                os.unlink(child, dir_fd=fd)
    finally:
        os.close(fd)
    os.rmdir(name, dir_fd=parent)


def _changed(folder, links):
    """The recorded links that are something else now: a real file or folder (or a real folder above one)."""
    out = []
    for rel in links or ():
        parts = rel.split('/') if isinstance(rel, str) else []
        if not parts or any(p in ('', '.', '..') for p in parts):
            continue                                # not a name a run folder was given: never one of ours
        path = folder
        for i, part in enumerate(parts):
            path = os.path.join(path, part)
            try:
                st = os.lstat(path)
            except FileNotFoundError:
                break                               # gone: nothing of ours is there
            except OSError:
                out.append(rel)
                break
            last = i == len(parts) - 1
            if last and not stat.S_ISLNK(st.st_mode):
                out.append(rel)
            elif not last and (stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode)):
                out.append(rel)                     # a folder above it was replaced: nothing below is looked into
                break
    return out


def take_down(folder, links, run_dir):
    """Remove a run's private folder, never following a link. Returns (what, detail):
    ('gone', None) removed (or already gone); ('kept', [names]) kept because those names are no longer the links the
    run made (only its own login file was removed); ('kept', 'link') the folder itself is a link now (left alone);
    ('outside', None) the folder is not under <data>/run (left alone); ('failed', why) it could not all be removed."""
    try:
        st = os.lstat(folder)
    except FileNotFoundError:
        return 'gone', None
    except OSError as e:
        return 'failed', e.__class__.__name__
    real_run = os.path.realpath(run_dir)
    parent = os.path.realpath(os.path.dirname(os.path.abspath(folder)))
    if not _inside(parent, real_run):
        return 'outside', None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        return 'kept', 'link'
    changed = _changed(folder, links)
    if changed:
        for name in OWN_FILES:
            p = os.path.join(folder, name)
            try:
                if not stat.S_ISDIR(os.lstat(p).st_mode):
                    os.unlink(p)
            except OSError:
                pass
        return 'kept', changed
    try:
        _remove_tree(folder)
    except OSError as e:
        return 'failed', e.__class__.__name__
    return 'gone', None
