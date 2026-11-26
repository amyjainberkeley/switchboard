"""Keeps the Mac awake while Claude Code works, and lets it sleep again two minutes after.

Nobody has to type caffeinate. While Claude Code is working, Switchboard holds one
`caffeinate -i -s -w <its own pid>`; two minutes after the work stops, it lets go.

What macOS allows, plainly (nothing here can change these):
- On battery with the lid shut, the Mac sleeps anyway. No sleep assertion stops that.
- `-s` only counts on AC power. `man caffeinate`: "-s  Create an assertion to prevent the system
  from sleeping. This assertion is valid only when system is running on AC power." On battery
  only `-i` (no idle sleep) holds, and only with the lid open.
- Nothing here needs admin rights or changes a pmset setting. `-w <pid>` ends the hold by itself
  if Switchboard stops or crashes, so a hold can never outlive it.

How "working" is decided, with no setting to change:
Claude Code writes every chat to a transcript file under ~/.claude/projects as it goes (the hooks
docs show `transcript_path` there and say "The transcript file is written asynchronously"), and
each subagent and workflow agent writes its own file under the session's folder.
- Working when any transcript was written in the last two minutes (FRESH).
- Also working while a turn is still open: the last request in a transcript is waiting for its
  reply, or the last tool call is still running (or waiting for a yes). Claude Code writes the
  tool call before running it and an "api-request" entry when it sends a request. In 13,000
  measured requests, 1 in 100 replies took over 2 minutes and the slowest 16, with no write in
  between; Claude Code's Bash tool runs a foreground command for up to 10 minutes.
  Only each entry's type and stop_reason are used. Nothing from a transcript is kept.
- Capped: a turn whose transcript has had no write for 15 minutes (CAP) counts as stuck (a
  question nobody answered, a crash), so a stuck session can't keep the Mac up all night.
Live `claude` processes are not used: idle sessions (an open editor panel) stay running for days,
so a running process says nothing about work.

    python3 -m switchboard.awake --once      prints the status; holds nothing, changes nothing
"""
import json
import math
import os
import re
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager

FRESH = 120             # a transcript written this recently means work is going on
CAP = 15 * 60           # an open turn with nothing written for this long is treated as stuck
AHEAD = 5               # a file time this far past the clock is still "now"; any further is not recent at all
LOW_BATTERY = 25        # percent
WARN_EVERY = 30 * 60    # at most one warning per half hour
FULL_SCAN_EVERY = 600   # look at every transcript's time this often; in between only at the recent ones
TAIL = 256 * 1024       # how much of a transcript's end to read for its last entry
MAX_TAILS = 64          # read at most this many transcripts per check
CAFFEINATE = '/usr/bin/caffeinate'
PMSET = ['/usr/bin/pmset', '-g', 'batt']
IOREG = ['/usr/sbin/ioreg', '-r', '-k', 'AppleClamshellState', '-d', '1']

OPEN, CLOSED, UNKNOWN = 'open', 'closed', 'unknown'
_KINDS = tuple(p % k for k in (b'assistant', b'user', b'api-request', b'system')
               for p in (b'"type":"%s"', b'"type": "%s"'))


def projects_root():
    """Where Claude Code keeps transcripts: $CLAUDE_CONFIG_DIR/projects, else ~/.claude/projects."""
    base = os.environ.get('CLAUDE_CONFIG_DIR') or os.path.join(os.path.expanduser('~'), '.claude')
    return os.path.join(base, 'projects')


def run_cmd(cmd, timeout=5):
    """A command's output, or '' when it can't be run. No shell."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout or ''
    except (OSError, subprocess.SubprocessError):
        return ''


# ---------- power and lid ----------

def parse_batt(text):
    """(on_ac, battery_pct) from `pmset -g batt`. A Mac without a battery gives (True, None)."""
    on_ac, pct = None, None
    for line in text.splitlines():
        m = re.search(r"drawing from '([^']+)'", line)
        if m:
            on_ac = m.group(1) == 'AC Power'
        elif 'InternalBattery' in line:
            p = re.search(r'\t(\d{1,3})%;', line) or re.search(r'(\d{1,3})%', line)
            if p:
                pct = min(100, int(p.group(1)))
    return on_ac, pct


def parse_clamshell(text):
    """True when the lid is shut, False when open, None when there is no lid (or no answer)."""
    m = re.search(r'"AppleClamshellState"\s*=\s*(Yes|No)', text)
    return None if not m else m.group(1) == 'Yes'


# ---------- finding recent transcripts ----------

class Transcripts(object):
    """Finds transcripts written recently, without looking at all of them every time.

    A busy Mac can hold 21,000 transcript files (most from workflow agents), and timing them all
    takes 0.4 s. So each check looks at folder times (a new file changes its folder's time), at the
    top-level chat files, and at files written recently; every FULL_SCAN_EVERY seconds it looks
    at everything, which catches an old chat that was picked up again.
    """

    def __init__(self, root=None, max_depth=8):
        self.root = root or projects_root()
        self.max_depth = max_depth
        self.dirs = {}      # folder -> (its time, [subfolders], [transcripts])
        self.times = {}     # transcript -> last time seen
        self.full_at = None

    def recent(self, now, within):
        """[(time written, path)] for transcripts written in the last `within` seconds, newest first."""
        full = self.full_at is None or now - self.full_at >= FULL_SCAN_EVERY
        if full:
            self.full_at = now
        hot = now - CAP - FRESH - FULL_SCAN_EVERY
        seen = set()
        stack = [(self.root, 0)]
        while stack:
            folder, depth = stack.pop()
            try:
                mtime = os.stat(folder).st_mtime
            except OSError:
                continue
            seen.add(folder)
            cached = self.dirs.get(folder)
            changed = cached is None or cached[0] != mtime
            if changed or full:
                subs, files = [], []
                try:
                    with os.scandir(folder) as it:
                        for e in it:
                            try:
                                if e.is_dir(follow_symlinks=False):
                                    subs.append(e.path)
                                elif e.name.endswith('.jsonl') and e.is_file(follow_symlinks=False):
                                    files.append(e.path)
                            except OSError:
                                pass
                except OSError:
                    continue
                if cached:
                    for gone in set(cached[2]) - set(files):
                        self.times.pop(gone, None)
                self.dirs[folder] = cached = (mtime, subs, files)
            for path in cached[2]:
                last = self.times.get(path)
                if changed or full or depth == 1 or last is None or last >= hot:
                    try:
                        self.times[path] = os.stat(path).st_mtime
                    except OSError:
                        self.times.pop(path, None)
            if depth < self.max_depth:
                stack.extend((s, depth + 1) for s in cached[1])
        for folder in [d for d in self.dirs if d not in seen]:
            for path in self.dirs.pop(folder)[2]:
                self.times.pop(path, None)
        # A time more than AHEAD seconds past now (a restored or synced file, a clock set back) is not recent:
        # it would otherwise read as "written just now" until that time comes, holding the Mac awake for days.
        cut = now - within
        return sorted(((t, p) for p, t in self.times.items() if cut <= t <= now + AHEAD), reverse=True)


def turn_state(path, tail=TAIL):
    """OPEN when a transcript's last request has no reply yet or its last tool call no result,
    CLOSED when the turn has ended, UNKNOWN when the end of the file doesn't say."""
    try:
        with open(path, 'rb') as f:
            size = os.fstat(f.fileno()).st_size
            start = max(0, size - tail)
            f.seek(start)
            lines = f.read().split(b'\n')
    except OSError:
        return UNKNOWN
    if start > 0:
        lines = lines[1:]                   # the first line is only the end of one
    for raw in reversed(lines):
        if not any(k in raw for k in _KINDS):
            continue
        try:
            d = json.loads(raw)
        except ValueError:
            continue                        # a line still being written, or not an entry
        if not isinstance(d, dict):
            continue
        kind = d.get('type')
        if kind == 'api-request':
            return OPEN                     # sent, no reply yet
        if kind == 'assistant':
            m = d.get('message') if isinstance(d.get('message'), dict) else {}
            stop = m.get('stop_reason')
            content = m.get('content') if isinstance(m.get('content'), list) else []
            calls = any(isinstance(b, dict) and b.get('type') == 'tool_use' for b in content)
            return OPEN if stop in ('tool_use', 'pause_turn') or (stop is None and calls) else CLOSED
        if kind == 'user':
            return CLOSED                   # a reply comes with a new request entry; this alone is no work
        if kind == 'system' and d.get('subtype') == 'api_error':
            return CLOSED                   # a retry writes a new request entry
    return UNKNOWN


def _ago(seconds):
    s = int(max(0, seconds))
    return '%d s' % s if s < 120 else '%d min' % (s // 60)


# ---------- the helper ----------

class Awake(object):
    """Holds caffeinate while Claude Code works. Everything outside Python is injectable for tests."""

    def __init__(self, run=run_cmd, popen=subprocess.Popen, clock=time.time, root=None, pid=None):
        self.run = run
        self.popen = popen
        self.clock = clock
        self.transcripts = Transcripts(root)
        self.pid = pid or os.getpid()
        self.proc = None
        self.last_warned = None
        self.warning = None                 # what should_warn() last decided to say
        self.turns = {}                     # transcript -> (time written, OPEN/CLOSED/UNKNOWN)

    def _turn(self, path, written):
        known = self.turns.get(path)
        if known and known[0] == written:
            return known[1]                 # not written since: same answer, no need to read it again
        state = turn_state(path)
        self.turns[path] = (written, state)
        return state

    # what is going on

    def working(self, now=None):
        """(working, reason). Reasons never name a file or a project."""
        now = self.clock() if now is None else now
        recent = self.transcripts.recent(now, CAP)
        if not recent:
            return False, 'No Claude Code chat has been written in the last %d minutes.' % (CAP // 60)
        newest = max(0.0, now - recent[0][0])
        if newest < FRESH:
            return True, 'A Claude Code chat was written %s ago.' % _ago(newest)
        live = set(p for _, p in recent)
        for path in [p for p in self.turns if p not in live]:
            del self.turns[path]
        for written, path in recent[:MAX_TAILS]:
            if self._turn(path, written) == OPEN:
                return True, ('A turn is still open (a reply or a tool is still running); the last write was %s '
                              'ago. After %d minutes with no write it counts as stuck.' % (_ago(newest), CAP // 60))
        return False, 'Claude Code finished; the last write was %s ago.' % _ago(newest)

    def power(self):
        on_ac, pct = parse_batt(self.run(PMSET))
        return {'on_ac': on_ac, 'battery_pct': pct, 'lid_closed': parse_clamshell(self.run(IOREG))}

    def holding(self):
        return self.proc is not None and self.proc.poll() is None

    def status(self, now=None):
        """{working, holding, on_ac, lid_closed, battery_pct, reason, limits}."""
        working, reason = self.working(now)
        st = dict(self.power(), working=working, holding=self.holding(), reason=reason)
        st['limits'] = limits(st)
        return st

    # holding and letting go

    def tick(self, now=None):
        """Check once; start or stop the hold to match. Returns the status."""
        st = self.status(now)
        if st['working'] and not self.holding():
            self._hold()
        elif not st['working'] and self.holding():
            self.release()
        st['holding'] = self.holding()
        return st

    def _hold(self):
        self.release()
        try:
            self.proc = self.popen([CAFFEINATE, '-i', '-s', '-w', str(self.pid)], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
        except OSError:
            self.proc = None

    def release(self):
        p, self.proc = self.proc, None
        if p is None or p.poll() is not None:
            return
        p.terminate()
        try:
            p.wait(timeout=2)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait(timeout=2)

    # warnings

    def should_warn(self, st=None, now=None):
        """True when the person should plug in: on battery under 25% while Claude Code works or the hold
        is on, or the lid shut on battery while working. At most once per 30 minutes. The words are
        left in self.warning."""
        now = self.clock() if now is None else now
        st = st or self.status(now)
        busy = st.get('working') or st.get('holding')
        on_battery = st.get('on_ac') is False
        why = None
        if st.get('working') and on_battery and st.get('lid_closed'):
            why = 'The lid is shut on battery power, so the Mac will sleep and Claude Code will stop.'
        elif busy and on_battery and st.get('battery_pct') is not None and st['battery_pct'] < LOW_BATTERY:
            why = 'Battery at %d%% while Claude Code is working.' % st['battery_pct']
        if why is None or (self.last_warned is not None and now - self.last_warned < WARN_EVERY):
            return False
        self.last_warned, self.warning = now, why
        return True

    # the loop

    def run_loop(self, poll_seconds=20, stop=None, on_tick=None):
        """Check every poll_seconds until `stop` (a threading.Event) is set. Always lets go at the end."""
        stop = stop or threading.Event()
        old = None
        if threading.current_thread() is threading.main_thread():
            old = signal.signal(signal.SIGTERM, lambda *_: stop.set())
        try:
            while not stop.is_set():
                try:
                    st = self.tick()
                except Exception as e:          # one bad check must not end the loop
                    st = {'error': str(e)}
                if on_tick:
                    on_tick(st)
                stop.wait(poll_seconds)
        finally:
            self.release()
            if old is not None:
                signal.signal(signal.SIGTERM, old)


@contextmanager
def held_awake(seconds, popen=subprocess.Popen, pid=None):
    """The Mac held awake for one short job (a login's renewal, whose answer must not be lost to a sleep): the same
    caffeinate Awake._hold starts (-i -s -w <this process>), with -t so it ends by itself after `seconds` (rounded up)
    even if this process is stopped before it lets go. Let go when the block ends. Yields True while held; False, and
    nothing held, when caffeinate can't start. Never raises for the hold itself."""
    secs = max(1, int(math.ceil(seconds)))
    try:
        proc = popen([CAFFEINATE, '-i', '-s', '-w', str(pid or os.getpid()), '-t', str(secs)],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    except (OSError, ValueError):
        proc = None
    try:
        yield proc is not None
    finally:
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
            except OSError:
                pass


def lid_shut_on_battery(run=run_cmd):
    """True when the Mac runs on battery with its lid shut: it sleeps then, whatever is held (see the top of this file).
    False when it is on power, its lid is open, it has no lid, or the answer is not known."""
    on_ac, _ = parse_batt(run(PMSET))
    return on_ac is False and parse_clamshell(run(IOREG)) is True


def limits(st):
    """What macOS allows right now, in plain words."""
    out = []
    if st.get('on_ac') is False:
        if st.get('lid_closed'):
            out.append('On battery with the lid shut, the Mac sleeps no matter what.')
        else:
            out.append('On battery only idle sleep is held off (-s counts only on AC power); '
                       'shutting the lid will sleep the Mac.')
    return out


_default = None


def _helper():
    global _default
    if _default is None:
        _default = Awake()
    return _default


def status():
    return _helper().status()


def should_warn():
    return _helper().should_warn()


def run(poll_seconds=20, stop=None, on_tick=None):
    """The background loop, for a service to call. Not started by anything in this file."""
    _helper().run_loop(poll_seconds, stop, on_tick)


def describe(st):
    power = 'AC power' if st['on_ac'] else 'battery' if st['on_ac'] is False else 'unknown power'
    lines = ['working:  %s. %s' % ('yes' if st['working'] else 'no', st['reason']),
             'holding:  %s' % ('yes' if st['holding'] else 'no (--once only looks; the background loop holds)'),
             'power:    %s%s, lid %s' % (power, '' if st['battery_pct'] is None else ', battery %d%%' % st['battery_pct'],
                                         'shut' if st['lid_closed'] else 'open' if st['lid_closed'] is False else 'none')]
    lines += ['limit:    ' + x for x in st['limits']]
    return '\n'.join(lines)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if '--once' not in argv:
        print('usage: python3 -m switchboard.awake --once [--json]\n'
              'Prints whether Claude Code is working and what the power is. Holds nothing.')
        return 2
    st = Awake().status()
    print(json.dumps(st, indent=1) if '--json' in argv else describe(st))
    return 0


if __name__ == '__main__':
    sys.exit(main())
