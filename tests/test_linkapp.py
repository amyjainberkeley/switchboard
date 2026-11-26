import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The link app, Switchboard Links.app (router/): the one app switchboard/linkapp.py knows, and an older install's copy
# of it at ~/Applications/Switchboard.app.
# Every app here is a made-up bundle in a throwaway home; its program is a small shell script that notes each run.
import os
import plistlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
sys.path.insert(0, ROOT)

from switchboard import core, linkapp

LINKS_ID = 'io.github.amyjainberkeley.switchboard.links'


def read_plist(*parts):
    with open(os.path.join(ROOT, *parts), 'rb') as f:
        return plistlib.load(f)


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-linkapp-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, 'home')
        self.apps = os.path.join(self.home, 'Applications')
        os.makedirs(self.apps)
        self.ran = os.path.join(self.tmp, 'ran.txt')
        patch = mock.patch.dict(os.environ, {'HOME': self.home})
        patch.start()
        self.addCleanup(patch.stop)
        linkapp.CACHE.update(at=0, value=None)
        self.addCleanup(linkapp.CACHE.update, at=0, value=None)

    def app(self, name, info, says=''):
        """A made-up app at ~/Applications/NAME: its Info.plist, and a program that notes its run and, asked for
        --status, prints SAYS."""
        path = os.path.join(self.apps, name)
        os.makedirs(os.path.join(path, 'Contents', 'MacOS'))
        with open(os.path.join(path, 'Contents', 'Info.plist'), 'wb') as f:
            plistlib.dump(info, f)
        exe = os.path.join(path, 'Contents', 'MacOS', info['CFBundleExecutable'])
        with open(exe, 'w') as f:
            f.write('#!/bin/sh\necho "%s $*" >> "%s"\n[ "$1" != --status ] || echo "%s"\n' % (name, self.ran, says))
        os.chmod(exe, 0o755)
        return path

    def links(self, name='Switchboard Links.app', exe='SwitchboardLinks', says=''):
        return self.app(name, {'CFBundleIdentifier': LINKS_ID, 'CFBundleExecutable': exe}, says)

    def runs(self):
        try:
            with open(self.ran) as f:
                return f.read().splitlines()
        except OSError:
            return []


class Names(Sandbox):
    def test_the_link_app_is_switchboard_links(self):
        self.assertEqual(linkapp.app_path(), os.path.join(self.apps, 'Switchboard Links.app'))
        self.assertEqual(linkapp.bin_path(), os.path.join(self.apps, 'Switchboard Links.app', 'Contents', 'MacOS', 'SwitchboardLinks'))
        self.assertEqual(linkapp.older_path(), os.path.join(self.apps, 'Switchboard.app'))
        self.assertEqual(linkapp.status(fresh=True), {'installed': False, 'on': False, 'browser': None})

    def test_the_source_builds_the_link_app_as_switchboard_links(self):
        links = read_plist('router', 'Info.plist')
        self.assertEqual((links['CFBundleName'], links['CFBundleDisplayName'], links['CFBundleExecutable']),
                         ('Switchboard Links', 'Switchboard Links', 'SwitchboardLinks'))
        self.assertEqual(links['CFBundleIdentifier'], LINKS_ID)      # never changes: the default browser is kept by it
        self.assertEqual(links['CFBundleShortVersionString'], core.VERSION)
        self.assertTrue(os.path.exists(os.path.join(ROOT, 'router', 'SwitchboardLinks.swift')))
        self.assertTrue(os.path.exists(os.path.join(ROOT, 'router', links['CFBundleIconFile'] + '.icns')))
        with open(os.path.join(ROOT, 'router', 'build.sh')) as f:
            build = f.read()
        self.assertIn('APP="$DEST/Switchboard Links.app"', build)
        # built aside (a failed build leaves nothing), then moved into its place
        self.assertIn('NEW="$WORK/Switchboard Links.app"', build)
        self.assertIn('"$NEW/Contents/MacOS/SwitchboardLinks" SwitchboardLinks.swift', build)
        self.assertIn('mv "$NEW" "$APP"', build)


class Status(Sandbox):
    def test_on_when_the_default_browser_is_the_link_app(self):
        path = os.path.join(self.apps, 'Switchboard Links.app')
        self.links(says=path)
        self.assertEqual(linkapp.status(fresh=True), {'installed': True, 'on': True, 'browser': 'Switchboard Links'})
        self.assertEqual(self.runs(), ['Switchboard Links.app --status'])

    def test_off_when_another_browser_is(self):
        other = self.app('Other Browser.app', {'CFBundleIdentifier': 'com.example.browser', 'CFBundleExecutable': 'Other'})
        self.links(says=other)
        self.assertEqual(linkapp.status(fresh=True), {'installed': True, 'on': False, 'browser': 'Other Browser'})

    def test_on_when_the_default_is_a_link_app_at_another_path(self):
        old = self.links(name='Old Copy.app', exe='Switchboard')
        self.links(says=old)
        self.assertTrue(linkapp.status(fresh=True)['on'])

    def test_an_older_installs_link_app_counts_until_it_is_moved(self):
        old = self.links(name='Switchboard.app', exe='Switchboard', says=os.path.join(self.apps, 'Switchboard.app'))
        self.assertEqual(linkapp.app_path(), old)
        self.assertEqual(linkapp.bin_path(), os.path.join(old, 'Contents', 'MacOS', 'Switchboard'))
        self.assertEqual(linkapp.status(fresh=True), {'installed': True, 'on': True, 'browser': 'Switchboard'})
        new = self.links(says=os.path.join(self.apps, 'Switchboard Links.app'))
        self.assertEqual(linkapp.app_path(), new)
        self.assertEqual(linkapp.status(fresh=True), {'installed': True, 'on': True, 'browser': 'Switchboard Links'})

    def test_another_app_at_the_older_path_is_never_run_as_the_link_app(self):
        # an early 0.2.0 build put a menu bar app here; to this file it is just another app
        self.app('Switchboard.app', {'CFBundleIdentifier': 'com.example.menu', 'CFBundleExecutable': 'Switchboard',
                                     'SwitchboardRole': 'menu-bar'})
        self.assertEqual(linkapp.kind(linkapp.older_path()), 'other')
        self.assertEqual(linkapp.app_path(), os.path.join(self.apps, 'Switchboard Links.app'))
        self.assertEqual(linkapp.status(fresh=True), {'installed': False, 'on': False, 'browser': None})
        with self.assertRaises(core.SwitchError) as cm:
            linkapp.turn(True)
        self.assertEqual(str(cm.exception),
                         'Needs the link app: switchboard update --link-app (it needs Apple’s command line tools)')  # as the page says
        self.assertEqual(self.runs(), [])

    def test_another_app_with_the_new_name_is_not_run(self):
        self.app('Switchboard Links.app', {'CFBundleIdentifier': 'com.example.other', 'CFBundleExecutable': 'SwitchboardLinks'})
        self.assertEqual(linkapp.kind(linkapp.app_path()), 'other')
        self.assertFalse(linkapp.status(fresh=True)['installed'])
        with self.assertRaises(core.SwitchError):
            linkapp.turn(False)
        self.assertEqual(self.runs(), [])

    def test_kind_reads_what_is_there(self):
        self.assertIsNone(linkapp.kind(os.path.join(self.apps, 'Nothing.app')))
        broken = os.path.join(self.apps, 'Broken.app')
        os.makedirs(os.path.join(broken, 'Contents'))
        with open(os.path.join(broken, 'Contents', 'Info.plist'), 'w') as f:
            f.write('not a plist')
        self.assertEqual(linkapp.kind(broken), 'other')
        self.assertEqual(linkapp.kind(self.links()), 'links')


if __name__ == '__main__':
    unittest.main()
