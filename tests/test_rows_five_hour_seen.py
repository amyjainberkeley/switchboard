import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# "No 5-hour limit" is said only for an account that has never shown a 5-hour number.
#
# One reading can hold no 5-hour window on a plan that has the limit (a reset passed and nothing used since: a Team
# seat's saved readings went 100%, then no 5-hour number, then 0% ten minutes later). Said from that one reading, the
# row would call the plan one with no 5-hour limit, which is false. So the rule also asks the saved readings
# (insights.five_seen, the log's 8 days): an account that ever showed a 5-hour number, 0 included, is not known for
# that reading, and each face shows what it shows for a number not known. An account that never showed one (a plan
# with no 5-hour limit) says the words as before. server.render hands every face the answer as `five_seen`.
#
# The log keeps 8 days, and an account at rest writes no line while nothing changes, so its prune keeps, for each
# account Switchboard still reads, the newest line with a 5-hour number however old: a plan that has a 5-hour limit
# never loses it by resting. A removed account's lines still go after 8 days. `status --json` follows the same rule:
# `five_hour` is null (no such limit) only where a row would say the words.
#
# Made-up *.example accounts only. No network, no engine, no page, no Chrome.
import json
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import core, insights, menubar, report, server, term  # noqa: E402
from test_rows_two_limits import NOW, TEAM, TZ, ULTRA, acct, view, w  # noqa: E402

GONE = 'gone@team.example'                                   # an account removed from Switchboard
FACES = (('term', 'no 5-hour limit'), ('menu', 'No 5-hour limit'), ('page', 'No 5-hour limit on this plan'))
UNKNOWN = {'used_percent': None, 'refills_at': None}         # `status --json`: a limit that is not known


def line(account, t, five, week=17):
    return json.dumps({'t': t, 'account': account, 'five': five, 'week': week, 'models': {}}, separators=(',', ':'))


class Log(unittest.TestCase):
    """A readings log of this test's own, in the throwaway data folder."""

    def setUp(self):
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = False
        self.path = core.data_path(insights.READINGS_FILE)
        self.forget()
        self.addCleanup(self.forget)
        self.now = time.time()

    def forget(self):
        for p in (self.path, self.path + '.lock'):
            if os.path.isdir(p):
                os.rmdir(p)
            elif os.path.exists(p):
                os.remove(p)
        insights.FIVE_SEEN.update(key=None, who=frozenset())
        insights.READINGS.update(last={}, loaded=False, pruned_at=None)

    def write(self, *lines, **how):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, how.get('mode', 'w'), encoding='utf-8') as f:
            f.write(''.join(x + '\n' for x in lines))

    def team_then_none(self):
        """The readings seen on a real Mac: 100%, then no 5-hour number, then 0%; and a plan that never has one."""
        self.write(line(ULTRA, self.now - 9000, None, 27), line(TEAM, self.now - 8000, 100),
                   line(TEAM, self.now - 700, None), line(ULTRA, self.now - 600, None, 28))


class TheSavedReadings(Log):
    def test_nobody_without_a_log(self):
        self.assertEqual(insights.five_seen(), frozenset())

    def test_an_account_that_ever_showed_a_5_hour_number(self):
        self.team_then_none()
        self.assertEqual(insights.five_seen(), frozenset([TEAM]))        # its last line holds none: it still counts

    def test_a_5_hour_window_at_0_is_a_number(self):
        self.write(line(TEAM, self.now - 60, 0), line(ULTRA, self.now - 50, None))
        self.assertEqual(insights.five_seen(), frozenset([TEAM]))

    def test_a_line_written_by_another_process_is_seen_at_the_next_look(self):
        self.write(line(TEAM, self.now - 700, None), line(ULTRA, self.now - 600, None))
        self.assertEqual(insights.five_seen(), frozenset())
        self.write(line(TEAM, self.now - 10, 3), mode='a')              # the page's own tick, in its own process
        self.assertEqual(insights.five_seen(), frozenset([TEAM]))

    def test_the_log_is_read_again_only_when_it_changed(self):
        self.team_then_none()
        self.assertEqual(insights.five_seen(), frozenset([TEAM]))
        real = insights._lines
        self.addCleanup(setattr, insights, '_lines', real)
        insights._lines = lambda path: self.fail('the log was read again though nothing changed')
        self.assertEqual(insights.five_seen(), frozenset([TEAM]))

    def test_a_line_that_cannot_be_read_is_skipped(self):
        self.write('not json', line(TEAM, self.now - 60, 41), '{"t": "soon", "account": "x@old.example", "five": 9}')
        self.assertEqual(insights.five_seen(), frozenset([TEAM]))

    def test_a_log_that_cannot_be_read_is_nobody_and_nothing_raises(self):
        os.makedirs(self.path)                                           # a folder where the log should be
        self.assertEqual(insights.five_seen(), frozenset())

    def test_the_prune_keeps_a_kept_accounts_last_5_hour_number_however_old(self):
        old = self.now - insights.READINGS_KEEP - 3600                    # older than the log's 8 days
        self.write(line(TEAM, old - 60, 30), line(TEAM, old, None), line(ULTRA, old, None, 27),
                   line(GONE, old, 40), line(TEAM, self.now - 60, None, 18))
        insights._prune(self.path, self.now, accounts=(TEAM, ULTRA))
        with open(self.path, encoding='utf-8') as f:
            kept = [json.loads(x) for x in f.read().splitlines()]
        self.assertEqual([(x['account'], x['five']) for x in kept], [(TEAM, 30), (TEAM, None)])   # in file order
        self.assertEqual(insights.five_seen(), frozenset([TEAM]))     # a removed account's line went, as before

    def test_with_no_account_named_the_prune_is_as_before(self):
        old = self.now - insights.READINGS_KEEP - 3600
        self.write(line(TEAM, old, 30), line(ULTRA, self.now - 60, None))
        insights._prune(self.path, self.now)
        self.assertEqual(insights.five_seen(), frozenset())

    def test_the_tick_keeps_it_for_every_account_it_reads(self):
        old = self.now - insights.READINGS_KEEP - 3600
        self.write(line(TEAM, old - 60, 30), line(TEAM, old, None), line(GONE, old, 40))
        wrote = insights.note_readings([(TEAM, self.now, None, 18, {}), (ULTRA, self.now, None, 28, {})], now=self.now)
        self.assertEqual(wrote, 2)
        self.assertEqual(insights.five_seen(), frozenset([TEAM]))
        self.assertNotIn(GONE, open(self.path, encoding='utf-8').read())

    def test_nobody_in_the_demo(self):
        self.team_then_none()
        core.DEMO = True
        self.assertEqual(insights.five_seen(), frozenset())


class TheOneRule(unittest.TestCase):
    def test_an_account_that_showed_a_5_hour_number_is_not_known(self):
        for face, said in FACES:
            usage = {'session': None, 'weekly': w(17)}
            self.assertEqual(term.no_five(face, account={'usage': usage, 'five_seen': True}), '', face)
            self.assertEqual(term.no_five(face, account={'usage': usage, 'five_seen': False}), said, face)
            self.assertEqual(term.no_five(face, account={'usage': usage}), said, face)       # a state without the key

    def test_with_a_number_nothing_changes(self):
        usage = {'session': w(40), 'weekly': w(17)}
        self.assertEqual(term.no_five('term', account={'usage': usage, 'five_seen': True}), '')


class EveryFace(unittest.TestCase):
    """The Team seat's reading with no 5-hour window, on the faces that read the state."""

    def view(self):
        v = view()
        for a in v['accounts']:
            a['five_seen'] = a['email'] == TEAM
            if a['email'] == TEAM:
                a['usage'] = dict(a['usage'], session=None)
        return v

    def test_the_terminal_shows_no_5_hour_line_for_it_and_the_words_for_the_plan_without_one(self):
        got = term.view(term.read(self.view(), tz=TZ))
        b = dict((x.split('\n')[0].split()[1], x.split('\n')[1:]) for x in '\n'.join(got).split('\n\n')[1:])
        self.assertEqual(len(b[TEAM]), 1)
        self.assertTrue(b[TEAM][0].startswith('   weekly  '))
        self.assertNotIn('5-hour', '\n'.join(b[TEAM]))
        self.assertEqual(b[ULTRA][0], '   no 5-hour limit')

    def test_the_menu_bar_too(self):
        rows = dict((r['email'], r) for r in menubar.rows(self.view(), TZ))
        self.assertEqual((rows[TEAM]['five'], rows[TEAM]['no_five']), (None, ''))
        texts = [op[5][0][0] for op in menubar.row_ops(rows[TEAM]) if op[0] == 'text']
        self.assertEqual(texts[0], 'Weekly')
        self.assertNotIn('5-hour', menubar.row_label(rows[TEAM]))
        self.assertEqual(rows[ULTRA]['no_five'], 'No 5-hour limit')


class TheState(Log):
    """server.render says it for each account, from the saved readings."""

    def state(self):
        st = core.blank_state()
        for slot, email in enumerate((ULTRA, TEAM), 1):
            a = core.ensure_account(st, email)
            a.update(connected=True, slot=slot, usage_status='ok', usage_error=None, note=None,
                     usage={'session': None, 'weekly': w(17), 'scoped': [], 'spend': None}, usage_at=self.now - 60)
        st['active'] = ULTRA
        return st

    def rendered(self, st=None):
        facts = {'profiles': {}, 'browser': {'name': 'Google Chrome', 'present': False, 'tabs_blocked': False},
                 'engine': {'installed': True, 'claude': True}, 'links': {'installed': False, 'on': False},
                 'auto': {'available': True, 'enabled': False, 'session_at': 90, 'weekly_at': 95},
                 'setup_hidden': True, 'now': self.now, 'demo': False}
        return server.render(st or self.state(), facts)

    def limits(self, out):
        return dict((a['email'], [(x['label'], x.get('words') if x.get('none') else None) for x in a['limits']])
                    for a in term.read(out, tz=TZ)['accounts'])

    def test_no_saved_reading_with_a_5_hour_number_both_say_the_words(self):
        out = self.rendered()
        self.assertEqual([(a['email'], a['five_seen']) for a in out['accounts']], [(ULTRA, False), (TEAM, False)])
        got = self.limits(out)
        self.assertEqual(got[ULTRA][0], ('5-hour', 'no 5-hour limit'))
        self.assertEqual(got[TEAM][0], ('5-hour', 'no 5-hour limit'))

    def test_the_one_that_showed_a_5_hour_number_does_not(self):
        self.team_then_none()
        out = self.rendered()
        self.assertEqual([(a['email'], a['five_seen']) for a in out['accounts']], [(ULTRA, False), (TEAM, True)])
        got = self.limits(out)
        self.assertEqual(got[ULTRA][0], ('5-hour', 'no 5-hour limit'))
        self.assertEqual([label for label, _ in got[TEAM]], ['weekly'])


class TheReport(unittest.TestCase):
    """`status --json`: five_hour is null (the account has no such limit) only where a row would say the words."""

    def five(self, **more):
        return report.limits(acct(1, TEAM, weekly=w(17), **more), NOW)['five_hour']

    def test_a_plan_with_no_5_hour_limit_is_null(self):
        self.assertIsNone(self.five())
        self.assertIsNone(self.five(five_seen=False))
        self.assertIsNone(self.five(trusted=False))                      # a reading too old to show still tells

    def test_one_that_showed_a_5_hour_number_is_not_known(self):
        self.assertEqual(self.five(five_seen=True), UNKNOWN)
        self.assertEqual(self.five(five_seen=True, trusted=False), UNKNOWN)

    def test_with_a_number_nothing_changes(self):
        got = report.limits(acct(1, TEAM, session=w(40), weekly=w(17), five_seen=True), NOW)['five_hour']
        self.assertEqual(got['used_percent'], 40)


class ThePage(unittest.TestCase):
    """The page follows the same rule (read from its file: no browser here)."""

    def setUp(self):
        with open(os.path.join(HERE, '..', 'switchboard', 'web', 'index.html'), encoding='utf-8') as f:
            self.page = f.read()

    def test_the_words_and_the_weekly_hover_ask_the_saved_readings_too(self):
        self.assertIn("raw && Object.keys(raw).length && !raw.session && !a.five_seen ? NO_FIVE : 'Not known'",
                      self.page)
        self.assertIn("noFive: !!raw && !raw.session && !a.five_seen && 'Anthropic reports no 5-hour limit", self.page)
        self.assertEqual(self.page.count('!a.five_seen'), 2)            # the two places, and no third rule


if __name__ == '__main__':
    unittest.main()
