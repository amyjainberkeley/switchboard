"""Stand-ins for the sign-in watch's tests: a world on a fake clock where changes come at set times and each look
takes a set time, so the watch's pace can be checked step by step without real time passing.

Nothing here touches the Keychain, launchd or a real home folder.
"""
import heapq
import itertools

from switchboard.accounts import signin_watch as sw

from fakes_autoswitch import FakeClock

T0_MONO = 50000.0


class Answer(object):
    """A look's answer, as the engine gives it."""

    def __init__(self, action='nothing', watch=False, next_s=None, seen=None, line=None):
        self.action = action
        self.watch = watch
        if next_s is not None:
            self.next_s = next_s
        if seen is not None:
            self.seen = seen
        if line is not None:
            self.line = line


class Sim(object):
    """Changes at set times (and sleeps of the Mac), and looks that take a set time."""

    def __init__(self, clock=None):
        self.clock = clock or FakeClock(mono=T0_MONO)
        self.queue = []
        self.seq = itertools.count()
        self.jobs = []
        self.changes_at = []          # (mono, hint) of every change handed to the watch

    def change(self, at, **hint):
        heapq.heappush(self.queue, (at, next(self.seq), 'change', dict(sw.no_hint(), **hint)))

    def sleep_at(self, at, seconds):
        heapq.heappush(self.queue, (at, next(self.seq), 'sleep', seconds))

    def next_event(self):
        return self.queue[0][0] if self.queue else float('inf')

    def next_job_end(self, now):
        ends = [j.end for j in self.jobs if j.end > now]
        return min(ends) if ends else float('inf')


class SimChanges(object):
    """Changes as the watch sees them: wait() moves the fake clock to the next change, the end of a look, or the
    timeout, whichever comes first."""

    def __init__(self, sim, mode='events'):
        self.sim = sim
        self.mode = mode
        self.waits = 0
        self.nudges = 0

    def wait(self, timeout):
        self.waits += 1
        c = self.sim.clock
        now = c.mono()
        t = min(now + max(0.0, timeout), self.sim.next_event(), self.sim.next_job_end(now))
        if t > now:
            c.advance(t - now)
        hint = None
        while self.sim.queue and self.sim.queue[0][0] <= c.mono():
            _, _, kind, payload = heapq.heappop(self.sim.queue)
            if kind == 'sleep':
                c.slept(payload)
            else:
                hint = sw.merge(hint, payload)
        if hint is not None:
            self.sim.changes_at.append((c.mono(), hint))
        return hint

    def sweep(self):
        return None

    def view(self, fresh=False):
        return {'kc_size': 4096, 'kc_mtime': 1.5, 'login': False, 'login_size': None, 'login_mtime': None,
                'settings_mtime': 2.5}

    def nudge(self):
        self.nudges += 1


class FakeJob(object):
    def __init__(self, clock, start, end, out):
        self.clock, self.start, self.end, self.out = clock, start, end, out

    def done(self):
        return self.clock.mono() >= self.end

    def outcome(self):
        return self.out


class FakeRunner(object):
    """spawn(fn) for the watch: fn runs at once (its answer is what the look said), and the job counts as running
    until `duration()` fake seconds have passed."""

    def __init__(self, sim, duration=lambda: 0.3):
        self.sim = sim
        self.duration = duration

    def __call__(self, fn):
        start = self.sim.clock.mono()
        d = self.duration()
        try:
            out = (fn(), None)
        except Exception as e:  # noqa: BLE001 - handed back as the look's failure
            out = (None, e)
        job = FakeJob(self.sim.clock, start, start + d, out)
        self.sim.jobs.append(job)
        return job


class Looks(object):
    """A look that notes each call (fake time and hint) and answers from `answer(hint)`."""

    def __init__(self, clock, answer=lambda hint: None):
        self.clock = clock
        self.answer = answer
        self.calls = []

    def __call__(self, hint):
        self.calls.append((self.clock.mono(), dict(hint)))
        return self.answer(hint)


def make(sim=None, answer=lambda hint: None, duration=lambda: 0.3, page=None, mode='events'):
    """(watch, sim, looks, beats, lines, logs) with every outside thing faked."""
    sim = sim or Sim()
    looks = Looks(sim.clock, answer)
    beats, lines, logs = [], [], []
    w = sw.Watch(SimChanges(sim, mode=mode), looks, clock=sim.clock, log=logs.append,
                 record=sw.Record(clock=sim.clock, sink=lines.append), beat_path='/nonexistent/beat.json',
                 write=lambda path, data: beats.append(dict(data)), page=page,
                 spawn=FakeRunner(sim, duration), page_spawn=FakeRunner(sim, lambda: 0.0), pid=4242)
    return w, sim, looks, beats, lines, logs
