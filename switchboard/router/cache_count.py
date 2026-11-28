"""The person's own count behind the helpers' cache switch (cache_setting.py).

Claude Code keeps a record for each helper and workflow agent a chat starts:

    <projects>/<project>/<session>/subagents/agent-<id>.jsonl
    <projects>/<project>/<session>/subagents/workflows/wf_<id>/agent-<id>.jsonl

count() reads those records and says how often a helper had to store its whole context in the cache again after a
wait of five minutes to an hour: the waits a one-hour cache lifetime would have bridged. Claude Code's prompt caching
page (https://code.claude.com/docs/en/prompt-caching, saved copy read 2026-11-27) says why: helpers get a five-minute
cache unless the person chooses an hour, each request that hits the cache resets its timer, and after a long enough
gap "the next request recomputes the full input and re-establishes the cache".

What is read: only the numbers and times of each request line (its time, its model name, and the usage block's input,
output, cache-read and cache-write counts, with the part written for an hour). Each line is parsed and everything
else in it is dropped at once: no word of a prompt, an answer, a tool call, a path, a folder or a name is kept,
returned, printed or written. The model name is turned into the catalog's family name (providers/catalog.py) and
then dropped. Ids are used only in memory, to count a request written on several lines once.

A re-store, as counted here: a request of a helper whose context (input + cache read + cache write) is at least
MIN_CONTEXT tokens, that reads at most READ_MAX of it from the cache and writes at least WRITE_MIN of it, coming
SHORT_S seconds or more, and less than LONG_S, after the helper's request before it, when that request wrote nothing
for an hour (its cache was most likely a five-minute one). The tokens stored again are the part that was already in
the cache: the smaller of what the re-store wrote and the request before it (its context plus its answer). An hour
lifetime reads up to those from the cache; every other five-minute cache write of the helpers is written at the hour
rate.

What the count cannot know, and does not claim:
- Why a cache was cold. A wait is the likeliest reason, but the server may drop a cache sooner, and a change of model
  or of the helper's tools also starts over. It counts what the records show, not the cause.
- The life of a cache that only was read. A request that wrote nothing shows no lifetime; a wait after it is read as
  a wait on a five-minute cache (the helpers' own unless the person chose an hour), which is wrong if an hour cache
  was already on.
- The wait itself. It measures from one request's time to the next one's, as the record writes them; the time a
  reply took to stream is inside that gap.
- Money, or a share of a plan's limit. How a plan counts cache reads and writes is not published, so the count gives
  tokens only, never a price or a percent of a limit.
- Whether an hour cache would have been worth it: an hour write is dearer, and a helper that never waits five
  minutes gets only that.
- Requests a fork copied from the chat or the helper it started from: a record is read as it is, so a copied
  request counts again in the fork's record.
- Records Claude Code has already removed, records on another Mac, and the main chat (its cache is a separate
  setting, and it is not read).

It is bounded: the last WINDOW_DAYS days at most, the newest records first, each file read line by line, a record it
cannot read skipped and counted, at most LIMIT_S seconds of work by default (it says when it stopped early), and
nothing kept between runs except one small cache file of per-record totals (CACHE_FILE under Switchboard's data
folder), keyed by a salted hash of the record's place, its size and its change time. A record that takes the whole time
limit by itself is marked there and left out (and said) while it is unchanged, so it cannot hold back the older ones;
the count of such a record is not known.

Nothing here turns anything on. These are functions a later step calls.
"""
import hashlib
import json
import math
import os
import re
import secrets
import stat
import time

from .. import core
from ..providers import catalog
from ..usage import parse_ts
from . import count_words as W

WINDOW_DAYS = 28
DAY_S = 86400
SHORT_S = 300.0             # the five-minute lifetime helpers get unless the person chooses an hour
LONG_S = 3600.0             # the hour lifetime: a wait this long or longer re-stores even with an hour cache
MIN_CONTEXT = 20000         # a smaller context stored again is left out: it is cheap and often a short helper's start
READ_MAX = 0.10             # a re-store reads at most this share of its context from the cache
WRITE_MIN = 0.50            # and writes at least this share of it
MAX_TOKENS = 10 ** 12       # a token count past this on one request is not a real one: it is read as 0
LIMIT_S = 60
CHECK_EVERY = 100           # request lines between two looks at the clock inside one record (a line can be large)
LONG_LINE = 1000000         # and a request line longer than this (in bytes) is a look of its own: it is slow to parse
CACHE_FILE = os.path.join('router', 'cache-count.json')
CACHE_VERSION = 1
OTHER = 'Other'
LONG_MARK = 'long'          # in the cache file: a record that took the whole time limit and was not finished

RESULT_KEYS = ('v', 'since', 'until', 'days', 'records_read', 'records_unread', 'records_left', 'records_too_long',
               'from_cache',
               'bad_lines', 'requests', 'restores', 'restore_tokens', 'restores_over_an_hour', 'cache_writes',
               'cache_writes_1h', 'hour_would_read', 'hour_would_write', 'by_family', 'stopped_early', 'found_all',
               'limit_s', 'took_s')
FAMILY_KEYS = ('requests', 'restores', 'restore_tokens', 'cache_writes')

# The per-record totals, in this order, in the cache file and in memory.
_TOTALS = ('requests', 'restores', 'restore_tokens', 'restores_over_an_hour', 'cache_writes', 'cache_writes_1h',
           'bad_lines')


class _Unreadable(Exception):
    """A record that could not be opened, or whose request lines all failed to parse."""


class _Stop(Exception):
    """The time limit was reached inside a record."""


def projects_folders():
    """Where Claude Code keeps its records on this Mac: [<Claude Code folder>/projects]."""
    return [os.path.join(core.claude_dir(), 'projects')]


def empty(since, until):
    """A count of nothing, for the window since..until."""
    out = dict((k, 0) for k in RESULT_KEYS)
    out.update(v=1, since=since, until=until, days=_days(since, until), by_family={}, stopped_early=False,
               found_all=True, took_s=0.0)
    return out


def count(folders, since=None, now=None, limit_s=LIMIT_S, cache_file=None, clock=None):
    """Count the helpers' re-stores after a wait of SHORT_S to LONG_S seconds, from since to now.

    folders: Claude Code projects folders (projects_folders() on this Mac). since: epoch seconds, never earlier than
    WINDOW_DAYS before now (an earlier one is moved up to it); None means the whole WINDOW_DAYS. cache_file: None
    for CACHE_FILE under Switchboard's data folder, False for none, or a path. clock: seconds, for the time limit
    (time.monotonic by default).

    Returns only numbers and the catalog's family names (RESULT_KEYS, by_family of FAMILY_KEYS), never a word read
    from a record.
    """
    clock = clock or time.monotonic
    started = clock()
    now = time.time() if now is None else float(now)
    floor = now - WINDOW_DAYS * DAY_S
    since = floor if since is None else max(float(since), floor)
    out = empty(since, now)
    out['limit_s'] = limit_s
    names = _family_names()
    families = set(name for _, name in names) | {OTHER}
    path = _cache_path(cache_file)
    old, salt = _load_cache(path)
    new = {}
    over = lambda: clock() - started > limit_s      # noqa: E731
    found, out['found_all'] = _records(folders, since, over)
    # A record that once had half the time limit or more and did not finish is left out while it is unchanged and the
    # limit is no longer, and tried after all the others once it changed: so one long record never holds back the
    # ones older than it.
    records, later = [], []
    for rec in found:
        key = _key(rec[0], salt)
        mark = _long_mark(old.get(key))
        if mark is None:
            records.append(rec)
        elif mark[0] == rec[1] and mark[1] == rec[2] and limit_s <= mark[3]:
            new[key] = old[key]
            out['records_too_long'] += 1
        else:
            later.append(rec)
    records += later
    stopped_at = None if out['found_all'] else 0
    for n, (place, size, mtime_ns) in enumerate(records if stopped_at is None else ()):
        begun = clock()
        if begun - started > limit_s:
            stopped_at = n
            break
        key = _key(place, salt)
        hit = old.get(key)
        if _fits(hit, size, mtime_ns, since, now, families):
            new[key] = hit
            totals = hit
            out['from_cache'] += 1
        else:
            try:
                rows = _read_record(place, over)
            except _Stop:
                if begun - started <= limit_s / 2:
                    new[key] = [size, mtime_ns, LONG_MARK, limit_s]     # had half the time or more, not finished
                stopped_at = n
                break
            except _Unreadable:
                out['records_unread'] += 1
                continue
            totals = [size, mtime_ns] + _totals(rows, since, now, names)
            if totals[2] is None or (totals[2] >= since and totals[3] <= now):
                new[key] = totals     # the whole record is in the window, so its totals hold for any window it fits
        out['records_read'] += 1
        _add(out, totals)
    out['records_left'] = out['records_too_long']
    if stopped_at is not None:
        out['stopped_early'] = True
        out['records_left'] += len(records) - stopped_at
        for place, _, _ in records[stopped_at:]:      # not reached this time: what the cache file knew stays
            key = _key(place, salt)
            if key in old:
                new.setdefault(key, old[key])
        if not out['found_all']:
            for key, hit in old.items():              # records it did not get to look for may still be there
                new.setdefault(key, hit)
    out['hour_would_read'] = out['restore_tokens']
    # Writes already made for an hour paid the hour rate; the re-stores become reads. The rest is what an hour cache
    # writes at its rate.
    out['hour_would_write'] = max(0, out['cache_writes'] - out['cache_writes_1h'] - out['restore_tokens'])
    if path:
        _save_cache(path, new, salt)
    out['took_s'] = round(max(0.0, clock() - started), 3)
    return out


def say(result):
    """The sentences the cache switch shows from a count, in order (count_words.py)."""
    days = result.get('days')
    days = WINDOW_DAYS if days is None else days
    when, span = _window_words(days)
    read, unread = result.get('records_read', 0), result.get('records_unread', 0)
    left = result.get('records_left', 0)
    if not read and not unread and not result.get('stopped_early') and not result.get('records_too_long'):
        return [W.NO_RECORDS.format(span=span)]
    n = result.get('restores', 0)
    size = _num(MIN_CONTEXT)
    if not read:
        lines = [W.NOTHING_COUNTED.format(span=span)]
    elif n == 1:
        lines = [W.COUNTED_ONE.format(when=when, size=size)]
    elif n:
        lines = [W.COUNTED_MANY.format(when=when, size=size, n=_num(n))]
    elif unread or result.get('stopped_early') or result.get('records_too_long'):
        # Not every record was read: say how many were, never that no helper had to.
        found = W.NONE_FOUND_ONE if read == 1 else W.NONE_FOUND_MANY
        lines = [found.format(size=size, read=_num(read))]
    else:
        lines = [W.NONE.format(when=when, size=size)]
    if n:
        lines.append(W.FORKS)
        lines.append(W.TOKENS.format(read=_num(result.get('hour_would_read', 0)),
                                     write=_num(result.get('hour_would_write', 0))))
        lines.append(W.WEIGH)
    if unread == 1:
        lines.append(W.UNREAD_ONE)
    elif unread:
        lines.append(W.UNREAD_MANY.format(n=_num(unread)))
    limit = _num(result.get('limit_s', LIMIT_S))
    too_long = result.get('records_too_long', 0)
    if too_long == 1:
        lines.append(W.TOO_LONG_ONE.format(s=limit))
    elif too_long:
        lines.append(W.TOO_LONG_MANY.format(n=_num(too_long), s=limit))
    if result.get('stopped_early'):
        if result.get('found_all', True):
            lines.append(W.STOPPED.format(s=limit, read=_num(read), total=_num(read + unread + left)))
        else:
            lines.append(W.STOPPED_LOOKING.format(s=limit))   # how many records there are is not known
    return lines


# ---------------------------------------------------------------- finding the records

def _records(folders, since, over=None):
    """([(place, size, mtime_ns)], all found) of every helper's and workflow agent's record changed since `since`,
    newest first. Links are not followed. A record last changed before the window holds nothing in it and is not
    opened. over() is asked before each chat's folder; when it says the time is up, the search stops and "all found"
    is False."""
    found = []
    for root in folders or ():
        for project in _dirs(root):
            for session in _dirs(project):
                if over is not None and over():
                    found.sort(key=lambda r: (-r[2], r[0]))
                    return found, False
                sub = os.path.join(session, 'subagents')
                if os.path.islink(sub):
                    continue
                for dirpath, dirnames, filenames in os.walk(sub):
                    dirnames[:] = sorted(d for d in dirnames if not os.path.islink(os.path.join(dirpath, d)))
                    for fn in filenames:
                        if not (fn.startswith('agent-') and fn.endswith('.jsonl')):
                            continue
                        place = os.path.join(dirpath, fn)
                        try:
                            st = os.lstat(place)
                        except OSError:
                            continue
                        if not stat.S_ISREG(st.st_mode) or st.st_mtime < since:
                            continue
                        found.append((place, st.st_size, st.st_mtime_ns))
    found.sort(key=lambda r: (-r[2], r[0]))
    return found, True


def _key(place, salt):
    """A record's name in the cache file: a hash of its place keyed with the file's own salt, so no folder or file
    name is written and a guessed place cannot be checked against the file."""
    return hashlib.blake2b(place.encode('utf-8', 'replace'), digest_size=12, key=bytes.fromhex(salt)).hexdigest()


def _dirs(folder):
    try:
        return sorted(e.path for e in os.scandir(folder) if e.is_dir(follow_symlinks=False))
    except OSError:
        return []


# ---------------------------------------------------------------- reading one record

def _read_record(place, over):
    """[(time, family source, input, output, cache write, cache read, hour write)] of one record, one per request.

    A request is one (message id, request id) pair on an assistant line; Claude Code writes a reply once per content
    block, so its numbers are the largest seen and its time the earliest. Raises _Unreadable when the file cannot be
    read or every request line in it is broken, and _Stop when over() says the time is up."""
    reqs = {}
    bad = good = seen = 0
    try:
        with open(place, 'rb') as f:
            for line in f:
                if b'"usage"' not in line or b'assistant' not in line:
                    continue
                seen += 1
                if (seen % CHECK_EVERY == 0 or len(line) > LONG_LINE) and over():
                    raise _Stop()
                row = _numbers(line)
                if row is None:
                    if line.endswith(b'\n'):
                        bad += 1        # a last line with no newline may still be being written: not broken
                    continue
                good += 1
                if row is False:
                    continue
                key, r = row
                had = reqs.get(key)
                if had is None:
                    reqs[key] = r
                    continue
                if r[0] is not None and (had[0] is None or r[0] < had[0]):
                    had[0] = r[0]
                if not had[1]:
                    had[1] = r[1]
                for i in range(2, 7):
                    if r[i] > had[i]:
                        had[i] = r[i]
    except OSError:
        raise _Unreadable()
    if bad and not good:
        raise _Unreadable()
    rows = [r for r in reqs.values() if r[0] is not None]
    rows.sort(key=lambda r: r[0])
    return {'rows': rows, 'bad': bad}


def _numbers(line):
    """(key, [time, model, input, output, cache write, cache read, hour write]) from one line; False for a line that
    is JSON but not a request; None for a line that is not JSON. Everything else on the line is dropped here."""
    try:
        d = json.loads(line)
    except (ValueError, RecursionError):      # RecursionError: a line nested deeper than Python's stack
        return None
    if not isinstance(d, dict) or d.get('type') != 'assistant':
        return False
    m = d.get('message')
    if not isinstance(m, dict) or m.get('model') == '<synthetic>' or not isinstance(m.get('usage'), dict):
        return False
    mid, rid = m.get('id'), d.get('requestId')
    if not isinstance(mid, str) and not isinstance(rid, str):
        return False
    key = hashlib.blake2b(('%s|%s' % (mid, rid)).encode('utf-8', 'replace'), digest_size=8).digest()
    u = m['usage']
    made = u.get('cache_creation') if isinstance(u.get('cache_creation'), dict) else {}
    hour = _n(made.get('ephemeral_1h_input_tokens'))
    parts = _n(made.get('ephemeral_5m_input_tokens')) + hour     # the write, when only its parts are given
    model = m.get('model') if isinstance(m.get('model'), str) else ''
    return key, [_time(d.get('timestamp')), model, _n(u.get('input_tokens')), _n(u.get('output_tokens')),
                 max(_n(u.get('cache_creation_input_tokens')), parts), _n(u.get('cache_read_input_tokens')), hour]


def _time(value):
    """Epoch seconds of a request's time, or None. usage.parse_ts raises on a few odd strings (a digit like "\u00b2"
    passes isdigit() but not int()); such a time is no time, and the line is still read."""
    try:
        return parse_ts(value)
    except (ValueError, OverflowError, TypeError):
        return None


def _n(value):
    """A token count: a non-negative int, else 0. NaN, Infinity and numbers past MAX_TOKENS (a JSON line can hold
    any of them) are 0 too."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    if isinstance(value, float) and not math.isfinite(value):
        return 0
    if value < 0 or value > MAX_TOKENS:
        return 0
    return int(value)


# ---------------------------------------------------------------- counting

def _totals(read, since, now, names):
    """[first time, last time, *_TOTALS, {family: FAMILY_KEYS list}]: the times of the record's first and last
    request (in or out of the window; None when it has none), then the totals of its requests in since..now."""
    t = dict((k, 0) for k in _TOTALS)
    t['bad_lines'] = read['bad']
    fams = {}
    rows = read['rows']
    prev = None
    for r in rows:
        ts, model, i, o, cw, cr, hour = r
        fam = _family(model, names)
        r[1] = None             # the model's own name goes; only its family is kept
        if since <= ts <= now:
            f = fams.setdefault(fam, [0, 0, 0, 0])
            t['requests'] += 1
            t['cache_writes'] += cw
            t['cache_writes_1h'] += hour
            f[0] += 1
            f[3] += cw
            if prev is not None and _restored(i, cw, cr) and prev[6] == 0:
                gap = ts - prev[0]
                if SHORT_S <= gap < LONG_S:
                    again = min(cw, prev[2] + prev[3] + prev[4] + prev[5])
                    t['restores'] += 1
                    t['restore_tokens'] += again
                    f[1] += 1
                    f[2] += again
                elif gap >= LONG_S:
                    t['restores_over_an_hour'] += 1
        prev = r
    first, last = (rows[0][0], rows[-1][0]) if rows else (None, None)
    return [first, last] + [t[k] for k in _TOTALS] + [fams]


def _restored(i, cw, cr):
    ctx = i + cw + cr
    return ctx >= MIN_CONTEXT and cr <= READ_MAX * ctx and cw >= WRITE_MIN * ctx


def _add(out, totals):
    for k, v in zip(_TOTALS, totals[4:4 + len(_TOTALS)]):
        out[k] += v
    for fam, nums in totals[4 + len(_TOTALS)].items():
        f = out['by_family'].setdefault(fam, dict((k, 0) for k in FAMILY_KEYS))
        for k, v in zip(FAMILY_KEYS, nums):
            f[k] += v


def _family_names():
    """[(model word, family name)] from the catalog, the longest word first, so a model name holding the word is
    read as that family. Every provider's models are there; a model name that holds none is OTHER."""
    try:
        entries = catalog.load()
    except catalog.CatalogError:
        try:
            entries = catalog.load(own=False)
        except catalog.CatalogError:
            return []
    pairs = [(m.id.lower(), m.name) for e in entries.values() for m in e.models]
    return sorted(pairs, key=lambda p: -len(p[0]))


def _family(model, names):
    low = (model or '').lower()
    for word, name in names:
        if word and word in low:
            return name
    return OTHER


def _window_words(days):
    """(the words that start a sentence, the words that end one) for a window of `days` whole days."""
    if days <= 0:
        return W.WHEN_TODAY, W.SPAN_TODAY
    if days == 1:
        return W.WHEN_DAY, W.SPAN_DAY
    return W.WHEN_DAYS.format(days=_num(days)), W.SPAN_DAYS.format(days=_num(days))


def _days(since, until):
    return max(0, int(round((until - since) / DAY_S)))


def _num(n):
    """A whole number with commas (2,010), or a number with one decimal when it is not whole."""
    if isinstance(n, float) and not n.is_integer():
        return '{:,.1f}'.format(n)
    return '{:,}'.format(int(n))


# ---------------------------------------------------------------- the cache file

def _is_count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _long_mark(hit):
    """(size, mtime_ns, LONG_MARK, limit_s) when the cache file says a record took the whole time limit, else None."""
    if isinstance(hit, list) and len(hit) == 4 and hit[2] == LONG_MARK and all(
            isinstance(x, int) and not isinstance(x, bool) for x in (hit[0], hit[1])) and isinstance(
            hit[3], (int, float)) and not isinstance(hit[3], bool):
        return hit
    return None


def _cache_path(cache_file):
    if cache_file is False:
        return None
    if cache_file:
        return cache_file
    try:
        return core.data_path(CACHE_FILE)
    except (core.SwitchError, RuntimeError, OSError):
        return None


def _fits(hit, size, mtime_ns, since, now, families):
    """A cached record's totals stand for this window only when the file is unchanged and every request in it falls
    inside since..now (then the window's totals are the record's own). Otherwise it is read again. The cache file is
    trusted no further than its shape: the totals must be counts and each family one of `families`, so nothing but
    numbers and the catalog's names can come back through it."""
    if not isinstance(hit, list) or len(hit) != 4 + len(_TOTALS) + 1:
        return False
    if hit[0] != size or hit[1] != mtime_ns or not isinstance(hit[-1], dict):
        return False
    if not all(_is_count(v) for v in hit[4:4 + len(_TOTALS)]):
        return False
    for fam, nums in hit[-1].items():
        if fam not in families or not isinstance(nums, list) or len(nums) != len(FAMILY_KEYS) or \
                not all(_is_count(v) for v in nums):
            return False
    first, last = hit[2], hit[3]
    if first is None:
        return True             # a record with no request at all
    return isinstance(first, (int, float)) and isinstance(last, (int, float)) and since <= first and last <= now


def _load_cache(path):
    """(records, salt) from the cache file. A file that is missing, broken or has no salt gives no records and a new
    salt (made once here and written with the file)."""
    records, salt = {}, None
    if path:
        try:
            with open(path, encoding='utf-8') as f:
                d = json.load(f)
        except (OSError, ValueError, RecursionError):
            d = None
        if isinstance(d, dict) and d.get('v') == CACHE_VERSION and isinstance(d.get('records'), dict) and \
                isinstance(d.get('salt'), str) and re.match(r'^[0-9a-f]{32}\Z', d['salt']):
            records, salt = d['records'], d['salt']
    return records, salt or secrets.token_hex(16)


def _save_cache(path, records, salt):
    """Write the cache file through Switchboard's own writer (core.write_bytes: whole or not at all, mode 0600, its
    folder 0700). A cache file that cannot be written is left out: the next count reads the records again."""
    try:
        text = json.dumps({'v': CACHE_VERSION, 'salt': salt, 'records': records}, separators=(',', ':'))
        core.write_bytes(path, text.encode('utf-8'))
    except OSError:     # DiskFull is an OSError too
        pass
