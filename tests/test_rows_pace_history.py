import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Once in History: the first time in a week that an account's weekly limit would be full before it refills at the
# pace of its use so far (pace.week, the rule every surface says), one History line says so, once for that week. It is
# never repeated in that week, a week that lasts says nothing, and the next week can say it again.
#
# Made-up *.example accounts only. No network, no engine, no page.
import os
import sys
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import core, pace, server

D, H = 86400, 3600
FAST, SLOW = 'ana@fast.example', 'ana@slow.example'


def iso(t):
    return server.iso(t)


def state(now, fast_pct=80, resets=None):
    """FAST: 80% used four days into its week (full before it refills). SLOW: 30% (it lasts)."""
    resets = now + 3 * D if resets is None else resets
    st = core.blank_state()
    for email, pct in ((FAST, fast_pct), (SLOW, 30)):
        a = core.ensure_account(st, email)
        a.update(connected=True, slot=len(st['accounts']), usage_at=now - 60,
                 usage={'session': None, 'weekly': {'pct': pct, 'resets': iso(resets), 'pace': None, 'lasts': None},
                        'scoped': [], 'spend': None})
    return st


def lines(st):
    return [e['text'] for e in st['events'] if 'at this pace' in e['text']]


class OnceInHistory(unittest.TestCase):
    def test_the_first_time_in_a_week_one_line(self):
        now = time.time()
        st = state(now)
        pace.note_history(st, now)
        got = lines(st)
        self.assertEqual(len(got), 1)
        self.assertRegex(got[0], r'^ana@fast\.example: at this pace, its week is full by .+, before it refills$')
        self.assertNotIn(SLOW, ' '.join(got))

    def test_never_repeated_in_that_week(self):
        now = time.time()
        st = state(now)
        for i in range(5):
            pace.note_history(st, now + i * H)
            st['accounts'][FAST]['usage_at'] = now + i * H
        self.assertEqual(len(lines(st)), 1)
        st['accounts'][FAST]['usage']['weekly']['pct'] = 40       # it slows down, then speeds up again: still once
        pace.note_history(st, now + 6 * H)
        st['accounts'][FAST]['usage']['weekly']['pct'] = 90
        st['accounts'][FAST]['usage_at'] = now + 7 * H
        pace.note_history(st, now + 7 * H)
        self.assertEqual(len(lines(st)), 1)

    def test_the_next_week_may_say_it_again(self):
        now = time.time()
        st = state(now)
        pace.note_history(st, now)
        later = now + 7 * D
        st['accounts'][FAST].update(usage_at=later - 60)
        st['accounts'][FAST]['usage']['weekly'].update(resets=iso(later + 3 * D), pct=80)
        pace.note_history(st, later)
        self.assertEqual(len(lines(st)), 2)

    def test_a_week_that_lasts_or_too_little_known_says_nothing(self):
        now = time.time()
        st = state(now, fast_pct=40)
        st['accounts'][SLOW]['usage_at'] = now - 2 * H          # too old to say anything
        pace.note_history(st, now)
        self.assertEqual(lines(st), [])

    def test_the_tick_notes_it(self):
        with mock.patch.object(pace, 'note_history') as noted, mock.patch.object(server.history, 'note_tick'):
            server.note_history({}, {}, {}, {}, {}, None)
        self.assertEqual(noted.call_count, 1)


if __name__ == '__main__':
    unittest.main()
