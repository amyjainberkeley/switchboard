"""The sign-in watch's pace, on a fake clock with made-up changes and a made-up look: when it looks, and how often."""
import _isolate  # noqa: F401 - first: keeps every write in temp folders

import json
import sys
import unittest
from unittest import mock

from switchboard.accounts import signin_watch as sw

from signin_watch_fakes import Answer, T0_MONO, make

T = T0_MONO


def starts(looks, after=None):
    return [t for t, _ in looks.calls if after is None or t > after]


def most_in_a_minute(times):
    best, j = 0, 0
    times = sorted(times)
    for i, t in enumerate(times):
        while times[j] <= t - 60.0:
            j += 1
        best = max(best, i - j + 1)
    return best


class PaceTest(unittest.TestCase):
    def test_the_first_look_comes_at_once(self):
        w, sim, looks, beats, lines, logs = make()
        w.run(until=T + 2.0)
        self.assertEqual(looks.calls[0], (T, dict(sw.no_hint(), tick=True)))
        self.assertEqual(len(looks.calls), 1)
        self.assertTrue(beats and beats[0]['pid'] == 4242)

    def test_200_changes_in_3_seconds_give_few_looks_and_one_after_the_last(self):
        w, sim, looks, beats, lines, logs = make()
        for i in range(200):
            sim.change(T + 0.5 + i * 0.015, keychain=True)
        last = T + 0.5 + 199 * 0.015
        w.run(until=T + 30.0)
        made = starts(looks, after=T)
        self.assertLessEqual(len(made), sw.LOOKS_PER_MIN)
        self.assertLessEqual(len(made), 6)
        self.assertGreater(made[-1], last, 'a look after the last change')
        self.assertLessEqual(made[-1], last + sw.SETTLE_S + sw.MIN_GAP_S + 1e-6)
        self.assertTrue(looks.calls[-1][1]['keychain'])

    def test_a_slow_look_while_30_changes_arrive_is_followed_by_exactly_one(self):
        times = iter([0.3, 2.0] + [0.3] * 50)
        w, sim, looks, beats, lines, logs = make(duration=lambda: next(times))
        sim.change(T + 1.0, keychain=True)
        for i in range(30):
            sim.change(T + 1.3 + i * 0.05, settings=True)
        w.run(until=T + 60.0)
        made = starts(looks, after=T)
        self.assertEqual(len(made), 2, made)
        slow_end = made[0] + 2.0
        self.assertGreaterEqual(made[1], slow_end)
        self.assertLessEqual(made[1], slow_end + 1e-6, 'right after the slow one ends (it began over 1 s before)')
        self.assertTrue(looks.calls[-1][1]['settings'])

    def test_a_login_file_that_appeared_is_looked_at_before_a_keychain_change_that_came_first(self):
        w, sim, looks, beats, lines, logs = make()
        sim.change(T + 5.0, keychain=True)
        sim.change(T + 5.05, login_file='appeared')
        sim.change(T + 5.40, login_file='appeared')
        w.run(until=T + 20.0)
        made = looks.calls[1:]
        self.assertAlmostEqual(made[0][0], T + 5.05, places=6)
        self.assertEqual(made[0][1], {'keychain': True, 'login_file': 'appeared', 'settings': False, 'tick': False,
                                      'login_file_written': True})
        self.assertAlmostEqual(made[1][0], T + 5.40, places=6, msg='ahead of the quiet pace (not at 6.05)')

    def test_appeared_beats_changed_and_the_later_of_appeared_and_gone_wins(self):
        self.assertEqual(sw.merge({'login_file': 'appeared'}, {'login_file': 'changed'})['login_file'], 'appeared')
        self.assertEqual(sw.merge({'login_file': 'changed'}, {'login_file': 'gone'})['login_file'], 'gone')
        self.assertEqual(sw.merge({'login_file': 'appeared'}, {'login_file': 'gone'})['login_file'], 'gone')
        self.assertEqual(sw.merge({'login_file': 'gone'}, {'login_file': 'appeared'})['login_file'], 'appeared')
        self.assertEqual(sw.merge({'login_file': 'gone'}, {'login_file': None})['login_file'], 'gone')

    def test_watch_answers_give_the_fast_pace_and_it_ends_after_its_time(self):
        w, sim, looks, beats, lines, logs = make(answer=lambda h: Answer('watch', watch=True, next_s=4.0))
        w.run(until=T + 400.0)
        made = starts(looks)
        fast = [t for t in made if t < T + sw.FAST_FOR_S + 5.0]
        self.assertGreaterEqual(len(fast), 25)
        gaps = [b - a for a, b in zip(fast, fast[1:])]
        self.assertTrue(all(abs(g - 4.3) < 1e-6 for g in gaps), gaps)
        self.assertEqual([t for t in made if T + sw.FAST_FOR_S + 5.0 < t < T + 300.0], [], 'then the quiet pace')
        self.assertEqual(len([x for x in logs if 'back to the quiet pace' in x]), 1)

    def test_a_watch_answer_without_next_s_and_a_short_next_s(self):
        answers = iter([Answer('put_back', watch=True), Answer('watch', next_s=0.01), Answer('nothing')])
        w, sim, looks, beats, lines, logs = make(answer=lambda h: next(answers, None))
        w.run(until=T + 30.0)
        made = starts(looks)
        self.assertEqual(len(made), 3)
        self.assertAlmostEqual(made[1] - made[0], 0.3 + sw.WATCH_NEXT_S)
        self.assertAlmostEqual(made[2] - made[1], 0.3 + sw.FAST_GAP_S, msg='never under FAST_GAP_S')
        self.assertEqual(looks.calls[1][1], sw.no_hint(), 'a look the pace asked for carries no change')

    def test_an_answer_without_watch_ends_the_fast_pace(self):
        answers = iter([Answer('put_back', watch=True, next_s=2.0), None])
        w, sim, looks, beats, lines, logs = make(answer=lambda h: next(answers, None))
        w.run(until=T + 200.0)
        self.assertEqual(len(starts(looks)), 2)

    def test_with_no_change_one_look_every_tick(self):
        w, sim, looks, beats, lines, logs = make()
        w.run(until=T + 1000.0)
        self.assertEqual(starts(looks), [T, T + 300.0, T + 600.0, T + 900.0])
        self.assertTrue(all(h == dict(sw.no_hint(), tick=True) for _, h in looks.calls))

    def test_one_look_right_after_the_mac_wakes(self):
        w, sim, looks, beats, lines, logs = make()
        sim.sleep_at(T + 50.0, 3600.0)
        w.run(until=T + 100.0)
        made = starts(looks, after=T)
        self.assertEqual(len(made), 1)
        self.assertLessEqual(made[0] - (T + 50.0), sw.MAX_WAIT_S + 1e-6)
        self.assertTrue(looks.calls[1][1]['tick'])
        self.assertTrue(any('sleep' in x for x in logs))

    def test_the_cap_holds_and_says_so_once(self):
        w, sim, looks, beats, lines, logs = make(duration=lambda: 0.1)
        for i in range(240):
            sim.change(T + 1.0 + i * 0.5, keychain=True)
        w.run(until=T + 300.0)
        made = starts(looks)
        self.assertLessEqual(most_in_a_minute(made), sw.LOOKS_PER_MIN)
        capped = [x for x in logs if 'until the minute has room' in x]
        self.assertEqual(len(capped), 1, capped)
        self.assertEqual(len([x for x in lines if '"kind":"cap"' in x]), 1)
        for c, _ in sim.changes_at:
            after = [t for t in made if t >= c]
            self.assertTrue(after and after[0] - c <= sw.CAP_GAP_S + 1e-6, c - T)
        self.assertTrue(any(b['problem'] and 'one look every' in b['problem'] for b in beats))
        self.assertIsNone(beats[-1]['problem'], 'the cap lifts once the minute has room')

    def test_a_look_that_hangs_past_its_deadline(self):
        times = iter([0.3, 100.0] + [0.3] * 50)
        w, sim, looks, beats, lines, logs = make(duration=lambda: next(times))
        for i in range(60):
            sim.change(T + 1.0 + i * 1.5, keychain=True)
        w.run(until=T + 200.0)
        made = starts(looks)
        hung = made[1]
        self.assertEqual([t for t in made if hung < t < hung + 100.0], [], 'no look while one runs')
        self.assertTrue(any(t >= hung + 100.0 for t in made), 'one after it ends')
        self.assertEqual(len([x for x in logs if 'more than 45 s' in x]), 1)
        during = [b for b in beats if hung + sw.LOOK_DEADLINE_S < b['beat_mono'] < hung + 100.0]
        self.assertGreaterEqual(len(during), 4, 'it goes on beating')
        self.assertTrue(all('more than 45 s' in b['problem'] for b in during))
        bm = [b['beat_mono'] for b in beats]
        self.assertLessEqual(max(b - a for a, b in zip(bm, bm[1:])), sw.BEAT_S + sw.MAX_WAIT_S + 1e-6)

    def test_a_look_that_never_ends_stops_the_watch_after_300_s_and_says_why_once(self):
        with mock.patch.object(sw, 'STOP_WAIT_S', 0.05):        # (the stop's own wait is in real time)
            w, sim, looks, beats, lines, logs = make(duration=lambda: 10 ** 9)
            for i in range(200):
                sim.change(T + 1.0 + i * 5.0, keychain=True)
            w.run(until=T + 1000.0)
        self.assertTrue(w.stop.is_set(), 'the watch stopped by itself')
        self.assertEqual(len(looks.calls), 1, 'no look while the one that hangs runs')
        stopped_m = beats[-1]['beat_mono']
        self.assertGreaterEqual(stopped_m, T + sw.LOOK_GIVE_UP_S)
        self.assertLessEqual(stopped_m, T + sw.LOOK_GIVE_UP_S + sw.MAX_WAIT_S + 1e-6)
        self.assertIsNotNone(beats[-1].get('stopped_at'), 'its last beat says it stopped')
        said = [x for x in logs if 'more than %d s' % sw.LOOK_GIVE_UP_S in x]
        self.assertEqual(len(said), 1, logs)
        self.assertIn('launchd starts a fresh watch', said[0])
        during = [b for b in beats if T + sw.LOOK_DEADLINE_S < b['beat_mono'] < T + sw.LOOK_GIVE_UP_S]
        self.assertGreaterEqual(len(during), 20, 'it goes on beating until it gives up')
        self.assertTrue(all('more than 45 s' in b['problem'] for b in during))
        self.assertEqual(json.loads(lines[-1])['kind'], 'stop')

    def test_a_look_that_ends_at_299_s_does_not_stop_the_watch(self):
        times = iter([299.0] + [0.3] * 200)
        w, sim, looks, beats, lines, logs = make(duration=lambda: next(times))
        for i in range(80):
            sim.change(T + 1.0 + i * 5.0, keychain=True)
        w.run(until=T + 500.0)
        self.assertFalse(w.stop.is_set())
        self.assertEqual([x for x in logs if 'more than %d s' % sw.LOOK_GIVE_UP_S in x], [])
        self.assertTrue(any(t >= T + 299.0 for t in starts(looks)), 'looks go on after it ends')
        self.assertGreaterEqual(beats[-1]['beat_mono'], T + 500.0 - 1e-6, 'it ran to the end of the test')
        self.assertIsNone(beats[-1]['problem'])

    def test_a_look_that_raises_a_thousand_times(self):
        def boom(hint):
            raise ValueError('the engine fell over')
        w, sim, looks, beats, lines, logs = make(answer=boom, duration=lambda: 0.05)
        for i in range(3000):
            sim.change(T + 1.0 + i * 1.0, keychain=True)
        w.run(until=T + 3100.0)
        self.assertGreaterEqual(len(looks.calls), 1000)
        failed = [x for x in logs if 'a look failed (ValueError)' in x]
        self.assertLessEqual(len(failed), 3100 // sw.ERROR_LOG_EVERY_S + 1)
        self.assertGreaterEqual(len(failed), 2)
        self.assertEqual(len([x for x in lines if '"action":"error"' in x]), len(failed))
        self.assertEqual(beats[-1]['looks'], len(looks.calls))
        self.assertNotIn('fell over', ''.join(logs + lines))

    def test_the_heartbeat_and_a_look_that_did_something(self):
        seen = {'item': 'absent', 'fp': '0123456789abcdef', 'email': 'person@mail.example',
                'token': 'sk-ant-oat01-' + 'A' * 40}
        w, sim, looks, beats, lines, logs = make(answer=lambda h: Answer('put_back', seen=seen,
                                                                         line='Put person@mail.example back.'))
        sim.change(T + 10.0, login_file='appeared', keychain=True)
        w.run(until=T + 30.0)
        hb = beats[-1]
        for key in ('pid', 'started_at', 'beat_at', 'mode', 'looks', 'last_look_at', 'last_change_at',
                    'last_action', 'last_action_at', 'problem'):
            self.assertIn(key, hb)
        self.assertEqual((hb['mode'], hb['looks'], hb['last_action']), ('events', 2, 'put_back'))
        rec = [json.loads(x) for x in lines]
        self.assertEqual([r['kind'] for r in rec], ['start', 'look', 'change', 'look', 'stop'])
        look = rec[-2]
        self.assertEqual(look['seen'], {'item': 'absent', 'fp': '0123456789abcdef'})
        self.assertEqual(look['hint']['login_file'], 'appeared')
        self.assertIn('before', look)
        self.assertIn('after', look)
        text = ''.join(lines) + json.dumps(beats) + ''.join(logs)
        self.assertNotIn('person@mail.example', text)
        self.assertNotIn('sk-ant', text)

    def test_an_unknown_action_word_is_kept_as_other(self):
        w, sim, looks, beats, lines, logs = make(answer=lambda h: Answer('sent person@mail.example'))
        w.run(until=T + 2.0)
        self.assertEqual(beats[-1]['last_action'], 'other')
        self.assertIn('"action":"other"', lines[-2])
        w, sim, looks, beats, lines, logs = make(answer=lambda h: {'action': ['not', 'a', 'word'], 'seen': [1]})
        w.run(until=T + 2.0)
        self.assertEqual(beats[-1]['last_action'], 'other')
        self.assertEqual([x for x in logs if 'failed' in x], [])


class QuietHourTest(unittest.TestCase):
    def test_an_hour_of_quiet(self):
        """An hour with no change: 12 looks (the first, then one every 5 minutes), no program, no file read, and no
        more than one wake-up a second."""
        programs, reads = [], []
        state = {'on': False}

        def hook(event, args):
            if not state['on']:
                return
            if event in ('subprocess.Popen', 'os.posix_spawn', 'os.system') or event.startswith('os.exec'):
                programs.append(event)
            if event == 'open':
                reads.append(args[0])
        sys.addaudithook(hook)
        page = sw.PageCheck(7949, '/nonexistent/io.github.amyjainberkeley.switchboard.plist', launchctl=lambda *a: 0,
                            connect=lambda port, timeout: True, exists=lambda p: True)
        w, sim, looks, beats, lines, logs = make(page=page)
        page.clock = sim.clock
        page.started_m = sim.clock.mono()
        state['on'] = True
        try:
            w.run(until=T + 3600.0)
        finally:
            state['on'] = False
        self.assertEqual(len(looks.calls), 12)
        self.assertEqual(programs, [])
        self.assertEqual(reads, [])
        self.assertLessEqual(w.changes.waits, 3600 + 2 * len(looks.calls))
        self.assertLessEqual(len(beats), 3600 // sw.BEAT_S + 2 * len(looks.calls) + 2)
        print('\nquiet hour: looks %d, programs started %d, files opened %d, wake-ups %d, heartbeats %d'
              % (len(looks.calls), len(programs), len(reads), w.changes.waits, len(beats)))


class TheWordsAPersonReads(unittest.TestCase):
    """The heartbeat's problem reaches doctor and the page as it is: plain words that say when nothing needs doing, or
    what happens next, and pass the page's filter (server._plain). The watch's log, where doctor sends a person, names
    it as a person sees it."""

    def problems(self, beats):
        return [b['problem'] for b in beats if b.get('problem')]

    def check(self, said, want):
        from switchboard import server
        self.assertTrue(said, 'a problem was said')
        for p in said:
            self.assertEqual(p, want)
            self.assertEqual(server._plain(p), p)
            self.assertNotIn('kqueue', p)

    def test_compared_once_a_second(self):
        w, sim, looks, beats, lines, logs = make(mode='polling')
        w.run(until=T + 30.0)
        self.check(self.problems(beats), 'the watch cannot be told of changes here, so it compares the files once a '
                                         'second; nothing to do')

    def test_many_changes(self):
        w, sim, looks, beats, lines, logs = make(duration=lambda: 0.1)
        for i in range(240):
            sim.change(T + 1.0 + i * 0.5, keychain=True)
        w.run(until=T + 300.0)
        self.check(self.problems(beats), 'many changes: one look every 3 s for now; nothing to do')

    def test_a_long_look(self):
        times = iter([0.3, 100.0] + [0.3] * 50)
        w, sim, looks, beats, lines, logs = make(duration=lambda: next(times))
        sim.change(T + 1.0, keychain=True)
        w.run(until=T + 200.0)
        self.check(self.problems(beats), 'a look has run for more than 45 s; if it runs for 300 s, the watch starts '
                                         'again')

    def test_the_log_names_the_sign_in_watch(self):
        w, sim, looks, beats, lines, logs = make(mode='polling', answer=lambda hint: Answer('put_back'))
        sim.sleep_at(T + 50.0, 3600.0)
        w.run(until=T + 100.0)
        self.assertTrue(logs)
        for x in logs:
            self.assertTrue(x.startswith('the sign-in watch: '), x)
        with open(sw.__file__, encoding='utf-8') as f:
            self.assertNotIn('signin watch:', f.read())


if __name__ == '__main__':
    unittest.main()
