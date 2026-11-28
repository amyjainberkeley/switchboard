"""The sign-in watch's rules over thousands of made-up runs from a fixed seed: random changes, answers, look times,
hangs, errors and sleeps of the Mac. After every run:

(a) never two looks at once;
(b) never more than LOOKS_PER_MIN looks start in any 60 s;
(c) every change is followed by a look that started after it, within SETTLE_S + MIN_GAP_S (or CAP_GAP_S while the cap
    holds) of the change or of the end of a look that was running then;
(d) the heartbeat is never older than 2 * BEAT_S of fake time while the loop runs;
(e) no line of the record, the heartbeat or the log holds a string that was fed in as a secret.
"""
import _isolate  # noqa: F401 - first: keeps every write in temp folders

import itertools
import json
import os
import random
import unittest

from switchboard.accounts import signin_watch as sw

from signin_watch_fakes import Answer, FakeRunner, Sim, T0_MONO, make

RUNS = int(os.environ.get('SIGNIN_WATCH_RUNS') or 3000)
SEED = 20261125
SECRETS = ('sk-ant-oat01-' + 'Qx7' * 12, 'person@mail.example', 'eyJhbGciOiJIUzI1NiJ9.e30.abc', 'Secret Chat Title',
           '/Users/someone/projects/private', '0123456789abcdef0123456789abcdef')


class Seq(object):
    """One counter shared by the deliveries of changes and the starts of looks, so 'after' is exact even at one
    fake instant."""

    def __init__(self):
        self.n = itertools.count()

    def __call__(self):
        return next(self.n)


def random_answer(rng):
    secret = rng.choice(SECRETS)
    r = rng.random()
    if r < 0.25:
        return None
    if r < 0.32:
        raise RuntimeError('failed for %s' % secret)
    seen = {'email': secret, 'count': rng.randint(0, 9), 'fp': '89abcdef01234567', 'item': 'absent',
            'deep': {'token': secret, 'ok': True, 'more': {'path': secret}}, secret: 1, 'title': secret}
    action = rng.choice(('nothing', 'put_back', 'watch', 'restored', 'moved', secret, None, 42))
    nxt = rng.choice((None, None, rng.uniform(0.0, 12.0), -1.0, float('nan'), True, 'soon', 1e12))
    watch = rng.random() < 0.35
    if rng.random() < 0.3:
        return {'action': action, 'watch': watch, 'next_s': nxt, 'seen': seen, 'line': 'Put %s back.' % secret}
    return Answer(action, watch=watch, next_s=nxt, seen=seen, line='Put %s back.' % secret)


def random_hint(rng):
    return {'keychain': rng.random() < 0.6, 'settings': rng.random() < 0.2,
            'login_file': rng.choice((None, None, None, 'appeared', 'gone', 'changed'))}


def one_run(rng):
    sim = Sim()
    t0 = T0_MONO
    span = rng.uniform(10.0, 150.0)
    t = t0 + rng.uniform(0.0, 3.0)
    while t < t0 + span:
        if rng.random() < 0.3:                                   # a burst, as a save or a pile-up of saves gives
            for _ in range(rng.randint(2, 60)):
                sim.change(t, **random_hint(rng))
                t += rng.uniform(0.0, 0.05)
        else:
            sim.change(t, **random_hint(rng))
        t += rng.expovariate(1.0 / rng.choice((0.2, 1.0, 5.0, 20.0)))
    if rng.random() < 0.1:
        sim.sleep_at(t0 + rng.uniform(0.0, span), rng.uniform(60.0, 7200.0))

    def duration():
        r = rng.random()
        if r < 0.03:
            return rng.uniform(sw.LOOK_DEADLINE_S, 130.0)          # a hang
        if r < 0.2:
            return rng.uniform(1.0, 5.0)
        return rng.uniform(0.02, 0.8)

    seq = Seq()
    starts = []
    w, sim, looks, beats, lines, logs = make(sim=sim, answer=lambda h: (starts.append(seq()), random_answer(rng))[1])
    w.spawn = FakeRunner(sim, duration)
    delivered = []
    wait = w.changes.wait

    def counted_wait(timeout):
        h = wait(timeout)
        if h is not None:
            delivered.append((sim.clock.mono(), seq()))
        return h
    w.changes.wait = counted_wait
    end = t0 + span + rng.uniform(5.0, 140.0)
    w.run(until=end)
    return sim, looks, beats, lines, logs, delivered, starts, end


class ManyRunsTest(unittest.TestCase):
    def test_the_rules_hold_over_many_made_up_runs(self):
        rng = random.Random(SEED)
        totals = {'runs': 0, 'changes': 0, 'looks': 0, 'hangs': 0, 'capped_runs': 0}
        for run in range(RUNS):
            sim, looks, beats, lines, logs, delivered, seqs, end = one_run(rng)
            where = 'run %d' % run
            jobs = sorted(sim.jobs, key=lambda j: j.start)
            look_starts = [j.start for j in jobs]
            # (a)
            for a, b in zip(jobs, jobs[1:]):
                self.assertGreaterEqual(b.start, a.end - 1e-9, '%s: two looks at once' % where)
            # (b)
            k = 0
            for i, s in enumerate(look_starts):
                while look_starts[k] <= s - 60.0:
                    k += 1
                self.assertLessEqual(i - k + 1, sw.LOOKS_PER_MIN, '%s: over the cap' % where)
            # (c)
            self.assertEqual(len(seqs), len(looks.calls))
            gap = max(sw.SETTLE_S + sw.MIN_GAP_S, sw.CAP_GAP_S)
            for c, cseq in delivered:
                running = [j.end for j in jobs if j.start < c < j.end or (j.start == c and j.end > c and
                                                                          seqs[jobs.index(j)] < cseq)]
                bound = max([c] + running) + gap
                if bound > end:
                    continue
                after = [looks.calls[i][0] for i, s in enumerate(seqs) if s > cseq]
                self.assertTrue(after, '%s: a change at %.3f was never looked at' % (where, c - T0_MONO))
                self.assertLessEqual(after[0], bound + 1e-6, '%s: a change at %.3f waited until %.3f'
                                     % (where, c - T0_MONO, after[0] - T0_MONO))
            # (d)
            marks = [b['beat_mono'] for b in beats]
            self.assertTrue(marks, where)
            for a, b in zip(marks, marks[1:]):
                self.assertLessEqual(b - a, 2 * sw.BEAT_S, '%s: the heartbeat went quiet' % where)
            self.assertLessEqual(end - marks[-1], 2 * sw.BEAT_S, where)
            # (e)
            text = ''.join(lines) + json.dumps(beats) + '\n'.join(logs)
            for s in SECRETS:
                self.assertNotIn(s, text, '%s: a secret reached a file' % where)
            for line in lines:
                json.loads(line)
            totals['runs'] += 1
            totals['changes'] += len(delivered)
            totals['looks'] += len(looks.calls)
            totals['hangs'] += sum(1 for j in jobs if j.end - j.start >= sw.LOOK_DEADLINE_S)
            totals['capped_runs'] += any('until the minute has room' in x for x in logs)
        print('\nmany runs: %s' % totals)
        self.assertGreater(totals['capped_runs'], 0, 'some runs reach the cap')
        self.assertGreater(totals['hangs'], 0, 'some runs have a look that hangs')


if __name__ == '__main__':
    unittest.main()
