import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# `switchboard update` on an install made by Homebrew ends with the version it had and the one it has now, and says in
# one sentence when a newer version is out that Homebrew does not have yet: one look, after the upgrade, at the core.py
# beside the address the one-line installer in Homebrew's copy is fetched from (the place an install from the web
# updates from). It says nothing more when Homebrew has the newest, when that look fails, or when the upgrade failed.
# Homebrew is a stand-in script on PATH and the network a stand-in; nothing is downloaded.
import os
import unittest
from unittest import mock

from switchboard import newer
from switchboard.accounts import net
import test_update_versions as versions           # by the module: its own tests are not run again from here
from test_update_check import Web
from test_update_versions import installs, write_exe

INSTALLER = ('#!/bin/sh\n'
             'REPO="${SWITCHBOARD_REPO:-maya-example/switchboard}"\n'
             'INSTALL_URL="https://raw.githubusercontent.com/$REPO/main/install.sh"\n')
CORE_PY = 'https://raw.githubusercontent.com/maya-example/switchboard/main/switchboard/core.py'


class HomebrewBehind(versions.Case):
    def brew(self, version, build):
        """Homebrew, standing in: its upgrade puts that version and build where the real installer does."""
        bin_dir = os.path.join(self.tmp, 'bin')
        write_exe(os.path.join(bin_dir, 'brew'), '#!/bin/sh\n' + installs(version, build))
        p = mock.patch.dict(os.environ, {'PATH': bin_dir + os.pathsep + '/usr/bin:/bin'})
        p.start()
        self.addCleanup(p.stop)
        self.source('homebrew:' + self.clone)

    def setUp(self):
        super().setUp()
        write_exe(os.path.join(self.clone, 'install.sh'), INSTALLER)       # Homebrew's copy of the repository
        self.web = Web((200, {}, b"VERSION = '0.3.1'\n"))
        p = mock.patch.object(newer, 'HTTP', self.web)
        p.start()
        self.addCleanup(p.stop)

    def test_already_the_newest_homebrew_has_and_a_newer_one_is_out(self):
        self.brew('0.2.0', 'aaaaaaa')
        code, out = self.run_cli('update')
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines(), ['Updating with Homebrew: brew upgrade switchboard',
                                            'Switchboard 0.2.0 (aaaaaaa) is the newest Homebrew has. Homebrew does not '
                                            'have Switchboard 0.3.1 yet; switchboard update takes it once it does.'])
        self.assertEqual([(c['method'], c['url']) for c in self.web.calls], [('GET', CORE_PY)])

    def test_updated_and_a_still_newer_one_is_out(self):
        self.brew('0.3.0', 'eeeeeee')
        self.assertEqual(self.run_cli('update')[1].splitlines()[-1],
                         'Updated Switchboard from 0.2.0 (aaaaaaa) to 0.3.0 (eeeeeee). Homebrew does not have '
                         'Switchboard 0.3.1 yet; switchboard update takes it once it does.')

    def test_nothing_more_when_homebrew_has_the_newest_or_the_look_fails(self):
        for answer in ((200, {}, b"VERSION = '0.2.0'\n"), (200, {}, b"VERSION = '0.1.9'\n"), (404, {}, b''),
                       (200, {}, b'no version here'), net.NetError('dns', False, 'no such host')):
            with self.subTest(answer=answer):
                self.web.answer = answer
                self.brew('0.2.0', 'aaaaaaa')
                self.assertEqual(self.run_cli('update')[1].splitlines()[-1],
                                 'Switchboard 0.2.0 (aaaaaaa) was already the newest.')

    def test_no_look_when_the_upgrade_failed_or_the_copy_names_no_repository(self):
        bin_dir = os.path.join(self.tmp, 'bin')
        self.brew('0.2.0', 'aaaaaaa')
        write_exe(os.path.join(bin_dir, 'brew'), '#!/bin/sh\nexit 1\n')
        self.assertEqual(self.run_cli('update')[1].splitlines()[-1], 'Switchboard is still 0.2.0 (aaaaaaa).')
        # the placeholder in two parts, so the publish step's scan does not take it for one left in by mistake
        write_exe(os.path.join(self.clone, 'install.sh'), INSTALLER.replace('maya-example/', 'OWN' 'ER/'))
        self.brew('0.2.0', 'aaaaaaa')
        self.assertEqual(self.run_cli('update')[1].splitlines()[-1],
                         'Switchboard 0.2.0 (aaaaaaa) was already the newest.')
        self.assertEqual(self.web.calls, [])


if __name__ == '__main__':
    unittest.main()
