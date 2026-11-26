import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The page's one quiet line when a newer version is out: under the version, in Settings, and nothing when the state
# carries none. Headless Chrome on the demo's made-up accounts, with the state's `newer` laid over the server's answer.
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)

from switchboard import core
from test_app import CHROME, ChromePage
from test_page_layout import STATE_EXTRA

LINE = 'Switchboard 9.0.0 is out (this is %s). Install it with: switchboard update' % core.VERSION


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class TheNewerLine(ChromePage):
    def test_one_quiet_line_under_the_version_and_none_without_it(self):
        got = self.run_page(STATE_EXTRA + """
      location.hash = '#settings'; await wait(700);
      const out = {before: [$('#about').textContent, $('#newer').textContent]};
      extra = {newer: %s};
      await look(900);
      const n = $('#newer'), a = $('#about');
      out.after = [a.textContent, n.textContent, n.previousElementSibling === a, getComputedStyle(n).fontSize,
                   n.querySelectorAll('*').length, document.querySelectorAll('.newer').length];
      done(out);
""" % ('"' + LINE + '"'))
        self.assertEqual(got['before'][1], '')
        self.assertTrue(re.match(r'^Switchboard %s · demo · localhost:\d+$' % re.escape(core.VERSION), got['before'][0]))
        self.assertEqual(got['after'][0], got['before'][0])                 # the version line itself is as it was
        self.assertEqual(got['after'][1:], [LINE, True, '12px', 0, 1])       # one line of plain text, right under it


if __name__ == '__main__':
    unittest.main()
