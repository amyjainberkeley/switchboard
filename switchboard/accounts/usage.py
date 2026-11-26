"""One poller with a per-account pace and budget; readings with age, source and reason; Claude Code's status line as
the live source wherever Claude Code runs one.

Where numbers come from, cheapest first:
1. Claude Code's status line, only where Claude Code runs one, which is its terminal interface. Switchboard's status
   line saves the limit numbers Claude Code hands it (rate_limits.five_hour and .seven_day) to
   <data>/live/<session>.json. They are the active account's, they cost nothing, and they are as fresh as Claude
   Code's last reply. collect_status_lines() and feed_status_line() put them in the store.
2. Anthropic's usage endpoint (GET https://api.anthropic.com/api/oauth/usage with the account's access token, the
   header anthropic-beta: oauth-2025-04-20 and Switchboard's own user agent, "Switchboard/<version>"). The only source
   for the other accounts, for per-model weekly windows (Fable), and for the active account wherever no status line
   runs (Claude Code's VS Code panel, for one). Anthropic answers about 28 to 30 reads an hour per account before it
   refuses with 429, and a refusal leaves that account unread for 6 to 75 minutes, so Switchboard stays well under
   that: at most 23 reads in any hour per account on the schedule and on request together, and never more than 24
   (BUDGET) with the reads before a switch.

The pace, one rule for every read of an account: one read comes back every 3 minutes (PACE_EVERY), and up to 4 can be
saved up (PACE_SAVED). Poller._saved replays it from the sends of the last hour, which the store keeps, so a restart
changes nothing. A read on the schedule and a read someone asked for each need a saved read. A read before a switch
and the read after a wake do not (only a wait and the budget hold them: a switch on old numbers is worse than a late
read); they take a saved read all the same, so the reads after them wait. A pass also takes a scheduled read due in
the next 15 s (PACE_SOON) and sends it at that moment: the loop rests at least that long between passes, so it would
otherwise wait for the next one.

When each account is read on the schedule (Poller):
- the active account: not at all while its status line numbers are under 3 minutes old, unless a model window counts
  for switching (only the endpoint has it); otherwise every 5 minutes, and near a line every 90 s while saved reads
  last, then every 3 minutes for as long as it stays near: never a stop;
- every other account: every 5 minutes; while the active account is near a line, the same as the active account (a
  switch may come, and its target must be fresh); every 15 minutes while the active account is far from its lines
  and Claude Code is idle;
- an account at a limit at most every 10 minutes, and any account just after one of its windows resets;
- as often on a busy Mac (a read is one small request, and usage climbs fastest while the Mac is busy); slower on a
  low battery (x3 under 25%);
- right after the Mac wakes, every account once (the active first, the rest spaced out), never a burst.

A read someone asked for (poll(only=..., asked=True), as the page asks) goes at once for each account that has a saved
read and was not read in the last minute (ASK_FRESH); never inside a wait, and never in the hour after a 429's wait
ended. Several pages asking at once make one read.

When a read fails:
- 429 (Anthropic is limiting reads): Retry-After is honoured (a long one gets a 15-minute margin, 75 minutes at
  most in all); without one the wait doubles. The numbers are kept and marked frozen: a lower bound, because usage
  only grows inside a window. For an hour after such a wait ends, each read of that account waits 1.5x longer, and
  none is sent on request.
- no token to read with (a login expired or unreadable): nothing is sent; the active account is tried again in a
  minute (Claude Code renews its own login at its next request), another account in 5 minutes.
- anything else: a wait that doubles from 1 minute up to 30; a server's Retry-After honoured up to an hour.
- one account's failure never stops the others.

What is never believed:
- a lower number with no reset time while the window it would replace is still open (the "hollow" answer the server
  gives for idle accounts): it never replaces a real number, and never makes an old number look fresh;
- a window whose reset has passed: its number reads as not known (it refilled, and nothing has been read since),
  never as the old number and never as 0 until a reading says so;
- status line numbers that carry another account's open window (a Claude Code still on the old login for a few
  seconds after a switch).

The store, <data>/usage.json (schema 1), is small: the last number per window per account with its time and source,
the poller's own read state (its sends of the last hour among it), and 7 days of 15-minute samples. The old
usage-cache.json is never read. Wall-clock time is stored (it is what people read, and what Anthropic's reset times are
in); every wait the poller keeps is measured on the monotonic clock, so a clock change or a sleep never stretches or
cuts one short.
"""
import errno
import fcntl
import http.client
import json
import math
import os
import random
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone

from . import api
from .. import core
from .. import trust

SCHEMA = 1
STORE = 'usage.json'
STORE_LOCK = 'usage.json.lock'
LIVE_DIR = 'live'

ENDPOINT = 'https://api.anthropic.com/api/oauth/usage'
BETA = 'oauth-2025-04-20'
IO_TIMEOUT = 5.0             # each socket step of a read
READ_DEADLINE = 8.0          # a whole read, the name lookup included
TOKEN_DEADLINE = 10.0        # getting an account's token (a Keychain read in the account engine)
MAX_BODY = 256 * 1024
MAX_HANGING = 3              # reads still running past their deadline; while this many hang, no new one starts

FIVE, WEEK, MODEL = '5-hour', 'weekly', 'model:'

BUDGET = 24                  # endpoint reads per hour per account (Anthropic answers about 28-30)
PACE_EVERY = 180.0           # one read comes back this often: 20 an hour
PACE_SAVED = 4               # and this many can be saved up. 20 + 4 = BUDGET, so the pace alone never spends it
PACE_SLACK = 1e-9            # a saved read counted from a sum of fractions is still whole when it misses by this
ASK_FRESH = 60.0             # a read someone asked for is not sent for an account read this recently
PACE_SOON = 15.0             # a pass also takes a scheduled read due this soon, and sends it at its moment: the loop
                             # rests at least this long between passes, so it would otherwise wait a whole pass
HOUR = 3600.0
STATUS_LINE_FRESH = 180.0    # the active account's status line numbers this recent need no endpoint read
ACTIVE_EVERY = 300.0
NEAR_EVERY = 90.0
OTHERS_EVERY = 300.0
FAR_IDLE_EVERY = 900.0
EXHAUSTED_EVERY = 600.0
NEAR_POINTS = 15.0           # a window this close to its line is "near"
RESET_SLACK = 60.0           # read again this long after a window's reset
JITTER = 0.10
MAX_OTHERS = 2               # other accounts read in one pass
MAX_OTHERS_NEAR = 4          # ... while the active account is near a line
STAGGER = (0.3, 0.8)         # seconds between two reads of one pass
PASS_DEADLINE = 60.0

FAIL_BASE = 60.0
FAIL_CAP = 1800.0
RETRY_CAP_429 = 4500.0       # 75 minutes
RETRY_CAP = 3600.0
RETRY_MARGIN = 900.0         # added to a 429's Retry-After longer than 10 minutes: polling right on it re-blocks
RETRY_MARGIN_OVER = 600.0
ZERO_WAIT_429 = 300.0
ZERO_WAIT_429_MAX = 600.0
NO_TOKEN_WAIT = 300.0
NO_TOKEN_WAIT_ACTIVE = 60.0  # the account in use: Claude Code renews its own login at its next request
AIMD_WINDOW = 3600.0
AIMD_GROWTH = 1.5
AIMD_MIN = 360.0
AIMD_MAX = 1800.0

LIVE_FRESH = 600.0           # status line records older than this are not read
AHEAD = 5.0                  # a time this far past the clock is still "now"; further is not believed
SETTLE = 30.0                # after a switch, a running Claude Code may use the old login this long
SAME_WINDOW = 300.0          # two readings of one window carry reset times this close

SAMPLE_EVERY = 900
SAMPLE_KEEP = 7 * 86400
LOCK_WAIT = 3.0

NETWORK_KINDS = ('network', 'timeout')
NO_REQUEST_KINDS = ('no-access-token', 'token-expired')


# ---------- time ----------

def _awake_clock():
    """The monotonic clock every process on this Mac reads alike: seconds awake since it started up. A time saved by
    one process (a heartbeat, a stamp) is measured on it by another, so it cannot be time.monotonic() everywhere:
    before Python 3.10, on macOS, that one counts from zero in each process. Where the system does not have this
    clock by name, time.monotonic, which is one clock for all processes there."""
    raw = getattr(time, 'CLOCK_UPTIME_RAW', None)
    if raw is None:
        return time.monotonic
    try:
        time.clock_gettime(raw)
    except (OSError, ValueError):
        return time.monotonic
    return lambda: time.clock_gettime(raw)


_AWAKE = _awake_clock()


class Clock(object):
    """Wall time for showing and for Anthropic's reset times; monotonic time for every wait. On a Mac the monotonic
    clock stops while the Mac sleeps, so a duration measured on it is time the Mac was awake, and it reads alike in
    every process (_awake_clock), so one process can measure from a time another saved. Tests pass a fake."""

    def wall(self):
        return time.time()

    def mono(self):
        return _AWAKE()

    def boot(self):
        return boot_id()

    def sleep(self, seconds):
        time.sleep(max(0.0, seconds))

    def wait(self, event, seconds):
        """Wait up to `seconds` for `event`; True when it was set."""
        return event.wait(max(0.0, seconds))

    def join(self, thread, seconds):
        """Wait up to `seconds` for `thread`; True when it finished."""
        thread.join(max(0.0, seconds))
        return not thread.is_alive()


_BOOT = []


def boot_id():
    """This boot's id (macOS kern.bootsessionuuid), or None. Monotonic times compare only within one boot."""
    if not _BOOT:
        _BOOT.append(_read_boot_id())
    return _BOOT[0]


def _read_boot_id():
    try:
        import ctypes
        import ctypes.util
        libc = ctypes.CDLL(ctypes.util.find_library('c'))
        buf = ctypes.create_string_buffer(64)
        size = ctypes.c_size_t(64)
        if libc.sysctlbyname(b'kern.bootsessionuuid', buf, ctypes.byref(size), None, ctypes.c_size_t(0)) == 0 \
                and buf.value:
            return buf.value.decode('ascii', 'replace')
    except Exception:  # noqa: BLE001 - no boot id only means wall-clock ages
        pass
    try:
        with open('/proc/sys/kernel/random/boot_id') as f:
            return f.read().strip() or None
    except OSError:
        return None


def elapsed(wall_t, mono_t, boot, clock):
    """Seconds since something recorded with both clocks: awake time on the monotonic clock when it happened in this
    boot, wall time otherwise. Never negative; None when nothing was recorded."""
    if mono_t is not None and boot is not None and boot == clock.boot():
        d = clock.mono() - mono_t
        if d >= 0:
            return d
    if wall_t is None:
        return None
    return max(0.0, clock.wall() - wall_t)


def stamp(clock):
    """{'t', 'mono', 'boot'}: a moment recorded so it can be measured later from any process (elapsed)."""
    return {'t': round(clock.wall(), 3), 'mono': round(clock.mono(), 3), 'boot': clock.boot()}


def since_stamp(rec, clock):
    if not isinstance(rec, dict):
        return None
    return elapsed(num(rec.get('t')), num(rec.get('mono')), rec.get('boot'), clock)


def clock_text(t, now=None):
    """A time as people read it, local: '4:10 PM' today, 'Thu 4 PM' this week, 'Nov 20' further out."""
    now = time.time() if now is None else now
    lt, ln = time.localtime(t), time.localtime(now)
    hm = '%d%s %s' % (lt.tm_hour % 12 or 12, ':%02d' % lt.tm_min if lt.tm_min else '', 'AM' if lt.tm_hour < 12 else 'PM')
    days = (datetime(lt.tm_year, lt.tm_mon, lt.tm_mday) - datetime(ln.tm_year, ln.tm_mon, ln.tm_mday)).days
    if days <= 0:
        return hm
    if days < 7:
        return '%s %s' % (('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')[lt.tm_wday], hm)
    return '%s %d' % (('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')[lt.tm_mon - 1],
                      lt.tm_mday)


def span_text(seconds):
    """'45 s', '12 min', '2 h 5 min'."""
    s = int(max(0, seconds or 0))
    if s < 90:
        return '%d s' % s
    if s < 5400:
        return '%d min' % round(s / 60.0)
    return '%d h %d min' % (s // 3600, (s % 3600) // 60)


# ---------- numbers and text from outside ----------

def num(x):
    """A finite float, or None. JSON from outside can hold true, null, strings, NaN or Infinity."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    x = float(x)
    return x if math.isfinite(x) else None


_FRACTION = re.compile(r'^(.*T\d\d:\d\d:\d\d)\.(\d+)(.*)$')


def parse_iso(s):
    """Epoch seconds from an ISO 8601 time ('2026-11-16T20:39:00.123456+00:00', 'Z' allowed, any number of fractional
    digits), or None. A time without a zone is read as UTC."""
    if not isinstance(s, str) or not s.strip() or len(s) > 64:
        return None
    t = s.strip()
    if t.endswith('Z') or t.endswith('z'):
        t = t[:-1] + '+00:00'
    m = _FRACTION.match(t)
    if m:
        t = '%s.%s%s' % (m.group(1), (m.group(2) + '000000')[:6], m.group(3))
    try:
        d = datetime.fromisoformat(t)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return num(d.timestamp())
    except (ValueError, OverflowError, OSError):
        return None


_LABEL_OK = re.compile(r'^[A-Za-z0-9][A-Za-z0-9 ._+-]{0,39}$')


def clean_label(text):
    """A model's display name made safe for a window name ('Fable', 'Opus 5'), or None."""
    if not isinstance(text, str):
        return None
    t = ' '.join(text.split())
    return t if _LABEL_OK.match(t) else None


def parse_retry_after(value):
    """Seconds from a Retry-After header (the seconds form only; an HTTP date is ignored), clamped at 0 and at
    RETRY_CAP_429 (a server's "inf" or 86400 must never park an account for a day), or None."""
    if value is None:
        return None
    try:
        v = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if v != v:
        return None
    return max(0.0, min(v, RETRY_CAP_429))


class BadResponse(ValueError):
    """The endpoint's answer could not be read as usage."""


def _window(w):
    """(pct or None, resets_at or None) from one window object; None when the window is absent (null, missing, {});
    BadResponse when it is malformed. utilization null keeps the window with a number not known."""
    if w is None:
        return None
    if not isinstance(w, dict):
        raise BadResponse('a window is not an object')
    if not w:
        return None
    if 'utilization' not in w:
        raise BadResponse('a window has no utilization')
    u = w.get('utilization')
    if u is None:
        pct = None
    else:
        pct = num(u)
        if pct is None or pct < 0:
            raise BadResponse('a utilization is not a number')
    return pct, parse_iso(w.get('resets_at'))


def parse_usage(data):
    """{window name: (pct or None, resets_at or None)} from the usage endpoint's JSON.

    five_hour -> '5-hour', seven_day -> 'weekly' (either may be null: an Ultra account has no 5-hour window, a Team
    seat may have no weekly one); the limits array's weekly_scoped entries with a model name -> 'model:<name>', and
    its 'session' entry stands in for a missing 5-hour window. Spend (extra_usage) is never a window: it never counts
    for switching. A malformed five_hour or seven_day fails the whole answer (BadResponse): dropping one window would
    let the rest pass for a complete reading."""
    if not isinstance(data, dict):
        raise BadResponse('the answer is not an object')
    out = {}
    for key, name in (('five_hour', FIVE), ('seven_day', WEEK)):
        got = _window(data.get(key))
        if got is not None:
            out[name] = got
    limits = data.get('limits')
    for e in limits if isinstance(limits, list) else ():
        if not isinstance(e, dict):
            continue
        pct = num(e.get('percent'))
        if pct is None or pct < 0:
            continue
        resets = parse_iso(e.get('resets_at'))
        scope = e.get('scope') if isinstance(e.get('scope'), dict) else {}
        model = scope.get('model') if isinstance(scope.get('model'), dict) else {}
        label = clean_label(model.get('display_name'))
        if e.get('kind') == 'weekly_scoped' and label:
            out.setdefault(MODEL + label, (pct, resets))
        elif e.get('kind') == 'session' and FIVE not in out:
            out[FIVE] = (pct, resets)
    return out


def parse_rate_limits(rate_limits, at):
    """{window name: (pct, resets_at)} from the rate_limits Claude Code hands its status line. A window whose reset had
    passed when the numbers came is left out (Claude Code drops those itself; one still there has refilled)."""
    out = {}
    if not isinstance(rate_limits, dict):
        return out
    for key, name in (('five_hour', FIVE), ('seven_day', WEEK)):
        w = rate_limits.get(key)
        if not isinstance(w, dict):
            continue
        pct, resets = num(w.get('used_percentage')), num(w.get('resets_at'))
        if pct is None or pct < 0 or (resets is not None and resets <= at):
            continue
        out[name] = (min(100.0, pct), resets)
    return out


# ---------- files ----------

class LockBusy(Exception):
    """Another process held one of Switchboard's file locks for longer than LOCK_WAIT."""


@contextmanager
def held(name, wait=LOCK_WAIT):
    """An exclusive lock on <data>/<name> for the block, given up after `wait` seconds (LockBusy) rather than waiting
    forever on a process that hangs. The kernel lets it go if this process dies."""
    os.makedirs(core.data_dir(), mode=0o700, exist_ok=True)
    fd = os.open(core.data_path(name), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        end = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as e:
                if e.errno not in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                    raise
                if time.monotonic() >= end:
                    raise LockBusy(name)
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)


def write_atomic(path, data):
    """JSON written so a reader sees the old file or the new one, never a torn one: an owner-only temp file from the
    first byte, flushed to disk, renamed over the target (through a symlink to where it leads). Raises OSError (a full
    disk too); the old file is then untouched."""
    path = os.path.realpath(path)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    body = json.dumps(data, separators=(',', ':'), allow_nan=False, sort_keys=True).encode('utf-8')
    tmp = '%s.%d.%d.tmp' % (path, os.getpid(), threading.get_ident())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path):
    """A JSON object from a file, or None when it is missing, unreadable or not an object."""
    try:
        with open(path, 'rb') as f:
            data = json.loads(f.read().decode('utf-8'))
    except (OSError, ValueError, UnicodeDecodeError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def store_path():
    return core.data_path(STORE)


def blank():
    return {'v': SCHEMA, 'accounts': {}, 'samples': {}}


def load_store(path=None):
    """The readings store. Missing, corrupt or another schema: empty (never an error)."""
    data = read_json(path or store_path())
    if data is None or data.get('v') != SCHEMA:
        return blank()
    if not isinstance(data.get('accounts'), dict):
        data['accounts'] = {}
    if not isinstance(data.get('samples'), dict):
        data['samples'] = {}
    for aid in [a for a, v in data['accounts'].items() if not isinstance(v, dict)]:
        del data['accounts'][aid]
    return data


def _log(msg):
    try:
        core.log('usage: %s' % msg)
    except Exception:  # noqa: BLE001 - a log line never stops a reading
        pass


# ---------- windows ----------

def merge_window(old, new, now):
    """The window entry to keep: the newer one, except a lower number with no reset time while the old window is still
    open. That one is refused whole: it neither replaces the old number nor makes it look fresh."""
    if not isinstance(old, dict):
        return new
    if (num(new.get('at')) or 0) < (num(old.get('at')) or 0):
        return old
    if new.get('gone') or old.get('gone'):
        return new
    op, np_, ores = num(old.get('pct')), num(new.get('pct')), num(old.get('resets_at'))
    if np_ is not None and op is not None and np_ < op and new.get('resets_at') is None and ores is not None \
            and ores > now:
        return old
    return new


def _window_order(name):
    return (0, '') if name == FIVE else (1, '') if name == WEEK else (2, name.lower())


def window_entries(acct, now):
    """[(name, pct or None, resets_at, read_at, source)] for one store entry, in order. A window whose reset passed after
    it was read reads as not known: it has refilled and nothing has been read since."""
    out = []
    wins = acct.get('w') if isinstance(acct.get('w'), dict) else {}
    for name in sorted(wins, key=_window_order):
        w = wins[name]
        if not isinstance(w, dict) or w.get('gone'):
            continue
        pct, resets, at = num(w.get('pct')), num(w.get('resets_at')), num(w.get('at'))
        if resets is not None and resets <= now and (at is None or at < resets):
            pct = None
        src = w.get('src') if w.get('src') in ('status_line', 'endpoint') else 'endpoint'
        out.append((name, pct, resets, at, src))
    return out


def frozen_now(acct):
    """The endpoint's last answer for this account was a 429 and nothing has been read from it since."""
    ep = acct.get('ep') if isinstance(acct.get('ep'), dict) else {}
    return ep.get('kind') == 'http-429' and (num(ep.get('fails')) or 0) > 0


def plain_error(kind, until=None, now=None):
    """What a failed read means, in one plain phrase a person can act on (never only a code)."""
    if not kind:
        return None
    later = ' until %s' % clock_text(until, now) if until and now and until > now else ''
    texts = {
        'http-429': 'Anthropic is limiting usage reads%s; the numbers shown are the last ones read and can only be '
                    'higher' % later,
        'timeout': 'Anthropic did not answer in time',
        'network': 'no connection to Anthropic (is the Mac online?)',
        'tls-cert': "this Mac refused Anthropic's certificate (a proxy, a captive portal or a wrong clock)",
        'bad-response': 'Anthropic sent usage numbers Switchboard could not read',
        'oauth-not-allowed': "this account's organization does not allow Claude Code sign-in (a lapsed plan or a "
                             "policy); renewing the plan brings it back",
        'no-access-token': "this account's saved login can't be read right now; Switchboard tries again by itself",
        'token-expired': "Claude Code's login for this account has lapsed while idle; Claude Code renews it when it is "
                         "next used",
        'http-401': "Anthropic did not accept this account's login for reading usage",
        'http-403': 'Anthropic would not show usage for this account',
        'error': 'something unexpected went wrong reading usage',
    }
    if kind in texts:
        return texts[kind]
    if kind.startswith('http-5'):
        return 'Anthropic had a problem (%s); Switchboard tries again by itself' % kind.replace('http-', 'error ')
    if kind.startswith('http-'):
        return 'Anthropic answered %s' % kind.replace('http-', 'error ')
    return kind


# ---------- the network ----------

class NetError(Exception):
    """A read that got no HTTP answer. kind: 'network', 'timeout' or 'tls-cert'. Never carries a token."""

    def __init__(self, kind, detail=''):
        super().__init__(kind)
        self.kind = kind
        self.detail = detail


class Response(object):
    def __init__(self, status, headers, body):
        self.status = status
        self.headers = dict((str(k).lower(), v) for k, v in (headers or {}).items())
        self.body = body or b''


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is never followed: urllib would send the Bearer token on to the new address. The 3xx comes back as
    the answer, a failed read."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_get(url, headers, timeout):
    """One HTTPS GET. Returns a Response for any HTTP answer (error statuses too, a redirect included); raises NetError
    otherwise. The system's proxy settings apply."""
    req = urllib.request.Request(url, headers=headers, method='GET')
    tls = urllib.request.HTTPSHandler(context=trust.context())     # the one context of every secure connection
    try:
        with urllib.request.build_opener(_NoRedirect, tls).open(req, timeout=timeout) as r:
            return Response(r.status, r.headers, r.read(MAX_BODY))
    except urllib.error.HTTPError as e:
        try:
            body = e.read(16384)
        except Exception:  # noqa: BLE001 - the status is what counts
            body = b''
        return Response(e.code, e.headers or {}, body)
    except urllib.error.URLError as e:
        reason = e.reason
        if isinstance(reason, ssl.SSLCertVerificationError):
            raise NetError('tls-cert', _refused(url, reason))
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise NetError('timeout')
        raise NetError('network', type(reason).__name__)
    except ssl.SSLCertVerificationError as e:
        raise NetError('tls-cert', _refused(url, e))
    except (socket.timeout, TimeoutError):
        raise NetError('timeout')
    except (OSError, http.client.HTTPException, ValueError) as e:
        raise NetError('network', type(e).__name__)


def _refused(url, error):
    """A certificate refused: what was not trusted, in one sentence (trust.refusal)."""
    u = urllib.parse.urlparse(url)
    return trust.refusal(u.hostname, u.port or 443, error)


_HANGING = []                # reads still running past their deadline
_HANGING_TOKENS = []         # token fetches still running past theirs (another resource: the Keychain)
_HANGING_LOCK = threading.Lock()


def with_deadline(fn, deadline, clock, name='read', pool=None):
    """fn() in a worker thread, waited for at most `deadline` seconds (a name lookup or a stalled connection can hang
    far longer than any socket timeout). A worker past its deadline is left to finish on its own; while MAX_HANGING
    of its pool are still hanging, no new one starts. Raises NetError('timeout') when it did not finish."""
    pool = _HANGING if pool is None else pool
    with _HANGING_LOCK:
        pool[:] = [t for t in pool if t.is_alive()]
        if len(pool) >= MAX_HANGING:
            raise NetError('timeout', 'earlier calls are still hanging')
    box = {}

    def run():
        try:
            box['ok'] = fn()
        except BaseException as e:  # noqa: BLE001 - handed to the caller
            box['err'] = e
    t = threading.Thread(target=run, name='switchboard-%s' % name, daemon=True)
    t.start()
    if not clock.join(t, deadline):
        with _HANGING_LOCK:
            pool.append(t)
        raise NetError('timeout', 'no answer within %d s' % deadline)
    if 'err' in box:
        raise box['err']
    return box.get('ok')


def user_agent():
    return 'Switchboard/%s' % core.VERSION


def headers_for(token):
    return {'Authorization': 'Bearer %s' % token, 'anthropic-beta': BETA, 'User-Agent': user_agent(),
            'Accept': 'application/json'}


def _org_refused(body):
    """A 403 whose JSON says the organization does not allow OAuth sign-in. Read defensively: never raises."""
    try:
        d = json.loads(body.decode('utf-8', 'replace')) if body else None
        err = d.get('error') if isinstance(d, dict) else None
        details = err.get('details') if isinstance(err, dict) else None
        return isinstance(details, dict) and details.get('error_code') == 'oauth_not_allowed_for_organization'
    except Exception:  # noqa: BLE001
        return False


# ---------- the poller ----------

class _Plan(object):
    """One account's read schedule, in monotonic seconds (process memory; usage.json keeps the wall-clock copy)."""
    __slots__ = ('next', 'until', 'reads', 'lift', 'interval', 'fails', 'kind', 'wake', 'try_m', 'ok_m',
                 'retry_after')

    def __init__(self):
        self.next = None        # the next read is due (None: now)
        self.until = None       # nothing is sent before this (a failure's wait)
        self.reads = deque()    # reads sent in the last hour (the budget and the pace)
        self.lift = None        # when a 429's wait ended (reads are slower for an hour after, none on request)
        self.interval = None
        self.fails = 0
        self.kind = None
        self.wake = False       # read once after a wake, whatever the plan says
        self.try_m = None
        self.ok_m = None
        self.retry_after = None


class Context(object):
    """What the poller needs from the loop for one pass."""

    def __init__(self, active=None, order=(), lines=None, busy=False, battery_factor=1.0, settling=False):
        self.active = active                  # the account Claude Code is on (None: not known)
        self.order = tuple(order)             # accounts that may be read, in the person's order
        self.lines = dict(lines or {FIVE: 90.0, WEEK: 95.0})   # window name -> switch line
        self.busy = busy
        self.battery_factor = battery_factor  # slower on a low battery; a busy Mac reads as often (a read is one
                                              # small request, and usage climbs fastest while the Mac is busy)
        self.settling = settling              # the Mac just woke: nothing is read yet


class Report(object):
    """One pass: which accounts were read, which answered, which failed and how, and whether the Mac looked offline.
    held: the accounts a pass for named accounts (poll's `only`) did not read: read too recently, inside a wait, past
    the hour's budget, or (a read someone asked for) with no saved read."""

    def __init__(self):
        self.tried = []
        self.ok = []
        self.failed = {}
        self.held = []
        self.skipped = None

    @property
    def offline(self):
        """Every read sent failed for want of a connection (the Mac's network, not Anthropic)."""
        sent = [a for a in self.tried if self.failed.get(a) not in NO_REQUEST_KINDS]
        kinds = [self.failed.get(a) for a in sent]
        return bool(sent) and all(k in NETWORK_KINDS for k in kinds) and 'network' in kinds

    def as_dict(self):
        return {'tried': len(self.tried), 'ok': len(self.ok), 'failed': dict(self.failed), 'offline': self.offline,
                'skipped': self.skipped}


class Poller(object):
    """Reads the usage endpoint on the schedule above. One per auto process (the lease makes it the only one).

    token_for(account_id, fresh=False) -> api.Read with the access token (api.access_token); net(url, headers,
    timeout) -> Response or NetError (http_get). Both, and the clock, are swapped for fakes in tests."""

    def __init__(self, clock=None, net=None, token_for=None, path=None, rand=None):
        self.clock = clock or Clock()
        self.net = net or http_get
        self.token_for = token_for or (lambda aid, fresh=False: api.access_token(aid, fresh=fresh))
        self.path = path
        self.rand = rand or random.random
        self.plans = {}
        self._loaded = set()
        self._polling = threading.Lock()      # one pass at a time (a pass left running past its stage's deadline
                                              # must not share the schedule with the next)

    # schedule state

    def plan(self, aid, store=None):
        p = self.plans.get(aid)
        if p is None:
            p = self.plans[aid] = _Plan()
        if aid not in self._loaded:
            self._loaded.add(aid)
            acct = ((store or load_store(self.path))['accounts']).get(aid) or {}
            self._restore(p, acct.get('ep') if isinstance(acct.get('ep'), dict) else {})
            if self._wake_all:                # a wake asked for before this account's plan was loaded
                p.wake = True
                if p.kind in NETWORK_KINDS:
                    p.until = None
        return p

    def _restore(self, p, ep):
        """A schedule saved by an earlier process, moved onto this process's monotonic clock. Every time is clamped to
        what the rules could have planned, so a clock that moved between the two processes shifts one read, at most."""
        now_w, now_m = self.clock.wall(), self.clock.mono()

        def ahead(t, cap):
            t = num(t)
            return None if t is None else now_m + max(0.0, min(t - now_w, cap))
        p.next = ahead(ep.get('next'), AIMD_MAX * 2)
        p.until = ahead(ep.get('until'), RETRY_CAP_429)
        reads = ep.get('reads') if isinstance(ep.get('reads'), list) else []
        p.reads = deque(sorted(now_m - min(max(0.0, now_w - t), HOUR)
                               for t in (num(x) for x in reads) if t is not None and now_w - t <= HOUR))
        lift = num(ep.get('lift'))
        if lift is not None and 0 <= now_w - lift <= AIMD_WINDOW:
            p.lift = now_m - (now_w - lift)
        elif lift is not None and lift > now_w:
            p.lift = ahead(lift, RETRY_CAP_429)
        p.interval = num(ep.get('interval'))
        p.fails = int(num(ep.get('fails')) or 0)
        p.kind = ep.get('kind') if isinstance(ep.get('kind'), str) else None
        for key, attr in (('try_at', 'try_m'), ('ok_at', 'ok_m')):
            t = num(ep.get(key))
            setattr(p, attr, None if t is None else now_m - max(0.0, now_w - t))

    def _budget_left(self, p, now_m):
        while p.reads and now_m - p.reads[0] > HOUR:
            p.reads.popleft()
        return BUDGET - len(p.reads)

    def _saved(self, p, now_m):
        """How many reads this account has saved up (the pace), replayed from its sends of the last hour: full at the
        oldest, one back every PACE_EVERY seconds up to PACE_SAVED, one taken by each send (never below none: a read
        before a switch or after a wake goes with none saved, and the reads after it wait). With no send in the hour,
        PACE_SAVED. A read on the schedule or asked for needs one saved, so no hour holds more than 23 of those
        (PACE_SAVED + HOUR / PACE_EVERY - 1), however the two mix."""
        self._budget_left(p, now_m)               # (drops the sends older than an hour)
        saved, last = float(PACE_SAVED), None
        for t in p.reads:
            if last is not None:
                saved = min(float(PACE_SAVED), saved + max(0.0, t - last) / PACE_EVERY)
            saved, last = max(0.0, saved - 1.0), t
        if last is not None:
            saved = min(float(PACE_SAVED), saved + max(0.0, now_m - last) / PACE_EVERY)
        return saved

    def _has_saved(self, p, now_m):
        return self._saved(p, now_m) >= 1.0 - PACE_SLACK

    def _saved_back(self, p, now_m):
        """Monotonic time this account has a saved read again (now when it has one)."""
        saved = self._saved(p, now_m)
        return now_m if saved >= 1.0 - PACE_SLACK else now_m + (1.0 - saved) * PACE_EVERY

    def wake(self):
        """After a wake, a clock change or a return to the network: every account once, whatever its plan. A wait
        that was only for a connection is lifted (the connection is back); a 429's wait never is."""
        for p in self.plans.values():
            p.wake = True
            if p.kind in NETWORK_KINDS:
                p.until = None
        self._wake_all = True

    _wake_all = False

    def read_first(self, aid):
        """Claude Code was just moved to this account on older numbers: its next read goes as soon as it may, whatever
        its plan said, and the loop wakes for it (next_due). A wait, a 429's included, and the hour's budget still
        hold."""
        self.plan(aid).wake = True

    def next_due(self, aid):
        """Monotonic time this account's next read is due, or None (now, or never planned): its plan, a wait, and the
        moment it has a saved read again (or, past the hour's budget, the moment the oldest send is an hour old), so
        the loop wakes for it rather than a pass later."""
        p = self.plans.get(aid)
        if p is None:
            return None
        now_m = self.clock.mono()
        times = [t for t in (p.until,) if t is not None]
        if not p.wake:                            # a read after a wake goes whatever the plan and the pace say
            times += [t for t in (p.next,) if t is not None]
            if not self._has_saved(p, now_m):
                times.append(self._saved_back(p, now_m))
        if self._budget_left(p, now_m) <= 0:
            times.append(p.reads[0] + HOUR)
        return max(times) if times else None

    # deciding who is due

    def _sendable(self, p, now_m):
        if p.until is not None and now_m < p.until:
            return False                      # a failure's wait, Retry-After included: never cut short
        return self._budget_left(p, now_m) > 0

    def _askable(self, p, now_m):
        """A read someone asked for may go: a saved read is there, and no 429's wait ended in the last hour (Anthropic
        just refused this account; reads for it go only on the slowed schedule until that hour is over)."""
        if p.lift is not None and now_m <= p.lift + AIMD_WINDOW:
            return False
        return self._has_saved(p, now_m)

    def due_at(self, aid, ctx, store, escalate=False, soon=0.0):
        """Monotonic time this account's scheduled read is due, when that is at most `soon` seconds from now (now, or
        that moment); else None. escalate: the active account is near a line, so every other account read more than
        NEAR_EVERY ago is due whatever its plan said (a switch may come; its target must be fresh). A wait, the budget
        and the pace (a saved read) still hold; only the read after a wake goes without a saved read."""
        acct = store['accounts'].get(aid) or {}
        p = self.plan(aid, store)
        now_m, now_w = self.clock.mono(), self.clock.wall()
        if not self._sendable(p, now_m):
            return None
        if p.wake:
            return now_m
        if aid == ctx.active and self._status_line_fresh(acct, now_w) and not self._model_counts(ctx):
            return None                       # Claude Code's own numbers are fresh, and they are all that counts
        plan = now_m if p.next is None else p.next
        if escalate and aid != ctx.active:
            plan = min(plan, now_m if p.ok_m is None else p.ok_m + NEAR_EVERY)
        # and never before a saved read is back: near a line the saved reads go 90 s apart, then one every PACE_EVERY,
        # never a stop
        at = max(now_m, plan, self._saved_back(p, now_m))
        return at if at - now_m <= soon else None

    def due(self, aid, ctx, store, escalate=False):
        """Due for a read now (due_at)."""
        return self.due_at(aid, ctx, store, escalate) is not None

    @staticmethod
    def _status_line_fresh(acct, now_w):
        at = num(acct.get('sl_at'))
        return at is not None and -AHEAD <= now_w - at <= STATUS_LINE_FRESH

    @staticmethod
    def _model_counts(ctx):
        return any(n.startswith(MODEL) for n in ctx.lines)

    def near(self, aid, ctx, store, now_w):
        """Some window that counts is within NEAR_POINTS of its line (or over it)."""
        acct = store['accounts'].get(aid) or {}
        for name, pct, _, _, _ in window_entries(acct, now_w):
            line = _line_for(ctx.lines, name)
            if line is not None and pct is not None and pct >= line - NEAR_POINTS:
                return True
        return False

    def far(self, aid, ctx, store, now_w):
        """Every window that counts is known and more than NEAR_POINTS under its line."""
        acct = store['accounts'].get(aid) or {}
        seen = False
        for name, pct, _, _, _ in window_entries(acct, now_w):
            line = _line_for(ctx.lines, name)
            if line is None:
                continue
            if pct is None or pct >= line - NEAR_POINTS:
                return False
            seen = True
        return seen

    def order(self, ctx, store):
        """Who to read this pass, as [(account, the moment its read is due)]: the active account if due, then the due
        others, those due now first, the stalest of them first (never-tried first). Due means now or within
        PACE_SOON: the pass sends that read at its moment."""
        now_m, now_w = self.clock.mono(), self.clock.wall()
        out = []
        if ctx.active and ctx.active in ctx.order:
            at = self.due_at(ctx.active, ctx, store, soon=PACE_SOON)
            if at is not None:
                out.append((ctx.active, at))
        near = bool(ctx.active) and self.near(ctx.active, ctx, store, now_w)
        others = [(a, self.due_at(a, ctx, store, escalate=near, soon=PACE_SOON)) for a in ctx.order if a != ctx.active]
        others = [(a, at) for a, at in others if at is not None]
        others.sort(key=lambda x: (x[1] > now_m, x[1], self.plans[x[0]].try_m is not None, self.plans[x[0]].try_m or 0,
                                   ctx.order.index(x[0])))
        limit = len(others) if self._wake_all else (MAX_OTHERS_NEAR if near else MAX_OTHERS)
        return out + others[:limit]

    # one pass

    def poll(self, ctx, budget_s=PASS_DEADLINE, only=None, fresh_within=None, asked=False):
        """One pass of reads, every one under its own deadline and the whole under budget_s. `only` reads just those
        accounts: each unless it was read in the last `fresh_within` seconds, never past a wait or the budget. Before
        a switch that is all (a switch on old numbers is worse than a late read). `asked`: a read someone asked for
        (the page): it needs a saved read too, is never sent in the hour after a 429's wait ended, and skips an
        account read in the last ASK_FRESH seconds unless `fresh_within` says otherwise. Returns a Report; the store
        is written once at the end."""
        rep = Report()
        if ctx.settling:
            rep.skipped = 'settling'
            return rep
        if not self._polling.acquire(False):
            rep.skipped = 'busy'
            return rep
        if asked and fresh_within is None:
            fresh_within = ASK_FRESH
        try:
            return self._poll(ctx, budget_s, only, fresh_within, asked, rep)
        finally:
            self._polling.release()

    def _poll(self, ctx, budget_s, only, fresh_within, asked, rep):
        try:
            store = load_store(self.path)
        except Exception:  # noqa: BLE001 - load_store never raises, but a pass never dies of it
            store = blank()
        if only is None:
            todo = self.order(ctx, store)
        else:
            todo = []
            now_w, now_m = self.clock.wall(), self.clock.mono()
            for aid in dict.fromkeys(only):           # an account named twice is read once
                acct = store['accounts'].get(aid) or {}
                p = self.plan(aid, store)
                if fresh_within is not None and self._fresh(aid, acct, ctx, now_w, fresh_within):
                    rep.held.append(aid)
                elif self._sendable(p, now_m) and (not asked or self._askable(p, now_m)):
                    todo.append((aid, None))
                else:
                    rep.held.append(aid)
        start = self.clock.mono()
        updates = {}
        for i, (aid, at) in enumerate(todo):
            ahead = 0.0 if at is None else max(0.0, at - self.clock.mono())
            if self.clock.mono() - start + ahead + READ_DEADLINE + TOKEN_DEADLINE > budget_s:
                break                                  # a whole read (its token too) must fit what is left
            if i:
                lo, hi = STAGGER
                self.clock.sleep(lo + (hi - lo) * self.rand())
            if at is not None and at > self.clock.mono():
                self.clock.sleep(at - self.clock.mono())      # due a moment after the pass began: read then, not a
                                                               # whole pass later
            p = self.plans[aid]
            paced = asked if only is not None else not p.wake
            if paced and not self._has_saved(p, self.clock.mono()):
                if only is not None:                   # (the moment waited for is the saved read's; the bound of 23
                    rep.held.append(aid)               # an hour rests on this, so it is checked at the send itself)
                continue
            rep.tried.append(aid)
            try:
                kind, wins = self._read(aid, ctx, store, paced)
            except Exception as e:  # noqa: BLE001 - one account's failure never stops the pass
                _log('reading account failed unexpectedly: %s' % type(e).__name__)
                kind, wins = 'error', None
                self._failed(self.plans[aid], 'error', None)
            read_at = self.clock.wall()
            if kind is None:
                rep.ok.append(aid)
                _merge_endpoint(store['accounts'].setdefault(aid, {}), wins, read_at)   # later reads of this pass
            else:                                                                        # see it at once
                rep.failed[aid] = kind
            updates[aid] = (wins, read_at)
        if only is None:
            self._wake_all = False
        if updates:
            self._save(updates, ctx)
        return rep

    def _fresh(self, aid, acct, ctx, now_w, within):
        """Read in the last `within` seconds: the status line for the active account (when no model window counts),
        the endpoint otherwise."""
        if aid == ctx.active and not self._model_counts(ctx):
            at = num(acct.get('sl_at'))
            if at is not None and -AHEAD <= now_w - at <= within:
                return True
        ok = num((acct.get('ep') or {}).get('ok_at')) if isinstance(acct.get('ep'), dict) else None
        return ok is not None and -AHEAD <= now_w - ok <= within

    def _read(self, aid, ctx, store, paced=True):
        """One account's read: (None, windows) on success, (kind, None) on failure. The plan is updated either way.
        paced: the read needed a saved read (on the schedule, or asked for), and so does a second request inside it."""
        p = self.plan(aid, store)
        p.wake = False
        now_m = self.clock.mono()
        p.try_m = now_m
        tok = self._token(aid, False)
        if tok.kind != api.PRESENT or not tok.value:
            kind = 'token-expired' if tok.why == 'expired' else 'no-access-token'
            self._failed(p, kind, None, active=aid == ctx.active)
            return kind, None
        try:
            resp = self._get(p, tok.value)
            if resp.status == 401 and aid != ctx.active:
                again = self._token(aid, True)          # refreshed by the account engine's own transaction
                # The renewed token goes out at once only when the pace allows a second request; otherwise the next
                # read takes it, and no hour holds more than the pace allows.
                if again.kind == api.PRESENT and again.value and again.value != tok.value \
                        and self._sendable(p, self.clock.mono()) \
                        and (not paced or self._has_saved(p, self.clock.mono())):
                    resp = self._get(p, again.value)
        except NetError as e:
            self._failed(p, e.kind, None)
            return e.kind, None
        retry_after = parse_retry_after(resp.headers.get('retry-after'))
        if resp.status != 200:
            kind = 'oauth-not-allowed' if resp.status == 403 and _org_refused(resp.body) else 'http-%d' % resp.status
            self._failed(p, kind, retry_after)
            if kind == 'http-429':
                _log('account read refused with 429 (usage-endpoint budget reached; backing off %s)'
                     % span_text((p.until or now_m) - now_m))
            return kind, None
        try:
            wins = parse_usage(json.loads(resp.body.decode('utf-8')))
        except (ValueError, UnicodeDecodeError, RecursionError):
            self._failed(p, 'bad-response', None)
            return 'bad-response', None
        self._succeeded(aid, p, wins, ctx, store)
        return None, wins

    def _token(self, aid, fresh):
        try:
            r = with_deadline(lambda: self.token_for(aid, fresh=fresh), TOKEN_DEADLINE, self.clock, name='token',
                              pool=_HANGING_TOKENS)
        except NetError:
            return api.Read(api.UNREADABLE, why='timeout')
        except NotImplementedError:
            return api.Read(api.UNREADABLE, why='not built')
        except api.AccountsError as e:
            return api.Read(api.UNREADABLE, why=e.kind)
        except Exception as e:  # noqa: BLE001 - a token that can't be had is a failed read, never a crash
            return api.Read(api.UNREADABLE, why=type(e).__name__)
        return r if isinstance(r, api.Read) else api.Read(api.UNREADABLE, why='no answer')

    def _get(self, p, token):
        p.reads.append(self.clock.mono())         # the budget counts every request sent, answered or not
        return with_deadline(lambda: self.net(ENDPOINT, headers_for(token), IO_TIMEOUT), READ_DEADLINE, self.clock)

    def _failed(self, p, kind, retry_after, active=False):
        now_m = self.clock.mono()
        p.kind = kind
        p.retry_after = retry_after
        if kind in NO_REQUEST_KINDS:
            # Nothing was sent. The account in use is tried again in a minute: Claude Code renews its own login at its
            # next request, and looking again costs one Keychain read. Another account waits 5 minutes: getting its
            # token may renew its saved login, a request of its own.
            p.until = now_m + (NO_TOKEN_WAIT_ACTIVE if active else NO_TOKEN_WAIT)
            p.fails = max(p.fails, 1)
            return
        p.fails += 1
        computed = min(FAIL_BASE * 2.0 ** min(p.fails - 1, 16), FAIL_CAP)
        if kind == 'http-429':
            if retry_after is None:
                wait = max(computed, AIMD_MIN)
            elif retry_after <= 0:
                wait = min(max(computed, ZERO_WAIT_429), ZERO_WAIT_429_MAX)
            else:
                asked = retry_after + (RETRY_MARGIN if retry_after > RETRY_MARGIN_OVER else 0.0)
                wait = min(max(asked, computed), RETRY_CAP_429)
            p.lift = now_m + wait
        elif retry_after is not None and retry_after > 0:
            wait = min(max(retry_after, computed), RETRY_CAP)
        else:
            wait = computed
        p.until = now_m + wait

    def _succeeded(self, aid, p, wins, ctx, store):
        now_m, now_w = self.clock.mono(), self.clock.wall()
        p.fails, p.kind, p.retry_after, p.ok_m = 0, None, None, now_m
        p.until = None
        active = aid == ctx.active
        if active:
            base = NEAR_EVERY if _near_wins(wins, ctx.lines) else ACTIVE_EVERY
        elif ctx.active and self.near(ctx.active, ctx, store, now_w):
            base = NEAR_EVERY
        elif ctx.active and not ctx.busy and self.far(ctx.active, ctx, store, now_w):
            base = FAR_IDLE_EVERY
        else:
            base = OTHERS_EVERY
        interval = base
        if p.lift is not None and p.lift <= now_m <= p.lift + AIMD_WINDOW:
            interval = min(AIMD_MAX, max(interval, (p.interval or base) * AIMD_GROWTH, AIMD_MIN))
        counted = [(n, pct, r) for n, (pct, r) in wins.items() if _line_for(ctx.lines, n) is not None]
        exhausted = [r for _, pct, r in counted if pct is not None and pct >= 100]
        if exhausted:
            interval = max(interval, EXHAUSTED_EVERY)
        p.interval = interval
        interval *= max(1.0, ctx.battery_factor)
        wait = interval * (1.0 + JITTER * (2.0 * self.rand() - 1.0))
        resets = [r for _, _, r in counted if r is not None and r > now_w]
        if exhausted:
            known = [r for r in exhausted if r is not None]
            if known and len(known) == len(exhausted) and max(known) > now_w:
                wait = min(wait, max(known) - now_w + RESET_SLACK)
        elif resets:
            wait = min(wait, min(resets) - now_w + RESET_SLACK)
        # never planned before a saved read is back: near a line that turns "every 90 s" into the saved reads 90 s
        # apart, then one every PACE_EVERY for as long as it lasts
        p.next = now_m + max(NEAR_EVERY / 2.0, wait, self._saved_back(p, now_m) - now_m)

    def _save(self, updates, ctx):
        """Merge this pass into the store under its lock: the poller's own read state replaced, windows merged newest
        first. A store that can't be written (lock held too long, disk full) is skipped; the next pass tries again."""
        now_w, now_m = self.clock.wall(), self.clock.mono()

        def wall(m):
            return None if m is None else round(now_w + (m - now_m), 3)
        try:
            with held(STORE_LOCK):
                store = load_store(self.path)
                for aid, (wins, read_at) in updates.items():
                    p = self.plans[aid]
                    acct = store['accounts'].setdefault(aid, {})
                    ep = {'next': wall(p.next), 'until': wall(p.until),
                          'reads': [wall(m) for m in p.reads], 'lift': wall(p.lift), 'interval': p.interval,
                          'fails': p.fails, 'kind': p.kind, 'try_at': wall(p.try_m), 'ok_at': wall(p.ok_m)}
                    acct['ep'] = dict((k, v) for k, v in ep.items() if v not in (None, []))
                    if wins is not None:
                        _merge_endpoint(acct, wins, read_at)
                        _sample(store, aid, read_at)
                write_atomic(self.path or store_path(), store)
        except (LockBusy, OSError, ValueError) as e:
            _log('could not save readings this pass (%s)' % type(e).__name__)


def _line_for(lines, name):
    """The switch line a window is judged against, or None when it does not count (policy.window_line)."""
    from . import policy
    return policy.window_line(lines, name)


def _near_wins(wins, lines):
    for name, (pct, _) in wins.items():
        line = _line_for(lines, name)
        if line is not None and pct is not None and pct >= line - NEAR_POINTS:
            return True
    return False


def _merge_endpoint(acct, wins, now_w):
    """An endpoint reading into one store entry. It is the whole set of windows the account has: a 5-hour, weekly or
    model window it no longer reports is marked gone (newest first still applies)."""
    w = acct.setdefault('w', {})
    if not isinstance(w, dict):
        w = acct['w'] = {}
    for name, (pct, resets) in wins.items():
        new = {'pct': pct, 'resets_at': resets, 'at': round(now_w, 3), 'src': 'endpoint'}
        w[name] = merge_window(w.get(name), new, now_w)
    for name in [n for n in w if n not in wins]:
        w[name] = merge_window(w.get(name), {'gone': True, 'at': round(now_w, 3), 'src': 'endpoint'}, now_w)


def _sample(store, aid, now):
    """One 15-minute sample of an account's numbers (the latest in each slot), 7 days kept."""
    acct = store['accounts'].get(aid) or {}
    vals = dict((name, round(pct, 1)) for name, pct, resets, _, _ in window_entries(acct, now) if pct is not None)
    if not vals:
        return
    slot = int(now // SAMPLE_EVERY) * SAMPLE_EVERY
    rows = store['samples'].get(aid)
    rows = [r for r in rows if isinstance(r, list) and len(r) == 2 and num(r[0]) is not None
            and now - SAMPLE_KEEP <= r[0] <= now + SAMPLE_EVERY] if isinstance(rows, list) else []
    if rows and rows[-1][0] == slot:
        rows[-1] = [slot, vals]
    else:
        rows.append([slot, vals])
    store['samples'][aid] = rows


# ---------- reading the store, and the status line ----------

def readings(now=None, path=None):
    """{account id: api.Reading} from the store. Never reads the network or the Keychain. checked_at: the last answer
    for the account (the endpoint's ok_at, the status line's sl_at) when it is later than read_at, the oldest number's
    time: a window whose newer answer was refused (merge_window) keeps its time, yet the account was just checked."""
    now = time.time() if now is None else now
    store = load_store(path)
    out = {}
    for aid, acct in store['accounts'].items():
        ep = acct.get('ep') if isinstance(acct.get('ep'), dict) else {}
        wins = tuple(api.Window(name, pct, resets, at, src) for name, pct, resets, at, src in window_entries(acct, now))
        times = [w.read_at for w in wins if w.read_at is not None]
        read_at = min(times) if times else num(ep.get('ok_at'))
        newest = max(wins, key=lambda w: w.read_at or 0) if wins else None
        source = newest.source if newest else ('endpoint' if num(ep.get('ok_at')) else 'none')
        failing = (num(ep.get('fails')) or 0) > 0 and ep.get('kind')
        error = plain_error(ep.get('kind'), num(ep.get('until')), now) if failing else None
        answered = [t for t in (read_at, num(ep.get('ok_at')), num(acct.get('sl_at')))
                    if t is not None and t <= now + AHEAD]
        out[aid] = api.Reading(aid, wins, read_at, source, frozen_now(acct), error,
                               checked_at=max(answered) if answered else None)
    return out


def samples(aid, path=None):
    """[(slot start, {window: pct})] for the last 7 days, oldest first."""
    rows = load_store(path)['samples'].get(aid)
    return [(r[0], dict(r[1])) for r in rows if isinstance(r, list) and len(r) == 2 and isinstance(r[1], dict)] \
        if isinstance(rows, list) else []


def _fits(store, aid, wins, now):
    """The status line's windows are this account's: one its own reading has still open carries the same reset time,
    and none carries another account's open window instead (the numbers a running Claude Code kept from before a
    switch). A window without a reset time says nothing either way."""
    for name, (_, resets) in wins.items():
        if resets is None or resets <= now:
            continue
        own = ((store['accounts'].get(aid) or {}).get('w') or {}).get(name)
        mine = num(own.get('resets_at')) if isinstance(own, dict) and not own.get('gone') else None
        if mine is not None and mine > now:
            if abs(resets - mine) > SAME_WINDOW:
                return False
            continue
        for other, acct in store['accounts'].items():
            if other == aid or not isinstance(acct.get('w'), dict):
                continue
            theirs = acct['w'].get(name)
            t = num(theirs.get('resets_at')) if isinstance(theirs, dict) and not theirs.get('gone') else None
            if t is not None and t > now and abs(resets - t) <= SAME_WINDOW:
                return False
    return True


def feed_status_line(account_id, rate_limits, at, now=None, path=None):
    """Claude Code's status line numbers for the account it runs on, into the store. True when a number was taken.
    Refused: numbers older than 10 minutes or dated past the clock, windows already refilled, and numbers that carry
    another account's open window."""
    now = time.time() if now is None else now
    at = num(at)
    if not isinstance(account_id, str) or not account_id or at is None or at > now + AHEAD or now - at > LIVE_FRESH:
        return False
    wins = parse_rate_limits(rate_limits, at)
    if not wins:
        return False
    try:
        with held(STORE_LOCK):
            store = load_store(path)
            if not _fits(store, account_id, wins, now):
                _log('status line numbers carry another account\'s window; not taken')
                return False
            acct = store['accounts'].setdefault(account_id, {})
            if at <= (num(acct.get('sl_at')) or 0.0):
                return False                  # these numbers (or newer) were taken already: nothing to write
            w = acct.setdefault('w', {})
            took = False
            for name, (pct, resets) in wins.items():
                new = {'pct': pct, 'resets_at': resets, 'at': round(at, 3), 'src': 'status_line'}
                if merge_window(w.get(name), new, now) is new:
                    w[name] = new
                    took = True
            acct['sl_at'] = max(num(acct.get('sl_at')) or 0.0, round(at, 3))
            if took:
                _sample(store, account_id, now)
            write_atomic(path or store_path(), store)
            return took
    except (LockBusy, OSError, ValueError) as e:
        _log('could not take status line numbers (%s)' % type(e).__name__)
        return False


def live_records(now, folder=None):
    """The status line's saved records that may be the active account's: from Claude Code on the default profile, not
    older than 10 minutes, not dated past the clock. [{'at', 'account' (the email the line showed), 'rate_limits'}],
    newest first."""
    folder = folder or core.data_path(LIVE_DIR)
    out = []
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return out
    for e in entries:
        try:
            if not e.name.endswith('.json') or not e.is_file(follow_symlinks=False) \
                    or now - e.stat(follow_symlinks=False).st_mtime > LIVE_FRESH + AHEAD:
                continue
        except OSError:
            continue
        rec = read_json(e.path) or {}
        at = num(rec.get('at'))
        if rec.get('v') != 1 or at is None or rec.get('own_profile') is True \
                or not isinstance(rec.get('rate_limits'), dict) or not -AHEAD <= now - at <= LIVE_FRESH:
            continue
        who = rec.get('account')
        out.append({'at': at, 'account': who.lower() if isinstance(who, str) else None, 'rate_limits': rec['rate_limits']})
    return sorted(out, key=lambda r: -r['at'])


def collect_status_lines(active_id, live_since=None, active_email=None, now=None, folder=None, path=None):
    """Feed the active account the newest status line numbers that are surely its own: newer than the moment it became
    the active account (plus SETTLE, for a Claude Code that kept the old login a little longer), and shown under its
    name when the line named one. True when numbers were taken."""
    if not active_id:
        return False
    now = time.time() if now is None else now
    since = (num(live_since) or 0.0) + SETTLE
    want = active_email.lower() if isinstance(active_email, str) and active_email else None
    for rec in live_records(now, folder):
        if rec['at'] <= since:
            break
        if want and rec['account'] and rec['account'] != want:
            continue
        return feed_status_line(active_id, rec['rate_limits'], rec['at'], now=now, path=path)
    return False


def forget_account(account_id, path=None):
    """Drop one removed account's readings and samples (roster.remove)."""
    store = load_store(path)
    keep = (set(store['accounts']) | set(store['samples'])) - set([account_id])
    return forget(keep, path)


def forget(keep_ids, path=None):
    """Drop readings of accounts no longer saved."""
    keep = set(keep_ids)
    try:
        with held(STORE_LOCK):
            store = load_store(path)
            gone = [a for a in store['accounts'] if a not in keep] + [a for a in store['samples'] if a not in keep]
            if not gone:
                return 0
            for a in gone:
                store['accounts'].pop(a, None)
                store['samples'].pop(a, None)
            write_atomic(path or store_path(), store)
            return len(set(gone))
    except (LockBusy, OSError, ValueError):
        return 0
