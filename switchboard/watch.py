"""What keeps Switchboard up, and says so when it is not: the page watching itself, the page and the menu bar watching
each other, and the notes for the times Switchboard is not doing its job.

Each way Switchboard can stop has more than one thing that brings it back:

- The page process exits: launchd starts it again (its job's KeepAlive). The menu bar starts it too, when the page has
  refused its readings for a few minutes and the person did not stop it (PageKeeper), at most every five minutes.
- The page process hangs without exiting (its loop, or the build of its state, stuck in one step): a thread of its own
  (PageWatch) exits the process after PAGE_STUCK seconds of awake time, so launchd starts a fresh page. Never while a
  switch is being written, never in the first minutes after the Mac wakes, never more than FIRES_PER_HOUR times an
  hour (then it stays as it is and says so).
- Another program holds the page's port: the page runs everything but its page (the readings, the loop that switches,
  the notes), tries the port again every minute, and says so once (port_note; the menu bar reads port_mark).
- The menu bar item stops without being quit: the page starts its job again (MenuBarKeeper).

What the person did on purpose is never undone: the page writes a stop mark when it is stopped (SIGTERM: launchd's
bootout, the installer, the uninstaller, a person), and the menu bar writes a quit mark when Quit is chosen.

Everything here is plain Python 3.9: the menu bar imports it too. Times that measure a wait are on the monotonic
clock, which stops while the Mac sleeps; the wall clock is used only for what other processes must read.
"""
import errno
import fcntl
import os
import time

from . import core
from .core import data_path, log

# ---------- is this the background job? ----------


def background():
    """True when launchd started this process (its parent is launchd, process 1): something starts it again when it
    exits. A page run by hand in a terminal is never exited by its own watchdog, and a taken port is said and left."""
    try:
        return os.getppid() == 1
    except OSError:
        return False


def _wall_times(values, now, within):
    """The wall times in `values` (a list from a file) that fall within the last `within` seconds. A time dated past
    the clock (it was set back) counts for nothing."""
    out = []
    for t in values if isinstance(values, list) else []:
        if isinstance(t, (int, float)) and not isinstance(t, bool) and -5.0 <= now - t < within:
            out.append(float(t))
    return out


def _write(name, data):
    """A small file of this module's, written whole or not at all. Never raises: a full disk only loses the note."""
    try:
        core.write_json(data_path(name), data)
        return True
    except (OSError, ValueError) as e:
        log('could not write %s: %r' % (name, e))
        return False


def _remove(name):
    try:
        os.remove(data_path(name))
    except OSError:
        pass


# ---------- the page watches itself ----------

PAGE_STUCK = 600.0      # awake seconds the page's loop may stay in one step, or one build of its state may run. Every
                        # step that can wait has a deadline far under this (the Keychain 45 s, a lock 60 s, a read
                        # 70 s), so ten minutes is only ever reached by a step that hangs.
WAKE_GRACE = 300.0      # after a wake the network and the disk come back slowly: these first minutes never count
FIRES_PER_HOUR = 3      # a page that hangs again at once is not helped by a fourth restart within the hour
LOOK_EVERY = 15.0       # seconds between two looks
SLEPT = 30.0            # a wall-clock step this much bigger than the monotonic one is a sleep (or the time was set)
EXIT_STUCK = 75         # the page's exit code when its watchdog ends it (launchd starts it again on any exit)
EXIT_PYTHON = 76        # ... and when no Python could start the loop (launchd starts it on the launcher's Python)
PAGE_WATCH = 'page-watch.json'     # {'fired': [wall times]}: when the watchdog ended the page, kept for an hour


def watchdog_fired(now=None):
    """How many times the page's watchdog ended the page within the last hour."""
    now = time.time() if now is None else now
    return len(_wall_times(core.read_json(data_path(PAGE_WATCH)).get('fired'), now, 3600.0))


class PageWatch(object):
    """The page's watchdog (see the module's docstring). stage() -> (name, monotonic time it began) of the page's
    loop, (None, None) before it names one; building() -> the monotonic time a build of the state that is still
    running began, or None; switching() -> True while a switch is being written. exit is os._exit in the page."""

    def __init__(self, stage, building, switching, mono=time.monotonic, wall=time.time, exit=os._exit):
        self.stage, self.building, self.switching = stage, building, switching
        self.mono, self.wall, self.exit = mono, wall, exit
        self.clock = None           # (wall, monotonic) at the last look
        self.woke_at = None         # monotonic: the last wake seen
        self.said = set()           # what was said once already: 'switch', 'hour'

    def _age(self, since, now):
        floor = since if self.woke_at is None else max(since, self.woke_at)
        return now - floor          # nothing from before the last wake counts

    def look(self):
        """None, or the words for what has hung (the page should exit)."""
        m, w = self.mono(), self.wall()
        if self.clock is not None and (w - self.clock[0]) - (m - self.clock[1]) > SLEPT:
            self.woke_at = m
        self.clock = (w, m)
        if self.woke_at is not None and m - self.woke_at < WAKE_GRACE:
            return None
        late = []
        name, since = self.stage()
        if since is not None and self._age(since, m) > PAGE_STUCK:
            late.append("its loop has been %s for %d min" % (name or 'in one step', self._age(since, m) // 60))
        began = self.building()
        if began is not None and self._age(began, m) > PAGE_STUCK:
            late.append('its state has been building for %d min' % (self._age(began, m) // 60))
        if not late:
            self.said.discard('switch')
            return None
        why = '; '.join(late)
        if self.switching():
            if 'switch' not in self.said:
                self.said.add('switch')
                log("the page's watchdog: %s, but a switch is being written; it waits for the switch" % why)
            return None
        if watchdog_fired(w) >= FIRES_PER_HOUR:
            if 'hour' not in self.said:
                self.said.add('hour')
                log("the page's watchdog: %s, and it restarted the page %d times in the last hour already; not again "
                    "this hour. Run: switchboard doctor" % (why, FIRES_PER_HOUR))
            return None
        return why

    def fire(self, why):
        """One line in the log, the restart counted for the hour, then the process ends (launchd starts it again)."""
        w = self.wall()
        fired = _wall_times(core.read_json(data_path(PAGE_WATCH)).get('fired'), w, 3600.0)
        _write(PAGE_WATCH, {'fired': fired + [round(w, 3)]})
        log("the page's watchdog: %s; ending the page so that launchd starts a fresh page" % why)
        core.log_flush()
        self.exit(EXIT_STUCK)

    def run(self, stop=None, sleep=time.sleep):
        """Look every LOOK_EVERY seconds for as long as the process runs (a daemon thread in the page)."""
        while not (stop is not None and stop.is_set()):
            try:
                why = self.look()
                if why:
                    self.fire(why)
            except Exception as e:  # noqa: BLE001 - the watchdog never takes the page down by its own fault
                if 'error' not in self.said:
                    self.said.add('error')
                    log("the page's watchdog could not look: %r" % e)
            sleep(LOOK_EVERY)


def lock_held(path):
    """True while some process holds an exclusive flock on `path` (tried, never waited for; a lock that is not there
    is not made)."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        return False
    except OSError as e:
        return e.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES)
    finally:
        os.close(fd)                # closing it lets a shared lock go


def switch_in_progress():
    """True while a switch is being written: the engine's switch lock is held (by the page, a terminal or the loop), or
    the loop's heartbeat names its switching stage, or the stage that puts a lost sign-in back (it reads and writes
    Claude Code's login, as a switch does)."""
    if core.read_json(data_path('auto-heartbeat.json')).get('stage') in ('switching', 'putting back'):
        return True
    return lock_held(data_path('switch.lock'))


# ---------- the page's starts ----------

STARTS = 'page-starts.json'        # {'starts': [wall times]}: when the page started, kept for an hour
RESTARTS_WINDOW = 600.0            # "restarted more than three times in ten minutes"
RESTARTS_SAID = 3


def note_start(now=None):
    """Count one start of the page (serve calls it once)."""
    now = time.time() if now is None else now
    starts = _wall_times(core.read_json(data_path(STARTS)).get('starts'), now, 3600.0)
    _write(STARTS, {'starts': starts + [round(now, 3)]})


def starts_within(seconds, now=None):
    now = time.time() if now is None else now
    return len(_wall_times(core.read_json(data_path(STARTS)).get('starts'), now, seconds))


def restart_words(now=None):
    """'Switchboard restarted 4 times in the last 10 minutes.' when the page started more than RESTARTS_SAID times in
    RESTARTS_WINDOW seconds, else None."""
    n = starts_within(RESTARTS_WINDOW, now)
    if n <= RESTARTS_SAID:
        return None
    return 'Switchboard restarted %d times in the last %d minutes.' % (n, RESTARTS_WINDOW // 60)


# ---------- what the person did on purpose ----------

STOPPED = 'page-stopped.json'      # {'at', 'pid'}: the page was stopped (SIGTERM); a page that starts removes it


def note_stopped(now=None):
    """The page is being stopped on purpose (launchd's bootout: the installer, the uninstaller, a person): the menu bar
    then leaves it stopped instead of starting it again."""
    _write(STOPPED, {'at': round(time.time() if now is None else now, 3), 'pid': os.getpid()})


def stopped():
    """{'at', 'pid'} while the page's last stop was on purpose and no page has started since, else None."""
    rec = core.read_json(data_path(STOPPED))
    at = rec.get('at')
    return rec if isinstance(at, (int, float)) and not isinstance(at, bool) else None


def clear_stopped():
    _remove(STOPPED)


# ---------- a port another program holds ----------

PORT_MARK = 'page-port.json'       # {'port', 'since'}: the page waits for its port; gone once it has it


def mark_port(n, now=None):
    """The page could not have port n: written for the menu bar. A mark for the same port keeps its first time, so a
    page that starts again while the port is still taken is the same spell (said once)."""
    rec = port_mark()
    if rec is not None and rec['port'] == n:
        return rec
    rec = {'port': int(n), 'since': round(time.time() if now is None else now, 3)}
    _write(PORT_MARK, rec)
    return rec


def port_mark():
    """{'port', 'since'} while the page waits for a port another program holds, else None."""
    rec = core.read_json(data_path(PORT_MARK))
    n, since = rec.get('port'), rec.get('since')
    if isinstance(n, int) and not isinstance(n, bool) and isinstance(since, (int, float)):
        return {'port': n, 'since': float(since)}
    return None


def clear_port():
    _remove(PORT_MARK)


def port_words(n):
    """What the person reads while another program holds the page's port: which port, that auto-switch still works,
    and the one thing to do. The port it suggests is never the one in use."""
    return 'Port %d is in use by another program, so the page can\'t open. Auto-switch still works. %s' % (
        n, free_port_words(n))


def free_port_words(n):
    """The one thing to do while another program holds port n, wherever it is said (the page's note, the menu bar,
    a command that can't reach the page): the command that moves the page, with a port that is never n."""
    return 'Pick a free port: switchboard update --port %d' % (7802 if n == 7801 else 7801)


# ---------- when auto-switch last decided ----------

DECIDED_LATE = 300.0    # awake seconds past the pass the loop planned before "last decided N min ago" is said: a pass
                        # on a very busy Mac can run a few minutes over, and the line is for a loop that stopped deciding
PLAN_MAX = 600.0        # the longest wait the loop plans between two passes (autoloop.MAX_SLEEP)


def _number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x == x


def decided_late(s, enabled):
    """Whole minutes of awake time since the loop last finished a pass, when that is more than DECIDED_LATE past the
    pass it planned next, auto-switch is on and the loop holds its lease; else None. `s` is autoloop.status(): its
    pass_age_s is awake time in this boot (None across a restart of the Mac: nothing is said until the first pass)."""
    if not enabled or not isinstance(s, dict) or not s.get('running'):
        return None
    age = s.get('pass_age_s')
    if not _number(age):
        return None
    last, nxt = s.get('last_pass_at'), s.get('next_pass_at')
    planned = min(PLAN_MAX, max(0.0, nxt - last)) if _number(last) and _number(nxt) else PLAN_MAX
    if age <= planned + DECIDED_LATE:
        return None
    return int(age // 60)


def decided_words(minutes):
    """'Auto-switch last decided 12 minutes ago.' (hours past two), or None. Minutes and hours spelled out, as the page
    says them elsewhere."""
    if minutes is None:
        return None
    if minutes < 120:
        return 'Auto-switch last decided %d minutes ago.' % minutes
    return 'Auto-switch last decided %d hours ago.' % (minutes // 60)


# The one thing to do after "Auto-switch last decided ..." and "Switchboard restarted ..." on the page, in the menu bar
# and under the terminal's list: the words the notification for the same fact ends with (notify.DOCTOR). Never "it is
# being started again": the page replaces a loop that decides nothing only 12 minutes past its plan, and only while
# it runs. Doctor itself names the step (__main__._doctor_watch).
WATCH_TODO = 'If it stays like this, run: switchboard doctor'


def with_todo(decided, restarts):
    """(decided, restarts) as they are shown, the one thing to do once, after the last of them that is said."""
    if restarts:
        return decided, '%s %s' % (restarts, WATCH_TODO)
    return ('%s %s' % (decided, WATCH_TODO) if decided else decided), restarts


# ---------- the times Switchboard is not doing its job, said once for each spell ----------

SPELLS = 'watch-spells.json'       # {kind: {'key', 'since', 'last', 'said'}}: spells under way, for a page that restarts
SPELL_FOR = {                      # awake seconds a trouble lasts before it is said
    'auto': 300.0,                 # stopped, stuck or restarting: a loop that ends starts again in seconds, a stuck one
                                   # is restarted within about four minutes; past five it is not coming back by itself
    'blocked': 300.0,              # the account in use can't work and nothing switched: a forced switch is tried at
                                   # every pass (a minute or two apart), so five minutes is several that found nothing
    'keychain': 300.0,             # a Keychain dialog unanswered: a slow Keychain answers within a minute
    'old': 300.0,                  # every number over an hour old: five more minutes, so a wake (when every number is
                                   # old for the minute the first readings take) is never said
    'signin': 300.0,               # Claude Code without a sign-in that Switchboard could not put back: a lost one it
}                                  # can put back is back within seconds, and that put-back has its own note
GOOD_FOR = 120.0                   # awake seconds a trouble must be gone before its spell ends: one that comes and goes
                                   # is one spell, said once
SAME_SPELL = 900.0                 # a page that starts again within this of its last look goes on with the spells
NUMBERS_OLD = 3600.0               # "more than an hour old"
FINE_AGAIN = ('auto', 'blocked', 'signin')   # the only spells whose end is said
DISK_REASON = 'your disk is almost full (under 50 MB free)'    # after "No switch was possible for N min: "
DISK_TODO = 'Free some disk space.'


def _blocked_why(a, now):
    """Why the account in use can't work, from the page's state of it, or None: its login was refused (it needs a new
    sign-in), or a 5-hour or weekly window at 100% that has not reset yet."""
    if a.get('needs_login'):
        return 'Its login was refused, so it needs a new sign-in', 'signin'
    u = a.get('usage') if isinstance(a.get('usage'), dict) else {}
    for key, words in (('session', '5-hour'), ('weekly', 'weekly')):
        w = u.get(key) if isinstance(u.get(key), dict) else {}
        pct = w.get('pct')
        resets = core.parse_iso(w.get('resets')) if isinstance(w.get('resets'), str) else None
        if _number(pct) and pct >= 100 and (resets is None or resets > now):
            return 'It is at its %s limit' % words, 'limit'
    return None


def _loop_dead(s):
    """The loop (autoloop.status `s`) is not running, or has not beaten for longer than the page lets a loop go
    before it starts a fresh one (engine.AutoHost.STALE, awake time)."""
    from . import engine
    stuck = s.get('stuck_for_s')
    return not s.get('running') or (_number(stuck) and stuck > engine.AutoHost.STALE)


def troubles(auto, s, st, decision, now, keychain_words=None, lost=None, disk_full=False):
    """What is wrong now, as {kind: {'key', 'words'...}}, from what the page already has: auto (engine.auto_settings),
    s (autoloop.status), st (the page's state: 'active', 'accounts'), decision (the policy's last answer, or None) and
    the wall clock. Kinds:
    - 'auto': auto-switch is on and its loop is not on (stopped, stuck, restarting, not starting), or it decided
      nothing for minutes (decided_late). words: the page's own line for it.
    - 'blocked': auto-switch is on, the account in use can't work (_blocked_why) and the policy did not switch, or
      the disk is too full for any switch to begin (disk_full: then the disk is the one thing to do). key names the
      account; words, reason (why no switch) and todo (the one thing to do).
    - 'keychain': the loop's live read waits on a Keychain dialog (`keychain_words`, the loop's own sentence), or its
      beat is overdue in a stage that waits for the Keychain.
    - 'old': auto-switch is on and every account that does not need a sign-in has numbers more than NUMBERS_OLD old,
      while the loop has a network (s['offline_since'] unset): offline, it can't be mended from Switchboard and mends
      itself once the network is back.
    - 'signin': auto-switch is on and Claude Code has had no sign-in since lost['since'], while its settings still
      name a kept account (lost['account'], its whole email) that the put-back could not put back. `lost` comes from
      the put-back's own record (server.lost_signin), so a sign-out the person chose, a switch part-way and a sign-in
      under way on the page are never in it. hold: the put-back's own first wait for it is not over yet. Not while the
      loop that writes that record is dead (_loop_dead): the loss in it may be long over, and 'auto' says the rest."""
    auto, s = auto if isinstance(auto, dict) else {}, s if isinstance(s, dict) else {}
    accounts = st.get('accounts') if isinstance(st, dict) and isinstance(st.get('accounts'), dict) else {}
    on = bool(auto.get('enabled'))
    out = {}
    late = decided_late(s, on)
    if on and (not auto.get('on') or late is not None):
        out['auto'] = {'key': 'auto', 'words': (auto.get('problem') if not auto.get('on') else None) or
                       decided_words(late) or 'Auto-switch is not running.'}
    active = st.get('active') if isinstance(st, dict) else None
    a = accounts.get(active) if isinstance(active, str) else None
    why = _blocked_why(a, now) if on and isinstance(a, dict) else None
    if why and (getattr(decision, 'action', None) != 'switch' or disk_full):
        reason = getattr(decision, 'reason', None) if getattr(decision, 'action', None) == 'stay' else None
        out['blocked'] = {'key': 'blocked:%s' % active, 'who': active, 'words': why[0],
                          'reason': DISK_REASON if disk_full else reason if isinstance(reason, str) else None,
                          'todo': (DISK_TODO if disk_full else
                                   'Open Switchboard and press Sign in to all.' if why[1] == 'signin' else
                                   'Open Switchboard to see what has room: switchboard page')}
    said = getattr(decision, 'reason', None) or ''
    waits = s.get('waiting_for') == 'the Keychain' and _number(s.get('stuck_for_s')) and s['stuck_for_s'] > 0
    if (keychain_words and isinstance(said, str) and keychain_words in said) or waits:
        out['keychain'] = {'key': 'keychain'}
        out.pop('auto', None)       # the dialog is why auto-switch waits: one note, the one that says what to do
    if on and not _number(s.get('offline_since')):
        read = [x for x in accounts.values() if isinstance(x, dict) and x.get('connected') and not x.get('unmanaged')
                and not x.get('needs_login')]
        ages = [now - x['usage_at'] if _number(x.get('usage_at')) else None for x in read]
        if read and all(age is None or age > NUMBERS_OLD for age in ages):
            out['old'] = {'key': 'old'}
    if on and isinstance(lost, dict) and isinstance(lost.get('account'), str) and _number(lost.get('since')) and \
            not _loop_dead(s):
        out['signin'] = {'key': 'signin:%s' % lost['account'], 'who': lost['account'], 'since': lost['since'],
                         'hold': bool(lost.get('hold'))}
    return out


class Spells(object):
    """Each trouble (troubles()) said once for each spell, after SPELL_FOR awake seconds, and its end said (FINE_AGAIN
    only) once it has been gone GOOD_FOR. Nothing from before a wake counts. The spells are kept in SPELLS, so a page
    that starts again within SAME_SPELL goes on with them: the same spell is never said twice (and notify.send never
    shows the same note and spell twice either)."""

    def __init__(self, mono=time.monotonic, wall=time.time, path=None):
        self.mono, self.wall = mono, wall
        self.path = path
        self.open = None            # kind -> {'key', 'since', 'start' (monotonic), 'said', 'good' (monotonic or None)}
        self.clock = None
        self.written = None         # whether the file holds spells: with none open and none written, nothing is written
        self.back_at = None         # when the put-back last put a lost sign-in back (look's back_at)

    def _file(self):
        return self.path or data_path(SPELLS)

    def _load(self, m, w):
        """The spells a page before this one left, still fresh: carried on with the awake time they had."""
        self.open = {}
        rec = core.read_json(self._file())
        for kind, v in (rec.items() if isinstance(rec, dict) else []):
            if kind in SPELL_FOR and isinstance(v, dict) and isinstance(v.get('key'), str) and \
                    _number(v.get('since')) and _number(v.get('last')) and -5.0 <= w - v['last'] < SAME_SPELL:
                self.open[kind] = {'key': v['key'], 'since': float(v['since']), 'said': v.get('said') is True,
                                   'start': m - max(0.0, min(v['last'] - v['since'], SPELL_FOR[kind])), 'good': None}

    def _save(self, w):
        if not self.open and self.written is False:
            return                  # nothing wrong, and the file says so already: no write every minute
        self.written = bool(self.open)
        _write(SPELLS if self.path is None else self.path, dict(
            (k, {'key': v['key'], 'since': round(v['since'], 3), 'last': round(w, 3), 'said': v['said']})
            for k, v in self.open.items()))

    def look(self, found, active=None, back_at=None):
        """found: troubles() now; active: the account Claude Code is on now; back_at: when the put-back last put a
        lost sign-in back (its own note said so). Returns the Notes to send."""
        from . import notify
        m, w = self.mono(), self.wall()
        if self.open is None:
            self._load(m, w)
        slept = self.clock is not None and (w - self.clock[0]) - (m - self.clock[1]) > SLEPT
        if slept:
            for v in self.open.values():            # the Mac slept: what came before the wake counts for nothing
                v['start'] = m
                v['good'] = None if v['good'] is None else m
        awake = 0.0 if self.clock is None or slept else max(0.0, m - self.clock[1])
        self.clock = (w, m)
        self.back_at = back_at
        notes = []
        for kind in SPELL_FOR:
            t, v = found.get(kind), self.open.get(kind)
            if t is not None and (v is None or v['key'] != t['key']):
                if v is not None:
                    notes += self._ended(kind, v, active)
                # a trouble that says when it began counts from then, but never more than the awake time since the
                # last look (a sleep in between counts for nothing)
                early = min(max(0.0, w - t['since']), awake) if _number(t.get('since')) else 0.0
                v = self.open[kind] = {'key': t['key'], 'since': w - early, 'start': m - early, 'said': False,
                                       'good': None}
            if t is not None:
                v['good'] = None
                if kind == 'old' and (found.get('signin') is not None or 'signin' in self.open):
                    v['start'] = m      # one cause, one note: after hours without a sign-in the numbers go old too,
                    continue            # and the sign-in's note said what to do; old counts once that spell is over
                if not v['said'] and not t.get('hold') and m - v['start'] >= SPELL_FOR[kind]:
                    v['said'] = True
                    notes.append(self._note(kind, t, v, notify))
            elif v is not None:
                v['good'] = m if v['good'] is None else v['good']
                if m - v['good'] >= GOOD_FOR:
                    notes += self._ended(kind, v, active)
                    del self.open[kind]
        self._save(w)
        return notes

    def _note(self, kind, t, v, notify):
        since, minutes = int(v['since']), int(SPELL_FOR[kind] // 60)
        if kind == 'auto':
            return notify.auto_stuck(t.get('words'), since)
        if kind == 'blocked':
            return notify.blocked(t['who'], t['words'], minutes, t.get('reason'), t['todo'], since)
        if kind == 'keychain':
            return notify.keychain_waiting(minutes, since)
        if kind == 'signin':
            return notify.signin_lost(t['who'], minutes, since)
        return notify.numbers_old(since)

    def _ended(self, kind, v, active):
        """The end of a spell that was said: 'works again' for auto-switch, and for an account that can work again
        while Claude Code is still on it (a switch away is told by its own note)."""
        from . import notify
        if not v['said'] or kind not in FINE_AGAIN:
            return []
        if kind == 'auto':
            return [notify.auto_fine(int(v['since']))]
        if kind == 'signin':
            # Claude Code is signed in again, unless the put-back's own note already said so (one note for one
            # loss), or it is on no account yet (a sign-out chosen meanwhile is never called "signed in")
            back = getattr(self, 'back_at', None)
            told = _number(back) and back >= v['since'] - 5.0
            return [] if told or not active else [notify.signin_back(active, int(v['since']))]
        who = v['key'].split(':', 1)[1]
        return [notify.blocked_fine(who, int(v['since']))] if who == active else []


# ---------- the menu bar starts the page; the page starts the menu bar ----------

PAGE_DOWN_FOR = 180.0   # awake seconds the page refuses every connection before the menu bar starts it: launchd starts a
                        # page that exited within 20 s (ThrottleInterval), so three minutes means launchd did not
START_GAP = 300.0       # at most one start by a watcher every five minutes (the menu bar's, and any other's later)
STARTS_PER_HOUR = 3     # and three an hour: a page that will not stay up is not helped by more
VERIFY_FOR = 120.0      # after a start, the page must answer within this long to count as running again
PAGE_LOOK_EVERY = 15.0  # seconds between two looks of the menu bar's watch (a connection, never a reading)
TRIES = 'page-tries.json'          # {'tries': [{'at': wall time, 'by': who started it, 'ok': True, False or None}]}:
                                   # every start of the page by a watcher, kept for an hour, read by every watcher


def page_answers(port, timeout=2.0):
    """True when something accepts a connection on this Mac's port (the page, or a program in its place: the page's
    own port mark tells those apart); False when the connection is refused or times out."""
    import socket
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=timeout):
            return True
    except OSError:
        return False


def tries(now=None):
    """The watchers' starts of the page within the last hour: [{'at', 'by', 'ok'}], oldest first."""
    now = time.time() if now is None else now
    out = []
    for t in core.read_json(data_path(TRIES)).get('tries') or []:
        if isinstance(t, dict) and _wall_times([t.get('at')], now, 3600.0):
            out.append(t)
    return out


def note_try(by, ok, at, now=None):
    """Record a start (or set its outcome: the same `at`), under a lock so two watchers never lose each other's."""
    now = time.time() if now is None else now
    try:
        with core.file_lock('page-tries.lock'):
            kept = [t for t in tries(now) if t.get('at') != round(at, 3)]
            _write(TRIES, {'tries': kept + [{'at': round(at, 3), 'by': by, 'ok': ok}]})
    except Exception as e:  # noqa: BLE001 - a start that is not counted is still bounded by this process's own count
        log('could not count a start of the page: %r' % e)


def start_job(label, plist, launchctl, domain):
    """Start a launchd job the way the menu bar's Start does: kickstart, else load it again. True when launchd took it;
    None when there is no job description (nothing is started then)."""
    if not plist or not os.path.exists(plist):
        return None
    return launchctl('kickstart', '%s/%s' % (domain, label)) == 0 or launchctl('bootstrap', domain, plist) == 0


class PageKeeper(object):
    """The menu bar's watch on the page: a second watcher, in another process and another launchd job. When the page
    has refused every connection for PAGE_DOWN_FOR awake seconds, the person did not stop it (stopped()) and it is not
    waiting for its port (port_mark()), it asks once more, then starts the page through launchd (start: () -> True,
    False, or None when there is no job), at most every START_GAP and STARTS_PER_HOUR an hour across every watcher
    (TRIES). One note either way: "running again" when it answers within VERIFY_FOR, or "could not be started" once for
    the spell. answers: () -> bool; send: (Note) -> anything."""

    def __init__(self, answers, start, send, mono=time.monotonic, wall=time.time, by='menu bar'):
        self.answers, self.start, self.send = answers, start, send
        self.mono, self.wall, self.by = mono, wall, by
        self.down_since = None      # monotonic: the first refusal of this spell
        self.spell = None           # wall: when this spell began (the "could not be started" note's spell)
        self.pending = None         # {'at' (wall), 'mono'}: a start waiting to be seen answering
        self.said_down = False
        self.clock = None
        self.held = set()           # what was said in the log once already

    def _once(self, what, words):
        if what not in self.held:
            self.held.add(what)
            log(words)

    def look(self):
        """One look. Returns what it did: None, 'back', 'started', 'failed'."""
        m, w = self.mono(), self.wall()
        if self.clock is not None and (w - self.clock[0]) - (m - self.clock[1]) > SLEPT:
            self.down_since = None if self.down_since is None else m    # after a wake the page needs its own minute
        self.clock = (w, m)
        up = self.answers()
        if self.pending is not None:
            if up:
                at, self.pending = self.pending['at'], None
                note_try(self.by, True, at, now=w)
                self._quiet()
                self.send(_notify().page_back(int(at)))
                log('the page answers again after the %s started it' % self.by)
                return 'back'
            if m - self.pending['mono'] < VERIFY_FOR:
                return None
            note_try(self.by, False, self.pending['at'], now=w)
            self.pending = None
            return self._failed('it was started but did not answer within %d s' % VERIFY_FOR)
        if up:
            self._quiet()
            return None
        if self.down_since is None:
            self.down_since, self.spell = m, w
        if m - self.down_since < PAGE_DOWN_FOR:
            return None
        if stopped() is not None:
            self._once('stopped', 'the page was stopped on purpose: the %s leaves it stopped' % self.by)
            return None
        if port_mark() is not None:
            return None                 # the page runs, waiting for its port: starting it again would not help
        recent = tries(w)
        if any(_wall_times([t.get('at')], w, START_GAP) for t in recent):
            return None
        if len(recent) >= STARTS_PER_HOUR:
            self._once('hour', 'the page has been started %d times in the last hour and did not stay up; the %s '
                               'starts it no more this hour' % (STARTS_PER_HOUR, self.by))
            return self._failed(None)
        if self.answers():
            self._quiet()
            return None
        took = self.start()
        if took is None:
            self._once('no-job', 'the page is not running and has no background job: the %s does not start it'
                       % self.by)
            return None
        note_try(self.by, None if took else False, w, now=w)
        if not took:
            return self._failed('launchd would not start it')
        self.pending = {'at': w, 'mono': m}
        log('the page had refused every connection for %d s: the %s started it through launchd'
            % (m - self.down_since, self.by))
        return 'started'

    def _failed(self, why):
        if why:
            log('the page could not be started by the %s: %s' % (self.by, why))
        if not self.said_down:
            self.said_down = True
            self.send(_notify().page_down(int(self.spell or self.wall())))
        return 'failed'

    def _quiet(self):
        self.down_since = self.spell = None
        self.said_down = False
        self.held.discard('stopped')
        self.held.discard('hour')

    def run(self, stop=None, sleep=time.sleep):
        """Look every PAGE_LOOK_EVERY seconds for as long as the process runs (a daemon thread in the menu bar)."""
        while not (stop is not None and stop.is_set()):
            try:
                self.look()
            except Exception as e:  # noqa: BLE001 - the watch never takes the menu bar down by its own fault
                self._once('error', "the %s's watch on the page could not look: %r" % (self.by, e))
            sleep(PAGE_LOOK_EVERY)


def _notify():
    from . import notify
    return notify


QUIT = 'menubar-quit.json'         # {'at', 'pid'}: Quit was chosen in the menu bar; a menu bar that starts removes it
BAR_DOWN_FOR = 60.0     # awake seconds the menu bar is not running before the page starts its job: launchd starts one
                        # that crashed within 20 s, so a minute means launchd did not
BAR_GAP = 300.0         # at most one start of the menu bar by the page every five minutes
BAR_PER_HOUR = 3        # and three an hour (a menu bar that can't draw exits at once: no more than that)
BAR_TRIES = 'menubar-tries.json'   # {'tries': [wall times]}: the page's starts of the menu bar, kept for an hour


def note_quit(now=None):
    """Quit was chosen in the menu bar: neither launchd (its job starts it again only after a crash) nor the page
    starts it again."""
    _write(QUIT, {'at': round(time.time() if now is None else now, 3), 'pid': os.getpid()})


def quit_chosen():
    rec = core.read_json(data_path(QUIT))
    at = rec.get('at')
    return rec if _number(at) else None


def clear_quit():
    _remove(QUIT)


class MenuBarKeeper(object):
    """The page's watch on the menu bar. When the menu bar's job is installed (job: () -> its plist or None), the menu
    bar has not run for BAR_DOWN_FOR awake seconds (running: () -> bool) and the person did not quit it (quit_chosen),
    the page starts its job again (start: (plist) -> True, False or None), at most every BAR_GAP and BAR_PER_HOUR an
    hour."""

    def __init__(self, running, job, start, mono=time.monotonic, wall=time.time):
        self.running, self.job, self.start = running, job, start
        self.mono, self.wall = mono, wall
        self.gone_since = None
        self.said = set()

    def look(self):
        """One look. Returns None, 'started' or 'failed'."""
        m, w = self.mono(), self.wall()
        plist = self.job()
        if not plist or self.running():
            self.gone_since = None
            self.said.clear()
            return None
        if quit_chosen() is not None:
            self.gone_since = None
            return None
        if self.gone_since is None:
            self.gone_since = m
        if m - self.gone_since < BAR_DOWN_FOR:
            return None
        recent = _wall_times(core.read_json(data_path(BAR_TRIES)).get('tries'), w, 3600.0)
        if _wall_times(recent, w, BAR_GAP):
            return None
        if len(recent) >= BAR_PER_HOUR:
            if 'hour' not in self.said:
                self.said.add('hour')
                log('the menu bar was started %d times in the last hour and did not stay up; the page starts it no '
                    'more this hour. Run: switchboard doctor' % BAR_PER_HOUR)
            return None
        _write(BAR_TRIES, {'tries': recent + [round(w, 3)]})
        took = self.start(plist)
        log('the menu bar was not running and was not quit: the page %s its job' % (
            'started' if took else 'could not start'))
        return 'started' if took else 'failed'
