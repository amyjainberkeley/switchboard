"""What happened, one line each: the log behind `switchboard log` and the page's History.

Every event Switchboard records (core.event) also goes to <data>/history.jsonl. One JSON object per line: `t` (epoch
seconds), `kind`, `text` (the sentence the page says) and the facts behind it when they are known: `account`,
`from`, `to`, `by` (you, auto or claude-code) and `reason`; a limit adds `window`, `pct` and `line`, auto-switch `on`.

  switch   Claude Code moved to another account. The engine records each switch it makes itself (core.event), with
           its kind and source and one plain reason; one Switchboard only noticed is Claude Code's (a /login, or
           another program).
  kept     a login kept: by itself (auto) or by add (you)
  auto     auto-switch turned on or off, by you or by itself, and the loop that reads the accounts (and switches
           while auto-switch is on) restarted when it was stuck
  limit    an account reached a switch line, or a window that had reached one refilled
  signin   a sign-in, and what is known about which browser profile is signed in as which account
  repair   a switch that was cut off, finished or undone by the engine when it started again
  problem  something that did not work
  setting  a setting changed
  removed  an account taken out of Switchboard
  info     anything else

The kind and the facts come from the caller when it knows more than its sentence (core.event's keyword arguments),
else from the sentence itself (classify), so every place that records an event is logged without being changed.

The file only grows. At 5 MB it becomes history.jsonl.1 (the one before that goes) and a new one starts.

What a line holds: Switchboard's own sentence and the facts above, the sentence and the reason cut at 300 characters
and each other fact at 160, with control characters taken out. The sentences name accounts by email, browser
profiles by name, an API key by its name and last four characters, files and folders Switchboard changed by their
paths, and an artifact by its title as claude.ai shows it (the chat that published it named it), cut to 60
characters; a problem is said in the
engine's or the system's own words, cut the same way. What a line never holds: a login, a token, a
key, a prompt, a chat's text or a transcript line. Anything shaped like a key or a token is masked as well
(TOKEN_RE: a known key prefix and what follows it, a JWT, and a long run of key or base64 characters).
"""
import fcntl
import io
import json
import os
import re
import sys
import time

from . import core, names
from .notify import AUTO_ON        # auto-switch on by itself, in keep.py's words

MAX = 5 * 1024 * 1024
TEXT_MAX = 300
FACT_MAX = 160
REASON_MAX = 300            # a switch's reason is a whole sentence with two emails (policy.stay_note fits it)
KINDS = ('switch', 'kept', 'auto', 'limit', 'signin', 'repair', 'problem', 'setting', 'removed', 'info')
BY = ('you', 'auto', 'claude-code')
FACTS = ('account', 'from', 'to', 'by', 'reason', 'on', 'window', 'pct', 'line')
AUTO_FRESH = 900            # a switch the engine recorded is the one just noticed only when this recent (seconds)
PROBLEM_AGAIN = 3600        # a problem that cleared and came back is said again only after this long
D = 86400
USAGE = 'switchboard log [-n N] [-f] [--json]'

ANSI_RE = re.compile(r'\x1b\[[0-9;?]*[ -/]*[@-~]')
# Anything shaped like a key or a token, masked with only its known prefix kept ("sk-ant-…", "ghp_…", else "…"):
# - sk-ant- and what follows it;
# - another key's known prefix and what follows it, with a digit in it: sk-, sk_, rk_, key-, GitHub's ghp_ gho_ ghu_
#   ghs_ ghr_ github_pat_, GitLab's glpat-, Slack's xoxa- xoxb- xoxp- xoxr- xoxs-, hf_, npm_, AWS's AKIA ASIA, AIza;
# - a JWT: eyJ..., then a dot and more, whatever the length of its parts;
# - base64 with / or + in it: a run of 40 or more of its characters (= padding after) with letters and digits, that
#   does not start right after a / or a . (so a folder path or an address is never taken for one);
# - any other run of 40 or more key characters (letters, digits, - and _) with letters and digits in it.
KEY_PREFIXES = ('sk-', 'sk_', 'rk_', 'key-', 'ghp_', 'gho_', 'ghu_', 'ghs_', 'ghr_', 'github_pat_', 'glpat-',
                'xoxa-', 'xoxb-', 'xoxp-', 'xoxr-', 'xoxs-', 'hf_', 'npm_', 'AKIA', 'ASIA', 'AIza')
TOKEN_RE = re.compile(
    r'(?P<keep>sk-ant-)[A-Za-z0-9_-]{2,}'
    r'|(?<![A-Za-z0-9_-])(?P<prefix>' + '|'.join(re.escape(p) for p in KEY_PREFIXES) + r')'
    r'(?=[A-Za-z0-9_-]*[0-9])[A-Za-z0-9_-]{8,}'
    r'|(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_.-]+'
    r'|(?<![A-Za-z0-9+/_.-])(?=[A-Za-z0-9_-]*[+/])(?=[A-Za-z0-9+/_-]*[0-9])(?=[A-Za-z0-9+/_-]*[A-Za-z])'
    r'[A-Za-z0-9+_-][A-Za-z0-9+/_-]{39,}=*'
    r'|(?<![A-Za-z0-9_-])(?=[A-Za-z0-9_-]*[0-9])(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]{40,}')


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float('inf')


def clean(text, limit=TEXT_MAX):
    """One line of plain text: keys masked, control characters out, spaces closed up, cut at `limit`."""
    s = ANSI_RE.sub('', str(text))
    s = TOKEN_RE.sub(lambda m: (m.group('keep') or m.group('prefix') or '') + '…', s)
    s = ''.join(c if c >= ' ' and c != '\x7f' and not '\x80' <= c <= '\x9f' and c not in '  ' else ' '
                for c in s)
    s = ' '.join(s.split())
    return s if len(s) <= limit else s[:limit - 1] + '…'


# ---------- writing ----------

def path():
    return core.data_path('history.jsonl')


def _plain_number(v):
    v = float(v)
    return int(v) if v.is_integer() else round(v, 1)


def record(kind, text, t=None, **facts):
    """Append one line and return it (None in the demo, which writes nothing)."""
    if core.DEMO:
        return None
    rec = {'t': float(t) if _num(t) else time.time(), 'kind': kind if kind in KINDS else 'info', 'text': clean(text)}
    for k in FACTS:
        v = facts.get(k)
        if v is None or v == '':
            continue
        if k == 'on':
            rec[k] = bool(v)
        elif k in ('pct', 'line'):
            if _num(v):
                rec[k] = _plain_number(v)
        elif k == 'by':
            if v in BY:
                rec[k] = v
        else:
            rec[k] = clean(v, REASON_MAX if k == 'reason' else FACT_MAX)
    _append((json.dumps(rec, ensure_ascii=False) + '\n').encode('utf-8'))
    return rec


def _append(data):
    p = path()
    os.makedirs(os.path.dirname(p), mode=0o700, exist_ok=True)
    lock = os.open(p + '.lock', os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)            # the page's threads and a command in a terminal, one at a time
        try:
            if os.path.getsize(p) >= MAX:
                os.replace(p, p + '.1')
        except OSError:
            pass
        fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
    finally:
        os.close(lock)                              # closing it lets the lock go


def from_event(text, kind='info', facts=None):
    """core.event's line: the caller's facts (h is the history kind), and what its sentence says for the rest."""
    facts = dict(facts or {})
    h = facts.pop('h', None)
    if 'frm' in facts:
        facts['from'] = facts.pop('frm')
    got_kind, got = classify(text, kind)
    if h in KINDS and h != got_kind:                    # another kind than its sentence: only the account carries over
        got = dict((k, v) for k, v in got.items() if k == 'account')
    got.update((k, v) for k, v in facts.items() if v is not None)
    t = got.pop('t', None)
    return record(h if h in KINDS else got_kind, text, t=t, **got)


# ---------- each sentence Switchboard says, as a kind and its facts ----------

E = r'(?P<account>[^\s@"]+@[^\s@"]+\.[A-Za-z]{2,})'
# (the sentence, its kind, who did it, the reason in short words: a format over the sentence's own parts)
RULES = [(re.compile(p.replace('<E>', E)), kind, by, reason) for p, kind, by, reason in (
    (r'^Claude Code is now on <E>$', 'switch', 'claude-code', None),
    (r'^Switched Claude Code to <E>$', 'switch', 'you', None),
    (r'^Kept <E> · (?P<n>\d+) accounts?$', 'kept', 'auto', '{n} account{s}'),
    (r'^Could not keep <E>: (?P<why>.+?)\.? Trying again in (?:5 minutes|a minute)\.$', 'problem', None,
     'could not keep it: {why}'),
    (r'^Automatic switching (?P<on>on|off) \(', 'auto', 'you', None),
    (r'^Restarted (automatic switching|reading the accounts)', 'auto', 'auto', None),
    (r'^Signed in as <E>, not (?P<asked>[^\s,]+)\. It was already here', 'signin', 'you',
     'signed in as it, not {asked}: it was already here'),
    (r'^Added <E>, not (?P<asked>[^\s:]+): ', 'signin', 'you', 'added instead of {asked}'),
    (r'^Added <E>$', 'signin', 'you', 'added'),
    (r'^Opened the sign-in for <E> in (?P<where>.+)$', 'signin', 'you', 'sign-in opened in {where}'),
    (r'^Opened the sign-in in (?P<where>.+)$', 'signin', 'you', 'sign-in opened in {where}'),
    (r'^Matched <E> to the (?P<app>.+?) profile "(?P<p>[^"]*)"$', 'signin', 'auto', '{app} profile "{p}"'),
    (r'^Made the (?P<app>.+?) profile "(?P<p>[^"]*)" for <E>$', 'signin', 'you', 'new {app} profile "{p}"'),
    (r'^Learned that the (?P<app>.+?) profile "(?P<p>[^"]*)" is signed in as <E>$', 'signin', 'auto',
     '{app} profile "{p}"'),
    (r'^The browser profile for <E> is signed out of claude\.ai$', 'signin', 'auto',
     'signed out of claude.ai in its browser profile'),
    (r'^Learned that "(?P<title>.*)" opens as <E>$', 'info', 'auto', 'opens "{title}"'),
    (r'^The sign-in (timed out|did not finish)', 'problem', None, None),
    (r'^Removed every account from Switchboard$', 'removed', 'you', None),
    (r'^Removed <E> from Switchboard$', 'removed', 'you', None),
    (r'^(Added|Removed) the key ', 'setting', 'you', None),
    (r'^Keys gateway (on|off)$', 'setting', 'you', None),
    (r'^(Keep the Mac awake|Notifications|Sonnet for helpers|Models by the job|Status line in Claude Code): (on|off)$',
     'setting', 'you', None),
    (r'^(House rules|Code map|Watching chats with claude-mem|claude-mem) (on|off|cap set)$', 'setting', 'you', None),
)]


def classify(text, kind='info'):
    """(history kind, facts) for one of Switchboard's sentences. Anything not known here: a warning is a problem, the
    rest is info."""
    text = text if isinstance(text, str) else str(text)
    if text == AUTO_ON:
        return 'auto', {'on': True, 'by': 'auto'}
    for rx, k, by, reason in RULES:
        m = rx.match(text)
        if not m:
            continue
        parts = m.groupdict()
        facts = {}
        if parts.get('account'):
            facts['account'] = parts['account'].lower()
        if by:
            facts['by'] = by
        if k == 'switch':
            facts['to'] = facts.get('account')
        if k == 'auto' and parts.get('on'):
            facts['on'] = parts['on'] == 'on'
        if reason:
            n = parts.get('n')
            facts['reason'] = reason.format(s='' if n == '1' else 's', **dict((x, y or '') for x, y in parts.items()))
        return k, facts
    return ('problem' if kind == 'warn' else 'info'), {}


# ---------- a switch Switchboard noticed: one the engine recorded, or Claude Code's ----------

def _pct(v):
    r = int(round(v))
    if r >= 100 > v:
        return ('%.1f' % v).rstrip('0').rstrip('.')
    return '%d' % r


def recorded_switch(to, since=0.0, now=None, n=40):
    """The switch to `to` the engine (or the page, the menu bar, a terminal) recorded since `since` and in the last
    AUTO_FRESH seconds, newest first: its history line, or None. One noticed after it is the same switch."""
    now = time.time() if now is None else now
    to = names.email_of((to or '').lower())        # an account's name may carry its organization (names.py)
    for rec in newest(n):
        if rec.get('kind') != 'switch' or names.email_of((rec.get('to') or rec.get('account') or '').lower()) != to:
            continue
        if rec.get('by') == 'claude-code':
            continue
        t = rec.get('t')
        if _num(t) and t >= since - 5 and -60 <= now - t <= AUTO_FRESH:
            return rec
    return None


def noticed(frm, to, since=0.0, now=None):
    """The facts for a switch Switchboard noticed and did not make here: when the engine (its automatic loop, a hook,
    a terminal) recorded that switch itself, {'recorded': its line}; else Claude Code's own (a /login, or another
    program moved it)."""
    rec = recorded_switch(to, since, now)
    if rec is not None:
        return {'recorded': rec}
    return {'h': 'switch', 'from': frm, 'to': to, 'account': to, 'by': 'claude-code'}


# ---------- what the background tick sees: limits reached and refilled, problems that start ----------

LIMIT_WINDOWS = (('five_hour', 'session', '5-hour'), ('seven_day', 'weekly', 'weekly'))


def note_tick(st, before, after, lines, auto=None, error=None, now=None):
    """In the tick, after the accounts were read: each account that reached a switch line (once per window), each
    window that had reached it and refilled, and a problem the engine, automatic switching or (while auto-switch is
    off) the reading of the accounts started to have.
    `lines` is server.plan_settings: session_line (5-hour) and weekly_line, each window against its own."""
    now = time.time() if now is None else now
    _limits(st, before or {}, after or {}, lines or {}, now)
    _problems(st, {'engine': error, 'auto': (auto or {}).get('problem'),
                   'reading': (auto or {}).get('reading_problem')}, now)


def _limits(st, before, after, lines, now):
    from . import notify
    said = st.get('limits_said') if isinstance(st.get('limits_said'), dict) else {}
    said = dict((k, v) for k, v in said.items() if isinstance(v, list) and len(v) == 2 and _num(v[1])
                and now - v[1] < 8 * D)
    own = lines.get('own_lines') if isinstance(lines.get('own_lines'), dict) else {}
    for email, acct in sorted((after.get('accounts') or {}).items()):
        old = (((before.get('accounts') or {}).get(email) or {}).get('usage')) or {}
        new = (acct or {}).get('usage') or {}
        mine = own.get((acct or {}).get('id')) or {}         # its own line where it has one (auto line)
        five = lines.get('session_line', 90) if _num(lines.get('session_line')) else 90
        week = lines.get('weekly_line', 95) if _num(lines.get('weekly_line')) else 95
        five = mine['session_at'] if _num(mine.get('session_at')) else five
        week = mine['weekly_at'] if _num(mine.get('weekly_at')) else week
        for window, key, words in LIMIT_WINDOWS:
            line = five if window == 'five_hour' else week
            k = '%s|%s' % (email, window)
            if notify.refilled(email, window, old.get(key), new.get(key), line, now):
                said.pop(k, None)
                core.event(st, '%s: %s limit refilled' % (email, words), h='limit', account=email, window=words,
                           reason='%s refilled' % words)
                continue
            used0, _ = notify.window_of(old.get(key))
            used1, resets = notify.window_of(new.get(key))
            if used0 is None or used1 is None or used0 >= line or used1 < line or (resets is not None and resets <= now):
                continue
            was = said.get(k)
            if was is not None and (was[0] is None or resets is None or abs(was[0] - resets) < notify.SAME_WINDOW):
                continue                                # this window reached its line once already
            said[k] = [resets, now]
            core.event(st, '%s reached its %s switch line: %s%%' % (email, words, _pct(used1)), h='limit',
                       account=email, window=words, pct=used1, line=line, reason='%s at %s%%' % (words, _pct(used1)))
    st['limits_said'] = said


def _problems(st, now_said, now):
    said = st.get('problems_said') if isinstance(st.get('problems_said'), dict) else {}
    for key, text in sorted(now_said.items()):
        was = said.get(key) if isinstance(said.get(key), dict) else None
        if not text:
            if was:
                was['cleared'] = True
            continue
        text = str(text)
        if was and was.get('text') == text and (not was.get('cleared') or now - (was.get('at') or 0) < PROBLEM_AGAIN):
            continue
        said[key] = {'text': text, 'at': now, 'cleared': False}
        core.event(st, text, 'warn', h='problem')
    st['problems_said'] = said


# ---------- from the terminal, when the page is not running ----------

def note(text, kind='info', **facts):
    """Record one event from outside the page (it takes the state's lock)."""
    with core.locked() as st:
        core.event(st, text, kind, **facts)


def switched_here(email, frm=None, since=None):
    """`switchboard 2` with the page down: the engine switched; say so as the page would (unless the engine recorded
    it itself), and set the account in use, so the page does not take it for a switch it only noticed."""
    since = time.time() - 5 if since is None else since
    with core.locked() as st:
        if recorded_switch(email, since) is None:
            core.event(st, 'Switched Claude Code to %s' % email, 'switch', h='switch', to=email, account=email, by='you',
                       frm=frm or st.get('active'))
        core.set_active(st, email)
        st['switched_at'] = time.time()


def kept_by_add(before, by='you'):
    """After an add: each login the engine keeps now that it did not keep before is kept (by you, or by itself when
    `switchboard` kept it at once). `before`: engine.saved_identities() from before. Returns their emails."""
    from . import engine, keep
    after = engine.saved_identities()
    new = sorted(set(e for e, _ in after - set(before or ())))
    if new:
        with core.locked() as st:
            for email in new:
                core.event(st, keep.kept_line(email, len(after)), 'switch', h='kept', account=email, by=by,
                           reason='%d account%s' % (len(after), '' if len(after) == 1 else 's'))
    return new


def auto_turned(on, by='you'):
    """Auto-switch turned on or off outside the page (`switchboard auto on|off` with the page down)."""
    from . import engine
    a = engine.auto_settings()
    note('Automatic switching %s (5-hour %g%%, weekly %g%%)' % ('on' if on else 'off', a['session_at'], a['weekly_at']),
         h='auto', on=bool(on), by=by)


# ---------- reading ----------

def _good(x):
    return isinstance(x, dict) and _num(x.get('t')) and isinstance(x.get('kind'), str) and isinstance(x.get('text'), str)


def _records(p, n=None, at=None, skip=None):
    """The last n complete lines of one file (all of them when n is None), parsed, oldest first. `at` (a dict), when
    given, gets the file's inode and where its last complete line read ends ({'ino', 'pos'}): nothing after that is
    read. A file whose inode is `skip` is not read (it is the one already read, renamed since)."""
    try:
        f = open(p, 'rb')
    except OSError:
        return []
    with f:
        st = os.fstat(f.fileno())
        if skip is not None and st.st_ino == skip:
            return []
        pos = end = st.st_size
        if n is None:
            pos, data = 0, f.read(end)
        else:
            data = b''
            while pos > 0 and data.count(b'\n') <= n:
                step = min(65536, pos)
                pos -= step
                f.seek(pos)
                data = f.read(step) + data
            data = data[:end - pos]                     # nothing written after the size was taken
        cut = pos > 0
    if at is not None:
        at.update(ino=st.st_ino, pos=pos + data.rfind(b'\n') + 1)    # a last line still being written is read later
    if n == 0:
        return []
    pieces = data.split(b'\n')
    pieces = pieces[1:-1] if cut else pieces[:-1]       # a first piece cut short; a last line still being written
    out = []
    for raw in pieces:
        try:
            x = json.loads(raw.decode('utf-8'))
        except ValueError:
            continue
        if _good(x):
            out.append(x)
    return out if n is None else out[-n:] if n else []


def read(n=None, at=None):
    """The last n lines (all, when None), oldest first, across history.jsonl.1 and history.jsonl. `at` (a dict), when
    given, gets where history.jsonl was read up to ({'ino': None, 'pos': 0} when there is none yet): `switchboard
    log -f` carries on from exactly there."""
    seen = {'ino': None, 'pos': 0}
    if n is not None and n <= 0:
        _records(path(), 0, seen)
        cur = None
    else:
        cur = _records(path(), n, seen)
    if at is not None:
        at.update(seen)
    if cur is None:
        return []
    if n is None or len(cur) < n:                       # (one renamed to .1 since it was read here is not read again)
        cur = _records(path() + '.1', None if n is None else n - len(cur), skip=seen['ino']) + cur
    out = sorted(cur, key=lambda x: x['t'])             # a line can be dated earlier than it was written
    return out if n is None else out[-n:]


def newest(n):
    return list(reversed(read(n)))


# ---------- the lines, as an engineer's log ----------

def when(t, now=None):
    """'Sat 01:12' within the last six days, else 'Nov 7 01:12' (local time)."""
    now = time.time() if now is None else now
    lt = time.localtime(t)
    if -60 <= now - t < 6 * D:
        return time.strftime('%a %H:%M', lt)
    return '%s %d %s' % (time.strftime('%b', lt), lt.tm_mday, time.strftime('%H:%M', lt))


def parts(rec):
    """(what the line is about, who did it and why): ('a@x → b@x', 'auto: 5-hour at 91%, refills first')."""
    kind, frm, to, acct = rec.get('kind'), rec.get('from'), rec.get('to'), rec.get('account')
    by, reason = rec.get('by'), rec.get('reason')
    if kind == 'switch' and to:
        subject = '%s → %s' % (frm, to) if frm and frm != to else to
    elif kind == 'auto' and 'on' in rec:
        subject = 'on' if rec['on'] else 'off'
    elif acct and kind not in ('setting',):
        subject = acct
    else:
        subject = rec.get('text') or ''
    tail = (by + (': ' + reason if reason else '')) if by else (reason or '')
    return subject, tail


def format_lines(recs, now=None, widths=None):
    """One aligned line each, oldest first as given: when, kind, what, by: why."""
    now = time.time() if now is None else now
    rows = [(when(x['t'], now), x['kind']) + parts(x) for x in recs]
    w = widths or columns(rows)
    return [('%-*s  %-*s  %-*s  %s' % (w[0], a, w[1], b, w[2], c, d)).rstrip() for a, b, c, d in rows]


def columns(rows):
    """Widths for the first three columns; a very long one does not push every line out."""
    return (max([len(r[0]) for r in rows] + [9]), max([len(r[1]) for r in rows] + [4]),
            min(64, max([len(r[2]) for r in rows] + [1])))


def page(n=200):
    """For GET /api/history: the newest n lines, newest first, each with the log's own two columns."""
    out = []
    for x in newest(n):
        subject, tail = parts(x)
        out.append(dict(x, subject=subject, tail=tail))
    return {'entries': out}


# ---------- switchboard log ----------

def follow(write, as_json=False, every=0.5, sleep=time.sleep, stop=None, widths=None, start=None):
    """Print each new line as it is written, until Ctrl-C (or `stop`). Follows the file through a rotation. `start`
    (read's `at`: where the lines already shown were read up to) carries on from exactly there, so a line written in
    between is printed too; without it, from the end of the file as it is now."""
    p = path()
    if start is not None:
        ino, pos = start.get('ino'), start.get('pos') or 0
    else:
        try:
            st = os.stat(p)
            ino, pos = st.st_ino, st.st_size
        except OSError:
            ino, pos = None, 0

    def emit(chunk):
        for raw in chunk.split(b'\n'):
            try:
                x = json.loads(raw.decode('utf-8')) if raw.strip() else None
            except ValueError:
                x = None
            if _good(x):
                write(json.dumps(x, ensure_ascii=False) if as_json else format_lines([x], widths=widths)[0])

    def rest(name, start):
        try:
            with open(name, 'rb') as f:
                f.seek(start)
                data = f.read()
        except OSError:
            return 0
        end = data.rfind(b'\n') + 1
        emit(data[:end])
        return end

    while not (stop and stop.is_set()):
        try:
            st = os.stat(p)
        except OSError:
            st = None
        if st is not None and ino is not None and st.st_ino != ino:      # rotated: finish the old file, then the new
            try:
                if os.stat(p + '.1').st_ino == ino:
                    rest(p + '.1', pos)
            except OSError:
                pass
            ino, pos = st.st_ino, 0
        elif st is not None and ino is None:
            ino, pos = st.st_ino, 0
        if st is not None and st.st_size > pos:
            pos += rest(p, pos)
        sleep(every)


def main(args, out=None):
    """`switchboard log [-n N] [-f] [--json]`: the last 20 lines, oldest first, then (with -f) each new one."""
    out = out or sys.stdout
    n, keep_going, as_json, i = 20, False, False, 0
    while i < len(args):
        a = args[i]
        if a in ('-f', '--follow'):
            keep_going = True
        elif a == '--json':
            as_json = True
        elif a == '-n' and i + 1 < len(args) and args[i + 1].isdigit():
            n, i = int(args[i + 1]), i + 1
        elif a.startswith('-n') and a[2:].isdigit():
            n = int(a[2:])
        else:
            if '--json' in args:                        # one document on standard output, also for this
                from . import scripted
                return scripted.wrong_words(USAGE, out)
            out.write(USAGE + '\n')
            return 2
        i += 1
    try:
        _show(out, n, keep_going, as_json)
    except KeyboardInterrupt:                           # Ctrl-C ends -f: nothing more to say
        pass
    except BrokenPipeError:                             # `switchboard log | head`: the reader has what it wanted
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), out.fileno())     # so the exit's own flush is quiet too
        except (OSError, AttributeError, ValueError, io.UnsupportedOperation):
            pass
    return 0


def _show(out, n, keep_going, as_json):
    at = {}
    recs = read(n, at=at)
    rows = [(when(x['t']), x['kind']) + parts(x) for x in recs]
    widths = columns(rows)
    if as_json:
        for x in recs:
            out.write(json.dumps(x, ensure_ascii=False) + '\n')
    elif recs:
        for line in format_lines(recs, widths=widths):
            out.write(line + '\n')
    elif n and not keep_going:
        out.write('Nothing yet.\n')
    out.flush()
    if keep_going:
        def write(line):
            out.write(line + '\n')
            out.flush()
        follow(write, as_json=as_json, widths=widths, start=at)
