import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Tests for the packs that install themselves: packs/runner.py, fetch.py, facts.py,
# claudemem_install.py, codegraph_install.py, install.py, and their switches in claudecode.py and the page.
# Fakes only: fake node, npx, bun, uv, claude-mem, codegraph and graphify (tests/fixtures/packs/fake_tools.py), a fake
# claude-mem worker (fake_worker.py), and fake release archives served on 127.0.0.1. Every home is a throwaway one;
# nothing real is installed, and nothing leaves the Mac.
import base64
import hashlib
import io
import json
import os
import socket
import stat
import sys
import tarfile
import tempfile
import threading
import time
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
FX = os.path.join(HERE, 'fixtures', 'packs')
FAKE = os.path.join(FX, 'fake_tools.py')
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard.packs import claudemem_install as CMI
from switchboard.packs import codegraph_install as CGI
from switchboard.packs import codemap as CM
from switchboard.packs import common as C
from switchboard.packs import facts as F
from switchboard.packs import fetch as FE
from switchboard.packs import install as I
from switchboard.packs import memory as M
from switchboard.packs import runner as R

SECRET = 'sk-ant-api03-MADEUPSECRETFORTESTS0123456789'
USER_SETTINGS = ('{\n    "env": {"ANTHROPIC_API_KEY": "%s"},\n    "hooks": {"Stop": [{"hooks": [{"type": "command", '
                 '"command": "say done"}]}]},\n    "enabledPlugins": {"mine@local": true},\n    "theme": "dark"\n}\n'
                 % SECRET).encode()


def tmp(prefix='sb-packs-'):
    return os.path.realpath(tempfile.mkdtemp(prefix=prefix))


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    n = s.getsockname()[1]
    s.close()
    return n


def tree(base, skip=()):
    """Every file, folder and link under base: its hash, 'dir', or its target."""
    out = {}
    for d, dn, fn in os.walk(base):
        dn[:] = [x for x in dn if os.path.relpath(os.path.join(d, x), base) not in skip]
        for n in dn + fn:
            p = os.path.join(d, n)
            rel = os.path.relpath(p, base)
            if rel in skip:
                continue
            if os.path.islink(p):
                out[rel] = ('link', os.readlink(p))
            elif os.path.isdir(p):
                out[rel] = 'dir'
            else:
                with open(p, 'rb') as f:
                    out[rel] = (hashlib.sha256(f.read()).hexdigest(), stat.S_IMODE(os.stat(p).st_mode))
    return out


class Files(object):
    """A loopback web server for made-up release archives."""

    def __init__(self):
        self.files, self.asked = {}, []
        files, asked = self.files, self.asked

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                asked.append(self.path)
                body = files.get(self.path)
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url(self, name):
        return 'http://127.0.0.1:%d/%s' % (self.port, name)

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def shim(conf, name):
    """A program inside a fake archive: hands over to fake_tools.py as `name`, with its settings in conf."""
    return ('#!/bin/sh\nexec /usr/bin/python3 -IB %s --conf %s %s "$@"\n' % (FAKE, conf, name)).encode()


def tar_bytes(entries):
    """entries: [(name, bytes or None for a folder, mode, link target or None)]."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tf:
        for name, data, mode, link in entries:
            ti = tarfile.TarInfo(name)
            ti.mtime = 1700000000
            if link is not None:
                ti.type, ti.linkname = tarfile.SYMTYPE, link
                tf.addfile(ti)
            elif data is None:
                ti.type, ti.mode = tarfile.DIRTYPE, 0o755
                tf.addfile(ti)
            else:
                ti.size, ti.mode = len(data), mode
                tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def zip_bytes(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for name, data, mode in entries:
            zi = zipfile.ZipInfo(name)
            zi.external_attr = (0o100000 | mode) << 16
            zf.writestr(zi, data)
    return buf.getvalue()


def cm_package(version='15.18.3', extra=b''):
    """A made-up claude-mem npm package, shaped as npm serves one (package/package.json and its bin)."""
    manifest = json.dumps({'name': 'claude-mem', 'version': version, 'type': 'module',
                           'bin': {'claude-mem': 'dist/npx-cli/index.js'}}).encode()
    return tar_bytes([('package/package.json', manifest, 0o644, None),
                      ('package/dist/npx-cli/index.js', b'// stand-in\n' + extra, 0o644, None)])


def npm_integrity(blob):
    return 'sha512-' + base64.b64encode(hashlib.sha512(blob).digest()).decode('ascii')


def serve_cm_package(files, pins, blob=None):
    """pins with claude-mem's package served on this Mac, and npm's integrity of what is served."""
    blob = cm_package() if blob is None else blob
    v = pins['claude_mem']['version']
    files.files['/claude-mem-%s.tgz' % v] = blob
    pins['claude_mem'].update(url=files.url('claude-mem-{version}.tgz'), npm_integrity=npm_integrity(blob),
                              bytes_max=len(blob) + 4096)
    return blob


class Mac(object):
    """A throwaway home with Claude Code's folder, a data folder for Switchboard, a folder of fake programs, and the
    packs pointed at all of it (install.KW). Each test gets its own; tearDown stops anything a fake started. `under`
    puts all of it in a folder of that name (a name with a space, #, % or ?, as a person's home can have)."""

    def __init__(self, test, tools=('node', 'npx', 'bun', 'uv', 'uvx'), cfg=None, settings=None, cc_json=None,
                 cm_settings=None, port=None, rc=b'# mine\n', claude_config_dir=True, under=None):
        self.test = test
        self.root = tmp()
        if under:
            self.root = os.path.join(self.root, under)
            os.makedirs(self.root)
        self.home = os.path.join(self.root, 'home')
        self.cd = os.path.join(self.home, '.claude')
        os.makedirs(self.cd)
        self.data = os.path.join(self.root, 'data')
        self.bin = os.path.join(self.root, 'fakebin')
        os.makedirs(self.bin)
        for n in tools:
            os.symlink(FAKE, os.path.join(self.bin, n))
        self.cfg = dict(cfg or {})
        self.save_cfg()
        self.port = port or free_port()
        cm = dict(cm_settings or {}, CLAUDE_MEM_WORKER_PORT=str(self.port))
        os.makedirs(os.path.join(self.home, '.claude-mem'))
        with open(os.path.join(self.home, '.claude-mem', 'settings.json'), 'w') as f:
            json.dump(cm, f, indent=2)
        if settings is not None:
            self.write(os.path.join(self.cd, 'settings.json'), settings)
        if cc_json is not None:
            self.write(self.claude_json(claude_config_dir), cc_json)
        if rc is not None:
            self.write(os.path.join(self.home, '.zshrc'), rc)
        env = {'HOME': self.home, 'SWITCHBOARD_HOME': self.data}
        if claude_config_dir:
            env['CLAUDE_CONFIG_DIR'] = self.cd
        p = mock.patch.dict(os.environ, env)
        p.start()
        test.addCleanup(p.stop)
        if not claude_config_dir:
            saved = os.environ.pop('CLAUDE_CONFIG_DIR', None)
            if saved is not None:
                test.addCleanup(os.environ.__setitem__, 'CLAUDE_CONFIG_DIR', saved)
        self.environ = {'PATH': self.bin + ':/usr/bin:/bin', 'HOME': self.home}
        if claude_config_dir:
            self.environ['CLAUDE_CONFIG_DIR'] = self.cd
        dirs = mock.patch.object(FE, 'COMMON_DIRS', ())       # never this Mac's own node, bun or uv
        dirs.start()
        test.addCleanup(dirs.stop)
        real_run = R.run

        def only_fakes(argv, *a, **kw):                      # any real program run by a test fails it at once
            R.check(argv)                                    # (what the runner refuses never gets this far)
            exe = os.path.realpath(argv[0])
            if exe != '/usr/bin/curl' and not exe.startswith((os.path.realpath(tempfile.gettempdir()) + os.sep, FX)):
                raise AssertionError('a test ran a real program: %s' % argv)
            if exe == '/usr/bin/curl' and not argv[-1].startswith('http://127.0.0.1:'):
                raise AssertionError('a test asked the internet: %s' % argv[-1])
            return real_run(argv, *a, **kw)
        guard = mock.patch.object(R, 'run', only_fakes)
        guard.start()
        test.addCleanup(guard.stop)
        self.files = Files()                                 # claude-mem's npm package, served on this Mac
        test.addCleanup(self.files.close)
        self.pins = FE.load_pins()
        serve_cm_package(self.files, self.pins)
        old = dict(I.KW)
        I.KW.clear()
        I.KW.update(environ=self.environ, home=self.home, claude_dir=self.cd, machine='arm64', battery=(True, 100),
                    free=10 ** 12, start_wait=10, pins=self.pins)
        test.addCleanup(lambda: (I.KW.clear(), I.KW.update(old)))
        test.addCleanup(I.JOBS.clear)
        test.addCleanup(self.stop_worker)
        I.HEALTH.update(at=0, running=False, ok=None, line=None, why=None, next=0, tries=0, port=None)

    def claude_json(self, cfg_dir=True):
        return os.path.join(self.cd, '.claude.json') if cfg_dir else os.path.join(self.home, '.claude.json')

    def save_cfg(self):
        with open(os.path.join(self.bin, 'fake.json'), 'w') as f:
            json.dump(self.cfg, f)

    def write(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)

    def read(self, path):
        return C.read_bytes(path)

    def calls(self, name=None):
        try:
            with open(os.path.join(self.bin, 'calls.jsonl')) as f:
                rows = [json.loads(x) for x in f if x.strip()]
        except OSError:
            return []
        return [r for r in rows if name is None or r['name'] == name]

    def state(self):
        return C.load_state(os.path.join(self.data, 'packs', 'state.json'))[0]

    def stop_worker(self):
        pidfile = os.path.join(self.home, '.claude-mem', 'worker.pid')
        try:
            with open(pidfile) as f:
                pid = int(json.loads(f.read())['pid'])
        except (OSError, ValueError, KeyError, TypeError):
            return
        try:
            os.kill(pid, 15)
        except OSError:
            pass

    def install(self, pack='claude-mem'):
        p = I.plan(pack)
        self.test.assertIsNone(p.get('blocked'), p.get('blocked'))
        r = I.begin(pack, p['id'], wait=True)
        self.test.assertEqual(r['state'], 'installing')
        return I.job_view(pack)

    def off(self, pack='claude-mem'):
        with I.LOCK:
            I.plan_remove(pack)
            lines = I.remove(pack)
            if pack == 'claude-mem':
                M.cm_apply(M.cm_plan(False, None, self.cd, None, self.environ, self.home))
            else:
                p = CM.plan(False, self.cd, None, home=self.home, path=self.environ['PATH'])
                if p.get('op') and not p.get('blocked'):
                    CM.apply(p)
        return lines


# ---------------------------------------------------------------- the runner

class TestRunner(unittest.TestCase):
    def setUp(self):
        self.mac = Mac(self, tools=('node', 'npx', 'codegraph', 'graphify'))

    def env(self, **extra):
        return R.environment([self.mac.bin, '/usr/bin', '/bin'], extra, self.mac.environ, self.mac.home)

    def test_only_the_allowed_shapes_run(self):
        npx = os.path.join(self.mac.bin, 'npx')
        bad = [[npx, '--yes', 'claude-mem@15.18.3', 'install', '--ide', 'claude-code', '--provider', 'claude',
                '--model', 'haiku', '--no-auto-start', '--disable-auto-memory'],
               [npx, '--yes', 'claude-mem@15.18.3', 'uninstall'],
               [npx, '--yes', 'claude-mem@latest', 'start'],
               [npx, 'claude-mem', 'install'],
               ['npx', '--yes', 'claude-mem@15.18.3', 'start'],                   # not a full path
               [os.path.join(self.mac.bin, 'codegraph'), 'uninstall'],
               [os.path.join(self.mac.bin, 'codegraph'), 'install', '--yes'],
               [os.path.join(self.mac.bin, 'codegraph'), 'init', 'relative/folder', '-y'],
               [os.path.join(self.mac.bin, 'graphify'), 'claude', 'install'],
               [os.path.join(self.mac.bin, 'claude'), '--version'],
               [os.path.join(self.mac.bin, 'claude-mem'), 'telemetry', 'disable'],
               ['/bin/sh', '-c', 'echo hi'],
               ['/usr/bin/curl', '-fsSL', 'http://example.com/x']]
        for argv in bad:
            with self.subTest(argv=argv):
                with self.assertRaises(R.NotAllowed):
                    R.run(argv, self.env())
        self.assertEqual(self.mac.calls(), [])                            # nothing was started

    def test_scrubbed_environment_temp_folder_and_one_argument_per_item(self):
        environ = dict(self.mac.environ, ANTHROPIC_API_KEY=SECRET, GITHUB_TOKEN='ghp_' + 'x' * 30, http_proxy='')
        env = R.environment([self.mac.bin, '/usr/bin', '/bin'], {'DO_NOT_TRACK': '1'}, environ, self.mac.home)
        odd = os.path.join(self.mac.root, 'a b; touch pwned $(id)')
        os.makedirs(odd)
        r = R.run([os.path.join(self.mac.bin, 'codegraph'), 'init', odd, '-y'], env)
        self.assertTrue(r.ok, r.out)
        call = self.mac.calls('codegraph')[0]
        self.assertEqual(call['args'], ['init', odd, '-y'])      # one argument, never a shell
        self.assertFalse(os.path.exists(os.path.join(self.mac.root, 'pwned')))
        self.assertFalse(any(k.startswith('ANTHROPIC_') or 'TOKEN' in k for k in call['env']))
        self.assertEqual(call['env']['HOME'], self.mac.home)
        self.assertEqual(call['env']['PATH'], ':'.join([self.mac.bin, '/usr/bin', '/bin']))
        self.assertEqual(call['env']['DO_NOT_TRACK'], '1')
        self.assertNotEqual(os.path.realpath(call['cwd']), os.path.realpath(os.getcwd()))
        self.assertTrue(call['cwd'].startswith(os.path.realpath(tempfile.gettempdir())) or
                        os.path.realpath(call['cwd']).startswith(os.path.realpath(tempfile.gettempdir())))
        self.assertFalse(os.path.exists(call['cwd']))                      # the fresh folder is gone afterwards
        for k in ('ANTHROPIC_API_KEY', 'GH_TOKEN', 'MY_SECRET', 'PATH_TO_X'):
            with self.subTest(k=k):
                with self.assertRaises(R.NotAllowed):
                    R.environment(['/usr/bin'], {k: 'x'}, {}, self.mac.home)

    def test_timeout_ends_the_whole_group(self):
        self.mac.cfg['sleep'] = 30
        self.mac.save_cfg()
        t0 = time.time()
        r = R.run([os.path.join(self.mac.bin, 'node'), '--version'], self.env(), timeout=1)
        self.assertTrue(r.timed_out)
        self.assertFalse(r.ok)
        self.assertLess(time.time() - t0, 15)

    def test_output_is_capped_and_secrets_are_masked_in_the_log(self):
        self.mac.cfg['print'] = ('line\n' * 60000) + 'token %s and Bearer abcdefghijklmnop12345\n' % SECRET
        self.mac.save_cfg()
        log = R.new_log('claude-mem')
        pkg = os.path.join(C.packs_dir(), 'claude-mem', 'claude-mem-15.18.3.tgz')
        self.mac.write(pkg, cm_package())
        r = R.run([os.path.join(self.mac.bin, 'npx'), '--yes', 'file:' + pkg, 'version'], self.env(), log_path=log)
        self.assertTrue(r.ok)
        self.assertLess(len(r.out), R.OUTPUT_HEAD + R.OUTPUT_TAIL + 200)
        self.assertIn('bytes left out', r.out)
        self.assertNotIn(SECRET, r.out)
        with open(log) as f:
            text = f.read()
        self.assertNotIn(SECRET, text)
        self.assertNotIn('abcdefghijklmnop12345', text)
        self.assertEqual(stat.S_IMODE(os.stat(log).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(log)).st_mode), 0o700)


# ---------------------------------------------------------------- pinned downloads

class Downloads(object):
    """Made-up releases of node, bun, uv and codegraph on a loopback server, and pins that point at them."""

    def __init__(self, test, conf=None, cfg=None):
        self.files = Files()
        test.addCleanup(self.files.close)
        if conf is None:                     # the downloaded programs answer from their own settings
            conf = tmp('sb-dlconf-')
            with open(os.path.join(conf, 'fake.json'), 'w') as f:
                json.dump(cfg or {}, f)
        self.conf = conf
        node = tar_bytes([('node-v22.23.5-darwin-arm64/', None, 0, None),
                          ('node-v22.23.5-darwin-arm64/bin/node', shim(conf, 'node'), 0o755, None),
                          ('node-v22.23.5-darwin-arm64/lib/npx-cli.sh', shim(conf, 'npx'), 0o755, None),
                          ('node-v22.23.5-darwin-arm64/bin/npx', None, 0, '../lib/npx-cli.sh')])
        bun = zip_bytes([('bun-darwin-aarch64/bun', shim(conf, 'bun'), 0o755)])
        uv = tar_bytes([('uv-aarch64-apple-darwin/uv', shim(conf, 'uv'), 0o755, None),
                        ('uv-aarch64-apple-darwin/uvx', shim(conf, 'uvx'), 0o755, None)])
        cg = tar_bytes([('codegraph-darwin-arm64/bin/codegraph', shim(conf, 'codegraph'), 0o755, None),
                        ('codegraph-darwin-arm64/lib/node', b'stand-in', 0o755, None)])
        self.blobs = {'node': node, 'bun': bun, 'uv': uv, 'codegraph': cg}
        self.pins = FE.load_pins()
        serve_cm_package(self.files, self.pins)
        for name, blob in self.blobs.items():
            path = '/%s-%s' % (name, 'release.zip' if name == 'bun' else 'release.tar.gz')
            self.files.files[path] = blob
            p = self.pins[name]
            p['url'] = self.files.url(path.lstrip('/'))
            p['sha256'] = {'arm64': hashlib.sha256(blob).hexdigest(), 'x86_64': hashlib.sha256(blob).hexdigest()}
            p['bytes'] = {'arm64': len(blob), 'x86_64': len(blob)}

    def set(self, **cfg):
        with open(os.path.join(self.conf, 'fake.json'), 'w') as f:
            json.dump(cfg, f)

    def calls(self):
        try:
            with open(os.path.join(self.conf, 'calls.jsonl')) as f:
                return [json.loads(x) for x in f if x.strip()]
        except OSError:
            return []

    def serve(self, name, blob):
        path = '/%s-%s' % (name, 'release.zip' if name == 'bun' else 'release.tar.gz')
        self.files.files[path] = blob


class TestFetch(unittest.TestCase):
    def setUp(self):
        self.mac = Mac(self, tools=())
        self.dl = Downloads(self)
        I.KW['pins'] = self.dl.pins
        self.env = R.environment(FE.SYSTEM_DIRS, environ=self.mac.environ, home=self.mac.home)

    def test_names_for_each_processor(self):
        pins = FE.load_pins()
        self.assertTrue(FE.asset(pins, 'node', 'arm64')['url'].endswith('node-v22.23.5-darwin-arm64.tar.gz'))
        self.assertTrue(FE.asset(pins, 'node', 'x86_64')['url'].endswith('node-v22.23.5-darwin-x64.tar.gz'))
        self.assertTrue(FE.asset(pins, 'bun', 'arm64')['url'].endswith('/bun-v1.4.4/bun-darwin-aarch64.zip'))
        self.assertTrue(FE.asset(pins, 'uv', 'x86_64')['url'].endswith('/0.13.8/uv-x86_64-apple-darwin.tar.gz'))
        self.assertTrue(FE.asset(pins, 'codegraph', 'arm64')['url'].endswith('/v1.6.2/codegraph-darwin-arm64.tar.gz'))
        self.assertNotEqual(FE.asset(pins, 'codegraph', 'arm64')['sha256'], FE.asset(pins, 'codegraph', 'x86_64')['sha256'])
        with self.assertRaises(C.Refused):
            FE.arch('riscv64')

    def test_every_pin_is_https_on_its_projects_own_host_with_a_sha256(self):
        pins = FE.load_pins()
        hosts = {'node': 'https://nodejs.org/dist/', 'bun': 'https://github.com/oven-sh/bun/releases/download/',
                 'uv': 'https://github.com/astral-sh/uv/releases/download/',
                 'codegraph': 'https://github.com/colbymchenry/codegraph/releases/download/'}
        for name, prefix in hosts.items():
            for a in ('arm64', 'x86_64'):
                x = FE.asset(pins, name, a)
                self.assertTrue(x['url'].startswith(prefix), x['url'])
                self.assertRegex(x['sha256'], r'^[0-9a-f]{64}$')
        self.assertRegex(pins['claude_mem']['version'], r'^\d+\.\d+\.\d+$')
        # a prompt to refresh, not a runtime effect: pins older than 60 days fail here
        age = time.time() - time.mktime(time.strptime(pins['refreshed'], '%Y-%m-%d'))
        self.assertLess(age, 60 * 86400, 'packs/pins.json is over 60 days old: read each release again and refresh it')

    def test_no_node_anywhere_downloads_checks_unpacks_and_links_it(self):
        got = FE.ensure_node(self.dl.pins, self.env, self.mac.environ, self.mac.home, machine='arm64')
        self.assertTrue(got['fetched'])
        own = os.path.join(self.mac.data, 'packs', 'runtimes', 'node-v22.23.5')
        self.assertEqual(got['bin'], os.path.join(own, 'bin'))
        self.assertTrue(os.path.islink(os.path.join(own, 'bin', 'npx')))         # the archive's own link, kept inside
        self.assertEqual(os.readlink(os.path.join(self.mac.home, '.local', 'bin', 'node')), os.path.join(own, 'bin', 'node'))
        self.assertEqual([e['kind'] for e in got['made']], ['dir', 'tree', 'dir', 'dir', 'file'])
        self.assertFalse(os.path.exists(os.path.join(self.mac.data, 'packs', '.download')))
        FE.remove_made(got['made'], self.mac.home)
        self.assertFalse(os.path.exists(os.path.join(self.mac.home, '.local')))
        self.assertFalse(os.path.exists(own))

    def test_a_good_node_on_the_path_is_used_and_nothing_downloads(self):
        for n in ('node', 'npx'):
            os.symlink(FAKE, os.path.join(self.mac.bin, n))
        environ = dict(self.mac.environ, PATH=self.mac.bin + ':/usr/bin:/bin')
        got = FE.ensure_node(self.dl.pins, self.env, environ, self.mac.home, machine='arm64')
        self.assertFalse(got['fetched'])
        self.assertEqual(got['bin'], self.mac.bin)
        self.assertEqual(self.dl.files.asked, [])

    def test_too_old_node_is_not_used_and_is_not_shadowed_on_the_path(self):
        for n in ('node', 'npx'):
            os.symlink(FAKE, os.path.join(self.mac.bin, n))
        self.mac.cfg['node_version'] = 'v20.11.0'
        self.mac.save_cfg()
        environ = dict(self.mac.environ, PATH=self.mac.bin + ':/usr/bin:/bin')
        got = FE.ensure_node(self.dl.pins, self.env, environ, self.mac.home, machine='arm64')
        self.assertTrue(got['fetched'])
        self.assertIsNone(got['link'])                       # a node exists, so ~/.local/bin/node is not made
        self.assertTrue(FE.node_ok((20, 19, 0)) and FE.node_ok((22, 12, 1)) and FE.node_ok((24, 1, 0)))
        self.assertFalse(FE.node_ok((20, 18, 9)) or FE.node_ok((21, 9, 0)) or FE.node_ok((22, 11, 0)))

    def test_a_bad_checksum_refuses_and_leaves_nothing(self):
        self.dl.pins['node']['sha256']['arm64'] = '0' * 64
        before = tree(self.mac.root, skip=('fakebin/calls.jsonl',))
        with self.assertRaises(C.Refused) as e:
            FE.ensure_node(self.dl.pins, self.env, self.mac.environ, self.mac.home, machine='arm64')
        self.assertIn('did not match its pinned checksum', str(e.exception))
        after = tree(self.mac.root, skip=('fakebin/calls.jsonl',))
        own = ('data', 'data/packs')                      # Switchboard's own folder, where its logs go
        self.assertEqual({k: v for k, v in after.items() if k not in own and not k.startswith('data/packs/logs')},
                         {k: v for k, v in before.items() if k not in own and not k.startswith('data/packs/logs')})

    def test_unsafe_archives_refuse_and_leave_nothing_half_made(self):
        cases = {'dotdot': tar_bytes([('x/../../evil', b'x', 0o644, None)]),
                 'absolute': tar_bytes([('/x/evil', b'x', 0o644, None)]),
                 'link out': tar_bytes([('x/bin/node', b'x', 0o755, None), ('x/bin/out', None, 0, '../../../../etc')]),
                 'link abs': tar_bytes([('x/bin/out', None, 0, '/etc/passwd')])}
        for name, blob in cases.items():
            with self.subTest(name):
                arch = os.path.join(self.mac.root, 'a.tar.gz')
                with open(arch, 'wb') as f:
                    f.write(blob)
                dest = os.path.join(self.mac.root, 'out')
                with self.assertRaises(C.Refused):
                    FE.unpack(arch, dest, 1, 'tar')
                self.assertFalse(os.path.exists(dest))
                self.assertEqual([n for n in os.listdir(self.mac.root) if n.startswith('out')], [])
        with self.assertRaises(C.Refused):                                    # never over an existing folder
            os.makedirs(os.path.join(self.mac.root, 'there'))
            FE.unpack(arch, os.path.join(self.mac.root, 'there'), 1, 'tar')

    def test_bun_goes_where_claude_mem_looks_and_an_old_one_there_is_named(self):
        got = FE.ensure_bun(self.dl.pins, self.env, self.mac.environ, self.mac.home, machine='arm64')
        target = os.path.join(self.mac.home, '.bun', 'bin', 'bun')
        self.assertEqual(got['bun'], target)
        self.assertEqual([(os.path.relpath(e['path'], self.mac.home), e['kind']) for e in got['made']],
                         [('.bun', 'dir'), ('.bun/bin', 'dir'), ('.bun/bin/bun', 'file')])
        self.dl.set(bun_version='1.3.0')
        with self.assertRaises(C.Refused) as e:
            FE.ensure_bun(self.dl.pins, self.env, self.mac.environ, self.mac.home, machine='arm64')
        self.assertIn('bun upgrade', str(e.exception))
        self.dl.set()
        FE.remove_made(got['made'], self.mac.home)
        self.assertFalse(os.path.exists(os.path.join(self.mac.home, '.bun')))

    def test_uv_is_reused_or_fetched_into_local_bin_and_a_folder_with_other_things_stays(self):
        os.makedirs(os.path.join(self.mac.home, '.local', 'bin'))
        got = FE.ensure_uv(self.dl.pins, self.env, self.mac.environ, self.mac.home, machine='arm64')
        self.assertTrue(got['fetched'])
        self.assertEqual(sorted(os.listdir(os.path.join(self.mac.home, '.local', 'bin'))), ['uv', 'uvx'])
        with open(os.path.join(self.mac.home, '.local', 'bin', 'claude'), 'w') as f:
            f.write('someone else put this here later')
        again = FE.ensure_uv(self.dl.pins, self.env, self.mac.environ, self.mac.home, machine='arm64')
        self.assertFalse(again['fetched'])
        FE.remove_made(got['made'], self.mac.home)
        self.assertEqual(os.listdir(os.path.join(self.mac.home, '.local', 'bin')), ['claude'])


# ---------------------------------------------------------------- facts and exact JSON edits

class TestFacts(unittest.TestCase):
    def test_a_member_in_and_out_gives_the_same_bytes_back(self):
        for raw in (USER_SETTINGS, b'{}', b'{ }\n', b'{\n}\n', b'{"a": 1}', b'{\n\t"a": {"b": 2}\n}\n', None, b''):
            for keys in (['enabledPlugins', 'claude-mem@thedotmack'], ['thedotmack']):
                with self.subTest(raw=raw, keys=keys):
                    obj = F.load_obj(raw) if raw and raw.strip() else {}
                    parent_made = len(keys) == 2 and keys[0] not in obj
                    inner = F.empty_inner(raw, keys)
                    new = F.with_member(raw, keys, {'x': [1, 2]} if len(keys) == 1 else True)
                    self.assertEqual(F.member(F.load_obj(new), keys)[0], True)
                    back = F.without_member(new, keys, drop_empty_parent=parent_made, inner=inner)
                    if raw is not None and raw.strip():
                        self.assertEqual(back, raw)
                    else:
                        self.assertEqual(F.load_obj(back), {})

    def test_splice_keeps_the_persons_formatting(self):
        rewritten = (json.dumps(dict(json.loads(USER_SETTINGS), enabledPlugins={'mine@local': True,
                                                                               'claude-mem@thedotmack': True}),
                                indent=2) + '\n').encode()
        made = F.splice(USER_SETTINGS, rewritten, ['enabledPlugins', 'claude-mem@thedotmack'])
        self.assertEqual(F.load_obj(made), F.load_obj(rewritten))
        self.assertTrue(made.startswith(USER_SETTINGS[:40]))
        self.assertEqual(F.without_member(made, ['enabledPlugins', 'claude-mem@thedotmack']), USER_SETTINGS)
        other = rewritten.replace(b'"dark"', b'"light"')                # the installer changed something else
        self.assertIsNone(F.splice(USER_SETTINGS, other, ['enabledPlugins', 'claude-mem@thedotmack']))

    def test_one_string_in_a_list_in_place(self):
        for raw in (b'{"permissions": {"allow": ["Bash(ls:*)"], "deny": []}}\n',
                    b'{\n  "permissions": {\n    "allow": [\n      "Read",\n      "Bash(ls:*)"\n    ]\n  }\n}\n',
                    b'{"permissions": {"allow": [ ]}}', b'{"permissions": {}}', b'{"model": "opus"}', None):
            with self.subTest(raw=raw):
                obj = F.load_obj(raw) if raw else {}
                perms = obj.get('permissions')
                inner = F.array_inner(raw, ['permissions', 'allow']) if raw else None
                pinner = F.empty_inner(raw, ['permissions', 'allow']) if isinstance(perms, dict) else None
                new = F.array_add(raw, ['permissions', 'allow'], 'mcp__codegraph__*')
                self.assertIn('mcp__codegraph__*', F.load_obj(new)['permissions']['allow'])
                allow = (perms or {}).get('allow')
                if isinstance(allow, list):
                    back = F.array_remove(new, ['permissions', 'allow'], 'mcp__codegraph__*', inner=inner)
                else:
                    back = F.without_member(new, ['permissions', 'allow'], drop_empty_parent=not isinstance(perms, dict),
                                            inner=pinner)
                self.assertEqual(back, raw if raw else back)
                if raw is None:
                    self.assertEqual(F.load_obj(back), {})

    def test_claude_code_lock_is_waited_on_and_a_stale_one_is_taken_over(self):
        d = tmp()
        path = os.path.join(d, '.claude.json')
        os.mkdir(path + '.lock')
        t0 = time.time()
        with self.assertRaises(C.Refused) as e:
            with F.claude_json_lock(path, wait=0.5):
                pass
        self.assertIn('Try again', str(e.exception))
        self.assertGreaterEqual(time.time() - t0, 0.5)
        self.assertTrue(os.path.isdir(path + '.lock'))                    # a live holder's lock is never taken
        old = time.time() - 30
        os.utime(path + '.lock', (old, old))
        with F.claude_json_lock(path, wait=0.5):
            self.assertTrue(os.path.isdir(path + '.lock'))
        self.assertFalse(os.path.exists(path + '.lock'))


# ---------------------------------------------------------------- claude-mem, installed by Switchboard

def no_network():
    """Refuse every connection that is not to this Mac, in this process, while on."""
    real = socket.socket.connect

    def connect(self, address):
        host = address[0] if isinstance(address, tuple) else None
        if host is not None and not (str(host).startswith('127.') or host in ('localhost', '::1')):
            raise AssertionError('a connection left the Mac: %r' % (address,))
        return real(self, address)
    return mock.patch.object(socket.socket, 'connect', connect)


def recent_chat(mac, age=10):
    d = os.path.join(mac.cd, 'projects', '-Users-x-code')
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, 'chat.jsonl')
    with open(p, 'w') as f:
        f.write('{"type":"user"}\n')
    t = time.time() - age
    os.utime(p, (t, t))
    I._SEEN.clear()


class TestClaudeMemInstall(unittest.TestCase):
    def setUp(self):
        self.mac = Mac(self, settings=USER_SETTINGS, cfg={'rc': True})
        self.start = tree(self.mac.home)

    def test_a_the_plan_names_every_file_key_and_host_and_changes_nothing(self):
        p = I.plan('claude-mem')
        lines = '\n'.join(I.plan_detail(p))
        short = I.plan_lines(p)
        self.assertEqual(short[0], 'Installs claude-mem 15.18.3 and starts it.')
        self.assertEqual(short[-1], 'Off takes out what it adds. Your notes stay.')
        self.assertLessEqual(len(short), 6)
        for want in ('known_marketplaces.json: "thedotmack"', 'installed_plugins.json: "claude-mem@thedotmack"',
                     'settings.json: enabledPlugins."claude-mem@thedotmack"', '~/.local/bin/claude-mem',
                     'CLAUDE_MEM_SKIP_SUBAGENT_OBSERVATIONS = "true"', 'CLAUDE_MEM_AUTO_UPDATE = "false"',
                     'CLAUDE_MEM_FILE_READ_GATE_ENABLED = "false"', 'CLAUDE_MEM_QUOTA_FALLBACK_PROVIDER = "off"',
                     'CLAUDE_MEM_QUOTA_THRESHOLD_SEVEN_DAY = "0.85"', 'registry.npmjs.org', 'github.com',
                     'its notes stay when you turn it off'):
            self.assertIn(want, lines)
        self.assertRegex(p['id'], r'^[0-9a-f]{16}$')
        self.assertEqual(tree(self.mac.home), self.start)
        self.assertEqual([c['name'] for c in self.mac.calls()], ['node', 'bun', 'uv'])     # only asked versions
        r = I.begin('claude-mem', 'not-the-plan')
        self.assertEqual(r['state'], 'plan')
        self.assertEqual(tree(self.mac.home), self.start)

    def test_bcdj_install_writes_the_gate_first_records_the_effects_and_undo_gives_every_byte_back(self):
        with no_network():
            j = self.mac.install()
        self.assertEqual(j['state'], 'done', j)
        self.assertEqual(j['percent'], 100)
        self.assertIn('installed and running on port %d' % self.mac.port, j['line'])
        # (b) the gate was in claude-mem's settings when its installer ran, and it is still there
        call = [c for c in self.mac.calls('npx') if c['args'][2] == 'install'][0]
        self.assertEqual(call['cm_settings']['CLAUDE_MEM_SKIP_SUBAGENT_OBSERVATIONS'], 'true')
        self.assertEqual(call['cm_settings']['CLAUDE_MEM_AUTO_UPDATE'], 'false')
        cm = json.loads(self.mac.read(os.path.join(self.mac.home, '.claude-mem', 'settings.json')))
        for k, v in (('CLAUDE_MEM_SKIP_SUBAGENT_OBSERVATIONS', 'true'), ('CLAUDE_MEM_AUTO_UPDATE', 'false'),
                     ('CLAUDE_MEM_FILE_READ_GATE_ENABLED', 'false'), ('CLAUDE_MEM_QUOTA_FALLBACK_PROVIDER', 'off'),
                     ('CLAUDE_MEM_QUOTA_THRESHOLD_SEVEN_DAY', '0.85'), ('CLAUDE_MEM_PROVIDER', 'claude')):
            self.assertEqual(cm[k], v)
        # (h) never --disable-auto-memory; exactly the documented flags, no shell, telemetry off in the environment
        pkg = os.path.join(self.mac.data, 'packs', 'claude-mem', 'claude-mem-15.18.3.tgz')     # the checked package
        self.assertEqual(call['args'], ['--yes', 'file:' + pkg, 'install', '--ide', 'claude-code',
                                        '--provider', 'claude', '--model', 'haiku', '--no-auto-start'])
        self.assertEqual(call['env']['DO_NOT_TRACK'], '1')
        self.assertEqual(call['env']['CLAUDE_MEM_ONLINE_OPTIN'], 'false')
        self.assertTrue(call['env']['npm_config_cache'].startswith(self.mac.data))
        self.assertNotIn(SECRET, json.dumps(call['env']))
        # telemetry off through its npm command (its hook launcher does not know the command), and it took
        self.assertIn(['telemetry', 'disable'], [c['args'][2:] for c in self.mac.calls('npx')])
        self.assertFalse([c for c in self.mac.calls('claude-mem') if c['args'][:1] == ['telemetry']])
        self.assertTrue(os.path.isfile(os.path.join(self.mac.home, '.claude-mem', 'telemetry.json')))
        self.assertEqual(self.mac.state()['installs']['claude_mem']['notes'], [])
        # (c) the record matches what the installer did, and never holds the secret in settings.json
        rec = self.mac.state()['installs']['claude_mem']
        self.assertEqual(rec['state'], 'installed')
        self.assertEqual(sorted(os.path.relpath(p, self.mac.home) for p in rec['created']),
                         ['.claude/plugins/cache/thedotmack', '.claude/plugins/data/claude-mem-thedotmack',
                          '.claude/plugins/marketplaces/thedotmack', '.local/bin/claude-mem'])
        self.assertNotIn(SECRET, json.dumps(self.mac.state()))
        self.assertNotIn('say done', json.dumps(self.mac.state()))
        # settings.json: the person's own bytes plus the one member (the installer wrote it whole, 2-space)
        settings = self.mac.read(os.path.join(self.mac.cd, 'settings.json'))
        self.assertTrue(settings.startswith(USER_SETTINGS[:USER_SETTINGS.index(b'"enabledPlugins"')]))
        self.assertEqual(F.without_member(settings, ['enabledPlugins', 'claude-mem@thedotmack']), USER_SETTINGS)
        # its own background update is off
        km = json.loads(self.mac.read(os.path.join(self.mac.cd, 'plugins', 'known_marketplaces.json')))
        self.assertIs(km['thedotmack']['autoUpdate'], False)
        # (j) the PATH line it appended to ~/.zshrc is recorded
        self.assertEqual([r['appended'] for r in rec['rc']], ['\n# claude-mem\nexport PATH="$HOME/.local/bin:$PATH"\n'])
        # the worker answers
        self.assertTrue(CMI.health(self.mac.port)['ok'])
        # (d) off: the whole home is as it was, but for claude-mem's notes
        lines = self.mac.off()
        self.assertEqual(lines, ['claude-mem: taken out; your notes stay in ~/.claude-mem.'])
        after = tree(self.mac.home)
        changed = sorted(k for k in set(after) | set(self.start) if after.get(k) != self.start.get(k))
        self.assertEqual([k for k in changed if not k.startswith('.claude-mem')], [])
        self.assertFalse(CMI.health(self.mac.port, 0.5)['ok'])
        self.assertIsNone(self.mac.state().get('installs', {}).get('claude_mem'))
        self.assertIsNone(self.mac.state()['claude_mem'])
        self.assertTrue(os.path.isfile(os.path.join(self.mac.home, '.claude-mem', 'settings.json')))

    def test_e_undo_refuses_and_changes_nothing_when_a_recorded_entry_was_edited(self):
        self.assertEqual(self.mac.install()['state'], 'done')
        path = os.path.join(self.mac.cd, 'plugins', 'installed_plugins.json')
        obj = json.loads(self.mac.read(path))
        obj['plugins']['claude-mem@thedotmack'][0]['version'] = '99.0.0'
        self.mac.write(path, (json.dumps(obj, indent=2) + '\n').encode())
        before = tree(self.mac.home)
        with self.assertRaises(C.Refused) as e:
            self.mac.off()
        self.assertIn('changed since claude-mem was installed', str(e.exception))
        self.assertEqual(tree(self.mac.home), before)
        self.assertTrue(CMI.health(self.mac.port)['ok'])                 # not even the worker was stopped

    def test_f_a_user_hook_an_env_secret_and_their_own_plugins_stay(self):
        self.mac.install()
        got = json.loads(self.mac.read(os.path.join(self.mac.cd, 'settings.json')))
        self.assertEqual(got['env']['ANTHROPIC_API_KEY'], SECRET)
        self.assertEqual(got['hooks'], json.loads(USER_SETTINGS)['hooks'])
        self.assertEqual(got['enabledPlugins'], {'mine@local': True, 'claude-mem@thedotmack': True})
        self.mac.off()
        self.assertEqual(self.mac.read(os.path.join(self.mac.cd, 'settings.json')), USER_SETTINGS)

    def test_g_installer_failure_leaves_a_partial_record_a_repair_path_and_the_gate(self):
        self.mac.cfg['fail_after'] = 'registered'
        self.mac.save_cfg()
        j = self.mac.install()
        self.assertEqual(j['state'], 'failed')
        self.assertEqual(j['step'], 3)
        self.assertIn('Stopped at step 3 of 5 (Install)', j['line'])
        self.assertIn('tree-sitter download failed', j['line'])
        rec = self.mac.state()['installs']['claude_mem']
        self.assertEqual(rec['state'], 'partial')
        self.assertEqual(self.mac.state()['claude_mem']['owned']['CLAUDE_MEM_SKIP_SUBAGENT_OBSERVATIONS']['new_raw'],
                         '"true"')
        v = I.view('claude-mem', True)
        self.assertTrue(v['stopped'])
        # Repair runs it again from the start, on the first record (what was there before the first try)
        self.mac.cfg['fail_after'] = None
        self.mac.save_cfg()
        r = I.repair('claude-mem', wait=True)
        self.assertEqual(r['state'], 'installing')
        self.assertEqual(I.job_view('claude-mem')['state'], 'done', I.job_view('claude-mem'))
        self.mac.off()
        after = tree(self.mac.home)
        self.assertEqual([k for k in set(after) | set(self.start)
                          if after.get(k) != self.start.get(k) and not k.startswith('.claude-mem')], [])

    def test_gate_changed_by_its_installer_stops_before_start(self):
        self.mac.cfg['clobber_gate'] = True
        self.mac.save_cfg()
        j = self.mac.install()
        self.assertEqual(j['state'], 'failed')
        self.assertIn('Stopped at step 4 of 5 (Verify)', j['line'])
        self.assertIn('helpers would be watched', j['line'])
        self.assertFalse(CMI.health(self.mac.port, 0.5)['ok'])

    def test_i_turning_it_on_again_changes_nothing(self):
        self.mac.install()
        before = tree(self.mac.home)
        n = len(self.mac.calls())
        self.assertEqual(I.begin('claude-mem', 'anything')['state'], 'present')
        self.assertEqual(tree(self.mac.home), before)
        self.assertEqual(len(self.mac.calls()), n)

    def test_already_installed_by_the_person_is_not_installed_again(self):
        os.makedirs(os.path.join(self.mac.cd, 'plugins'))
        with open(os.path.join(self.mac.cd, 'plugins', 'installed_plugins.json'), 'w') as f:
            json.dump({'version': 2, 'plugins': {'claude-mem@thedotmack': [{'scope': 'user'}]}}, f)
        self.assertEqual(I.begin('claude-mem', 'x')['state'], 'present')
        self.assertEqual(self.mac.calls(), [])

    def test_blocked_on_low_battery_and_low_disk(self):
        I.KW['battery'] = (False, 20)
        self.assertIn('on battery at 20%', I.plan('claude-mem')['blocked'])
        I.KW['battery'] = (True, 20)
        I.KW['free'] = 100 << 20
        self.assertIn('Free some space first', I.plan('claude-mem')['blocked'])
        with self.assertRaises(C.Refused):
            I.begin('claude-mem', 'x')


class TestClaudeMemFetchesWhatItNeeds(unittest.TestCase):
    def test_no_node_bun_or_uv_on_the_mac_all_three_come_from_their_pins_and_go_again(self):
        mac = Mac(self, tools=(), settings=USER_SETTINGS)
        dl = Downloads(self, cfg={'rc': False})
        I.KW['pins'] = dl.pins
        start = tree(mac.home)
        p = I.plan('claude-mem')
        self.assertIn('nodejs.org', p['hosts'])
        self.assertEqual(len([u for u in p['uses'] if 'from' in u]), 3)
        j = mac.install()
        self.assertEqual(j['state'], 'done', j)
        self.assertEqual(sorted(dl.files.asked), ['/bun-release.zip', '/claude-mem-15.18.3.tgz', '/node-release.tar.gz',
                                                  '/uv-release.tar.gz'])
        self.assertTrue(os.path.isfile(os.path.join(mac.home, '.bun', 'bin', 'bun')))
        self.assertTrue(os.path.islink(os.path.join(mac.home, '.local', 'bin', 'node')))   # no node anywhere else
        mac.off()
        after = tree(mac.home)
        self.assertEqual([k for k in set(after) | set(start)
                          if after.get(k) != start.get(k) and not k.startswith('.claude-mem')], [])
        self.assertFalse(os.path.exists(os.path.join(mac.data, 'packs', 'runtimes')))
        self.assertFalse(os.path.exists(os.path.join(mac.data, 'packs', 'npm-cache')))


class TestKeepRunning(unittest.TestCase):
    def setUp(self):
        self.mac = Mac(self)
        self.assertEqual(self.mac.install()['state'], 'done')
        I.HEALTH.update(at=0, ok=None, tries=0, next=0)

    def tick(self, now):
        I.HEALTH['at'] = 0
        return I.health_tick(now)

    def test_up_is_left_alone(self):
        n = len(self.mac.calls('claude-mem'))
        self.assertEqual(self.tick(time.time()), 'ok')
        self.assertEqual(len(self.mac.calls('claude-mem')), n)
        self.assertEqual(I.HEALTH['line'], 'Running on port %d.' % self.mac.port)

    def test_down_and_claude_code_used_lately_starts_it_once(self):
        self.mac.stop_worker()
        time.sleep(0.3)
        recent_chat(self.mac)
        self.assertEqual(self.tick(time.time()), 'started')
        self.assertTrue(CMI.health(self.mac.port)['ok'])
        starts = [c for c in self.mac.calls('claude-mem') if c['args'] == ['start']]
        self.assertEqual(len(starts), 2)                     # the install's, and this one

    def test_down_with_no_claude_code_lately_waits_for_the_next_session(self):
        self.mac.stop_worker()
        time.sleep(0.3)
        recent_chat(self.mac, age=2 * 3600)
        self.assertEqual(self.tick(time.time()), 'idle')
        self.assertIn('starts with your next Claude Code session', I.HEALTH['line'])
        self.assertFalse(CMI.health(self.mac.port, 0.5)['ok'])

    def test_a_start_that_fails_backs_off_1_5_15_minutes(self):
        self.mac.stop_worker()
        time.sleep(0.3)
        recent_chat(self.mac)
        self.mac.cfg['no_start'] = True
        self.mac.save_cfg()
        I.KW['start_wait'] = 0.5
        now = time.time()
        self.assertEqual(self.tick(now), 'failed')
        self.assertEqual(I.HEALTH['next'], now + 60)
        self.assertEqual(self.tick(now + 30), 'waiting')
        self.assertEqual(self.tick(now + 61), 'failed')
        self.assertEqual(I.HEALTH['next'], now + 61 + 300)
        self.assertEqual(self.tick(now + 400), 'failed')
        self.assertEqual(I.HEALTH['next'], now + 400 + 900)
        self.assertEqual(self.tick(now + 2000), 'failed')
        self.assertEqual(I.HEALTH['next'], now + 2000 + 900)
        self.assertIn('Not running.', I.HEALTH['line'])

    def test_port_held_by_another_program_is_said_and_nothing_is_killed_or_started(self):
        self.mac.stop_worker()
        time.sleep(0.3)
        recent_chat(self.mac)
        other = ThreadingHTTPServer(('127.0.0.1', self.mac.port), type('H', (BaseHTTPRequestHandler,), {
            'do_GET': lambda h: (h.send_response(200), h.end_headers(), h.wfile.write(b'{"hello": 1}')),
            'log_message': lambda h, *a: None}))
        self.addCleanup(other.server_close)
        self.addCleanup(other.shutdown)
        threading.Thread(target=other.serve_forever, daemon=True).start()
        n = len(self.mac.calls('claude-mem'))
        self.assertEqual(self.tick(time.time()), 'port')
        self.assertEqual(I.HEALTH['line'], 'Port %d is in use by another program.' % self.mac.port)
        self.assertEqual(len(self.mac.calls('claude-mem')), n)

    def test_a_worker_that_hangs_is_not_answering(self):
        self.mac.stop_worker()
        time.sleep(0.3)
        hang = ThreadingHTTPServer(('127.0.0.1', self.mac.port), type('H', (BaseHTTPRequestHandler,), {
            'do_GET': lambda h: time.sleep(5), 'log_message': lambda h, *a: None}))
        self.addCleanup(hang.server_close)
        threading.Thread(target=hang.serve_forever, daemon=True).start()
        t0 = time.time()
        self.assertEqual(CMI.health(self.mac.port, 0.5)['why'], 'hang')
        self.assertLess(time.time() - t0, 3)
        recent_chat(self.mac, age=2 * 3600)
        self.assertEqual(self.tick(time.time()), 'idle')
        self.assertTrue(I.HEALTH['line'].startswith('Not answering.'))
        hang.shutdown()

    def test_nothing_when_the_switch_is_off(self):
        self.mac.off()
        self.assertIsNone(self.tick(time.time()))
        self.assertIsNone(I.HEALTH['line'])


class TestWhatItWatchesGap(unittest.TestCase):
    def test_a_headless_claude_p_run_carries_no_agent_fields_so_it_is_watched_like_a_chat(self):
        event = {'hook_event_name': 'PostToolUse', 'session_id': 's1', 'cwd': '/Users/x/scripts/nightly',
                 'tool_name': 'Bash', 'tool_input': {'command': 'ls'}, 'tool_response': {}}
        settings = {M.CM_SKIP: 'true', M.CM_OBSERVER: 'true'}
        self.assertTrue(M.cm_would_observe(event, settings, {}, '/Users/x'))
        helper = dict(event, agent_id='a1', agent_type='general-purpose')
        self.assertFalse(M.cm_would_observe(helper, settings, {}, '/Users/x'))
        # the known gap's answer: its folder in CLAUDE_MEM_EXCLUDED_PROJECTS
        settings[M.CM_EXCLUDED] = '/Users/x/scripts/**'
        self.assertFalse(M.cm_would_observe(event, settings, {}, '/Users/x'))


# ---------------------------------------------------------------- codegraph, installed by Switchboard

CC_JSON = (b'{\n  "numStartups": 12,\n  "mcpServers": {\n    "notes": {\n      "type": "stdio",\n      "command": '
           b'"notes-server"\n    }\n  },\n  "projects": {}\n}\n')
OTHER_AGENTS = {'.cursor/mcp.json': b'{"mcpServers": {}}\n', '.codex/config.toml': b'[mcp_servers]\n',
                '.gemini/settings.json': b'{}\n', '.codeium/windsurf/mcp_config.json': b'{"mcpServers": {}}\n'}


class TestCodegraphInstall(unittest.TestCase):
    def setUp(self):
        self.mac = Mac(self, tools=(), settings=b'{\n  "permissions": {\n    "allow": [\n      "Bash(ls:*)"\n    ]\n  }\n}\n',
                       cc_json=CC_JSON, rc=None)
        for rel, data in OTHER_AGENTS.items():
            self.mac.write(os.path.join(self.mac.home, rel), data)
        self.dl = Downloads(self)
        I.KW['pins'] = self.dl.pins
        self.start = tree(self.mac.home)

    def test_the_plan_changes_nothing_and_names_the_permission_it_grants(self):
        p = I.plan('codegraph')
        lines = '\n'.join(I.plan_detail(p))
        self.assertEqual(I.plan_lines(p), ['Installs codegraph v1.6.2.', 'Downloads it from GitHub, about 0 MB, checked against its pin. It takes about 285 MB.',
                                          'Adds its MCP server to Claude Code, an allow rule for its tools, and the code map block.',
                                          'Off takes out what it adds. Project maps stay.'])
        for want in ('codegraph v1.6.2', '~/.codegraph/versions/v1.6.2', 'mcpServers.codegraph',
                     'permissions.allow "mcp__codegraph__*" (its tools run without asking)', 'the code map block',
                     'github.com', 'Project maps stay when you turn it off'):
            self.assertIn(want, lines)
        self.assertEqual(tree(self.mac.home), self.start)
        self.assertEqual(self.dl.files.asked, [])

    def test_install_writes_one_entry_one_allow_rule_and_the_block_then_off_gives_every_byte_back(self):
        with no_network():
            j = self.mac.install('codegraph')
        self.assertEqual(j['state'], 'done', j)
        self.assertEqual(j['names'], ['Download', 'Verify', 'Unpack', 'Connect', 'Check'])
        w = CGI.where(self.mac.cd, self.mac.home)
        # the program, where codegraph's own install.sh puts it
        self.assertEqual(os.readlink(w['current']), os.path.join(w['versions'], 'v1.6.2'))
        self.assertEqual(os.readlink(w['bin_link']), os.path.join(w['versions'], 'v1.6.2', 'bin', 'codegraph'))
        # the MCP entry: codegraph's own, with the command as a full path
        with open(os.path.join(FX, 'codegraph', 'print-config-claude.json')) as f:
            want = json.load(f)['mcpServers']['codegraph']
        got = json.loads(self.mac.read(self.mac.claude_json()))
        self.assertEqual(dict(got['mcpServers']['codegraph'], command='codegraph'), want)
        self.assertEqual(got['mcpServers']['codegraph']['command'], os.path.join(w['current'], 'bin', 'codegraph'))
        self.assertEqual(got['mcpServers']['notes'], {'type': 'stdio', 'command': 'notes-server'})
        st = json.loads(self.mac.read(os.path.join(self.mac.cd, 'settings.json')))
        self.assertEqual(st['permissions']['allow'], ['Bash(ls:*)', 'mcp__codegraph__*'])
        self.assertNotIn('hooks', st)                                     # no prompt hook
        md = self.mac.read(os.path.join(self.mac.cd, 'CLAUDE.md')).decode()
        self.assertIn('codegraph_explore', md)
        self.assertNotIn('CODEGRAPH_START', md)
        # nothing but codegraph's own help was run, never `install` or `uninstall`, telemetry off
        args = [c['args'] for c in self.mac.calls() + Downloads.calls(self.dl)]
        self.assertIn(['telemetry', 'off'], args)
        self.assertFalse(any(a[:1] in (['install'], ['uninstall']) for a in args), args)
        # other agents' files untouched
        for rel, data in OTHER_AGENTS.items():
            self.assertEqual(self.mac.read(os.path.join(self.mac.home, rel)), data)
        # off: every byte back, the program gone, nothing killed
        self.assertEqual(self.mac.off('codegraph'), ['codegraph: taken out; project maps stay.'])
        self.assertEqual(tree(self.mac.home), self.start)
        self.assertIsNone(self.mac.state().get('installs', {}).get('codegraph'))

    def test_project_maps_guard_the_roots_and_stay_unless_asked(self):
        self.assertEqual(self.mac.install('codegraph')['state'], 'done')
        for bad in (self.mac.home, '/', os.path.join(self.mac.home, 'Desktop'), os.path.join(self.mac.home, 'Documents'),
                    os.path.join(self.mac.home, 'Downloads'), 'relative/project', os.path.join(self.mac.home, 'nope')):
            if bad.startswith(self.mac.home) and bad != self.mac.home and not bad.endswith('nope'):
                os.makedirs(bad, exist_ok=True)
            with self.subTest(bad=bad):
                with self.assertRaises(C.Refused):
                    CGI.map_project(bad, home=self.mac.home, environ=self.mac.environ)
        proj = os.path.join(self.mac.home, 'code', 'shop')
        os.makedirs(proj)
        r = CGI.map_project(proj, home=self.mac.home, environ=self.mac.environ)
        self.assertEqual(r['path'], proj)
        init = [c for c in Downloads.calls(self.dl) if c['args'][:1] == ['init']][-1]
        self.assertEqual(init['args'], ['init', proj, '-y'])
        self.assertTrue(os.path.isdir(os.path.join(proj, '.codegraph')))
        CGI.refresh_project(proj, home=self.mac.home, environ=self.mac.environ)
        self.assertEqual([c['args'] for c in Downloads.calls(self.dl)][-1], ['sync', proj])
        self.mac.off('codegraph')
        self.assertTrue(os.path.isdir(os.path.join(proj, '.codegraph')))        # maps stay unless asked

    def test_a_tool_already_here_is_used_and_nothing_is_installed(self):
        cg = dict(json.loads(CC_JSON))
        cg['mcpServers']['codegraph'] = {'type': 'stdio', 'command': 'codegraph', 'args': ['serve', '--mcp']}
        self.mac.write(self.mac.claude_json(), (json.dumps(cg, indent=2) + '\n').encode())
        self.assertEqual(I.begin('codegraph', 'x')['state'], 'present')
        self.assertEqual(self.dl.files.asked, [])

    def test_a_bad_checksum_stops_at_verify_and_off_leaves_nothing(self):
        self.dl.pins['codegraph']['sha256']['arm64'] = 'f' * 64
        j = self.mac.install('codegraph')
        self.assertEqual(j['state'], 'failed')
        self.assertIn('Stopped at step 2 of 5 (Verify)', j['line'])
        self.assertIn('did not match its pinned checksum', j['line'])
        self.assertFalse(os.path.exists(os.path.join(self.mac.home, '.codegraph')))
        self.mac.off('codegraph')
        self.assertEqual(tree(self.mac.home), self.start)

    def test_repair_after_a_stop_part_way_keeps_the_first_before_and_off_still_gives_every_byte_back(self):
        self.dl.set(codegraph_version='9.9.9')            # the release answers wrong: stopped at Check, all else done
        j = self.mac.install('codegraph')
        self.assertIn('Stopped at step 5 of 5 (Check)', j['line'])
        rec = self.mac.state()['installs']['codegraph']
        self.assertTrue(rec['root_made'])
        self.assertFalse(rec['mcp']['before']['present'])
        self.dl.set()
        r = I.repair('codegraph', wait=True)
        self.assertEqual(r['state'], 'installing')
        self.assertEqual(I.job_view('codegraph')['state'], 'done', I.job_view('codegraph'))
        rec = self.mac.state()['installs']['codegraph']
        self.assertTrue(rec['root_made'])                 # not "there before", though the first try made it
        self.assertFalse(rec['mcp']['before']['present'])
        self.assertEqual(len([e for e in rec['made'] if e['kind'] == 'codegraph']), 1)
        self.mac.off('codegraph')
        self.assertEqual(tree(self.mac.home), self.start)

    def test_an_edited_entry_refuses_the_undo_and_changes_nothing(self):
        self.mac.install('codegraph')
        cj = json.loads(self.mac.read(self.mac.claude_json()))
        cj['mcpServers']['codegraph']['args'] = ['serve', '--mcp', '--verbose']
        self.mac.write(self.mac.claude_json(), (json.dumps(cj, indent=2) + '\n').encode())
        before = tree(self.mac.home)
        with self.assertRaises(C.Refused):
            self.mac.off('codegraph')
        self.assertEqual(tree(self.mac.home), before)

    def test_without_claude_config_dir_the_entry_goes_in_the_home_claude_json(self):
        mac = Mac(self, tools=(), cc_json=CC_JSON, claude_config_dir=False, rc=None)
        I.KW['pins'] = self.dl.pins
        start = tree(mac.home)
        self.assertEqual(mac.install('codegraph')['state'], 'done')
        self.assertIn('codegraph', json.loads(mac.read(os.path.join(mac.home, '.claude.json')))['mcpServers'])
        self.assertFalse(os.path.exists(os.path.join(mac.cd, '.claude.json')))
        mac.off('codegraph')
        self.assertEqual(tree(mac.home), start)


class TestClaudeJsonRace(unittest.TestCase):
    """Claude Code rewrites ~/.claude.json while it runs. Two writers: one that takes its lock (as Claude Code does),
    one that ignores it and writes from an old copy. Either way the file is never corrupt, and the entry is there
    exactly once, or Switchboard says to try again."""

    def writer(self, path, stop, honest):
        n = 0
        old = json.loads(CC_JSON)
        while not stop.is_set():
            n += 1
            if honest:
                try:
                    with F.claude_json_lock(path, wait=5):
                        obj = json.loads(C.read_bytes(path))
                        obj['numStartups'] = n
                        C.atomic_write(path, (json.dumps(obj, indent=2) + '\n').encode(), 0o600)
                except C.Refused:
                    pass
            else:
                obj = dict(old, numStartups=n)                   # its copy from before Switchboard's edit
                C.atomic_write(path, (json.dumps(obj, indent=2) + '\n').encode(), 0o600)
            time.sleep(0.05)

    def run_race(self, honest):
        d = tmp()
        path = os.path.join(d, '.claude.json')
        with open(path, 'wb') as f:
            f.write(CC_JSON)
        w = {'claude_json': path}
        entry = {'type': 'stdio', 'command': '/x/codegraph', 'args': ['serve', '--mcp'], 'alwaysLoad': True}
        stop = threading.Event()
        t = threading.Thread(target=self.writer, args=(path, stop, honest), daemon=True)
        t.start()
        try:
            try:
                info = CGI.add_mcp(w, entry, tries=3, settle=0.1)
                said = None
            except C.Refused as e:
                info, said = None, str(e)
            time.sleep(0.2)
        finally:
            stop.set()
            t.join(5)
        obj = json.loads(C.read_bytes(path))                         # never corrupt
        servers = obj.get('mcpServers', {})
        return info, said, servers, entry

    def test_a_writer_that_takes_claude_codes_lock_keeps_the_entry(self):
        info, said, servers, entry = self.run_race(True)
        self.assertIsNone(said)
        self.assertEqual(servers.get('codegraph'), entry)
        self.assertEqual(list(servers).count('codegraph'), 1)
        self.assertIn('notes', servers)

    def test_a_writer_that_ignores_it_is_caught_by_the_read_back(self):
        info, said, servers, entry = self.run_race(False)
        if said is not None:
            self.assertIn('Try again in a minute', said)
        else:
            self.assertIsNotNone(info)


# ---------------------------------------------------------------- the switches: API, loop, page

from switchboard import claudecode, core, demo, server
import test_app


class TestSwitches(unittest.TestCase):
    """The page's /api/pack and /api/claudecode with the real install code on fakes."""

    def setUp(self):
        self.mac = Mac(self, settings=USER_SETTINGS)
        claudecode.KW.update(claude_dir=self.mac.cd, home=self.mac.home, environ=self.mac.environ, path=self.mac.environ['PATH'],
                             battery=(True, 100), free=10 ** 12)
        self.addCleanup(claudecode.KW.clear)

    def events(self):
        return [e['text'] for e in core.load()['events']]

    def wait_job(self, pack):
        for _ in range(400):
            j = I.JOBS.get(pack)
            if j and j['state'] != 'running':
                return j
            time.sleep(0.05)
        self.fail('the install did not end')

    def test_plan_then_consent_installs_shows_progress_and_off_takes_it_out(self):
        r = server.act('/api/pack', {'pack': 'claudemem', 'on': True})
        self.assertEqual(r['state'], 'plan')
        self.assertEqual(self.events(), [])                              # only shown: nothing happened yet
        self.assertFalse(server.claudecode_view()['claudemem']['on'])
        with self.assertRaises(core.SwitchError):                         # an old or made-up plan is not consent
            r2 = server.act('/api/pack', {'pack': 'claudemem', 'on': True, 'consent': 'deadbeefdeadbeef'})
            self.assertEqual(r2['state'], 'plan')
            raise core.SwitchError('asked again')
        r = server.act('/api/pack', {'pack': 'claudemem', 'on': True, 'consent': r['id']})
        self.assertEqual(r['state'], 'installing')
        self.assertEqual(r['job']['of'], 5)
        j = self.wait_job('claude-mem')
        self.assertEqual(j['state'], 'done', j)
        v = server.claudecode_view()['claudemem']
        self.assertTrue(v['on'])
        self.assertTrue(v['installed'])
        self.assertTrue(v['setup']['by_switchboard'])
        self.assertTrue(v['setup']['health']['ok'])
        self.assertEqual(v['setup']['job']['percent'], 100)
        ev = self.events()
        self.assertIn('Installing claude-mem', ev)
        self.assertTrue(any(e.startswith('claude-mem 15.18.3 is installed and running') for e in ev), ev)
        # the worker went down: the view says so, and Repair starts it again
        self.mac.stop_worker()
        time.sleep(0.3)
        I.HEALTH.update(ok=None, looked=0)
        self.assertFalse(server.claudecode_view()['claudemem']['setup']['health']['ok'])
        r = server.act('/api/pack', {'pack': 'claudemem', 'do': 'repair'})
        self.assertEqual(r['state'], 'installing')
        self.assertEqual(self.wait_job('claude-mem')['state'], 'done')
        self.assertTrue(CMI.health(self.mac.port)['ok'])
        self.assertIn(['repair'], [c['args'][2:] for c in self.mac.calls('npx')])
        # off: exactly what was added goes; its notes stay
        r = server.act('/api/pack', {'pack': 'claudemem', 'on': False})
        self.assertIn('claude-mem: taken out; your notes stay in ~/.claude-mem.', r['says'])
        self.assertFalse(server.claudecode_view()['claudemem']['on'])
        self.assertEqual(self.mac.read(os.path.join(self.mac.cd, 'settings.json')), USER_SETTINGS)

    def test_a_click_while_an_install_runs_is_refused_plainly(self):
        self.mac.cfg['sleep'] = 1
        self.mac.save_cfg()
        p = server.act('/api/pack', {'pack': 'claudemem', 'on': True})
        server.act('/api/pack', {'pack': 'claudemem', 'on': True, 'consent': p['id']})
        with self.assertRaises(core.SwitchError) as e:
            server.act('/api/pack', {'pack': 'house', 'on': True})
        self.assertIn('An install is running', str(e.exception))
        self.wait_job('claude-mem')

    def test_tidy_takes_out_the_installs_before_their_settings(self):
        self.mac.install()
        I.KW['pins'] = Downloads(self).pins
        self.mac.write(self.mac.claude_json(), CC_JSON)
        self.mac.install('codegraph')
        said = claudecode.undo_all()
        self.assertIn('codegraph: taken out; project maps stay.', said)
        self.assertIn('claude-mem: taken out; your notes stay in ~/.claude-mem.', said)
        self.assertIn('claude-mem settings: put back as it was.', said)
        self.assertLess(said.index('claude-mem: taken out; your notes stay in ~/.claude-mem.'),
                        said.index('claude-mem settings: put back as it was.'))
        self.assertEqual(self.mac.read(os.path.join(self.mac.cd, 'settings.json')), USER_SETTINGS)
        self.assertEqual(self.mac.read(self.mac.claude_json()), CC_JSON)
        self.assertFalse(os.path.exists(os.path.join(self.mac.home, '.codegraph')))

    def test_the_loop_looks_at_claude_mems_worker_beside_the_weekly_cap(self):
        seen = []
        with mock.patch.object(server, 'tick', return_value=None), mock.patch.object(server, 'keep_tick'), \
                mock.patch.object(server, 'mac_tick'), mock.patch.object(server.engine.HOST, 'ensure'), \
                mock.patch.object(server.keep, 'login_changed', return_value=False), \
                mock.patch.object(claudecode, 'cap_tick'), \
                mock.patch.object(claudecode, 'health_tick', side_effect=lambda **kw: seen.append(kw)), \
                mock.patch.object(core, 'DEMO', False):
            claudecode.HEALTH.update(at=0, running=False)
            server.loop(ticks=1)
            for _ in range(50):
                if seen:
                    break
                time.sleep(0.02)
        self.assertEqual(len(seen), 1)
        self.assertIs(seen[0]['say'], server._note_event)
        claudecode.HEALTH.update(running=False)


SETUP_RUNNING = {'by_switchboard': False, 'version': None, 'stopped': False, 'notes': [],
                 'job': {'state': 'running', 'step': 3, 'of': 5, 'name': 'Install', 'percent': 30, 'line': '',
                         'names': ['Runtimes', 'Settings', 'Install', 'Verify', 'Start'], 'log': ['$ npx --yes claude-mem@15.18.3 install']}}
SETUP_FAILED = dict(SETUP_RUNNING, job=dict(SETUP_RUNNING['job'], state='failed',
                                            line='Stopped at step 3 of 5 (Install): claude-mem’s installer stopped (exit 1).'))
SETUP_DOWN = {'by_switchboard': True, 'version': '15.18.3', 'stopped': False, 'notes': [], 'job': None,
              'health': {'ok': False, 'line': 'Not running. It starts with your next Claude Code session.', 'port': 37701}}


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class PackPage(test_app.ChromePage):
    """Settings › In Claude Code, in headless Chrome (demo page, with the claude-code view and its answers stood in)."""

    def view(self, mem_setup=None, cm_setup=None, mem_on=False, cm_on=False):
        base = demo.claudecode

        def fake():
            v = base()
            v['claudemem'].update(on=mem_on, installed=False, setup=mem_setup)
            v['codemap'].update(on=cm_on, found=[], nothing_to_add=False, setup=cm_setup)
            return v
        p = mock.patch.object(demo, 'claudecode', fake)
        p.start()
        self.addCleanup(p.stop)

    def answer_plan(self):
        real = demo.act

        def act(path, body):
            if path == '/api/pack' and body.get('on') is True and not body.get('consent'):
                return {'state': 'plan', 'id': 'abc123abc123abcd',
                        'lines': ['Installs claude-mem 15.18.3.', 'Adds ~/.local/bin/claude-mem: its hook launcher.',
                                  'Downloads from registry.npmjs.org, github.com, about 350 MB.'],
                        'detail': ['Installs claude-mem 15.18.3.', 'Adds ~/.claude/settings.json: enabledPlugins.x.',
                                   'Adds ~/.local/bin/claude-mem: its hook launcher.', 'Sets in ~/.claude-mem: A = 1.']}
            if path == '/api/pack' and body.get('do') in ('map', 'refresh'):
                return {'says': 'Mapped %s.' % body['folder']}
            if path == '/api/pack' and body.get('consent'):
                return {'state': 'installing', 'job': SETUP_RUNNING['job']}
            return real(path, body)
        p = mock.patch.object(demo, 'act', act)
        p.start()
        self.addCleanup(p.stop)

    def test_the_switch_shows_the_plan_and_install_sends_it_back_as_consent(self):
        self.view()
        self.answer_plan()
        v = self.run_page("""
      location.hash = '#settings'; await wait(900);
      $('[data-do="pack"][data-pack="claudemem"]').click(); await wait(600);
      const plan = all('#setup-claudemem .plan li').map((x) => x.textContent);
      const buttons = all('#setup-claudemem button').map((x) => x.textContent);
      $('[data-do="packInstall"][data-pack="claudemem"]').click(); await wait(600);
      done({plan, buttons, sent: sent.filter((x) => x[0] === '/api/pack').map((x) => x[1]), after: !!$('#setup-claudemem .plan')});
""")
        self.assertEqual(v['plan'][0], 'Installs claude-mem 15.18.3.')
        self.assertEqual(len(v['plan']), 3)
        self.assertEqual(v['buttons'], ['Install', 'Cancel', 'Details'])
        self.assertEqual(v['sent'], [{'pack': 'claudemem', 'on': True},
                                     {'pack': 'claudemem', 'on': True, 'consent': 'abc123abc123abcd'}])
        self.assertFalse(v['after'])

    def test_cancel_drops_the_plan_and_sends_nothing(self):
        self.view()
        self.answer_plan()
        v = self.run_page("""
      location.hash = '#settings'; await wait(900);
      $('[data-do="pack"][data-pack="claudemem"]').click(); await wait(600);
      $('[data-do="packCancel"]').click(); await wait(300);
      done({plan: !!$('#setup-claudemem'), sent: sent.filter((x) => x[0] === '/api/pack').length});
""")
        self.assertEqual(v, {'plan': False, 'sent': 1})

    def test_a_running_install_has_a_bar_and_its_step(self):
        self.view(mem_setup=SETUP_RUNNING, mem_on=True)
        v = self.run_page("""
      location.hash = '#settings'; await wait(1100);
      const bar = $('#setup-claudemem [role="progressbar"]');
      done({now: bar && bar.getAttribute('aria-valuenow'), step: ($('#setup-claudemem .hint') || {}).textContent,
            aside: all('#row-claudemem .r-ctl .muted').map((x) => x.textContent), cap: !!$('#cmCap')});
""")
        self.assertEqual(v['now'], '30')
        self.assertEqual(v['step'], 'Step 3 of 5 · Install')
        self.assertIn('Installing · 30%', v['aside'])
        self.assertFalse(v['cap'])                         # the cap comes once it is installed

    def test_a_stopped_install_says_where_and_offers_repair_and_its_log(self):
        self.view(mem_setup=SETUP_FAILED, mem_on=True)
        v = self.run_page("""
      location.hash = '#settings'; await wait(1100);
      const line = ($('#setup-claudemem .hint') || {}).textContent;
      $('[data-do="packLog"]').click(); await wait(300);
      const log = ($('#setup-claudemem pre') || {}).textContent;
      $('[data-do="packRepair"]').click(); await wait(500);
      done({line, log, sent: sent.filter((x) => x[0] === '/api/pack').map((x) => x[1])});
""")
        self.assertEqual(v['line'], 'Stopped at step 3 of 5 (Install): claude-mem’s installer stopped (exit 1).')
        self.assertIn('npx --yes claude-mem@15.18.3 install', v['log'])
        self.assertEqual(v['sent'], [{'pack': 'claudemem', 'do': 'repair'}])

    def test_a_worker_that_is_down_offers_repair(self):
        self.view(mem_setup=SETUP_DOWN, mem_on=True)
        v = self.run_page("""
      location.hash = '#settings'; await wait(1100);
      done({line: ($('#setup-claudemem .hint') || {}).textContent, repair: !!$('[data-do="packRepair"]'), cap: !!$('#cmCap')});
""")
        self.assertEqual(v, {'line': 'Not running. It starts with your next Claude Code session.', 'repair': True, 'cap': True})

    def test_the_code_map_with_nothing_found_can_be_turned_on(self):
        self.view()
        v = self.run_page("""
      location.hash = '#settings'; await wait(1100);
      const b = $('[data-do="pack"][data-pack="codemap"]');
      done({disabled: b.disabled, title: b.closest('.row').querySelector('.r-desc').textContent, steps: !!$('#sec-settings .steps')});
""")
        self.assertFalse(v['disabled'])
        self.assertEqual(v['title'], 'Claude Code asks codegraph before it searches files.')    # the words under its title
        self.assertFalse(v['steps'])                       # no "install it yourself" steps


    def test_details_show_every_file_and_key(self):
        self.view()
        self.answer_plan()
        v = self.run_page("""
      location.hash = '#settings'; await wait(900);
      $('[data-do="pack"][data-pack="claudemem"]').click(); await wait(600);
      const short = all('#setup-claudemem .plan li').length;
      $('[data-do="packDetail"]').click(); await wait(300);
      done({short, long: all('#setup-claudemem .plan li').map((x) => x.textContent), btn: $('[data-do="packDetail"]').textContent});
""")
        self.assertEqual(v['short'], 3)
        self.assertEqual(len(v['long']), 4)
        self.assertIn('Sets in ~/.claude-mem: A = 1.', v['long'])
        self.assertEqual(v['btn'], 'Less')

    def test_project_maps_under_the_code_map(self):
        self.view(cm_setup={'by_switchboard': True, 'version': 'v1.6.2', 'stopped': False, 'notes': [], 'job': None,
                            'map_tool': 'codegraph', 'projects': [{'path': '/Users/x/code/shop', 'at': 1, 'tool': 'codegraph'}]},
                  cm_on=True)
        self.answer_plan()
        v = self.run_page("""
      location.hash = '#settings'; await wait(1100);
      const rows = all('#setup-codemap .klist li .lab').map((x) => x.textContent);
      $('#mapFolder').value = '/Users/x/code/api';
      $('[data-k="packMap"]').click(); await wait(500);
      $('[data-do="packMap"][data-again="1"]').click(); await wait(500);
      done({rows, sent: sent.filter((x) => x[0] === '/api/pack').map((x) => x[1])});
""")
        self.assertEqual(v['rows'], ['/Users/x/code/shop'])
        self.assertEqual(v['sent'], [{'pack': 'codemap', 'do': 'map', 'folder': '/Users/x/code/api'},
                                     {'pack': 'codemap', 'do': 'refresh', 'folder': '/Users/x/code/shop'}])


class TestGraphifyWhenPresent(unittest.TestCase):
    """Step 6: graphify is used when it is already here: extract --code-only and update, never its claude or hook
    commands, never an install."""

    def test_map_and_refresh_with_graphify(self):
        mac = Mac(self, tools=('graphify',), rc=None)
        proj = os.path.join(mac.home, 'code', 'api')
        os.makedirs(proj)
        self.assertEqual(CGI.map_tool(mac.environ, mac.home)[0], 'graphify')
        r = CGI.map_project(proj, environ=mac.environ, home=mac.home)
        self.assertEqual(r['tool'], 'graphify')
        self.assertEqual(r['line'], 'Graph report 3 files, 12 nodes')           # the report's first lines
        CGI.refresh_project(proj, environ=mac.environ, home=mac.home)
        self.assertEqual([c['args'] for c in mac.calls('graphify')], [['extract', proj, '--code-only'], ['update', proj]])
        self.assertTrue(os.path.isfile(os.path.join(proj, 'graphify-out', 'graph.json')))
        self.assertEqual([m['path'] for m in CGI.maps()], [proj])
        for bad in (mac.home, '/'):
            with self.assertRaises(C.Refused):
                CGI.map_project(bad, environ=mac.environ, home=mac.home)
        with self.assertRaises(C.Refused):
            CGI.refresh_project(os.path.join(mac.home, 'code'), environ=mac.environ, home=mac.home)   # never mapped
        self.assertTrue(all(c['args'][0] in ('extract', 'update') for c in mac.calls('graphify')))
        with self.assertRaises(R.NotAllowed):
            R.check([os.path.join(mac.bin, 'graphify'), 'claude', 'install'])
        with self.assertRaises(R.NotAllowed):
            R.check([os.path.join(mac.bin, 'graphify'), 'hook', 'install'])

    def test_no_map_tool_says_so(self):
        mac = Mac(self, tools=(), rc=None)
        proj = os.path.join(mac.home, 'p')
        os.makedirs(proj)
        with self.assertRaises(C.Refused) as e:
            CGI.map_project(proj, environ=mac.environ, home=mac.home)
        self.assertIn('Turn on the code map first', str(e.exception))


class TestTerminal(unittest.TestCase):
    """python3 -m switchboard.packs.install: the same switch for engineers and checks."""

    def test_plan_on_without_yes_on_with_yes_and_off(self):
        mac = Mac(self, settings=USER_SETTINGS)
        start = tree(mac.home)
        out = io.StringIO()
        self.assertEqual(I.main(['plan', 'claude-mem'], out), 0)
        self.assertIn('Adds ~/.local/bin/claude-mem: its hook launcher.', out.getvalue())
        out = io.StringIO()
        self.assertEqual(I.main(['on', 'claude-mem'], out), 0)
        self.assertTrue(out.getvalue().endswith('Run again with --yes to install.\n'))
        self.assertEqual(tree(mac.home), start)                    # no --yes: nothing changed
        out = io.StringIO()
        self.assertEqual(I.main(['on', 'claude-mem', '--yes'], out), 0)
        lines = out.getvalue().splitlines()
        self.assertEqual(lines[:5], ['Step 1 of 5: Runtimes', 'Step 2 of 5: Settings', 'Step 3 of 5: Install',
                                     'Step 4 of 5: Verify', 'Step 5 of 5: Start'])
        self.assertTrue(lines[5].startswith('claude-mem 15.18.3 is installed and running on port'))
        out = io.StringIO()
        self.assertEqual(I.main(['off', 'claude-mem'], out), 0)
        self.assertIn('claude-mem: taken out; your notes stay in ~/.claude-mem.', out.getvalue())
        after = tree(mac.home)
        self.assertEqual([k for k in set(after) | set(start) if after.get(k) != start.get(k)
                          and not k.startswith('.claude-mem')], [])
