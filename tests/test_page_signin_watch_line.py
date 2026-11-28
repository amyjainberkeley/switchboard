import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The page, in headless Chrome (a throwaway profile, no window): the sign-in watch shows nothing at all while all is
# well, and one plain line under the header, where a stuck or stopped loop is told, once the server says the watch has
# been down for two minutes (its state's watch.line). No control, no setting. The demo's made-up state, with the watch
# block swapped by the page's own fetch.
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import server  # noqa: E402
from test_app import CHROME, ChromePage  # noqa: E402
from test_page_robust import STALL  # noqa: E402

STEPS = r"""
      const line = () => $('[data-k="watch-line"]');
      const well = {there: true, running: true, beat_age_s: 4, mode: 'events', last_action: null, last_action_at: null,
                    problem: null, line: null, page_looking: false, page_looked_at: null};
      extra = {watch: well}; await look(600);
      const fine = {line: !!line(), notes: notes()};
      extra = {watch: Object.assign({}, well, {running: false, beat_age_s: 200})}; await look(600);
      const early = {line: !!line()};
      extra = {watch: Object.assign({}, well, {running: false, beat_age_s: 300, line: '%(down)s'})}; await look(600);
      const down = {line: !!line(), text: line() ? line().textContent : '',
                    buttons: line() ? line().querySelectorAll('button').length : -1};
      extra = {watch: Object.assign({}, well, {running: false, line: '%(gave_up)s', problem: '%(gave_up)s'})};
      await look(600);
      const gaveUp = {text: line() ? line().textContent : ''};
      extra = {watch: {there: false, running: false, line: null}}; await look(600);
      const none = {line: !!line()};
      extra = {}; await look(600);
      const missing = {line: !!line()};
      done({fine, early, down, gaveUp, none, missing});
""" % {'down': server.WATCH_DOWN.replace("'", "\\'"), 'gave_up': server.WATCH_GAVE_UP.replace("'", "\\'")}


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class TheWatchLine(ChromePage):
    def test_nothing_while_well_and_one_plain_line_when_the_server_says_it_is_down(self):
        got = self.run_page(STALL + STEPS, budget=9000)
        self.assertFalse(got['fine']['line'])
        self.assertNotIn('sign-in watch', got['fine']['notes'])
        self.assertFalse(got['early']['line'])            # down, but the server has no line yet: nothing
        self.assertTrue(got['down']['line'])
        self.assertIn(server.WATCH_DOWN, got['down']['text'])
        self.assertEqual(got['down']['buttons'], 0)        # no new control
        self.assertIn(server.WATCH_GAVE_UP, got['gaveUp']['text'])
        self.assertFalse(got['none']['line'])
        self.assertFalse(got['missing']['line'])           # a state without the block (an older page server)


if __name__ == '__main__':
    unittest.main()
