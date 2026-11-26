"""Turning a pack off takes out only the tools Switchboard downloaded, as they were when it put them there.

uv, uvx and bun go into the person's own folders (~/.local/bin, ~/.bun/bin), and node may be linked from ~/.local/bin.
They can replace any of them later (`uv self update`, `bun upgrade`, their own installer, their own link): what is
there then is theirs, and stays. A record written before files carried their SHA-256 is taken out by path, as before.
On tests/test_packs_install.py's fixtures: made-up releases on 127.0.0.1 and fake programs.
"""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import os
import unittest

from switchboard.packs import fetch as FE, install as I, runner as R
from test_packs_install import Downloads, Mac


class ToolsReplacedSinceStay(unittest.TestCase):
    def setUp(self):
        self.mac = Mac(self, tools=())
        self.dl = Downloads(self)
        I.KW['pins'] = self.dl.pins
        self.env = R.environment(FE.SYSTEM_DIRS, environ=self.mac.environ, home=self.mac.home)
        self.local_bin = os.path.join(self.mac.home, '.local', 'bin')

    def fetch(self, fn):
        return fn(self.dl.pins, self.env, self.mac.environ, self.mac.home, machine='arm64')

    def replace(self, path, text):
        os.unlink(path)
        with open(path, 'w') as f:
            f.write(text)
        os.chmod(path, 0o755)

    def test_each_file_placed_is_recorded_with_its_sha256(self):
        uv = self.fetch(FE.ensure_uv)
        bun = self.fetch(FE.ensure_bun)
        for e in [e for e in uv['made'] + bun['made'] if e['kind'] == 'file']:
            self.assertEqual(e.get('sha256'), FE.file_sha256(e['path']), e['path'])
        self.assertEqual(sorted(os.path.basename(e['path']) for e in uv['made'] if e['kind'] == 'file'), ['uv', 'uvx'])

    def test_the_node_link_is_recorded_with_where_it_points(self):
        got = self.fetch(FE.ensure_node)
        link = [e for e in got['made'] if e['kind'] == 'file']
        self.assertEqual(len(link), 1)
        self.assertEqual(link[0]['path'], os.path.join(self.local_bin, 'node'))
        self.assertEqual(link[0].get('link'), os.readlink(link[0]['path']))
        self.assertNotIn('sha256', link[0])

    def test_a_uv_replaced_by_its_own_update_stays_and_the_rest_goes(self):
        got = self.fetch(FE.ensure_uv)
        uv = os.path.join(self.local_bin, 'uv')
        self.replace(uv, '#!/bin/sh\necho "uv 9.9.9 (their own update)"\n')
        kept = []
        gone = FE.remove_made(got['made'], self.mac.home, kept=kept)
        self.assertTrue(os.path.isfile(uv))
        with open(uv) as f:
            self.assertIn('their own update', f.read())
        self.assertFalse(os.path.lexists(os.path.join(self.local_bin, 'uvx')))     # unchanged: it goes
        self.assertIn(os.path.join(self.local_bin, 'uvx'), gone)
        self.assertNotIn(uv, gone)
        self.assertEqual(kept, ['~/.local/bin/uv was changed after Switchboard put it there, so it stays.'])
        self.assertTrue(os.path.isdir(self.local_bin))                             # not empty, so it stays too

    def test_a_bun_replaced_by_bun_upgrade_stays(self):
        got = self.fetch(FE.ensure_bun)
        bun = os.path.join(self.mac.home, '.bun', 'bin', 'bun')
        self.replace(bun, '#!/bin/sh\necho 1.9.0\n')
        kept = []
        FE.remove_made(got['made'], self.mac.home, kept=kept)
        self.assertTrue(os.path.isfile(bun))
        self.assertEqual(kept, ['~/.bun/bin/bun was changed after Switchboard put it there, so it stays.'])

    def test_a_tool_replaced_by_a_link_of_their_own_stays(self):
        got = self.fetch(FE.ensure_uv)
        uv = os.path.join(self.local_bin, 'uv')
        theirs = os.path.join(self.mac.root, 'their-uv')
        with open(theirs, 'w') as f:
            f.write('#!/bin/sh\n')
        os.unlink(uv)
        os.symlink(theirs, uv)
        FE.remove_made(got['made'], self.mac.home, kept=[])
        self.assertEqual(os.readlink(uv), theirs)

    def test_untouched_tools_all_go_as_before(self):
        got = self.fetch(FE.ensure_bun)
        kept = []
        FE.remove_made(got['made'], self.mac.home, kept=kept)
        self.assertFalse(os.path.lexists(os.path.join(self.mac.home, '.bun')))
        self.assertEqual(kept, [])

    def test_the_node_link_goes_only_while_it_points_where_switchboard_pointed_it(self):
        got = self.fetch(FE.ensure_node)
        link = os.path.join(self.local_bin, 'node')
        theirs = os.path.join(self.mac.root, 'their-node')
        os.unlink(link)
        os.symlink(theirs, link)                                     # their own node now, by a link of theirs
        kept = []
        FE.remove_made(got['made'], self.mac.home, kept=kept)
        self.assertEqual(os.readlink(link), theirs)
        self.assertEqual(kept, ['~/.local/bin/node was changed after Switchboard put it there, so it stays.'])
        self.assertFalse(os.path.lexists(os.path.join(self.mac.data, 'packs', 'runtimes')))   # Switchboard's own: gone

    def test_the_node_link_replaced_by_a_file_stays(self):
        got = self.fetch(FE.ensure_node)
        link = os.path.join(self.local_bin, 'node')
        self.replace(link, '#!/bin/sh\necho v24.0.0\n')
        FE.remove_made(got['made'], self.mac.home, kept=[])
        self.assertTrue(os.path.isfile(link) and not os.path.islink(link))

    def test_an_untouched_node_link_goes(self):
        got = self.fetch(FE.ensure_node)
        FE.remove_made(got['made'], self.mac.home, kept=[])
        self.assertFalse(os.path.lexists(os.path.join(self.mac.home, '.local')))

    def test_a_record_from_an_older_build_is_taken_out_by_path_as_before(self):
        got = self.fetch(FE.ensure_uv)
        old = [dict((k, v) for k, v in e.items() if k in ('path', 'kind')) for e in got['made']]
        uv = os.path.join(self.local_bin, 'uv')
        self.replace(uv, '#!/bin/sh\necho changed\n')
        kept = []
        FE.remove_made(old, self.mac.home, kept=kept)
        self.assertFalse(os.path.lexists(uv))
        self.assertEqual(kept, [])

    def test_without_a_list_for_what_stays_it_still_leaves_theirs(self):
        got = self.fetch(FE.ensure_uv)
        uv = os.path.join(self.local_bin, 'uv')
        self.replace(uv, '#!/bin/sh\necho changed\n')
        FE.remove_made(got['made'], self.mac.home)
        self.assertTrue(os.path.isfile(uv))


if __name__ == '__main__':
    unittest.main()
