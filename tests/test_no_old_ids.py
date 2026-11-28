import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The ids are the public ones everywhere: io.github.amyjainberkeley.switchboard for the page's job, with .watch, .menubar
# and .links. The ids an older install used (before Nov 26 2026) may be named in exactly these places, each listed
# below by its path: engine.OUR_LABELS (so an older install's leftover job is never taken for another program that
# switches Claude Code's login), the installer's and the uninstaller's one-time sweep of an older install, and the
# check that runs those two (tests/install-check.sh, cases 30 and 31). Anywhere else the old id is a fault: a job or an
# app looked for under a name that nothing installs any more (the sign-in watch would never start the page again, and
# the page never the watch). Reads files only: nothing runs, nothing is written.
import ast
import os
import unittest

ROOT = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
OLD = 'io.github.' + 'claude-switchboard'       # put together, so this file never names it whole
NEW = 'io.github.amyjainberkeley.switchboard'
ALLOWED = {
    'switchboard/engine.py': 'engine.OUR_LABELS, and nothing else in it',
    'install.sh': "the installer's one-time sweep: its OLD_LABEL line and comments",
    'uninstall.sh': "the uninstaller's one-time sweep: its OLD_LABEL line and comments",
    'tests/install-check.sh': 'cases 30 and 31, which run the installer and the uninstaller over an older install',
}
SKIP = ('.git', '__pycache__', 'node_modules')


def naming_it():
    """{path relative to the tree, with / : [the numbers of the lines that name the old id]} over every file."""
    found = {}
    for top, dirs, names in os.walk(ROOT):
        dirs[:] = sorted(d for d in dirs if d not in SKIP)
        for name in sorted(names):
            path = os.path.join(top, name)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            with open(path, 'rb') as f:
                data = f.read()
            if OLD.encode() not in data:
                continue
            lines = data.decode('utf-8', 'replace').splitlines()
            found[os.path.relpath(path, ROOT).replace(os.sep, '/')] = [n for n, l in enumerate(lines, 1) if OLD in l]
    return found


def read(name):
    with open(os.path.join(ROOT, name), encoding='utf-8') as f:
        return f.read()


class TheOldIds(unittest.TestCase):
    def test_they_are_named_only_in_the_places_listed(self):
        found = naming_it()
        self.assertEqual(sorted(set(found) - set(ALLOWED)), [], dict((k, v) for k, v in found.items()
                                                                     if k not in ALLOWED))

    def test_in_the_engine_only_in_our_labels_after_the_new_ones(self):
        text = read('switchboard/engine.py')
        tree = ast.parse(text)
        spans = [(node.lineno, node.end_lineno) for node in tree.body if isinstance(node, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == 'OUR_LABELS' for t in node.targets)]
        self.assertEqual(len(spans), 1, 'engine.OUR_LABELS is assigned once, at the top level')
        first, last = spans[0]
        for n, line in enumerate(text.splitlines(), 1):
            if OLD in line:
                with self.subTest(line=n):
                    self.assertTrue(first <= n <= last, line)
        labels = ast.literal_eval(ast.get_source_segment(text, next(
            node.value for node in tree.body if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == 'OUR_LABELS' for t in node.targets))))
        self.assertTrue(labels and labels[0].startswith(NEW), labels)          # this build's ids first
        old = [i for i, x in enumerate(labels) if x.startswith(OLD)]
        new = [i for i, x in enumerate(labels) if x.startswith(NEW)]
        self.assertTrue(not old or min(old) > max(new), labels)

    def test_in_the_scripts_only_the_sweep_names_it(self):
        for name in ('install.sh', 'uninstall.sh'):
            with self.subTest(script=name):
                lines = read(name).splitlines()
                named = [l for l in lines if OLD in l]
                code = [l for l in named if not l.lstrip().startswith('#')]
                self.assertEqual(code, ['OLD_LABEL="%s"' % OLD])     # every other line of the sweep uses $OLD_LABEL
                self.assertIn('LABEL="%s"' % NEW, lines)              # and the jobs this copy adds have the new id

    def test_this_check_names_it_nowhere_else_either(self):
        with open(os.path.abspath(__file__), encoding='utf-8') as f:
            self.assertNotIn(OLD, f.read())


if __name__ == '__main__':
    unittest.main()
