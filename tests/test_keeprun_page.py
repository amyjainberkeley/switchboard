import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The Keep running section on the page, in headless Chrome on the demo's made-up answer (keeprun.sample): it sits under
# the accounts and above the week, says the week in one sentence, shows at most five steps until "Show all", ends every
# cause row in "Handled", its step or nothing, says the log lines after a switch apart from the rows, and its
# "Turn on" press asks the server to turn Keep running on. With no model ticked, and switchboard.room in the build,
# the model picker says "None ticked: the models your chats use".
# Run with (it starts headless Chrome: run it under the Chrome lock):
#   SWITCHBOARD_HOME="$(mktemp -d)/data" taskpolicy -b python3 -m unittest discover -s tests -p 'test_keeprun_page.py'
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)

from unittest import mock  # noqa: E402

from switchboard import keeprun, server  # noqa: E402
from test_app import CHROME, ChromePage  # noqa: E402

# A row's status: "Handled" (measured), a step's words, or nothing; never "Not handled" or "Partly handled"
STEP_WORDS = ('Handled', 'Turn on Keep running', 'Open the chats that were cut off', 'Resume the runs',
              'Plug in before you close the lid', 'Turn on automatic switching')


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class TheSection(ChromePage):
    def setUp(self):
        super().setUp()
        on = mock.patch.object(keeprun, 'running_status', lambda: (True, {'on': False}))   # as with the plugin's lane
        on.start()
        self.addCleanup(on.stop)

    def test_the_section_draws_its_parts_in_order(self):
        got = self.run_page(r"""
      for (let i = 0; i < 200 && !$('#keep .kp-steps'); i++) await wait(50);
      const order = all('#sec-accounts > *, #sec-accounts > #acc-board').map((e) => e.id).filter(Boolean);
      const steps = all('#keep .kp-steps > li').length;
      const rows = all('#keep .kp-rows > li')
        .map((li) => [li.dataset.cause || li.dataset.run || '', (li.querySelector('.st') || {}).textContent || '']);
      const head = ($('#h-keep') || {}).textContent;
      const sum = ($('#keep .kp-sum') || {}).textContent;
      const logs = all('#keep [data-k="switch-log"]').map((p) => p.textContent);
      const turn = $('#keep [data-step="on"] .go');
      const copy = $('#keep [data-step="terminal"] .tok');
      turn.click();
      await wait(300);
      $('#keep [data-do="keepAll"]').click();
      await wait(100);
      const stepsAll = all('#keep .kp-steps > li').length;
      done({order, steps, stepsAll, rows, head, sum, logs, copy: copy ? copy.dataset.copy : null, sent});
""")
        self.assertEqual([x for x in got['order'] if x in ('acc-board', 'keep', 'week')], ['acc-board', 'keep', 'week'])
        self.assertEqual(got['head'], 'Keep runningOff')
        self.assertTrue(got['sum'].startswith('Last 7 days: 31 early stops in chats'), got['sum'])
        self.assertEqual(got['steps'], 5)                        # at most five until "Show all"
        self.assertEqual(got['stepsAll'], 9)
        self.assertIn(['/api/running', {'on': True}], got['sent'])
        self.assertIsNone(got['copy'])                           # the sixth step and on wait behind "Show all"
        for key, st in got['rows']:
            self.assertTrue(st == '' or st in STEP_WORDS or 'died' in st, (key, st))
        self.assertNotIn('rechecked', [k for k, st in got['rows']])                  # log lines are not a row
        self.assertTrue(got['logs'] and got['logs'][0].startswith('VS Code logged 27 “did not finish starting” lines '
                                                                  'within 3 minutes after the switch to '), got['logs'])

    def test_no_copy_line_and_the_picker_words(self):
        follows = mock.patch.object(server, 'keep_follows', lambda: True)     # as when switchboard.room is there
        follows.start()
        self.addCleanup(follows.stop)
        got = self.run_page(r"""
      for (let i = 0; i < 200 && !$('#keep .kp-steps'); i++) await wait(50);
      $('#keep [data-do="keepAll"]').click();
      await wait(100);
      const copy = $('#keep [data-step="terminal"] .tok');
      location.hash = '#settings';
      for (let i = 0; i < 100 && !$('input[data-change="models"]'); i++) await wait(50);
      const before = $('[data-k="model-none"]');
      all('input[data-change="models"]').filter((x) => x.checked).forEach((x) => x.click());   // untick every model
      for (let i = 0; i < 200 && !$('[data-k="model-none"]'); i++) await wait(50);
      const none = $('[data-k="model-none"]');
      done({copy: copy ? copy.dataset.copy : null, before: before ? before.textContent : null,
            none: none ? none.textContent : null, sent});
""")
        self.assertIsNone(got['copy'])                           # the terminal step has no line to copy now
        self.assertIsNone(got['before'])                         # the demo counts Fable: a tick, no words
        self.assertIn(['/api/auto', {'model': []}], got['sent'])
        self.assertEqual(got['none'], 'None ticked: the models your chats use')


if __name__ == '__main__':
    unittest.main()
