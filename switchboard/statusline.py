"""Switchboard's line at the bottom of every Claude Code window.

    ● ana@work.example on Opus · 51% left · refills Sun 2 PM · next: ana@home.example

Claude Code runs this command on every refresh, hands it a JSON object on
stdin, and shows the one line it prints. The account names come from
the small file Switchboard writes for it (statusline.json). The numbers and the model's name
(`model.display_name` in that JSON) come from Claude Code itself.

It has to be quick and must never fail loudly. On any problem it prints a short
plain line and exits 0, because Claude Code shows nothing at all when a status
line command exits with any other code.

Each run also saves the limit numbers Claude Code handed it to
~/.switchboard/live/<session id>.json. That is how Switchboard learns the active
account's numbers without asking anyone (server.apply_live_numbers). Only numbers are
saved, never any text from the conversation. A Claude Code on a profile of its own
(CLAUDE_CONFIG_DIR away from ~/.claude) marks its record, since it may be another account.

What this relies on, from https://code.claude.com/docs/en/statusline (checked 2026-11-13):
- "`rate_limits.five_hour.used_percentage`, `rate_limits.seven_day.used_percentage` |
  Percentage of the 5-hour or 7-day rate limit consumed, from 0 to 100"
- "`rate_limits.five_hour.resets_at`, `rate_limits.seven_day.resets_at` |
  Unix epoch seconds when the 5-hour or 7-day rate limit window resets"
- "`rate_limits`: appears only for claude.ai Pro, Max, and Ultra subscribers, [...] Even then, it
  appears only after the first API response in the session. Each window (`five_hour`, `seven_day`,
  `spend_limit`) may be independently absent, and Claude Code drops a window once its `resets_at`
  time passes."
- "Read the `COLUMNS` and `LINES` environment variables instead. Claude Code sets these to the
  current terminal dimensions before running your script."
- "Claude Code displays your script's output only after the script exits with code 0"
- "The `command` field runs in a shell"

Speed: the work itself takes about 1.5 ms. What costs time is starting Python and importing
modules, so the hot path imports only os, sys, time and the C half of the json module (`_json`).
The json package itself pulls in `re`, which costs ~45 ms on Apple's Python 3.9.

This file imports nothing from the rest of Switchboard, so it starts fast and runs on its own:

    python3 -I -S /path/to/switchboard/statusline.py < input.json

Turning it on or off is a separate, explicit step: install_plan() and uninstall_plan() describe
the exact change to Claude Code's settings file, and apply() makes it. It goes on by itself once,
at the first run, and never over a status line of the person's own (keep.statusline_by_itself).
Its entry asks Claude Code to run it every minute even while a session is quiet ("refreshInterval":
60, REFRESH); a line Switchboard put in before it did gets that at the next first run or page start
(every_minute), only while it is still exactly what Switchboard put there. Run every minute, a
quiet session hands over the same numbers again: its record then keeps the time they came (save_live).
"""
import os
import sys
import time

DOT = '●'
SEP = ' · '
GREEN, ORANGE, RED, GREY, RESET = '\033[32m', '\033[38;5;208m', '\033[31m', '\033[90m', '\033[0m'
FALLBACK = 'Switchboard: no status right now'
MARGIN = 2                   # columns Claude Code keeps for itself on the status row
LIVE_MAX_AGE = 24 * 3600     # live files older than this are removed
PRUNE_EVERY = 3600           # look for old live files at most once an hour
AHEAD = 5                    # a file dated further than this past the clock is out of date, not new
WINDOWS = ('five_hour', 'seven_day')
DAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
ID_CHARS = frozenset('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.')
MARK = '# switchboard statusline'
REFRESH = 60                 # seconds: Claude Code runs the line again this often, even while a session is quiet


# ---------- where things are ----------

def data_dir():
    """Switchboard's own folder: $SWITCHBOARD_HOME, else ~/.switchboard (the same rule as core.py)."""
    return os.environ.get('SWITCHBOARD_HOME') or os.path.join(os.path.expanduser('~'), '.switchboard')


def state_path():
    """Where the names come from: the small file Switchboard's server writes for this line on every tick
    (the account in use, the next one, their names), shaped like its state file so either reads the same.
    $SWITCHBOARD_STATE, else ~/.switchboard/statusline.json."""
    return os.environ.get('SWITCHBOARD_STATE') or os.path.join(data_dir(), 'statusline.json')


def live_dir():
    return os.path.join(data_dir(), 'live')


def record_path():
    """Where install keeps the status line that was there before, so uninstall can put it back."""
    return os.path.join(data_dir(), 'statusline-install.json')


NOTIFY_RECORD = 'notify-hooks-install.json'     # the notification hooks' record (notify_hooks.record_path)
SHARED = ('file_was', 'inner')                  # what the first of the two to go into a file hands the second


def _notify_records():
    """The notification hooks' record, read as a file (this module imports nothing of Switchboard's)."""
    import json
    try:
        with open(os.path.join(data_dir(), NOTIFY_RECORD)) as fh:
            data = json.load(fh)
        return dict(data['installs']) if isinstance(data.get('installs'), dict) else {}
    except (OSError, ValueError, AttributeError, KeyError, TypeError):
        return {}


def settings_path():
    """Claude Code's user settings file: $CLAUDE_CONFIG_DIR/settings.json, else ~/.claude/settings.json."""
    base = os.environ.get('CLAUDE_CONFIG_DIR') or os.path.join(os.path.expanduser('~'), '.claude')
    return os.path.join(base, 'settings.json')


# ---------- JSON on the hot path ----------

class _Ctx(object):
    """What json's C scanner reads from json.JSONDecoder, with the decoder's defaults."""
    strict = True
    object_hook = None
    object_pairs_hook = None
    parse_float = float
    parse_int = int
    parse_constant = float          # 'NaN', 'Infinity', '-Infinity', as json.loads reads them


def _loads(raw):
    """json.loads, through the same C scanner json uses, without importing the json package."""
    text = raw.decode('utf-8') if isinstance(raw, bytes) else raw
    try:
        import _json
        scan = _json.make_scanner(_Ctx())
    except Exception:
        import json
        return json.loads(text)
    i = _skip(text, 0)
    try:
        obj, end = scan(text, i)
    except StopIteration:
        raise ValueError('no JSON value')
    if _skip(text, end) != len(text):
        raise ValueError('extra text after the JSON value')
    return obj


def _dumps(x):
    """Compact JSON for the live record: dicts, lists, strings, finite numbers, booleans and None."""
    if x is None:
        return 'null'
    if x is True or x is False:
        return 'true' if x else 'false'
    if isinstance(x, int):
        return int.__repr__(x)
    if isinstance(x, float):
        if x != x or x in (float('inf'), float('-inf')):
            raise ValueError('not a finite number')
        return float.__repr__(x)
    if isinstance(x, str):
        try:
            from _json import encode_basestring_ascii
        except ImportError:
            import json
            return json.dumps(x)
        return encode_basestring_ascii(x)
    if isinstance(x, dict):
        return '{' + ','.join(_dumps(str(k)) + ':' + _dumps(v) for k, v in x.items()) + '}'
    if isinstance(x, (list, tuple)):
        return '[' + ','.join(_dumps(v) for v in x) + ']'
    raise TypeError('cannot write %s as JSON' % type(x).__name__)


def _skip(text, i):
    while i < len(text) and text[i] in ' \t\n\r':
        i += 1
    return i


# ---------- reading ----------

def read_state(path=None):
    try:
        with open(path or state_path(), 'rb') as f:
            st = _loads(f.read())
        return st if isinstance(st, dict) else {}
    except (OSError, ValueError, RecursionError):
        return {}


def _num(x):
    """A finite number, or None. JSON from outside can hold true, null, strings, NaN or Infinity."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    x = float(x)
    return x if x == x and -1e15 < x < 1e15 else None


def _clean(text, limit=24):
    """A name made safe to print: no control characters (they could move the cursor), not too long."""
    text = ''.join(c for c in text if c >= ' ' and c != '\x7f' and not '\x80' <= c <= '\x9f').strip()
    return text if len(text) <= limit else text[:limit - 1] + '…'


KEYISH = frozenset('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_=+/.')


def looks_secret(text):
    """A token or a key, not a name: Anthropic's own shape, or one long unbroken run of key characters with
    no @."""
    t = text.strip()
    return t.startswith('sk-ant-') or t.startswith('eyJ') and t.count('.') == 2 or \
        (len(t) >= 40 and '@' not in t and set(t) <= KEYISH)


def short_name(st, who):
    """The name people see for an account: its email, as on every other surface (core.name_of). `who` may also
    already be a name, which is returned as it is. Long enough for an email; still cut, so nothing huge prints.
    Something shaped like a token is never printed."""
    if not isinstance(who, str) or not who.strip() or looks_secret(who):
        return ''
    return _clean(who.strip(), 64)


# ---------- how old the names are, and whose a separate profile is ----------

STALE_AFTER = 600            # seconds: names the page has not renewed for this long say how old they are
OWN_MAX = 256 * 1024         # a separate profile's own config larger than this is not read on every refresh


def names_age(st, now):
    """Seconds since the page last renewed statusline.json ('at'), or None: not known, or dated past the clock (the
    clock was set back: a time ahead is never trusted)."""
    at = _num(st.get('at'))
    if at is None or at - now > AHEAD:
        return None
    return max(0.0, now - at)


def age_words(seconds):
    m = int(seconds // 60)
    return '%d min' % m if m < 120 else '%d h' % (m // 60)


def own_account(env):
    """The email a Claude Code on a profile of its own (own_profile) is signed in to, from that profile's own config:
    $CLAUDE_CONFIG_DIR/.config.json when it is a file with something in it (Claude Code's own rule), else
    $CLAUDE_CONFIG_DIR/.claude.json, its oauthAccount.emailAddress. '' when it can't be told quickly (no file, a big
    one, not JSON)."""
    d = env.get('CLAUDE_CONFIG_DIR')
    if not d:
        return ''
    d = os.path.expanduser(d)
    for name in ('.config.json', '.claude.json'):
        p = os.path.join(d, name)
        try:
            st = os.stat(p)
        except OSError:
            continue
        if name == '.config.json' and (not os.path.isfile(p) or st.st_size == 0):
            continue
        if st.st_size > OWN_MAX:
            return ''
        try:
            with open(p, 'rb') as f:
                cfg = _loads(f.read())
        except (OSError, ValueError, RecursionError):
            return ''
        acct = cfg.get('oauthAccount') if isinstance(cfg, dict) else None
        email = acct.get('emailAddress') if isinstance(acct, dict) else None
        return email if isinstance(email, str) else ''
    return ''


def windows(data, now):
    """{'five_hour'|'seven_day': (used percent 0-100, resets_at or None)} for the windows still open."""
    rl = data.get('rate_limits')
    out = {}
    if not isinstance(rl, dict):
        return out
    for name in WINDOWS:
        w = rl.get(name)
        if not isinstance(w, dict):
            continue
        used, resets = _num(w.get('used_percentage')), _num(w.get('resets_at'))
        if used is None or (resets is not None and resets <= now):
            continue                    # no number, or that window has already refilled
        out[name] = (min(100.0, max(0.0, used)), resets)
    return out


def pick(wins):
    """(name, window) to show: the weekly one, unless the five-hour one has less left."""
    week, five = wins.get('seven_day'), wins.get('five_hour')
    if five and (not week or five[0] > week[0]):
        return 'five_hour', five
    if week:
        return 'seven_day', week
    return None, None


def left_of(used):
    """Whole percent left, rounded down so it never claims more than there is."""
    return int(100.0 - used)


def when(ts, now=None):
    """A refill time in local time, as short as reads clearly: '4:30 PM' today, 'Sun 2 PM' within
    the week, 'Nov 20' further out."""
    now = time.time() if now is None else now
    t = time.localtime(int(round(ts / 60.0)) * 60)
    n = time.localtime(now)
    clock = '%d%s %s' % (t.tm_hour % 12 or 12, ':%02d' % t.tm_min if t.tm_min else '',
                         'AM' if t.tm_hour < 12 else 'PM')
    days = _day_number(t) - _day_number(n)
    if days <= 0:
        return clock
    if days < 7:
        return '%s %s' % (DAYS[t.tm_wday], clock)
    return '%s %d' % (MONTHS[t.tm_mon - 1], t.tm_mday)


def _day_number(t):
    """Days since a fixed point for a local date, so two dates subtract across months and years."""
    y, m = (t.tm_year, t.tm_mon) if t.tm_mon > 2 else (t.tm_year - 1, t.tm_mon + 12)
    return 365 * y + y // 4 - y // 100 + y // 400 + (153 * (m - 3) + 2) // 5 + t.tm_mday


# ---------- the line ----------

def colour_for(left):
    if left is None:
        return GREY
    return GREEN if left > 25 else ORANGE if left > 10 else RED


def model_name(data):
    """The model in use, as Claude Code names it in the status line's input (`model.display_name`), short and safe
    to print: 'Opus', 'Fable 2'. '' when it gives none."""
    m = data.get('model')
    name = m.get('display_name') if isinstance(m, dict) else None
    if not isinstance(name, str):
        return ''
    name = name.strip()
    if name[:7].lower() == 'claude ':
        name = name[7:]
    return _clean(name, 24)


# A narrow terminal drops these first, in this order; then the account's name is cut.
DROP = ('next', 'refills', 'model', 'left')


def _join(name, model, parts):
    head = '%s on %s' % (name, model) if name and model else name or model
    return SEP.join(([head] if head else []) + [text for _, text in parts])


def build(data, st, now=None, columns=None, colour=True, env=None):
    """The line to print: '● {account} on {model} · {n}% left · refills {when} · next: {account}'. `columns` is the
    terminal width, or None for no limit. env: this Claude Code's environment. On a profile of its own (own_profile) the
    account is that profile's own (or "its own login"), never the one Switchboard switches, and there is no next. Names
    the page has not renewed for STALE_AFTER seconds say how old they are: "(stale 12 min)"."""
    now = time.time() if now is None else now
    if env is not None and own_profile(env):
        name, nxt = short_name(st, own_account(env)) or 'its own login', ''
    else:
        name, nxt = short_name(st, st.get('active')), short_name(st, st.get('next'))
        if nxt == name:
            nxt = ''
        age = names_age(st, now) if name else None
        if age is not None and age > STALE_AFTER:
            name = '%s (stale %s)' % (name, age_words(age))
    model = model_name(data)
    which, win = pick(windows(data, now))
    parts, left = [], None
    if win:
        left = left_of(win[0])
        parts.append(('left', '%d%% left%s' % (left, ' (5h)' if which == 'five_hour' else '')))
        if win[1] is not None:
            parts.append(('refills', 'refills ' + when(win[1], now)))
    if nxt and nxt != name:
        parts.append(('next', 'next: ' + nxt))

    text = _join(name, model, parts)
    if columns is not None:
        room = max(1, columns - MARGIN) - 2          # what fits after the dot and its space
        for drop in DROP:                            # narrow terminal: drop parts, least needed first
            if len(text) <= room:
                break
            if drop == 'model':
                model = ''
            else:
                parts = [p for p in parts if p[0] != drop]
            text = _join(name, model, parts)
        if len(text) > room:                         # only the name is left, and it is still too long
            text = text[:room - 1] + '…' if room >= 2 else ''
    return ((colour_for(left) + DOT + RESET) if colour else DOT) + (' ' + text if text else '')


def _columns(env):
    try:
        n = int(env.get('COLUMNS') or 0)
    except ValueError:
        return None
    return n if n > 0 else None


def _colour(env):
    return not env.get('NO_COLOR')      # https://no-color.org: any non-empty value turns colour off


# ---------- the live numbers feed ----------

def _session_file_name(sid):
    if not isinstance(sid, str) or not 0 < len(sid) <= 128 or sid[0] == '.' or not set(sid) <= ID_CHARS:
        return None
    return sid + '.json'


def own_profile(env):
    """True when this Claude Code runs on a profile of its own: CLAUDE_CONFIG_DIR set to a folder other than ~/.claude
    (`switchboard engine run`, a folder tied to an account, a profile made by hand). It may be signed in to another
    account than the one in use, so Switchboard never takes its numbers for that one."""
    d = env.get('CLAUDE_CONFIG_DIR')
    if not d:
        return False
    try:
        default = os.path.join(os.path.expanduser('~'), '.claude')
        return os.path.realpath(os.path.expanduser(d)) != os.path.realpath(default)
    except Exception:
        return True


def live_record(data, st, now, env=None):
    """What gets saved for this session: the limit numbers only, or None when there are none."""
    rl = data.get('rate_limits')
    if not isinstance(rl, dict):
        return None
    clean = {}
    for name in WINDOWS:
        w = rl.get(name)
        if isinstance(w, dict) and _num(w.get('used_percentage')) is not None:
            clean[name] = {k: _num(w.get(k)) for k in ('used_percentage', 'resets_at')}
    spend = rl.get('spend_limit')
    if isinstance(spend, dict) and _num(spend.get('used_percentage')) is not None:
        s = {k: _num(spend.get(k)) for k in ('used_percentage', 'resets_at', 'used_usd', 'limit_usd')}
        period = spend.get('period')
        if isinstance(period, str) and period.isalpha() and len(period) <= 16:
            s['period'] = period
        clean['spend_limit'] = {k: v for k, v in s.items() if v is not None}
    if not clean:
        return None
    active = st.get('active')
    rec = {'v': 1, 'session_id': data.get('session_id'), 'at': round(now, 3), 'rate_limits': clean,
           # The account Switchboard believed was active when these numbers arrived: the name the line showed.
           'account': active if isinstance(active, str) and active else None}
    if own_profile(os.environ if env is None else env):
        rec['own_profile'] = True
    return rec


def save_live(data, st, now=None, folder=None, env=None):
    """Write live/<session id>.json atomically. Returns the path, or None when there was nothing to save.

    Claude Code runs the line every minute even while a session is quiet (REFRESH), handing it the numbers of its last
    answer each time. When they are the ones this session's record already holds, the record stays as it is: it keeps
    the time they came (and the account named then), so numbers an idle session has held for an hour never pass for
    numbers of the last minute."""
    now = time.time() if now is None else now
    fname = _session_file_name(data.get('session_id'))
    rec = live_record(data, st, now, env) if fname else None
    if rec is None:
        return None
    folder = folder or live_dir()
    path = os.path.join(folder, fname)
    if _held(path, rec):
        prune(folder, now)
        return None
    body = _dumps(rec).encode('utf-8')
    os.makedirs(folder, mode=0o700, exist_ok=True)
    tmp = '%s.%d.tmp' % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(body)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    prune(folder, now)
    return path


def _held(path, rec):
    """This session's record already holds exactly these numbers (save_live)."""
    try:
        with open(path, 'rb') as f:
            old = _loads(f.read())
    except (OSError, ValueError, RecursionError):
        return False
    return isinstance(old, dict) and old.get('v') == rec['v'] and old.get('rate_limits') == rec['rate_limits']


def prune(folder, now=None, max_age=LIVE_MAX_AGE, every=PRUNE_EVERY):
    """Remove live files older than a day, or dated past the clock. Costs one stat per run: the folder is only
    read once an hour."""
    now = time.time() if now is None else now
    marker = os.path.join(folder, '.pruned')
    try:
        if now - os.stat(marker).st_mtime < every:
            return 0
    except OSError:
        pass
    try:
        with open(marker, 'w'):
            pass
        os.utime(marker, (now, now))
    except OSError:
        return 0
    gone = 0
    try:
        with os.scandir(folder) as it:
            for e in it:
                try:
                    if e.name != '.pruned' and e.is_file(follow_symlinks=False) \
                            and not -AHEAD <= now - e.stat(follow_symlinks=False).st_mtime <= max_age:
                        os.unlink(e.path)
                        gone += 1
                except OSError:
                    pass
    except OSError:
        pass
    return gone


# ---------- the command ----------

def main(stdin=None, stdout=None, env=None, now=None):
    """Read Claude Code's JSON, print one line, save the numbers. Always returns 0."""
    stdout = stdout if stdout is not None else sys.stdout
    env = os.environ if env is None else env
    now = time.time() if now is None else now
    line, data, st = FALLBACK, None, {}
    try:
        raw = (stdin if stdin is not None else sys.stdin.buffer).read()
        data = _loads(raw) if raw and raw.strip() else {}
        if not isinstance(data, dict):
            data = {}
        st = read_state()
        line = build(data, st, now, _columns(env), _colour(env), env)
    except Exception:
        line = FALLBACK
    try:
        stdout.write(line + '\n')
        stdout.flush()
    except Exception:
        _silence_stdout()
    if data:
        try:
            save_live(data, st, now, env=env)
        except Exception:
            pass
    return 0


def _silence_stdout():
    """Claude Code closed the pipe (it cancels a run when a newer one starts). Exit without a word."""
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    except Exception:
        pass


def cli():
    """The status line command. Exits 0 whatever happens, even on Ctrl-C: no traceback on the status row."""
    try:
        main()
    except BaseException:
        pass
    sys.exit(0)


# ---------- turning it on and off ----------

class PlanError(Exception):
    """A plan that can't be applied. The text says why, in words a person can act on."""


def command(python=None, script=None):
    """The shell command Claude Code should run.

    It imports this file as a module instead of running it as a script, because Python caches an
    imported module's compiled form and recompiles a script on every run (about 15 ms here). Only
    this file's own folder is added, at the end of the path, so nothing else in Switchboard loads
    and no standard module can be shadowed. -I ignores PYTHON* settings and keeps the current
    folder off the path; -S skips site-packages. The trailing shell comment marks the line as ours.
    """
    import json
    import shlex
    python = python or sys.executable or '/usr/bin/python3'
    folder = os.path.dirname(os.path.abspath(script or __file__))
    code = ('import sys; D = %s; sys.path[:] = [p for p in sys.path if p] + [D]; import os; '
            'print("Switchboard: status line not found") if not os.path.isfile(os.path.join(D, "statusline.py")) '
            'else __import__("statusline").cli()' % json.dumps(folder))    # a JSON string is a Python string
    return '%s -I -S -c %s %s' % (shlex.quote(python), shlex.quote(code), MARK)


def precompile(python_tag=None, script=None):
    """Write this Python's bytecode for this file into __pycache__ next to it, so the command above loads it
    instead of compiling ~800 lines on every run. Apple's Python never writes bytecode when started with -I,
    so without this it compiles every time. Python checks the bytecode against the file's size and time and
    compiles afresh when the file changed, so a stale copy is never used. Returns the path, or None when the
    folder can't be written (the line still works, a few milliseconds slower)."""
    import py_compile
    src = os.path.abspath(script or __file__)
    tag = python_tag or sys.implementation.cache_tag
    cfile = os.path.join(os.path.dirname(src), '__pycache__', '%s.%s.pyc' % (os.path.splitext(os.path.basename(src))[0], tag))
    try:
        return py_compile.compile(src, cfile=cfile, doraise=True)
    except (OSError, py_compile.PyCompileError):
        return None


def ours(python=None, script=None, command_line=None):
    """The statusLine value Switchboard installs: its command, which Claude Code also runs every REFRESH seconds while
    a session is quiet ("refreshInterval": "re-runs your command every N seconds", code.claude.com/docs/en/statusline),
    so the line and the numbers it saves move during a long task too."""
    return {'type': 'command', 'command': command_line or command(python, script), 'refreshInterval': REFRESH}


def is_ours(value):
    """True when a statusLine value runs Switchboard's status line (from any folder or Python)."""
    if not isinstance(value, dict) or not isinstance(value.get('command'), str):
        return False
    cmd = value['command']
    if cmd.rstrip().endswith(MARK):
        return True
    import shlex
    try:
        words = shlex.split(cmd)
    except ValueError:
        return False
    for i, w in enumerate(words):
        if w.replace('\\', '/').endswith('/switchboard/statusline.py'):
            return True
        if w == '-m' and i + 1 < len(words) and words[i + 1] == 'switchboard.statusline':
            return True
    return False


def install_plan(settings_path, replace=False, command_line=None):
    """The exact change that turns Switchboard's status line on. Changes nothing by itself.

    If the file already has a statusLine that isn't Switchboard's, the plan keeps it and says so,
    unless replace=True. Only the statusLine key ever changes; every other byte of the file stays.
    """
    want = ours(command_line=command_line)
    f = _settings_file(settings_path)
    if f.get('error'):
        return _refused(f, 'install')
    cur, has = f['obj'].get('statusLine'), 'statusLine' in f['obj']
    if has and is_ours(cur):
        same = cur.get('command') == want['command']
        if same and 'refreshInterval' in cur:          # a refresh of the person's own on it is theirs
            return _nothing(f, 'install', "Switchboard's status line is already on.")
        # Ours, from an older place, or from before it ran every minute (then only the refresh is added). What was
        # there before ours stays the saved one.
        value = dict(cur, refreshInterval=REFRESH) if same else want
        rec = _records().get(f['target'])
        record = dict(rec, installed=value) if rec else \
            {'had_previous': False, 'previous': None, 'previous_text': None, 'installed': value}
        return _change(f, 'install', value, record, "Switchboard's status line is updated to run %s."
                       % ('every minute' if same else 'from here'))
    if has and not replace:
        return _nothing(f, 'install', "You already have a status line (%s). Switchboard left it as it is. "
                        "Ask for replace=True to use Switchboard's instead; turning Switchboard's off later "
                        "puts yours back exactly." % _describe(cur), kept_theirs=True)
    member = f['members'].get('statusLine')
    record = {'had_previous': has, 'previous': cur if has else None,
              'previous_text': f['text'][member[2]:member[3]] if (has and member) else None,
              'installed': want}
    if f['layout'] is None:
        record['file_was'] = f['text'] if f['digest'] else None      # None: there was no file at all
    elif not f['layout'][2]:                                         # an empty object: its own spacing comes back
        record['inner'] = f['text'][f['layout'][0] + 1:f['layout'][1]]
    hooks = _notify_records().get(f['target']) or {}
    for k in SHARED:                    # the hooks went in first: whichever of the two goes last puts the file back
        if k not in record and k in hooks:
            record[k] = hooks[k]
    msg = ("Switchboard's status line replaces yours (%s). Yours is saved, and turning Switchboard's off "
           "puts it back." % _describe(cur)) if has else "Switchboard's status line goes on."
    return _change(f, 'install', want, record, msg)


def uninstall_plan(settings_path):
    """The exact change that turns Switchboard's status line off and puts back what was there before."""
    f = _settings_file(settings_path)
    if f.get('error'):
        return _refused(f, 'uninstall')
    cur, has = f['obj'].get('statusLine'), 'statusLine' in f['obj']
    if not has:
        return _nothing(f, 'uninstall', "Switchboard's status line isn't on.")
    if not is_ours(cur):
        return _nothing(f, 'uninstall', "The status line here isn't Switchboard's (%s), so it stays as it is."
                        % _describe(cur), kept_theirs=True)
    rec = _records().get(f['target']) or {}
    if rec.get('had_previous'):
        return _change(f, 'uninstall', rec.get('previous'), None,
                       "Switchboard's status line goes off and yours (%s) comes back." % _describe(rec.get('previous')),
                       restore_text=rec.get('previous_text'))
    plan = _change(f, 'uninstall', _ABSENT, None, "Switchboard's status line goes off.", inner=rec.get('inner'))
    if 'file_was' in rec and plan['expect'] == {}:
        # Switchboard made this file, or found it empty, and nothing else has been added since: put it back as it was.
        was = rec['file_was']
        plan.update(delete=was is None, text=was if was is not None else '', change=True,
                    message="Switchboard's status line goes off, and the settings file it made goes too."
                    if was is None else plan['message'])
    return plan


def off_elsewhere(current):
    """Take Switchboard's status line out of every other settings file its record names (one the page put it in
    while this process's CLAUDE_CONFIG_DIR names another folder). Returns the paths changed; raises PlanError when one
    is refused."""
    here = os.path.realpath(current)
    done = []
    for target in sorted(t for t in _records() if t != here):
        plan = uninstall_plan(target)
        if not plan.get('ok'):
            raise PlanError(plan['message'])
        if plan.get('change'):
            done.append(apply(plan)['path'])
        else:
            _drop_record(target)                       # none of Switchboard's there any more
    return done


def refresh_plan(settings_path):
    """The change that makes a status line Switchboard put in before it ran every minute run every minute too: its
    value gains "refreshInterval", only while it is still exactly what Switchboard's record says it put there. So a
    line of the person's own, one they changed since (a refresh of their own taken out included: that makes it once),
    or one with no record is never touched. Changes nothing by itself."""
    f = _settings_file(settings_path)
    if f.get('error'):
        return _refused(f, 'install')
    cur, rec = f['obj'].get('statusLine'), _records().get(f['target'])
    if not rec or not is_ours(cur) or cur != rec.get('installed') or 'refreshInterval' in cur:
        return _nothing(f, 'install', 'Nothing to change.')
    value = dict(cur, refreshInterval=REFRESH)
    return _change(f, 'install', value, dict(rec, installed=value), "Switchboard's status line runs every minute now.")


def every_minute(current=None):
    """refresh_plan for this process's Claude Code settings file and for every other one Switchboard's record names
    (one the page put it in while a terminal's CLAUDE_CONFIG_DIR names another). A file that can't be read, or that
    changes under the write, is left as it is for the next time. Returns the paths changed."""
    here = os.path.realpath(current or settings_path())
    done = []
    for target in [here] + sorted(t for t in _records() if t != here):
        plan = refresh_plan(target)
        if plan.get('ok') and plan.get('change'):
            try:
                done.append(apply(plan)['path'])
            except PlanError:
                pass
    return done


def python_gone(command_line):
    """True when the Python a command of Switchboard's starts with is no longer there (an app that held it was removed,
    an update chose another). False when it is there, or when the command can't be read: then it is left as it is."""
    import shlex
    try:
        words = shlex.split(command_line)
    except (ValueError, AttributeError):
        return False
    return bool(words) and not (os.path.isfile(words[0]) and os.access(words[0], os.X_OK))


def repair(python=None):
    """At the page's start: a status line Switchboard put in whose Python is gone runs on this one instead, through
    install_plan and apply as ever (checked, backed up, recorded for the undo). Only a line that ends with Switchboard's
    mark and is still exactly what its record says it put there; a status line of yours is never touched. Looks at
    this process's settings file and every other one the record names. Never raises. Returns one log line for each
    file it changed, or could not change."""
    python = python or sys.executable
    if not python or python_gone(python):
        return []
    said = []
    here = os.path.realpath(settings_path())
    for target in [here] + sorted(t for t in _records() if t != here):
        try:
            f = _settings_file(target)
            cur = None if f.get('error') else f['obj'].get('statusLine')
            rec = _records().get(f['target'])
            if not rec or not isinstance(cur, dict) or cur != rec.get('installed') or not isinstance(
                    cur.get('command'), str) or not cur['command'].rstrip().endswith(MARK) or \
                    not python_gone(cur['command']):
                continue
            plan = install_plan(target, command_line=command(python=python))
            if plan.get('ok') and not plan.get('change'):
                continue
            if plan.get('ok'):
                apply(plan)
                said.append('the status line in %s runs on %s now: the Python it named is gone' % (target, python))
                continue
            why = plan.get('message')
        except PlanError as e:
            why = str(e)
        except Exception as e:  # noqa: BLE001 - the line stays as it was; the page starts either way
            why = repr(e)
        said.append('the status line in %s names a Python that is gone and was left as it is: %s' % (target, why))
    return said


def apply(plan):
    """Make a plan's change: re-check the file is as planned, keep a backup next to it, write atomically.

    Returns {'changed', 'path', 'backup', 'message'}. Raises PlanError when it refuses.
    """
    if not plan.get('ok'):
        raise PlanError(plan.get('message') or 'This plan cannot be applied.')
    if not plan.get('change'):
        return {'changed': False, 'path': plan['path'], 'backup': None, 'message': plan['message']}
    target = plan['target']
    changed = "Claude Code's settings file changed just now. Nothing was written; try again."
    try:
        now_bytes = _read_bytes(target)
    except OSError as e:
        raise PlanError("Can't read %s: %s" % (plan['path'], e.strerror or e))
    if _digest(now_bytes) != plan['digest']:
        raise PlanError(changed)
    if now_bytes is not None and not os.access(target, os.W_OK):
        raise PlanError("Claude Code's settings file is set read-only, so Switchboard leaves it alone.")
    backup = None
    records_before = _records()
    try:
        if now_bytes is not None:
            backup = backup_path(plan['path'])         # one per settings file, private, replaced each time
            _write_atomic(backup, now_bytes, 0o600)
        if plan['action'] == 'install':
            _save_record(target, plan['record'])         # saved before the change, so it can't be lost
    except OSError as e:                               # a folder that can't be written, a full disk: before any change
        raise PlanError(cant_write(plan['path'], e))
    try:
        # The last look, right before the swap: a save Claude Code made since the plan is never overwritten.
        if _digest(_read_bytes(target)) != plan['digest']:
            raise PlanError(changed)
        if plan.get('delete'):
            os.unlink(target)
        else:
            try:
                mode = os.stat(target).st_mode & 0o7777
            except OSError:
                mode = 0o600
            _write_atomic(target, plan['text'].encode('utf-8'), mode)
    except BaseException as e:
        if plan['action'] == 'install':                # nothing changed, so no record of a change either
            try:
                _put_records(records_before)
            except OSError:
                pass
        if isinstance(e, OSError):
            raise PlanError(cant_write(plan['path'], e))
        raise
    if not plan.get('delete'):
        check = _settings_file(plan['path'])
        if check.get('error') or check['obj'] != plan['expect']:
            raise PlanError('The settings file did not read back as planned. Its copy from before is beside it, '
                            'ending in .switchboard-backup.')
    if plan['action'] == 'uninstall':
        try:
            _drop_record(target)
        except OSError:                                # the change is made; a record left behind names only that file
            pass
    return {'changed': True, 'path': plan['path'], 'backup': backup, 'message': plan['message']}


def cant_write(path, e):
    """One plain line for a write refused by the system (a folder that can't be written, a full disk)."""
    return "Switchboard can't write beside %s (%s). Nothing was changed." % (path, getattr(e, 'strerror', None) or e)


# ---------- plan helpers ----------

_ABSENT = object()      # "no statusLine key at all", which is different from "statusLine": null


def _describe(value):
    if isinstance(value, dict) and isinstance(value.get('command'), str):
        c = value['command']
        return 'it runs %s' % (c if len(c) <= 60 else c[:57] + '...')
    import json
    return 'it is set to %s' % json.dumps(value)[:60]


def _read_bytes(path):
    try:
        with open(path, 'rb') as f:
            return f.read()
    except FileNotFoundError:
        return None


def _digest(b):
    import hashlib
    return None if b is None else hashlib.sha256(b).hexdigest()


def _settings_file(path):
    """Read and understand a settings file. Never raises for a bad file: it says what is wrong instead."""
    import json
    path = os.path.abspath(os.path.expanduser(path))
    target = os.path.realpath(path)        # a symlinked settings file stays a symlink: its target is written
    f = {'path': path, 'target': target}
    try:
        raw = _read_bytes(target)
    except OSError as e:
        return dict(f, error="Can't read %s: %s" % (path, e.strerror or e))
    f['digest'] = _digest(raw)
    try:
        text = raw.decode('utf-8') if raw is not None else ''
    except UnicodeDecodeError:
        return dict(f, error='%s is not UTF-8 text, so Switchboard leaves it alone.' % path)
    f['text'] = text
    if not text.strip():
        f.update(obj={}, members={}, layout=None)
        return f
    try:
        obj = json.loads(text)
        layout = _layout(text) if isinstance(obj, dict) else None
    except (ValueError, RecursionError):
        return dict(f, error="%s isn't valid JSON, so Switchboard leaves it alone. Claude Code reports it as "
                            "a Settings Error too; fix that first." % path)
    if not isinstance(obj, dict):
        return dict(f, error="%s doesn't hold a JSON object, so Switchboard leaves it alone." % path)
    if [m[0] for m in layout[2]].count('statusLine') > 1:
        return dict(f, error='%s has statusLine twice. Remove one first.' % path)
    f.update(obj=obj, layout=layout, members={m[0]: m for m in layout[2]})
    return f


def _layout(text):
    """(where '{' is, where '}' is, [(key, key start, value start, value end)]) for the top-level object."""
    import json
    from json.decoder import scanstring
    dec = json.JSONDecoder()
    i = _skip(text, 0)
    if text[i:i + 1] != '{':
        raise ValueError('not an object')
    start, i, members = i, _skip(text, i + 1), []
    if text[i:i + 1] == '}':
        end = i
    else:
        while True:
            if text[i:i + 1] != '"':
                raise ValueError('expected a key')
            key, j = scanstring(text, i + 1)
            k = _skip(text, j)
            if text[k:k + 1] != ':':
                raise ValueError('expected :')
            v = _skip(text, k + 1)
            _, e = dec.raw_decode(text, v)
            members.append((key, i, v, e))
            n = _skip(text, e)
            if text[n:n + 1] == ',':
                i = _skip(text, n + 1)
                continue
            if text[n:n + 1] == '}':
                end = n
                break
            raise ValueError('expected , or }')
    if _skip(text, end + 1) != len(text):
        raise ValueError('extra text')
    return start, end, members


def _indent_of(text, i):
    line = text.rfind('\n', 0, i) + 1
    pre = text[line:i]
    return pre if line > 0 and not pre.strip(' \t') else None     # None: the object is all on one line


def _dump(value, indent):
    import json
    if indent is None:
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(value, indent=indent or '  ', ensure_ascii=False).replace('\n', '\n' + indent)


def _splice(f, value, restore_text=None, inner=None):
    """The file's new text with statusLine set to value (or removed for _ABSENT), every other byte as it was. inner:
    what was between the braces before statusLine went into an empty object, put back when it is the last to go."""
    text, layout = f['text'], f['layout']
    if layout is None:                                     # no file, or an empty one
        return text if value is _ABSENT else '{\n  "statusLine": %s\n}\n' % _dump(value, '  ')
    start, end, members = layout
    idx = next((n for n, m in enumerate(members) if m[0] == 'statusLine'), None)
    if value is _ABSENT:
        if idx is None:
            return text
        if idx > 0:
            return text[:members[idx - 1][3]] + text[members[idx][3]:]
        if len(members) > 1:
            return text[:members[0][1]] + text[members[1][1]:]
        return text[:start + 1] + (inner if isinstance(inner, str) and not inner.strip() else '') + text[end:]
    if idx is not None:
        _, k, v, e = members[idx]
        body = restore_text if restore_text is not None else _dump(value, _indent_of(text, k))
        return text[:v] + body + text[e:]
    if members:
        last = members[-1]
        ind = _indent_of(text, last[1])
        if ind is None:
            return text[:last[3]] + ', "statusLine": ' + _dump(value, None) + text[last[3]:]
        return text[:last[3]] + ',\n' + ind + '"statusLine": ' + _dump(value, ind) + text[last[3]:]
    return text[:start + 1] + '\n  "statusLine": ' + _dump(value, '  ') + '\n' + text[end:]


def _change(f, action, value, record, message, restore_text=None, inner=None):
    import difflib
    import json
    expect = dict(f['obj'])
    if value is _ABSENT:
        expect.pop('statusLine', None)
    else:
        expect['statusLine'] = value
    if restore_text is not None:
        try:
            if json.loads(restore_text) != value:
                restore_text = None
        except ValueError:
            restore_text = None
    text, rewritten = _splice(f, value, restore_text, inner), False
    try:
        same = json.loads(text) == expect if text.strip() else expect == {}
    except ValueError:
        same = False
    if not same:                # should never happen; never write a file that reads back differently
        text, rewritten = json.dumps(expect, indent=2, ensure_ascii=False) + '\n', True
    diff = ''.join(difflib.unified_diff(f['text'].splitlines(True), text.splitlines(True),
                                        f['path'], f['path'] + ' (after)'))
    return {'ok': True, 'action': action, 'change': text != f['text'], 'path': f['path'], 'target': f['target'],
            'digest': f['digest'], 'key': 'statusLine',
            'before': f['obj'].get('statusLine'), 'had_before': 'statusLine' in f['obj'],
            'after': None if value is _ABSENT else value, 'has_after': value is not _ABSENT,
            'text': text, 'expect': expect, 'diff': diff, 'rewritten': rewritten,
            'record': record, 'kept_theirs': False, 'message': message}


def _nothing(f, action, message, kept_theirs=False):
    cur, has = f['obj'].get('statusLine'), 'statusLine' in f['obj']
    return {'ok': True, 'action': action, 'change': False, 'path': f['path'], 'target': f['target'],
            'digest': f['digest'], 'key': 'statusLine', 'before': cur, 'had_before': has,
            'after': cur, 'has_after': has, 'text': f['text'], 'expect': f['obj'], 'diff': '',
            'rewritten': False, 'record': None, 'kept_theirs': kept_theirs, 'message': message}


def _refused(f, action):
    return {'ok': False, 'action': action, 'change': False, 'path': f['path'], 'target': f['target'],
            'message': f['error']}


def _records():
    import json
    try:
        with open(record_path()) as fh:
            data = json.load(fh)
        return dict(data['installs']) if isinstance(data.get('installs'), dict) else {}
    except (OSError, ValueError, AttributeError, KeyError):
        return {}


def record_names(path, target):
    """For the uninstaller: does the install record at `path` (this file's, or the notification hooks') name this
    settings file, by its real path? False when there is no record. True when the record can't be read: the cautious
    answer, so a file Switchboard may have changed is never taken for one it never touched."""
    import json
    try:
        with open(path, 'rb') as fh:
            data = json.loads(fh.read().decode('utf-8'))
    except FileNotFoundError:
        return False
    except (OSError, ValueError, RecursionError):
        return True
    installs = data.get('installs') if isinstance(data, dict) else None
    return target in installs if isinstance(installs, dict) else True


def _save_record(target, record):
    import json
    recs = _records()
    recs[target] = dict(record, at=round(time.time()))
    _write_atomic(record_path(), json.dumps({'v': 1, 'installs': recs}, indent=1).encode('utf-8'), 0o600)


def _drop_record(target):
    import json
    recs = _records()
    if recs.pop(target, None) is not None:
        _write_atomic(record_path(), json.dumps({'v': 1, 'installs': recs}, indent=1).encode('utf-8'), 0o600)


def backup_path(settings_path):
    """The one copy Switchboard keeps of a settings file from before its last change (the models setting uses the
    same one): replaced on every change, never piling up, readable only by you."""
    return '%s.switchboard-backup' % settings_path


def _put_records(recs):
    import json
    _write_atomic(record_path(), json.dumps({'v': 1, 'installs': recs}, indent=1).encode('utf-8'), 0o600)


def _write_atomic(path, data, mode):
    folder = os.path.dirname(path) or '.'
    os.makedirs(folder, mode=0o700, exist_ok=True)
    tmp = os.path.join(folder, '.%s.%d.switchboard-tmp' % (os.path.basename(path), os.getpid()))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, 'wb') as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


if __name__ == '__main__':
    cli()
