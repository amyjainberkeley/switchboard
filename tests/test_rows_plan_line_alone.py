import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The left side of an account's row on the page is its email and the plan under it, and nothing else: the plan line
# has no chat count ("Team", never "Team · 2 chats"), nor on hover. The Chats section keeps its own numbers.
#
# Read from the page's file: no browser here (tests/test_page_chats.py draws it in Chrome).
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))


def page():
    with open(os.path.join(HERE, '..', 'switchboard', 'web', 'index.html'), encoding='utf-8') as f:
        return f.read()


def function(src, name):
    """The source of one top-level function of the page's script, up to the next one."""
    m = re.search(r'\nfunction %s\(.*?(?=\n(?:function |const |// ))' % name, src, re.S)
    return m.group(0) if m else ''


class ThePlanLine(unittest.TestCase):
    def test_an_accounts_plan_line_is_the_plan_alone(self):
        row = function(page(), 'accRow')
        self.assertTrue(row)
        self.assertIn("'<span class=\"sub\"' + tipAttr(whole, a.note) + '>' + esc(plan) + '</span>'", row)
        self.assertNotIn('chat', row)                       # no count, no link to Chats, nothing on hover

    def test_the_chats_section_keeps_its_numbers(self):
        src = page()
        self.assertNotIn('chatsHome', src)                  # the count under each email is gone, with its helper
        self.assertIn("{id: 'chats', label: 'Chats', chord: 'G C', icon: I.chat, count: () => (S ? chatsCount() : '')}", src)


if __name__ == '__main__':
    unittest.main()
