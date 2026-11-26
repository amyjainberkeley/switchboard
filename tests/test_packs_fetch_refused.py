"""A downloaded Node, Bun or uv whose version check fails is taken out again.

A download can pass its checksum and be put in place, then give no answer to `--version` in time, and the refusal
must not leave it there (install-check case 13 checks ~/.bun/bin/bun). A refused fetch never reaches
the install's record, so nothing would ever take it out. On tests/test_packs_install.py's fixtures: made-up releases on
127.0.0.1, and fake programs that report the version a test sets (a too-old one stands in for a failed check).
"""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import os
import unittest

from switchboard.packs import common as C, fetch as FE, install as I, runner as R
from test_packs_install import Downloads, Mac, tree

LOGS = 'data/packs/logs'


class AFetchThatDoesNotRun(unittest.TestCase):
    def setUp(self):
        self.mac = Mac(self, tools=())
        self.dl = Downloads(self)
        I.KW['pins'] = self.dl.pins
        self.env = R.environment(FE.SYSTEM_DIRS, environ=self.mac.environ, home=self.mac.home)
        self.before = self.files()

    def files(self):
        """The throwaway Mac's files, but Switchboard's own packs folder and its logs (made by any fetch)."""
        return dict((k, v) for k, v in tree(self.mac.root, skip=('fakebin/calls.jsonl',)).items()
                    if k not in ('data', 'data/packs') and not k.startswith(LOGS))

    def refused(self, fn, name):
        with self.assertRaises(C.Refused) as cm:
            fn(self.dl.pins, self.env, self.mac.environ, self.mac.home, machine='arm64')
        self.assertEqual(str(cm.exception), 'The %s Switchboard downloaded did not run on this Mac, so it was removed '
                                            'again. Try again later.' % name)
        self.assertEqual(self.files(), self.before)                        # nothing of it is left anywhere

    def test_bun_comes_out_of_its_folder_again(self):
        self.dl.set(bun_version='0.1.0')
        self.refused(FE.ensure_bun, 'Bun')
        self.assertFalse(os.path.lexists(os.path.join(self.mac.home, '.bun')))
        self.dl.set()
        got = FE.ensure_bun(self.dl.pins, self.env, self.mac.environ, self.mac.home, machine='arm64')
        self.assertTrue(got['fetched'])                                    # so the next try downloads it again

    def test_uv_comes_out_of_local_bin_again_and_a_folder_with_other_things_stays(self):
        local_bin = os.path.join(self.mac.home, '.local', 'bin')
        os.makedirs(local_bin)
        with open(os.path.join(local_bin, 'claude'), 'w') as f:
            f.write('someone else put this here')
        self.before = self.files()
        self.dl.set(uv_version='0.0.1')
        self.refused(FE.ensure_uv, 'uv')
        self.assertEqual(os.listdir(local_bin), ['claude'])

    def test_node_comes_out_of_switchboards_folder_again(self):
        self.dl.set(node_version='v18.20.0')
        self.refused(FE.ensure_node, 'Node')
        self.assertFalse(os.path.lexists(os.path.join(self.mac.data, 'packs', 'runtimes')))
        self.assertFalse(os.path.lexists(os.path.join(self.mac.home, '.local', 'bin', 'node')))

    def test_a_node_an_earlier_install_downloaded_stays_where_its_record_finds_it(self):
        got = FE.ensure_node(self.dl.pins, self.env, self.mac.environ, self.mac.home, machine='arm64')
        own = os.path.join(self.mac.data, 'packs', 'runtimes', 'node-v22.23.5')
        self.assertEqual(got['bin'], os.path.join(own, 'bin'))
        self.dl.set(node_version='v18.20.0')                               # later it no longer answers well
        with self.assertRaises(C.Refused) as cm:
            FE.ensure_node(self.dl.pins, self.env, self.mac.environ, self.mac.home, machine='arm64')
        self.assertEqual(str(cm.exception), 'The Node Switchboard downloaded does not start on this Mac.')
        self.assertTrue(os.path.isdir(own))                                # the earlier record takes it out, not this


if __name__ == '__main__':
    unittest.main()
