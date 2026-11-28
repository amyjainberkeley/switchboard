import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# One name for Claude Code's plain login file (~/.claude/.credentials.json) in every string the put-back makes that a
# person can read: History lines, the reasons and lines of its answers, log lines. Comments and docstrings may say
# either.
import ast
import os
import unittest

from switchboard.accounts import api, putback

SOURCE = os.path.join(os.path.dirname(putback.__file__), 'putback.py')


def strings_a_person_can_read(path):
    """Every string constant in the module that is not a docstring."""
    with open(path) as f:
        tree = ast.parse(f.read())
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body and \
                isinstance(node.body[0], ast.Expr) and isinstance(getattr(node.body[0], 'value', None), ast.Constant):
            docs.add(id(node.body[0].value))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            out.append((node.lineno, node.value))
    return out


class OneNameForThePlainLoginFile(unittest.TestCase):
    def test_no_string_a_person_can_read_says_plaintext(self):
        said = [(n, s) for n, s in strings_a_person_can_read(SOURCE) if 'plaintext' in s.lower()]
        self.assertEqual(said, [])

    def test_the_lines_say_plain_login_file(self):
        v = putback.Verdict('leave', 'uuid-b', 'x', key='file_owner')
        self.assertIn('its plain login file', putback._left_line(v, 'ana@home.example'))
        v = putback.Verdict('leave', 'uuid-b', 'x', key='file_limit')
        self.assertIn('its plain login file', putback._left_line(v, 'ana@home.example'))
        v = putback.Verdict('leave', reason='x', key='file')
        self.assertIn('its plain login file', putback._left_line(v, 'ana@home.example'))
        seen = putback.Seen(live=api.PRESENT, login='oauth', from_file=True, full=False, named='uuid-b', saved='ok')
        self.assertIn('plain login file', putback.decide(seen, None, [], 1000.0).reason)


if __name__ == '__main__':
    unittest.main()
