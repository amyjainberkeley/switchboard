import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# A nearly full disk. A switch writes its record, a safety copy of the login in use and Claude Code's settings whole,
# and its own floor (50 MB free) is what lets it promise to undo a switch cut off half-way. So under that floor the loop
# begins no switch at all, even for an account that can't work, and writes no record of one. What is true instead: the
# page says in one sentence that the disk is nearly full and that freeing space lets Switchboard switch again; History
# says it once for the spell, not once a pass; and when the account in use can't work, the note for "blocked and
# nothing happened" names the disk as the one thing to do. Stand-ins only; nothing is switched for real.
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import core, notify, server, watch
from switchboard.accounts import api, autoloop, switcher
from test_autoswitch_loop import LoopCase, ok
from test_watch_notes import BEATING, NOW, ON, account

MB = 1024 * 1024


class TheLoop(LoopCase):
    def setUp(self):
        super().setUp()
        self.free = [10 * MB]
        self.answers(ok(100.0, 30.0), ok(10.0, 20.0), ok(10.0, 20.0))    # the account in use is at its limit

    def loop_now(self):
        return self.loop(disk_free=lambda: self.free[0])

    def test_no_switch_begins_and_no_record_is_written_even_for_a_blocked_account(self):
        loop = self.loop_now()
        for _ in range(5):
            res = loop.run_pass()
            self.assertEqual(res.decision.action, 'switch')         # the policy would move it
            self.assertIsNone(res.switched)
            self.clock.advance(60)
        self.assertEqual(self.engine.switches, [])
        self.assertFalse(os.path.exists(core.data_path(switcher.JOURNAL)))
        self.assertIn('disk almost full: no switch begins under 50 MB free', self.log_text())

    def test_history_says_it_once_for_the_spell_and_again_for_a_new_one(self):
        loop = self.loop_now()
        for _ in range(5):
            loop.run_pass()
            self.clock.advance(60)
        self.assertEqual(loop.notes, [(autoloop.DISK_FULL_WORDS, 'problem')])
        self.free[0] = 10 * 1024 * MB                                 # room again: the switch goes, the spell ends
        self.assertIsNotNone(loop.run_pass().switched)
        self.assertIsNone(autoloop.load_state().get('disk_full'))
        self.engine.active = 'a1'
        self.free[0] = 10 * MB
        self.clock.advance(3600)
        loop.run_pass()
        self.assertEqual(loop.notes, [(autoloop.DISK_FULL_WORDS, 'problem')] * 2)


class ThePage(unittest.TestCase):
    def test_one_sentence_while_no_switch_can_begin(self):
        with mock.patch.object(core, 'disk_free', lambda fresh=False: 10 * MB):
            got = server.disk_view()
        self.assertEqual(got['line'], autoloop.DISK_FULL_WORDS)
        self.assertEqual(got['line'].count('. '), 0)                 # one sentence
        self.assertIn('free some space', got['line'])
        with mock.patch.object(core, 'disk_free', lambda fresh=False: 150 * MB):
            got = server.disk_view()
        self.assertEqual((got['low'], got['line']), (True, None))    # low, but a switch can still begin

    def test_the_page_draws_it_in_place_of_the_extras_line(self):
        with open(os.path.join(os.path.dirname(server.__file__), 'web', 'index.html'), encoding='utf-8') as f:
            page = f.read()
        self.assertIn("if (disk.line) h += nl(esc(disk.line))", page)
        self.assertIn("else if (disk.low) h += nl('Your disk is almost full'", page)
        # the line above the floor and the one under it never show together, and read as one family: "Your disk is
        # almost full (... free), so Switchboard ..."; above the floor it says what is paused (notify.DISK_PAUSED,
        # the words History and the notification use) and ends with the one thing to do
        low = page[page.index("else if (disk.low) h += nl("):]
        low = low[:low.index('\n')]
        self.assertIn("', so " + notify.DISK_PAUSED + ". Free some disk space.')", low)
        self.assertTrue(autoloop.DISK_FULL_WORDS.startswith('Your disk is almost full (under 50 MB free), so '
                                                            'Switchboard '))


class TheBlockedNote(unittest.TestCase):
    def st(self):
        return {'active': 'ana@one.example', 'accounts': {'ana@one.example': account(pct_week=100.0),
                                                         'ben@two.example': account()}}

    def test_it_names_the_disk_as_the_one_thing_to_do(self):
        move = api.Decision('switch', target='b', kind='forced', reason='It is at its weekly limit')
        got = watch.troubles(ON, BEATING, self.st(), move, NOW, disk_full=True)['blocked']
        self.assertEqual((got['reason'], got['todo']), (watch.DISK_REASON, watch.DISK_TODO))
        n = notify.blocked(got['who'], got['words'], 5, got['reason'], got['todo'], 1)
        # the family's form (why it can't work. No switch was possible for N min: why not. The one thing to do.),
        # with "no switch" said once: the disk is the reason, freeing space the thing to do
        self.assertEqual(n.body, 'It is at its weekly limit. No switch was possible for 5 min: your disk is almost '
                                 'full (under 50 MB free). Free some disk space.')
        self.assertEqual(n.body.lower().count('switch'), 1)

    def test_with_room_a_switch_to_make_is_no_trouble_as_before(self):
        move = api.Decision('switch', target='b', kind='forced', reason='It is at its weekly limit')
        self.assertNotIn('blocked', watch.troubles(ON, BEATING, self.st(), move, NOW, disk_full=False))


if __name__ == '__main__':
    unittest.main()
