"""Pinned downloads: Node, Bun and uv for claude-mem, and codegraph's own build.

A download is checked against the SHA-256 pinned in pins.json (each read from the project's own release) before
anything is unpacked. Unpacking goes into a sibling folder first and is renamed into place only when every entry was
written, so a failure leaves nothing half-made. Entries that would land outside the folder (absolute names, '..',
links that point out) refuse the whole archive.

What is used when it is already on this Mac: a `node` of 20.19+ (20.x) or 22.12+, a `bun` of the pinned minimum or
newer, any `uv` that answers. What is fetched otherwise, and where:
  node  <data>/packs/runtimes/node-v<version>/ (Switchboard's own; npx comes with it). When no node exists anywhere,
        ~/.local/bin/node links to it too, because claude-mem's search tools run `node` from Claude Code's PATH.
  bun   ~/.bun/bin/bun, where claude-mem looks for Bun (and where Bun's own installer puts it), with no shell file edit.
  uv    ~/.local/bin/uv and uvx, where uv's own installer puts them.
Every path made is returned, so the install's record can take out exactly those. A download that then gives no good
answer to `--version` comes out again before the refusal, since a refused fetch never reaches that record.
"""
import base64
import hashlib
import json
import os
import re
import shutil
import stat
import tarfile
import zipfile

from switchboard.packs import common, runner

PINS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pins.json')
SYSTEM_DIRS = ('/usr/bin', '/bin', '/usr/sbin', '/sbin')
# Only the PATH given is searched, besides each tool's own folder: the page's background job has /opt/homebrew/bin and
# /usr/local/bin on its PATH (install.sh), a terminal has the person's own. Nothing is picked up from a folder the
# PATH does not name, so what is used is what Claude Code's own shells would find. (Tests keep this empty.)
COMMON_DIRS = ()


def load_pins(path=None):
    with open(path or PINS_PATH, 'rb') as f:
        return json.loads(f.read().decode('utf-8'))


def arch(machine=None):
    """'arm64' or 'x86_64' for this Mac (machine as uname gives it, for tests)."""
    m = machine or os.uname().machine
    if m in ('arm64', 'aarch64'):
        return 'arm64'
    if m in ('x86_64', 'amd64'):
        return 'x86_64'
    raise common.Refused('This Mac reports a processor Switchboard has no pinned download for (%s).' % m)


def asset(pins, name, machine=None):
    """The pinned download of one program for this Mac."""
    p = pins[name]
    a = arch(machine)
    return {'name': name, 'version': p['version'], 'arch': a,
            'url': p['url'].format(version=p['version'], arch=p['arch'][a]),
            'sha256': p['sha256'][a], 'bytes': p['bytes'][a], 'strip': p.get('strip', 1),
            'kind': 'zip' if p['url'].endswith('.zip') else 'tar'}


def runtimes_dir():
    return os.path.join(common.packs_dir(), 'runtimes')


def download_dir():
    """Where a download waits while it is checked; removed after each use."""
    return os.path.join(common.packs_dir(), '.download')


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def integrity_ok(path, integrity):
    """True when the file matches an npm integrity string ('sha512-' and the base64 of its SHA-512), the form npm
    records for each version it serves."""
    algo, _, want = (integrity or '').partition('-')
    if algo != 'sha512' or not want:
        return False
    h = hashlib.sha512()
    try:
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(1 << 20), b''):
                h.update(chunk)
    except OSError:
        return False
    return base64.b64encode(h.digest()).decode('ascii') == want


# ---------------------------------------------------------------- download and check

def download(a, work, env, log_path=None, curl='/usr/bin/curl', timeout=900):
    """The pinned file, downloaded into the folder `work` and checked: against a['sha256'], or a['integrity'] (npm's
    form). Raises Refused (and keeps nothing) when the download fails or does not match its pin."""
    common.make_private_dir(work)
    dest = os.path.join(work, os.path.basename(a['url'].split('?')[0]))
    if os.path.lexists(dest):
        os.unlink(dest)
    r = runner.run(runner.curl_argv(a['url'], dest, a['bytes'] + (1 << 20), curl), env, timeout=timeout,
                   log_path=log_path)
    if not r.ok:
        if os.path.lexists(dest):
            os.unlink(dest)
        raise common.Refused("Couldn't download %s %s (%s). Check the connection and try again."
                             % (a['name'], a['version'], 'timed out' if r.timed_out else r.last_line() or
                                'curl exit %s' % r.code))
    got = a['integrity'] if a.get('integrity') else file_sha256(dest)
    if not (integrity_ok(dest, a['integrity']) if a.get('integrity') else got == a['sha256']):
        os.unlink(dest)
        raise common.Refused('The download of %s %s did not match its pinned checksum, so it was thrown away. '
                             'Nothing was installed.' % (a['name'], a['version']))
    runner.note(log_path, '%s %s: checksum ok (%s)' % (a['name'], a['version'], got))
    return dest


# ---------------------------------------------------------------- unpack, all or nothing

def _parts(name, strip):
    """The entry's path inside the destination, as parts, or None when it is the stripped top itself."""
    if name.startswith('/') or '\\' in name:
        raise common.Refused('An entry in the archive has an absolute or odd path (%s).' % name)
    parts = [x for x in name.split('/') if x not in ('', '.')]
    if any(x == '..' for x in parts):
        raise common.Refused('An entry in the archive points outside it (%s).' % name)
    if len(parts) <= strip:
        return None
    return parts[strip:]


def _inside(base, rel_parts, link_target):
    """True when a link at base/rel_parts pointing at link_target stays inside base."""
    if os.path.isabs(link_target):
        return False
    here = os.path.dirname(os.path.join(base, *rel_parts))
    full = os.path.normpath(os.path.join(here, link_target))
    return full == base or full.startswith(base + os.sep)


def unpack(archive, dest, strip=1, kind='tar'):
    """Unpack archive into dest (which must not exist yet). Writes into a sibling folder, then renames it."""
    if os.path.lexists(dest):
        raise common.Refused('%s is already there; Switchboard will not unpack over it.' % dest)
    parent = os.path.dirname(dest)
    os.makedirs(parent, exist_ok=True)
    partial = dest + '.partial-%d' % os.getpid()
    if os.path.lexists(partial):
        shutil.rmtree(partial, ignore_errors=True)
    os.mkdir(partial, 0o755)
    base = os.path.realpath(partial)
    try:
        if kind == 'zip':
            _unzip(archive, base, strip)
        else:
            _untar(archive, base, strip)
        os.rename(partial, dest)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    return dest


def _untar(archive, base, strip):
    links = []
    try:
        tf = tarfile.open(archive, 'r:*')
    except (tarfile.TarError, OSError) as e:
        raise common.Refused('The archive could not be read (%s).' % e)
    with tf:
        for m in tf.getmembers():
            rel = _parts(m.name, strip)
            if rel is None:
                continue
            path = os.path.join(base, *rel)
            if m.isdir():
                os.makedirs(path, exist_ok=True)
            elif m.isfile():
                os.makedirs(os.path.dirname(path), exist_ok=True)
                src = tf.extractfile(m)
                with open(path, 'wb') as out:
                    shutil.copyfileobj(src, out)
                os.chmod(path, 0o755 if m.mode & 0o111 else 0o644)
            elif m.issym():
                if not _inside(base, rel, m.linkname):
                    raise common.Refused('A link in the archive points outside it (%s -> %s).' % (m.name, m.linkname))
                links.append((path, m.linkname))
            elif m.islnk():
                target = _parts(m.linkname, strip)
                if target is None:
                    raise common.Refused('A hard link in the archive is odd (%s).' % m.name)
                links.append((path, ('hard', os.path.join(base, *target))))
            else:
                raise common.Refused('The archive holds a device or special file (%s).' % m.name)
    for path, target in links:          # after every file is there, so a link to a later entry works
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if isinstance(target, tuple):
            shutil.copy2(target[1], path)
        else:
            os.symlink(target, path)


def _unzip(archive, base, strip):
    try:
        zf = zipfile.ZipFile(archive)
    except (zipfile.BadZipFile, OSError) as e:
        raise common.Refused('The archive could not be read (%s).' % e)
    with zf:
        for info in zf.infolist():
            rel = _parts(info.filename, strip)
            if rel is None:
                continue
            path = os.path.join(base, *rel)
            mode = (info.external_attr >> 16) & 0o177777
            if info.filename.endswith('/'):
                os.makedirs(path, exist_ok=True)
                continue
            if stat.S_ISLNK(mode):
                raise common.Refused('The archive holds a link (%s); Switchboard expected plain files.' % info.filename)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with zf.open(info) as src, open(path, 'wb') as out:
                shutil.copyfileobj(src, out)
            os.chmod(path, 0o755 if mode & 0o111 else 0o644)


# ---------------------------------------------------------------- versions

_NUM = re.compile(r'(\d+)\.(\d+)(?:\.(\d+))?')


def parse_version(text):
    m = _NUM.search(text or '')
    return tuple(int(x) for x in m.groups() if x is not None) if m else None


def node_ok(v):
    """claude-mem's own rule (package.json engines): ^20.19.0 || >=22.12.0."""
    return bool(v) and ((v[0] == 20 and v[:2] >= (20, 19)) or v[:2] >= (22, 12))


def at_least(v, minimum):
    return bool(v) and tuple(v) >= tuple(minimum)


def probe(exe, env, log_path=None):
    """The version a program reports, or None (not there, or it would not answer in 20 s)."""
    if not (os.path.isfile(exe) and os.access(exe, os.X_OK)):
        return None
    r = runner.run([exe, '--version'], env, timeout=20, log_path=log_path)
    return parse_version(r.out) if r.ok else None


def did_not_run(name, made):
    """A download that passed its checksum but gives no good answer to `--version` here (it can't run on this Mac, or
    it is too old, or the Mac was too busy for it to answer in 20 s): what this fetch put in place comes out again, since
    a refused fetch never reaches the install's record. The Refused to raise."""
    remove_made(made)
    return common.Refused('The %s Switchboard downloaded did not run on this Mac, so it was removed again. '
                          'Try again later.' % name)


def _dirs(environ, home, extra=()):
    seen, out = set(), []
    for d in list((environ.get('PATH') or '').split(os.pathsep)) + list(extra):
        if d and os.path.isabs(d) and d not in seen:
            seen.add(d)
            out.append(d)
    return out


def _mkdirs(path, stop):
    """Make path and the folders above it that are missing (below stop). Returns them as record entries, in the
    order made, so they can be taken out again only while empty."""
    chain = []
    d = path
    while d and d != stop and d != os.path.dirname(d) and not os.path.lexists(d):
        chain.append(d)
        d = os.path.dirname(d)
    made = []
    for d in reversed(chain):
        os.mkdir(d, 0o755)
        made.append({'path': d, 'kind': 'dir'})
    return made


# ---------------------------------------------------------------- Node, Bun, uv

def ensure_node(pins, env, environ=None, home=None, log_path=None, machine=None, plan_only=False):
    """{'bin', 'node', 'npx', 'version', 'fetched', 'made': [paths], 'link': path or None, 'say'}."""
    environ = os.environ if environ is None else environ
    home = home or common.home()
    own_dir = os.path.join(runtimes_dir(), 'node-v%s' % pins['node']['version'])
    found_any = False
    for d in _dirs(environ, home, COMMON_DIRS + (os.path.join(own_dir, 'bin'),)):
        node, npx = os.path.join(d, 'node'), os.path.join(d, 'npx')
        if not os.path.isfile(node):
            continue
        found_any = True
        if not os.path.isfile(npx):
            continue
        v = probe(node, env, log_path)
        if node_ok(v):
            return {'bin': d, 'node': node, 'npx': npx, 'version': '.'.join(map(str, v)), 'fetched': False,
                    'made': [], 'link': None, 'say': 'Node %s at %s' % ('.'.join(map(str, v)), d)}
    a = asset(pins, 'node', machine)
    if plan_only:
        return {'bin': None, 'fetch': a, 'fetched': True, 'made': [], 'found_any': found_any,
                'say': 'Node %s from nodejs.org (%d MB), kept in Switchboard\'s folder' % (a['version'], a['bytes'] >> 20)}
    made = []
    if not os.path.isdir(own_dir):
        common.make_private_dir(common.packs_dir())
        made += _mkdirs(runtimes_dir(), common.packs_dir())
        work = download_dir()
        try:
            archive = download(a, work, env, log_path)
            unpack(archive, own_dir, a['strip'], a['kind'])
        except BaseException:
            shutil.rmtree(work, ignore_errors=True)
            remove_made(made)
            raise
        finally:
            shutil.rmtree(work, ignore_errors=True)
        made.append({'path': own_dir, 'kind': 'tree'})
    b = os.path.join(own_dir, 'bin')
    v = probe(os.path.join(b, 'node'), env, log_path)
    if not node_ok(v) and made:                 # downloaded just now: it comes out again
        raise did_not_run('Node', made)
    if not node_ok(v):                          # downloaded by an earlier install, which keeps it in its record
        raise common.Refused('The Node Switchboard downloaded does not start on this Mac.')
    link = None
    local_bin = os.path.join(home, '.local', 'bin')
    if not found_any and not os.path.lexists(os.path.join(local_bin, 'node')):
        made += _mkdirs(local_bin, home)
        os.symlink(os.path.join(b, 'node'), os.path.join(local_bin, 'node'))
        link = os.path.join(local_bin, 'node')
        made.append({'path': link, 'kind': 'file', 'link': os.readlink(link)})
    return {'bin': b, 'node': os.path.join(b, 'node'), 'npx': os.path.join(b, 'npx'),
            'version': '.'.join(map(str, v)), 'fetched': True, 'made': made, 'link': link,
            'say': 'Node %s (downloaded, checked)' % '.'.join(map(str, v))}


def ensure_bun(pins, env, environ=None, home=None, log_path=None, machine=None, plan_only=False):
    """{'bin', 'bun', 'version', 'fetched', 'made', 'say'}. Bun goes to ~/.bun/bin/bun when it is fetched."""
    environ = os.environ if environ is None else environ
    home = home or common.home()
    minimum = pins['bun']['min'][0]
    target = os.path.join(home, '.bun', 'bin', 'bun')
    for d in _dirs(environ, home, (os.path.dirname(target),) + COMMON_DIRS):
        exe = os.path.join(d, 'bun')
        v = probe(exe, env, log_path) if os.path.isfile(exe) else None
        if at_least(v, minimum):
            return {'bin': d, 'bun': exe, 'version': '.'.join(map(str, v)), 'fetched': False, 'made': [],
                    'say': 'Bun %s at %s' % ('.'.join(map(str, v)), d)}
    if os.path.lexists(target):
        raise common.Refused('%s is older than claude-mem needs (Bun %s or newer). Run: bun upgrade. Then try again.'
                             % (target, '.'.join(map(str, minimum))))
    a = asset(pins, 'bun', machine)
    if plan_only:
        return {'bin': None, 'fetch': a, 'fetched': True, 'made': [],
                'say': 'Bun %s from github.com (%d MB), to ~/.bun/bin' % (a['version'], a['bytes'] >> 20)}
    work = download_dir()
    unpacked = os.path.join(work, 'bun')
    made = _mkdirs(os.path.dirname(target), home)
    try:
        archive = download(a, work, env, log_path)
        unpack(archive, unpacked, a['strip'], a['kind'])
        shutil.move(os.path.join(unpacked, 'bun'), target)
        os.chmod(target, 0o755)
        made.append({'path': target, 'kind': 'file', 'sha256': file_sha256(target)})
    except BaseException:
        remove_made(made)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
    v = probe(target, env, log_path)
    if not at_least(v, minimum):
        raise did_not_run('Bun', made)
    return {'bin': os.path.dirname(target), 'bun': target, 'version': '.'.join(map(str, v)), 'fetched': True,
            'made': made, 'say': 'Bun %s (downloaded, checked)' % '.'.join(map(str, v))}


def ensure_uv(pins, env, environ=None, home=None, log_path=None, machine=None, plan_only=False):
    """{'bin', 'uv', 'version', 'fetched', 'made', 'say'}. uv goes to ~/.local/bin when it is fetched."""
    environ = os.environ if environ is None else environ
    home = home or common.home()
    minimum = pins['uv']['min'][0]
    local_bin = os.path.join(home, '.local', 'bin')
    for d in _dirs(environ, home, (local_bin, os.path.join(home, '.cargo', 'bin')) + COMMON_DIRS):
        exe = os.path.join(d, 'uv')
        v = probe(exe, env, log_path) if os.path.isfile(exe) else None
        if at_least(v, minimum):
            return {'bin': d, 'uv': exe, 'version': '.'.join(map(str, v)), 'fetched': False, 'made': [],
                    'say': 'uv %s at %s' % ('.'.join(map(str, v)), d)}
    names = ('uv', 'uvx')
    for n in names:
        if os.path.lexists(os.path.join(local_bin, n)):
            raise common.Refused('%s is there but does not answer. Remove or fix it, then try again.'
                                 % os.path.join(local_bin, n))
    a = asset(pins, 'uv', machine)
    if plan_only:
        return {'bin': None, 'fetch': a, 'fetched': True, 'made': [],
                'say': 'uv %s from github.com (%d MB), to ~/.local/bin' % (a['version'], a['bytes'] >> 20)}
    work = download_dir()
    unpacked = os.path.join(work, 'uv')
    made = _mkdirs(local_bin, home)
    try:
        archive = download(a, work, env, log_path)
        unpack(archive, unpacked, a['strip'], a['kind'])
        for n in names:
            src = os.path.join(unpacked, n)
            if os.path.isfile(src):
                shutil.move(src, os.path.join(local_bin, n))
                os.chmod(os.path.join(local_bin, n), 0o755)
                made.append({'path': os.path.join(local_bin, n), 'kind': 'file',
                             'sha256': file_sha256(os.path.join(local_bin, n))})
    except BaseException:
        remove_made(made)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
    v = probe(os.path.join(local_bin, 'uv'), env, log_path)
    if not at_least(v, minimum):
        raise did_not_run('uv', made)
    return {'bin': local_bin, 'uv': os.path.join(local_bin, 'uv'), 'version': '.'.join(map(str, v)), 'fetched': True,
            'made': made, 'say': 'uv %s (downloaded, checked)' % '.'.join(map(str, v))}


def _still_placed(e, p):
    """Is the file or link at p still the one the fetch put there? A file by its SHA-256, a link by where it points.
    The person may have replaced it since (uv self update, bun upgrade, a link of their own): then it is theirs. A
    record from before these were kept has neither, and is taken at its word."""
    if e.get('sha256'):
        return os.path.isfile(p) and not os.path.islink(p) and file_sha256(p) == e['sha256']
    if e.get('link'):
        return os.path.islink(p) and os.readlink(p) == e['link']
    return True


def remove_made(entries, home=None, kept=None):
    """Take out what a fetch made, newest first: files and links, while they are still the ones it put there; a whole
    folder only when it is Switchboard's own (under <data>/packs); any other folder only while empty. Returns the paths
    taken out; a file left because it was changed since gets one sentence in `kept`, when a list is given."""
    gone = []
    own = os.path.join(os.path.realpath(common.packs_dir()), '')
    for e in reversed(list(entries or [])):
        if isinstance(e, str):
            e = {'path': e, 'kind': 'file'}
        p, kind = e.get('path'), e.get('kind')
        if not p or not os.path.isabs(p):
            continue
        try:
            if kind == 'file' and (os.path.islink(p) or os.path.isfile(p)):
                if not _still_placed(e, p):
                    if kept is not None:
                        shown = '~' + p[len(home):] if home and p.startswith(os.path.join(home, '')) else p
                        kept.append('%s was changed after Switchboard put it there, so it stays.' % shown)
                    continue
                os.unlink(p)
                gone.append(p)
            elif kind == 'tree' and os.path.isdir(p) and not os.path.islink(p) and \
                    os.path.join(os.path.realpath(p), '').startswith(own):
                shutil.rmtree(p)
                gone.append(p)
            elif kind == 'dir' and os.path.isdir(p) and not os.path.islink(p):
                os.rmdir(p)
                gone.append(p)
        except OSError:
            pass
    return gone
