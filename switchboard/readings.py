"""One Mac's readings shown on another: `switchboard readings give` and `switchboard readings take`.

A person with two Macs keeps the same accounts on both, and each Mac reads every account's limits from Anthropic.
`switchboard readings give | ssh other-mac switchboard readings take -` hands one Mac's numbers to the other, which
shows them for a while in place of older numbers of its own.

`give` prints a small JSON document (a contract, as `status --json` is; tests/test_readings_carry.py pins it):

  {"schema": 1, "given_at": "2026-11-25T06:10:00Z",
   "accounts": [{"email": "maya@northwind.example", "checked_at": "2026-11-25T06:09:20Z",
                 "limits": {"five_hour": {"used_percent": 62.5, "refills_at": "2026-11-25T08:00:00Z",
                                          "read_at": "2026-11-25T06:09:20Z"},
                            "week": {...}, "models": [{"name": "Fable", ...}]}}]}

Only the numbers this Mac read itself go out (never ones it took from another Mac), each with when it was read. No
login, token, account id or organization: an account is its whole email. A limit not known is null.

`take` keeps, for each account this Mac also keeps (by its whole email), a reading that is newer than its own, in
<data>/readings-taken.json (owner-only), for at most the time given (10 minutes unless said; an hour at most). It is
shown (api.readings, so the page, `switchboard status` and the menu bar) while it is newer than this Mac's own reading
and its time is not up, marked as from another Mac. Its age is its real age: the time it was read on the other Mac,
never the time it arrived (and never younger for a clock there that runs ahead: see take). A reading dated ahead of this
Mac's clock is not taken.

What is taken changes what is shown, not what is decided: automatic switching reads the accounts' own readings
(usage.readings), never these.
"""
import json
import os
import re
import sys
import time

from . import core

SCHEMA = 1
STORE = 'readings-taken.json'
FOR_DEFAULT = 600.0         # seconds a taken reading is shown, unless --for says
FOR_MAX = 3600.0
AHEAD = 5.0                 # a time this far past the clock is still "now"; further is not believed
READ_MAX = 1024 * 1024
USAGE = 'switchboard readings give\nswitchboard readings take [-|FILE] [--for 10m]'
WAITING = 'Waiting for what `switchboard readings give` printed on the other Mac: paste it, then press Control-D.'
FIVE, WEEK = '5-hour', 'weekly'


class Words(Exception):
    pass


def _iso(t):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(t)) if _num(t) and 0 < t < 1e11 else None


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x == x and abs(x) != float('inf')


def _pct(x):
    return round(float(x), 1) if _num(x) else None


# ---------- give ----------

def _limit(w, fallback_at):
    return {'used_percent': _pct(w.pct), 'refills_at': _iso(w.resets_at),
            'read_at': _iso(w.read_at if w.read_at is not None else fallback_at)}


def give(now=None):
    """The document above, from this Mac's own readings (usage.readings: never the network, never the Keychain)."""
    from .accounts import api, usage
    now = time.time() if now is None else now
    own = usage.readings(now=now)
    out = []
    for a in api.accounts():
        r = own.get(a.id)
        wins = dict((w.name, w) for w in (r.windows if r else ()))
        blank = {'used_percent': None, 'refills_at': None, 'read_at': None}
        at = r.read_at if r else None
        out.append({'email': a.email,
                    'checked_at': _iso(r.checked_at if r and r.checked_at is not None else at),
                    'limits': {'five_hour': _limit(wins[FIVE], at) if FIVE in wins else dict(blank),
                               'week': _limit(wins[WEEK], at) if WEEK in wins else dict(blank),
                               'models': [dict({'name': n[6:]}, **_limit(w, at)) for n, w in wins.items()
                                          if n.startswith('model:') and w.pct is not None]}})
    return {'schema': SCHEMA, 'given_at': _iso(now), 'accounts': out}


# ---------- take ----------

def store_path():
    return core.data_path(STORE)


def _load():
    doc = core.read_json(store_path())
    accounts = doc.get('accounts') if doc.get('v') == SCHEMA else None
    return dict((k, v) for k, v in accounts.items() if isinstance(v, dict)) if isinstance(accounts, dict) else {}


def _when(text):
    t = core.parse_iso(text) if isinstance(text, str) else None
    return t if _num(t) else None


def _windows(limits, now, skew=0.0):
    """{window name: {'pct', 'resets_at', 'at'}} from one account's limits in a given document, or a reason it is not
    taken. skew: how far the other Mac's clock is ahead of this one's, taken off each time it read."""
    if not isinstance(limits, dict):
        return 'it has no limits'
    pairs = [(FIVE, limits.get('five_hour')), (WEEK, limits.get('week'))]
    from .accounts import usage
    for m in limits.get('models') or [] if isinstance(limits.get('models'), list) else []:
        name = usage.clean_label(m.get('name')) if isinstance(m, dict) and isinstance(m.get('name'), str) else None
        if name:
            pairs.append(('model:' + name, m))
    out = {}
    for name, w in pairs:
        if not isinstance(w, dict) or not _num(w.get('used_percent')) or not 0 <= w['used_percent'] <= 100:
            continue
        at = _when(w.get('read_at'))
        if at is None:
            continue
        at -= skew
        if at > now + AHEAD:
            return "its numbers are dated ahead of this Mac's clock"
        out[name] = {'pct': float(w['used_percent']), 'resets_at': _when(w.get('refills_at')), 'at': at}
    return out or 'it has no numbers'


def take(doc, for_s=FOR_DEFAULT, now=None):
    """Keep the readings of `doc` (give's document) that are newer than this Mac's own, until now + for_s. Returns
    [(email, words)]: what happened to each account of the document."""
    from .accounts import api, usage
    now = time.time() if now is None else now
    if not isinstance(doc, dict) or isinstance(doc.get('schema'), bool) or not isinstance(doc.get('schema'), int) \
            or not isinstance(doc.get('accounts'), list):
        raise core.SwitchError("This isn't what `switchboard readings give` prints. Nothing was taken.")
    if doc['schema'] > SCHEMA:
        raise core.SwitchError('These readings come from a newer Switchboard. Update this one (switchboard update). '
                               'Nothing was taken.')
    by_email = {}
    for a in api.accounts():
        by_email.setdefault((a.email or '').lower(), []).append(a.id)
    own = usage.readings(now=now)
    # The other Mac's clock may run ahead of this one's, and its times would then read as fresher than they are. Its
    # document says when it was given; read at once (a pipe), a time ahead of this clock is how far ahead it runs,
    # and that much is taken off every time it read. A clock behind is left as it is: its numbers only look older.
    given = _when(doc.get('given_at'))
    skew = given - now if given is not None and given > now + AHEAD else 0.0
    said, keep = [], {}
    for item in doc['accounts']:
        email = item.get('email') if isinstance(item, dict) and isinstance(item.get('email'), str) else None
        if not email:
            continue
        ids = by_email.get(email.lower()) or []
        if len(ids) != 1:
            said.append((email, 'not kept here' if not ids else 'kept here more than once; not taken'))
            continue
        wins = _windows(item.get('limits'), now, skew)
        if not isinstance(wins, dict):
            said.append((email, 'not taken: %s' % wins))
            continue
        at = min(w['at'] for w in wins.values())
        mine = own.get(ids[0])
        if mine is not None and mine.read_at is not None and mine.read_at >= at:
            said.append((email, 'not taken: its own numbers here are newer'))
            continue
        checked = _when(item.get('checked_at'))
        checked = checked - skew if checked is not None else None
        keep[ids[0]] = {'email': email, 'w': wins, 'read_at': at,
                        'checked_at': checked if checked is not None and at <= checked <= now + AHEAD else at,
                        'taken_at': now, 'until': now + for_s}
        said.append((email, 'shown here until %s' % _clock(now + for_s)))
    with core.file_lock(STORE + '.lock'):
        kept = dict((aid, e) for aid, e in _load().items() if _num(e.get('until')) and e['until'] > now)
        kept.update(keep)
        core.write_json(store_path(), {'v': SCHEMA, 'accounts': kept})
    return said


def _clock(t):
    from . import term
    return term._hour_minute(t)


# ---------- what is shown ----------

def _reading(aid, e, now):
    """An api.Reading from a taken entry, marked as from elsewhere, or None. A window whose refill time has passed
    since it was read reads as not known (usage.window_entries' rule)."""
    from .accounts import api, usage
    wins = e.get('w') if isinstance(e.get('w'), dict) else {}
    out = []
    for name in sorted(wins, key=usage._window_order):
        w = wins[name]
        if not isinstance(w, dict) or not _num(w.get('at')):
            continue
        pct, resets, at = w.get('pct') if _num(w.get('pct')) else None, \
            w.get('resets_at') if _num(w.get('resets_at')) else None, w['at']
        if resets is not None and resets <= now and at < resets:
            pct = None
        out.append(api.Window(name, pct, resets, at, 'endpoint'))
    if not out:
        return None
    read_at = min(w.read_at for w in out)
    checked = e.get('checked_at') if _num(e.get('checked_at')) else read_at
    return api.Reading(aid, tuple(out), read_at, 'endpoint', checked_at=max(read_at, checked), elsewhere=True)


def shown(own, now=None):
    """`own` ({account id: api.Reading}, this Mac's readings) with each taken reading in place of an older own one,
    while its time is not up."""
    now = time.time() if now is None else now
    try:
        taken = _load()
    except Exception:  # noqa: BLE001 - what was taken is a nicety: this Mac's own readings stand without it
        return own
    out = dict(own)
    for aid, e in taken.items():
        if not _num(e.get('until')) or now >= e['until'] or not _num(e.get('taken_at')) or e['taken_at'] > now + AHEAD:
            continue
        r = _reading(aid, e, now)
        mine = out.get(aid)
        if r is None or (mine is not None and mine.read_at is not None and mine.read_at >= r.read_at):
            continue
        out[aid] = r
    return out


def signature(now=None):
    """What changes when what is shown may change: the file, and which of its readings still have time."""
    now = time.time() if now is None else now
    try:
        s = os.stat(store_path())
    except OSError:
        return None
    return (s.st_ino, s.st_mtime_ns, s.st_size,
            tuple(sorted(aid for aid, e in _load().items() if _num(e.get('until')) and now < e['until'])))


# ---------- the command ----------

def _terminal_in():
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def duration(text):
    """Seconds from '10m', '1h', '90s' or a bare number of minutes. Raises Words."""
    m = re.fullmatch(r'(\d+(?:\.\d+)?)([smh]?)', text.strip().lower())
    if not m:
        raise Words('--for needs a time, like --for 10m, --for 1h or --for 90s.')
    s = float(m.group(1)) * {'s': 1, 'm': 60, 'h': 3600, '': 60}[m.group(2)]
    if s <= 0:
        raise Words('--for needs a time above zero, like --for 10m.')
    if s > FOR_MAX:
        raise Words('--for is an hour at most (--for 1h): older numbers are not worth showing.')
    return s


def main(args, out=None, err=None):
    out, err = out or sys.stdout, err or sys.stderr
    try:
        if core.DEMO:
            raise core.SwitchError('The demo has made-up accounts; it gives and takes no readings.')
        if args[:1] == ['give']:
            if len(args) > 1:
                raise Words('Unknown word: %s.' % args[1])
            out.write(json.dumps(give(), indent=1, ensure_ascii=False) + '\n')
            return 0
        if not args or args[0] != 'take':
            raise Words('Unknown word: %s.' % (args[0] if args else ''))
        src, for_s, i, rest = None, FOR_DEFAULT, 0, args[1:]
        while i < len(rest):
            a = rest[i]
            if a == '--for':
                if i + 1 >= len(rest):
                    raise Words('--for needs a time, like --for 10m.')
                i += 1
                for_s = duration(rest[i])
            elif (a == '-' or not a.startswith('-')) and src is None:
                src = a
            else:
                raise Words('Unknown word: %s.' % a)
            i += 1
    except Words as e:
        err.write('%s\n%s\n' % (e, USAGE))
        return 2
    try:
        if src in (None, '-'):
            if _terminal_in():
                err.write(WAITING + '\n')          # a person at a terminal, nothing piped in: say what it waits for
                err.flush()
            raw = sys.stdin.buffer.read(READ_MAX + 1)
        else:
            with open(os.path.expanduser(src), 'rb') as f:
                raw = f.read(READ_MAX + 1)
    except OSError as e:
        raise core.SwitchError('%s can\'t be read (%s).' % (src, e.strerror or e))
    try:
        doc = json.loads(raw[:READ_MAX + 1].decode('utf-8')) if len(raw) <= READ_MAX else None
    except (ValueError, UnicodeDecodeError, RecursionError):
        doc = None
    said = take(doc, for_s)
    from .outside import said as plain
    for email, words in said:
        out.write('%s: %s\n' % (plain(email), words))          # the email is the other document's, not ours
    if not said:
        out.write('No account in it.\n')
    return 0
