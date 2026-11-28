"""The sign-in watch brings a stopped page back through launchd, and never harms a running one.

launchctl is always a made-up function here (tests/_isolate.py refuses the real one).
"""
import _isolate  # noqa: F401 - first: keeps every write in temp folders

import json
import random
import socket
import unittest

from switchboard import __main__ as cli
from switchboard.accounts import signin_watch as sw

from fakes_autoswitch import FakeClock
from signin_watch_fakes import T0_MONO, make

PLIST = '/nonexistent/LaunchAgents/io.github.amyjainberkeley.switchboard.plist'
UID = 501


class Launchd(object):
    def __init__(self, kickstart=0, bootstrap=0):
        self.calls = []
        self.answers = {'kickstart': kickstart, 'bootstrap': bootstrap}

    def __call__(self, *args):
        self.calls.append(args)
        return self.answers[args[0]]


class Page(object):
    """A page check on a fake clock, with every outside thing made up."""

    def __init__(self, up=False, plist=True, journal=None, kickstart=0, bootstrap=0, age=600.0):
        self.clock = FakeClock(mono=T0_MONO)
        self.up, self.plist, self.journal_value = up, plist, journal
        self.launchd = Launchd(kickstart, bootstrap)
        self.logs, self.lines = [], []
        self.check = sw.PageCheck(7949, PLIST, launchctl=self.launchd, journal=self.journal,
                                  connect=lambda port, timeout: self.up, exists=lambda p: self.plist,
                                  clock=self.clock, log=self.logs.append,
                                  record=sw.Record(clock=self.clock, sink=self.lines.append),
                                  started_m=T0_MONO - age, uid=UID)

    def journal(self):
        if isinstance(self.journal_value, Exception):
            raise self.journal_value
        return self.journal_value

    def run(self, checks):
        out = []
        for _ in range(checks):
            out.append(self.check.check())
            self.clock.advance(sw.PAGE_CHECK_S)
        return out


class PageCheckTest(unittest.TestCase):
    def test_the_label_is_the_pages_job(self):
        self.assertEqual(sw.PAGE_AGENT, cli.AGENT)

    def test_a_page_that_answers_is_left_alone(self):
        p = Page(up=True)
        self.assertEqual(set(p.run(20)), {'up'})
        self.assertEqual(p.launchd.calls, [])

    def test_three_failures_at_least_90_s_apart_then_kickstart_with_the_exact_arguments(self):
        p = Page()
        self.assertEqual(p.run(3), ['down', 'down', 'down'], 'three failures 60 s apart are not enough')
        self.assertEqual(p.launchd.calls, [])
        self.assertEqual(p.run(1), ['kickstart'])
        self.assertEqual(p.launchd.calls, [('kickstart', 'gui/501/io.github.amyjainberkeley.switchboard')])
        self.assertEqual(len(p.logs), 1)
        self.assertIn('kickstart 0', p.logs[0])
        rec = json.loads(p.lines[0])
        self.assertEqual((rec['kind'], rec['action'], rec['rc'], rec['ok']), ('page', 'kickstart', 0, True))

    def test_not_loaded_leads_to_bootstrap(self):
        p = Page(kickstart=113)
        p.run(4)
        self.assertEqual(p.launchd.calls, [('kickstart', 'gui/501/io.github.amyjainberkeley.switchboard'),
                                           ('bootstrap', 'gui/501', PLIST)])
        self.assertIn('kickstart 113, bootstrap 0', p.logs[0])
        self.assertEqual(json.loads(p.lines[0])['action'], 'bootstrap')

    def test_a_page_that_comes_back_resets_the_count(self):
        p = Page()
        p.run(3)
        p.up = True
        p.run(1)
        p.up = False
        p.run(3)
        self.assertEqual(p.launchd.calls, [])

    def test_no_job_file_holds_it_back(self):
        p = Page(plist=False)
        self.assertEqual(set(p.run(20)), {'absent'})
        self.assertEqual(p.launchd.calls, [])

    def test_a_switch_part_way_holds_it_back(self):
        p = Page(journal={'phase': 'writing'})
        self.assertEqual(p.run(6)[-1], 'held')
        self.assertEqual(p.launchd.calls, [])
        p.journal_value = None
        p.run(1)
        self.assertEqual(len(p.launchd.calls), 1, 'once the switch is done')

    def test_a_journal_that_cannot_be_read_holds_it_back(self):
        p = Page(journal=OSError('busy'))
        p.run(6)
        self.assertEqual(p.launchd.calls, [])

    def test_a_young_watch_holds_it_back(self):
        p = Page(age=0.0)
        self.assertEqual(p.run(4), ['down'] * 4)
        self.assertEqual(p.launchd.calls, [])
        p.run(1)
        self.assertEqual(len(p.launchd.calls), 1, 'once the watch is two minutes old')

    def test_at_most_three_tries_in_30_minutes_and_one_line_saying_so(self):
        p = Page(kickstart=0)
        p.run(60)                                     # 30 minutes of a page that never answers
        kicks = [c for c in p.launchd.calls if c[0] == 'kickstart']
        self.assertEqual(len(kicks), 3)
        self.assertEqual(len([x for x in p.logs if 'none more for now' in x]), 1)
        self.assertEqual(len([x for x in p.logs if 'asked launchd' in x]), 3)
        self.assertEqual(len(p.lines), 3)
        p.run(3)
        self.assertEqual(len([c for c in p.launchd.calls if c[0] == 'kickstart']), 3)
        p.run(1)
        self.assertEqual(len([c for c in p.launchd.calls if c[0] == 'kickstart']), 4,
                         'again once the first try is 30 minutes old')

    def test_never_k_never_bootout_and_the_rules_hold_over_many_made_up_runs(self):
        rng = random.Random(20261125)
        for run in range(1500):
            p = Page(kickstart=rng.choice((0, 0, 113, 5, 127)), bootstrap=rng.choice((0, 5, 37, 127)),
                     age=rng.uniform(0.0, 300.0))
            history = []
            for _ in range(rng.randint(1, 150)):
                p.up = rng.random() < 0.3
                p.plist = rng.random() < 0.9
                p.journal_value = rng.choice((None, None, None, {'phase': 'x'}, ValueError('x')))
                before = len(p.launchd.calls)
                p.check.check()
                made = p.launchd.calls[before:]
                if made:
                    self.assertTrue(p.plist and not p.up and p.journal_value is None, 'run %d' % run)
                    self.assertGreaterEqual(p.clock.mono() - p.check.started_m, sw.PAGE_MIN_AGE_S)
                    history.append(p.clock.mono())
                p.clock.advance(rng.choice((sw.PAGE_CHECK_S, sw.PAGE_CHECK_S, rng.uniform(1.0, 400.0))))
            for args in p.launchd.calls:
                self.assertNotIn('-k', args)
                self.assertNotIn('bootout', args)
                self.assertIn(args[0], ('kickstart', 'bootstrap'))
            for i, t in enumerate(history):
                self.assertLessEqual(len([u for u in history[:i + 1] if t - u < sw.PAGE_TRIES_WINDOW_S]),
                                     sw.PAGE_TRIES, 'run %d' % run)


class WatchRunsThePageCheckTest(unittest.TestCase):
    def test_the_watch_checks_every_30_s_and_starts_a_stopped_page_once(self):
        launchd = Launchd()
        page = sw.PageCheck(7949, PLIST, launchctl=launchd, connect=lambda port, timeout: False,
                            exists=lambda p: True, uid=UID)
        w, sim, looks, beats, lines, logs = make(page=page)
        page.clock, page.started_m = sim.clock, sim.clock.mono()
        w.run(until=T0_MONO + sw.PAGE_MIN_AGE_S - 1.0)
        self.assertEqual(launchd.calls, [], 'not in its first two minutes')
        w.run(until=T0_MONO + 200.0)
        self.assertEqual(launchd.calls, [('kickstart', 'gui/501/io.github.amyjainberkeley.switchboard')])

    def test_a_page_check_that_raises_never_ends_the_watch(self):
        class Broken(object):
            def check(self):
                raise RuntimeError('no')
        w, sim, looks, beats, lines, logs = make(page=Broken())
        w.run(until=T0_MONO + 700.0)
        self.assertEqual(len(looks.calls), 3)
        self.assertEqual(len([x for x in logs if 'checking the page failed' in x]), 2)


class ConnectTest(unittest.TestCase):
    def test_page_answers_opens_a_connection_and_sends_nothing(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind(('127.0.0.1', 7950))
        except OSError:
            srv.close()
            self.skipTest('port 7950 is in use')
        srv.listen(1)
        try:
            self.assertTrue(sw.page_answers(7950))
            conn, _ = srv.accept()
            conn.settimeout(1.0)
            self.assertEqual(conn.recv(10), b'', 'nothing is sent, and the connection is closed')
            conn.close()
        finally:
            srv.close()
        self.assertFalse(sw.page_answers(7950))


if __name__ == '__main__':
    unittest.main()
