import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# When auto-switch last decided, said only when it is late ("Auto-switch last decided 12 minutes ago."), and a page that
# restarted more than three times in ten minutes: on the page (server.watch_words through auto_view), in the menu bar
# (menubar.watch_lines), under the terminal's list (__main__.watch_line) and in doctor (__main__._doctor_watch). The
# age is awake time since the loop's last finished pass, past the pass it planned: a long planned wait, a sleep or a
# Mac that restarted is never "late". Clocks and the heartbeat are stand-ins; nothing is started.
import os
import shutil
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import core, menubar, server, watch
from switchboard.accounts import api, autoloop
from test_autoswitch_loop import LoopCase, ok

NOW = 1.8e9
LATE = {'running': True, 'pass_age_s': 14 * 60.0 + 5, 'last_pass_at': NOW - 845, 'next_pass_at': NOW - 785}
IN_TIME = {'running': True, 'pass_age_s': 90.0, 'last_pass_at': NOW - 90, 'next_pass_at': NOW - 30}
AUTO_ON = {'enabled': True, 'on': True, 'problem': None}


class Late(unittest.TestCase):
    def test_late_past_the_planned_pass_says_the_minutes(self):
        self.assertEqual(watch.decided_late(LATE, True), 14)
        self.assertEqual(watch.decided_words(14), 'Auto-switch last decided 14 minutes ago.')   # as the page spells it

    def test_a_few_minutes_over_the_plan_on_a_busy_mac_is_not_late(self):
        s = dict(IN_TIME, pass_age_s=60 + watch.DECIDED_LATE - 1, next_pass_at=IN_TIME['last_pass_at'] + 60)
        self.assertIsNone(watch.decided_late(s, True))

    def test_a_long_wait_the_loop_planned_is_not_late(self):
        s = {'running': True, 'pass_age_s': 13 * 60.0, 'last_pass_at': NOW - 780, 'next_pass_at': NOW - 180}
        self.assertIsNone(watch.decided_late(s, True))

    def test_a_plan_longer_than_the_loop_ever_makes_counts_as_its_longest(self):
        s = dict(LATE, pass_age_s=20 * 60.0 + 5, next_pass_at=LATE['last_pass_at'] + 86400)
        self.assertEqual(watch.decided_late(s, True), 20)          # 10 min planned at most, then 5 more

    def test_nothing_while_off_not_running_or_never_passed_in_this_boot(self):
        self.assertIsNone(watch.decided_late(LATE, False))
        self.assertIsNone(watch.decided_late(dict(LATE, running=False), True))
        self.assertIsNone(watch.decided_late(dict(LATE, pass_age_s=None), True))
        self.assertIsNone(watch.decided_late(None, True))

    def test_hours_past_two(self):
        self.assertEqual(watch.decided_words(150), 'Auto-switch last decided 2 hours ago.')
        self.assertIsNone(watch.decided_words(None))


class TheAgeIsAwakeTime(LoopCase):
    def test_the_heartbeat_gives_the_awake_age_of_the_last_pass(self):
        self.answers(ok(20.0, 40.0), ok(20.0, 40.0), ok(20.0, 40.0))
        self.loop().run_pass()
        self.assertLess(autoloop.status(self.clock)['pass_age_s'], 1.0)
        self.clock.advance(120)
        self.assertAlmostEqual(autoloop.status(self.clock)['pass_age_s'], 120.0, delta=1.0)
        self.clock.slept(3 * 3600)                      # a sleep is not time the loop could decide in
        self.assertAlmostEqual(autoloop.status(self.clock)['pass_age_s'], 120.0, delta=1.0)

    def test_after_the_mac_restarted_there_is_no_age(self):
        self.answers(ok(20.0, 40.0), ok(20.0, 40.0), ok(20.0, 40.0))
        self.loop().run_pass()
        self.clock.b = 'boot-B'
        self.assertIsNone(autoloop.status(self.clock)['pass_age_s'])


class Base(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(core.data_dir(), ignore_errors=True)
        os.makedirs(core.data_dir(), exist_ok=True)

    def restarted(self, n):
        for i in range(n):
            watch.note_start(now=None)


class ThePageSaysIt(Base):
    def view(self, s, auto=AUTO_ON):
        with mock.patch.object(api, 'auto_status', lambda: s), \
                mock.patch.object(server.engine, 'auto_settings', lambda: dict(auto)):
            return server.auto_view()

    def test_late_is_in_the_pages_auto_view(self):
        # The fact, then the one thing to do: the words the notification for the same fact ends with (notify.DOCTOR).
        # Not "Switchboard is starting it again": it replaces such a loop only 12 minutes past its plan, and only
        # while the page runs.
        self.assertEqual(self.view(LATE)['decided'], 'Auto-switch last decided 14 minutes ago. If it stays like this, '
                                                     'run: switchboard doctor')
        self.assertEqual(watch.WATCH_TODO, server.notify.DOCTOR)

    def test_in_time_is_not(self):
        self.assertIsNone(self.view(IN_TIME)['decided'])

    def test_four_starts_in_ten_minutes_are_said_three_are_not(self):
        self.restarted(3)
        self.assertIsNone(self.view(IN_TIME)['restarts'])
        self.restarted(1)
        self.assertEqual(self.view(IN_TIME)['restarts'], 'Switchboard restarted 4 times in the last 10 minutes. If it '
                                                         'stays like this, run: switchboard doctor')
        both = self.view(LATE)                  # both lines at once: the thing to do once, after the last of them
        self.assertEqual((both['decided'], both['restarts']),
                         ('Auto-switch last decided 14 minutes ago.', 'Switchboard restarted 4 times in the last 10 '
                                                                      'minutes. If it stays like this, run: switchboard doctor'))

    def test_a_heartbeat_that_cannot_be_read_never_stops_the_view(self):
        with mock.patch.object(api, 'auto_status', mock.Mock(side_effect=OSError('made up'))), \
                mock.patch.object(server.engine, 'auto_settings', lambda: dict(AUTO_ON)):
            v = server.auto_view()
        self.assertIsNone(v['decided'])

    def test_the_page_draws_both_in_a_function_of_its_own(self):
        with open(os.path.join(HERE, '..', 'switchboard', 'web', 'index.html'), encoding='utf-8') as f:
            page = f.read()
        self.assertIn('return h + watchNotesHTML();', page)
        body = page[page.index('function watchNotesHTML()'):]
        body = body[:body.index('\n}\n')]
        self.assertIn('au.decided && au.enabled && au.on && !au.problem', body)   # a stuck loop has its own line
        self.assertIn('au.restarts', body)


class TheMenuBarSaysIt(unittest.TestCase):
    def test_late_and_restarts_are_lines_under_auto_switch(self):
        auto = dict(AUTO_ON, decided='Auto-switch last decided 14 min ago.',
                    restarts='Switchboard restarted 4 times in the last 10 minutes.')
        self.assertEqual(menubar.watch_lines(auto), [('line', auto['decided']), ('line', auto['restarts'])])

    def test_not_while_a_problem_says_more(self):
        auto = dict(AUTO_ON, on=False, problem='Automatic switching is stuck.', decided='Auto-switch last decided '
                                                                                        '14 min ago.')
        self.assertEqual(menubar.watch_lines(auto), [])

    def test_in_the_menu_after_the_auto_switch_item(self):
        state = {'setup': {'engine': True, 'accounts': True}, 'active': 'ana@one.example',
                 'accounts': [{'email': 'ana@one.example', 'slot': 1, 'usage': {}}],
                 'auto': dict(AUTO_ON, decided='Auto-switch last decided 14 min ago.')}
        items = menubar.menu(state)
        auto_at = [i for i, x in enumerate(items) if x[0] == 'auto'][0]
        self.assertIn(('line', 'Auto-switch last decided 14 min ago.'), items[auto_at + 1:])


class TheTerminalSaysIt(Base):
    def test_under_the_list(self):
        from switchboard import __main__ as cli
        view = {'auto': dict(AUTO_ON, decided='Auto-switch last decided 14 min ago.')}
        self.assertEqual(cli.watch_line(view), 'Auto-switch last decided 14 min ago.')
        self.assertIsNone(cli.watch_line({'auto': AUTO_ON}))
        self.assertIsNone(cli.watch_line({}))

    def test_doctor_says_each_only_when_it_is_so(self):
        from switchboard import __main__ as cli
        lines = []

        def line(good, text, fix=''):
            lines.append((good, text))
        with mock.patch.object(api, 'auto_status', lambda: IN_TIME), \
                mock.patch.object(cli.engine, 'auto_settings', lambda: dict(AUTO_ON)):
            cli._doctor_watch(line)
        self.assertEqual(lines, [])
        self.restarted(4)
        with open(core.data_path(watch.PAGE_WATCH), 'w') as f:
            f.write('{"fired": [%f]}' % (__import__('time').time() - 60))
        with mock.patch.object(api, 'auto_status', lambda: LATE), \
                mock.patch.object(cli.engine, 'auto_settings', lambda: dict(AUTO_ON)):
            cli._doctor_watch(line)
        self.assertEqual(lines, [(False, 'Auto-switch last decided 14 minutes ago'),
                                 (False, 'Switchboard restarted 4 times in the last 10 minutes'),
                                 (None, "The page's own watchdog restarted it 1 time in the last hour")])

    def test_doctor_ends_each_with_the_one_thing_to_do(self):
        # Doctor can't send the person to doctor: it names the step itself. A fresh page ends a loop that decides
        # nothing and starts another (engine.AutoHost._end_stale), page running or not; why the page restarts is in
        # its log.
        from switchboard import __main__ as cli
        lines = []

        def line(good, text, fix=''):
            lines.append((text, fix))
        self.restarted(4)
        with mock.patch.object(api, 'auto_status', lambda: LATE), \
                mock.patch.object(cli.engine, 'auto_settings', lambda: dict(AUTO_ON)):
            cli._doctor_watch(line)
        self.assertEqual(lines[0], ('Auto-switch last decided 14 minutes ago',
                                    'If it stays like this, ' + cli.restart_page_words()[0].lower() +
                                    cli.restart_page_words()[1:]))
        self.assertTrue(lines[1][1].startswith('Why is in its log: '), lines[1])
        self.assertNotIn('open the page to see why', lines[0][1])         # the page does not say why


if __name__ == '__main__':
    unittest.main()
