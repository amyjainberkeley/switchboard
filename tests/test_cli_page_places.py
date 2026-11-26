"""The places the command line sends a person to on the page are places the page has: the Settings view's groups
Links (with Let Switchboard check links), Keys, and In Claude Code (where a pack's Repair is)."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, '..', 'switchboard', '__main__.py')
PAGE = os.path.join(HERE, '..', 'switchboard', 'web', 'index.html')


class PagePlaces(unittest.TestCase):
    def setUp(self):
        with open(MAIN, encoding='utf-8') as f:
            self.main = f.read()
        with open(PAGE, encoding='utf-8') as f:
            self.page = f.read()

    def test_no_removed_place(self):
        for gone in ('More settings', 'More, then Keys', 'Chrome setup'):
            self.assertNotIn(gone, self.main)

    def test_named_groups_exist(self):
        groups = set(re.findall(r"\{id: '[a-z]+', title: '([^']+)'", self.page))
        for g in ('Links', 'Keys', 'In Claude Code'):
            self.assertIn(g, groups)
        self.assertIn('Let Switchboard check links', self.page)
        for words in ('under Settings, Links.', '(Settings, Links)', 'On the page: Settings, Links, Let Switchboard check links',
                      '(Settings, then Keys)', '(Settings, In Claude Code, %s)'):
            self.assertIn(words, self.main)


if __name__ == '__main__':
    unittest.main()
