import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# install.sh puts a new link app in place in two ways: from a release's prebuilt copy, or built on this Mac. An older
# install's link app (the bundle identifier Switchboard had before) in that place that is the default browser, or
# whose seat can't be told, is never taken away: before a new one goes in, from the release or built here, it moves
# aside to "Switchboard Links (old).app" and keeps the seat; when the new one could not be put in place it goes back;
# when it can't be moved aside, no new one goes in this time and one line says so.
#
# install.sh's own link app block, run by /bin/sh with the stand-ins of tests/test_link_app_release.py (codesign,
# swiftc, xcode-select, sw_vers) in a throwaway home. Nothing of the Mac's is run or changed.
import os
import re
import shutil
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

import test_link_app_release as tl  # noqa: E402

OLD_ID = next(line for line in tl.install_sh().splitlines()
              if line.startswith('OLD_LABEL=')).split('=', 1)[1].strip('"') + '.links'
STILL = ('Your default browser is still the older Switchboard Links, and it works as before. To finish the move, make '
         'the new one the default browser on the page (macOS asks you).\n')
TAKEN = 'Installed the link app from the release: '      # the rest of that line is the release test's to pin
# codesign as the release test's stand-in, but the copy build.sh --from makes (in its switchboard-links.* folder)
# does not verify when $FAKE/copy-fails is there: the release's app passes every check and then can't be put in place.
CODESIGN = tl.CODESIGN.replace('echo "codesign $*" >> "$FAKE/calls"\n',
                               'echo "codesign $*" >> "$FAKE/calls"\n'
                               'case "$*" in *--verify*switchboard-links.*) [ ! -f "$FAKE/copy-fails" ] || exit 1 ;; '
                               'esac\n', 1)


class AnOlderLinkApp(unittest.TestCase):
    setUp = tl.InstallerTakesThePrebuiltApp.setUp
    make_prebuilt = tl.InstallerTakesThePrebuiltApp.make_prebuilt
    install = tl.InstallerTakesThePrebuiltApp.install
    program = tl.InstallerTakesThePrebuiltApp.program
    compiled = tl.InstallerTakesThePrebuiltApp.compiled

    def older_app(self):
        """An older install's link app in the new one's place, whose answer to --status names no app: its seat can't
        be told, so it counts as the default browser."""
        tl.write(os.path.join(self.app, 'Contents', 'Info.plist'), tl.PLIST.replace('com.example.links-test', OLD_ID))
        tl.write(os.path.join(self.app, 'Contents', 'MacOS', 'SwitchboardLinks'), '#!/bin/sh\n# the older one\n', 0o755)

    def aside(self):
        return os.path.join(os.path.dirname(self.app), 'Switchboard Links (old).app')

    def no_compiler(self):
        tl.write(os.path.join(self.bin, 'xcode-select'), '#!/bin/sh\nexit 2\n', 0o755)

    def older_program_at(self, app):
        with open(os.path.join(app, 'Contents', 'MacOS', 'SwitchboardLinks')) as f:
            return f.read()

    def test_the_releases_copy_moves_it_aside_and_it_keeps_the_seat(self):
        self.older_app()
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        taken, still = r.stdout.split('\n', 1)
        self.assertTrue(taken.startswith(TAKEN), r.stdout)
        self.assertEqual(still, STILL + 'the block ended\n')
        self.assertEqual(self.program(), '#!/bin/sh\n# the release\'s copy\n')
        self.assertEqual(self.older_program_at(self.aside()), '#!/bin/sh\n# the older one\n')
        self.assertEqual(self.compiled(), [])

    def test_a_copy_that_can_not_be_put_in_place_puts_it_back(self):
        self.older_app()
        self.no_compiler()
        tl.write(os.path.join(self.bin, 'codesign'), CODESIGN, 0o755)
        tl.write(os.path.join(self.fake, 'copy-fails'), '')
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines()[0], "Not using the release's link app: it could not be put in "
                                                   '~/Applications (details in ~/.switchboard/build.log).')
        self.assertNotIn(STILL.strip(), r.stdout)
        self.assertEqual(self.older_program_at(self.app), '#!/bin/sh\n# the older one\n')     # back, in its place
        self.assertFalse(os.path.exists(self.aside()))

    def test_a_build_here_moves_it_aside_too(self):
        self.older_app()
        shutil.rmtree(self.prebuilt)
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r'^Building the link app… \d+ s\n%sthe block ended\n$' % re.escape(STILL))
        self.assertEqual(self.program(), '#!/bin/sh\n# compiled on this Mac\n')
        self.assertEqual(self.older_program_at(self.aside()), '#!/bin/sh\n# the older one\n')

    def test_one_that_can_not_be_moved_aside_stays_and_nothing_new_goes_in(self):
        self.older_app()
        os.makedirs(self.aside())                   # something else already has the name it would move to
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout, 'Left the older Switchboard Links as it is: it is your default browser, and it '
                                   'could not be moved aside for the new one.\nthe block ended\n')
        self.assertEqual(self.older_program_at(self.app), '#!/bin/sh\n# the older one\n')
        self.assertEqual(self.compiled(), [])


if __name__ == '__main__':
    unittest.main()
