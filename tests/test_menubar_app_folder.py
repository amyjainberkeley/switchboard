import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The menu bar's app folder (switchboard/menubar_wrap.make): the two things that matter while a menu bar may be
# running from it. Making it again with nothing changed touches nothing on disk, and whatever does change is put in
# place by a rename, so a running process keeps the file it opened. Run with:
#   SWITCHBOARD_HOME="$(mktemp -d)/data" python3 -m unittest discover -s tests -p test_menubar_app_folder.py
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import core
from switchboard import menubar_wrap as wrap


def everything(top):
    """Every path under top (top too), with what a rewrite would change: its inode, its modification time, and for a
    link its target. Links are not followed."""
    out = {}
    for folder, dirs, names in os.walk(top):
        # a link to a folder comes in dirs, and is not walked into
        links = [os.path.join(folder, n) for n in dirs if os.path.islink(os.path.join(folder, n))]
        for p in [folder] + links + [os.path.join(folder, n) for n in names]:
            st = os.lstat(p)
            out[os.path.relpath(p, top)] = (st.st_ino, st.st_mtime_ns, os.readlink(p) if os.path.islink(p) else None)
    return out


class TheAppFolder(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix='switchboard-appfolder-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        base = os.path.join(self.tmp, 'python')
        self.exe = os.path.join(base, 'bin', 'python3.12')       # a made-up interpreter: a few bytes
        os.makedirs(os.path.dirname(self.exe))
        with open(self.exe, 'w') as f:
            f.write('#!/bin/sh\necho made-up\n')
        os.chmod(self.exe, 0o755)
        os.utime(self.exe, (1700000000, 1700000000))
        os.makedirs(os.path.join(base, 'lib'))
        with open(os.path.join(base, 'lib', 'libpython3.12.dylib'), 'w') as f:
            f.write('lib')
        self.env = os.path.join(self.tmp, 'env')
        os.makedirs(os.path.join(self.env, 'lib', 'python3.12', 'site-packages'))
        self.where = os.path.join(self.tmp, 'apps')
        self.app = os.path.join(self.where, 'Switchboard.app')

    def make(self):
        return wrap.make(self.where, executable=self.exe, prefix=self.env)

    def test_a_second_make_changes_nothing_on_disk(self):
        self.make()
        before = everything(self.app)
        self.assertIn(os.path.join('Contents', 'MacOS', 'python'), before)
        self.assertIn(os.path.join('Contents', 'lib', 'python3.12'), before)
        time.sleep(0.02)                    # a rewrite now would show in the times, not only the inodes
        self.make()
        self.assertEqual(everything(self.app), before)

    def test_what_changes_is_replaced_by_a_rename(self):
        python = self.make()
        plist = os.path.join(self.app, 'Contents', 'Info.plist')
        link = os.path.join(self.app, 'Contents', 'lib', 'python3.12')
        with open(python, 'rb') as f:
            old_python = f.read()
        with open(plist, 'rb') as f:
            old_plist = f.read()
        # what a running menu bar holds: the files it opened
        held = [open(python, 'rb'), open(plist, 'rb')]
        for f in held:
            self.addCleanup(f.close)
        inodes = [os.fstat(f.fileno()).st_ino for f in held] + [os.lstat(link).st_ino]
        # a new Python, a new version and a new environment
        with open(self.exe, 'a') as f:
            f.write('# newer\n')
        other_env = os.path.join(self.tmp, 'env2')
        os.makedirs(os.path.join(other_env, 'lib', 'python3.12'))
        self.env = other_env
        with mock.patch.object(core, 'VERSION', core.VERSION + '.1'):
            self.make()
        for path, inode in zip([python, plist, link], inodes):
            self.assertNotEqual(os.lstat(path).st_ino, inode, path)
        # the old inodes still hold the old bytes: nothing was written into them
        self.assertEqual([f.read() for f in held], [old_python, old_plist])
        with open(python, 'rb') as a, open(self.exe, 'rb') as b:
            self.assertEqual(a.read(), b.read())
        with open(plist, 'rb') as f:
            self.assertIn((core.VERSION + '.1').encode(), f.read())
        self.assertEqual(os.readlink(link), os.path.join(other_env, 'lib', 'python3.12'))
        # and no temporary file is left beside them
        for folder in ('MacOS', 'lib', ''):
            names = os.listdir(os.path.join(self.app, 'Contents', folder))
            self.assertEqual([n for n in names if n.startswith('.')], [], folder)


if __name__ == '__main__':
    unittest.main()
