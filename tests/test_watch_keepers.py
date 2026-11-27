import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Two processes watch each other. The menu bar starts the page through launchd when the page has refused every
# connection for minutes and the person did not stop it (watch.PageKeeper), at most every five minutes and three times
# an hour across every watcher (the count in page-tries.json), and says so in one notification either way. The page
# starts the menu bar's job when the menu bar stopped and was not turned off (watch.MenuBarKeeper). And NOT in the
# normal cases: a page launchd is restarting, a page stopped on purpose, a page waiting for its port, a wake, a menu bar
# the person turned off. While the page waits for its port, the menu bar says which port and what to do.
# launchctl, notifications and clocks are stand-ins; nothing is started.
import os
import shutil
import socket
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import core, menubar, server, watch


class Clock(object):
    def __init__(self):
        self.mono, self.wall = 5000.0, 1.8e9

    def tick(self, s, wall=None):
        self.mono += s
        self.wall += s if wall is None else wall


class Base(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(core.data_dir(), ignore_errors=True)
        os.makedirs(core.data_dir(), exist_ok=True)
        self.clock = Clock()
        self.logged = []
        p = mock.patch.object(watch, 'log', side_effect=self.logged.append)
        p.start()
        self.addCleanup(p.stop)


class PageKeeperBase(Base):
    def setUp(self):
        Base.setUp(self)
        self.up = [False]
        self.starts = []
        self.took = [True]
        self.sent = []

        def start():
            self.starts.append(self.clock.wall)
            return self.took[0]
        self.keeper = watch.PageKeeper(answers=lambda: self.up[0], start=start, send=self.sent.append,
                                       mono=lambda: self.clock.mono, wall=lambda: self.clock.wall)

    def minutes(self, n):
        """Looks every 15 s for n minutes; what each did."""
        out = []
        for _ in range(int(n * 60 / watch.PAGE_LOOK_EVERY)):
            out.append(self.keeper.look())
            self.clock.tick(watch.PAGE_LOOK_EVERY)
        return [x for x in out if x]


class TheMenuBarStartsThePage(PageKeeperBase):
    def test_a_page_that_refused_for_three_minutes_is_started_once_and_said_back(self):
        did = self.minutes(3.5)
        self.assertEqual(did, ['started'])
        self.assertEqual(len(self.starts), 1)
        self.up[0] = True                               # launchd started it; it answers
        self.assertEqual(self.minutes(1), ['back'])
        self.assertEqual([n.body for n in self.sent], ['Switchboard had stopped. It is running again.'])
        self.assertEqual([t['ok'] for t in watch.tries(self.clock.wall)], [True])    # where every watcher reads it

    def test_not_before_three_minutes(self):
        self.assertEqual(self.minutes(2.75), [])
        self.assertEqual(self.starts, [])

    def test_a_page_launchd_restarts_within_seconds_is_left_to_launchd(self):
        self.minutes(0.5)
        self.up[0] = True
        self.minutes(1)
        self.up[0] = False
        self.minutes(2)                                 # each spell starts again from its first refusal
        self.assertEqual(self.starts, [])

    def test_a_page_stopped_on_purpose_is_left_stopped(self):
        watch.note_stopped()
        self.minutes(20)
        self.assertEqual(self.starts, [])
        self.assertEqual(self.sent, [])

    def test_a_page_waiting_for_its_port_is_not_started_again(self):
        watch.mark_port(7801)
        self.minutes(20)
        self.assertEqual(self.starts, [])

    def test_at_most_every_five_minutes_and_three_times_an_hour(self):
        self.took[0] = True                             # launchd takes it, but the page never answers
        self.minutes(60)
        self.assertEqual(len(self.starts), watch.STARTS_PER_HOUR)
        gaps = [b - a for a, b in zip(self.starts, self.starts[1:])]
        self.assertTrue(all(g >= watch.START_GAP for g in gaps), gaps)
        self.assertEqual([n.body for n in self.sent],
                         ['Switchboard has stopped and could not be started. Run: switchboard doctor'])  # once a spell

    def test_a_start_by_another_watcher_counts(self):
        watch.note_try('another watcher', None, self.clock.wall, now=self.clock.wall)
        self.minutes(4)
        self.assertEqual(self.starts, [])               # within five minutes of the other's start

    def test_launchd_refusing_is_said_once(self):
        self.took[0] = False
        self.assertEqual(self.minutes(3.5), ['failed'])
        self.assertEqual(len(self.sent), 1)
        self.assertIn('could not be started', self.sent[0].body)

    def test_no_background_job_starts_nothing_and_says_nothing(self):
        self.took[0] = None
        self.minutes(20)
        self.assertEqual(self.sent, [])
        self.assertEqual(watch.tries(self.clock.wall), [])

    def test_it_asks_once_more_right_before_it_starts(self):
        asks = []

        def answers():
            asks.append(self.clock.mono)
            return len(asks) == 14                      # the thirteenth look (at three minutes) asks twice: up
        self.keeper.answers = answers
        self.minutes(3.25)
        self.assertEqual(len(asks), 14)
        self.assertEqual(self.starts, [])

    def test_refusals_before_a_wake_do_not_count(self):
        self.minutes(2)
        self.clock.tick(0, wall=6 * 3600)
        self.assertEqual(self.minutes(2.5), [])
        self.assertEqual(self.starts, [])

    def test_a_look_that_fails_never_ends_the_watch(self):
        self.keeper.answers = mock.Mock(side_effect=RuntimeError('made up'))
        stop = mock.Mock()
        stop.is_set.side_effect = [False, False, True]
        self.keeper.run(stop=stop, sleep=lambda s: None)
        self.assertEqual(len([x for x in self.logged if 'could not look' in x]), 1)


class TheMenuBarsTwoNotes(unittest.TestCase):
    def test_each_says_what_happened_and_the_one_thing_to_do(self):
        from switchboard import notify
        back, down = notify.page_back(1), notify.page_down(1)
        self.assertEqual(back.body, 'Switchboard had stopped. It is running again.')
        self.assertTrue(down.body.endswith('Run: switchboard doctor'))
        for n in (back, down):
            for bad in ('unlimited', 'never run out', 'bypass', 'avoid', ' her '):
                self.assertNotIn(bad, (n.title + ' ' + n.body).lower())


class AConnectionIsTheLook(unittest.TestCase):
    def test_a_listening_port_answers_and_a_closed_one_does_not(self):
        s = socket.socket()
        s.bind(('127.0.0.1', 0))
        s.listen(1)
        port = s.getsockname()[1]
        try:
            self.assertTrue(watch.page_answers(port))
        finally:
            s.close()
        self.assertFalse(watch.page_answers(port))

    def test_a_job_is_started_through_launchd_only(self):
        calls = []
        self.assertIsNone(watch.start_job('x.label', '/nowhere/x.plist', lambda *a: calls.append(a) or 0, 'gui/1'))
        self.assertEqual(calls, [])                     # no job description: nothing is started
        plist = os.path.join(core.data_dir(), 'x.plist')
        os.makedirs(core.data_dir(), exist_ok=True)
        with open(plist, 'w') as f:
            f.write('<plist/>')
        answers = [1, 0]
        self.assertTrue(watch.start_job('x.label', plist, lambda *a: calls.append(a) or answers.pop(0), 'gui/1'))
        self.assertEqual(calls, [('kickstart', 'gui/1/x.label'), ('bootstrap', 'gui/1', plist)])


class ThePageStartsTheMenuBar(Base):
    def setUp(self):
        Base.setUp(self)
        self.runs = [False]
        self.plist = ['/job.plist']
        self.starts = []
        self.off = [False]
        self.keeper = watch.MenuBarKeeper(running=lambda: self.runs[0], job=lambda: self.plist[0],
                                          start=lambda p: self.starts.append(p) or True, off=lambda: self.off[0],
                                          mono=lambda: self.clock.mono, wall=lambda: self.clock.wall)

    def ticks(self, minutes):
        out = []
        for _ in range(int(minutes)):
            out.append(self.keeper.look())
            self.clock.tick(60)
        return [x for x in out if x]

    def test_a_menu_bar_gone_for_a_minute_is_started_through_its_job(self):
        self.assertEqual(self.ticks(2), ['started'])
        self.assertEqual(self.starts, ['/job.plist'])

    def test_one_launchd_brings_back_within_the_minute_is_left_alone(self):
        self.keeper.look()
        self.runs[0] = True
        self.ticks(5)
        self.assertEqual(self.starts, [])

    def test_a_menu_bar_the_person_turned_off_stays_off(self):
        self.off[0] = True                              # switchboard menubar off
        self.ticks(30)
        self.assertEqual(self.starts, [])
        self.off[0] = False                             # switchboard menubar on, and it stopped by itself later
        self.ticks(2)
        self.assertEqual(len(self.starts), 1)

    def test_no_job_no_start(self):
        self.plist[0] = None
        self.ticks(30)
        self.assertEqual(self.starts, [])

    def test_at_most_every_five_minutes_and_three_times_an_hour(self):
        self.ticks(60)
        self.assertEqual(len(self.starts), watch.BAR_PER_HOUR)
        self.assertEqual(len([x for x in self.logged if 'no more this hour' in x]), 1)

    def test_from_the_pages_tick_only_under_launchd(self):
        calls = []
        plist = os.path.join(core.data_dir(), 'menubar-job.plist')
        with open(plist, 'w') as f:
            f.write('<plist/>')
        with mock.patch.object(server, 'BAR_KEEPER', []), \
                mock.patch.object(menubar, 'running_pid', lambda: None), \
                mock.patch.object(menubar, 'our_job', lambda: plist), \
                mock.patch.object(menubar, 'launchctl', lambda *a: calls.append(a) or 0):
            with mock.patch.object(watch, 'background', lambda: False):
                self.assertIsNone(server.menubar_tick())
            with mock.patch.object(watch, 'background', lambda: True):
                server.menubar_tick()                               # the first look: gone since now
                server.BAR_KEEPER[0].gone_since -= watch.BAR_DOWN_FOR + 1
                self.assertEqual(server.menubar_tick(), 'started')
        self.assertEqual(calls, [('kickstart', '%s/%s' % (menubar.domain(), menubar.AGENT))])


class TheMenuBarSaysThePort(Base):
    def test_while_the_page_waits_for_its_port_the_menu_says_which_and_what_to_do(self):
        watch.mark_port(core.port())
        said = menubar.watch_note()
        self.assertIn('Port %d is in use by another program' % core.port(), said)
        self.assertIn('Auto-switch still works', said)
        self.assertIn('switchboard update --port', said)
        items = menubar.menu(None, note=said, status={'kind': 'busy', 'line': menubar.UNREADABLE_LINE})
        self.assertEqual(items[0], ('note', said))
        self.assertNotIn(('do', 'Start', 'start'), items)   # the page runs: Start would not help

    def test_no_mark_or_another_port_says_nothing(self):
        self.assertIsNone(menubar.watch_note())
        watch.mark_port(core.port() + 1)
        self.assertIsNone(menubar.watch_note())


if __name__ == '__main__':
    unittest.main()
