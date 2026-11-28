"""Artifacts: which account owns each one, and opening it where that account is signed in.

A claude.ai artifact opens only for the account that published it. Everyone
else sees "Page not found". Two things tell Switchboard who the owner is:

1. When it was published. Claude Code's session hooks note each publish the
   moment it happens (hooks/published.jsonl), and its chat history on this Mac
   stamps every publish and every "my artifacts" listing with a time; Switchboard
   knows which account Claude Code was on at that time.
2. The page itself. After opening a link, Switchboard reads the tab's title. A
   real title means the right account. "Page not found" means the wrong one, so
   it closes that tab, tries the next profile, and remembers what worked.

The second is the proof. The first only decides which profile to try first.

Each account's browser profile is a link with a state (link_state), and a proof expires:
  proved     one of its pages loaded there within PROOF_DAYS (the date is kept with the proof)
  old        proved, but longer ago than that: it is checked again before it is trusted
  unproved   picked by the person or guessed from the browser's own data, never shown yet
  suspect    since its last proof the profile showed claude.ai's sign-in page, or another account's page loaded there
  missing    its folder is gone from the browser
  none       no profile
A quiet daily check re-proves one page per account (reprove_tick). An account with no profile signed in as it says
"No Chrome profile is signed in as this account", with a Set up action (setup_profile) that opens claude.ai/login in a
new profile named for the account.

The owner map. After every change the page writes ~/.switchboard/owners.json (owner_map): each artifact's owner and
the profile it opens in, each account's link and Claude in Chrome facts, the account Claude Code is on and its recent
switches. The link app, the command line (open_link, owner_of) and Claude Code's session hooks read it directly, so a
link still opens as its owner when the page is slow or down: in the owner's profile, never in whatever window was
used last.
"""
import collections
import glob
import json
import os
import re
import threading
import time
from urllib.parse import parse_qs, quote, urlsplit

from . import browser, linkapp
from . import term
from .core import (SwitchError, _in_tests, data_path, event, load, locked, log, name_of, owner_seg, parse_iso,
                   profile_unproven, projects, signed_in_here, update_setting)

ART_RE = re.compile(r'https://claude\.ai/(?:code/)?artifact/([A-Za-z0-9_-]{16,})')
ART_URL = r'(https://claude\.ai/(?:code/)?artifact/[A-Za-z0-9_-]{16,})'
HEAD_RE = re.compile(r'(?:Published (?P<file>.+?) at|Created a new Artifact at|Updated the Artifact at) ' + ART_URL)
LIST_HEAD_RE = re.compile(r'\d+ published artifacts?|Showing \d+ of \d+ published artifacts|\d+ artifacts?, published by you')
MINE_RE = re.compile(r'^- \(mine\) (.+?) — ' + ART_URL + r'(?: — updated (\d{4}-\d\d-\d\d))?', re.M)
DOC_RE = re.compile(r'"frame":\{"slug":"[^"]+","url":"' + ART_URL + '"')
SCAN_V = 1      # raise this to read all chat history again with new rules
CALLS_KEPT = 20 # Artifact calls a chat's file may have waiting for their answers between two reads
KEEP = 800
# How sure we are of an owner. "seen" means the page really loaded in that profile.
RANK = {None: 0, 'guess': 1, 'published': 2, 'listed': 3, 'seen': 4}
FROM_HISTORY = ('published', 'listed')
SURE = ('you', 'checked')      # a profile link the person chose, or one a page load proved
GO_LOCK = threading.Lock()     # one open or check in the browser at a time
GO_WAIT = 3.0       # a clicked link waits this long for another open or a check, then opens from the owner map
GO_PAGE_WAIT = 10.0     # the page's Open and `switchboard open` wait this long for one, then say so
SCAN_LOCK = threading.Lock()
TITLE_SAID = 60     # an artifact's title, in a line of the page's list and the history: at most this many characters
PROOF_DAYS = 14     # a proof older than this is "old": checked again before it is trusted
DAY = 86400
SETUP_GRACE = 3600  # a profile Set up made counts as there for this long before the browser lists it
POSTER_SLACK = 0.2  # the link app's timer gives the page this long past fallback_after (router/SwitchboardLinks.swift)
AHEAD = 300         # a proof dated further than this past the clock is not trusted for its date
FILE_MAX = 1024     # characters of a local file's path kept with an artifact


def said_title(title):
    """An artifact's title as a line names it: one line, cut to TITLE_SAID characters."""
    t = ' '.join(str(title).split())
    return t if len(t) <= TITLE_SAID else t[:TITLE_SAID - 1] + '…'


def art_id(url):
    m = ART_RE.search(url or '')
    return m.group(1) if m else None


# ---------- link forms ----------

ID_PATH_RE = re.compile(r'^/(?:code/)?artifact/([A-Za-z0-9_-]{16,})(?:/|$)')
URL_IN_TEXT_RE = re.compile(r'(?i)https?://[^\s<>"\'|`]+')
TRAILING = '.,;:!?)]}>\'"'


def _unwrap(parts):
    """The link inside a well-known redirect (Outlook's safe links, Google's /url, Slack's redirect), or None."""
    host, q = (parts.hostname or '').lower(), parse_qs(parts.query)
    if host.endswith('.safelinks.protection.outlook.com'):
        names = ('url',)
    elif host in ('www.google.com', 'google.com') and parts.path == '/url':
        names = ('q', 'url')
    elif host == 'slack-redir.net' and parts.path == '/link':
        names = ('url',)
    else:
        return None
    for n in names:
        if q.get(n):
            return q[n][0]
    return None


def parse_link(text, _depth=0):
    """A claude.ai artifact link, in whatever form it came: {'url': the link written plainly, 'id': the artifact's
    id}, or None. Takes a link inside text (the first one), in angle brackets or Slack's <link|label>, with a trailing
    full stop or bracket, behind a well-known redirect, with an uppercase scheme or host, or with :443. Only https and
    only the host claude.ai itself (never claude.ai.example, never a login part like claude.ai@example)."""
    if not isinstance(text, str) or len(text) > 8192:
        return None
    m = URL_IN_TEXT_RE.search(text)
    if not m:
        return None
    s = m.group(0).rstrip(TRAILING)
    try:
        parts = urlsplit(s)
        port = parts.port
    except ValueError:
        return None
    if parts.username is not None or parts.password is not None:
        return None
    inner = _unwrap(parts) if _depth < 2 else None
    if inner:
        return parse_link(inner, _depth + 1)
    if parts.scheme.lower() != 'https' or (parts.hostname or '').lower() != 'claude.ai' or port not in (None, 443):
        return None
    m = ID_PATH_RE.match(parts.path)
    if not m:
        return None
    url = 'https://claude.ai' + parts.path + ('?' + parts.query if parts.query else '') + \
        ('#' + parts.fragment if parts.fragment else '')
    return {'url': url, 'id': m.group(1)}


def find(st, aid):
    return next((x for x in st['artifacts'] if x.get('id') == aid), None)


def blank_item(aid, when):
    return {'id': aid, 'url': 'https://claude.ai/artifact/' + aid, 'name': None, 'title': None,
            'first': when, 't': when, 'owner': None, 'src': None, 'ot': None, 'profile': None}


# ---------- reading chat history ----------

def take_owner(item, owner, src, when):
    """Keep the better-supported owner; between equals, the newer one."""
    if not owner:
        return
    have, new = RANK.get(item.get('src'), 0), RANK[src]
    if new > have or (new == have and (when or 0) >= (item.get('ot') or 0)):
        item.update(owner=owner, src=src, ot=when)


def note_artifact(st, known, url, t, src, name=None, title=None, changed=None, file=None):
    """t is when the chat saw it (that decides the owner); changed is when the artifact itself last changed. file: the
    local file this publish named (kept as 'file', with its time as 'ft', for the page's picture: previews.py)."""
    aid = art_id(url)
    if not aid or not t:
        return
    changed = changed or t
    item = known.get(aid)
    if item is None:
        item = known[aid] = blank_item(aid, changed)
    item['first'], item['t'] = min(item.get('first') or changed, changed), max(item.get('t') or 0, changed)
    if title:
        item['title'] = title
    if name and name != 'index':
        item['name'] = name
    if file and t >= (item.get('ft') or 0):
        item['file'], item['ft'] = file, t
    owner, sure = owner_seg(st, t)
    take_owner(item, owner, src if sure else 'guess', t)


def local_file(path, cwd=None):
    """A local file's path as a publish named it, absolute and plain, or None (a relative one is taken from `cwd`):
    never one with a control character, never longer than FILE_MAX."""
    if not isinstance(path, str) or not path or len(path) > FILE_MAX or any(c < ' ' or c == '\x7f' for c in path):
        return None
    if not os.path.isabs(path):
        if not isinstance(cwd, str) or not os.path.isabs(cwd):
            return None
        path = os.path.join(cwd, path)
    path = os.path.normpath(path)
    return path if len(path) <= FILE_MAX else None


def page_name(f):
    """An artifact's name from the file it was published from: the file's name, or its folder's for an index.html."""
    name = os.path.splitext(os.path.basename(f))[0] if f else None
    if name == 'index':                     # index.html says nothing; its folder does
        name = os.path.basename(os.path.dirname(f)) or None
    return name


def read_doc_names(line, names):
    """A Claude Doc's title is in the request that creates it; its link only comes back in the answer."""
    try:
        d = json.loads(line)
    except ValueError:
        return
    content = (d.get('message') or {}).get('content')
    for block in content if isinstance(content, list) else []:
        if isinstance(block, dict) and block.get('type') == 'tool_use' and str(block.get('name', '')).endswith('Claude_Docs__batch'):
            box = (block.get('input') or {}).get('container')
            name = ((box.get('create') or {}).get('name') if isinstance(box, dict) else None)
            if isinstance(name, str) and name.strip():
                names[block.get('id')] = name.strip()


def read_artifact_calls(line, calls):
    """The ids of the Artifact tool's calls in one line of chat history, added to `calls`. Only an answer to one of
    them is a publish: any other tool's output (a file a shell command printed) can start with the same words."""
    try:
        d = json.loads(line)
    except ValueError:
        return
    if not isinstance(d, dict) or d.get('type') != 'assistant' or not isinstance(d.get('message'), dict):
        return
    content = d['message'].get('content')
    for block in content if isinstance(content, list) else []:
        if isinstance(block, dict) and block.get('type') == 'tool_use' and block.get('name') == 'Artifact' \
                and isinstance(block.get('id'), str):
            calls.add(block['id'])


def read_history_line(st, known, line, names, calls):
    try:
        d = json.loads(line)
    except ValueError:
        return
    if d.get('type') != 'user':
        return
    content = (d.get('message') or {}).get('content')
    if not isinstance(content, list):
        return
    t = parse_iso(d.get('timestamp'))
    for block in content:
        if not isinstance(block, dict) or block.get('type') != 'tool_result' or block.get('is_error'):
            continue
        text = block.get('content')
        if isinstance(text, list):
            text = '\n'.join(x.get('text', '') for x in text if isinstance(x, dict))
        if not isinstance(text, str):
            continue
        # Only a result that STARTS this way counts, so a chat that merely
        # prints old history can't pass for a publish. And only the Artifact tool's own answer: a call this read did
        # not see (it lies before where the read began) leaves the answer unknown, so it is not taken.
        tid = block.get('tool_use_id')
        m = HEAD_RE.match(text) if isinstance(tid, str) and tid in calls else None
        if isinstance(tid, str):
            calls.discard(tid)               # answered: it waits no longer
        if m:
            f = m.group('file')
            note_artifact(st, known, m.group(2), t, 'published', name=page_name(f),
                          file=local_file(f, d.get('cwd')) if f else None)
        elif text.startswith('{"verdict"') and '"created":' in text[:4000]:
            m = DOC_RE.search(text)          # a Claude Doc being born
            if m:
                note_artifact(st, known, m.group(1), t, 'published', title=names.get(block.get('tool_use_id')))
        elif LIST_HEAD_RE.match(text):
            for m in MINE_RE.finditer(text):
                day = parse_iso(m.group(3) + 'T12:00:00') if m.group(3) else None
                note_artifact(st, known, m.group(2), t, 'listed', title=m.group(1), changed=min(day, t) if day else None)


def history_files(root=None):
    root = root or projects()
    return glob.glob(os.path.join(root, '*', '*.jsonl')) + glob.glob(os.path.join(root, '*', '*', 'subagents', '*.jsonl'))


def scan(st, root=None):
    """Read only what chat history added since last time."""
    if st.get('scan_v') != SCAN_V:
        # New rules: start over, keeping only owners that a real page load proved.
        st['scan'] = {}
        st['scan_calls'] = {}
        st['artifacts'] = [x for x in st['artifacts'] if x.get('src') == 'seen' and x.get('id')]
        st['scan_v'] = SCAN_V
    known = {x['id']: x for x in st['artifacts'] if x.get('id')}
    # Claude Code writes an Artifact call before its answer (the person approves it, the page is sent), so a read can
    # end between the two. The call then waits here, by its chat's file, for the read that brings the answer.
    waiting = st.setdefault('scan_calls', {})
    view = None                             # who was on when: the switch history set right by History's own times
    for path in history_files(root):
        try:
            size = os.stat(path).st_size
        except OSError:
            continue
        offset = st['scan'].get(path, 0)
        if size < offset:
            offset = 0
        if size == offset:
            continue
        asked = [i for i in waiting.get(path) or [] if isinstance(i, str)] if offset else []
        try:
            names, calls = {}, set(asked)
            with open(path, 'rb') as f:
                f.seek(offset)
                for line in f:
                    if not line.endswith(b'\n'):
                        break                # still being written; next time
                    offset += len(line)
                    if b'Claude_Docs__batch' in line and b'"create"' in line and b'tool_use' in line:
                        read_doc_names(line, names)
                    if b'"Artifact"' in line and b'tool_use' in line:
                        read_artifact_calls(line, calls)
                        asked += sorted(calls.difference(asked))
                    if b'artifact/' in line and b'claude.ai/' in line and b'tool_result' in line:
                        if view is None:
                            view = dict(st, timeline=switch_timeline(st))
                        read_history_line(view, known, line, names, calls)
        except OSError:
            continue
        st['scan'][path] = offset
        asked = [i for i in asked if i in calls][-CALLS_KEPT:]
        if asked:
            waiting[path] = asked
        else:
            waiting.pop(path, None)
    for p in [p for p in st['scan'] if not os.path.exists(p)]:
        st['scan'].pop(p)
    for p in [p for p in waiting if p not in st['scan']]:
        waiting.pop(p)
    st['artifacts'] = sorted(known.values(), key=lambda x: x.get('t') or 0, reverse=True)[:KEEP]


def refresh():
    """Scan on a copy (the first read takes a few seconds), then fold the result in. One scan at a time. The page's
    tick runs this every minute, so it also writes the owner map when anything in it changed, and, in the page's own
    process, starts the quiet daily re-proof when one is due."""
    with SCAN_LOCK:
        _refresh()
    try:
        write_owner_map()
    except Exception as e:  # noqa: BLE001 - the map is a fallback; it never stops the tick
        log('the owner map was not written: %r' % e)
    try:
        start_reprove()
    except Exception as e:  # noqa: BLE001
        log('the link check did not start: %r' % e)


def _refresh():
    snapshot = load()
    scan(snapshot)
    with locked() as st:
        if st.get('scan_v') != SCAN_V:
            st['artifacts'] = [x for x in st['artifacts'] if x.get('src') == 'seen' and x.get('id')]
        known = {x['id']: x for x in st['artifacts'] if x.get('id')}
        for new in snapshot['artifacts']:
            old = known.get(new['id'])
            if old is None:
                known[new['id']] = new
                continue
            take_owner(old, new.get('owner'), new.get('src'), new.get('ot'))
            old['first'] = min(old.get('first') or new['first'], new['first'])
            old['t'] = max(old.get('t') or 0, new.get('t') or 0)
            old['title'] = old.get('title') or new.get('title')
            old['name'] = old.get('name') or new.get('name')
            if new.get('file') and (new.get('ft') or 0) > (old.get('ft') or 0):
                old['file'], old['ft'] = new['file'], new['ft']
        st['artifacts'] = sorted(known.values(), key=lambda x: x.get('t') or 0, reverse=True)[:KEEP]
        st['scan'], st['scan_v'] = snapshot['scan'], SCAN_V
        st['scan_calls'] = snapshot.get('scan_calls') or {}
        take_published(st)                  # and what the session hooks noted since: seconds old, not a minute


# ---------- what Claude Code just published: the session hooks' record ----------
#
# Each artifact a session publishes, creates or updates is one line in hooks/published.jsonl the moment Claude Code
# answers (session_hooks.published_line): its link and id, the local file the answer named, the time and the session.
# The page takes new lines on each pass of its loop (published_tick) and before an open or a lookup (_refresh), and
# `switchboard owner` and `switchboard open` read them directly when the page is down, so a page made a minute ago
# opens as its owner, never "not known yet". The owner is the account Claude Code was on at that time, from the switch
# history (switch_timeline), by the history scan's own rule: near a switch it is only a guess, and the first open
# checks it in the browser.

PUBLISHED = os.path.join('hooks', 'published.jsonl')
PUB_LINE_MAX = 8192             # a longer line is not one the hooks wrote
PUB_READ_MAX = 2 << 20          # bytes of the record read at once (the hooks keep it under 256 KB)
ID_RE = re.compile(r'[A-Za-z0-9_-]{16,128}')    # whole: fullmatch, never a trailing newline
HOWS = ('published', 'updated', 'created', 'doc')
FILES_V = 1                     # raise this to look through all chat history again for the files pages came from
_PUB = {'seen': None}


def published_files():
    """(the record's older half, the record): hooks/published.jsonl.1 and hooks/published.jsonl."""
    p = data_path(PUBLISHED)
    return p + '.1', p


def _whole_lines(path, start=0, ino=None):
    """(the whole lines of `path` from byte `start`, (its inode, where they end)), or ([], None) when there is no such
    file or it is not the one with inode `ino`. A last line still being written is left for next time."""
    try:
        f = open(path, 'rb')
    except OSError:
        return [], None
    with f:
        info = os.fstat(f.fileno())
        if ino is not None and info.st_ino != ino:
            return [], None
        start = start if 0 <= start <= info.st_size else 0
        f.seek(start)
        data = f.read(min(info.st_size - start, PUB_READ_MAX))
    end = data.rfind(b'\n') + 1
    if end == 0 and len(data) >= PUB_READ_MAX:
        end = len(data)                     # no line in all that: not the hooks' writing, passed over
    return data[:end].split(b'\n')[:-1] if end else [], (info.st_ino, start + end)


def _good_line(raw, now):
    """One line of the record as {'t', 'id', 'url', 'how', 'file', 'title'}, or None when it is not one the hooks
    wrote: an id that is not an artifact's, a time from the future, a relative or overlong file path."""
    if len(raw) > PUB_LINE_MAX:
        return None
    try:
        rec = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None
    if not isinstance(rec, dict) or rec.get('v') != 1:
        return None
    t, aid = rec.get('t'), rec.get('id')
    if isinstance(t, bool) or not isinstance(t, (int, float)) or not 1e9 < t < now + AHEAD:
        return None
    if not isinstance(aid, str) or not ID_RE.fullmatch(aid):
        return None
    url = rec.get('url')
    if url not in ('https://claude.ai/artifact/' + aid, 'https://claude.ai/code/artifact/' + aid):
        url = 'https://claude.ai/artifact/' + aid
    f = rec.get('file')
    f = local_file(f) if isinstance(f, str) and os.path.isabs(f) else None
    title = rec.get('title') if isinstance(rec.get('title'), str) else None
    title = ' '.join(''.join(c if c >= ' ' and c != '\x7f' else ' ' for c in title).split())[:200] or None \
        if title else None
    return {'t': float(t), 'id': aid, 'url': url, 'how': rec.get('how') if rec.get('how') in HOWS else 'published',
            'file': f, 'title': title}


def read_published(pos=None, now=None):
    """The record's new lines since `pos` ({'ino', 'off', 't'}: the file last read, how far, the newest line's time),
    oldest first, and the new pos. The record moved aside since (published.jsonl.1 has the inode last read) is
    finished first, then the new one is read from its start. Lost track of (both moved on since, or a first read), the
    whole of both is read, from a little before pos's time."""
    now = time.time() if now is None else now
    older, current = published_files()
    pos = dict(pos) if isinstance(pos, dict) else {}
    ino, off, newest = pos.get('ino'), pos.get('off') or 0, pos.get('t')
    raw, floor, at = [], None, None
    lines, got = _whole_lines(current, off, ino) if ino is not None else ([], None)
    if got is not None:                     # the file read last time, still in place
        raw, at = lines, got
    else:
        lines, got = _whole_lines(older, off, ino) if ino is not None else ([], None)
        if got is not None:                 # the file read last time, moved aside: finish it, then the new one
            raw, at = lines, got
        else:                               # lost track: all of it, from a little before the last line taken
            raw = _whole_lines(older)[0]
            floor = newest - 5 if isinstance(newest, (int, float)) else None
        lines, got = _whole_lines(current)
        if got is not None:
            raw, at = raw + lines, got
    out = []
    for line in raw:
        rec = _good_line(line, now)
        if rec is not None and (floor is None or rec['t'] >= floor):
            out.append(rec)
            newest = max(newest or 0, rec['t'])
    new = {'ino': at[0], 'off': at[1]} if at is not None else {}
    if newest is not None:
        new['t'] = newest
    return out, new


def switch_timeline(st):
    """Which account Claude Code was on, and since when: the page's own switch history (st['timeline'], each switch
    when the page noticed it: up to a pass of its loop late, and later still while the page was down), set right by the
    switches History recorded (history.jsonl: each switch the engine made, at the moment it made it). The switches
    History has between two of the page's entries, when the last of them is to the account the page saw next, are when
    those switches happened; the ones after the page's last entry are switches it has not seen yet. So a publish made
    after a switch the page noticed late, or has not noticed, gets the account switched to."""
    tl = [[e, float(t)] for e, t in (x for x in st.get('timeline') or [] if isinstance(x, (list, tuple)) and len(x) == 2)
          if isinstance(e, str) and isinstance(t, (int, float)) and not isinstance(t, bool)]
    try:
        from . import history
        recs = history.newest(40)
    except Exception:  # noqa: BLE001 - no History: the page's own timeline
        recs = []
    switches = []
    for r in recs:
        who, t = (r.get('to') or r.get('account') or ''), r.get('t')
        if r.get('kind') == 'switch' and isinstance(who, str) and '@' in who and isinstance(t, (int, float)) \
                and not isinstance(t, bool):
            switches.append((float(t), who.lower()))
    switches.sort()
    out, since = [], None
    for who, t in tl:
        gap = [s for s in switches if (since is None or s[0] > since) and s[0] <= t]
        if gap and gap[-1][1] == who:           # History agrees with what the page saw next: its times are the real ones
            out.extend([w, at] for at, w in gap)
        out.append([who, t])
        since = t
    out.extend([w, at] for at, w in switches if since is None or at > since)
    merged = []
    for who, t in out:                          # one entry per switch: an account's first time, not when it was noticed
        if not merged or merged[-1][0] != who:
            merged.append([who, t])
    return merged


def _take_line(view, known, rec):
    note_artifact(view, known, rec['url'], rec['t'], 'published', name=page_name(rec.get('file')),
                  title=rec.get('title'), file=rec.get('file'))


def take_published(st, now=None):
    """The record's new lines, taken into `st` (in place): each artifact with its owner from the switch history (as
    the history scan takes one: near a switch only a guess) and the local file its publish named. The record's place
    is kept in st['published']. Returns how many lines were taken."""
    pos = st.get('published') if isinstance(st.get('published'), dict) else None
    lines, new = read_published(pos, now)
    if new != (pos or {}):
        st['published'] = new
    if not lines:
        return 0
    known = {x['id']: x for x in st['artifacts'] if x.get('id')}
    view = dict(st, timeline=switch_timeline(st))
    for rec in lines:
        _take_line(view, known, rec)
    st['artifacts'] = sorted(known.values(), key=lambda x: x.get('t') or 0, reverse=True)[:KEEP]
    return len(lines)


def _record_seen():
    out = []
    for p in published_files():
        try:
            s = os.stat(p)
            out.append((s.st_ino, s.st_size, s.st_mtime_ns))
        except OSError:
            out.append(None)
    return tuple(out)


def take_published_now():
    """The record's new lines, taken into the saved state now, and owners.json written again when they changed it.
    How many. Nothing is written when there is nothing new."""
    pos = load().get('published')
    lines, new = read_published(pos if isinstance(pos, dict) else None)
    if not lines and new == (pos if isinstance(pos, dict) else {}):
        return 0
    with locked() as st:
        n = take_published(st)
    if n:
        _map_soon()
    return n


def published_tick():
    """Each pass of the page's loop: the record's new lines go into the state and owners.json at once
    (take_published_now), and the pictures still missing are started (previews.kick). Two stats when nothing is new."""
    seen = _record_seen()
    taken = 0
    if seen != _PUB['seen']:
        taken = take_published_now()
        _PUB['seen'] = seen
    from . import previews
    previews.kick(new=bool(taken))
    return taken


def published_owner(aid, st=None, now=None):
    """What the record says of one artifact, read directly (an open while the page is down): {'email': its owner from
    the switch history, or None; 'src': 'published', or 'guess' near a switch; 'file', 'title'}, or None when no line
    names it."""
    lines = [r for r in read_published(None, now)[0] if r['id'] == aid]
    if not lines:
        return None
    st = load() if st is None else st
    known, view = {}, dict(st, timeline=switch_timeline(st))
    for rec in lines:
        _take_line(view, known, rec)
    item = known.get(aid) or {}
    return {'email': item.get('owner'), 'src': item.get('src'), 'file': item.get('file'), 'title': item.get('title')}


def _published_files_in(line, calls):
    """[(id, local file, time)] of the publishes in one line of chat history that named a file, in answers to the
    Artifact tool's calls (`calls`, from read_artifact_calls)."""
    try:
        d = json.loads(line)
    except ValueError:
        return []
    if not isinstance(d, dict) or d.get('type') != 'user':
        return []
    content = (d.get('message') or {}).get('content') if isinstance(d.get('message'), dict) else None
    t = parse_iso(d.get('timestamp')) if isinstance(d.get('timestamp'), str) else None
    out = []
    for block in content if isinstance(content, list) and t else []:
        if not isinstance(block, dict) or block.get('type') != 'tool_result' or block.get('is_error') \
                or not isinstance(block.get('tool_use_id'), str) or block['tool_use_id'] not in calls:
            continue
        text = block.get('content')
        if isinstance(text, list):
            text = '\n'.join(x.get('text', '') for x in text if isinstance(x, dict) and isinstance(x.get('text'), str))
        m = HEAD_RE.match(text) if isinstance(text, str) else None
        f = local_file(m.group('file'), d.get('cwd')) if m and m.group('file') else None
        if f:
            out.append((m.group(2).rsplit('/', 1)[-1], f, t))
    return out


def backfill_files(root=None):
    """Once (until FILES_V changes): the local file each known artifact was last published from, looked for in all of
    Claude Code's history on this Mac, since the scan reads only what is new and pages it knew before files were kept
    have none. Each known artifact without a file gets one; st['files_v'] says it was done. How many got one."""
    found = {}
    for path in history_files(root):
        calls = set()
        try:
            with open(path, 'rb') as f:
                for line in f:
                    if b'"Artifact"' in line and b'tool_use' in line:
                        read_artifact_calls(line, calls)
                    if b'Published ' in line and b'tool_result' in line and b'claude.ai/' in line:
                        for aid, file, t in _published_files_in(line, calls):
                            if aid not in found or t >= found[aid][1]:
                                found[aid] = (file, t)
        except OSError:
            continue
    n = 0
    with locked() as st:
        for x in st['artifacts']:
            got = found.get(x.get('id'))
            if got and not x.get('file'):
                x['file'], x['ft'] = got
                n += 1
        st['files_v'] = FILES_V
    return n


# ---------- the accounts as folders, their artifacts as files ----------

NOT_MATCHED = 'Not matched yet'


def folders(st, names, pictures=None):
    """Every artifact this Mac has published, in one folder per account (each account Switchboard has, in slot order,
    then any other owner, such as an account since removed) and one "Not matched yet" folder (no owner known yet).
    A folder: {id, email, name, count, changed (its newest change), profile, profile_name, files}. A file: {id, title,
    changed, url, owner, profile, profile_name (the browser profile it opens in: the one it loaded in, else its
    owner's), sure (its owner is more than a guess near a switch), preview (Switchboard has a picture of the page:
    GET /api/preview/<id>)}. `names` is {profile folder: name}; `pictures`: the ids with a picture (previews.known)."""
    if pictures is None:
        from . import previews
        pictures = previews.known()
    accounts = st.get('accounts') or {}
    order = [e for e in sorted(st.get('order') or [], key=lambda e: (accounts.get(e) or {}).get('slot') or 99)
             if (accounts.get(e) or {}).get('connected')]
    by_owner = collections.OrderedDict((e, []) for e in order)
    loose = []
    for x in st.get('artifacts') or []:
        if not x.get('id') or not x.get('url'):
            continue
        owner = x.get('owner') or None
        profile = x.get('profile') or (accounts.get(owner) or {}).get('profile') if owner else x.get('profile')
        f = {'id': x['id'], 'title': x.get('title') or x.get('name') or '', 'changed': x.get('t'), 'url': x['url'],
             'owner': owner, 'profile': profile or None, 'profile_name': names.get(profile, profile) if profile else '',
             'sure': RANK.get(x.get('src'), 0) >= 2, 'preview': x['id'] in pictures}
        (by_owner.setdefault(owner, []) if owner else loose).append(f)

    def folder(fid, email, name, files):
        files = sorted(files, key=lambda f: -(f['changed'] or 0))
        profile = (accounts.get(email) or {}).get('profile') if email else None
        return {'id': fid, 'email': email, 'name': name, 'count': len(files),
                'changed': files[0]['changed'] if files else None, 'profile': profile,
                'profile_name': names.get(profile, profile) if profile else '', 'files': files}
    others = sorted(e for e in by_owner if e not in order)
    out = [folder(e, e, name_of(st, e), by_owner[e]) for e in order + others]
    out.append(folder('unmatched', None, NOT_MATCHED, loose))
    return {'folders': out, 'count': sum(f['count'] for f in out)}


# ---------- each account's link to a profile, and how sure it is ----------

def _bound(a, key):
    """A proof or a doubt, only while it is about the profile the account links to now (another part of Switchboard
    may have moved the link without knowing about proofs)."""
    v = a.get(key)
    return v if isinstance(v, dict) and v.get('profile') and v.get('profile') == a.get('profile') \
        and isinstance(v.get('at'), (int, float)) else None


def link_state(a, names=None, now=None):
    """(state, at, why) of an account's link to its browser profile: 'none', 'missing' (its folder is gone; only said
    when the browser's profile list could be read), 'suspect' (at: when; why: 'signin', 'wrong' or 'other_account'),
    'old' (at: the proof's date), 'proved' (at: the proof's date, None when not known) or 'unproved'."""
    now = time.time() if now is None else now
    p = (a or {}).get('profile')
    if not p:
        return 'none', None, None
    if names and p not in names:
        made = a.get('setup_at')
        if isinstance(made, (int, float)) and 0 <= now - made < SETUP_GRACE:
            return 'unproved', None, 'new'          # Set up just made it: the browser lists it within seconds
        return 'missing', None, 'gone'
    proof, doubt = _bound(a, 'proof'), _bound(a, 'doubt')
    proved_at = proof['at'] if proof else None
    if doubt and (proved_at is None or doubt['at'] >= proved_at):
        return 'suspect', doubt['at'], doubt.get('why') or 'wrong'
    out = a.get('signed_out')
    if isinstance(out, (int, float)) and (proved_at is None or out >= proved_at):
        return 'suspect', out, 'signin'
    if proof:
        if proved_at > now + AHEAD:                         # a date from the future: the proof stands, its age is not known
            return 'proved', None, None
        return ('old' if now - proved_at > PROOF_DAYS * DAY else 'proved'), proved_at, None
    if a.get('profile_src') == 'checked':                   # proved before proofs were dated, or by a sign-in there
        return 'proved', None, None
    return 'unproved', None, a.get('profile_src')


def _solid(a, names, now):
    """A link a page load would not overturn: the person picked it, or a page proved it lately, and nothing has
    contradicted it since."""
    return bool(a) and a.get('profile_src') in SURE and link_state(a, names, now)[0] in ('proved', 'unproved')


def opens_in(a, names=None, now=None):
    """The profile an account's pages should open in now, or None when no profile is signed in as it: its link while
    proved, old or unproved, and while signed out too (one sign-in there fixes it); never a profile that showed another
    account's page, nor one that is gone."""
    state, _, why = link_state(a, names, now)
    if state in ('proved', 'old', 'unproved') or (state == 'suspect' and why == 'signin'):
        return a.get('profile')
    return None


def _one_account_per_profile(st, who, profile, now):
    """A page of `who` loaded in `profile`: one profile holds one claude.ai login, so any other account's link to it
    is now in doubt (never two accounts proved on one profile)."""
    for e, b in st['accounts'].items():
        if e != who and b.get('profile') == profile:
            b['doubt'] = {'profile': profile, 'at': now, 'why': 'other_account', 'by': who}


def _doubt(st, email, profile, why, now=None):
    a = st['accounts'].get(email)
    if a is not None and a.get('profile') == profile:
        a['doubt'] = {'profile': profile, 'at': time.time() if now is None else now, 'why': why}


# ---------- opening one where its owner is signed in ----------

def candidates(st, item, names=None):
    """Profiles worth trying, most likely first, as [(profile folder, account email or None)]. Its own owner's profile
    first, then the profile it loaded in before, then other accounts' profiles (best links first, then the account
    with most pages, then the account in use), then profiles signed in to claude.ai that no account links to yet.
    A profile now proved for another account goes last, and for a page whose owner a page load proved, profiles
    proved for other accounts are not tried at all: they can't open it."""
    names = browser.profiles() if names is None else names
    now = time.time()
    accounts = st['accounts']
    email_of = {}
    for email in st['order']:
        p = (accounts.get(email) or {}).get('profile')
        if p in names:
            email_of.setdefault(p, email)
    owned = collections.Counter(x.get('owner') for x in st['artifacts'])
    owner = item.get('owner') if item else None
    sure_owner = bool(owner) and RANK.get(item.get('src'), 0) >= 2
    seen_owner = sure_owner and item.get('src') == 'seen'
    scored = {}

    def add(profile, score, email=None):
        if profile in names and (profile not in scored or score < scored[profile][0]):
            scored[profile] = (score, len(scored), email_of.get(profile) or email)

    def taken_by_other(profile):
        e = email_of.get(profile)
        return bool(owner) and e is not None and e != owner and \
            link_state(accounts[e], names, now)[0] in ('proved', 'old')

    # For a page nobody owns yet, proved profiles first. For a page whose owner history names, another account's
    # proved profile is the least likely to open it (it is proved to be that other account): unproved ones first.
    rank_of = {'proved': 1.0, 'unproved': 0, 'old': 0.3, 'suspect': 0.6} if sure_owner else \
        {'proved': 0, 'unproved': 0.3, 'old': 0.3, 'suspect': 0.8}
    if item:
        own = accounts.get(owner) if owner else None
        if own and own.get('profile'):
            ok = opens_in(own, names, now) is not None
            add(own['profile'], 0 if ok else 8, owner)
        loaded = item.get('profile')
        if loaded:
            add(loaded, 6 if taken_by_other(loaded) else 1, owner)
    others = sorted(email_of.items(), key=lambda x: (rank_of.get(link_state(accounts[x[1]], names, now)[0], 1),
                                                    -owned.get(x[1], 0), x[1] != st.get('active')))
    for i, (profile, email) in enumerate(others):
        if email == owner:
            continue
        state = link_state(accounts[email], names, now)[0]
        if seen_owner and state in ('proved', 'old'):
            continue                        # a proved profile of another account can't open a page proved to be the owner's
        add(profile, 2 + i * 0.01 + (3 if sure_owner else 0) + rank_of.get(state, 1), email)
    orgs = {a.get('org_id') for a in accounts.values()}
    for profile in sorted(browser.hints(orgs)):                          # signed in to claude.ai, not linked yet
        if profile not in email_of:
            add(profile, 7)
    if not scored:
        for profile in sorted(names)[:6]:
            add(profile, 9)
    return [(p, v[2]) for p, v in sorted(scored.items(), key=lambda kv: (kv[1][0], kv[1][1]))]


def learn_ok(aid, profile, email, title, signed_out):
    """The page loaded in this profile. Write down what that proves and return the owner's email."""
    now = time.time()
    names = browser.profiles()
    with locked() as st:
        it = find(st, aid)
        if it is None:
            it = blank_item(aid, now)
            st['artifacts'].insert(0, it)
        label = names.get(profile, profile)
        who = email if email in st['accounts'] else None
        said = it.get('owner') if it.get('src') in FROM_HISTORY else None
        if said and said != who and said in st['accounts']:
            link = st['accounts'].get(who) if who else None
            theirs = st['accounts'][said]
            if not link or not _solid(link, names, now):
                # Chat history names another account, and this profile was only a
                # guess for the one we tried. The page loading here settles it.
                if not theirs.get('profile') or not _solid(theirs, names, now):
                    if link and link.get('profile') == profile:
                        link.update(profile=None, profile_src=None)
                    theirs.update(profile=profile, profile_src='checked')
                    event(st, 'Learned that the %s profile "%s" is signed in as %s' % (browser.app(), label, name_of(st, said)))
                who = said
        if who:
            a = st['accounts'][who]
            if not a.get('profile'):
                a.update(profile=profile, profile_src='checked')
            elif a['profile'] == profile and a.get('profile_src') != 'you':
                a['profile_src'] = 'checked'
            elif a['profile'] != profile and not _solid(a, names, now):
                # Its pages load here, and its old link was never proved, is old, or was contradicted: point it here.
                old = names.get(a['profile'], a['profile'])
                a.update(profile=profile, profile_src='checked')
                event(st, 'Learned that %s opens in the %s profile "%s" now (not "%s")' % (
                    name_of(st, who), browser.app(), label, old))
            if a.get('profile') == profile:
                a.pop('signed_out', None)
                a['proof'] = {'profile': profile, 'at': now}
                a.pop('doubt', None)
                a.pop('recheck', None)
            signed_in_here(st, who, profile)        # whoever linked the profile: the person, Switchboard, a guess
            _one_account_per_profile(st, who, profile, now)
            if it.get('owner') != who:
                event(st, 'Learned that "%s" opens as %s' % (said_title(title or it.get('title') or aid[:8]),
                                                              name_of(st, who)))
        it.update(owner=who, src='seen', ot=now, profile=profile, title=title or it.get('title'))
        mark_signed_out(st, signed_out)
    _map_soon()
    return who


def learn_wrong(aid, profile, email):
    """This profile said "Page not found". Forget whatever pointed here. When it is the owner's own profile, that
    link is checked again soon with another of its pages (the page may only have been deleted)."""
    names = browser.profiles()
    with locked() as st:
        it = find(st, aid)
        if it is None:
            return
        if it.get('profile') == profile:
            it['profile'] = None
        if email and it.get('owner') == email:
            a = st['accounts'].get(email) or {}
            sure_link = _solid(a, names, time.time())      # a link in doubt says nothing against what history says
            if it.get('src') not in FROM_HISTORY or sure_link:
                it.update(owner=None, src=None, ot=None)
            if a.get('profile') == profile:
                a['recheck'] = time.time()
    _map_soon()


def mark_signed_out(st, pairs):
    """These (profile, account) pairs showed claude.ai's sign-in page: nobody is signed in there now, so every link to
    those profiles is in doubt until a page loads there again."""
    now = time.time()
    for profile, email in pairs:
        profile_unproven(st, profile)
        for b in st['accounts'].values():
            if profile and b.get('profile') == profile:
                b['doubt'] = {'profile': profile, 'at': now, 'why': 'signin'}
        a = st['accounts'].get(email)
        if a is None:
            continue
        if not a.get('signed_out'):
            event(st, 'The browser profile for %s is signed out of claude.ai' % name_of(st, email), 'warn')
        a['signed_out'] = now


class NoProfile(SwitchError):
    """The page's owner is known, and no browser profile is signed in as it."""

    def __init__(self, email, url):
        super().__init__('This page belongs to %s, and no %s profile is signed in as %s yet. Set one up on '
                         "Switchboard's page (Set up), then open it again." % (email, browser.app(), email))
        self.email, self.url, self.kind = email, url, 'no_profile'


class Busy(SwitchError):
    """Another open, or a check, has the browser right now."""


# ---------- the record of opens: <data>/opens.jsonl ----------
#
# One line per link opened for a person (never for the quiet check): {'t', 'id' (the artifact's id cut to 8
# characters), 'owner' (an email, or None), 'profile' (the browser profile folder last tried, or None), 'verdict',
# 'tries', 'by'}. Never the whole id, a link, a title, or anything a page showed.

OPENS = 'opens.jsonl'
OPENS_MAX = 1 << 20         # past this size it becomes opens.jsonl.1 (the one before that goes), as History's does
VERDICTS = ('ok', 'wrong', 'signin', 'trouble', 'unchecked')
OPENS_BY = ('page', 'map', 'poster', 'explain')


def opens_path():
    return data_path(OPENS)


def note_open(aid, owner, profile, verdict, tries, by):
    """Append one line to the record of opens (owner-only, one whole write under a lock, moved aside past
    OPENS_MAX). verdict: 'ok' (its page loaded), 'wrong' (every profile tried said "Page not found"), 'signin',
    'trouble' (claude.ai's own error page) or 'unchecked' (opened without reading the tab). by: 'page' (the page
    opened and read it), 'map' (opened from the owner map), 'poster' (the link app opened it from the map before
    the page could) or 'explain' (Switchboard's own page about a missing profile). Never raises."""
    import fcntl
    from . import core
    if core.DEMO:
        return None
    rec = {'t': round(time.time(), 3), 'id': str(aid or '')[:8], 'owner': owner if isinstance(owner, str) else None,
           'profile': profile if isinstance(profile, str) else None,
           'verdict': verdict if verdict in VERDICTS else 'unchecked',
           'tries': tries if isinstance(tries, int) and not isinstance(tries, bool) and tries >= 0 else 0,
           'by': by if by in OPENS_BY else 'page'}
    p = opens_path()
    try:
        os.makedirs(os.path.dirname(p), mode=0o700, exist_ok=True)
        lock = os.open(p + '.lock', os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)        # the page's threads and a command in a terminal, one at a time
            try:
                if os.path.getsize(p) >= OPENS_MAX:
                    os.replace(p, p + '.1')
            except OSError:
                pass
            fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, (json.dumps(rec, sort_keys=True) + '\n').encode('utf-8'))
            finally:
                os.close(fd)
        finally:
            os.close(lock)                          # closing it lets the lock go
    except OSError as e:
        log('the record of opens was not written: %r' % e)
        return None
    return rec


def _verdict(r):
    """The record's verdict for what _go returned."""
    if r.get('signin'):
        return 'signin'
    if r.get('trouble'):
        return 'trouble'
    return 'ok' if r.get('checked') else 'unchecked'


def _state_free():
    """True when nobody holds the saved state's lock this moment: a look, never a wait (core.locked waits up to
    LOCK_DEADLINE for it)."""
    import fcntl
    try:
        fd = os.open(data_path('.lock'), os.O_WRONLY | os.O_CREAT, 0o600)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    finally:
        os.close(fd)                                # closing it lets the lock go
    return True


def _take_new_pages():
    """The session hooks' record taken in before an open (take_published_now), so a page published seconds ago
    opens as its owner and owners.json names it for the link app at once. Never raises, and never waits for the saved
    state: while another thread or process holds its lock, the record is left for the page's next pass
    (published_tick) and the open goes on with what is known."""
    try:
        if _state_free():
            take_published_now()
    except Exception as e:  # noqa: BLE001 - the open goes on with what is known
        log('the record of new pages was not read: %r' % e)


def go(url, quiet=False, wait=None):
    """Open a claude.ai artifact in the browser profile whose account can open it.

    quiet=True is the check Switchboard runs on itself: it opens the page in the
    background and closes the tab again once it has seen the answer.
    wait: how long to wait for another open or a check to finish (None: as long as
    it takes, for a check); past that, Busy.
    """
    link = parse_link(url)
    if not link:
        raise SwitchError('That is not a claude.ai artifact link.')
    if not quiet:
        _take_new_pages()
    if not GO_LOCK.acquire(timeout=-1 if wait is None else max(0.0, float(wait))):
        raise Busy('Switchboard is opening or checking another link in the browser right now. Try again in a moment.')
    try:
        trace = _TRACE
        trace.clear()
        try:
            r = _go(link['url'], link['id'], quiet)
        except SwitchError as e:
            # Every profile tried said "Page not found". When its owner then has no profile, the click's one line is
            # the explanation's (explain_no_profile), not this one.
            if not quiet and trace.get('wrong') and not isinstance(e, NoProfile):
                note_open(link['id'], trace.get('owner'), trace.get('profile'), 'wrong', trace['tries'], 'page')
            raise
        finally:
            _map_soon()
        if not quiet:
            note_open(link['id'], r.get('email'), r.get('profile'), _verdict(r), trace.get('tries') or 1, 'page')
        return r
    finally:
        GO_LOCK.release()


_TRACE = {}     # what the open under way did, for its line in the record: written only while GO_LOCK is held


def _go(url, aid, quiet):
    """go's open, holding GO_LOCK. It tells _TRACE how many profiles it opened ('tries'), the last ('profile'), the
    owner known before the open ('owner'), and whether every one it tried said "Page not found" ('wrong')."""
    trace = _TRACE
    st = load()
    item = find(st, aid)
    if not item or RANK.get(item.get('src'), 0) < 2:
        refresh()                            # it may have been published seconds ago
        st = load()
        item = find(st, aid)
    names = browser.profiles()
    tries = candidates(st, item)            # (two arguments: what stands in for it in tests takes two)
    if not tries:
        raise SwitchError('No browser profile is signed in to claude.ai yet. Open Switchboard to set one up.')
    trusted = bool(item and item.get('owner') and RANK.get(item.get('src'), 0) >= 2)
    owner = item.get('owner') if trusted else None
    trace['owner'] = owner
    was_running = browser.running()
    first = browser.tabs() if was_running else []
    if first is None:                        # can't look: open the best guess and trust it
        if quiet:                            # a check that can't look learns nothing, so it opens nothing
            return {'email': tries[0][1], 'profile': tries[0][0], 'checked': False, 'why': browser.TABS.get('state')}
        if owner and opens_in(st['accounts'].get(owner) or {}, names) is None:
            raise NoProfile(owner, url)      # no profile is signed in as its owner: a guess would say "Page not found"
        trace.update(tries=1, profile=tries[0][0])
        browser.open_url(tries[0][0], url)
        return {'email': tries[0][1], 'profile': tries[0][0], 'checked': False, 'why': browser.TABS.get('state')}

    signed_out = []
    for i, (profile, email) in enumerate(tries):
        before = {row[1] for row in (first if i == 0 else browser.tabs() or [])}
        trace.update(tries=i + 1, profile=profile)
        browser.open_url(profile, url, background=quiet)
        # A browser that was not running takes a while to start (and may restore its windows first).
        verdict, tab = browser.watch_tab(aid, before, timeout=40 if (i == 0 and not was_running) else 15)
        if verdict == 'ok':
            title = browser.clean_title(tab[3])
            who = learn_ok(aid, profile, email, title, signed_out)
            if quiet:
                browser.close_tab(tab[0], tab[1])
            return {'email': who, 'profile': profile, 'checked': True, 'title': title, 'tried': i + 1}
        if verdict == 'wrong':
            browser.close_tab(tab[0], tab[1])
            learn_wrong(aid, profile, email)
            continue
        if verdict == 'signin':
            signed_out.append((profile, email))
            if i == 0 and trusted and not quiet:
                # We know this is the right profile. Leave the sign-in page open:
                # one sign-in there and the page loads.
                with locked() as s2:
                    mark_signed_out(s2, signed_out)
                return {'email': email, 'profile': profile, 'checked': True, 'signin': True}
            browser.close_tab(tab[0], tab[1])
            continue
        if quiet and tab:
            browser.close_tab(tab[0], tab[1])
        if verdict == 'trouble':                 # claude.ai's own outage page: nothing about accounts is learned
            return {'email': email, 'profile': profile, 'checked': False, 'trouble': True}
        return {'email': email, 'profile': profile, 'checked': False}   # the browser was slow; leave it be
    if signed_out:
        with locked() as s2:
            mark_signed_out(s2, signed_out)
        profile, email = signed_out[0]
        if not quiet:
            trace['profile'] = profile
            browser.open_url(profile, url)
        return {'email': email, 'profile': profile, 'checked': True, 'signin': True}
    trace['wrong'] = True
    if owner and opens_in(load()['accounts'].get(owner) or {}, names) is None:
        raise NoProfile(owner, url)
    raise SwitchError('None of your browser profiles could open that link: none is signed in to claude.ai as the '
                      "account it belongs to. Set one up for that account on Switchboard's page (Set up), then open "
                      'it again.')


def go_quietly(url, sent_at=None, fallback_after=None):
    """For a clicked link: whatever happens, something must open, and where its owner is most likely signed in.

    sent_at and fallback_after come from the link app: it opens the link from the owner map by itself when no answer
    has come within fallback_after seconds of sent_at (its timer allows POSTER_SLACK more). A request that reaches
    this later than that has been opened already, so it is not opened again; one that reaches it in time is opened
    here, and its answer goes back at once."""
    if isinstance(sent_at, (int, float)) and isinstance(fallback_after, (int, float)) and \
            0 <= fallback_after <= 60 and time.time() > sent_at + fallback_after + POSTER_SLACK:
        log('a link reached the page after its poster opened it from the owner map; not opened twice')
        aid = (parse_link(url) or {}).get('id')
        got = map_lookup(read_owner_map(), aid)          # what the link app went by (the map as written last)
        note_open(aid, got.get('email'), got.get('profile'), 'unchecked', 1, 'poster')
        _take_new_pages()                               # the next open of a page published just now finds it
        return {'by': 'poster', 'checked': False}
    try:
        return go(url, wait=GO_WAIT)        # never long: the link app was told the page has it
    except NoProfile as e:
        log('opening %s: %s has no signed-in profile' % (url, e.email))
        return explain_no_profile(e.email, e.url)
    except Exception as e:  # noqa: BLE001 - Busy included: another open or a check has the browser
        log('opening %s failed: %r' % (url, e))
        try:
            got = open_from_map(url)
        except Exception as e2:  # noqa: BLE001 - nothing to open from: said in the log, never a thread's traceback
            log('and from the owner map: %r' % e2)
            return {'by': None, 'checked': False}
        if got.get('no_profile'):
            return explain_no_profile(got['email'], got['url'])
        return got


def explain_no_profile(email, url):
    """A clicked page whose owner has no profile signed in as it: open Switchboard's own explanation of that (the /go
    page, with a Set up button) rather than "Page not found" in some other account's profile."""
    inner = 'owner=%s&url=%s' % (quote(email, safe=''), quote(url, safe=''))      # each value whole, even with & in it
    page = 'http://127.0.0.1:%d/go#%s' % (linkapp.page_port(), quote(inner, safe=''))
    browser.open_url(None, page)
    note_open((parse_link(url) or {}).get('id'), email, None, 'unchecked', 0, 'explain')
    return {'by': 'explain', 'email': email, 'no_profile': True, 'checked': False}


def open_from_map(url, m=None):
    """Open a link without the page's check: in its owner's profile from the owner map (or, with no map, from the saved
    state), else in the profile the map names for links nobody knows yet. Returns what was done. An owner that is
    known and has no profile signed in as it gets nothing opened ('no_profile': True): another account's profile
    would only say "Page not found"."""
    link = parse_link(url)
    if not link:
        raise SwitchError('That is not a claude.ai artifact link.')
    m = m if m is not None else read_owner_map()
    if not m:
        try:
            m = owner_map(load(), browser.profiles())
        except Exception:  # noqa: BLE001 - nothing known at all: the browser decides
            m = {}
    got = map_lookup(m, link['id'])
    if not (m.get('artifacts') or {}).get(link['id']):
        got = _from_record(m, link['id']) or got    # published a moment ago: the record names it before the map
    if got['email'] and not got['profile']:
        return dict(got, by='map', checked=False, url=link['url'], no_profile=True)
    browser.open_url(got['profile'], link['url'])
    note_open(link['id'], got['email'], got['profile'], 'unchecked', 1, 'map')
    return dict(got, by='map', checked=False, url=link['url'])


def _from_record(m, aid):
    """map_lookup's answer for an artifact the owner map does not hold yet, from the session hooks' record and the
    switch history (published_owner), or None when the record does not name it or no owner is known: the profile its
    owner's pages open in (the map's, else the saved state's), or None when no profile is signed in as it."""
    try:
        rec = published_owner(aid)
    except Exception as e:  # noqa: BLE001 - the map's own answer stands
        log('the record of new pages was not read: %r' % e)
        return None
    if not rec or not rec.get('email'):
        return None
    email = rec['email']
    accounts = m.get('accounts') if isinstance(m.get('accounts'), dict) else {}
    if isinstance(accounts.get(email), dict):
        p = accounts[email].get('opens') if isinstance(accounts[email].get('opens'), str) else None
    else:
        p = opens_in(load()['accounts'].get(email) or {}, browser.profiles())
    return {'email': email, 'profile': p, 'src': rec.get('src'), 'from': 'record' if p else None}


def check_profiles():
    """Prove which profile is which: open one known page per account in the background, read it, close it."""
    st = load()
    out = []
    for email in st['order']:
        pages = [x for x in st['artifacts'] if x.get('owner') == email and RANK.get(x.get('src'), 0) >= 2]
        if not pages:
            out.append({'email': email, 'result': 'no_page'})
            continue
        try:
            r = go(pages[0]['url'], quiet=True)
        except NoProfile:
            out.append({'email': email, 'result': 'no_profile'})
            continue
        except SwitchError:
            out.append({'email': email, 'result': 'none'})
            continue
        result = 'signin' if r.get('signin') else 'trouble' if r.get('trouble') else \
            'unchecked' if not r.get('checked') else 'ok' if r.get('email') == email else 'other'
        out.append({'email': email, 'result': result, 'profile': r.get('profile')})
    return out


# ---------- the quiet daily re-proof ----------

REPROVE_EVERY = DAY         # each account's link is proved again about once a day
RECHECK_AFTER = 300         # after its own profile said "Page not found": checked again within minutes
LOOK_EVERY = 600            # the tick looks for a due check at most every 10 minutes
REPROVE = {'running': False, 'looked': -1e9, 'forced': None}


def background_on():
    """Background link work runs only in the page's own process (linkapp.announce_port marks it), never in a command
    run in a terminal, and never in tests unless a test turns it on with its fakes (REPROVE['forced'])."""
    if REPROVE['forced'] is not None:
        return bool(REPROVE['forced'])
    return linkapp.PAGE['up'] and not _in_tests()


def pages_of(st, email):
    """The account's own pages worth proving its link with: the ones that loaded in its profile first, then the
    others a page load proved, then the ones history says it published; newest first in each."""
    a = st['accounts'].get(email) or {}
    mine = [x for x in st['artifacts'] if x.get('owner') == email and RANK.get(x.get('src'), 0) >= 2 and x.get('id')]
    return sorted(mine, key=lambda x: (0 if x.get('src') == 'seen' and x.get('profile') == a.get('profile') else
                                       1 if x.get('src') == 'seen' else 2, -(x.get('t') or 0)))


def reprove_due(st, names, now=None):
    """Accounts whose link should be proved again now, most urgent first: one whose own profile just said "Page not
    found" (recheck), then the rest whose last look is a day old, the oldest proof first. Only accounts with a profile
    that is there and a page of their own to prove it with."""
    now = time.time() if now is None else now
    due = []
    for email in st.get('order') or []:
        a = st['accounts'].get(email) or {}
        if not a.get('connected') or not a.get('profile') or a['profile'] not in names or not pages_of(st, email):
            continue
        last = a.get('reproof_at')
        last = last if isinstance(last, (int, float)) and last <= now + AHEAD else 0
        if a.get('recheck') and now - last >= RECHECK_AFTER:
            due.append((0, 0, email))
        elif now - last >= REPROVE_EVERY:
            at = link_state(a, names, now)[1]
            due.append((1, at or 0, email))
    return [e for _, _, e in sorted(due)]


def prove_one(email):
    """Prove one account's link quietly: its own page opened in the background in its own profile only, read, and the
    tab closed. Up to two pages, so one deleted page can't condemn a working profile. Returns 'ok', 'signin', 'wrong',
    'trouble', 'unchecked', 'no_page', 'no_profile' or 'busy' (another open is running)."""
    if not GO_LOCK.acquire(blocking=False):
        return 'busy'
    result = 'unchecked'
    try:
        st = load()
        names = browser.profiles()
        a = st['accounts'].get(email) or {}
        profile = a.get('profile')
        if not profile or profile not in names:
            result = 'no_profile'
            return result
        pages = pages_of(st, email)[:2]
        if not pages:
            result = 'no_page'
            return result
        if not browser.running():
            return result
        first = browser.tabs()
        if first is None:
            return result
        wrong = 0
        for page in pages:
            before = {row[1] for row in first}
            browser.open_url(profile, page['url'], background=True)
            verdict, tab = browser.watch_tab(page['id'], before)
            if tab is not None:
                browser.close_tab(tab[0], tab[1])
            if verdict == 'ok':
                learn_ok(page['id'], profile, email, browser.clean_title(tab[3]), [])
                result = 'ok'
                return result
            if verdict == 'signin':
                with locked() as s2:
                    mark_signed_out(s2, [(profile, email)])
                result = 'signin'
                return result
            if verdict != 'wrong':
                result = 'trouble' if verdict == 'trouble' else 'unchecked'
                return result
            wrong += 1
            first = browser.tabs() or []
        with locked() as s2:
            _doubt(s2, email, profile, 'wrong')
            event(s2, 'The %s profile "%s" no longer opens the pages of %s' % (browser.app(), names.get(profile, profile),
                                                                              name_of(s2, email)), 'warn')
        result = 'wrong'
        return result
    finally:
        try:
            with locked() as s3:
                b = s3['accounts'].get(email)
                if b is not None and result != 'busy':
                    # a look that settled nothing is tried again in an hour, not tomorrow
                    b['reproof_at'] = time.time() - (REPROVE_EVERY - 3600 if result in ('unchecked', 'trouble') else 0)
                    if result in ('ok', 'wrong', 'signin', 'no_page', 'no_profile'):
                        b.pop('recheck', None)
        finally:
            GO_LOCK.release()
            _map_soon()


def reprove_ready():
    """None when a quiet check may run now, else why not: the browser must already be running (it is never started
    for a check), Switchboard must be allowed to read its tabs (and not be waiting out a slow browser), and the Mac
    must not be busy (load per core under 1.5)."""
    if not browser.present():
        return 'no browser'
    if browser.load_per_core() >= 1.5:
        return 'busy Mac'
    if not browser.running():
        return 'browser closed'
    if browser.consent() != browser.ALLOWED or browser._mono() < browser.TABS['until']:
        return 'tabs not readable'
    return None


def reprove_tick(now=None):
    """One quiet re-proof, of the account most in need, when one is due and reprove_ready. Returns (email, result) or
    None."""
    st = load()
    due = reprove_due(st, browser.profiles(), now)
    if not due or reprove_ready():
        return None
    return due[0], prove_one(due[0])


def start_reprove():
    """From the tick: start reprove_tick in the background when the page may do background work, none is running,
    and the last look was 10 minutes ago or more."""
    if not background_on() or REPROVE['running'] or browser._mono() - REPROVE['looked'] < LOOK_EVERY:
        return False
    REPROVE['looked'] = browser._mono()
    REPROVE['running'] = True

    def run():
        try:
            got = reprove_tick()
            if got:
                log('link check: %s -> %s' % got)
        except Exception as e:  # noqa: BLE001
            log('link check failed: %r' % e)
        finally:
            REPROVE['running'] = False
    threading.Thread(target=run, name='switchboard-link-check', daemon=True).start()
    return True


# ---------- the owner map: ~/.switchboard/owners.json ----------

MAP_FILE = 'owners.json'
MAP_V = 1
_MAP = {'last': None, 'lock': threading.Lock()}


def map_path():
    return data_path(MAP_FILE)


def _profile_accounts(st, names, now):
    """{profile: the account its claude.ai follows}: the account a still-good proof names there, else the account
    whose page loaded there last."""
    out = {}
    for email in st.get('order') or []:
        a = st['accounts'].get(email) or {}
        if link_state(a, names, now)[0] in ('proved', 'old'):
            out.setdefault(a['profile'], email)
    for email in st.get('order') or []:
        p = (st['accounts'].get(email) or {}).get('signed_in_profile')
        if p and p in names:
            out.setdefault(p, email)
    for email in st.get('order') or []:                     # "another account's page loaded there" names who
        d = _bound(st['accounts'].get(email) or {}, 'doubt')
        if d and d.get('why') == 'other_account' and isinstance(d.get('by'), str) and d['profile'] in names:
            out.setdefault(d['profile'], d['by'])
    return out


def _page_opens_in(x, st, names, proved_by, now):
    """The profile one artifact should open in, from what is known, or None: its owner's (opens_in), else the profile
    it loaded in, unless that profile is now another account's or is known not to open its owner's pages."""
    owner = x.get('owner') or None
    a = st['accounts'].get(owner) or {} if owner else {}
    if owner:
        p = opens_in(a, names, now)
        if p:
            return p
    loaded = x.get('profile')
    if not loaded or loaded not in names:
        return None
    holder = proved_by.get(loaded)
    if holder is not None and holder != owner:
        return None
    d = _bound(a, 'doubt') if owner else None
    if d and d['profile'] == loaded and d.get('why') != 'signin':
        return None
    return loaded


def _slot(a):
    """An account's number in `switchboard`'s list, or None."""
    s = a.get('slot')
    return s if isinstance(s, int) and not isinstance(s, bool) and s > 0 else None


def owner_map(st, names, now=None, extensions=None, host=None):
    """The owner map, as written to owners.json. Readable without the page:
      active      the account Claude Code is on (an email), timeline its last switches [[email, when], ...]
      accounts    {email: {id, profile, profile_name, link, at, why, opens (the profile its pages open in, or None),
                   extension (the Claude extension is in its profile: true, false, or null when not known),
                   slot (its number in `switchboard`'s list) and plan (its plan's words, 'Max 20x'), each or null:
                   the session hooks name an account by these at a chat's start, never by its email}}
      artifacts   {id: {o: owner, p: the profile it opens in or null, s: how sure: seen, listed, published, guess}}
      fallback    the profile for a link nobody knows yet: the account in use's, else the newest proof's; never the
                  "other links" profile unless that is one of these
      chrome      {profiles: {folder: {extension, account}}, host: Claude Code's native host is there and runs}"""
    now = time.time() if now is None else now
    if extensions is None:
        try:
            extensions = browser.extensions() if browser.present() else {}
        except Exception:  # noqa: BLE001
            extensions = {}
    if host is None:
        try:
            host = browser.native_host()['ok'] if browser.present() else None
        except Exception:  # noqa: BLE001
            host = None
    accounts, newest = {}, (0, None)
    for email in st.get('order') or []:
        a = st['accounts'].get(email) or {}
        if not a.get('connected'):
            continue
        state, at, why = link_state(a, names, now)
        p = a.get('profile')
        opens = opens_in(a, names, now)
        accounts[email] = {'id': a.get('account_id') or a.get('id'), 'profile': p,
                           'profile_name': names.get(p, p) if p else None, 'link': state, 'at': at, 'why': why,
                           'opens': opens, 'extension': extensions.get(p) if p and p in extensions else None,
                           'slot': _slot(a), 'plan': term.plan_words(dict(a, org='', plan_line='')) or None}
        if state in ('proved', 'old') and at and at > newest[0]:
            newest = (at, p)
    for e in [x.get('owner') for x in st.get('artifacts') or []]:
        if e and e not in accounts and e in st['accounts']:     # a removed account still owns its pages
            a = st['accounts'][e]
            accounts[e] = {'id': a.get('account_id') or a.get('id'), 'profile': a.get('profile'),
                           'profile_name': names.get(a.get('profile'), a.get('profile')) if a.get('profile') else None,
                           'link': link_state(a, names, now)[0], 'at': None, 'why': None,
                           'opens': opens_in(a, names, now), 'extension': None, 'removed': True}
    proved_by = _profile_accounts(st, names, now)
    arts = {}
    for x in st.get('artifacts') or []:
        aid = x.get('id')
        if not aid:
            continue
        arts[aid] = {'o': x.get('owner') or None, 'p': _page_opens_in(x, st, names, proved_by, now), 's': x.get('src')}
    active = st.get('active')
    fallback = (accounts.get(active) or {}).get('opens') or newest[1]
    chrome = {'profiles': dict((p, {'extension': bool(v), 'account': proved_by.get(p)})
                               for p, v in sorted(extensions.items())), 'host': host,
              'store': browser.CLAUDE_EXTENSION_PAGE}
    try:
        app = browser.app() if browser.present() else None
    except Exception:  # noqa: BLE001
        app = None
    return {'v': MAP_V, 'written_at': now, 'active': active, 'browser': app,
            'active_id': (accounts.get(active) or {}).get('id'),
            'timeline': [list(x) for x in (st.get('timeline') or [])[-10:] if isinstance(x, (list, tuple))],
            'accounts': accounts, 'artifacts': arts, 'fallback': fallback, 'chrome': chrome, 'auto': _auto_on()}


def _auto_on():
    """Whether automatic switching is on (api.auto_settings): True, False, or None when it can't be read. The session
    hooks ask for a need switch only while it is on: Switchboard never switches by itself while it is off."""
    try:
        from .accounts import api
        return bool((api.auto_settings() or {}).get('enabled'))
    except Exception:  # noqa: BLE001 - not known: the hooks then don't ask
        return None


def write_owner_map(st=None, names=None):
    """Write owners.json when what it says changed (atomically, readable only by you). True when written."""
    st = load() if st is None else st
    names = browser.profiles() if names is None else names
    m = owner_map(st, names)
    body = dict(m, written_at=None)
    with _MAP['lock']:
        if _MAP['last'] == body and os.path.exists(map_path()):
            return False
        _write_atomic(map_path(), json.dumps(m, indent=None, separators=(',', ':'), sort_keys=True).encode('utf-8'))
        _MAP['last'] = body
    return True


def _map_soon():
    try:
        write_owner_map()
    except Exception as e:  # noqa: BLE001
        log('the owner map was not written: %r' % e)


def _write_atomic(path, data):
    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    tmp = os.path.join(folder, '.%s.%d.tmp' % (os.path.basename(path), os.getpid()))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_owner_map(path=None):
    """The owner map, or {} when there is none or it can't be read."""
    try:
        with open(path or map_path(), 'rb') as f:
            m = json.loads(f.read(16 << 20).decode('utf-8'))
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    return m if isinstance(m, dict) and m.get('v') == MAP_V else {}


def map_lookup(m, aid):
    """{'email': owner or None, 'profile': where to open it or None, 'src', 'from': 'owner' | 'loaded' | 'fallback' |
    None} for one artifact id, from an owner map."""
    x = (m.get('artifacts') or {}).get(aid) if isinstance(m, dict) else None
    x = x if isinstance(x, dict) else {}
    owner = x.get('o') if isinstance(x.get('o'), str) else None
    p = x.get('p') if isinstance(x.get('p'), str) else None
    if p:
        return {'email': owner, 'profile': p, 'src': x.get('s'), 'from': 'owner' if owner else 'loaded'}
    fb = m.get('fallback') if isinstance(m, dict) and isinstance(m.get('fallback'), str) else None
    if owner and fb:
        # The owner is known and has no profile: opening it in another account's profile would only say "Page not
        # found", so the fallback is used only when the owner is unknown.
        return {'email': owner, 'profile': None, 'src': x.get('s'), 'from': None}
    return {'email': owner, 'profile': fb, 'src': x.get('s'), 'from': 'fallback' if fb else None}


# ---------- what the page shows per account: its link, Set up, Claude in Chrome ----------

NO_PROFILE = 'No Chrome profile is signed in as this account'


def _date(t):
    return time.strftime('%b %-d', time.localtime(t)) if t else None


def short_name(email, accounts):
    """A word for an account, for a new browser profile's folder name (which can't hold an @): its alias; else its
    domain's first label (gmail, fernhill) when no other account without an alias has that label and no account's
    alias is that word; else the part before the @. `accounts`: {email: {'alias', ...}} of the accounts the page
    lists. The page and every other surface name an account by its whole email."""
    accounts = accounts or {}
    alias = (accounts.get(email) or {}).get('alias')
    if isinstance(alias, str) and alias:
        return alias
    local, _, domain = (email or '').partition('@')
    word = domain.split('.')[0] if domain else ''
    for other, a in accounts.items():
        said = (a or {}).get('alias')
        if other != email and (said == word or (not said and other.partition('@')[2].split('.')[0] == word)):
            return local
    return word or local


def page_accounts(st, email=None):
    """{email: account} of the accounts the page lists (the connected ones), with `email`'s own when it has one."""
    return dict((e, a) for e, a in (st.get('accounts') or {}).items() if a.get('connected') or e == email)


def profile_label(email, accounts=None):
    """What a new profile for this account is named after "Claude ": short_name over `accounts` (the accounts the
    page lists). An alias with nothing a profile's name can hold (no letter or digit) gives way to the rule's word."""
    accounts = dict(accounts or {})
    alias = (accounts.get(email) or {}).get('alias')
    if isinstance(alias, str) and alias and not re.sub(r'[^A-Za-z0-9 _-]', '', alias).strip():
        accounts[email] = dict(accounts[email], alias=None)
    return short_name(email, accounts) or 'account'


def setup_profile(email):
    """Set up: a new browser profile named for the account, claude.ai's sign-in page opened in it, and the account
    linked to it (unproved until one of its pages loads there, which the first open after the sign-in does). Returns
    {'profile', 'profile_name', 'opened': True}."""
    if not browser.present():
        raise SwitchError('A new profile needs %s. Install it and open it once.' % browser.app())
    with locked() as st:
        a = st['accounts'].get(email)
        if a is None:
            raise SwitchError('That account is not set up here.')
        name = browser.new_profile_name(profile_label(email, page_accounts(st, email)))
        for other in st['accounts'].values():                   # one profile, one account
            if other is not a and other.get('profile') == name:
                other.update(profile=None, profile_src=None)
        a.update(profile=name, profile_src='you', setup_at=time.time())
        for k in ('signed_out', 'proof', 'doubt', 'recheck'):
            a.pop(k, None)
        profile_unproven(st, name)
        browser.open_url(name, 'https://claude.ai/login')
        event(st, 'Made the %s profile "%s" for %s: sign in to claude.ai there once' % (browser.app(), name, email))
    _map_soon()
    return {'profile': name, 'profile_name': name, 'opened': True}


def link_rows(st, names, now=None, extensions=None, host=None):
    """{email: {'link', 'at', 'why', 'profile', 'profile_name', 'line', 'action', 'chrome': {'state', 'line',
    'action'}}} for every account the page shows. action: None, {'do': 'setup', 'label': 'Set up', 'profile_name'},
    {'do': 'signin', 'label': 'Sign in there', 'profile'}, or for Claude in Chrome {'do': 'add_extension', 'label':
    'Add to this profile', 'profile', 'url'}."""
    now = time.time() if now is None else now
    extensions = (browser.extensions() if browser.present() else {}) if extensions is None else extensions
    proved_by = _profile_accounts(st, names, now)
    out = {}
    for email in st.get('order') or []:
        a = st['accounts'].get(email) or {}
        if not a.get('connected'):
            continue
        state, at, why = link_state(a, names, now)
        p = a.get('profile')
        pn = names.get(p, p) if p else None
        action = None
        if state == 'proved':
            line = 'Opens in the %s profile "%s"%s.' % (browser.app(), pn, ', proved %s' % _date(at) if at else '')
        elif state == 'old':
            line = 'Opens in the %s profile "%s" (last proved %s; Switchboard checks it again).' % (
                browser.app(), pn, _date(at))
        elif state == 'unproved':
            line = 'Opens in the %s profile "%s" (not proved yet: the first page of its own that loads there proves ' \
                   'it).' % (browser.app(), pn)
        elif state == 'suspect' and why == 'signin':
            line = 'The %s profile "%s" is signed out of claude.ai. Sign in there once.' % (browser.app(), pn)
            action = {'do': 'signin', 'label': 'Sign in there', 'profile': p}
        else:
            if state == 'missing':
                more = ' Its profile "%s" is gone from %s.' % (p, browser.app())
            elif state == 'suspect':
                holder = proved_by.get(p) or (a.get('doubt') or {}).get('by')
                more = (' The profile "%s" is signed in as %s now.' % (pn, name_of(st, holder)) if holder and
                        holder != email else ' The profile "%s" no longer opens its pages.' % pn)
            else:
                more = ''
            line = NO_PROFILE + '.' + more
            action = {'do': 'setup', 'label': 'Set up', 'profile_name': 'Claude ' + profile_label(
                email, page_accounts(st, email))}
        out[email] = {'link': state, 'at': at, 'why': why, 'profile': p, 'profile_name': pn, 'line': line,
                      'action': action, 'chrome': chrome_row(a, names, extensions, host, now),
                      'checked': 'last checked %s' % _date(at) if state in ('proved', 'old') and at else None}
    return out


def chrome_row(a, names, extensions, host=None, now=None):
    """Claude in Chrome for one account: {'state': 'ready' | 'missing' (the extension is not in its profile) |
    'signed_out' | 'no_profile' | 'no_host' (Claude Code's way into the browser is not set up, for every account) |
    'unknown', 'line', 'action'}. Claude in Chrome follows the account Claude Code is on, so it needs the extension in
    each account's own profile."""
    p = opens_in(a, names, now)
    state_link, _, why = link_state(a, names, now)
    if host is False:
        return {'state': 'no_host', 'line': "Claude in Chrome can't reach Claude Code on this Mac yet: in Claude Code, "
                "run /chrome once.", 'action': None}
    if not p:
        return {'state': 'no_profile', 'line': 'Claude in Chrome needs a %s profile signed in as this account.'
                % browser.app(), 'action': None}
    pn = names.get(p, p)
    if state_link == 'suspect' and why == 'signin':
        return {'state': 'signed_out', 'line': 'Claude in Chrome: the profile "%s" is signed out of claude.ai.' % pn,
                'action': None}
    ext = extensions.get(p)
    if ext is True:
        return {'state': 'ready', 'line': 'Claude in Chrome: ready in "%s" (keep %s open in that profile).'
                % (pn, browser.app()), 'action': None}
    if ext is False:
        return {'state': 'missing', 'line': 'Claude in Chrome: the extension is not in "%s".' % pn,
                'action': {'do': 'add_extension', 'label': 'Add to this profile', 'profile': p,
                           'url': browser.CLAUDE_EXTENSION_PAGE}}
    return {'state': 'unknown', 'line': 'Claude in Chrome: not known for "%s".' % pn, 'action': None}


def chrome_profiles(st, names, extensions=None, now=None):
    """Claude in Chrome per browser profile, for the page: [{'profile', 'name', 'extension' (True/False), 'version',
    'account' (the account its claude.ai follows, or None when not known), 'line', 'action'}], in the browser's own
    order of names. action is "Add to this profile" where the extension is missing."""
    now = time.time() if now is None else now
    extensions = (browser.extensions() if browser.present() else {}) if extensions is None else extensions
    try:
        versions = browser.extension_versions() if browser.present() else {}
    except OSError:
        versions = {}
    follows = _profile_accounts(st, names, now)
    out = []
    for p, name in sorted(names.items(), key=lambda kv: kv[1].lower()):
        if p not in extensions:
            continue
        who = follows.get(p)
        has = bool(extensions.get(p))
        line = 'The Claude extension is %s; claude.ai here is %s.' % (
            'here' if has else 'not here', 'signed in as %s' % name_of(st, who) if who else 'not known yet')
        action = None if has else {'do': 'add_extension', 'label': 'Add to this profile', 'profile': p,
                                   'url': browser.CLAUDE_EXTENSION_PAGE}
        out.append({'profile': p, 'name': name, 'extension': has, 'version': versions.get(p), 'account': who,
                    'line': line, 'action': action})
    return out


def add_extension(profile):
    """"Add to this profile": the Claude extension's Chrome Web Store page, opened in that profile. Never installs
    anything by itself: the person presses Add to Chrome there."""
    if not profile or not browser.PROFILE_RE.match(profile) or profile not in browser.profiles():
        raise SwitchError('That browser profile does not exist.')
    browser.open_url(profile, browser.CLAUDE_EXTENSION_PAGE)
    return {'opened': True, 'profile': profile}


def switch_note(st, to_email, names=None, extensions=None, label=None):
    """One line to add to the notification of a switch to `to_email`, or None: no profile signed in as it (its pages
    won't open), or Claude in Chrome lacking the extension in its profile. `label`: how the account is named (as the
    notification names it); its email when not given."""
    names = browser.profiles() if names is None else names
    if not names:                       # no browser profiles known (no Chrome, or its list can't be read): nothing
        return None
    a = st['accounts'].get(to_email) or {}
    p = opens_in(a, names)
    if not p:
        return 'No %s profile is signed in as %s, so its pages won\'t open: Set up on Switchboard\'s page.' % (
            browser.app(), label or name_of(st, to_email))
    extensions = (browser.extensions() if browser.present() else {}) if extensions is None else extensions
    if extensions.get(p) is False and any(extensions.values()):
        return 'Claude in Chrome needs the extension in "%s": Add to this profile, on Switchboard\'s page.' % (
            names.get(p, p))
    return None


DESKTOP_NOTE = 'The Claude desktop app and claude.ai in your browser keep their own sign-ins.'


def desktop_note():
    """Said once: the first switch after install adds this line to its notification. Returns the line the first
    time (remembered in settings.json), None after."""
    said = {}

    def change(v):
        said['first'] = not v
        return True
    try:
        update_setting('desktop_note_said', change)
    except OSError:
        return None
    return DESKTOP_NOTE if said.get('first') else None


# ---------- for the command line and a go helper ----------

def owner_of(url, scan=True):
    """Who owns an artifact, for `switchboard owner <link>`: {'url', 'id', 'email', 'src', 'profile',
    'profile_name', 'link', 'said' (one plain line)}. When the owner isn't known yet, reads the session hooks' record of
    new pages first (it needs no page), then chat history."""
    link = parse_link(url)
    if not link:
        raise SwitchError('That is not a claude.ai artifact link.')
    st = load()
    item = find(st, link['id'])
    if not item or RANK.get(item.get('src'), 0) < 2:
        try:
            if take_published_now():        # published a moment ago: the session hooks' record names it already
                st = load()
                item = find(st, link['id'])
        except OSError as e:
            log('the record of new pages was not read: %r' % e)
    if scan and (not item or RANK.get(item.get('src'), 0) < 2):
        refresh()
        st = load()
        item = find(st, link['id'])
    names = browser.profiles()
    owner = item.get('owner') if item else None
    src = item.get('src') if item else None
    a = st['accounts'].get(owner) or {} if owner else {}
    state = link_state(a, names)[0] if owner else None
    p = opens_in(a, names) if owner else None
    pn = names.get(p, p) if p else None
    cmd_open = 'switchboard open %s' % link['url']
    if not owner:
        said = ('Not known yet. %s tries each account\'s profile, shows it where it loads, and remembers the owner.'
                % cmd_open)
    else:
        how = {'seen': 'it loaded in its profile', 'listed': 'its own list of published pages names it',
               'published': 'Claude Code published it while on that account',
               'guess': 'it was published near a switch, so this is a guess'}.get(src, 'from history')
        where = (' Opens in the %s profile "%s".' % (browser.app(), pn) if p else
                 ' No %s profile is signed in as it: Set up on Switchboard\'s page.' % browser.app())
        said = '%s%s (%s).%s Open it as its owner: %s. Act on it as its owner: switchboard run %s -- -p "..."' % (
            'Probably ' if src == 'guess' else '', name_of(st, owner), how, where, cmd_open, owner)
    return {'url': link['url'], 'id': link['id'], 'email': owner, 'src': src, 'profile': p, 'profile_name': pn,
            'link': state, 'said': said}


def open_link(url, wait=True, timeout=45.0):
    """`switchboard open <link>` and a go helper: open an artifact as its owner. The running page does it when it
    answers (it checks the tab and learns); when it doesn't answer, the owner map opens it in the owner's profile.
    Never tab reading from here (a terminal is not the page; macOS would ask about it). Returns {'by': 'page' | 'map',
    'email', 'profile', 'checked', 'signin', 'title', 'said'}."""
    link = parse_link(url)
    if not link:
        raise SwitchError('That is not a claude.ai artifact link.')
    sent = time.time()
    body = {'url': link['url']} if wait else {'url': link['url'], 'wait': False, 'sent_at': sent,
                                               'fallback_after': timeout}
    try:
        r = linkapp.ask_page('/api/go', body, timeout=timeout if wait else min(timeout, 5.0))
    except linkapp.PageDown as e:
        log('the page did not answer an open (%s); opening from the owner map' % e)
        r = None
    if r is not None:
        r = dict(r, by='page')
        who = r.get('name') or r.get('email') or r.get('profile_name') or 'its owner'
        if not wait:
            r['said'] = "Switchboard's page is opening it as its owner."
        elif r.get('no_profile'):
            r['said'] = NoProfile(r.get('email') or 'its owner', link['url']).args[0]
        elif r.get('signin'):
            r['said'] = 'That profile (%s) is signed out of claude.ai. Sign in once in the window that opened.' % who
        elif r.get('trouble'):
            r['said'] = 'claude.ai is having trouble right now (it showed its own error page). Try again in a minute.'
        elif r.get('checked'):
            r['said'] = 'Opened as %s%s.' % (who, ': ' + r['title'] if r.get('title') else '')
        else:
            r['said'] = 'Opened in the profile for %s (the page could not be checked).' % who
        return r
    got = open_from_map(link['url'])
    names = browser.profiles()
    pn = names.get(got['profile'], got['profile']) if got.get('profile') else None
    if got.get('no_profile'):
        got['said'] = ("Switchboard's page didn't answer. This page belongs to %s, and no %s profile is signed in as "
                       "it: start the page (switchboard serve) and press Set up for %s." % (got['email'], browser.app(),
                                                                                           got['email']))
    elif got.get('profile'):
        got['said'] = ("Switchboard's page didn't answer, so it opened from the saved owner list: %s profile \"%s\"%s."
                       % (browser.app(), pn, ' (%s)' % got['email'] if got.get('email') else ''))
    else:
        got['said'] = ("Switchboard's page didn't answer and no owner is saved for this page, so it opened where %s "
                       "decides. Start the page (switchboard serve) to open it as its owner." % browser.app())
    return got
