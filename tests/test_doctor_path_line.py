"""doctor's advice when the switchboard command is not on the PATH is the installer's own line: ~/.local/bin, where
the command is linked, goes last, in the profile of the person's shell."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import os
import re
import unittest
from unittest import mock

from switchboard import __main__ as cli

HERE = os.path.dirname(os.path.abspath(__file__))


class DoctorPathLine(unittest.TestCase):
    def installer(self):
        with open(os.path.join(HERE, '..', 'install.sh'), encoding='utf-8') as f:
            text = f.read()
        lines = dict(re.findall(r'^\s+(bash|fish|\*)\) PATH_HOW="(.*)" ;;$', text, re.M))
        return {k: v.replace('\\"', '"').replace('\\$', '$') for k, v in lines.items()}

    def test_the_same_line_as_the_installer_for_each_shell(self):
        said = self.installer()
        for shell, key in (('/bin/zsh', '*'), ('/bin/bash', 'bash'), ('/opt/homebrew/bin/fish', 'fish'), ('', '*')):
            with mock.patch.dict(os.environ, {'SHELL': shell}):
                self.assertEqual(cli.path_line(), said[key], shell)
        self.assertIn('$PATH:$HOME/.local/bin', said['*'])            # the folder last

    def test_doctor_says_it(self):
        with open(cli.__file__, encoding='utf-8') as f:
            src = f.read()
        self.assertIn("'To type it, put ~/.local/bin on your PATH with this line, then open a new terminal:  %s' % path_line()", src)
        self.assertNotIn('export PATH="%s:$PATH"', src)


if __name__ == '__main__':
    unittest.main()
