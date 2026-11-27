import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Add account in the menu bar opens the page at its Add form (menubar.add_url): the page, in headless Chrome with a
# throwaway profile, opens the form at #accounts/add, and the address then reads #accounts, so a reload does not open
# it again. The demo's made-up accounts only.
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..'))

import test_app
from switchboard import menubar


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class TheAddForm(test_app.ChromePage):
    def test_the_menu_bars_address_opens_the_add_form(self):
        self.assertTrue(menubar.add_url().endswith('/#accounts/add'))
        got = self.run_page("""
          done({form: !!document.querySelector('form[data-form="add"][data-zone="add"]'), hash: location.hash,
                email: !!document.querySelector('[data-k="add-email"]')});""", path='#accounts/add')
        self.assertEqual(got, {'form': True, 'hash': '#accounts', 'email': True})

    def test_the_accounts_alone_do_not(self):
        got = self.run_page("""
          done({form: !!document.querySelector('form[data-form="add"]'), hash: location.hash});""", path='#accounts')
        self.assertEqual(got, {'form': False, 'hash': '#accounts'})


if __name__ == '__main__':
    unittest.main()
