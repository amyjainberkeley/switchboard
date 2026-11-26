"""What a real install of the code map and claude-mem showed (2026-11, codegraph v1.6.2), kept as tests.

Both were installed for real from a try-out download: a throwaway home in a sandbox, the pinned downloads and npm over
HTTPS, through the page's own buttons.

The code map:
- The real codegraph v1.6.2 (the pinned download, its checksum checked, offline) refuses
  `codegraph init <folder> -y --local-only`: "error: unknown option '--local-only'", exit 1. The fake codegraph
  refuses an option the real one does not take, the way the real one does, so a test sees such a command fail.
- `codegraph init <folder> -y` maps it and writes only <folder>/.codegraph/.
- `sync <folder>` and `status <folder> --json` work as Switchboard runs them.
- `serve --mcp`, the MCP entry Switchboard writes, answers Claude Code's handshake and lists its tool.

The sizes the plans give:
- claude-mem takes about 2.1 GB once installed: its npm and Bun package caches, its plugin, its launcher, Node, Bun
  and uv.
- codegraph takes 283 MB.
- Each pin carries the space it was measured to take (disk_mb). The plans say how much each takes, and say
  "downloads" only for a download.
"""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..'))
import test_packs_install as T
from switchboard.packs import codegraph_install as CGI
from switchboard.packs import install as I
from switchboard.packs import runner as R

FAKE = os.path.join(HERE, 'fixtures', 'packs', 'fake_tools.py')


class TheFakeRefusesWhatCodegraphRefuses(unittest.TestCase):
    """The fake codegraph answers an option it does not know the way codegraph 1.6.2 does."""

    def fake(self, *args):
        conf = tempfile.mkdtemp(prefix='sb-cgfake-')
        home = tempfile.mkdtemp(prefix='sb-cghome-')
        with open(os.path.join(conf, 'fake.json'), 'w') as f:
            json.dump({}, f)
        return subprocess.run([sys.executable, FAKE, '--conf', conf, 'codegraph'] + list(args),
                              env={'HOME': home, 'PATH': '/usr/bin:/bin'}, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True, timeout=30)

    def test_an_unknown_option_ends_with_codegraphs_own_error(self):
        proj = tempfile.mkdtemp(prefix='sb-cgproj-')
        r = self.fake('init', proj, '-y', '--local-only')
        self.assertEqual((r.returncode, r.stderr.strip()), (1, "error: unknown option '--local-only'"))
        self.assertFalse(os.path.exists(os.path.join(proj, '.codegraph')))
        self.assertEqual(self.fake('sync', proj, '--local-only').returncode, 1)
        self.assertEqual(self.fake('status', proj, '--pretty').returncode, 1)

    def test_the_options_codegraph_takes_work(self):
        proj = tempfile.mkdtemp(prefix='sb-cgproj-')
        self.assertEqual(self.fake('init', proj, '-y').returncode, 0)
        self.assertTrue(os.path.isdir(os.path.join(proj, '.codegraph')))
        self.assertEqual(self.fake('sync', proj).returncode, 0)
        r = self.fake('status', proj, '--json')
        self.assertEqual(r.returncode, 0)
        self.assertEqual(json.loads(r.stdout)['initialized'], True)


class MapsWithWhatCodegraphKnows(unittest.TestCase):
    setUp = T.TestCodegraphInstall.setUp            # a made-up Mac with codegraph's made-up release on loopback

    def test_a_project_is_mapped_with_init_folder_yes(self):
        self.assertEqual(self.mac.install('codegraph')['state'], 'done')
        proj = os.path.join(self.mac.home, 'code', 'shop')
        os.makedirs(proj)
        r = CGI.map_project(proj, home=self.mac.home, environ=self.mac.environ)
        self.assertEqual(r['path'], proj)
        init = [c for c in self.dl.calls() if c['args'][:1] == ['init']][-1]
        self.assertEqual(init['args'], ['init', proj, '-y'])
        self.assertTrue(os.path.isdir(os.path.join(proj, '.codegraph')))
        CGI.refresh_project(proj, home=self.mac.home, environ=self.mac.environ)
        self.assertEqual([c['args'] for c in self.dl.calls()][-1], ['sync', proj])

    def test_the_runner_refuses_init_with_local_only(self):
        cg = os.path.join(self.mac.home, '.codegraph', 'current', 'bin', 'codegraph')
        proj = os.path.join(self.mac.home, 'code', 'shop')
        with self.assertRaises(R.NotAllowed):
            R.check([cg, 'init', proj, '-y', '--local-only'])
        self.assertEqual(R.check([cg, 'init', proj, '-y']), 'codegraph')

    def test_codegraphs_plan_says_its_download_and_the_space_it_takes(self):
        p = I.plan('codegraph')
        self.assertEqual(p['mb'], 285)                                    # measured: 283 MB unpacked
        self.assertEqual(I.plan_lines(p)[1], 'Downloads it from GitHub, about %d MB, checked against its pin. '
                                             'It takes about 285 MB.' % p['download_mb'])
        self.assertIn('Downloads from github.com. Takes about 285 MB.', I.plan_detail(p))


class ClaudeMemsPlanSaysWhatItTakes(unittest.TestCase):
    def test_with_node_bun_and_uv_to_fetch_it_takes_about_two_gigabytes(self):
        T.Mac(self, tools=(), settings=T.USER_SETTINGS)
        dl = T.Downloads(self, cfg={'rc': False})
        I.KW['pins'] = dl.pins
        p = I.plan('claude-mem')
        self.assertEqual(p['mb'], 1830 + 190 + 85 + 30)                   # claude-mem, Node, Bun, uv: as measured
        self.assertEqual(I.plan_lines(p)[1], 'Downloads it from npm, and Node 22.23.5, Bun 1.4.4 and uv 0.13.8 from '
                                             'nodejs.org and GitHub. It takes about 2.1 GB.')
        self.assertIn('Downloads from registry.npmjs.org, nodejs.org, github.com. Takes about 2.1 GB.',
                      I.plan_detail(p))

    def test_less_free_space_than_that_and_a_margin_stops_it_before_anything_changes(self):
        T.Mac(self, tools=(), settings=T.USER_SETTINGS)
        dl = T.Downloads(self, cfg={'rc': False})
        I.KW['pins'] = dl.pins
        I.KW['battery'] = (True, 100)
        I.KW['free'] = (2135 << 20) + (2 << 30) - 1                      # what it takes, plus the margin, less a byte
        self.assertIn('Free some space first', I.plan('claude-mem')['blocked'])
        I.KW['free'] = (2135 << 20) + (2 << 30)
        self.assertIsNone(I.plan('claude-mem')['blocked'])



if __name__ == '__main__':
    unittest.main()
