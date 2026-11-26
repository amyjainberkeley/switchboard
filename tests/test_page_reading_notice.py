import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The page, in headless Chrome (a throwaway profile, no window), with auto-switch off: the accounts are still read,
# so when that reading is stuck or stopped the notes under the header say so, in words of reading, with no "Turn off"
# (turning auto-switch off is no fix); a healthy reading, or nothing kept, says nothing; a restart of the loop is
# said; with auto-switch on the notes are as they were. Settings says that off still reads. The demo's made-up state,
# with the auto view swapped by the page's own fetch.
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from test_app import CHROME, ChromePage
from test_page_robust import STALL

STUCK = 'Reading the accounts is stuck (waiting for the network). Switchboard restarts it.'
RESTARTED = 'Restarted reading the accounts at 10:42 (it was stuck waiting for the network).'
ON_STUCK = 'Automatic switching is stuck (waiting for the network). Switchboard restarts it.'

STEPS = r"""
      const s0 = await base('/api/state').then((r) => r.json());
      const off = Object.assign({}, s0.auto, {enabled: false, on: false, starting: false, problem: null, restarted: null,
                                              reading_wanted: true, reading: false, reading_problem: '%(stuck)s'});
      const line = () => !!$('[data-k="reading-line"]'), offButton = () => !!$('[data-k="auto:off"]');
      extra = {auto: off}; await look(600);
      const stuck = {notes: notes(), line: line(), offButton: offButton()};
      extra = {auto: Object.assign({}, off, {reading: true, reading_problem: null})}; await look(600);
      const healthy = {notes: notes(), line: line()};
      extra = {auto: Object.assign({}, off, {reading_wanted: false, reading_problem: null})}; await look(600);
      const nothing = {line: line()};
      extra = {auto: Object.assign({}, off, {reading: true, reading_problem: null, restarted: '%(restarted)s'})};
      await look(600);
      const restarted = notes();
      extra = {auto: Object.assign({}, off, {enabled: true, problem: '%(on_stuck)s', reading_problem: null})};
      await look(600);
      const on = {notes: notes(), line: line(), offButton: offButton()};
      extra = {};
      await settings();
      const desc = all('#sec-settings .r-desc').map((x) => x.textContent);
      done({stuck, healthy, nothing, restarted, on, desc});
""" % {'stuck': STUCK, 'restarted': RESTARTED, 'on_stuck': ON_STUCK}


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class ReadingWithAutoSwitchOff(ChromePage):
    def test_the_notes_say_when_reading_is_stuck_while_auto_switch_is_off(self):
        got = self.run_page(STALL + STEPS, budget=9000)
        self.assertIn(STUCK, got['stuck']['notes'])
        self.assertTrue(got['stuck']['line'])
        self.assertFalse(got['stuck']['offButton'])                     # off already: nothing to turn off
        self.assertNotIn('Reading the accounts', got['healthy']['notes'])
        self.assertFalse(got['healthy']['line'])
        self.assertFalse(got['nothing']['line'])                        # nothing kept: nothing to read
        self.assertIn(RESTARTED, got['restarted'])
        self.assertIn(ON_STUCK, got['on']['notes'])                     # on: as before, with its Turn off
        self.assertTrue(got['on']['offButton'])
        self.assertFalse(got['on']['line'])
        self.assertIn('Moves Claude Code to another account before it reaches a line. Off, the accounts are still read.',
                      got['desc'])


if __name__ == '__main__':
    unittest.main()
