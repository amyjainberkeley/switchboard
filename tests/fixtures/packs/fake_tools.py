#!/usr/bin/python3 -IB
"""Stand-ins for the programs the packs run, for tests only. Never the real ones.

Called through a link named node, npx, bun, uv, uvx, claude-mem, codegraph or graphify in a folder that also holds
fake.json (what to do) and calls.jsonl (what each call got: its arguments, its whole environment, its folder).
`--conf DIR NAME ARGS...` names the folder and the program when the call is not through such a link (the launcher
the fake installer writes, and the programs inside the fake download archives).

npx takes what it is given in the package's place as real npx does (libnpmexec and npm-package-arg in npm 10.9.9 and
11.19.0, run offline, 2026-11-14). A bare path to a file is run as a command, through
sh: a .tgz is no program, so "Permission denied" and exit 126 (a file that can be run, runs). `file:<path>` installs
the package npm reads there: npm percent-encodes a space, #, % and ? itself, so a plain path names that very file and
an encoded one names another (npm_reads below); no file there is npm's ENOENT, exit 254.

`npx --yes file:<path>/claude-mem-V.tgz install ...` (or the older `npx --yes claude-mem@V install ...`) does what
claude-mem 15.18.3's installer does (src/npx-cli/commands/install.ts,
checked 2026-11-14), in the home it is given: merges its keys into ~/.claude-mem/settings.json, copies plugin files,
writes known_marketplaces.json, installed_plugins.json and settings.json (enabledPlugins) whole with JSON.stringify's
2-space style, writes its hook launcher to ~/.local/bin/claude-mem, and appends a PATH line to ~/.zshrc when
~/.local/bin is not on its PATH (fake.json "rc": true). fake.json can make it fail part-way ("fail_after"), change
a gate key ("clobber_gate"), or exit non-zero. Its `repair` places the hook launcher again, with other bytes, when
fake.json says "repair_launcher" (the real repair does when its launcher is out of date).
"""
import json
import os
import re
import shlex
import signal
import socket
import subprocess
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.realpath(__file__))
PLUGIN = 'claude-mem@thedotmack'
# What npm-package-arg percent-encodes in a file: spec before it reads the spec as a URL (lib/npa.js, encodedPathChars:
# the same in 12.0.2, npm 10.9.9's, and in 13.0.2, npm 11.19.0's).
NPM_ENCODES = {'\0': '%00', '\t': '%09', '\n': '%0A', '\r': '%0D', ' ': '%20', '"': '%22', '#': '%23', '%': '%25',
               '?': '%3F', '[': '%5B', '\\': '%5C', ']': '%5D', '^': '%5E', '|': '%7C', '~': '%7E'}


def conf_and_name(argv):
    if len(argv) > 3 and argv[1] == '--conf':
        return argv[2], argv[3], argv[4:]
    return os.path.dirname(os.path.abspath(argv[0])), os.path.basename(argv[0]), argv[1:]


def load(path, default=None):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def dump_js(path, obj):
    """JSON.stringify(obj, null, 2) plus a newline, written whole through a temp file (writeJsonFileAtomic)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp-fake'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(json.dumps(obj, indent=2, ensure_ascii=False) + '\n')
    os.replace(tmp, path)


def home():
    return os.environ['HOME']


def claude_dir():
    return os.environ.get('CLAUDE_CONFIG_DIR') or os.path.join(home(), '.claude')


def cm_data():
    return os.path.join(home(), '.claude-mem')


def cm_port():
    v = os.environ.get('CLAUDE_MEM_WORKER_PORT')
    if not v:
        s = load(os.path.join(cm_data(), 'settings.json'), {}) or {}
        t = s['env'] if isinstance(s.get('env'), dict) and any(k.startswith('CLAUDE_MEM_') for k in s['env']) else s
        v = t.get('CLAUDE_MEM_WORKER_PORT')
    return int(v) if v else 37700 + os.getuid() % 100


def write(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(text)
    os.chmod(path, mode)


# ---------------------------------------------------------------- claude-mem's installer

def cm_install(conf, cfg, version):
    cd = claude_dir()
    fail_after = cfg.get('fail_after')
    # its own settings: merged, never replaced (mergeSettings)
    sp = os.path.join(cm_data(), 'settings.json')
    s = load(sp, {}) or {}
    t = s['env'] if isinstance(s.get('env'), dict) and any(k.startswith('CLAUDE_MEM_') for k in s['env']) else s
    t.setdefault('CLAUDE_MEM_PROVIDER', 'claude')
    t['CLAUDE_MEM_MODEL'] = 'haiku'
    if cfg.get('clobber_gate'):
        t['CLAUDE_MEM_SKIP_SUBAGENT_OBSERVATIONS'] = 'false'
    dump_js(sp, s)
    # plugin files
    market = os.path.join(cd, 'plugins', 'marketplaces', 'thedotmack')
    write(os.path.join(market, 'plugin', '.claude-plugin', 'plugin.json'), json.dumps({'name': 'claude-mem',
                                                                                      'version': version}))
    write(os.path.join(market, 'plugin', 'hooks', 'hooks.json'), '{"hooks": {}}\n')
    cache = os.path.join(cd, 'plugins', 'cache', 'thedotmack', 'claude-mem', version)
    write(os.path.join(cache, 'scripts', 'worker-service.cjs'), '// stand-in\n')
    os.makedirs(os.path.join(cd, 'plugins', 'data', 'claude-mem-thedotmack'), exist_ok=True)
    if fail_after == 'files':
        print('npm install failed (exit 1): ERESOLVE')
        return 1
    km_path = os.path.join(cd, 'plugins', 'known_marketplaces.json')
    km = load(km_path, {}) or {}
    old = km.get('thedotmack')
    km['thedotmack'] = {'source': {'source': 'github', 'repo': 'thedotmack/claude-mem'}, 'installLocation': market,
                        'lastUpdated': '2026-11-14T05:00:00.000Z',
                        'autoUpdate': old.get('autoUpdate') if isinstance(old, dict) else True}
    dump_js(km_path, km)
    ip_path = os.path.join(cd, 'plugins', 'installed_plugins.json')
    ip = load(ip_path, {}) or {}
    ip.setdefault('version', 2)
    ip.setdefault('plugins', {})
    entries = [e for e in ip['plugins'].get(PLUGIN, []) if not (isinstance(e, dict) and e.get('scope') == 'user')]
    ip['plugins'][PLUGIN] = entries + [{'scope': 'user', 'installPath': cache, 'version': version,
                                        'installedAt': '2026-11-14T05:00:00.000Z',
                                        'lastUpdated': '2026-11-14T05:00:00.000Z'}]
    dump_js(ip_path, ip)
    st_path = os.path.join(cd, 'settings.json')
    st = load(st_path, {}) or {}
    st.setdefault('enabledPlugins', {})[PLUGIN] = True
    dump_js(st_path, st)
    for rel in cfg.get('also_make') or []:      # something else lands in the same folders while it installs
        p = os.path.join(home(), rel)
        if rel.endswith('/'):
            os.makedirs(p, exist_ok=True)
        else:
            write(p, 'not claude-mem\n', 0o755)
    if fail_after == 'registered':
        print('Installation Aborted: tree-sitter download failed')
        return 1
    local_bin = os.path.join(home(), '.local', 'bin')
    place_launcher(conf)
    if cfg.get('rc') and local_bin not in os.environ.get('PATH', '').split(os.pathsep):
        with open(os.path.join(home(), '.zshrc'), 'a') as f:
            f.write('\n# claude-mem\nexport PATH="$HOME/.local/bin:$PATH"\n')
    print('  Installation Complete')
    print('  Version:     %s' % version)
    return int(cfg.get('install_exit', 0))


def place_launcher(conf, placed_by=''):
    """Its hook launcher, ~/.local/bin/claude-mem: hands over to this file as claude-mem (its folders quoted for sh,
    so a home with a space in its name works)."""
    write(os.path.join(home(), '.local', 'bin', 'claude-mem'), '#!/bin/sh\n%sexec /usr/bin/python3 -IB %s --conf %s '
          'claude-mem "$@"\n' % (placed_by, shlex.quote(os.path.join(HERE, 'fake_tools.py')), shlex.quote(conf)),
          0o755)


def cm_start(cfg):
    port = cm_port()
    if cfg.get('no_start'):
        print('Worker failed to start')
        return 1
    pidfile = os.path.join(cm_data(), 'worker.pid')
    p = subprocess.Popen(['/usr/bin/python3', '-IB', os.path.join(HERE, 'fake_worker.py'), str(port),
                          cfg.get('worker_mode', 'ok')], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    os.makedirs(cm_data(), exist_ok=True)
    with open(pidfile, 'w') as f:
        f.write(json.dumps({'pid': p.pid, 'port': port}))      # as the real worker.pid holds it (checked 2026-11-14)
    cache = os.path.join(home(), 'Library', 'Caches', 'bun', '@t@')     # Bun's transpiler cache, as real Bun writes it
    os.makedirs(cache, exist_ok=True)
    write(os.path.join(cache, 'stand-in.pile'), 'x')
    for _ in range(100):
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)
    print('Worker started on port %d' % port)
    return 0


def cm_stop():
    pidfile = os.path.join(cm_data(), 'worker.pid')
    try:
        with open(pidfile) as f:
            pid = int(json.loads(f.read())['pid'])
    except (OSError, ValueError, KeyError, TypeError):
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except OSError:
            break
        time.sleep(0.05)
    try:
        os.unlink(pidfile)
    except OSError:
        pass
    print('Worker stopped')
    return 0


def claude_mem(conf, cfg, args, version='15.18.3', via_npx=False):
    if args[:1] == ['install']:
        return cm_install(conf, cfg, version)
    if args[:1] == ['start']:
        return cm_start(cfg)
    if args[:1] == ['stop']:
        return cm_stop()
    if args == ['telemetry', 'disable']:
        if not via_npx:                        # the real hook launcher (checked 2026-11-14) does not know this command
            print('Unknown command: telemetry')
            print('claude-mem: worker exited 1, continuing without memory')
            return 0
        dump_js(os.path.join(cm_data(), 'telemetry.json'), {'enabled': False})
        return 0
    if args[:1] in (['repair'], ['status'], ['version']):
        if args[0] == 'repair' and cfg.get('repair_launcher'):
            place_launcher(conf, '# placed again by its repair\n')
        print('claude-mem %s' % version)
        return int(cfg.get('repair_exit', 0)) if args[0] == 'repair' else 0
    if args == ['--version']:
        print(version)
        return 0
    print('fake claude-mem: unknown %s' % args)
    return 64


# ---------------------------------------------------------------- npx, as real npx takes what it is given

def npm_reads(spec, where):
    """The file npm reads for a `file:` spec (or a bare path), worked out as npm-package-arg's fromFile does it on a
    Mac: the characters in NPM_ENCODES are percent-encoded, the result is read as a file URL (its '.' and '..'
    segments taken out by name, never through links), and the URL's path is decoded. So `file:` and a plain path
    names that very path, and a path encoded already names another: '%20' stays the three characters %, 2 and 0."""
    raw = ''.join(NPM_ENCODES.get(c, c) for c in spec)
    if not raw.startswith('file:'):
        raw = 'file:' + raw
    if re.match(r'^file://[^/]', raw):                     # file://path is read as file:/path
        raw = 'file:/' + raw[5:]
    rest = raw[5:]
    if re.match(r'^/{1,3}\.\.?(/|$)', rest):               # file:/../x is read as file:../x
        rest = re.sub(r'^/{1,3}', '', rest)
    if rest.startswith('///'):                             # file:///path: an empty host
        rest = rest[2:]
    if not rest.startswith('/'):                           # relative: to the folder npx runs in
        rest = ''.join(NPM_ENCODES.get(c, c) for c in os.path.abspath(where)) + '/' + rest
    segs = []
    for s in rest.split('/')[1:]:
        if s == '..':
            segs = segs[:-1]
        elif s != '.':
            segs.append(s)
    path = urllib.parse.unquote('/' + '/'.join(segs), errors='strict')
    if re.match(r'^/~(/|$)', path):                        # /~/x is read as the home folder's x
        path = os.path.join(home(), path[3:])
    return path


def npx(conf, cfg, args):
    """`npx --yes <package> <claude-mem's command>`. With no --package, libnpmexec first looks for a file named by the
    first word (localFileExists: resolve(<folder>/node_modules/.bin, word), which for an absolute path is that path)
    and runs it as a command through sh. Otherwise the word is a package spec: claude-mem@V, or a package file."""
    if args[:1] != ['--yes'] or len(args) < 3:
        return 64
    spec = args[1]
    if os.path.isabs(spec) and os.path.isfile(spec):
        if os.access(spec, os.X_OK):
            return subprocess.call([spec] + args[2:])
        sys.stderr.write('sh: %s: Permission denied\n' % spec)
        return 126
    m = re.match(r'^claude-mem@(\d+\.\d+\.\d+)$', spec)
    if not m:
        if not (spec.startswith('file:') or os.path.isabs(spec)):
            return 64
        path = npm_reads(spec, os.getcwd())
        if not os.path.isfile(path):
            sys.stderr.write("npm error code ENOENT\nnpm error syscall open\nnpm error path %s\nnpm error errno -2\n"
                             "npm error enoent ENOENT: no such file or directory, open '%s'\n" % (path, path))
            return 254
        m = re.match(r'^claude-mem-(\d+\.\d+\.\d+)\.tgz$', os.path.basename(path))
        if not m:
            return 64
    if args[2] == 'install' and cfg.get('install_sleep'):        # an install that takes its time
        time.sleep(float(cfg['install_sleep']))
    if cfg.get('print'):
        sys.stdout.write(cfg['print'])
    return claude_mem(conf, cfg, args[2:], m.group(1), via_npx=True)


# ---------------------------------------------------------------- codegraph and graphify

# The options codegraph 1.6.2 itself takes, per command (`codegraph <command> --help`, checked 2026-11-14).
# Like the real one, the fake ends with "error: unknown option '<it>'" and exit 1 on any other: the real one refused
# Switchboard's `init <folder> -y --local-only` that way while this fake let it through.
CG_OPTIONS = {
    'init': ('-i', '--index', '-f', '--force', '-v', '--verbose', '-y', '--yes', '-h', '--help'),
    'sync': ('-q', '--quiet', '-h', '--help'),
    'status': ('-j', '--json', '-h', '--help'),
}


def codegraph(cfg, args):
    if args == ['--version']:
        print(cfg.get('codegraph_version', '1.6.2'))
        return 0
    if args == ['telemetry', 'off']:
        dump_js(os.path.join(home(), '.codegraph', 'telemetry.json'), {'enabled': False})
        return 0
    if args[:1] and args[0] in CG_OPTIONS:
        unknown = [a for a in args[1:] if a.startswith('-') and a not in CG_OPTIONS[args[0]]]
        if unknown:
            sys.stderr.write("error: unknown option '%s'\n" % unknown[0])
            return 1
    if args[:1] == ['init']:
        d = os.path.join(args[1], '.codegraph')
        os.makedirs(d, exist_ok=True)
        write(os.path.join(d, '.gitignore'), '*\n')
        write(os.path.join(d, 'codegraph.db'), 'stand-in\n')
        print('Indexed 3 files')
        return 0
    if args[:1] == ['sync']:
        print('Synced')
        return 0
    if args[:1] == ['status']:              # the real one's field names (fileCount, not files)
        print(json.dumps({'initialized': os.path.isdir(os.path.join(args[1], '.codegraph')), 'fileCount': 3}))
        return 0
    if args == ['daemon']:
        print('No CodeGraph daemons running.')
        return 0
    return 64


def graphify(args):
    if args[:1] == ['extract']:
        d = os.path.join(args[1], 'graphify-out')
        os.makedirs(d, exist_ok=True)
        write(os.path.join(d, 'graph.json'), '{"nodes": [], "links": []}\n')
        write(os.path.join(d, 'GRAPH_REPORT.md'), '# Graph report\n3 files, 12 nodes\n')
        print('graph written')
        return 0
    if args[:1] == ['update']:
        print('updated')
        return 0
    return 64


def main(argv):
    conf, name, args = conf_and_name(argv)
    cfg = load(os.path.join(conf, 'fake.json'), {}) or {}
    seen = load(os.path.join(cm_data(), 'settings.json')) if 'HOME' in os.environ else None
    with open(os.path.join(conf, 'calls.jsonl'), 'a') as f:
        f.write(json.dumps({'name': name, 'args': args, 'env': dict(os.environ), 'cwd': os.getcwd(), 't': time.time(),
                            'cm_settings': seen}) + '\n')
    if cfg.get('sleep'):
        time.sleep(float(cfg['sleep']))
    if name == 'node':
        print(cfg.get('node_version', 'v22.23.5'))
        return 0
    if name == 'bun':
        print(cfg.get('bun_version', '1.4.4'))
        return 0
    if name in ('uv', 'uvx'):
        print('%s %s (fake)' % (name, cfg.get('uv_version', '0.13.8')))
        return 0
    if name == 'npx':
        return npx(conf, cfg, args)
    if name == 'claude-mem':
        return claude_mem(conf, cfg, args)
    if name == 'codegraph':
        return codegraph(cfg, args)
    if name == 'graphify':
        return graphify(args)
    return 64


if __name__ == '__main__':
    sys.exit(main(sys.argv))
