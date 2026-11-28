import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The demo's one made-up Codex. The board (the state's 'providers', providers/view.py's shape) and the rows (the
# state's 'provider_rows', the rows every face draws) are both built from it, the rows from the board as the server
# derives them, so the two cannot disagree.
#
# - By default it has room: Plus, maya@northwind.example, 5-hour 8%, weekly 21%, read 40 seconds ago, the newest
#   made-up chat "Billing export fix", and it can be continued to.
# - out=True is the story the public tests pin: ChatGPT Pro, weekly 100%, out until the day after tomorrow.
# - codex=False: Codex is on this Mac and not added; no rows.
# - FACTS['providers'] and FACTS['provider_rows'] are the tests' hooks, each replacing its own key.
# - GET /api/state in the demo holds both keys; a press on the made-up row opens nothing.
import json
import time
import unittest

from switchboard import core, demo, server
from switchboard.providers import press

KEYS = ['app', 'back_at', 'can', 'chat', 'email', 'fresh', 'in_use', 'name', 'next', 'plan', 'provider', 'read_at',
        'state', 'vendor', 'why', 'windows']


class OneMadeUpCodex(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = True
        demo.STATE.clear()
        demo.FACTS.clear()
        self.addCleanup(demo.STATE.clear)
        self.addCleanup(demo.FACTS.clear)

    def codex(self, board):
        return next(p for p in board['providers'] if p['id'] == 'codex')

    def test_the_state_holds_the_board_and_the_rows(self):
        code, body = server.SNAPSHOT.answer()
        st = json.loads(body)
        self.assertEqual(code, 200)
        self.assertEqual((st['providers']['v'], [p['id'] for p in st['providers']['providers']]),
                         (1, ['claude', 'codex']))
        (row,) = st['provider_rows']
        self.assertEqual(sorted(row), KEYS)
        self.assertEqual((row['provider'], row['vendor'], row['plan'], row['email'], row['state'], row['fresh'],
                          row['can'], row['chat']),
                         ('codex', 'OpenAI', 'Plus', 'maya@northwind.example', 'room', True, True,
                          {'name': 'Billing export fix', 'mark': press.mark(demo.DEMO_CHATS[0][0])}))

    def test_by_default_it_has_room_and_the_two_shapes_agree(self):
        now = time.time()
        b = demo.providers(now)
        codex = self.codex(b)
        (seat,) = codex['seats']
        (row,) = demo.provider_rows(now)
        self.assertEqual((codex['state'], codex['plan'], seat['plan'], codex['email'], codex['fit_why']),
                         ('room', 'ChatGPT Plus', 'plus', 'maya@northwind.example', None))
        self.assertEqual([(w['label'], w['used_pct']) for w in seat['windows']], [('5-hour', 8.0), ('weekly', 21.0)])
        self.assertEqual(row['windows'], [{k: w[k] for k in ('label', 'used_pct', 'resets_at', 'line')}
                                          for w in seat['windows']])
        self.assertEqual((row['plan'], row['email'], row['read_at'], row['state'], row['back_at']),
                         ('Plus', codex['email'], seat['read_at'], seat['state'], seat['back_at']))
        self.assertEqual(row['read_at'], now - 40)
        self.assertGreater(seat['windows'][0]['resets_at'], now)

    def test_out_is_the_story_the_public_tests_pin(self):
        now = time.time()
        b = demo.providers(now, out=True)
        codex = self.codex(b)
        (seat,) = codex['seats']
        self.assertEqual((codex['state'], codex['plan'], [w['used_pct'] for w in seat['windows'] if
                                                          w['label'] == 'weekly']), ('out', 'ChatGPT Pro', [100.0]))
        self.assertGreater(codex['back_at'], now + 86400)
        self.assertEqual((seat['state'], seat['back_at']), ('out', codex['back_at']))
        demo.FACTS['providers'] = b
        (row,) = demo.view()['provider_rows']                        # the rows follow the board the test set
        self.assertEqual((row['state'], row['back_at'], row['fresh'], row['plan'], row['next']),
                         ('out', codex['back_at'], False, 'Pro', False))

    def test_not_added_offers_it_and_has_no_row(self):
        b = demo.providers(time.time(), codex=False)
        self.assertEqual(([p['id'] for p in b['providers']], [a['id'] for a in b['add']]), (['claude'], ['codex']))
        self.assertEqual(demo.provider_rows(time.time(), codex=False), [])
        demo.FACTS['providers'] = b
        self.assertEqual(demo.view()['provider_rows'], [])

    def test_each_hook_replaces_its_own_key(self):
        row = dict(demo.provider_rows(time.time())[0], provider='tern', name='Tern')
        demo.FACTS['provider_rows'] = [row]
        got = demo.view()
        self.assertEqual([r['provider'] for r in got['provider_rows']], ['tern'])
        self.assertEqual([p['id'] for p in got['providers']['providers']], ['claude', 'codex'])

    def test_a_press_on_the_made_up_row_opens_nothing(self):
        with self.assertRaises(core.SwitchError) as e:
            demo.act('/api/continue', {'provider': 'codex', 'mark': 'a1b2c3d4'})
        self.assertEqual(str(e.exception), "The demo's OpenAI row is made up, so nothing opens.")


if __name__ == '__main__':
    unittest.main()
