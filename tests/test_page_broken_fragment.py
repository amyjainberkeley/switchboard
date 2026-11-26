import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# An address whose #fragment is a broken escape (#chats/%E0) still draws the page, as if it had no fragment after the
# section. Run with:
#   SWITCHBOARD_HOME="$(mktemp -d)/data" taskpolicy -b python3 -m unittest discover -s tests -p 'test_page_broken_fragment.py'
# The page runs in headless Chrome (its own throwaway profile, no window) on the demo's made-up state.
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from test_app import CHROME, ChromePage


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class ABrokenFragment(ChromePage):
    def test_draws_the_page_on_its_section(self):
        for path in ('#chats/%E0', '#artifacts/%'):
            got = self.run_page("""
      done({drawn: !!$('main[data-drawn]'), broken: /Something went wrong/.test($('main').textContent),
            shown: all('.sec').filter((el) => !el.hidden).map((el) => el.id)});
""", path=path)
            self.assertEqual(got, {'drawn': True, 'broken': False, 'shown': ['sec-' + path[1:].split('/')[0]]}, path)


if __name__ == '__main__':
    unittest.main()
