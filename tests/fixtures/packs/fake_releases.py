#!/usr/bin/python3 -IB
"""Made-up releases of node, bun, uv and codegraph, and a made-up claude-mem npm package, served on 127.0.0.1, for
checks only (install-check case 13 and the page's screenshots). Never the real ones.

    python3 -I -B fake_releases.py OUT CONF

Writes the archives into OUT, serves them on a port the system picks, and writes OUT/pins.json (packs/pins.json with
these addresses and checksums) and then OUT/port. The programs inside answer as fake_tools.py does, with their
settings in CONF/fake.json and their calls in CONF/calls.jsonl. Runs until it is sent SIGTERM.
"""
import base64
import hashlib
import io
import json
import os
import signal
import sys
import tarfile
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.realpath(__file__))
PINS = os.path.join(HERE, '..', '..', '..', 'switchboard', 'packs', 'pins.json')


def shim(conf, name):
    return ('#!/bin/sh\nexec /usr/bin/python3 -IB %s --conf %s %s "$@"\n'
            % (os.path.join(HERE, 'fake_tools.py'), conf, name)).encode()


def tar_bytes(entries):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tf:
        for name, data, mode, link in entries:
            ti = tarfile.TarInfo(name)
            ti.mtime = 1700000000
            if link is not None:
                ti.type, ti.linkname = tarfile.SYMTYPE, link
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


def main(out, conf):
    os.makedirs(out, exist_ok=True)
    os.makedirs(conf, exist_ok=True)
    if not os.path.exists(os.path.join(conf, 'fake.json')):
        with open(os.path.join(conf, 'fake.json'), 'w') as f:
            f.write('{}')
    blobs = {
        'node': ('node.tar.gz', tar_bytes([('node/bin/node', shim(conf, 'node'), 0o755, None),
                                           ('node/lib/npx-cli.sh', shim(conf, 'npx'), 0o755, None),
                                           ('node/bin/npx', None, 0, '../lib/npx-cli.sh')])),
        'bun': ('bun.zip', zip_bytes([('bun-darwin/bun', shim(conf, 'bun'), 0o755)])),
        'uv': ('uv.tar.gz', tar_bytes([('uv/uv', shim(conf, 'uv'), 0o755, None),
                                       ('uv/uvx', shim(conf, 'uvx'), 0o755, None)])),
        'codegraph': ('codegraph.tar.gz', tar_bytes([('codegraph/bin/codegraph', shim(conf, 'codegraph'), 0o755, None)])),
    }
    manifest = json.dumps({'name': 'claude-mem', 'version': '15.18.3', 'bin': {'claude-mem': 'dist/npx-cli/index.js'}})
    package = tar_bytes([('package/package.json', manifest.encode(), 0o644, None),
                         ('package/dist/npx-cli/index.js', b'// stand-in\n', 0o644, None)])
    files = {'/claude-mem-15.18.3.tgz': package}
    for name, (fname, data) in blobs.items():
        with open(os.path.join(out, fname), 'wb') as f:
            f.write(data)
        files['/' + fname] = data

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
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

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), H)
    port = httpd.server_address[1]
    with open(PINS) as f:
        pins = json.load(f)
    for name, (fname, data) in blobs.items():
        sha = hashlib.sha256(data).hexdigest()
        pins[name]['url'] = 'http://127.0.0.1:%d/%s' % (port, fname)
        pins[name]['sha256'] = {'arm64': sha, 'x86_64': sha}
        pins[name]['bytes'] = {'arm64': len(data), 'x86_64': len(data)}
    pins['claude_mem'].update(url='http://127.0.0.1:%d/claude-mem-{version}.tgz' % port, bytes_max=len(package) + 4096,
                              npm_integrity='sha512-' + base64.b64encode(hashlib.sha512(package).digest()).decode())
    with open(os.path.join(out, 'pins.json'), 'w') as f:
        json.dump(pins, f, indent=1)
    with open(os.path.join(out, 'port'), 'w') as f:
        f.write(str(port))
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
    httpd.serve_forever()


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
