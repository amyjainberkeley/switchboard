"""The sign-in watch's second call, the hold (api.watch_hold): made at once, in the watch's own loop, for each change of
Claude Code's plain login file and of its settings, before the look for that change is scheduled, whatever look is
running and outside the look cap; once at the start; a login file that goes is not handed on once HOLD_PUTS_PER_MIN
files were put back in a minute, and every other change always is; a hold that raises or is slow is said and never
stops the watch; a file the hold put there itself is no change for the look. On a fake clock with made-up changes, a
made-up look and a made-up hold."""
import _isolate  # noqa: F401 - first: keeps every write in temp folders

import json
import types
import unittest

from switchboard.accounts import signin_watch as sw

from signin_watch_fakes import Answer, T0_MONO, make

T = T0_MONO
TOKEN = 'sk-ant-ort01-' + 'Qx7' * 12


class Holds(object):
    """A made-up hold: notes (fake time, event) of each call, in one list with the looks' starts."""

    def __init__(self, clock, order, answer=lambda event: {'did': None}):
        self.clock, self.order, self.answer = clock, order, answer
        self.calls = []
        self.looked = []            # the times of the call made after each look, kept apart from the changes'

    def __call__(self, event):
        if event == 'looked':
            self.looked.append(self.clock.mono())
            return self.answer(event)
        self.calls.append((self.clock.mono(), event))
        self.order.append(('hold', event, self.clock.mono()))
        return self.answer(event)


def watch(answer=lambda event: {'did': None}, duration=lambda: 0.3, look_answer=lambda hint: None):
    order = []
    w, sim, looks, beats, lines, logs = make(answer=look_answer, duration=duration)
    real = w.look

    def look(hint):
        order.append(('look', dict(hint), sim.clock.mono()))
        return real(hint)
    w.look = look
    w.hold = Holds(sim.clock, order, answer)
    return w, sim, looks, lines, logs, order


class TheHoldCall(unittest.TestCase):
    def test_it_comes_at_the_change_and_before_the_look_for_it(self):
        w, sim, looks, lines, logs, order = watch()
        sim.change(T + 5.0, login_file='appeared')
        sim.change(T + 9.0, login_file='gone')
        w.run(until=T + 20.0)
        self.assertEqual(w.hold.calls, [(T, 'start'), (T + 5.0, 'appeared'), (T + 9.0, 'gone')])
        names = [(o[0], o[1] if o[0] == 'hold' else o[1].get('login_file')) for o in order]
        self.assertEqual(names, [('hold', 'start'), ('look', None), ('hold', 'appeared'), ('look', 'appeared'),
                                 ('hold', 'gone'), ('look', 'gone')])

    def test_settings_first_then_the_file_when_both_changed(self):
        w, sim, looks, lines, logs, order = watch()
        sim.change(T + 5.0, settings=True, login_file='gone')
        w.run(until=T + 10.0)
        self.assertEqual([e for _, e in w.hold.calls], ['start', 'settings', 'gone'])

    def test_a_keychain_change_alone_is_not_handed_to_it(self):
        w, sim, looks, lines, logs, order = watch()
        sim.change(T + 5.0, keychain=True)
        w.run(until=T + 10.0)
        self.assertEqual([e for _, e in w.hold.calls], ['start'])
        self.assertEqual(len(looks.calls), 2)

    def test_while_a_look_hangs_it_is_still_called_at_the_change(self):
        times = iter([0.3, 100.0] + [0.3] * 50)
        w, sim, looks, lines, logs, order = watch(duration=lambda: next(times))
        sim.change(T + 2.0, keychain=True)                    # the look that hangs
        sim.change(T + 10.0, login_file='gone')
        sim.change(T + 30.0, login_file='appeared')
        w.run(until=T + 60.0)
        self.assertIn((T + 10.0, 'gone'), w.hold.calls)
        self.assertIn((T + 30.0, 'appeared'), w.hold.calls)
        self.assertEqual(len(looks.calls), 2)                 # no look while the one before runs

    def test_under_the_look_cap_every_change_still_reaches_it_at_once(self):
        w, sim, looks, lines, logs, order = watch(duration=lambda: 0.1)
        for i in range(50):
            sim.change(T + 1.0 + i * 1.0, login_file='changed' if i % 2 else 'gone')
        w.run(until=T + 60.0)
        made = [e for e in w.hold.calls if e[1] != 'start']
        self.assertEqual(len(made), 50)
        self.assertEqual([t for t, _ in made], [T + 1.0 + i * 1.0 for i in range(50)])
        self.assertLessEqual(len(looks.calls), sw.LOOKS_PER_MIN)
        self.assertTrue(any('looks in the last minute' in x for x in logs))       # the look cap held looks back

    def test_files_put_back_have_a_cap_of_their_own_said_once(self):
        w, sim, looks, lines, logs, order = watch(answer=lambda event: {'did': 'held' if event == 'gone' else None})
        for i in range(70):
            sim.change(T + 1.0 + i * 0.5, login_file='gone')  # 70 in 35 s, each answered with a file put back
        sim.change(T + 40.0, settings=True)                   # under the cap these are still handed on
        sim.change(T + 41.0, login_file='changed')
        sim.change(T + 100.0, login_file='gone')
        w.run(until=T + 120.0)
        gone = [t for t, e in w.hold.calls if e == 'gone']
        self.assertEqual([t for t in gone if t < T + 61.0], [T + 1.0 + i * 0.5 for i in range(sw.HOLD_PUTS_PER_MIN)])
        self.assertIn((T + 40.0, 'settings'), w.hold.calls)
        self.assertIn((T + 41.0, 'changed'), w.hold.calls)
        self.assertIn((T + 100.0, 'gone'), w.hold.calls)      # the minute had room again
        self.assertEqual(len([x for x in logs if 'times in the last minute' in x]), 1)
        capped = [json.loads(x) for x in lines if '"did":"cap"' in x]
        self.assertEqual([(x['kind'], x['event'], x['made']) for x in capped], [('hold', 'gone', 60)])
        self.assertEqual(sw.HOLD_PUTS_PER_MIN, 60)

    def test_a_file_put_in_place_and_taken_away_at_once_counts_as_put_back(self):
        w, sim, looks, lines, logs, order = watch(answer=lambda event: {'did': 'removed' if event == 'gone' else None})
        for i in range(70):
            sim.change(T + 1.0 + i * 0.5, login_file='gone')
        w.run(until=T + 50.0)
        self.assertEqual(len([t for t, e in w.hold.calls if e == 'gone']), sw.HOLD_PUTS_PER_MIN)

    def test_changes_that_make_no_file_are_never_held_back(self):
        # Claude Code rewrites its settings file dozens of times a minute while chats work, and a pile-up of saves
        # writes and removes its plain login file many times: none of that may use up what a later removal needs
        w, sim, looks, lines, logs, order = watch()
        for i in range(200):
            sim.change(T + 1.0 + i * 0.25, settings=True, login_file=('changed', 'gone', None, None)[i % 4])
        sim.change(T + 55.0, login_file='gone')
        w.run(until=T + 60.0)
        events = [e for _, e in w.hold.calls]
        self.assertEqual(events.count('settings'), 200)
        self.assertEqual(events.count('changed'), 50)
        self.assertEqual(events.count('gone'), 51)            # none of them answered with a file put back
        self.assertIn((T + 55.0, 'gone'), w.hold.calls)
        self.assertEqual([x for x in logs if 'times in the last minute' in x], [])

    def test_an_answer_that_asks_for_a_look_gets_one_then(self):
        # a file the hold put back is taken away by a look once it is old enough: the watch has one made at that time
        w, sim, looks, lines, logs, order = watch(
            answer=lambda event: {'did': 'held', 'look_in': 3.2} if event == 'gone' else {'did': None})
        sim.change(T + 10.0, login_file='gone')
        w.run(until=T + 40.0)
        starts = [t for kind, _, t in order if kind == 'look' and t > T + 1.0]
        self.assertEqual(len(starts), 2, starts)              # the look for the change, and the one asked for
        self.assertLess(starts[0], T + 10.5)
        self.assertAlmostEqual(starts[1], T + 13.2, delta=0.05)

    def test_after_each_look_that_ended_it_is_asked_once(self):
        w, sim, looks, lines, logs, order = watch()
        sim.change(T + 5.0, keychain=True)
        sim.change(T + 9.0, login_file='gone')
        w.run(until=T + 20.0)
        self.assertEqual(len(w.hold.looked), 3)               # the start's look and one for each change
        ends = sorted(t for kind, _, t in order if kind == 'look')
        for asked, started in zip(sorted(w.hold.looked), ends):
            self.assertGreater(asked, started)                # after the look, not before it

    def test_what_it_answers_after_a_look_can_ask_for_the_next_one(self):
        # a look left a plain login file for a later look to take away: the hold says when that look is due
        asked = []

        def answer(event):
            if event == 'looked' and not asked:
                asked.append(event)
                return {'did': None, 'ours': True, 'look_in': 10.2}
            return {'did': None}
        w, sim, looks, lines, logs, order = watch(answer=answer)
        w.run(until=T + 40.0)
        starts = [t for kind, _, t in order if kind == 'look']
        self.assertEqual(len(starts), 2, starts)              # the start's look, and the one asked for after it
        self.assertAlmostEqual(starts[1], w.hold.looked[0] + 10.2, delta=0.05)

    def test_a_look_that_failed_is_not_followed_by_that_call(self):
        def boom(hint):
            raise RuntimeError('made-up')
        w, sim, looks, lines, logs, order = watch(look_answer=boom)
        sim.change(T + 5.0, keychain=True)
        w.run(until=T + 10.0)
        self.assertEqual(w.hold.looked, [])

    def test_the_later_of_two_times_asked_for_counts(self):
        w, sim, looks, lines, logs, order = watch(
            answer=lambda event: {'did': 'held', 'look_in': 3.2} if event == 'gone' else {'did': None})
        sim.change(T + 10.0, login_file='gone')
        sim.change(T + 12.0, login_file='gone')               # put back again: old enough later
        w.run(until=T + 40.0)
        starts = [t for kind, _, t in order if kind == 'look' and t > T + 12.5]
        self.assertEqual(len(starts), 1, starts)
        self.assertAlmostEqual(starts[0], T + 15.2, delta=0.05)

    def test_an_answer_that_asks_for_a_sooner_look_does_not_take_the_later_one_away(self):
        # a file is in place and old enough at +10 s; bytes kept a moment later ask for a look at +3 s only
        asks = {'gone': 10.2, 'changed': 3.2}
        w, sim, looks, lines, logs, order = watch(
            answer=lambda event: {'did': 'held', 'look_in': asks[event]} if event in asks else {'did': None})
        sim.change(T + 10.0, login_file='gone')
        sim.change(T + 11.0, login_file='changed')
        w.run(until=T + 40.0)
        starts = [t for kind, _, t in order if kind == 'look' and t > T + 11.5]
        self.assertEqual(len(starts), 1, starts)
        self.assertAlmostEqual(starts[0], T + 20.2, delta=0.05)

    def test_the_look_that_was_asked_for_is_asked_again_when_it_fails(self):
        def look_answer(hint):
            if looks_at and looks_at[-1] > T + 13.0 and len([t for t in looks_at if t > T + 13.0]) == 1:
                raise RuntimeError('made-up')
            return None
        looks_at = []
        w, sim, looks, lines, logs, order = watch(
            answer=lambda event: {'did': 'held', 'look_in': 3.2} if event == 'gone' else {'did': None},
            look_answer=look_answer)
        real = w.look

        def look(hint):
            looks_at.append(sim.clock.mono())
            return real(hint)
        w.look = look
        sim.change(T + 10.0, login_file='gone')
        w.run(until=T + 40.0)
        late = [t for t in looks_at if t > T + 13.0]
        self.assertEqual(len(late), 2, late)                  # the one that failed, and the one after it
        self.assertAlmostEqual(late[0], T + 13.2, delta=0.05)
        self.assertLess(late[1], T + 13.2 + 0.3 + sw.WATCH_NEXT_S + 0.2)

    def test_an_answer_without_it_or_with_a_wrong_one_asks_for_no_look(self):
        for bad in (None, True, 'soon', -1, [3]):
            w, sim, looks, lines, logs, order = watch(answer=lambda event, bad=bad: {'did': 'held', 'look_in': bad})
            sim.change(T + 10.0, login_file='gone')
            w.run(until=T + 40.0)
            starts = [t for kind, _, t in order if kind == 'look' and t > T + 1.0]
            self.assertEqual(len(starts), 1, (bad, starts))

    def test_a_hold_that_raises_is_said_once_and_the_watch_goes_on(self):
        def boom(event):
            raise KeyError(TOKEN)
        w, sim, looks, lines, logs, order = watch(answer=boom)
        for i in range(30):
            sim.change(T + 1.0 + i * 3.0, login_file='gone')
        w.run(until=T + 120.0)
        self.assertEqual(len(w.hold.calls), 31)
        self.assertEqual([x for x in logs if 'failed' in x],
                         ["the sign-in watch: its step for Claude Code's plain login file failed (KeyError)"])
        self.assertGreaterEqual(len(looks.calls), 30)         # every change was still looked at
        self.assertNotIn(TOKEN, ''.join(lines) + ''.join(logs))

    def test_a_raising_hold_is_said_again_after_ten_minutes(self):
        w, sim, looks, lines, logs, order = watch(answer=lambda e: 1 / 0)
        sim.change(T + 5.0, login_file='gone')
        sim.change(T + 5.0 + sw.ERROR_LOG_EVERY_S + 1.0, login_file='gone')
        w.run(until=T + sw.ERROR_LOG_EVERY_S + 20.0)
        self.assertEqual(len([x for x in logs if 'plain login file failed (ZeroDivisionError)' in x]), 2)

    def test_a_slow_hold_is_said_once(self):
        clock = []

        def slow(event):
            clock[0].advance(sw.HOLD_SLOW_S + 0.05)
            return {'did': None}
        w, sim, looks, lines, logs, order = watch(answer=slow)
        clock.append(sim.clock)
        for i in range(5):
            sim.change(T + 1.0 + i * 2.0, login_file='gone')
        w.run(until=T + 30.0)
        said = [x for x in logs if "its step for Claude Code's plain login file took" in x]
        self.assertEqual(len(said), 1, said)

    def test_a_call_that_did_something_makes_one_record_line_with_plain_facts_only(self):
        answers = {'gone': {'did': 'held', 'made': 2, 'failed': 0, 'why': TOKEN},
                   'settings': {'did': 'removed', 'why': 'nobody', 'made': 0, 'failed': 0}}
        w, sim, looks, lines, logs, order = watch(answer=lambda e: answers.get(e, {'did': None}))
        sim.change(T + 5.0, login_file='gone')
        sim.change(T + 9.0, settings=True)
        sim.change(T + 12.0, login_file='appeared')
        w.run(until=T + 20.0)
        recs = [json.loads(x) for x in lines]
        held = [r for r in recs if r['kind'] == 'hold']
        self.assertEqual([(r['event'], r['did'], r.get('why'), r.get('made')) for r in held],
                         [('gone', 'held', None, 2), ('settings', 'removed', 'nobody', 0)])
        self.assertNotIn(TOKEN, ''.join(lines))

    def test_the_holds_own_file_is_no_change_for_the_look(self):
        w, sim, looks, lines, logs, order = watch(answer=lambda e: {'did': None, 'ours': e == 'appeared'})
        sim.change(T + 5.0, login_file='appeared')            # the file the hold put there
        sim.change(T + 9.0, keychain=True, login_file='appeared')
        w.run(until=T + 20.0)
        hints = [h for _, h in looks.calls]
        self.assertEqual(len(hints), 2, hints)                # the start, then the keychain change only
        self.assertEqual(hints[1], dict(sw.no_hint(), keychain=True))
        self.assertEqual([json.loads(x)['hint']['login_file'] for x in lines if '"kind":"change"' in x], [None])

    def test_a_write_of_claude_codes_is_still_a_change(self):
        w, sim, looks, lines, logs, order = watch(answer=lambda e: {'did': 'kept'})
        sim.change(T + 5.0, login_file='appeared')
        w.run(until=T + 10.0)
        self.assertEqual(looks.calls[1][1]['login_file'], 'appeared')
        self.assertTrue(looks.calls[1][1]['login_file_written'])

    def test_without_a_hold_the_watch_is_as_before(self):
        w, sim, looks, beats, lines, logs = make()
        self.assertIsNone(w.hold)
        sim.change(T + 5.0, login_file='appeared')
        w.run(until=T + 10.0)
        self.assertEqual(looks.calls[1][1]['login_file'], 'appeared')

    def test_a_wake_hands_its_changes_to_the_hold(self):
        w, sim, looks, lines, logs, order = watch()
        w.changes.sweep = lambda: dict(sw.no_hint(), login_file='gone', login_file_written=False)
        sim.sleep_at(T + 50.0, 3600.0)
        w.run(until=T + 60.0)
        self.assertIn('gone', [e for _, e in w.hold.calls])


class TheDefaultHold(unittest.TestCase):
    def test_the_engines_watch_hold_when_it_has_one(self):
        def fn(event):
            return {'did': None}
        self.assertIs(sw.default_hold(types.SimpleNamespace(watch_hold=fn)), fn)

    def test_none_without_one(self):
        self.assertIsNone(sw.default_hold(types.SimpleNamespace()))
        self.assertIsNone(sw.default_hold(types.SimpleNamespace(watch_hold='not callable')))

    def test_this_engine_has_one(self):
        from switchboard.accounts import api
        self.assertIs(sw.default_hold(), api.watch_hold)

    def test_a_look_answer_word_hold_is_kept_in_the_record(self):
        self.assertEqual(sw.clean({'kind': 'hold', 'did': 'held', 'why': 'nobody', 'event': 'gone'}),
                         {'kind': 'hold', 'did': 'held', 'why': 'nobody', 'event': 'gone'})
        self.assertEqual(sw.clean({'why': 'another', 'did': 'stays'}), {'why': 'another', 'did': 'stays'})
        self.assertEqual(sw.word(Answer('hold').action), 'hold')


if __name__ == '__main__':
    unittest.main()
