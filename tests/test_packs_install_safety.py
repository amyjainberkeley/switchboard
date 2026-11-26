import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# Safety rules of the packs that install themselves. The fixtures are test_packs_install's own (a throwaway home,
# fake node, npx, bun, uv, claude-mem and codegraph, a fake worker, made-up releases on 127.0.0.1); nothing real is
# installed, and nothing leaves the Mac.
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard.packs import claudemem_install as CMI
from switchboard.packs import codegraph_install as CGI
from switchboard.packs import common as C
from switchboard.packs import facts as F
from switchboard.packs import fetch as FE
from switchboard.packs import install as I
from switchboard.packs import runner as R
import test_packs_install as TP  # its fixtures; its tests run from its own file


class Died(BaseException):
    """Switchboard stopping part-way (a quit, a crash, the Mac asleep too long): nothing after this point runs in
    its process. Its installer, started in a session of its own, goes on."""


def rel(paths, home):
    return sorted(os.path.relpath(p, home) for p in paths)


def changed(start, after):
    return sorted(k for k in set(after) | set(start) if after.get(k) != start.get(k) and not k.startswith('.claude-mem'))


# ---------------------------------------------------------------- undo takes out only what claude-mem added

OTHERS = ['.local/bin/pipx-tool', '.claude/plugins/marketplaces/someone-elses/', '.claude/plugins/data/other-plugin/',
          '.claude/plugins/cache/other-market/']


class OnlyWhatClaudeMemAdded(unittest.TestCase):
    def setUp(self):
        self.mac = TP.Mac(self, settings=TP.USER_SETTINGS, cfg={'rc': True})
        self.start = TP.tree(self.mac.home)

    def test_what_lands_beside_it_during_the_install_stays_when_it_is_turned_off(self):
        self.mac.cfg['also_make'] = OTHERS             # another plugin, a tool in ~/.local/bin, during its install
        self.mac.save_cfg()
        j = self.mac.install()
        self.assertEqual(j['state'], 'done', j)
        rec = self.mac.state()['installs']['claude_mem']
        self.assertEqual(rel(rec['created'], self.mac.home),
                         ['.claude/plugins/cache/thedotmack', '.claude/plugins/data/claude-mem-thedotmack',
                          '.claude/plugins/marketplaces/thedotmack', '.local/bin/claude-mem'])
        self.mac.off()
        for p in OTHERS:
            self.assertTrue(os.path.exists(os.path.join(self.mac.home, p)), p)
        kept = set(x.rstrip('/') for x in OTHERS) | {'.local', '.local/bin', '.claude/plugins', '.claude/plugins/data',
                                                    '.claude/plugins/cache', '.claude/plugins/marketplaces'}
        self.assertEqual(changed(self.start, TP.tree(self.mac.home)), sorted(kept))

    def test_created_counts_only_claude_mems_own_names(self):
        w = CMI.where(self.mac.cd, self.mac.home)
        before = CMI.snapshot(w)
        for p in OTHERS + ['.claude/plugins/marketplaces/thedotmack/', '.local/bin/claude-mem']:
            full = os.path.join(self.mac.home, p)
            os.makedirs(full if p.endswith('/') else os.path.dirname(full), exist_ok=True)
            if not p.endswith('/'):
                with open(full, 'w') as f:
                    f.write('x')
        self.assertEqual(rel(CMI.created_since(before, CMI.snapshot(w), w), self.mac.home),
                         ['.claude/plugins/marketplaces/thedotmack', '.local/bin/claude-mem'])


# ---------------------------------------------------------------- an install cut short keeps its undo

class AnInstallCutShort(unittest.TestCase):
    def setUp(self):
        self.mac = TP.Mac(self, settings=TP.USER_SETTINGS, cfg={'rc': True})
        self.start = TP.tree(self.mac.home)

    def cut_short(self):
        """Steps 1 and 2 run, then its installer, which finishes; Switchboard stops before writing down what it did."""
        ctx = I._ctx('claude-mem', R.new_log('claude-mem'), {'cap': None, 'consent': 'plan'})
        CMI.step_runtimes(ctx)
        CMI.step_settings(ctx)
        with mock.patch.object(CMI, 'record_effects', side_effect=Died('stopped')):
            with self.assertRaises(Died):
                CMI.step_install(ctx)
        self.assertTrue(os.path.isfile(os.path.join(self.mac.home, '.local', 'bin', 'claude-mem')))

    def back_but_for_spacing(self, lines):
        """Everything as it was, but settings.json's spacing: its installer wrote it whole while Switchboard was
        stopped, and Switchboard keeps nothing of that file but facts (its settings are as they were, and it says so)."""
        self.assertEqual(changed(self.start, TP.tree(self.mac.home)), ['.claude/settings.json'])
        path = os.path.join(self.mac.cd, 'settings.json')
        self.assertEqual(TP.json.loads(self.mac.read(path)), TP.json.loads(TP.USER_SETTINGS))
        self.assertIn("claude-mem's installer wrote ~/.claude/settings.json whole while Switchboard was stopped, so "
                      "its spacing is the installer's.", lines)

    def test_off_takes_out_everything_it_added(self):
        self.cut_short()
        self.assertTrue(I.view('claude-mem', True)['stopped'])
        self.back_but_for_spacing(self.mac.off())

    def test_repair_runs_it_again_and_off_still_takes_out_everything(self):
        self.cut_short()
        self.assertEqual(I.repair('claude-mem', wait=True)['state'], 'installing')
        self.assertEqual(I.job_view('claude-mem')['state'], 'done', I.job_view('claude-mem'))
        self.back_but_for_spacing(self.mac.off())



# ---------------------------------------------------------------- claude-mem's package is checked before it runs

class TheCheckedPackageRuns(unittest.TestCase):
    def setUp(self):
        self.mac = TP.Mac(self, settings=TP.USER_SETTINGS, cfg={'rc': True})
        self.start = TP.tree(self.mac.home)
        self.pkg = os.path.join(self.mac.data, 'packs', 'claude-mem', 'claude-mem-15.18.3.tgz')

    def npx_specs(self):
        return [c['args'][1] for c in self.mac.calls('npx')]

    def test_a_package_that_does_not_match_its_pin_never_runs(self):
        self.mac.files.files['/claude-mem-15.18.3.tgz'] = TP.cm_package(extra=b'not what npm recorded')
        j = self.mac.install()
        self.assertEqual(j['state'], 'failed', j)
        self.assertIn('Stopped at step 3 of 5 (Install)', j['line'])
        self.assertIn('did not match its pinned checksum', j['line'])
        self.assertEqual(self.npx_specs(), [])                          # nothing of it ran
        self.assertFalse(os.path.exists(self.pkg))                      # and nothing of it was kept
        self.mac.off()
        self.assertEqual(changed(self.start, TP.tree(self.mac.home)), [])

    def test_npx_is_only_ever_given_the_checked_file(self):
        j = self.mac.install()
        self.assertEqual(j['state'], 'done', j)
        self.assertTrue(self.npx_specs())
        self.assertEqual(set(self.npx_specs()), {'file:' + self.pkg})   # install and telemetry: never a name to look up
        with open(self.pkg, 'rb') as f:
            self.assertEqual(TP.npm_integrity(f.read()), self.mac.pins['claude_mem']['npm_integrity'])
        self.assertEqual(self.mac.files.asked, ['/claude-mem-15.18.3.tgz'])
        self.assertEqual(self.mac.state()['installs']['claude_mem']['package']['integrity'],
                         self.mac.pins['claude_mem']['npm_integrity'])
        self.mac.off()
        self.assertFalse(os.path.exists(os.path.dirname(self.pkg)))     # off takes it away

    def test_a_package_changed_after_the_check_is_not_run_by_start_or_repair(self):
        self.assertEqual(self.mac.install()['state'], 'done')
        self.mac.stop_worker()
        os.unlink(os.path.join(self.mac.home, '.local', 'bin', 'claude-mem'))      # start goes through npx then
        with open(self.pkg, 'ab') as f:
            f.write(b'changed since')
        n = len(self.npx_specs())
        rec = CMI.record()
        w = CMI.where(self.mac.cd, self.mac.home)
        self.assertFalse(CMI.start(w, self.mac.environ, rec, wait=1)['ok'])
        self.assertEqual(len(self.npx_specs()), n)                      # the changed file never ran
        self.assertEqual(I.repair('claude-mem', wait=True)['state'], 'installing')   # repair fetches it again, checked
        self.assertEqual(I.job_view('claude-mem')['state'], 'done', I.job_view('claude-mem'))
        with open(self.pkg, 'rb') as f:
            self.assertEqual(TP.npm_integrity(f.read()), self.mac.pins['claude_mem']['npm_integrity'])

    def test_the_runner_never_lets_npx_look_a_name_up(self):
        npx = os.path.join(self.mac.bin, 'npx')
        env = R.environment([self.mac.bin, '/usr/bin', '/bin'], {}, self.mac.environ, self.mac.home)
        for spec in ('claude-mem@15.18.3', 'claude-mem', '/tmp/claude-mem-15.18.3.tgz', 'file:/tmp/claude-mem-15.18.3.tgz',
                     os.path.join(os.path.dirname(self.pkg), 'other-1.0.0.tgz'),
                     self.pkg):                                         # the bare path: npx would run it as a command
            with self.subTest(spec=spec):
                with self.assertRaises(R.NotAllowed):
                    R.run([npx, '--yes', spec, 'start'], env)
        self.assertEqual(self.npx_specs(), [])

    def test_the_pin_is_npms_own_record_of_the_version(self):
        pins = CMI.fetch.load_pins()['claude_mem']
        self.assertEqual(pins['url'], 'https://registry.npmjs.org/claude-mem/-/claude-mem-{version}.tgz')
        self.assertRegex(pins['npm_integrity'], r'^sha512-[A-Za-z0-9+/]{86}==$')
        self.assertGreater(pins['bytes_max'], 32272607)                 # npm's unpackedSize for 15.18.3



# ---------------------------------------------------------------- only claude-mem's own worker is claude-mem

def stranger(test, port):
    """Some other program on claude-mem's port that answers its health check the way claude-mem does."""
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({'status': 'ok'}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(('127.0.0.1', port), H)
    test.addCleanup(srv.server_close)
    test.addCleanup(srv.shutdown)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class OnlyItsOwnWorker(unittest.TestCase):
    def setUp(self):
        self.mac = TP.Mac(self, settings=TP.USER_SETTINGS)
        self.start = TP.tree(self.mac.home)

    def test_a_stranger_that_answers_ok_is_not_taken_for_claude_mem(self):
        stranger(self, self.mac.port)
        h = CMI.health(self.mac.port)
        self.assertEqual((h['ok'], h['why']), (False, 'other'))
        self.assertIn('Port %d is in use by another program; claude-mem gets a free one.' % self.mac.port,
                      I.plan('claude-mem')['notes'])
        j = self.mac.install()                                       # claude-mem moves to a free port and runs there
        self.assertEqual(j['state'], 'done', j)
        port = self.mac.state()['installs']['claude_mem']['port']
        self.assertNotEqual(port, self.mac.port)
        self.assertTrue(CMI.health(port)['ok'])
        w = CMI.where(self.mac.cd, self.mac.home)
        self.assertEqual(self.mac.off(), ['claude-mem: taken out; your notes stay in ~/.claude-mem.'])   # not blocked
        self.assertFalse(CMI.health(port, 0.5)['ok'])
        self.assertEqual(CMI.health(self.mac.port)['why'], 'other')   # the stranger is untouched
        self.assertEqual(changed(self.start, TP.tree(self.mac.home)), [])
        self.assertTrue(CMI.stop(w, self.mac.environ, None))           # nothing of claude-mem's runs there


# ---------------------------------------------------------------- the worker listens where Switchboard looks

class ThePortFromTheEnvironment(unittest.TestCase):
    def test_a_port_set_in_switchboards_environment_reaches_claude_mems_own_commands(self):
        mac = TP.Mac(self, settings=TP.USER_SETTINGS)
        start = TP.tree(mac.home)
        env_port = TP.free_port()
        mac.environ['CLAUDE_MEM_WORKER_PORT'] = str(env_port)          # claude-mem's settings name mac.port
        j = mac.install()
        self.assertEqual(j['state'], 'done', j)
        self.assertIn('running on port %d' % env_port, j['line'])
        self.assertTrue(CMI.health(env_port)['ok'])
        for c in mac.calls('npx') + mac.calls('claude-mem'):
            self.assertEqual(c['env'].get('CLAUDE_MEM_WORKER_PORT'), str(env_port), c['args'])
        mac.off()
        self.assertFalse(CMI.health(env_port, 0.5)['ok'])              # stopped where it ran
        self.assertEqual(changed(start, TP.tree(mac.home)), [])



# ---------------------------------------------------------------- the code map's entries are recorded first

class CodeMapRecordedBeforeWritten(unittest.TestCase):
    SETTINGS = b'{\n  "permissions": {\n    "allow": [\n      "Bash(ls:*)"\n    ]\n  }\n}\n'

    def setUp(self):
        self.mac = TP.Mac(self, tools=(), settings=self.SETTINGS, cc_json=TP.CC_JSON, rc=None)
        I.KW['pins'] = TP.Downloads(self).pins
        self.start = TP.tree(self.mac.home)

    def stop_right_after_writing(self, path):
        """Steps 1-3, then step 4 until the write to `path` is done: Switchboard stops right after it."""
        ctx = I._ctx('codegraph', R.new_log('codegraph'), {})
        for step in (CGI.step_download, CGI.step_verify, CGI.step_unpack):
            step(ctx)
        real = F.write_if_same

        def write_then_stop(target, expect, new, *a, **kw):
            done = real(target, expect, new, *a, **kw)
            if target == path:
                raise Died('stopped')
            return done
        with mock.patch.object(F, 'write_if_same', write_then_stop):
            with self.assertRaises(Died):
                CGI.step_connect(ctx)

    def repair_then_off(self):
        self.assertEqual(I.repair('codegraph', wait=True)['state'], 'installing')
        self.assertEqual(I.job_view('codegraph')['state'], 'done', I.job_view('codegraph'))
        self.mac.off('codegraph')
        self.assertEqual(self.mac.read(self.mac.claude_json()), TP.CC_JSON)
        self.assertEqual(self.mac.read(os.path.join(self.mac.cd, 'settings.json')), self.SETTINGS)
        self.assertEqual(changed(self.start, TP.tree(self.mac.home)), [])

    def test_a_stop_right_after_the_mcp_entry_is_written(self):
        self.stop_right_after_writing(self.mac.claude_json())
        self.assertIn(b'"codegraph"', self.mac.read(self.mac.claude_json()))
        self.repair_then_off()

    def test_a_stop_right_after_the_allow_rule_is_written(self):
        self.stop_right_after_writing(os.path.join(self.mac.cd, 'settings.json'))
        self.assertIn(b'mcp__codegraph__', self.mac.read(os.path.join(self.mac.cd, 'settings.json')))
        self.repair_then_off()



# ---------------------------------------------------------------- a download stays bounded, and leaves nothing

OLD_CURL = """#!/usr/bin/python3 -IB
# A curl that ignores --max-filesize when the size is not known first (curl before 8.4 documents that it does): it
# writes 64 MB to the -o file, 1 MB at a time.
import sys, time
dest = sys.argv[sys.argv.index('-o') + 1]
with open(dest, 'wb') as f:
    for _ in range(64):
        f.write(b'x' * (1 << 20))
        f.flush()
        time.sleep(0.02)
"""


class DownloadsStayBounded(unittest.TestCase):
    def setUp(self):
        self.mac = TP.Mac(self, tools=())
        self.curl = os.path.join(self.mac.root, 'oldcurl', 'curl')
        os.makedirs(os.path.dirname(self.curl))
        with open(self.curl, 'w') as f:
            f.write(OLD_CURL)
        os.chmod(self.curl, 0o755)

    def test_a_body_past_the_cap_is_stopped_whatever_curl_does(self):
        dest = os.path.join(self.mac.root, 'download.bin')
        env = R.environment(['/usr/bin', '/bin'], {}, self.mac.environ, self.mac.home)
        r = R.run(R.curl_argv('http://127.0.0.1:9/x', dest, 1 << 20, curl=self.curl), env, timeout=60)
        self.assertFalse(r.ok)
        self.assertLess(os.path.getsize(dest), 16 << 20)            # stopped near the cap, not after 64 MB
        self.assertIn('more than', r.out)

    def test_fetch_keeps_nothing_of_it(self):
        work = os.path.join(self.mac.root, 'work')
        a = {'name': 'node', 'version': '22.23.5', 'url': 'http://127.0.0.1:9/node.tar.gz', 'bytes': 1 << 20,
             'sha256': '0' * 64}
        env = R.environment(['/usr/bin', '/bin'], {}, self.mac.environ, self.mac.home)
        with self.assertRaises(C.Refused):
            FE.download(a, work, env, curl=self.curl)
        self.assertEqual(os.listdir(work), [])

    def test_codegraphs_failed_check_leaves_no_download_folder(self):
        dl = TP.Downloads(self)
        dl.pins['codegraph']['sha256'] = {'arm64': '0' * 64, 'x86_64': '0' * 64}
        I.KW['pins'] = dl.pins
        j = self.mac.install('codegraph')
        self.assertEqual(j['state'], 'failed', j)
        self.assertIn('did not match its pinned checksum', j['line'])
        self.assertFalse(os.path.exists(FE.download_dir()), os.listdir(C.packs_dir()))



# ---------------------------------------------------------------- codegraph's undo and its project maps

class CodegraphUndoAndMaps(unittest.TestCase):
    def setUp(self):
        self.mac = TP.Mac(self, tools=(), settings=b'{}\n', cc_json=TP.CC_JSON, rc=None)
        I.KW['pins'] = TP.Downloads(self).pins
        self.start = TP.tree(self.mac.home)
        self.root = os.path.join(self.mac.home, '.codegraph')

    def test_an_upgrade_after_the_install_stays_when_it_is_turned_off(self):
        self.assertEqual(self.mac.install('codegraph')['state'], 'done')
        newer = os.path.join(self.root, 'versions', 'v1.7.0')           # what `codegraph upgrade` leaves
        os.makedirs(os.path.join(newer, 'bin'))
        with open(os.path.join(newer, 'bin', 'codegraph'), 'w') as f:
            f.write('#!/bin/sh\n')
        os.unlink(os.path.join(self.root, 'current'))
        os.symlink(newer, os.path.join(self.root, 'current'))
        self.mac.off('codegraph')
        self.assertTrue(os.path.isfile(os.path.join(newer, 'bin', 'codegraph')))
        self.assertEqual(os.readlink(os.path.join(self.root, 'current')), newer)
        self.assertFalse(os.path.exists(os.path.join(self.root, 'versions', 'v1.6.2')))     # Switchboard's own goes
        self.assertFalse(os.path.exists(os.path.join(self.root, 'telemetry.json')))

    def test_a_plain_install_and_off_still_leave_nothing(self):
        self.assertEqual(self.mac.install('codegraph')['state'], 'done')
        self.mac.off('codegraph')
        self.assertEqual(changed(self.start, TP.tree(self.mac.home)), [])

    def test_hidden_folders_and_the_library_are_never_mapped(self):
        home = os.path.realpath(self.mac.home)
        for rel in ('.ssh', '.config', '.config/nvim', '.aws', '.claude', 'Library/Keychains', 'Library/Mail'):
            p = os.path.join(home, rel)
            os.makedirs(p, exist_ok=True)
            with self.subTest(folder=rel):
                self.assertIsNotNone(CGI.refuse_root(p, home))
        project = os.path.join(home, 'code', 'app')
        os.makedirs(project)
        self.assertIsNone(CGI.refuse_root(project, home))



# ---------------------------------------------------------------- claude-mem's part: a blank file comes back blank

class ClaudeMemGivesABlankFileBack(unittest.TestCase):
    def test_an_empty_or_blank_settings_file(self):
        for data in (b'', b'\n'):
            with self.subTest(start=data):
                mac = TP.Mac(self, settings=data)
                self.assertEqual(mac.install()['state'], 'done')
                mac.off()
                self.assertEqual(mac.read(os.path.join(mac.cd, 'settings.json')), data)


if __name__ == '__main__':
    unittest.main()
