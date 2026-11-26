"""claude-mem, installed by Switchboard: the plan, five steps, start and health, and an undo of exactly what was added.

The install is claude-mem's own installer, run without questions (read in its source, src/npx-cli/commands/install.ts,
15.18.3): `npx --yes file:<package> install --ide claude-code --provider claude --model haiku --no-auto-start`, where
<package> is npm's file for 15.18.3, downloaded by Switchboard and checked against the integrity pinned in pins.json
(npm's own record of it) before anything of it runs, and again before each later run: npx is never given a name to
look up, and never the bare path, which it would run as a command (npx_argv, runner.package_spec).
claude-mem's own dependencies come from npm as npm resolves them (checked by npm against npm's records).
With --ide and --provider given and no terminal it asks nothing; `--provider claude` runs its observer on your own
Claude login; it never gets --disable-auto-memory, which would turn off Claude Code's own memory.

What it adds, all recorded as facts before and after (packs/facts.py), and what the undo takes out:
  <Claude Code folder>/plugins/known_marketplaces.json   "thedotmack"            (autoUpdate set to false after)
  <Claude Code folder>/plugins/installed_plugins.json    "claude-mem@thedotmack"
  <Claude Code folder>/settings.json                     enabledPlugins."claude-mem@thedotmack"
  <Claude Code folder>/plugins/marketplaces/thedotmack/, cache/thedotmack/claude-mem/, data/claude-mem-thedotmack/
                                                         (cache/thedotmack/ too when its install made it and nothing
                                                         else is in it: other plugins of that marketplace stay)
  ~/.local/bin/claude-mem                                 its hook launcher (taken out only while it is the file
                                                         its install, or its own repair, left there)
  a line in a shell file, when ~/.local/bin is not on the PATH it sees
  ~/.claude-mem/                                          its notes: kept when it is turned off
Its installer writes each of the three JSON files whole; Switchboard puts the one member it added into the file's
earlier bytes (held in memory only), so the person's formatting stays and the undo gives those bytes back.

Settings written first, in ~/.claude-mem/settings.json (memory.cm_plan, the same exact undo as the gate): helpers'
tool calls dropped (chats only), its own auto-update off (Switchboard owns the version), the File Read Gate off,
the quota fallback off (so the weekly cap can't be bypassed), and its own weekly backstop at 0.85: claude-mem's
observer runs on the person's own Claude login, and with that login its quota guard stops it once the weekly limit of
the account in use (the one Claude Code is signed in to) is 85% used. claude-mem keeps no launchd job: Claude Code's
SessionStart hook starts its worker. Switchboard checks the worker's /api/health each minute while Claude Code is in
use and starts it once when it is down (packs/install.py).
"""
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.request

from switchboard.packs import common, facts, fetch, memory, runner

PLUGIN = 'claude-mem@thedotmack'
MARKET = 'thedotmack'
PORT_BASE = 37700
EXTRA = (
    ('CLAUDE_MEM_AUTO_UPDATE', '"false"', 'Switchboard updates claude-mem, to a version it tested'),
    ('CLAUDE_MEM_FILE_READ_GATE_ENABLED', '"false"', 'Claude reads whole files as usual'),
    ('CLAUDE_MEM_QUOTA_FALLBACK_PROVIDER', '"off"', 'nothing gets round the weekly cap'),
    ('CLAUDE_MEM_QUOTA_THRESHOLD_SEVEN_DAY', '"0.85"', 'its own stop at 85% of the weekly limit of the account in use'),
)
RC_FILES = ('.zshrc', '.zprofile', '.bashrc', '.bash_profile', '.profile', os.path.join('.config', 'fish', 'config.fish'))
STEPS = (('runtimes', 'Runtimes', 25), ('settings', 'Settings', 5), ('install', 'Install', 40), ('verify', 'Verify', 10),
         ('start', 'Start', 20))
INSTALL_TIMEOUT = 900
LOW_BATTERY = 30
DISK_MARGIN = 2 << 30


def where(cd=None, home=None):
    cd = common.claude_dir(cd)
    home = home or common.home()
    p = os.path.join(cd, 'plugins')
    return {'cd': cd, 'home': home, 'plugins': p,
            'markets': os.path.join(p, 'known_marketplaces.json'),
            'installed': os.path.join(p, 'installed_plugins.json'),
            'settings': os.path.join(cd, 'settings.json'),
            'launcher': os.path.join(home, '.local', 'bin', 'claude-mem'),
            'npm_cache': os.path.join(common.packs_dir(), 'npm-cache'),
            'bun_cache': os.path.join(common.packs_dir(), 'bun-cache'),
            'package_dir': os.path.join(common.packs_dir(), 'claude-mem')}


MEMBERS = (('markets', [MARKET]), ('installed', ['plugins', PLUGIN]), ('settings', ['enabledPlugins', PLUGIN]))
EMPTY = ({}, {'version': 2}, {'version': 2, 'plugins': {}})     # what its files hold with nothing of claude-mem's


# ---------------------------------------------------------------- the worker

def _usable_port(v):
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        return None
    return n if 1024 <= n <= 65535 else None


def env_port(environ):
    """The port CLAUDE_MEM_WORKER_PORT names in this environment, when it is a usable one, else None. Switchboard's
    commands for claude-mem are given it too, so its worker listens where Switchboard looks."""
    return _usable_port((environ or {}).get('CLAUDE_MEM_WORKER_PORT'))


def worker_port(environ=None, home=None):
    """The port claude-mem's worker uses: CLAUDE_MEM_WORKER_PORT (environment, then its settings), else 37700 plus
    the last two digits of this user's id (its own rule)."""
    environ = os.environ if environ is None else environ
    n = env_port(environ)
    if n is None:
        obj = memory._target(memory._read_json(memory.cm_paths(environ, home)['settings']) or {})
        n = _usable_port(obj.get('CLAUDE_MEM_WORKER_PORT'))
    return n if n is not None else PORT_BASE + os.getuid() % 100


def worker_pid(environ=None, home=None):
    """(pid, port) of the worker claude-mem's own pid file names (<its data folder>/worker.pid holds {"pid", "port",
    "startedAt"}, as the real worker writes it: checked 2026-11-14 against claude-mem 15.18.3), or None."""
    raw = common.read_bytes(os.path.join(memory.cm_paths(environ, home)['data'], 'worker.pid'))
    try:
        rec = json.loads(raw.decode('utf-8')) if raw else None
        return int(rec['pid']), int(rec['port'])
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeDecodeError):
        return None


def ours(port, environ=None, home=None):
    """True when claude-mem's pid file names a live process on this port: what answers there is its worker."""
    w = worker_pid(environ, home)
    if not w or w[1] != port or w[0] <= 1:
        return False
    try:
        os.kill(w[0], 0)
    except OSError:                                     # gone, or another user's (its worker runs as this user)
        return False
    return True


def health(port, timeout=2.0, environ=None, home=None):
    """{'ok': True} when claude-mem's worker answers GET /api/health on 127.0.0.1:port, and is the one claude-mem's
    own pid file names; else why not: 'down' (nothing there), 'hang' (no answer in time), 'other' (something else
    answers, also another program that answers {"status": "ok"}: Switchboard never takes it for claude-mem, never
    stops it, and claude-mem is given a free port instead)."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open('http://127.0.0.1:%d/api/health' % port, timeout=timeout) as r:
            body = r.read(65536)
            code = r.status
    except urllib.error.HTTPError:
        return {'ok': False, 'why': 'other'}
    except (socket.timeout, TimeoutError):
        return {'ok': False, 'why': 'hang'}
    except urllib.error.URLError as e:
        if isinstance(getattr(e, 'reason', None), (socket.timeout, TimeoutError)):
            return {'ok': False, 'why': 'hang'}
        return {'ok': False, 'why': 'down'}
    except (OSError, ValueError):
        return {'ok': False, 'why': 'down'}
    try:
        obj = json.loads(body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        obj = None
    if code == 200 and isinstance(obj, dict) and str(obj.get('status', '')).lower() in ('ok', 'healthy') \
            and ours(port, environ, home):
        return {'ok': True, 'port': obj.get('port', port)}
    return {'ok': False, 'why': 'other'}


def port_free(port):
    """True when nothing listens on 127.0.0.1:port (a port left in TIME_WAIT by a worker that just stopped is free)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        return s.connect_ex(('127.0.0.1', port)) != 0
    except OSError:
        return True
    finally:
        s.close()


# ---------------------------------------------------------------- the plan

def record(state_path=None):
    try:
        st, _ = common.load_state(state_path)
    except common.Refused:
        return None
    return (st.get('installs') or {}).get('claude_mem')


def save_record(rec, state_path=None):
    sp = state_path or common.default_state_path()
    st, _ = common.load_state(sp)
    st.setdefault('installs', {})
    if rec is None:
        st['installs'].pop('claude_mem', None)
    else:
        st['installs']['claude_mem'] = rec
    common.save_state(sp, st)


def power_and_disk(need_bytes, home, battery=None, free=None):
    """A plain reason not to start a long install now, or None."""
    if battery is None:
        from switchboard import awake
        battery = awake.parse_batt(awake.run_cmd(awake.PMSET))
    on_ac, pct = battery
    if on_ac is False and pct is not None and pct < LOW_BATTERY:
        return 'The Mac is on battery at %d%%. Plug it in, then turn this on.' % pct
    if free is None:
        try:
            free = shutil.disk_usage(home).free
        except OSError:
            free = None
    if free is not None and free < need_bytes + DISK_MARGIN:
        return 'This Mac has %.1f GB free; this needs about %.1f GB more. Free some space first.' % (
            free / 1e9, (need_bytes + DISK_MARGIN) / 1e9)
    return None


def disk_mb(pins, f):
    """The space a runtime fetched for claude-mem takes once unpacked (MiB): its pin's measured disk_mb, else three
    times its download."""
    return (pins.get(f['name']) or {}).get('disk_mb') or (f['bytes'] >> 20) * 3


def plan(pins=None, cd=None, home=None, environ=None, machine=None, state_path=None, battery=None, free=None,
         log_path=None):
    """What installing claude-mem would do. Changes nothing (it asks node, bun and uv their versions)."""
    pins = pins or fetch.load_pins()
    environ = os.environ if environ is None else environ
    w = where(cd, home)
    v = pins['claude_mem']['version']
    out = {'pack': 'claude-mem', 'version': v, 'blocked': None, 'notes': [], 'steps': [s[1] for s in STEPS]}
    if not os.path.isdir(w['cd']):
        out['blocked'] = 'There is no Claude Code folder at %s. Open Claude Code once first.' % w['cd']
        return out
    inst = memory.cm_installed(cd, environ, home)
    if inst['registered'] and not record(state_path):
        out['blocked'] = None
        out['installed_already'] = True
        return out
    env = runner.environment(fetch.SYSTEM_DIRS, environ=environ, home=w['home'])
    try:
        node = fetch.ensure_node(pins, env, environ, w['home'], log_path, machine, plan_only=True)
        bun = fetch.ensure_bun(pins, env, environ, w['home'], log_path, machine, plan_only=True)
        uv = fetch.ensure_uv(pins, env, environ, w['home'], log_path, machine, plan_only=True)
    except common.Refused as e:
        out['blocked'] = str(e)
        return out
    need = [x for x in (node, bun, uv) if x.get('fetch')]
    mb = pins['claude_mem'].get('disk_mb', 350) + sum(disk_mb(pins, x['fetch']) for x in need)
    port = worker_port(environ, w['home'])
    out['port'] = port
    if not port_free(port) and not health(port, environ=environ, home=w['home'])['ok']:
        out['notes'].append('Port %d is in use by another program; claude-mem gets a free one.' % port)
    out['uses'] = [x['say'] for x in (node, bun, uv)]
    out['adds'] = [
        '%s: "%s"' % (_short(w['markets'], w['home']), MARKET),
        '%s: "%s"' % (_short(w['installed'], w['home']), PLUGIN),
        '%s: enabledPlugins."%s"' % (_short(w['settings'], w['home']), PLUGIN),
        '%s: its plugin files' % _short(os.path.join(w['plugins'], 'marketplaces', MARKET), w['home']),
        '%s: its hook launcher' % _short(w['launcher'], w['home'])]
    out['keeps'] = '~/.claude-mem: its notes stay when you turn it off'
    out['settings'] = ['%s = %s' % (memory.CM_SKIP, '"true"')] + ['%s = %s' % (k, val) for k, val, _ in EXTRA]
    hosts = ['registry.npmjs.org']
    if any(x['fetch']['name'] == 'node' for x in need):
        hosts.append('nodejs.org')
    hosts.append('github.com')
    out['hosts'] = hosts
    out['mb'] = mb
    out['commands'] = ['curl %s (checked against npm\'s %s...)' % (package_pin(pins)['url'],
                                                                    pins['claude_mem']['npm_integrity'][:19]),
                       'npx --yes file:<that file> install %s' % ' '.join(runner.CM_INSTALL_ARGS),
                       'npx --yes file:<that file> telemetry disable', 'claude-mem start']
    out['notes'].append('Its observer runs on your own Claude login, inside the weekly cap you set.')
    out['blocked'] = power_and_disk(mb << 20, w['home'], battery, free)
    out['id'] = common.sha(json.dumps({k: out.get(k) for k in ('version', 'uses', 'adds', 'settings', 'hosts')},
                                      sort_keys=True).encode())[:16]
    return out


def _short(path, home):
    path, home = os.path.realpath(path), os.path.realpath(home)
    return '~' + path[len(home):] if path.startswith(home + os.sep) else path


# ---------------------------------------------------------------- the steps

def owned_names(w):
    """What claude-mem's installer makes, by name (its source, src/npx-cli/commands/install.ts: the marketplace folder,
    the plugin cache and its version, the plugin's data folder, the hook launcher; a real install, checked 2026-11-14,
    made just these). Only these are ever counted as its own: never whatever else appears in the same folders while it
    installs (another plugin, a tool in ~/.local/bin)."""
    p = w['plugins']
    own = os.path.join(p, 'cache', MARKET, 'claude-mem')
    names = [os.path.join(p, 'marketplaces', MARKET), os.path.join(p, 'cache', MARKET), own,
             os.path.join(own, fetch.load_pins()['claude_mem']['version']),
             os.path.join(p, 'data', 'claude-mem-' + MARKET), w['launcher']]
    try:
        names += [os.path.join(own, n) for n in os.listdir(own)]        # its versions, in its own cache folder
    except OSError:
        pass
    return names


def snapshot(w):
    """Which of claude-mem's own names, and the folders they go in, are there now (names only, never contents)."""
    return dict((p, os.path.lexists(p)) for p in containers(w) + owned_names(w))


def containers(w):
    return [w['plugins'], os.path.join(w['plugins'], 'marketplaces'), os.path.join(w['plugins'], 'cache'),
            os.path.join(w['plugins'], 'data'), os.path.dirname(os.path.dirname(w['launcher'])),
            os.path.dirname(w['launcher'])]


def created_since(before, after, w):
    """claude-mem's own names that are there in `after` and were not in `before`, outermost only. A name `before`
    does not list counts as having been there: it is never taken out."""
    own = set(owned_names(w)) - set(containers(w))
    new = sorted(p for p, there in after.items() if there and p in own and before.get(p, True) is False)
    return [p for p in new if not any(p.startswith(q + os.sep) for q in new if q != p)]


def rc_before(home):
    """Each shell file's size and SHA-256 (or None when there is none): facts, never its text, kept in the record so
    what was appended can be found again after Switchboard stopped part-way."""
    out = {}
    for name in RC_FILES:
        raw = common.read_bytes(os.path.join(home, name))
        out[os.path.join(home, name)] = None if raw is None else {'size': len(raw), 'sha': common.sha(raw)}
    return out


def rc_changes(before, home):
    """[{'file', 'appended', 'at', 'existed'}] for shell files the install appended to; notes for any other change."""
    out, notes = [], []
    for path, old in before.items():
        new = common.read_bytes(path)
        if old is None:
            if new is None:
                continue
            at = 0
        elif new is not None and len(new) == old['size'] and common.sha(new) == old['sha']:
            continue
        elif new is not None and len(new) > old['size'] and common.sha(new[:old['size']]) == old['sha']:
            at = old['size']
        else:
            notes.append('%s changed during the install in a way Switchboard did not make; it is left as it is.'
                         % path)
            continue
        out.append({'file': path, 'appended': new[at:].decode('utf-8', 'replace'), 'at': at, 'existed': old is not None})
    return out, notes


def run_env(ctx, extra=None):
    w = ctx['w']
    dirs = [ctx['node']['bin'], ctx['bun']['bin'], ctx['uv']['bin']]
    path_now = (ctx['environ'].get('PATH') or '').split(os.pathsep)
    local_bin = os.path.dirname(w['launcher'])
    if local_bin in path_now:
        dirs.append(local_bin)
    dirs += list(fetch.SYSTEM_DIRS)
    seen, ordered = set(), []
    for d in dirs:
        if d and d not in seen:
            seen.add(d)
            ordered.append(d)
    e = {'DO_NOT_TRACK': '1', 'CLAUDE_MEM_TELEMETRY': '0', 'CLAUDE_MEM_ONLINE_OPTIN': 'false',
         'npm_config_cache': w['npm_cache'], 'npm_config_update_notifier': 'false', 'npm_config_fund': 'false',
         'npm_config_audit': 'false', 'npm_config_yes': 'true', 'BUN_INSTALL_CACHE_DIR': w['bun_cache']}
    if ctx.get('port_set') or env_port(ctx['environ']):          # the port Switchboard watches, given on
        e['CLAUDE_MEM_WORKER_PORT'] = str(ctx['port'])
    e.update(extra or {})
    return runner.environment(ordered, e, ctx['environ'], w['home'])


def caches(home):
    """Bun's cache on macOS (its transpiler cache; real Bun wrote ~10 MB there running claude-mem's scripts in a
    real install), with the folders above it, outermost first."""
    lib = os.path.join(home, 'Library')
    return [lib, os.path.join(lib, 'Caches'), os.path.join(lib, 'Caches', 'bun')]


def new_record(ctx):
    home = ctx['w']['home']
    return {'by': 'switchboard', 'version': ctx['pins']['claude_mem']['version'], 'state': 'installing',
            'at': time.time(), 'cd': ctx['w']['cd'], 'home': home, 'made': [], 'members': [],
            'created': [], 'containers_made': [], 'rc': [], 'notes': [], 'port': None, 'log': ctx.get('log'),
            'caches_made': [c for c in caches(home) if not os.path.lexists(c)]}


def step_runtimes(ctx):
    pins, w = ctx['pins'], ctx['w']
    env = runner.environment(fetch.SYSTEM_DIRS, environ=ctx['environ'], home=w['home'])
    rec = record(ctx['state_path'])          # a stopped install runs again on its first record: what was there
    if rec is None or rec.get('state') == 'installed':   # before the first try stays the "before"
        rec = new_record(ctx)
    rec['log'] = ctx.get('log')
    ctx['rec'] = rec
    save_record(rec, ctx['state_path'])
    said = []
    for name, fn in (('node', fetch.ensure_node), ('bun', fetch.ensure_bun), ('uv', fetch.ensure_uv)):
        got = fn(pins, env, ctx['environ'], w['home'], ctx.get('log'), ctx.get('machine'))
        ctx[name] = got
        rec['made'] += [e for e in got.get('made') or [] if e not in rec['made']]
        rec[name + '_bin'] = got['bin']             # start, stop and repair use the same ones later
        save_record(rec, ctx['state_path'])
        said.append(got['say'])
    return '; '.join(said)


def step_settings(ctx):
    w, rec = ctx['w'], ctx['rec']
    port = worker_port(ctx['environ'], w['home'])
    extra = list(EXTRA)
    if not port_free(port) and not health(port, environ=ctx['environ'], home=w['home'])['ok']:
        port = _free_port_near(port)
        extra.append(('CLAUDE_MEM_WORKER_PORT', json.dumps(str(port)), 'its usual port is in use'))
        ctx['port_set'] = True
    ctx['port'] = rec['port'] = port
    p = memory.cm_plan(True, ctx.get('cap'), ctx.get('cd'), ctx['state_path'], ctx['environ'], w['home'], extra=extra)
    if p.get('blocked'):
        raise common.Refused(p['blocked'])
    memory.cm_apply(p)
    again = bool(rec.get('members'))
    if not again:
        rec['containers_made'] = [c for c in containers(w) if not os.path.lexists(c)]
    if not again or 'before_names' not in rec:
        # In the record before its installer starts, and kept from the first try: if Switchboard stops while the
        # installer runs on (its own session), off and a repair still know what was there before (names and facts).
        rec['before_names'] = snapshot(w)
        rec['rc_before'] = rc_before(w['home'])
    ctx['raw_before'] = {k: common.read_bytes(w[k]) for k, _ in MEMBERS}
    if again:
        for m in rec['members']:        # its installer ran on, by itself, after the first try stopped: its spacing
            k = [x for x, keys in MEMBERS if w[x] == m['file']][0]
            raw = ctx['raw_before'][k]
            if not m['before']['present'] and common.sha(raw) != m.get('before_sha') and \
                    facts.member(facts.load_obj(raw) or {}, m['keys'])[0]:
                m['rewritten_whole'] = True
        save_record(rec, ctx['state_path'])
        return 'claude-mem settings in place (%d written now)' % len(p.get('edits') or [])
    rec['members'] = []
    for k, keys in MEMBERS:
        raw = ctx['raw_before'][k]
        f = facts.member_fact(w[k], keys)
        if raw is not None and raw.strip() and not f['parses']:     # a blank file reads as {}, as its installer reads it
            raise common.Refused('%s is not valid JSON, so Switchboard will not install into it.' % w[k])
        rec['members'].append({'file': w[k], 'keys': keys, 'file_existed': raw is not None,
                               'before': {'present': f['present'], 'value': f['value']},
                               'parent_made': len(keys) == 2 and not facts.member(facts.load_obj(raw) or {},
                                                                                  keys[:1])[0],
                               'inner': facts.empty_inner(raw, keys) if raw is not None else None,
                               'blank_was': facts.blank_text(raw), 'before_sha': common.sha(raw)})
    save_record(rec, ctx['state_path'])
    return 'claude-mem settings written first (%d)' % len(p.get('edits') or [])


def _free_port_near(port):
    near = [n for n in range(port + 100, port + 200) if n <= 65535]   # near the top of the range: its own ports only
    for n in near + list(range(38800, 38900)):
        if port_free(n):
            return n
    raise common.Refused('No free port was found for claude-mem\'s worker.')


def package_pin(pins):
    """claude-mem's npm package as pinned: its version, npm's integrity for it, where npm serves it, a size cap."""
    cm = pins['claude_mem']
    return {'version': cm['version'], 'integrity': cm['npm_integrity'], 'url': cm['url'].format(version=cm['version']),
            'bytes_max': int(cm['bytes_max'])}


def package_path(pin):
    return os.path.join(common.packs_dir(), 'claude-mem', 'claude-mem-%s.tgz' % pin['version'])


def checked_package(pin):
    """The package file when it is here and matches its pinned integrity right now, else None."""
    p = package_path(pin) if pin else None
    return p if p and os.path.isfile(p) and not os.path.islink(p) and fetch.integrity_ok(p, pin['integrity']) else None


def ensure_package(pin, env, log_path=None):
    """claude-mem's npm package: downloaded from npm once and checked against its pinned integrity before any of it
    runs (a file that does not match is thrown away). Returns its path; raises Refused, keeping nothing, otherwise."""
    have = checked_package(pin)
    if have:
        return have
    work = fetch.download_dir()
    try:
        got = fetch.download({'name': 'claude-mem', 'version': pin['version'], 'url': pin['url'],
                              'bytes': pin['bytes_max'], 'integrity': pin['integrity']}, work, env, log_path)
        common.make_private_dir(os.path.dirname(package_path(pin)))
        os.replace(got, package_path(pin))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return package_path(pin)


def _run_package(pin):
    """The checked package for one npx run, checked again right before it: a file changed since never runs."""
    p = checked_package(pin)
    if not p:
        raise common.Refused("claude-mem's package no longer matches its pinned checksum, so it was not run. Repair "
                             'downloads it again.')
    return p


def npx_argv(npx, pkg, *sub):
    """Every npx command for claude-mem: the checked package file pkg as npx's `file:` spec (runner.package_spec),
    then claude-mem's own command."""
    return [npx, '--yes', runner.package_spec(pkg)] + list(sub)


def step_install(ctx):
    pin = package_pin(ctx['pins'])
    ensure_package(pin, runner.environment(fetch.SYSTEM_DIRS, environ=ctx['environ'], home=ctx['w']['home']),
                   ctx.get('log'))
    ctx['rec']['package'] = pin                   # start, stop and repair run this same checked file later
    save_record(ctx['rec'], ctx['state_path'])
    pkg = _run_package(pin)
    argv = npx_argv(ctx['node']['npx'], pkg, 'install', *runner.CM_INSTALL_ARGS)
    ctx['launcher_was'] = _file_sha(ctx['w']['launcher'])
    r = runner.run(argv, run_env(ctx), timeout=ctx.get('timeout', INSTALL_TIMEOUT), log_path=ctx.get('log'),
                   on_start=_noting(ctx, 'install', pkg))
    _ended(ctx)
    ctx['install_result'] = r
    record_effects(ctx)
    if not r.ok:
        raise common.Refused("claude-mem's installer %s: %s" % (
            'took too long and was stopped' if r.timed_out else 'stopped (exit %s)' % r.code,
            r.last_line() or 'see the log'))
    return 'Installation Complete' if 'Installation Complete' in r.out else (r.last_line() or 'installed')


def record_effects(ctx):
    """What the installer added, from the facts before and after; the three JSON files back in their own bytes."""
    w, rec = ctx['w'], ctx['rec']
    rec['created'] = sorted(set(rec.get('created') or []) | set(created_since(rec['before_names'], snapshot(w), w)))
    _note_launcher(rec, w, ctx.get('launcher_was'))
    rc, notes = rc_changes(rec['rc_before'], w['home'])
    rec['rc'] = rc                                # from the first try's facts: everything appended since
    rec['notes'] = list(dict.fromkeys(rec.get('notes', []) + notes))
    for m in rec['members']:
        k = [x for x, keys in MEMBERS if w[x] == m['file']][0]
        before = ctx['raw_before'][k]
        now = common.read_bytes(m['file'])
        if before is not None and now is not None and now != before:
            made = facts.splice(before, now, m['keys'], m['file']) if not m['before']['present'] else None
            if made is not None and made != now:
                if facts.write_if_same(m['file'], now, made):
                    now = made
            elif made is None and facts.load_obj(now) is not None:
                rec['notes'].append("claude-mem's installer rewrote %s whole (its spacing may differ); Switchboard "
                                    'takes out only its own entry.' % _short(m['file'], w['home']))
        present, value = facts.member(facts.load_obj(now) or {}, m['keys'])
        m['after'] = {'present': present, 'value': value if present else None}
        m['after_sha'] = common.sha(now)
    save_record(rec, ctx['state_path'])


def _file_sha(path):
    """The SHA-256 of a plain file's bytes (never through a link), or None."""
    try:
        if os.path.islink(path) or not os.path.isfile(path):
            return None
        return common.sha(common.read_bytes(path))
    except OSError:
        return None


def _note_launcher(rec, w, was):
    """When a run of claude-mem's (its installer, its repair) wrote its hook launcher, and its install made the
    launcher, the launcher's hash goes in the record: Off takes it out only while it is still that file. True when it
    was noted. (A run cut short, still named in the record, may have placed it again unseen: Off then takes it out
    without comparing the hash.)"""
    now = _file_sha(w['launcher'])
    if now and now != was and w['launcher'] in (rec.get('created') or []):
        rec['launcher_sha'] = now
        return True
    return False


def step_verify(ctx):
    w, rec = ctx['w'], ctx['rec']
    missing = [m['file'] for m in rec['members'] if not m['after']['present']]
    if missing:
        raise common.Refused("claude-mem's installer finished, but %s has no claude-mem entry." % missing[0])
    if not os.path.isfile(w['launcher']):
        raise common.Refused("claude-mem's installer finished, but its hook launcher (%s) is not there."
                             % _short(w['launcher'], w['home']))
    s = memory.cm_status(ctx.get('cd'), ctx['state_path'], ctx['environ'], w['home'])
    if not s['helpers_dropped']:
        raise common.Refused("claude-mem's installer changed %s, so helpers would be watched. Switchboard stopped "
                             'here; turn it off to take it out.' % memory.CM_SKIP)
    # Claude Code's own background update of the marketplace: off, so the version stays the tested one.
    mk = [m for m in rec['members'] if m['file'] == w['markets']][0]
    raw = common.read_bytes(w['markets'])
    val = mk['after']['value']
    if isinstance(val, dict) and val.get('autoUpdate') is not False:
        new = facts.with_member(raw, [MARKET, 'autoUpdate'], False, w['markets'])
        if not facts.write_if_same(w['markets'], raw, new):
            raise common.Refused('%s changed while Switchboard was writing; try again.' % w['markets'])
        mk['after'] = {'present': True, 'value': dict(val, autoUpdate=False)}
        mk['after_sha'] = common.sha(new)
        mk['auto_update_was'] = val.get('autoUpdate', '(unset)')
    # Its npm command, not the hook launcher: the launcher answers "Unknown command: telemetry" and exits 0 (checked
    # 2026-11-14 against claude-mem 15.18.3).
    t = runner.run(npx_argv(ctx['node']['npx'], _run_package(rec['package']), 'telemetry', 'disable'), run_env(ctx),
                   timeout=300, log_path=ctx.get('log'))
    if not t.ok or 'unknown command' in t.out.lower():
        rec['notes'].append("claude-mem's telemetry could not be turned off by its own command; DO_NOT_TRACK is set "
                            'for every command Switchboard runs.')
    rec['state'] = 'installed'
    save_record(rec, ctx['state_path'])
    return 'plugin registered and enabled; launcher in place; its update off'


def step_start(ctx):
    r = start(ctx['w'], ctx['environ'], ctx['rec'], ctx['state_path'], ctx.get('log'), ctx)
    if not r['ok']:
        raise common.Refused(r['line'])
    ctx['port'] = r['port']
    return r['line']


def step_repair(ctx):
    """claude-mem's own repair (Bun, uv and its plugin's packages again), for an install that stopped working."""
    w, rec = ctx['w'], ctx['rec']
    d = node_bin(rec, ctx['environ'])
    if not d:
        raise common.Refused('Node is not on this Mac any more. Turn claude-mem off and on again.')
    pin = rec.get('package') or package_pin(ctx['pins'])
    ensure_package(pin, runner.environment(fetch.SYSTEM_DIRS, environ=ctx['environ'], home=w['home']), ctx.get('log'))
    if not rec.get('package'):
        rec['package'] = pin
        save_record(rec, ctx['state_path'])
    pkg = _run_package(pin)
    was = _file_sha(w['launcher'])
    r = runner.run(npx_argv(os.path.join(d, 'npx'), pkg, 'repair'), _env_for(w, ctx['environ'], rec),
                   timeout=INSTALL_TIMEOUT, log_path=ctx.get('log'), on_start=_noting(ctx, 'repair', pkg))
    _ended(ctx)
    if _note_launcher(rec, w, was):               # its repair placed the launcher again: still claude-mem's own
        save_record(rec, ctx['state_path'])
    if not r.ok:
        raise common.Refused("claude-mem's repair stopped (%s)." % (r.last_line() or 'exit %s' % r.code))
    return r.last_line() or 'repaired'


# ---------------------------------------------------------------- a long run, noted while it goes

def _noting(ctx, what, pkg):
    """runner.run's on_start for claude-mem's long runs (its installer, its repair): the run's pid and start time go
    into the record before it does anything. npx runs in a session of its own, so when Switchboard stops, the run goes
    on, and Off, Repair and On wait for it (installer_running)."""
    def note(pid):
        seen = _process(pid)
        ctx['rec']['run'] = {'pid': pid, 'what': what, 'package': pkg, 'started': seen[0] if seen else None,
                             'at': time.time()}
        save_record(ctx['rec'], ctx['state_path'])
    return note


def _ended(ctx):
    """The run returned, so its process has ended: the record no longer names it."""
    if ctx['rec'].pop('run', None) is not None:
        save_record(ctx['rec'], ctx['state_path'])


def _process(pid):
    """(start time, state, command line) of a process as ps shows them, or None. The time is read in C and UTC, so it
    reads the same each time."""
    try:
        r = subprocess.run(['/bin/ps', '-ww', '-p', str(pid), '-o', 'lstart=,stat=,command='],
                           env={'LC_ALL': 'C', 'TZ': 'UTC'}, stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    parts = r.stdout.split(None, 6) if r.returncode == 0 else []
    if len(parts) < 6:
        return None
    return ' '.join(parts[:5]), parts[5], parts[6] if len(parts) > 6 else ''


def installer_running(rec):
    """True only while the run the record names (claude-mem's installer or its repair) still runs: that pid is alive
    and is that run, which is when its start time is the one noted as it started, or its command line names the
    package file it was given. A bare pid is never trusted: macOS gives numbers out again. (The start time is there
    because npm rewrites npx's command line to its own title, and hides parts of it that look like secrets.)"""
    run = rec.get('run') if isinstance(rec, dict) else None
    if not isinstance(run, dict):
        return False
    pid, pkg = run.get('pid'), run.get('package')
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 1 or not isinstance(pkg, str) or not pkg:
        return False
    try:
        os.kill(pid, 0)
    except OSError:                                     # gone, or another user's (the run is this user's)
        return False
    seen = _process(pid)
    if seen is None or 'Z' in seen[1]:                  # gone, or ended and not yet waited for
        return False
    return (run.get('started') is not None and seen[0] == run['started']) or pkg in seen[2]


def wait_line(rec):
    """One plain line while the run the record names still runs, else None."""
    if not installer_running(rec):
        return None
    return "claude-mem's %s is still running (pid %d). Try again once it has finished." % (
        'repair' if rec['run'].get('what') == 'repair' else 'installer', rec['run']['pid'])


# ---------------------------------------------------------------- start, stop, repair

def node_bin(rec, environ):
    """The folder with the node and npx the install used (its record), else Switchboard's own, else the first on the
    PATH given (never the PATH this process happens to have)."""
    for d in [(rec or {}).get('node_bin'), _own_node_bin()] + \
            [x for x in (environ.get('PATH') or '').split(os.pathsep) if x and os.path.isabs(x)] + list(fetch.COMMON_DIRS):
        if d and os.path.isfile(os.path.join(d, 'npx')) and os.path.isfile(os.path.join(d, 'node')):
            return d
    return None


def _cm_argv(w, rec, sub, environ, node_dir=None):
    if os.path.isfile(w['launcher']) and os.access(w['launcher'], os.X_OK):
        return [w['launcher'], sub]
    d = node_dir or node_bin(rec, environ)
    pkg = checked_package((rec or {}).get('package'))         # a missing or changed file never runs
    if not d or not pkg:
        return None
    return npx_argv(os.path.join(d, 'npx'), pkg, sub)


def _env_for(w, environ, rec, ctx=None):
    if ctx and ctx.get('node'):
        return run_env(ctx)
    dirs = [node_bin(rec, environ), (rec or {}).get('bun_bin'), (rec or {}).get('uv_bin'),
            os.path.join(w['home'], '.bun', 'bin'), os.path.dirname(w['launcher'])]
    dirs += [d for d in (environ.get('PATH') or '').split(os.pathsep) if d and os.path.isabs(d)]
    dirs += list(fetch.SYSTEM_DIRS)
    seen, ordered = set(), []
    for d in dirs:
        if d and d not in seen:
            seen.add(d)
            ordered.append(d)
    e = {'DO_NOT_TRACK': '1', 'CLAUDE_MEM_TELEMETRY': '0', 'npm_config_cache': w['npm_cache'],
         'npm_config_update_notifier': 'false', 'BUN_INSTALL_CACHE_DIR': w['bun_cache']}
    if env_port(environ):                                          # the port Switchboard watches, given on
        e['CLAUDE_MEM_WORKER_PORT'] = str(env_port(environ))
    return runner.environment(ordered, e, environ, w['home'])


def start(w, environ, rec, state_path=None, log_path=None, ctx=None, wait=60):
    """Start the worker (its launcher's `start`), then wait for /api/health. {'ok', 'line', 'port'}."""
    port = worker_port(environ, w['home'])
    if health(port, environ=environ, home=w['home'])['ok']:
        return {'ok': True, 'line': 'claude-mem is running on port %d.' % port, 'port': port}
    node_dir = (ctx or {}).get('node', {}).get('bin') if ctx else None
    argv = _cm_argv(w, rec, 'start', environ, node_dir)
    if argv is None:
        return {'ok': False, 'line': "claude-mem's launcher is missing. Repair puts it back.", 'port': port}
    if not port_free(port):
        h = health(port, environ=environ, home=w['home'])
        if not h['ok']:
            return {'ok': False, 'port': port,
                    'line': 'Port %d is in use by another program, so claude-mem can\'t start there.' % port}
    r = runner.run(argv, _env_for(w, environ, rec, ctx), timeout=120, log_path=log_path)
    t0 = time.time()
    while time.time() - t0 < wait:
        if health(port, environ=environ, home=w['home'])['ok']:
            return {'ok': True, 'line': 'claude-mem is running on port %d.' % port, 'port': port}
        time.sleep(0.5)
    return {'ok': False, 'port': port, 'line': 'claude-mem did not start (%s).' % (
        r.last_line() or ('exit %s' % r.code))}


def _own_node_bin():
    d = os.path.join(fetch.runtimes_dir(), 'node-v%s' % fetch.load_pins()['node']['version'], 'bin')
    return d if os.path.isdir(d) else None


def stop(w, environ, rec, log_path=None, wait=15):
    """Stop the worker with its own command; True when the port is free afterwards (or nothing ran)."""
    port = worker_port(environ, w['home'])
    if not health(port, environ=environ, home=w['home'])['ok']:
        return True                                     # nothing of claude-mem's runs there
    argv = _cm_argv(w, rec, 'stop', environ)
    if argv:
        runner.run(argv, _env_for(w, environ, rec), timeout=60, log_path=log_path)
    t0 = time.time()
    while time.time() - t0 < wait:
        if not health(port, 0.5, environ, w['home'])['ok']:
            return True
        time.sleep(0.3)
    return False


# ---------------------------------------------------------------- the undo

def plan_undo(rec, w=None):
    """Every change the undo would make, or Refused naming what the person changed since (nothing changed then). Also
    Refused while claude-mem's installer or repair from before still runs: what it writes later would be left."""
    line = wait_line(rec)
    if line:
        raise common.Refused(line)
    w = w or where(rec.get('cd'), rec.get('home'))
    acts, refuse = [], []
    for m in rec.get('members') or []:
        raw = common.read_bytes(m['file'])
        obj = facts.load_obj(raw)
        if raw is not None and raw.strip() and obj is None:
            refuse.append('%s is not valid JSON now' % m['file'])
            continue
        present, value = facts.member(obj or {}, m['keys'])
        if 'after' not in m:            # its installer ran on after Switchboard stopped: what it wrote is its own
            if present and (not m['before']['present'] or value != m['before']['value']):
                acts.append(('member', m))
            continue
        if not m['after']['present'] or not present:
            continue                                   # nothing of ours there now
        if value != m['after']['value']:
            refuse.append('%s: "%s" changed since claude-mem was installed' % (m['file'], '.'.join(m['keys'])))
            continue
        acts.append(('member', m))
    if refuse:
        raise common.Refused('Switchboard did not take claude-mem out, because %s. Put it back as it was, or remove '
                             'claude-mem yourself (npx claude-mem uninstall).' % '; and '.join(refuse))
    return acts


def undo(rec, environ=None, state_path=None, log_path=None):
    """Take out what the install added. Refuses, changing nothing, when the person changed a recorded entry.
    Keeps ~/.claude-mem (the notes). Returns lines saying what happened."""
    environ = os.environ if environ is None else environ
    w = where(rec.get('cd'), rec.get('home'))
    acts = plan_undo(rec, w)
    said = []
    if not stop(w, environ, rec, log_path):
        raise common.Refused("claude-mem's worker did not stop, so nothing was taken out. Try again in a minute.")
    spacing = []
    for _, m in acts:
        raw = common.read_bytes(m['file'])
        if m['before']['present']:
            new = facts.with_member(raw, m['keys'], m['before']['value'], m['file'])
        else:
            new = facts.without_member(raw, m['keys'], drop_empty_parent=m.get('parent_made'), path=m['file'],
                                       inner=m.get('inner'))
        obj = facts.load_obj(new)
        if not m['file_existed'] and obj in EMPTY and not os.path.islink(m['file']):
            new = None                                  # the installer made this file for this entry alone
        elif m.get('blank_was') is not None and obj in EMPTY:
            new = m['blank_was'].encode('utf-8')        # it was empty, or a blank line: as it was
        if not facts.write_if_same(m['file'], raw, new):
            raise common.Refused('%s changed while Switchboard was writing; try again.' % m['file'])
        cut_short = 'after' not in m or m.get('rewritten_whole')
        if cut_short and new is not None and m.get('before_sha') and common.sha(new) != m['before_sha']:
            spacing.append("claude-mem's installer wrote %s whole while Switchboard was stopped, so its spacing is the "
                           "installer's." % _short(m['file'], w['home']))
    created = set(rec.get('created') or [])
    rcs = rec.get('rc') or []
    if rec.get('before_names') is not None:     # also what its installer made after Switchboard stopped part-way
        created |= set(created_since(rec['before_names'], snapshot(w), w))
        if any('after' not in m for m in rec.get('members') or []) and rec.get('rc_before') is not None:
            rcs = rc_changes(rec['rc_before'], w['home'])[0]
    market_cache = os.path.join(w['plugins'], 'cache', MARKET)
    kept = []
    for p in sorted(created, key=len):
        if p == market_cache and os.path.isdir(p) and not os.path.islink(p):
            _take_out(os.path.join(p, 'claude-mem'))   # the marketplace's cache: another of its plugins may be in it
            try:
                os.rmdir(p)                     # only when nothing else is in it
            except OSError:
                pass
        elif p == w['launcher'] and rec.get('launcher_sha') and 'run' not in rec and \
                _file_sha(p) != rec['launcher_sha']:   # (a run cut short may have placed it again: then it goes)
            if os.path.lexists(p):              # another file there under its name since: it stays
                kept.append('%s was changed after claude-mem installed it, so it stays.'
                            % (_short(os.path.dirname(p), w['home']) + os.sep + os.path.basename(p)))
        else:
            _take_out(p)
    for r in rcs:
        _undo_rc(r, rec)
    fetch.remove_made(rec.get('made'), w['home'])
    for d in (w['npm_cache'], w['bun_cache'], w['package_dir']):
        shutil.rmtree(d, ignore_errors=True)
    for c in sorted(rec.get('containers_made') or [], key=len, reverse=True):
        try:
            os.rmdir(c)
        except OSError:
            pass
    made_caches = rec.get('caches_made') or []
    bun_cache = caches(w['home'])[-1]
    if bun_cache in made_caches and os.path.isdir(bun_cache) and not os.path.islink(bun_cache):
        shutil.rmtree(bun_cache, ignore_errors=True)       # a cache that came with this install
    for c in sorted(made_caches, key=len, reverse=True):
        try:
            os.rmdir(c)
        except OSError:
            pass
    said.append('claude-mem: taken out; your notes stay in ~/.claude-mem.')
    return said + kept + spacing


def _take_out(p):
    if os.path.islink(p) or os.path.isfile(p):
        os.unlink(p)
    elif os.path.isdir(p):
        shutil.rmtree(p, ignore_errors=True)


def _undo_rc(r, rec):
    raw = common.read_bytes(r['file'])
    added = r['appended'].encode('utf-8')
    if raw is None or not added:
        return
    at = r['at']
    if raw[at:at + len(added)] == added:
        new = raw[:at] + raw[at + len(added):]
    elif raw.count(added) == 1:
        i = raw.find(added)
        new = raw[:i] + raw[i + len(added):]
    else:
        rec.setdefault('notes', []).append('The line claude-mem added to %s was changed, so it stays.' % r['file'])
        return
    if not r['existed'] and new == b'':
        os.unlink(r['file'])
    else:
        common.atomic_write(r['file'], new, common.file_mode(r['file'], 0o644))
