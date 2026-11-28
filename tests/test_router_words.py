import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# The router's words (switchboard/router/words.py) under the same rules as the providers' words
# (tests/test_providers_words.py): nothing that says a limit is got around, no "vendor", "assistant" only about
# carrying work on, History lines filed under their own kind and never read as a switch or a sign-in, and no field
# that could carry a path, an email or a prompt's words. Never "the best model" anywhere in the router's code.
import glob
import os
import re
import string
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from switchboard import history  # noqa: E402
from switchboard.router import words  # noqa: E402
from test_providers_words import problems  # noqa: E402  (the one guard both packages are held to)

FIELDS = {'subject', 'helper', 'app', 'model', 'at', 'why', 'work', 'to', 'on', 'effort', 'passed', 'path', 'name',
          'names', 'n'}
SAMPLE = {'subject': 'A lookup helper in Claude', 'helper': 'A lookup helper', 'app': 'Claude', 'model': 'Haiku',
          'at': ' at medium', 'why': 'your route for lookups', 'work': 'A lookup', 'to': 'Codex',
          'on': ' on GPT-6 Luna', 'effort': 'medium', 'passed': 'Codex at your lines'}


def sentences():
    out = []

    def take(name, v):
        if isinstance(v, str):
            out.append((name, v))
        elif isinstance(v, dict):
            for k, x in v.items():
                take('%s[%r]' % (name, k), x)
        elif isinstance(v, (tuple, list)):
            for i, x in enumerate(v):
                take('%s[%d]' % (name, i), x)

    for name in dir(words):
        if not name.startswith('_') and name.isupper():
            take(name, getattr(words, name))
    return out


def fields(template):
    return {f for _, f, _, _ in string.Formatter().parse(template) if f}


class TheRules(unittest.TestCase):
    def test_the_guard_still_catches(self):
        for bad in ('bypass the limit', 'rotating accounts', 'pooled plans', 'never run out', 'unlimited'):
            self.assertTrue(problems(bad), bad)

    def test_every_sentence(self):
        got = sentences()
        self.assertGreater(len(got), 60)
        for name, text in got:
            with self.subTest(name=name):
                self.assertEqual(problems(text), [], text)
                self.assertNotRegex(re.sub(r'\{\w*\}', '', text), r'(?i)\bvendors?\b')
                if re.search(r'(?i)\bassistants?\b', text):
                    self.assertRegex(text, r'(?i)carr|another assistant')
                self.assertNotIn('\n', text)
                self.assertLessEqual(fields(text), FIELDS, text)

    def test_never_the_best_model_in_the_code(self):
        files = glob.glob(os.path.join(ROOT, 'switchboard', 'router', '*.py'))
        self.assertGreaterEqual(len(files), 2)
        for path in files:
            with open(path, encoding='utf-8') as f:
                text = f.read()
            with self.subTest(file=os.path.basename(path)):
                self.assertNotRegex(text, r'(?i)best model|best ai|smartest')
                self.assertEqual(problems(text), [])

    def test_each_kind_has_its_words(self):
        from switchboard.providers import kinds
        for table in (words.KIND_HELPER, words.KIND_WORK, words.KIND_PLURAL, words.ROUTE_WHY, words.PRESET_WHY):
            self.assertEqual(set(table), set(kinds.KINDS))
        from switchboard.router import resolve
        self.assertEqual(set(words.PASSED), set(resolve.PASS_CODES))
        self.assertEqual(set(words.DEFAULT_WHY), set(resolve.WHERE))


class Switches(unittest.TestCase):
    def test_the_chat_effort_switch_says_a_default_can_lower_an_effort_set_by_hand(self):
        # A default's effort is a ceiling, and the plugin cannot tell /effort from the effort Claude Code chose.
        text = words.SWITCHES['chat.effort']
        self.assertIn('default', text)
        self.assertIn('by hand', text)


class History(unittest.TestCase):
    def test_each_line_is_filed_under_its_own_kind(self):
        self.assertEqual(len(words.HISTORY), 15)       # 14, and one continue after a move
        for template, kind in words.HISTORY:
            text = template.format(**{n: SAMPLE[n] for n in fields(template)})
            with self.subTest(text=text):
                self.assertNotIn('path', fields(template))
                self.assertIn(kind, history.KINDS)
                self.assertFalse(any(rx.match(text) for rx, _, _, _ in history.RULES), text)
                rec = history.from_event(text, 'warn' if kind == 'problem' else 'info', {'h': kind, 'by': 'auto'})
                self.assertEqual((rec['kind'], rec['text']), (kind, text))
                self.assertNotIn('account', rec)


if __name__ == '__main__':
    unittest.main()
