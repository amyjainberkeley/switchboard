"""codegraph, installed by Switchboard (colbymchenry/codegraph, MIT), and project maps.

Not `codegraph install`: under --yes that writes to every agent it finds and adds a prompt hook, and `codegraph
uninstall` removes from every agent and deletes the program whoever added it (its src/installer, read 2026-11-14).
Switchboard does what codegraph's own install.sh does, with a pinned checksum it lacks, and connects only Claude Code:

  ~/.codegraph/versions/<version>/   the release (its own Node inside), checked against pins.json, then unpacked
  ~/.codegraph/current               a link to it, as install.sh makes it (so `codegraph upgrade` still works)
  ~/.local/bin/codegraph             a link, only when nothing of that name is there
  ~/.claude.json  mcpServers.codegraph   the entry `codegraph install --print-config claude` prints, with the
                                     command as its full path, so Claude Code finds it whatever its PATH is
  <Claude Code folder>/settings.json  permissions.allow "mcp__codegraph__*" (its tools run without asking)
  <Claude Code folder>/CLAUDE.md     the code map block (packs/codemap.py): ask the map before reading files
No prompt hook and no CODEGRAPH section. Claude Code writes ~/.claude.json while it runs; the entry goes in under
Claude Code's own lock on that file, is read back, and is tried again once.

Undo takes out exactly those, and codegraph's own small files in ~/.codegraph when Switchboard made that folder
(telemetry.json from its `telemetry off`); a version `codegraph upgrade` added since stays, with the link to it, and
then the folder too. Project maps (<project>/.codegraph/) stay unless asked. Claude Code sessions running at the time
keep their codegraph until they end (its daemon runs from the files it opened, and ends by itself 5 minutes after its
last session); nothing is killed.

Project maps: map_project() runs `codegraph init <folder> -y` (its .codegraph/ ignores itself in git),
never in /, the home folder, or the home folder's Desktop, Documents, Downloads themselves, and never in its Library
or hidden folders (~/.ssh, ~/.config) or anything inside them. When Switchboard's codegraph is not here, the codegraph
or graphify on the PATH is used (the person's own, as their shell would run it).
"""
import json
import os
import shutil
import time

from switchboard.packs import claudemem_install as cmi
from switchboard.packs import codemap, common, facts, fetch, runner

ALLOW = 'mcp__codegraph__*'
STEPS = (('download', 'Download', 45), ('verify', 'Verify', 10), ('unpack', 'Unpack', 15), ('connect', 'Connect', 20),
         ('check', 'Check', 10))
REFUSED_ROOTS = ('Desktop', 'Documents', 'Downloads', 'Library', 'Applications', 'Movies', 'Music', 'Pictures',
                 'Public')


def where(cd=None, home=None):
    cd = common.claude_dir(cd)
    home = home or common.home()
    root = os.path.join(home, '.codegraph')
    return {'cd': cd, 'home': home, 'root': root, 'versions': os.path.join(root, 'versions'),
            'current': os.path.join(root, 'current'), 'bin_link': os.path.join(home, '.local', 'bin', 'codegraph'),
            'claude_json': codemap._claude_json(cd, home), 'settings': os.path.join(cd, 'settings.json')}


def program(w):
    return os.path.join(w['current'], 'bin', 'codegraph')


def mcp_entry(w):
    """codegraph's own entry for Claude Code (its src/installer/targets/claude.ts and shared.ts), with the command
    as a full path."""
    return {'type': 'stdio', 'command': program(w), 'args': ['serve', '--mcp'], 'alwaysLoad': True}


def record(state_path=None):
    try:
        st, _ = common.load_state(state_path)
    except common.Refused:
        return None
    return (st.get('installs') or {}).get('codegraph')


def save_record(rec, state_path=None):
    sp = state_path or common.default_state_path()
    st, _ = common.load_state(sp)
    st.setdefault('installs', {})
    if rec is None:
        st['installs'].pop('codegraph', None)
    else:
        st['installs']['codegraph'] = rec
    common.save_state(sp, st)


# ---------------------------------------------------------------- the plan

def plan(pins=None, cd=None, home=None, environ=None, machine=None, state_path=None, battery=None, free=None):
    pins = pins or fetch.load_pins()
    environ = os.environ if environ is None else environ
    w = where(cd, home)
    a = fetch.asset(pins, 'codegraph', machine)
    out = {'pack': 'codegraph', 'version': a['version'], 'blocked': None, 'notes': [],
           'steps': [s[1] for s in STEPS]}
    if not os.path.isdir(w['cd']):
        out['blocked'] = 'There is no Claude Code folder at %s. Open Claude Code once first.' % w['cd']
        return out
    found = codemap.detect(cd, None, w['home'], environ.get('PATH', ''))
    if (found['codegraph']['present'] or found['graphify']['present']) and not record(state_path):
        out['installed_already'] = True
        return out
    for path in (w['claude_json'], w['settings']):
        raw = common.read_bytes(path)
        if raw is not None and raw.strip() and facts.load_obj(raw) is None:
            out['blocked'] = '%s is not valid JSON, so Switchboard will not add to it.' % path
            return out
    out['adds'] = ['~/.codegraph/versions/%s: the program (%d MB download, its own Node inside)'
                   % (a['version'], a['bytes'] >> 20),
                   '~/.codegraph/current, and ~/.local/bin/codegraph when nothing is there',
                   '%s: mcpServers.codegraph' % cmi._short(w['claude_json'], w['home']),
                   '%s: permissions.allow "%s" (its tools run without asking)' % (
                       cmi._short(w['settings'], w['home']), ALLOW),
                   '%s: the code map block' % cmi._short(common.instructions_path(w['cd']), w['home'])]
    out['keeps'] = 'Project maps stay when you turn it off'
    out['hosts'] = ['github.com']
    out['download_mb'] = a['bytes'] >> 20
    out['mb'] = pins['codegraph'].get('disk_mb') or (a['bytes'] * 3) >> 20      # the space it takes, measured
    out['commands'] = ['curl %s (checked against %s...)' % (a['url'], a['sha256'][:12]), 'codegraph telemetry off']
    out['blocked'] = cmi.power_and_disk(out['mb'] << 20, w['home'], battery, free)
    out['id'] = common.sha(json.dumps({k: out.get(k) for k in ('version', 'adds', 'hosts')}, sort_keys=True)
                           .encode())[:16]
    return out


# ---------------------------------------------------------------- the steps

def new_record(ctx):
    w = ctx['w']
    return {'by': 'switchboard', 'version': ctx['asset']['version'], 'state': 'installing', 'at': time.time(),
            'cd': w['cd'], 'home': w['home'], 'made': [], 'mcp': None, 'allow': None, 'projects': [],
            'root_made': not os.path.lexists(w['root']), 'log': ctx.get('log')}


def step_download(ctx):
    w = ctx['w']
    ctx['asset'] = a = fetch.asset(ctx['pins'], 'codegraph', ctx.get('machine'))
    rec = record(ctx['state_path'])          # a stopped install runs again on its first record: what was there
    if rec is None or rec.get('state') == 'installed':   # before the first try stays the "before"
        rec = new_record(ctx)
    rec['log'] = ctx.get('log')
    ctx['rec'] = rec
    save_record(rec, ctx['state_path'])
    env = runner.environment(fetch.SYSTEM_DIRS, environ=ctx['environ'], home=w['home'])
    work = ctx['work'] = fetch.download_dir()
    common.make_private_dir(work)
    dest = os.path.join(work, os.path.basename(a['url']))
    r = runner.run(runner.curl_argv(a['url'], dest, a['bytes'] + (1 << 20)), env, timeout=900,
                   log_path=ctx.get('log'))
    if not r.ok:
        shutil.rmtree(work, ignore_errors=True)       # nothing of a failed download is kept
        raise common.Refused("Couldn't download codegraph %s (%s). Check the connection and try again."
                             % (a['version'], 'timed out' if r.timed_out else r.last_line() or 'exit %s' % r.code))
    ctx['archive'] = dest
    return '%d MB' % (os.path.getsize(dest) >> 20)


def step_verify(ctx):
    a = ctx['asset']
    got = fetch.file_sha256(ctx['archive'])
    if got != a['sha256']:
        shutil.rmtree(ctx['work'], ignore_errors=True)   # the archive and the folder it waited in
        raise common.Refused('The download of codegraph %s did not match its pinned checksum, so it was thrown away. '
                             'Nothing was installed.' % a['version'])
    return 'checksum matches the pin'


def step_unpack(ctx):
    w, a, rec = ctx['w'], ctx['asset'], ctx['rec']
    dest = os.path.join(w['versions'], a['version'])
    rec['made'] += fetch._mkdirs(w['versions'], w['home'])
    save_record(rec, ctx['state_path'])
    mine = {'path': dest, 'kind': 'codegraph'}
    try:
        if mine in rec['made'] and os.path.isdir(dest):
            pass                              # unpacked by the first try: kept
        else:
            fetch.unpack(ctx['archive'], dest, a['strip'], 'tar')
            rec['made'].append(mine)
    finally:
        shutil.rmtree(ctx['work'], ignore_errors=True)
    if not os.path.lexists(w['current']):
        os.symlink(dest, w['current'])
        rec['made'].append({'path': w['current'], 'kind': 'file'})
    elif os.path.realpath(w['current']) != os.path.realpath(dest):
        rec['notes'] = rec.get('notes', []) + ['%s points elsewhere; left as it is.' % w['current']]
    if not os.path.lexists(w['bin_link']):
        rec['made'] += fetch._mkdirs(os.path.dirname(w['bin_link']), w['home'])
        os.symlink(os.path.join(dest, 'bin', 'codegraph'), w['bin_link'])
        rec['made'].append({'path': w['bin_link'], 'kind': 'file'})
    save_record(rec, ctx['state_path'])
    if not os.access(os.path.join(dest, 'bin', 'codegraph'), os.X_OK):
        raise common.Refused('The codegraph release has no bin/codegraph.')
    return dest


def step_connect(ctx):
    w, rec = ctx['w'], ctx['rec']
    first = rec.get('mcp')

    def keep_mcp(info):                       # in the record before it is in the file: a stop right after the write
        if first:                             # still knows it is Switchboard's. A run again keeps the first "before".
            info.update({k: first[k] for k in ('before', 'file_existed', 'parent_made', 'inner', 'blank_was')
                         if k in first})
        rec['mcp'] = info
        save_record(rec, ctx['state_path'])
    keep_mcp(add_mcp(w, mcp_entry(w), keep=keep_mcp))
    if not (rec.get('allow') or {}).get('added'):

        def keep_allow(info):
            rec['allow'] = info
            save_record(rec, ctx['state_path'])
        rec['allow'] = add_allow(w, keep=keep_allow)
    save_record(rec, ctx['state_path'])
    p = codemap.plan(True, ctx.get('cd'), ctx['state_path'], None, w['home'], ctx['environ'].get('PATH', ''))
    if p.get('op') and not p.get('blocked'):
        codemap.apply(p)
    elif p.get('blocked'):
        raise common.Refused(p['blocked'])
    return 'MCP entry, allow rule and the code map block'


def step_check(ctx):
    w, a, rec = ctx['w'], ctx['asset'], ctx['rec']
    env = runner.environment(fetch.SYSTEM_DIRS, {'DO_NOT_TRACK': '1', 'CODEGRAPH_TELEMETRY': '0'}, ctx['environ'],
                             w['home'])
    v = fetch.probe(program(w), env, ctx.get('log'))
    if v is None or v[:3] != fetch.parse_version(a['version'])[:3]:
        raise common.Refused('codegraph does not answer as %s.' % a['version'])
    t = runner.run([program(w), 'telemetry', 'off'], env, timeout=60, log_path=ctx.get('log'))
    if not t.ok:
        rec.setdefault('notes', []).append("codegraph's telemetry could not be turned off by its own command.")
    rec['state'] = 'installed'
    save_record(rec, ctx['state_path'])
    return 'codegraph %s answers' % '.'.join(map(str, v))


# ---------------------------------------------------------------- the MCP entry and the allow rule

def add_mcp(w, entry, tries=3, settle=0.2, keep=None):
    """Put mcpServers.codegraph in Claude Code's ~/.claude.json under its own lock; read back to be sure. keep(info) is
    called with what is about to be added, before it is written."""
    path = w['claude_json']
    last = None
    for _ in range(tries):
        with facts.claude_json_lock(path):
            raw = common.read_bytes(path)
            obj = facts.load_obj(raw) if raw is not None and raw.strip() else {}
            if obj is None:
                last = 'it was being written'
                time.sleep(settle)
                continue
            present, val = facts.member(obj, ['mcpServers', 'codegraph'])
            if present and val != entry:
                raise common.Refused('%s already has a codegraph entry that is not Switchboard\'s; it stays as it is.'
                                     % path)
            info = {'file': path, 'keys': ['mcpServers', 'codegraph'], 'file_existed': raw is not None,
                    'before': {'present': present}, 'after': entry,
                    'parent_made': not isinstance(obj.get('mcpServers'), dict),
                    'inner': facts.empty_inner(raw, ['mcpServers', 'codegraph']) if raw is not None else None,
                    'blank_was': facts.blank_text(raw)}
            if not present:
                new = facts.with_member(raw, ['mcpServers', 'codegraph'], entry, path)
                if keep:
                    keep(dict(info))
                if not facts.write_if_same(path, raw, new):
                    last = 'it changed while Switchboard was writing'
                    continue
        time.sleep(settle)
        back = facts.load_obj(common.read_bytes(path))
        if back is not None and facts.member(back, ['mcpServers', 'codegraph']) == (True, entry):
            return info
        last = 'Claude Code wrote it again right after'
    raise common.Refused('Switchboard could not add codegraph to %s (%s). Try again in a minute.' % (path, last))


def remove_mcp(info, tries=3, settle=0.2):
    path = info['file']
    for _ in range(tries):
        with facts.claude_json_lock(path):
            raw = common.read_bytes(path)
            obj = facts.load_obj(raw)
            if raw is None:
                return
            if obj is None:
                time.sleep(settle)
                continue
            present, val = facts.member(obj, info['keys'])
            if not present:
                return
            if val != info['after']:
                raise common.Refused('%s: mcpServers.codegraph changed since Switchboard added it, so it stays.' % path)
            new = facts.without_member(raw, info['keys'], drop_empty_parent=info.get('parent_made'), path=path,
                                       inner=info.get('inner'))
            if not info.get('file_existed') and facts.load_obj(new) == {} and not os.path.islink(path):
                new = None                      # made for this entry alone (a link made since stays)
            elif info.get('blank_was') is not None and facts.load_obj(new) == {}:
                new = info['blank_was'].encode('utf-8')     # empty, or a blank line, as it was
            if facts.write_if_same(path, raw, new):
                return
    raise common.Refused('%s kept changing while Switchboard took codegraph out. Try again in a minute.' % path)


def add_allow(w, keep=None):
    """permissions.allow "mcp__codegraph__*" in Claude Code's settings. keep(info) is called with what is about to be
    added, before it is written."""
    path = w['settings']
    raw = common.read_bytes(path)
    obj = facts.load_obj(raw) if raw is not None and raw.strip() else {}
    if obj is None:
        raise common.Refused('%s is not valid JSON, so Switchboard will not add to it.' % path)
    perms = obj.get('permissions')
    allow = perms.get('allow') if isinstance(perms, dict) else None
    info = {'file': path, 'item': ALLOW, 'file_existed': raw is not None, 'made_permissions': not isinstance(perms, dict),
            'made_allow': not isinstance(allow, list), 'added': False,
            # the spacing inside the object the rule's parent goes into, when that object is empty (the top level, or
            # "permissions"), and a blank file's own text: both come back when the rule goes
            'perm_inner': facts.empty_inner(raw, ['permissions', 'allow']) if raw is not None else None,
            'allow_inner': facts.array_inner(raw, ['permissions', 'allow']) if isinstance(allow, list) else None,
            'blank_was': facts.blank_text(raw)}
    if isinstance(allow, list) and ALLOW in allow:
        return info
    new = facts.array_add(raw, ['permissions', 'allow'], ALLOW, path)
    if keep:
        keep(dict(info, added=True))
    if not facts.write_if_same(path, raw, new):
        if keep:
            keep(info)                        # not added after all
        raise common.Refused('%s changed while Switchboard was writing; try again.' % path)
    info['added'] = True
    return info


def remove_allow(info):
    if not info or not info.get('added'):
        return
    path = info['file']
    raw = common.read_bytes(path)
    obj = facts.load_obj(raw)
    if raw is None or obj is None:
        return
    allow = (obj.get('permissions') or {}).get('allow') if isinstance(obj.get('permissions'), dict) else None
    if not isinstance(allow, list) or ALLOW not in allow:
        return
    if info['made_allow'] and allow == [ALLOW]:
        new = facts.without_member(raw, ['permissions', 'allow'], drop_empty_parent=info['made_permissions'], path=path,
                                   inner=info.get('perm_inner'))
    else:
        new = facts.array_remove(raw, ['permissions', 'allow'], ALLOW, path, inner=info.get('allow_inner'))
    if not info['file_existed'] and facts.load_obj(new) == {} and not os.path.islink(path):
        new = None
    elif info.get('blank_was') is not None and facts.load_obj(new) == {}:
        new = info['blank_was'].encode('utf-8')
    if not facts.write_if_same(path, raw, new):
        raise common.Refused('%s changed while Switchboard was writing; try again.' % path)


# ---------------------------------------------------------------- the undo

def plan_undo(rec):
    """Raises Refused (nothing changed) when an entry Switchboard added was changed since."""
    m = rec.get('mcp')
    if m:
        obj = facts.load_obj(common.read_bytes(m['file']))
        present, val = facts.member(obj or {}, m['keys'])
        if present and val != m['after']:
            raise common.Refused('Switchboard did not take codegraph out, because mcpServers.codegraph in %s changed '
                                 'since it was added. Put it back as it was, or take it out yourself.' % m['file'])


def undo(rec, delete_maps=False):
    """Take out what the install added; project maps stay unless delete_maps. Returns lines."""
    plan_undo(rec)
    if rec.get('mcp') and not rec['mcp']['before']['present']:
        remove_mcp(rec['mcp'])
    remove_allow(rec.get('allow'))
    said = []
    if delete_maps:
        for p in rec.get('projects') or []:
            d = os.path.join(p['path'], '.codegraph')
            if p.get('made') and os.path.isdir(d) and not os.path.islink(d):
                shutil.rmtree(d, ignore_errors=True)
    w = where(rec.get('cd'), rec.get('home'))
    mine = [e['path'] for e in rec.get('made') or [] if e.get('kind') == 'codegraph' and e.get('path')]
    if rec.get('root_made') and os.path.isdir(w['root']) and not os.path.islink(w['root']):
        for n in os.listdir(w['root']):     # codegraph's own small files from its runs (telemetry.json): never a
            p = os.path.join(w['root'], n)  # folder, so a version `codegraph upgrade` added stays, and its link
            if os.path.isfile(p) and not os.path.islink(p):
                os.unlink(p)
    for e in reversed(rec.get('made') or []):
        p = e.get('path')
        if e.get('kind') == 'codegraph' and p and os.path.isdir(p) and not os.path.islink(p) and \
                os.path.dirname(p) == w['versions']:
            shutil.rmtree(p, ignore_errors=True)
        elif p in (w['current'], w['bin_link']):        # Switchboard's links, while they still lead to its version
            if os.path.islink(p) and any(_inside(os.path.realpath(p), v) for v in mine):
                os.unlink(p)                            # (re-pointed by an upgrade, or replaced since: theirs)
        else:
            fetch.remove_made([e], w['home'])           # folders only while empty
    said.append('codegraph: taken out%s.' % ('' if delete_maps else '; project maps stay'))
    return said


def _inside(path, folder):
    folder = os.path.realpath(folder)
    return path == folder or path.startswith(folder + os.sep)


# ---------------------------------------------------------------- project maps

def refuse_root(path, home=None):
    """A plain reason this folder is not mapped, or None. Never the home folder's hidden folders (~/.ssh, ~/.config,
    ~/.aws, ~/.claude) or its Library, nor anything inside them: keys and settings, not projects."""
    home = os.path.realpath(home or common.home())
    p = os.path.realpath(os.path.expanduser(path or ''))
    if not path or not os.path.isabs(os.path.expanduser(path)):
        return 'Give the project folder as a full path.'
    if not os.path.isdir(p):
        return '%s is not a folder.' % path
    if p in ('/', home, os.path.dirname(home), '/Users', '/Volumes', '/System', '/Library', '/Applications', '/private',
             '/tmp', '/var', '/opt', '/usr') or p in tuple(os.path.join(home, x) for x in REFUSED_ROOTS):
        return '%s holds too much to map. Pick one project folder.' % path
    first = os.path.relpath(p, home).split(os.sep)[0] if p.startswith(home + os.sep) else ''
    if first.startswith('.') or first == 'Library':
        return '%s is in %s, which holds settings and keys, not projects. Pick a project folder.' % (
            path, os.path.join('~', first))
    return None


def map_tool(environ=None, home=None, state_path=None):
    """('codegraph', program) when codegraph is here (Switchboard's first), else ('graphify', program) when graphify
    is, else (None, None). graphify only ever runs `extract <folder> --code-only` and `update <folder>` (graphify
    0.9.108, graphify/__main__.py): no model, no key, never its `claude install` or hooks."""
    environ = os.environ if environ is None else environ
    rec = record(state_path) or {}
    w = where(rec.get('cd'), home or rec.get('home'))
    if os.path.exists(program(w)):
        return 'codegraph', program(w)
    found = codemap.detect(None, None, w['home'], environ.get('PATH', ''))
    if found['codegraph']['command']:
        return 'codegraph', found['codegraph']['command']
    if found['graphify']['command']:
        return 'graphify', found['graphify']['command']
    return None, None


def maps(state_path=None):
    """The project folders mapped through Switchboard: codegraph's record, or graphify's list."""
    rec = record(state_path)
    if rec:
        return rec.get('projects') or []
    try:
        st, _ = common.load_state(state_path)
    except common.Refused:
        return []
    return (st.get('installs') or {}).get('maps') or []


def _remember(path, tool, made, state_path):
    entry = {'path': path, 'at': time.time(), 'made': made, 'tool': tool}
    rec = record(state_path)
    if rec is not None:
        rec['projects'] = [x for x in rec.get('projects') or [] if x['path'] != path] + [entry]
        save_record(rec, state_path)
        return
    sp = state_path or common.default_state_path()
    st, _ = common.load_state(sp)
    inst = st.setdefault('installs', {})
    inst['maps'] = [x for x in inst.get('maps') or [] if x['path'] != path] + [entry]
    common.save_state(sp, st)


def _env(environ, home):
    return runner.environment(fetch.SYSTEM_DIRS, {'DO_NOT_TRACK': '1', 'CODEGRAPH_TELEMETRY': '0',
                                                  'GRAPHIFY_QUERY_LOG_DISABLE': '1'}, environ, home)


def map_project(path, state_path=None, environ=None, home=None, log_path=None):
    environ = os.environ if environ is None else environ
    rec = record(state_path) or {}
    w = where(rec.get('cd'), home or rec.get('home'))
    tool, exe = map_tool(environ, w['home'], state_path)
    if not exe:
        raise common.Refused('No code map tool is here. Turn on the code map first.')
    why = refuse_root(path, w['home'])
    if why:
        raise common.Refused(why)
    p = os.path.realpath(os.path.expanduser(path))
    out = os.path.join(p, '.codegraph' if tool == 'codegraph' else 'graphify-out')
    had = os.path.isdir(out)
    argv = [exe, 'init', p, '-y'] if tool == 'codegraph' else [exe, 'extract', p, '--code-only']
    r = runner.run(argv, _env(environ, w['home']), timeout=1800, log_path=log_path)
    if not r.ok:
        raise common.Refused("%s couldn't map %s: %s" % (tool, path, r.last_line() or 'exit %s' % r.code))
    _remember(p, tool, not had, state_path)
    line = r.last_line()
    if tool == 'graphify':
        report = common.read_bytes(os.path.join(out, 'GRAPH_REPORT.md'))
        if report:
            line = ' '.join(x.strip('# ') for x in report.decode('utf-8', 'replace').splitlines()[:2] if x.strip())
    return {'path': p, 'line': line, 'tool': tool}


def refresh_project(path, state_path=None, environ=None, home=None, log_path=None):
    environ = os.environ if environ is None else environ
    rec = record(state_path) or {}
    w = where(rec.get('cd'), home or rec.get('home'))
    tool, exe = map_tool(environ, w['home'], state_path)
    if not exe:
        raise common.Refused('No code map tool is here. Turn on the code map first.')
    p = os.path.realpath(os.path.expanduser(path))
    if not os.path.isdir(os.path.join(p, '.codegraph' if tool == 'codegraph' else 'graphify-out')):
        raise common.Refused('%s has no map yet.' % path)
    argv = [exe, 'sync', p] if tool == 'codegraph' else [exe, 'update', p]
    r = runner.run(argv, _env(environ, w['home']), timeout=900, log_path=log_path)
    if not r.ok:
        raise common.Refused("%s couldn't refresh %s: %s" % (tool, path, r.last_line() or 'exit %s' % r.code))
    return {'path': p, 'line': r.last_line(), 'tool': tool}
