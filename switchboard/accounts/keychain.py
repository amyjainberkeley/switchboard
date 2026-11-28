"""The Keychain through /usr/bin/security only: typed reads, hex writes, one call at a time, deadlines, no killed dialogs.

What each call can answer, kept apart:
- present: the value, from the Keychain ('keychain'), or the last good value while the item is resting after a
  failure ('cache', marked degraded: show it, never spend or keep it);
- absent: exit 44 AND the login keychain is in this process's search list (a process outside the person's login
  session gets 44 for everything, which is not "logged out");
- unreadable, with why: 'locked' (exit 36, interaction not allowed: a locked keychain or no GUI), 'denied' (51, 128:
  refused or cancelled), 'no_keychain', 'timeout' (no answer within the deadline), 'busy' (another call holds the
  gate, or too many helpers are still waiting on a dialog), 'no_tool' (the tool could not be started), 'test_run'
  (the real tool was not started because Switchboard's test mode is on: TEST_RUN_VAR), 'unparseable', 'unknown'.

A helper that runs past its deadline is never killed: it may be showing "Switchboard wants to use your keychain", and
killing it tears that dialog down (and can crash securityd in a loop). Each helper runs in a session of its own, so
nothing that kills its caller's process group reaches it either. It is left to finish on its own, the item is
marked "needs approval or slow", and nothing touches that item again until it has. A late answer to a read becomes the
item's last good value. When macOS's SecurityAgent (the process that draws Keychain dialogs) is running at that moment,
the reason is 'dialog' ("waiting for you to answer a Keychain dialog"), not 'timeout'.
Two exceptions, and neither ever ends a helper while a Keychain dialog is showing: a call made while Claude Code's
save lock on its item is held is short (UNDER_LOCK_S) and its helper is ended at the deadline, since a write left to
finish could land after that lock is let go; and a process on its way out ends every helper it still has running
(end_children, from ccfiles.release_all), so none outlives it. While SecurityAgent is running the helper may be the
one drawing the dialog: it is left to finish as above (the lock is let go all the same), and its answer is 'dialog'.

Deadlines grow with load (one-minute load per core): as set under 1.5, rising in a straight line to three times as long
at 3 and above, never past 30 s for a read or 45 s for a write. A busy Mac answers late; that is not a locked Keychain.

Writes go the way Claude Code itself writes its item (checked in 2.1.325): one command line on the standard input of
`security -i` (add-generic-password -U -a "<account>" -s "<service>" -X "<hex>"), so the login never sits in the
process arguments where `ps` shows it; only when that line would be longer than Claude Code's own limit of 4032 bytes
(security's stdin line buffer is 4096, and a longer line is cut short without a word, which corrupts the write) does
the hex go in the arguments instead. No -T on an update, so the item's access list stays as its creator made it.
"""
import fcntl
import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Optional

from . import ccfiles
from .api import ABSENT, PRESENT, UNREADABLE, Read
from .facts import STDIN_LIMIT

TOOL = '/usr/bin/security'
READ_DEADLINE = 8.0
WRITE_DEADLINE = 15.0
READ_CAP = 30.0             # the longest a read may wait under load
WRITE_CAP = 45.0            # ... and a write or a delete
LOAD_EASY = 1.5             # load per core under this: deadlines as set
LOAD_HEAVY = 3.0            # at or above this: LOAD_FACTOR_MAX times as long
LOAD_FACTOR_MAX = 3.0
BACKOFF_S = 30.0            # an item rests this long after a failure (the Keychain as a whole for locked/slow)
LOAD_HIGH = 4.0             # load per core above this: Switchboard's routine Keychain calls are spaced out (busy_mac;
                            # the same line as autoloop.LOAD_HIGH, above which no optional switch is made)
LOAD = None                 # tests: f() -> load per core; None: system_load_per_core()
SEARCHABLE_TTL = 300.0
MAX_DETACHED = 2            # helpers still waiting on a dialog; past this no new helper starts
# While this thread holds Claude Code's save lock on its item (ccfiles.held_save_lock), Claude Code cannot save, and
# its own tries for that lock end after about 5 s (10 tries, 0.1 to 1 s apart; 2.1.329). So a call made then gets at
# most UNDER_LOCK_S, gates included, and no more than what is left of UNDER_LOCK_TOTAL_S since the lock was taken (once
# that is spent, nothing starts); past its deadline its helper is ended (SIGTERM, then SIGKILL after END_GRACE_S), never
# left to finish: a write that landed after the lock is let go could land over a newer save of Claude Code's.
# What comes on top of UNDER_LOCK_TOTAL_S for the last call: seeing whether a Keychain dialog shows (security_agent_up,
# up to 2 s) and ending its helper (up to twice END_GRACE_S). So the lock is let go within about 16 s, not 12.
UNDER_LOCK_S = 5.0
UNDER_LOCK_TOTAL_S = 12.0
END_GRACE_S = 1.0
ARGV_NOTE = 'keychain-argv.json'   # beside the gate: how many logins went in the arguments (doctor reads it)
# add-generic-password exits 45 (errSecDuplicateItem) without -U when the item is there, and with -U when another
# process saved the same item at that moment (measured on macOS 26.5: 7 to 9 of 10 simultaneous saves of one item).
DUPLICATE_EXIT = 45
PS = '/bin/ps'
# Set only by Switchboard's test suite: tests/_isolate.py puts it in the environment when a test file imports it, so
# every process a test starts inherits it. Nothing in the installed program sets it. While it is set, the real tool
# (TOOL, however it is named: real_tool) is never started, so a test's child process, which is outside that file's
# audit hook, cannot reach the person's Keychain. A stand-in tool's path is not refused.
TEST_RUN_VAR = 'SWITCHBOARD_TEST_RUN'
# What a person is told when a call answered 'test_run' (doctor, uninstall): plain words, and the log line of the
# refusal names the variable. No other file of the program names it.
TEST_RUN_WORDS = "Switchboard's test mode is on in this environment (its log names the variable to unset)"


def test_run():
    """True while Switchboard's test mode is on (TEST_RUN_VAR, set only by the test suite). The one test fence:
    the Keychain's own calls ask this, and so does any other part that must hold back in a test run."""
    return bool(os.environ.get(TEST_RUN_VAR))


# security's exit status is the low byte of the Keychain error (errSecItemNotFound -25300 -> 44, ...)
_EXIT_WHY = {36: 'locked', 51: 'denied', 128: 'denied', 37: 'no_keychain', 50: 'no_keychain'}
_GLOBAL = ('locked', 'no_keychain', 'timeout', 'busy', 'no_tool', 'test_run', 'dialog')
_MDAT = re.compile(r'"mdat"<timedate>=(?:0x[0-9A-Fa-f]+\s+)?"([^"]*)"')
_RETURNED = re.compile(rb':\s*returned\s+(-?\d+)')
_ACCT = re.compile(r'^\s*"acct"<blob>=(?:0x([0-9A-Fa-f]+)\s+"[^\n]*"|"([^"\n]*)")\s*$', re.M)
_PLAIN_NAME = re.compile(r'[^"\\\'\x00-\x1f\x7f]*')


def load_factor(per_core):
    """How much longer a Keychain call may take at this load per core: 1 under LOAD_EASY, LOAD_FACTOR_MAX at
    LOAD_HEAVY and above, a straight line between."""
    if per_core is None or isinstance(per_core, bool) or not isinstance(per_core, (int, float)) or \
            not math.isfinite(per_core) or per_core < LOAD_EASY:
        return 1.0
    return min(LOAD_FACTOR_MAX, 1.0 + (per_core - LOAD_EASY) * (LOAD_FACTOR_MAX - 1.0) / (LOAD_HEAVY - LOAD_EASY))


def system_load_per_core():
    """This Mac's one-minute load average per core, or None."""
    try:
        return os.getloadavg()[0] / float(os.cpu_count() or 1)
    except (OSError, AttributeError, ZeroDivisionError):
        return None


def busy_mac():
    """The Mac's one-minute load per core is above LOAD_HIGH. Then Switchboard's routine Keychain calls are spaced
    out (the sign-in watch's looks at a change of the Keychain or the settings alone, and the plain login file kept
    for Claude Code brought up to a change of the item's other parts), so it is not one more process queuing on
    `security` while Claude Code's own reads of its item, limited to 2 s, run out. Never what is urgent: a loss, a
    put-back, the plain login file's going, a renewal. False when the load can't be read."""
    try:
        per_core = (LOAD or system_load_per_core)()
    except Exception:  # noqa: BLE001 - a load that can't be read is no reason to wait
        return False
    return isinstance(per_core, (int, float)) and not isinstance(per_core, bool) and math.isfinite(per_core) and \
        per_core > LOAD_HIGH


def security_agent_up(run=subprocess.run, timeout=2.0):
    """True while macOS's SecurityAgent runs (it draws the Keychain's "wants to use your confidential information"
    dialogs), False when it does not, None when the process list can't be had. Bounded by `timeout`."""
    try:
        r = run([PS, '-axo', 'comm='], capture_output=True, timeout=timeout,
                env={'LC_ALL': 'C', 'PATH': '/usr/bin:/bin'})
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if r.returncode != 0:
        return None
    out = r.stdout.decode('utf-8', 'replace') if isinstance(r.stdout, bytes) else (r.stdout or '')
    return any(os.path.basename(line.strip()) == 'SecurityAgent' for line in out.splitlines())


DIALOG_UP = security_agent_up       # what end_children asks on the process's way out


def real_tool(tool, env=None):
    """Is `tool` the real /usr/bin/security: that path, a link or hard link to it, or a bare name that PATH (env's, as
    Popen looks it up, else this process's) finds there? Decided from the path alone; nothing is run."""
    path = tool
    if os.sep not in tool:
        path = shutil.which(tool, path=(os.environ if env is None else env).get('PATH', os.defpath))
        if path is None:
            return False
    try:
        return os.path.samefile(path, TOOL)
    except (OSError, ValueError):
        return False


def argv_note(data_dir):
    """{'count', 'last_at'}: logins written in security's arguments in that data folder so far, or None."""
    try:
        with open(os.path.join(data_dir, ARGV_NOTE)) as f:
            got = json.load(f)
    except (OSError, ValueError):
        return None
    return got if isinstance(got, dict) and isinstance(got.get('count'), int) and got['count'] > 0 else None


def stdin_line(service, account, hex_value, limit=STDIN_LIMIT, update=True):
    """The one `security -i` command line that writes the item, as Claude Code writes it, or None when the write must
    go in the arguments: the line would be longer than `limit` bytes, or a name holds a quote, a backslash or a control
    character (security's own line splitting is then not something to rely on). update False: no -U, so the command
    only creates the item and exits 45 when it is already there."""
    if not (_PLAIN_NAME.fullmatch(service) and _PLAIN_NAME.fullmatch(account)):
        return None
    line = 'add-generic-password %s-a "%s" -s "%s" -X "%s"\n' % ('-U ' if update else '', account, service, hex_value)
    return line if len(line.encode('utf-8')) <= limit else None


def item_account(out):
    """The account name in `security find-generic-password`'s attribute listing ("acct"<blob>="name", or the hex
    form it uses for bytes it can't print), or None when there is none to use."""
    m = _ACCT.search((out or b'').decode('utf-8', 'replace'))
    if m is None:
        return None
    if m.group(1):
        try:
            return bytes.fromhex(m.group(1)).decode('utf-8')
        except (ValueError, UnicodeDecodeError):
            return None
    return m.group(2) if m.group(2) else None


@dataclass(frozen=True)
class Done:
    """What a write or delete did. ok False: why ('timeout', 'busy', 'locked', 'denied', ...). A write that timed out
    may still land later (its helper was left to finish); the item stays 'busy' until it does. ended: it timed out
    while Claude Code's save lock was held and its helper was ended and has exited (or a signal ended its helper), so
    it can no longer land; whether it landed before that only a read can tell. untouched (with 'busy'): this call
    started no helper, and no earlier write or delete of this item is still out, so nothing of this process's can
    change the item after this answer. Not part of what makes two answers equal."""
    ok: bool
    why: Optional[str] = None
    ended: bool = False
    untouched: bool = field(default=False, compare=False)


class _Timeout(Exception):
    def __init__(self, why='timeout', ended=False):
        super().__init__(why)
        self.why = why
        self.ended = ended


class _Busy(Exception):
    def __init__(self, started=False):
        super().__init__('busy')
        self.started = started                  # a helper was started and ended at once (the process is on its way out)


class _NoTool(Exception):
    def __init__(self, why='no_tool'):
        super().__init__(why)
        self.why = why                          # 'test_run': the real tool, refused while the test mode is on


def _why(code, err):
    text = (err or b'').decode('utf-8', 'replace').lower()
    if 'interaction is not allowed' in text or 'user interaction' in text:
        return 'locked'
    if 'user canceled' in text or 'auth failed' in text or 'name or passphrase' in text:
        return 'denied'
    if 'default keychain' in text or 'no keychain' in text:
        return 'no_keychain'
    return _EXIT_WHY.get(code, 'unknown')


def _stdin_status(code, err):
    """The status of the one command a `security -i` call ran. security says "<command>: returned <status>" on its
    error stream when a command fails; that counts even if the call itself exited 0."""
    m = _RETURNED.search(err or b'')
    if m and code == 0:
        try:
            n = int(m.group(1))
        except ValueError:
            return 1
        return (n & 0xFF) or 1
    return code


def decode(out):
    """security -w prints the value and one newline; a value with bytes it can't print comes out as hex."""
    try:
        text = out.decode('utf-8')
    except UnicodeDecodeError:
        return None
    if text.endswith('\n'):
        text = text[:-1]
    if text and len(text) % 2 == 0 and re.fullmatch(r'[0-9a-fA-F]+', text):
        try:
            raw = bytes.fromhex(text).decode('utf-8')
            if raw.lstrip().startswith('{'):
                return raw
        except (ValueError, UnicodeDecodeError):
            pass
    return text


_CHILDREN = set()                   # helpers this process started and has not reaped yet (every Keychain object)
_CHILDREN_LOCK = threading.Lock()


def _child(proc, running):
    with _CHILDREN_LOCK:
        if running:
            _CHILDREN.add(proc)
        else:
            _CHILDREN.discard(proc)


def end_children(grace=END_GRACE_S, dialog=None):
    """End every helper this process started and has not reaped, including one left to finish after its deadline:
    SIGTERM to each, then SIGKILL to any still running `grace` seconds later. Called on the process's way out
    (ccfiles.release_all), so no helper outlives it. But none is ended while macOS shows a Keychain dialog (dialog(),
    by default DIALOG_UP, answers True): one of them may be drawing it, and ending it tears the dialog down (the
    module's first rule); they are then left to finish by themselves. How many were ended."""
    with _CHILDREN_LOCK:
        procs = [p for p in _CHILDREN if p.poll() is None]
    if not procs:
        return 0
    try:
        showing = (dialog or DIALOG_UP)()
    except Exception:  # noqa: BLE001 - not known is "no dialog": the helpers are ended
        showing = None
    if showing is True:
        return 0
    for p in procs:
        try:
            p.terminate()
        except OSError:
            pass
    until = time.monotonic() + grace
    for p in procs:
        try:
            p.wait(timeout=max(0.0, until - time.monotonic()))
        except Exception:  # noqa: BLE001 - TimeoutExpired: SIGKILL below
            pass
    for p in procs:
        if p.poll() is None:
            end_helper(p, grace)
    with _CHILDREN_LOCK:
        for p in procs:
            if p.poll() is not None:
                _CHILDREN.discard(p)
    return len(procs)


ccfiles.before_release(end_children)


def end_helper(proc, grace=END_GRACE_S):
    """End a helper: SIGTERM, then SIGKILL when it still runs `grace` seconds later, and reap it. True once it has
    exited. Never raises; its pipes are left to whoever reads them."""
    for signal_it in (proc.terminate, proc.kill):
        try:
            signal_it()
        except OSError:
            pass                                # it exited just now
        try:
            proc.wait(timeout=grace)
            return True
        except subprocess.TimeoutExpired:
            continue
        except Exception:  # noqa: BLE001 - ending a helper never raises
            break
    return proc.poll() is not None


class Keychain:
    """One per process (context.keychain()): the gate, the rests and the waiting helpers are per process.

    load: () -> one-minute load per core (or None), to lengthen deadlines under load; None: deadlines as set.
    dialog: () -> True while macOS shows a Keychain dialog (security_agent_up); None: a late answer is 'timeout'.
    The app's Keychain (context.keychain_for) passes both; tests pass fakes."""

    def __init__(self, tool=TOOL, read_deadline=READ_DEADLINE, write_deadline=WRITE_DEADLINE, backoff_s=BACKOFF_S,
                 mono=time.monotonic, wall=time.time, sleep=time.sleep, gate_path=None, max_detached=MAX_DETACHED,
                 log=None, searchable_ttl=SEARCHABLE_TTL, env=None, load=None, dialog=None,
                 stdin_limit=STDIN_LIMIT):
        self.tool = tool
        self.read_deadline = read_deadline
        self.write_deadline = write_deadline
        self.backoff_s = backoff_s
        self.gate_path = gate_path
        self.max_detached = max_detached
        self.searchable_ttl = searchable_ttl
        self.stdin_limit = stdin_limit
        self._mono, self._wall, self._sleep = mono, wall, sleep
        self._log = log or (lambda text: None)
        self._env = env
        self._load = load
        self._dialog = dialog
        self._gate = threading.Lock()
        self._lock = threading.Lock()           # guards everything below
        self._pending = {}                      # (service, account) -> (Popen, verb, since, why)
        self._rest = {}                         # (service, account) -> (until, why)
        self._rest_all = (0.0, None)
        self._good = {}                         # (service, account) -> (value, at)
        self._searchable = (None, -1e18)
        self.counts = dict((k, 0) for k in ('ok', 'absent', 'locked', 'denied', 'no_keychain', 'timeout', 'busy',
                                             'no_tool', 'test_run', 'unparseable', 'unknown', 'late', 'dialog',
                                             'argv'))
        self.last_good_at = None

    # ---------- deadlines ----------

    def _factor(self):
        if self._load is None:
            return 1.0
        try:
            return load_factor(self._load())
        except Exception:  # noqa: BLE001 - a load that can't be read leaves the deadlines as set
            return 1.0

    def deadlines(self):
        """(read, write) deadlines in seconds right now: as set, longer under load (never past READ_CAP / WRITE_CAP,
        or past what was set when that is longer still)."""
        f = self._factor()
        return (min(max(self.read_deadline, READ_CAP), self.read_deadline * f),
                min(max(self.write_deadline, WRITE_CAP), self.write_deadline * f))

    def _limit(self, deadline):
        """(deadline, more) for one call: as given, unless this thread holds Claude Code's save lock; then at most
        UNDER_LOCK_S and what is left of UNDER_LOCK_TOTAL_S for that hold, and more = {'end': True} for _run (its
        helper is ended at the deadline). Outside the lock more is empty, so _run is called as before."""
        held = ccfiles.held_save_lock()
        if held is None:
            return deadline, {}
        return min(deadline, UNDER_LOCK_S, UNDER_LOCK_TOTAL_S - held.held_for()), {'end': True}

    # ---------- reads ----------

    def read(self, service, account, fresh=False, interactive=False):
        """The item's value as a Read. fresh: never answer from the last good value (anything that will be written,
        spent, kept or compared). interactive: a person asked for this now, so an item resting after a failure is
        tried again (one waiting on a dialog still is not)."""
        key = (service, account)
        blocked = self._blocked(key, interactive)
        if blocked:
            self._count('busy' if blocked == 'busy' else None)
            return self._cached_or(key, fresh, blocked)
        try:
            deadline, more = self._limit(self.deadlines()[0])
            code, out, err = self._run(key, ['find-generic-password', '-a', account, '-s', service, '-w'],
                                       deadline, 'read', **more)
        except _Timeout as t:
            return self._cached_or(key, fresh, self._cut(t.why) if t.ended else self._fail(key, t.why))
        except _Busy:
            self._count('busy')
            return self._cached_or(key, fresh, 'busy')
        except _NoTool as t:
            return Read(UNREADABLE, why=self._fail(key, t.why))
        if code == 0:
            value = decode(out)
            if value is None:
                return Read(UNREADABLE, why=self._fail(key, 'unparseable'))
            self._ok(key, value)
            return Read(PRESENT, value=value, provenance='keychain')
        if code == 44:
            if self.searchable():
                self._ok(key, None)
                self._count('absent')
                return Read(ABSENT)
            return Read(UNREADABLE, why=self._fail(key, 'no_keychain'))
        return self._cached_or(key, fresh, self._fail(key, _why(code, err)))

    def exists(self, service, account):
        """Is the item there, without reading its secret (never shows a dialog). present: value is its modification
        date as security prints it (a cheap change signal), or ''."""
        key = (service, account)
        if self._pending_for(key) or self._too_many_pending():
            return Read(UNREADABLE, why='busy')
        try:
            deadline, more = self._limit(self.deadlines()[0])
            code, out, err = self._run(key, ['find-generic-password', '-a', account, '-s', service],
                                       deadline, 'exists', **more)
        except _Timeout as t:
            return Read(UNREADABLE, why=t.why)
        except _Busy:
            return Read(UNREADABLE, why='busy')
        except _NoTool as t:
            return Read(UNREADABLE, why=t.why)
        if code == 0:
            m = _MDAT.search(out.decode('utf-8', 'replace'))
            return Read(PRESENT, value=m.group(1) if m else '', provenance='keychain')
        if code == 44:
            return Read(ABSENT) if self.searchable() else Read(UNREADABLE, why='no_keychain')
        return Read(UNREADABLE, why=_why(code, err))

    def first_account(self, service):
        """The account name of one item of `service`, whichever it is, without reading its secret (never shows a
        dialog): present (value: the name), absent when the service has none, else unreadable. It finds an item no
        record names any more."""
        key = (service, '')
        if self._pending_for(key) or self._too_many_pending():
            return Read(UNREADABLE, why='busy')
        try:
            code, out, err = self._run(key, ['find-generic-password', '-s', service], self.deadlines()[0], 'exists')
        except _Timeout as t:
            return Read(UNREADABLE, why=t.why)
        except _Busy:
            return Read(UNREADABLE, why='busy')
        except _NoTool as t:                    # 'no_tool', or 'test_run' (the real tool, refused in test mode)
            return Read(UNREADABLE, why=t.why)
        if code == 0:
            name = item_account(out)
            return Read(UNREADABLE, why='unparseable') if name is None else Read(PRESENT, value=name)
        if code == 44:
            return Read(ABSENT) if self.searchable() else Read(UNREADABLE, why='no_keychain')
        return Read(UNREADABLE, why=_why(code, err))

    def searchable(self):
        """Is the login keychain in this process's search list? Asked once per searchable_ttl."""
        with self._lock:
            known, at = self._searchable
            if known is not None and self._mono() - at < self.searchable_ttl:
                return known
        try:
            deadline, more = self._limit(self.deadlines()[0])
            code, out, _ = self._run(('', 'list-keychains'), ['list-keychains', '-d', 'user'], deadline, 'list',
                                     **more)
        except (_Timeout, _Busy, _NoTool):
            return False                        # not known: 44 then reads as unreadable, never as absent
        # a keychain of the person's own (under a home folder's Library/Keychains, login or another one), not only
        # the system's: a process outside the login session sees just /Library/Keychains/System.keychain
        paths = [line.strip().strip('"') for line in out.decode('utf-8', 'replace').splitlines() if line.strip()]
        ok = code == 0 and any('/Library/Keychains/' in p and not p.startswith(('/Library/', '/System/'))
                               for p in paths)
        with self._lock:
            self._searchable = (ok, self._mono())
        return ok

    # ---------- writes ----------

    def write(self, service, account, value, interactive=False, create_only=False):
        """Create or replace the item with value (text). Done(ok, why). The value goes on security's standard input
        whenever its command line fits (see the module's docstring); never in the arguments when it does.

        create_only: only create the item; when it is already there nothing changes and the answer is
        Done(False, 'exists'). Of several such writes at one moment exactly one lands (measured on macOS 26.5).
        Exit 45 on an ordinary write is Done(False, 'collided'): another process saved the same item at that moment
        and macOS refused this one (the item holds one writer's whole value). Neither makes the item rest: nothing is
        wrong with the Keychain."""
        if not isinstance(value, str):
            raise TypeError('Keychain values are text')
        key = (service, account)
        waiting = self._pending_verb(key)
        if waiting is not None:                 # an earlier read still out changes nothing; a write or delete may
            return Done(False, 'busy', untouched=waiting not in ('write', 'delete'))
        if self._too_many_pending():
            return Done(False, 'busy', untouched=True)
        hex_value = value.encode('utf-8').hex()
        line = stdin_line(service, account, hex_value, self.stdin_limit, update=not create_only)
        if line is not None:
            args, data = ['-i'], line.encode('utf-8')
        else:
            args, data = ['add-generic-password'] + ([] if create_only else ['-U']) + \
                ['-a', account, '-s', service, '-X', hex_value], None
            self._count('argv')
            self._note_argv()
            self._log('Keychain: a %d-byte login for "%s" went in the arguments (its command line is too long for '
                      'standard input)' % (len(value.encode('utf-8')), service))
        try:
            deadline, more = self._limit(self.deadlines()[1])
            code, out, err = self._run(key, args, deadline, 'write', data=data, **more)
        except _Timeout as t:
            if t.ended:
                self._cut(t.why)
            else:
                self._fail(key, t.why)
            return Done(False, t.why, ended=t.ended)
        except _Busy as b:
            return Done(False, 'busy', untouched=not b.started)
        except _NoTool as t:
            return Done(False, self._fail(key, t.why))
        if code < 0:
            return self._signalled(key, 'write', code)
        if data is not None:
            code = _stdin_status(code, err)
        if code == 0:
            self._ok(key, value)
            return Done(True)
        if code == DUPLICATE_EXIT:
            self._count('exists' if create_only else 'collided')
            if not create_only:
                self._log('Keychain: another process saved "%s" at the same moment, so macOS refused this write '
                          '(exit 45)' % service)
            return Done(False, 'exists' if create_only else 'collided')
        return Done(False, self._fail(key, _why(code, err)))

    def delete(self, service, account):
        """Remove the item; an item already gone counts as done."""
        key = (service, account)
        waiting = self._pending_verb(key)
        if waiting is not None:
            return Done(False, 'busy', untouched=waiting not in ('write', 'delete'))
        try:
            deadline, more = self._limit(self.deadlines()[1])
            code, out, err = self._run(key, ['delete-generic-password', '-a', account, '-s', service],
                                       deadline, 'delete', **more)
        except _Timeout as t:
            if t.ended:
                self._cut(t.why)
            else:
                self._fail(key, t.why)
            return Done(False, t.why, ended=t.ended)
        except _Busy as b:
            return Done(False, 'busy', untouched=not b.started)
        except _NoTool as t:
            return Done(False, t.why)
        if code < 0:
            return self._signalled(key, 'delete', code)
        if code in (0, 44):
            with self._lock:
                self._good.pop(key, None)
            return Done(True)
        return Done(False, self._fail(key, _why(code, err)))

    # ---------- for doctor and the page ----------

    def state(self):
        with self._lock:
            now = self._mono()
            resting = sorted(k[0] for k, (until, _) in self._rest.items() if until > now)
            until_all, why_all = self._rest_all
            return {'counts': dict(self.counts), 'last_good_at': self.last_good_at,
                    'needs_approval_or_slow': sorted(k[0] for k in self._pending),
                    'waiting_on_dialog': sorted(k[0] for k, v in self._pending.items() if v[3] == 'dialog'),
                    'resting': resting, 'resting_all': why_all if until_all > now else None,
                    'searchable': self._searchable[0]}

    def forget(self):
        """Drop every cached value and rest (a person pressed "try again", or tests)."""
        with self._lock:
            self._good.clear()
            self._rest.clear()
            self._rest_all = (0.0, None)

    # ---------- inside ----------

    def _note_argv(self):
        """Count, beside the gate, one more login written in the arguments: doctor runs in a process of its own, and
        each process's counts are its own. Best effort; never stops a write."""
        if not self.gate_path:
            return
        path = os.path.join(os.path.dirname(self.gate_path), ARGV_NOTE)
        with self._lock:
            try:
                n = int((argv_note(os.path.dirname(self.gate_path)) or {}).get('count') or 0) + 1
                tmp = '%s.%d.tmp' % (path, os.getpid())
                with open(tmp, 'w') as f:
                    json.dump({'count': n, 'last_at': self._wall()}, f)
                os.replace(tmp, path)
            except (OSError, ValueError, TypeError):
                pass

    def _count(self, what):
        if what:
            with self._lock:
                self.counts[what] = self.counts.get(what, 0) + 1

    def _pending_for(self, key):
        """A helper for this item is still out (its reaper clears the entry once it has answered)."""
        with self._lock:
            return key in self._pending

    def _pending_verb(self, key):
        """What the helper still out for this item is doing ('read', 'exists', 'write', 'delete'), or None."""
        with self._lock:
            entry = self._pending.get(key)
        return entry[1] if entry is not None else None

    def _too_many_pending(self):
        with self._lock:
            return len(self._pending) >= self.max_detached

    def _blocked(self, key, interactive):
        """Why this item must not be asked now, or None."""
        with self._lock:
            entry = self._pending.get(key)
        if entry is not None:
            return entry[3]                     # 'dialog' while its helper waits on one, else 'timeout'
        if self._too_many_pending():
            return 'busy'
        if interactive:
            return None
        with self._lock:
            now = self._mono()
            until, why = self._rest.get(key, (0.0, None))
            if until > now:
                return why
            until, why = self._rest_all
            if until > now:
                return why
        return None

    def _cached_or(self, key, fresh, why):
        if not fresh:
            with self._lock:
                good = self._good.get(key)
            if good is not None and good[0] is not None:
                return Read(PRESENT, value=good[0], provenance='cache', degraded=True,
                            age_s=max(0.0, self._mono() - good[1]))
        return Read(UNREADABLE, why=why)

    def _ok(self, key, value):
        with self._lock:
            self._rest.pop(key, None)
            self._rest_all = (0.0, None)
            if value is not None:
                self._good[key] = (value, self._mono())
            else:
                self._good.pop(key, None)
            self.counts['ok'] += 1 if value is not None else 0
            self.last_good_at = self._wall()

    def _fail(self, key, why):
        with self._lock:
            until = self._mono() + self.backoff_s
            self._rest[key] = (until, why)
            if why in _GLOBAL:
                self._rest_all = (until, why)
            self.counts[why] = self.counts.get(why, 0) + 1
        self._log('Keychain: %s for "%s" (resting %.0f s)' % (why, key[0], self.backoff_s))
        return why

    def _cut(self, why):
        """A call ended at the short limit calls have while Claude Code's save lock is held: counted, and nothing
        rests. That limit is the lock's own, not a sign the Keychain is unwell, and a rest would refuse every read
        that is not a person's own for backoff_s, in the very seconds a lost login is looked for. (No pile of helpers
        can come of it: this one has exited, and the next call under the lock is ended the same way.)"""
        self._count(why)
        return why

    def _signalled(self, key, verb, code):
        """The answer for a write or delete whose helper was ended by a signal (a process on its way out ends its
        helpers: end_children; or something outside ended it). Like one ended at its deadline: it has exited, so it
        can no longer land, and whether it landed first only a read can tell. Nothing rests."""
        self._count('unknown')
        self._log('Keychain: the helper of a %s for "%s" was ended by a signal (%d)' % (verb, key[0], -code))
        return Done(False, 'timeout', ended=True)

    @contextmanager
    def _cross_gate(self, deadline_at):
        """One security call at a time across Switchboard's processes too (a lock file), within the call's deadline."""
        if not self.gate_path:
            yield
            return
        os.makedirs(os.path.dirname(self.gate_path), mode=0o700, exist_ok=True)
        fd = os.open(self.gate_path, os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except (BlockingIOError, PermissionError):
                    if self._mono() >= deadline_at:
                        raise _Busy()
                    self._sleep(0.02)
            yield
        finally:
            os.close(fd)

    def _run(self, key, args, deadline, verb, data=None, end=False):
        """(exit status, stdout, stderr) of one security call. data: bytes for its standard input (a write's command
        line), else nothing. Past the deadline: _Timeout('dialog' when SecurityAgent is up, else 'timeout'), and the
        helper is left to finish by itself. end: the call is made while Claude Code's save lock is held (_limit): no
        time left (the gates included), or the process letting go of everything (ccfiles.release_all), starts nothing
        (_Busy), and past the deadline the helper is ended, never left to finish (_Timeout('timeout', ended=True)),
        unless a Keychain dialog is showing: then it is left as outside the lock (_Timeout('dialog')), and it may
        still land once the dialog is answered. (A helper that has not exited after SIGKILL is left the same way,
        _Timeout('timeout'): "ended" is said only of a helper that is gone.) Calls made outside that lock still run
        on the way out (a renewal's answer must still be saved)."""
        if test_run() and real_tool(self.tool, self._env):
            self._log('Keychain: a test run tried to start the real %s (%s is set); it was not started'
                      % (TOOL, TEST_RUN_VAR))
            raise _NoTool('test_run')
        if end and (deadline <= 0 or ccfiles.stopping()):
            raise _Busy()
        deadline_at = self._mono() + deadline
        if not self._gate.acquire(timeout=max(0.01, deadline)):
            raise _Busy()
        try:
            with self._cross_gate(deadline_at):
                if end and self._mono() >= deadline_at:
                    raise _Busy()
                try:
                    # its own session: a caller that is killed with its whole group (the page ends a stuck loop
                    # that way) never takes a helper, and the dialog it may be drawing, with it
                    proc = subprocess.Popen([self.tool] + args,
                                            stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True,
                                            env=self._env, start_new_session=True)
                except OSError:
                    raise _NoTool()
                _child(proc, True)                          # until reaped (end_children ends it on the way out)
                if end and ccfiles.stopping():              # release_all is letting go of the lock right now
                    if end_helper(proc):
                        _child(proc, False)
                    raise _Busy(started=True)
                try:
                    out, err = proc.communicate(input=data, timeout=max(0.05, deadline_at - self._mono()))
                except subprocess.TimeoutExpired:
                    why = self._late_why()
                    if end and why != 'dialog':             # a helper that may be drawing a dialog is never ended
                        if self._end(key, proc, verb, deadline):
                            raise _Timeout('timeout', ended=True)
                        why = 'timeout'                     # it has not exited: not "ended", it may still land
                    self._detach(key, proc, verb, why)
                    raise _Timeout(why)
                _child(proc, False)
                return proc.returncode, out, err
        finally:
            self._gate.release()

    def _end(self, key, proc, verb, deadline):
        """End a helper that ran past its deadline while Claude Code's save lock was held, and say so once. True
        once it has exited (it can no longer land). False when it is still there after SIGTERM and SIGKILL (a process
        the system has not let go of yet): its pipes are left open for the caller, which leaves it like any late
        helper, so the item stays busy until it is gone."""
        exited = end_helper(proc)
        self._log('Keychain: a %s for "%s" ran past its %.1f s while Claude Code\'s save lock was held, so it was '
                  'ended%s' % (verb, key[0], deadline, '' if exited else ' (it has not exited yet)'))
        if not exited:
            return False
        _child(proc, False)
        for f in (proc.stdin, proc.stdout, proc.stderr):
            if f is not None:
                try:
                    f.close()
                except OSError:
                    pass
        return True

    def _late_why(self):
        """Why a call ran past its deadline: 'dialog' while macOS shows a Keychain dialog, else 'timeout'."""
        if self._dialog is None:
            return 'timeout'
        try:
            return 'dialog' if self._dialog() is True else 'timeout'
        except Exception:  # noqa: BLE001 - not known is "slow", never a crash
            return 'timeout'

    def _detach(self, key, proc, verb, why='timeout'):
        """Leave a helper that ran past its deadline to finish by itself (never kill it while this process runs: it may
        be a dialog; end_children ends it when the process leaves, unless a dialog is showing then)."""
        with self._lock:
            self._pending[key] = (proc, verb, self._mono(), why)

        def reap():
            try:
                out, _ = proc.communicate()
            except Exception:  # noqa: BLE001 - a reaper never raises
                out = b''
            if proc.poll() is not None:
                _child(proc, False)
            with self._lock:
                entry = self._pending.get(key)
                if entry is not None and entry[0] is proc:
                    del self._pending[key]
                self.counts['late'] += 1
                if verb == 'read' and proc.returncode == 0:
                    value = decode(out or b'')
                    if value:
                        self._good[key] = (value, self._mono())
                        self._rest.pop(key, None)
                        self.last_good_at = self._wall()
            self._log('Keychain: a %s for "%s" answered late (exit %s)' % (verb, key[0], proc.returncode))

        threading.Thread(target=reap, name='keychain-late-%s' % verb, daemon=True).start()
