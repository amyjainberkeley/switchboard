import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# A reading taken from another Mac (`switchboard readings take`) is shown, never decided on: the bare `switchboard
# switch` and the page's Next mark choose as if the account had no reading, as automatic switching does (it reads
# this Mac's own numbers only). Made-up accounts only; the engine is a stand-in (tests/fakes_accounts_api.py).
import dataclasses
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import __main__ as cli
from switchboard import server
from test_term import Sandbox, hold, iso_in

A, B, C = 'ana@north.example', 'ana@fern.example', 'ana@harbor.example'


class Taken(Sandbox):
    def setUp(self):
        super().setUp()
        self.ids = {A: hold(self.fake, A, active=True, five=(20, iso_in(3600)), week=(30, iso_in(86400))),
                    B: hold(self.fake, B, five=(100, iso_in(3600)), week=(30, iso_in(86400))),
                    C: hold(self.fake, C, five=(10, iso_in(3600)), week=(10, iso_in(86400)))}

    def taken(self, who):
        aid = self.ids[who]
        self.fake.read[aid] = dataclasses.replace(self.fake.read[aid], elsewhere=True)

    def test_bare_switch_passes_over_an_account_by_this_macs_own_reading(self):
        self.assertEqual(self.run_cli('switch')[0], 0)
        self.assertEqual(self.fake.live().account_id, self.ids[C])

    def test_bare_switch_does_not_pass_over_an_account_by_a_taken_reading(self):
        self.taken(B)
        self.assertEqual(self.run_cli('switch')[0], 0)
        self.assertEqual(self.fake.live().account_id, self.ids[B])      # the next one, as with no reading

    def test_the_plan_never_trusts_a_taken_reading(self):
        self.taken(B)
        view = cli.local_view()                                          # the page's state, its accounts as rows
        rows = [a for a in view['accounts'] if not a.get('unmanaged')]
        st = {'order': [a['email'] for a in rows], 'active': view.get('active'),
              'accounts': dict((a['email'], dict(a, connected=True)) for a in rows)}
        got = dict((p['name'], p['trusted']) for p in server.plan_accounts(st, now=time.time(),
                                                                           lines=server.plan_settings({})))
        self.assertEqual((got[A], got[B], got[C]), (True, False, True))


if __name__ == '__main__':
    unittest.main()
