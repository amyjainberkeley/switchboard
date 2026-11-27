import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The pace of an account's week, in words, only when it matters: when, at the pace of its use so far, its weekly limit
# would be full before it refills, the weekly line says when ("at this pace, full by Sat 4:23 PM"). When it lasts,
# nothing is said: no "on pace", no mark. One rule (switchboard/pace.py) for every surface: the terminal, the menu
# bar, the page's state and `switchboard status --json` (limits.week.pace).
#
# Never a guess: the reading must be one the page shows and under an hour old, the week open 12 hours with 10% used
# (plan.week_ahead's own floor), the reset still ahead. A check over many made-up cases holds the rule to that, and
# holds that a reading the rule can't use leaves every line as it was.
#
# Made-up *.example accounts only. No network, no engine, no page.
import os
import random
import sys
import unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import menubar, pace, plan, report, term

TZ = timezone(timedelta(hours=-8))
NOW = datetime(2026, 11, 13, 15, 31, 25, tzinfo=TZ).timestamp()          # Fri 3:31 PM
MON_NOON = datetime(2026, 11, 16, 12, 0, tzinfo=TZ).timestamp()          # the week refills Mon 12:00 PM
WEEK = 7 * 86400
FAST, SLOW, EARLY, OLD = 'ana@fast.example', 'ana@slow.example', 'ana@early.example', 'ana@old.example'


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def w(pct, resets=MON_NOON):
    return {'pct': pct, 'resets': iso(resets) if resets is not None else None, 'pace': None, 'lasts': None}


def acct(slot, email, weekly, session=None, **more):
    return dict({'email': email, 'name': email, 'alias': '', 'slot': slot, 'plan': 'Ultra', 'org': '', 'active': False,
                 'needs_login': False, 'disabled': False, 'unmanaged': False, 'trusted': True,
                 'usage': {'session': session, 'weekly': weekly, 'scoped': [], 'spend': None},
                 'usage_at': NOW - 60, 'checked_at': NOW - 60}, **more)


def view(**more):
    """FAST: 80% used four days into its week (an even week is at 59%): full by Sat. SLOW: 40%, it lasts. EARLY: a week
    open 6 hours. OLD: 90% read two hours ago."""
    return dict({'now': NOW, 'active': FAST, 'next': None, 'setup': {'engine': True, 'accounts': True},
                 'auto': {'available': True, 'enabled': True, 'on': True, 'session_at': 90.0, 'weekly_at': 95.0,
                          'model_at': 95.0, 'weekly_set': True, 'models': []},
                 'accounts': [acct(1, FAST, w(80), active=True), acct(2, SLOW, w(40)),
                              acct(3, EARLY, w(30, NOW + WEEK - 6 * 3600)),
                              acct(4, OLD, w(90), usage_at=NOW - 7200, checked_at=NOW - 7200)]}, **more)


def full_at(used, read_at=NOW - 60, resets=MON_NOON):
    """When the week is full at its own average pace (plan.week_ahead's arithmetic)."""
    elapsed = read_at - (resets - WEEK)
    return read_at + (100.0 - used) * elapsed / used


class TheRule(unittest.TestCase):
    def test_full_before_it_refills_says_when(self):
        got = pace.week(acct(1, FAST, w(80)), NOW)
        self.assertEqual(got['even'], 59)                       # 99.5 hours of 168
        self.assertIs(got['ahead'], True)
        self.assertAlmostEqual(got['full_at'], full_at(80), places=3)
        self.assertLess(got['full_at'], MON_NOON)
        self.assertEqual(pace.full_at(acct(1, FAST, w(80)), NOW), got['full_at'])

    def test_a_week_that_lasts_says_nothing(self):
        got = pace.week(acct(2, SLOW, w(40)), NOW)
        self.assertEqual(got, {'even': 59, 'ahead': False, 'full_at': None})
        self.assertIsNone(pace.full_at(acct(2, SLOW, w(40)), NOW))

    def test_too_little_to_say_is_nothing(self):
        early = pace.week(acct(3, EARLY, w(30, NOW + WEEK - 6 * 3600)), NOW)    # open 6 hours: under 12
        self.assertEqual(early, {'even': 4, 'ahead': None, 'full_at': None})
        self.assertEqual(pace.week(acct(1, FAST, w(8)), NOW)['ahead'], None)    # under 10% used
        for a in (acct(1, FAST, w(80), usage_at=NOW - 7200),                    # older than an hour
                  acct(1, FAST, w(80), trusted=False),                          # not shown on the page
                  acct(1, FAST, w(80), usage_at=NOW + 60),                      # dated ahead of the clock
                  acct(1, FAST, w(80, NOW - 60)),                               # past its reset
                  acct(1, FAST, w(80, None)),                                   # no reset
                  acct(1, FAST, w(None)),                                       # no number
                  acct(1, FAST, None), acct(1, FAST, w(80), usage=None), acct(1, FAST, w(80), usage_at=None)):
            self.assertIsNone(pace.full_at(a, NOW))
        self.assertIsNone(pace.week(acct(1, FAST, w(80), usage_at=NOW - 7200), NOW))
        self.assertIsNone(pace.week(acct(1, FAST, w(100)), NOW)['ahead'])        # already full: nothing to forecast

    def test_over_many_made_up_cases_the_rule_never_breaks(self):
        rnd = random.Random(20261125)
        said = 0
        for _ in range(5000):
            now = 1.79e9 + rnd.uniform(0, 1e7)
            resets = now + rnd.uniform(-3600, WEEK + 3600)
            used = rnd.choice([None, 0, 5, 9.99, 10, 100, rnd.uniform(0, 100)])
            at = now - rnd.choice([0, 30, 600, 3599, 3601, 7200, -10, rnd.uniform(-60, 9000)])
            trusted = rnd.choice([True, True, True, False, None])
            a = acct(1, FAST, {'pct': used, 'resets': iso(resets)}, usage_at=at, trusted=trusted)
            got = pace.week(a, now)
            if trusted is False or used is None or resets <= now or now - at > pace.FRESH or at - now > pace.AHEAD:
                self.assertIsNone(got)
                continue
            self.assertTrue(0 <= got['even'] <= 100)
            est = plan.week_ahead(used, resets, at, now)
            if est is None:
                self.assertEqual((got['ahead'], got['full_at']), (None, None))
                continue
            if got['ahead']:
                said += 1
                # full before it refills, after now, and only for a week used faster than an even one
                self.assertTrue(now < got['full_at'] < resets, (now, got, resets))
                self.assertGreater(used, (at - (resets - WEEK)) * 100.0 / WEEK - 1e-9)
            else:
                self.assertIsNone(got['full_at'])
                self.assertLessEqual(used, (at - (resets - WEEK)) * 100.0 / WEEK + 1e-9)
        self.assertGreater(said, 100)                       # the cases reach the words, not only the silences


class TheTerminal(unittest.TestCase):
    def blocks(self, v=None, width=None):
        got = term.view(term.read(v or view(), tz=TZ), width=width)
        return dict((next(e for e in (FAST, SLOW, EARLY, OLD) if e in b.split('\n')[0]), b)
                    for b in '\n'.join(got).split('\n\n')[1:])

    def test_the_weekly_line_says_when_it_would_be_full(self):
        words = 'at this pace, full by %s' % term.clock(full_at(80), NOW, TZ)
        self.assertEqual(words, 'at this pace, full by Sat 4:23 PM')
        line = self.blocks()[FAST].split('\n')[1]
        self.assertEqual(line, '   weekly  ' + '━' * 19 + '─' * 5 + '   80%  resets Mon 12:00 PM · in 3 days · ' + words)
        week = term.read(view(), tz=TZ)['accounts'][0]['limits'][-1]
        self.assertEqual(week['pace'], words)

    def test_nothing_is_said_when_it_lasts_or_too_little_is_known(self):
        blocks = self.blocks()
        for email in (SLOW, EARLY, OLD):
            self.assertNotIn('pace', blocks[email], email)
            self.assertNotIn('full', blocks[email], email)
        self.assertTrue(blocks[SLOW].split('\n')[1].endswith('40%  resets Mon 12:00 PM · in 3 days'))

    def test_pace_words_are_never_red_and_the_reset_stays_dim(self):
        coloured = term.view(term.read(view(), tz=TZ), colour=True)
        line = next(x for x in coloured if 'at this pace' in x)
        self.assertTrue(line.endswith('\x1b[2mresets Mon 12:00 PM · in 3 days\x1b[0m\x1b[2m · \x1b[0mat this pace, full by Sat 4:23 PM'),
                        line)


class TheMenuBar(unittest.TestCase):
    def rows(self, v=None):
        return dict((r['email'], r) for r in menubar.rows(v or view(), TZ))

    def test_the_pace_is_the_pages_to_say(self):
        # The menu bar's row is its email, one word and its bars; when the week would be full is said on the page
        # and in the terminal (above), never in the row.
        for email, r in self.rows().items():
            text = repr(menubar.row_ops(r))
            self.assertNotIn('At this pace', text, email)
            self.assertNotIn('full by', text, email)
            self.assertNotIn('pace', r, email)


class TheJson(unittest.TestCase):
    def doc(self, v=None):
        return dict((a['email'], a) for a in report.document(v or view(), '0.0.0')['accounts'])

    def test_limits_week_gains_pace(self):
        got = self.doc()
        self.assertEqual(got[FAST]['limits']['week']['pace'],
                         {'even_percent': 59, 'ahead': True, 'full_at': report.when(full_at(80))})
        self.assertTrue(got[FAST]['limits']['week']['pace']['full_at'].endswith('Z'))
        self.assertEqual(got[SLOW]['limits']['week']['pace'], {'even_percent': 59, 'ahead': False, 'full_at': None})
        self.assertEqual(got[EARLY]['limits']['week']['pace'], {'even_percent': 4, 'ahead': None, 'full_at': None})
        self.assertIsNone(got[OLD]['limits']['week']['pace'])                    # too old: not known
        self.assertEqual(report.SCHEMA, 1)


if __name__ == '__main__':
    unittest.main()
