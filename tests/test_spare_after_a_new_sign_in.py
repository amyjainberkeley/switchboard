import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# A removal of Claude Code's plain login file that the hold owes an answer for (a process told "no such item" less than
# HOLD_TIDY_S ago may still read the file), while the item holds a NEW sign-in of the account the settings name that no
# saved account can prove yet (Claude Code's own /login, say). The look's late answer can't make the spare from a login
# it can't prove; the keep pass at the end of the same sign-in watch look saves that sign-in. The answer stays owed
# until then, and the look makes the spare right after the keep, so the processes that were told "no such item" find
# the login in the file. Made-up order 724 of test_spare_many_cases is the order that found it (the joined line's
# stand-in Anthropic dates a second sign-in of one moment a day on, as a real later sign-in ends later). Stand-ins
# only, made-up *.example accounts.
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import test_spare_many_cases as many


class TheOrderThatFoundIt(unittest.TestCase):
    def setUp(self):
        self.t = many.ManyOrders('test_the_rules_hold_over_many_made_up_orders')
        self.t.setUp()
        self.addCleanup(self.t.doCleanups)

    def test_order_724_keeps_the_spare_for_the_processes_told_no_such_item(self):
        counts = dict.fromkeys(('spare', 'late', 'followed', 'removed_another', 'checked_readers', 'off_cases'), 0)
        self.assertEqual(self.t.run_case(724, counts), [])


if __name__ == '__main__':
    unittest.main()
