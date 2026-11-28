import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# The Claude Code plugin (switchboard/mods/switchboard-router) applies the same rule as the router's Python code, and
# both are held to one file of cases, tests/router_fixtures.json. The plugin keeps a copy of it beside its rule, and a
# module made from that copy, since a plugin's tests can import only code files. This file holds them together:
# - the copy is the router's file byte for byte (a missing file on either side is a difference);
# - the module is the copy, after its two comment lines and "export default ".
import os
import shutil
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
CORE = os.path.join(ROOT, 'tests', 'router_fixtures.json')
HOOKS = os.path.join(ROOT, 'switchboard', 'mods', 'switchboard-router', 'hooks')
COPY = os.path.join(HOOKS, 'fixtures.json')
MODULE = os.path.join(HOOKS, 'fixtures-data.ts')


def read(path):
    with open(path, 'rb') as f:
        return f.read()


def difference(core, copy):
    """None when the copy holds the core file's bytes; else where they first differ, or which file is missing."""
    for path in (core, copy):
        if not os.path.isfile(path):
            return '%s is missing' % path
    a, b = read(core), read(copy)
    if a == b:
        return None
    at = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    return '%s and %s differ from byte %d (%d and %d bytes)' % (core, copy, at, len(a), len(b))


def module_difference(module, copy):
    """None when the module is two comment lines, then "export default " and the copy's bytes; else what is wrong."""
    lines = read(module).split(b'\n', 2)
    if len(lines) < 3 or not (lines[0].startswith(b'//') and lines[1].startswith(b'//')):
        return '%s does not start with its two comment lines' % module
    if lines[2] != b'export default ' + read(copy):
        return '%s is not "export default " and %s: remake it as its second line says' % (module, copy)
    return None


class TheCopyOfTheCases(unittest.TestCase):
    def test_the_plugin_copy_is_the_router_file_byte_for_byte(self):
        self.assertIsNone(difference(CORE, COPY))

    def test_the_plugin_module_is_its_copy(self):
        self.assertIsNone(module_difference(MODULE, COPY))


class TheCheckItself(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def write(self, name, data):
        path = os.path.join(self.tmp, name)
        with open(path, 'wb') as f:
            f.write(data)
        return path

    def test_a_missing_router_file_is_a_difference(self):
        # The router's file is in the tree: one that is gone must fail the check, never pass it.
        got = difference(os.path.join(self.tmp, 'none.json'), self.write('copy.json', b'[]\n'))
        self.assertIsNotNone(got)
        self.assertIn('none.json', got)

    def test_the_same_bytes_are_no_difference(self):
        self.assertIsNone(difference(self.write('core.json', b'[1]\n'), self.write('copy.json', b'[1]\n')))

    def test_one_byte_apart_is_a_difference(self):
        got = difference(self.write('core.json', b'[1]\n'), self.write('copy.json', b'[1] \n'))
        self.assertIn('differ from byte 3', got)

    def test_the_same_cases_spelled_another_way_are_a_difference(self):
        self.assertIsNotNone(difference(self.write('core.json', b'[1]\n'), self.write('copy.json', b'[ 1 ]\n')))

    def test_a_module_that_lags_its_copy_is_a_difference(self):
        copy = self.write('copy.json', b'[2]\n')
        module = self.write('m.ts', b'// one\n// two\nexport default [1]\n')
        self.assertIsNotNone(module_difference(module, copy))
        module = self.write('m.ts', b'// one\n// two\nexport default [2]\n')
        self.assertIsNone(module_difference(module, copy))


if __name__ == '__main__':
    unittest.main()
