"""What is going on in each of your accounts: every chat on this Mac, the account it is on, where it started, and what
it holds that belongs to another account.

It comes from each chat's own file, not from the clock. Claude Code writes into a chat's file the login the chat
started under, and a line each time the login it is on changes (switchboard/chatfile.py reads those lines, and only
it does). A look reads each chat written in the last DAYS days in one forward pass, on from where the last look
stopped, and keeps of each line only:

- the account lines, through chatfile;
- the title lines (custom-title, ai-title): the last name you gave a chat wins over the one Claude Code gave it;
- of a reply (an assistant line): its time, for the chat's last turn on each account, and its request id, for the
  turns each account took in the last day. The id is kept as a short hash for a day, so a request a fork copied
  into another chat's file counts once. A helper's reply that Claude Code copies into the chat while the helper
  runs (a progress line) is not one of the chat's turns, and neither is Claude Code's own notice;
- of a call to a claude.ai connector (a tool named mcp__claude_ai_*): the connector's name (chatfile.connector), and
  from its result whether it was an error. It answered under the account the chat was on then;
- the chat's folder, as Claude Code wrote it.

No message text, no tool input and no tool output is kept. A helper's own file (<chat>/subagents/...) is counted,
and its time makes its chat "working", but it is not read.

A look is bounded: it reads at most PASS_BYTES and PASS_SECONDS, newest chats first, so a first look over gigabytes
is spread over passes and never holds anything up. Claude Code only adds to a chat's file, so each chat is read on
from where the last look stopped; a file shorter than that was replaced, and is read from its start. What a look
found goes into two files in Switchboard's folder, each written whole, both under one lock (chats.lock), so the
page's job and a command at the same moment never tear them or mix one's half with the other's:

  chats.json        the shape chatfile.py documents: what the session hooks and `switchboard resume` read
  chats-view.json   what else this view needs: titles, each chat's connectors, turns, how far the look has got

view() turns the two into what `switchboard chats`, GET /api/chats and each account's `chats` in GET /api/state
show.

How exact it is. An account line is written at a chat's first request after the login changed, so "since" is the
chat's first request on that account, not the moment of the switch. A chat that has been quiet since the login
changed still names the account of its last request; its next request goes to the login Claude Code is on (unless
it runs on a login of its own). A chat from an older Claude Code has no account lines: its account is not known,
and is never guessed from the clock. A chat is as fresh as the last look (`at`).
"""
import fcntl
import hashlib
import json
import os
import shutil
import sys
import time
from contextlib import contextmanager

from . import chatfile, core

DAYS = 7                    # chats written in the last week are read; `switchboard chats --days N` reads further
RECENT = 86400.0            # "now": an account's counts are over chats written in the last day, and idle ends here
IDLE_AFTER = 300.0          # working: written in the last 5 minutes (the engine's own line, accounts/sessions.py)
PASS_BYTES = 64 << 20       # what one pass of the page's job reads at most
PASS_SECONDS = 1.5
COMMAND_BYTES = 512 << 20   # what `switchboard chats` reads at most before it answers
COMMAND_SECONDS = 4.0
CHECK_EVERY = 4 << 20       # bytes read between two looks at the clock inside one file
KEEP_DAYS = 30              # a chat's saved entry is kept this long after it last wrote, while its file is there
TURN_KEEP = 25 * 3600.0     # request hashes and turn counts are kept a day and an hour
BUCKET = 900                # turns are counted per quarter hour
SMALL = 64 << 10            # a title line is never longer; nor is a line read only for the chat's folder
PENDING_MAX = 64            # connector calls waiting for their result, per chat
HELPERS_QUIET = 3600.0      # a helpers' folder whose files all wrote over an hour ago, and that holds the same files,
HELPERS_FULL = 900.0        # is looked at file by file again only after this long
SEEN_SCAN = 64              # result ids looked at in one line
RECENT_MOVES = 3            # the moves a chat shows: its last few
STEPS_KEEP = 256            # account steps kept per chat in chats.json, newest; the older ones' moves are counted in `cut`
CUT_ROOM = 64               # how far from STEPS_KEEP a cut may go to find a pause no flutter spans
SHORT = 8                   # a chat's short id: at least this many characters, more when two would be the same
TITLE_MAX = 200
VIEW_NAME = 'chats-view.json'
VIEW_V = 1
LOCK_NAME = 'chats.lock'
USAGE = 'switchboard chats [--all] [--days N] [--account NAME] [--json]'

# Claude Code writes compact JSON; a file whose first line is spaced ("type": "x") is read with spaced marks.
_MARKS = {False: (b'"type":"assistant"', b'"type":"user"', b'"tool_use_id":"', b'"timestamp":"'),
          True: (b'"type": "assistant"', b'"type": "user"', b'"tool_use_id": "', b'"timestamp": "')}


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x == x


def _count(x):
    return isinstance(x, int) and not isinstance(x, bool) and x >= 0


# ---------- the two files ----------

def _blank_index():
    return {'v': chatfile.INDEX_V, 'at': 0, 'orgs': {}, 'chats': {}, 'connectors': {}}


def _blank_side():
    return {'v': VIEW_V, 'chats': {}, 'turns': {}, 'seen': {}, 'look': {}}


def _blank_extra():
    return {'end': 0, 'size': 0, 'whole': None, 'custom': None, 'ai': None, 'cwd': None, 'by': {}, 'conn': {},
            'pending': {}, 'helpers': 0, 'helpers_last': None, 'hdirs': {}}


def _left(x):
    """Bytes of a chat's file not read yet: none when the look reached its end, or when everything after where it
    stopped is one last line still being written (`whole`: the file's size when a look found it so; a file that
    changed size since is read on)."""
    if x['whole'] is not None and x['whole'] == x['size']:
        return 0
    return max(0, x['size'] - x['end'])


def _extra(x):
    """One chat's entry in chats-view.json, with anything damaged left out."""
    out = _blank_extra()
    if not isinstance(x, dict):
        return out
    for k in ('end', 'size', 'helpers', 'whole'):
        if _count(x.get(k)):
            out[k] = x[k]
    for k in ('custom', 'ai', 'cwd'):
        if isinstance(x.get(k), str):
            out[k] = x[k]
    if _num(x.get('helpers_last')):
        out['helpers_last'] = x['helpers_last']
    if isinstance(x.get('by'), dict):
        out['by'] = dict((o, t) for o, t in x['by'].items() if isinstance(o, str) and _num(t))
    if isinstance(x.get('hdirs'), dict):
        out['hdirs'] = dict((k, v) for k, v in x['hdirs'].items() if isinstance(k, str) and isinstance(v, list)
                            and len(v) == 4 and _num(v[0]) and _count(v[1]) and (v[2] is None or _num(v[2]))
                            and _num(v[3]))
    if isinstance(x.get('pending'), dict):
        out['pending'] = dict((k, v) for k, v in x['pending'].items() if isinstance(k, str) and isinstance(v, str))
    for name, c in (x.get('conn') or {}).items() if isinstance(x.get('conn'), dict) else ():
        if not isinstance(name, str) or not isinstance(c, dict):
            continue
        ok = dict((o, t) for o, t in (c.get('ok') or {}).items() if isinstance(o, str) and _num(t)) \
            if isinstance(c.get('ok'), dict) else {}
        err = dict((o, [v[0], v[1] if _num(v[1]) else None]) for o, v in (c.get('err') or {}).items()
                   if isinstance(o, str) and isinstance(v, list) and len(v) == 2 and _count(v[0])) \
            if isinstance(c.get('err'), dict) else {}
        out['conn'][name] = {'ok': ok, 'err': err}
    return out


def _load_side(path):
    try:
        with open(path, 'rb') as fh:
            doc = json.loads(fh.read().decode('utf-8', 'replace'))
    except (OSError, ValueError, RecursionError):
        return _blank_side()
    if not isinstance(doc, dict) or doc.get('v') != VIEW_V:
        return _blank_side()
    side = _blank_side()
    for k in ('chats', 'turns', 'seen', 'look'):
        if isinstance(doc.get(k), dict):
            side[k] = doc[k]
    side['seen'] = dict((h, t) for h, t in side['seen'].items() if isinstance(h, str) and _num(t))
    side['turns'] = dict((o, dict((b, n) for b, n in per.items() if isinstance(b, str) and b.isdigit() and _count(n)))
                         for o, per in side['turns'].items() if isinstance(o, str) and isinstance(per, dict))
    return side


def load(folder=None):
    """(chats.json, chats-view.json) as the last look left them; a blank one for either that is missing, from
    another version or damaged."""
    folder = folder or core.data_dir()
    index = chatfile.load_index(chatfile.index_path(folder)) or _blank_index()
    for k in ('orgs', 'connectors'):
        if not isinstance(index.get(k), dict):
            index[k] = {}
    return index, _load_side(os.path.join(folder, VIEW_NAME))


def _write(index, side, folder):
    """Both files, each whole (core.write_bytes): chats-view.json first, so a pair cut short between the two leaves
    a chat whose two `end`s differ, which the next look reads again from its start."""
    core.write_bytes(os.path.join(folder, VIEW_NAME), json.dumps(side, separators=(',', ':')).encode('utf-8'))
    core.write_bytes(chatfile.index_path(folder), json.dumps(index, separators=(',', ':')).encode('utf-8'))


@contextmanager
def _one_at_a_time(folder):
    """One look at a time, across the page, a command and their threads: an exclusive lock on chats.lock in
    Switchboard's folder (core.file_lock's way, in the folder the look was given)."""
    os.makedirs(folder, mode=0o700, exist_ok=True)
    fd = os.open(os.path.join(folder, LOCK_NAME), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def org_map(st):
    """({organization id: email} for the accounts Switchboard keeps, the organizations two of them share)."""
    seen, shared = {}, set()
    for email, a in (st.get('accounts') or {}).items():
        org = a.get('org_id') if isinstance(a, dict) else None
        if not isinstance(org, str) or not org or not a.get('connected'):
            continue
        if seen.get(org, email) != email:
            shared.add(org)
        seen[org] = email
    return dict((o, e) for o, e in seen.items() if o not in shared), shared


# ---------- finding the chats ----------

def _scan(root, since):
    """{chat id: (its file, size, change time)} for each chat under Claude Code's projects folder that wrote at or
    after `since`. When two folders hold one id, the newer."""
    out = {}
    try:
        tops = list(os.scandir(root))
    except OSError:
        return out
    for p in tops:
        try:
            if not p.is_dir(follow_symlinks=False):
                continue
            with os.scandir(p.path) as it:
                for e in it:
                    if e.name.startswith('.') or not e.name.endswith('.jsonl'):
                        continue
                    try:
                        if not e.is_file(follow_symlinks=False):
                            continue
                        s = e.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    cid = e.name[:-len('.jsonl')]
                    if s.st_mtime >= since and (cid not in out or out[cid][2] < s.st_mtime):
                        out[cid] = (e.path, s.st_size, s.st_mtime)
        except OSError:
            continue
    return out


def _helpers(chat_file, seen, now):
    """(how many helpers a chat started, when the newest of them last wrote, what to remember for next time): the
    agent files in its folder's subagents/, workflow agents too. Counted, never read. A busy chat can have thousands,
    so each folder's files are looked at one by one only when the folder changed, one of them wrote in the last
    hour, or HELPERS_FULL has passed; `seen` is what the last look remembered ({folder's hash: [its change time,
    how many, newest, when its files were looked at]})."""
    root = os.path.join(chat_file[:-len('.jsonl')], 'subagents')
    n, newest, out = 0, None, {}
    for dirpath, names in _agent_files(root):
        key = _hash(os.path.relpath(dirpath, root))
        try:
            changed = os.stat(dirpath).st_mtime
        except OSError:
            continue
        was = seen.get(key)
        if isinstance(was, list) and len(was) == 4 and was[0] == changed and was[1] == len(names) and _num(was[2]) \
                and _num(was[3]) and now - was[2] > HELPERS_QUIET and 0 <= now - was[3] < HELPERS_FULL:
            m, at = was[2], was[3]
        else:
            m, at = None, round(now, 3)
            for fn in names:
                try:
                    t = os.stat(os.path.join(dirpath, fn)).st_mtime
                except OSError:
                    continue
                m = t if m is None or t > m else m
        out[key] = [changed, len(names), m, at]
        n += len(names)
        if m is not None and (newest is None or m > newest):
            newest = m
    return n, newest, out


def _agent_files(root, depth=0):
    """(folder, [agent file names]) for each folder under `root` that holds agent files. A name ending in .jsonl or
    .json is a file; any other name is looked at, and gone into when it is a folder."""
    try:
        names = os.listdir(root)
    except OSError:
        return
    agents = [nm for nm in names if nm.endswith('.jsonl') and nm.startswith('agent-')]
    if agents:
        yield root, agents
    if depth >= 4:
        return
    for nm in names:
        if not nm.endswith(('.jsonl', '.json')) and not nm.startswith('.'):
            sub = os.path.join(root, nm)
            if os.path.isdir(sub) and not os.path.islink(sub):
                for got in _agent_files(sub, depth + 1):
                    yield got


# ---------- reading one chat ----------

def _cut_of(e):
    """A chats.json entry's `cut` (the older steps left out: how many moves they held, the organization they ended on
    and since when), or None."""
    c = e.get('cut') if isinstance(e, dict) else None
    if isinstance(c, dict) and isinstance(c.get('org'), str) and c['org']:
        return {'moves': c['moves'] if _count(c.get('moves')) else None, 'org': c['org'],
                't': c['t'] if _num(c.get('t')) else None}
    return None


def _keep_newest(steps, owner, cut):
    """(steps, cut): about STEPS_KEEP of a chat's newest steps, and in `cut` how many moves the older ones held and
    the account they ended on, so chatfile.moves over what is kept, from there, still counts every move. It cuts
    between two steps settle() would never fold (SETTLE apart, or a time not known), within CUT_ROOM of STEPS_KEEP;
    a chat that changed without such a pause there is cut anyway, and its count of moves is then not known (None)."""
    k0 = len(steps) - STEPS_KEEP
    if k0 <= 0:
        return steps, cut

    def clean(k):
        a, b = steps[k - 1][0], steps[k][0]
        return a is None or b is None or b - a >= chatfile.SETTLE
    k = next((k for k in list(range(k0, max(0, k0 - CUT_ROOM), -1)) + list(range(k0 + 1, min(len(steps) - 1,
                                                                                         k0 + CUT_ROOM)))
              if clean(k)), None)
    drop = steps[:k if k is not None else k0]
    before = cut['moves'] if cut else 0
    moved = chatfile.moves(drop, cut['org'] if cut else owner) if k is not None and before is not None else None
    t = drop[-1][0]
    return steps[len(drop):], {'moves': before + moved if moved is not None else None, 'org': drop[-1][1],
                               't': round(t, 3) if t is not None else None}


def _value(raw, mark):
    """The short string after the last `mark` in a line (Claude Code writes a line's own time after its message, so
    the last one is the line's own), or None."""
    i = raw.rfind(mark)
    if i == -1:
        return None
    i += len(mark)
    j = raw.find(b'"', i, i + 160)
    return raw[i:j].decode('ascii', 'replace') if j != -1 else None


def _hash(rid):
    return hashlib.blake2b(rid.encode('utf-8', 'replace'), digest_size=6).hexdigest()


def _org(r):
    """The organization the chat is on at the line being read (a forward pass): its last account line, else the
    login it started under; '' when its file has named none yet."""
    return r.steps[-1][1] if r.steps else (r.owner or '')


def _reply(raw, n, marks, r, x, window, turns, seen, loads):
    """An assistant line: the chat's last turn on its account; a turn in the last day (each request once); a
    connector call waiting for its result. Parsed only when it is in the last day, calls a connector, or the chat's
    folder is still not known."""
    t = chatfile.parse_ts(_value(raw, marks[3]))
    calls = b'mcp__claude_ai_' in raw
    fresh = t is not None and t >= window
    d = None
    if fresh or calls or (x['cwd'] is None and r.cwd is None and n <= SMALL):
        try:
            d = loads(raw)
        except ValueError:
            return
        if not isinstance(d, dict) or d.get('type') != 'assistant':
            return                              # a helper's reply copied into the chat (a progress line), or other
        t = chatfile.parse_ts(d.get('timestamp')) or t
        if x['cwd'] is None and isinstance(d.get('cwd'), str) and d['cwd']:
            x['cwd'] = d['cwd']
        msg = d.get('message') if isinstance(d.get('message'), dict) else {}
        if msg.get('model') == '<synthetic>':
            return                              # Claude Code's own notice: no request was made
    elif b'"<synthetic>"' in raw:
        return
    org = _org(r)
    if t is not None and x['by'].get(org, 0) < t:
        x['by'][org] = round(t, 3)
    if d is None:
        return
    if fresh:
        rid = d.get('requestId') or msg.get('id')
        if isinstance(rid, str) and rid:
            h = _hash(rid)
            if h not in seen:
                seen[h] = round(t, 3)
                per = turns.setdefault(org, {})
                b = str(int(t // BUCKET))
                per[b] = per.get(b, 0) + 1
    if calls:
        content = msg.get('content')
        for block in content if isinstance(content, list) else ():
            if isinstance(block, dict) and block.get('type') == 'tool_use':
                name, tid = chatfile.connector(block.get('name')), block.get('id')
                if name and isinstance(tid, str) and 0 < len(tid) <= 128:
                    x['pending'][tid] = name
        while len(x['pending']) > PENDING_MAX:
            del x['pending'][next(iter(x['pending']))]


def _results(raw, marks, r, x, loads):
    """A user line, read only when it holds the result of a connector call waiting for one: answered or failed,
    under the account the chat is on."""
    pending, mark, i = x['pending'], marks[2], 0
    for _ in range(SEEN_SCAN):
        i = raw.find(mark, i)
        if i == -1:
            return
        i += len(mark)
        j = raw.find(b'"', i, i + 160)
        if j != -1 and raw[i:j].decode('ascii', 'replace') in pending:
            break
    else:
        return
    try:
        d = loads(raw)
    except ValueError:
        return
    if not isinstance(d, dict) or d.get('type') != 'user' or not isinstance(d.get('message'), dict):
        return
    t = chatfile.parse_ts(d.get('timestamp'))
    org = _org(r)
    content = d['message'].get('content')
    for block in content if isinstance(content, list) else ():
        if not isinstance(block, dict) or block.get('type') != 'tool_result' \
                or not isinstance(block.get('tool_use_id'), str):
            continue
        name = pending.pop(block['tool_use_id'], None)
        if name is None:
            continue
        c = x['conn'].setdefault(name, {'ok': {}, 'err': {}})
        if block.get('is_error') is True:
            n, at = c['err'].get(org) or [0, None]
            c['err'][org] = [n + 1, round(t, 3) if t is not None else at]
        elif t is not None and c['ok'].get(org, 0) < t:
            c['ok'][org] = round(t, 3)


def _title(raw, x, loads):
    try:
        d = loads(raw)
    except ValueError:
        return
    if not isinstance(d, dict):
        return
    key, field = {'custom-title': ('custom', 'customTitle'), 'ai-title': ('ai', 'aiTitle')}.get(d.get('type'),
                                                                                               (None, None))
    text = d.get(field) if key else None
    if isinstance(text, str):
        text = ' '.join(''.join(ch if ch >= ' ' and ch != '\x7f' else ' ' for ch in text).split())[:TITLE_MAX]
        if text:
            x[key] = text


def _read(path, r, x, start, budget, deadline, clock, window, turns, seen):
    """Read one chat's file on from byte `start`, in one forward pass, for all the view keeps (the module's notes):
    account lines into chatfile's Read `r`, the rest into the chat's entry `x`. Only whole lines are taken, by the
    rules chatfile.read keeps: a last line still being written is left for the next look, and a start inside a line
    passes over the rest of it. Stops after the line that reaches `budget` bytes, or once the clock passes
    `deadline`. Returns (where the next look starts, how many bytes this one read, whether it stopped at the end of
    the file's whole lines: at its end, or at a last line still being written)."""
    loads = json.loads
    pos, used, whole = start, 0, False
    try:
        fh = open(path, 'rb')
    except OSError:
        return start, 0, False
    with fh:
        skip = False
        if start > 0:
            fh.seek(start - 1)
            skip = fh.read(1) != b'\n'
        fh.seek(start)
        marks, check = None, CHECK_EVERY
        for raw in fh:
            if raw[-1:] != b'\n':
                whole = True
                break                                   # still being written: the next look takes it whole
            n = len(raw)
            pos += n
            used += n
            if skip:
                skip = False
                continue
            if marks is None:
                marks = _MARKS[b'"type":"' not in raw and b'"type": "' in raw]
            found = None
            if n <= chatfile.LINE_MAX and (b'"credential_org"' in raw or b'"session-owner"' in raw):
                found = chatfile.parse_line(raw.decode('utf-8', 'replace'), loads)
                if found is not None:
                    r.take(found)
            if found is None:
                if marks[0] in raw:
                    _reply(raw, n, marks, r, x, window, turns, seen, loads)
                elif x['pending'] and marks[1] in raw:
                    _results(raw, marks, r, x, loads)
                elif n <= SMALL and b'-title"' in raw:
                    _title(raw, x, loads)
            if used >= budget:
                break
            check -= n
            if check <= 0:
                check = CHECK_EVERY
                if clock() >= deadline:
                    break
        else:
            whole = True
    return pos, used, whole


# ---------- one look ----------

def look(st=None, root=None, now=None, days=DAYS, max_bytes=PASS_BYTES, max_seconds=PASS_SECONDS,
         clock=time.monotonic, folder=None):
    """One pass over the chats written in the last `days` days, newest first, reading at most `max_bytes` and
    `max_seconds`; both files written. `st` is Switchboard's saved state (for each account's organization); `root`
    Claude Code's projects folder and `folder` Switchboard's, both as they are now unless given (the page's job
    gives the ones it started with). Returns (chats.json, chats-view.json) as written."""
    now = time.time() if now is None else now
    root = core.projects() if root is None else root
    folder = core.data_dir() if folder is None else folder
    st = core.load() if st is None else st
    orgs, _ = org_map(st)
    with _one_at_a_time(folder):
        index, side = load(folder)
        deadline = clock() + max_seconds
        found = _scan(root, now - days * 86400)
        entries, extras = index['chats'], side['chats']
        turns, seen = side['turns'], side['seen']
        window = now - TURN_KEEP
        budget, read = max_bytes, 0
        for cid in sorted(found, key=lambda c: -found[c][2]):
            path, size, mtime = found[cid]
            e, x = entries.get(cid), extras.get(cid)
            if isinstance(e, dict) and e.get('file') == path and isinstance(x, dict) and x.get('end') == e.get('end'):
                r, x, cut = chatfile.resume_from(e), _extra(x), _cut_of(e)
            else:
                r, x, cut = chatfile.Read(), _blank_extra(), None
            if size < r.end:                            # replaced, or cut: read from its start
                r, x, cut = chatfile.Read(), _blank_extra(), None
            if size > r.end and x['whole'] != size and budget > 0 and clock() < deadline:
                r.end, used, whole = _read(path, r, x, r.end, budget, deadline, clock, window, turns, seen)
                x['whole'] = size if whole else None
                budget -= used
                read += used
            x['helpers'], x['helpers_last'], x['hdirs'] = _helpers(path, x['hdirs'], now)
            x['end'], x['size'] = r.end, size
            steps, cut = _keep_newest(r.steps, r.owner, cut)
            entries[cid] = {'file': path, 'end': r.end, 'owner': r.owner, 'owner_account': r.owner_account,
                            'steps': [[round(t, 3) if t is not None else None, o] for t, o in steps],
                            'cwd': r.cwd or x['cwd'], 'last': round(mtime, 3)}
            if cut:
                entries[cid]['cut'] = cut
            extras[cid] = x
        for cid in [c for c in entries if c not in found]:
            try:
                gone = os.stat(entries[cid]['file']).st_mtime < now - KEEP_DAYS * 86400
            except (OSError, TypeError, KeyError, ValueError):
                gone = True
            if gone:
                del entries[cid]
        for cid in [c for c in extras if c not in entries]:
            del extras[cid]
        for cid in list(extras):
            extras[cid] = _extra(extras[cid])
        for h in [h for h, t in seen.items() if t < window]:
            del seen[h]
        for org in list(turns):
            turns[org] = dict((b, k) for b, k in turns[org].items() if int(b) * BUCKET >= window - BUCKET)
            if not turns[org]:
                del turns[org]
        for x in extras.values():
            for name, c in x['conn'].items():
                for org, t in c['ok'].items():
                    if org and index['connectors'].setdefault(org, {}).get(name, 0) < t:
                        index['connectors'][org][name] = t
        left = sum(_left(extras[c]) for c in found)
        if left:                                        # a "no" is said only over a window read whole, this one
            index.pop('done', None)
            index.pop('since', None)
        else:
            index['since'], index['done'] = round(now - days * 86400, 3), True
        side['look'] = {'chats': len(found), 'read': sum(1 for c in found if not _left(extras[c])),
                        'left': left, 'bytes': read, 'at': round(now, 3),
                        'passes': (side['look'].get('passes') or 0) + 1 if _count(side['look'].get('passes')) else 1}
        index.update(v=chatfile.INDEX_V, at=round(now, 3), orgs=orgs)
        _write(index, side, folder)
    return index, side


# ---------- what else the view joins ----------

def published_by_chat(now=None, folder=None):
    """{chat id: [(time, artifact id)]}: what each chat published, created or updated, from the session hooks'
    record (hooks/published.jsonl and the older half beside it). A line the hooks did not write is passed over."""
    from . import artifacts
    now = time.time() if now is None else now
    record = os.path.join(core.data_dir() if folder is None else folder, artifacts.PUBLISHED)
    out = {}
    for path in (record + '.1', record):
        lines, _ = artifacts._whole_lines(path)
        for raw in lines:
            rec = artifacts._good_line(raw, now)
            if rec is None:
                continue
            try:
                sid = json.loads(raw.decode('utf-8')).get('sid')
            except (ValueError, UnicodeDecodeError, RecursionError, AttributeError):
                continue
            if isinstance(sid, str) and 0 < len(sid) <= 128:
                out.setdefault(sid, []).append((rec['t'], rec['id']))
    return out


def own_logins(folder=None):
    """{chat id: True} for each chat known to run on a login of its own (its own CLAUDE_CONFIG_DIR: a switch never
    moves it): the status line's record of it says so (live/<id>.json, kept a day), or the session hooks' record of
    it does (hooks/<id>.json, "own"). Any other chat: not known."""
    folder = folder or core.data_dir()
    out = {}
    for sub, key in (('live', 'own_profile'), ('hooks', 'own')):
        try:
            names = os.listdir(os.path.join(folder, sub))
        except OSError:
            continue
        for name in names[:4096]:
            if name.startswith('.') or not name.endswith('.json'):
                continue
            rec = core.read_json(os.path.join(folder, sub, name))
            if rec.get(key) is True:
                sid = rec.get('session_id') if isinstance(rec.get('session_id'), str) else name[:-len('.json')]
                out[sid] = True
    return out


# ---------- the view ----------

def _shorts(ids):
    """{id: its shortest start, SHORT characters at least, that no other id shares}."""
    ids = sorted(ids)
    out = {}
    for i, cid in enumerate(ids):
        need = SHORT
        for other in (ids[i - 1] if i else None, ids[i + 1] if i + 1 < len(ids) else None):
            if other is None:
                continue
            k = 0
            while k < len(cid) and k < len(other) and cid[k] == other[k]:
                k += 1
            need = max(need, k + 1)
        out[cid] = cid[:need]
    return out


def view(index, side, st, now=None, days=DAYS, published=None, own=None):
    """What `switchboard chats`, GET /api/chats and the accounts' `chats` blocks show (see docs/how-it-works.md,
    Chats): every account Switchboard keeps with its chats, and each chat written in the last `days` days. An
    account's counts are over the chats written in the last day, whatever `days` is."""
    now = time.time() if now is None else now
    index = index if isinstance(index, dict) and isinstance(index.get('chats'), dict) else _blank_index()
    side = side if isinstance(side, dict) and isinstance(side.get('chats'), dict) else _blank_side()
    orgs, shared = org_map(st)
    active = st.get('active') if isinstance(st.get('active'), str) else None
    conns = index.get('connectors') if isinstance(index.get('connectors'), dict) else {}
    done = index.get('done') is True
    since = index.get('since') if _num(index.get('since')) else None
    published, own = published or {}, own or {}

    def who(org):
        if not org:
            return None
        if org in orgs:
            return {'email': orgs[org], 'kept': True}
        return {'email': None, 'kept': org in shared, 'org': org[:8]}

    shorts = _shorts(index['chats'])
    rows = []
    for cid, e in index['chats'].items():
        if not isinstance(e, dict):
            continue
        x = _extra(side['chats'].get(cid))
        r = chatfile.resume_from(e)
        steps, owner, cut = r.steps, r.owner, _cut_of(e)
        base = cut['org'] if cut else owner         # where the steps kept begin (older ones are counted in `cut`)
        complete = x['size'] > 0 and not _left(x)
        on = chatfile.on_now(steps, base) if complete else None      # a chat not read to its end: not known yet
        times = [t for t in (e.get('last'), x['helpers_last']) if _num(t)]
        last = max(times) if times else None
        age = now - last if last is not None else None
        state = 'working' if age is not None and age < IDLE_AFTER else \
            'idle' if age is not None and age < RECENT else 'old'
        recent, prev = [], base
        for t, org in chatfile.settle(steps) if complete else ():
            if prev is not None and org != prev:
                recent.append({'t': t, 'from': who(prev), 'to': who(org)})
            prev = org
        first = {}
        for t, aid in sorted(published.get(cid) or ()):
            first.setdefault(aid, t)
        by_owner, pages_not_known = {}, 0
        for aid, t in first.items():
            org = chatfile.on_at(steps, t, base) if complete and not (cut and (cut['t'] is None or t < cut['t'])) \
                else None                       # before the steps kept: not known
            if org:
                by_owner[org] = by_owner.get(org, 0) + 1
            else:
                pages_not_known += 1
        held_pages = [{'owner': who(o), 'n': k} for o, k in sorted(by_owner.items(), key=lambda kv: -kv[1])
                      if on and o != on]
        connectors, held = [], []
        for name in sorted(x['conn']):
            c = x['conn'][name]
            connectors.append({'name': name,
                               'answered': [{'as': who(o), 'at': t} for o, t in sorted(c['ok'].items(),
                                                                                      key=lambda kv: kv[1])],
                               'failed': [{'as': who(o), 'n': v[0], 'at': v[1]} for o, v in sorted(c['err'].items())]})
            others = sorted((t, o) for o, t in c['ok'].items() if o and o != on)
            if on and others:
                t, o = others[-1]
                here_at = (conns.get(on) or {}).get(name)
                held.append({'name': name, 'as': who(o), 'at': t,
                             'here': 'yes' if _num(here_at) else 'no' if done and since is not None else 'not known',
                             'here_at': here_at if _num(here_at) else None,
                             'failed': (c['err'].get(on) or [0])[0]})
        mine = own.get(cid) is True
        on_email = (who(on) or {}).get('email')
        rows.append(({'id': cid, 'short': shorts.get(cid, cid[:SHORT]),
                      'title': x['custom'] or x['ai'], 'folder': os.path.basename(r.cwd.rstrip('/')) or None
                      if r.cwd else None,
                      'started': who(owner), 'on': who(on), 'since': recent[-1]['t'] if recent else None,
                      'moves': (cut['moves'] if cut else 0) + chatfile.moves(steps, base)
                      if complete and not (cut and cut['moves'] is None) else None,
                      'recent': recent[-RECENT_MOVES:],
                      'last': last, 'state': state, 'helpers': x['helpers'], 'own_login': True if mine else None,
                      'next_on': active if active and on and not mine and on_email != active else None,
                      'pages': len(first), 'pages_not_known': pages_not_known,
                      'holds': {'pages': held_pages, 'connectors': held}, 'connectors': connectors,
                      'read': complete}, (on or '') if complete else None, owner or '', x))

    rank = {'working': 0, 'idle': 1, 'old': 2}
    rows.sort(key=lambda rw: (rank[rw[0]['state']], -(rw[0]['last'] or 0), rw[0]['id']))
    listed = [rw for rw in rows if rw[0]['last'] is not None and now - rw[0]['last'] <= days * 86400]
    lately = [rw for rw in rows if rw[0]['last'] is not None and now - rw[0]['last'] < RECENT]

    keys = []
    order = sorted((st.get('accounts') or {}).items(), key=lambda kv: (kv[1].get('slot') or 99, kv[0]))
    for email, a in order:
        org = a.get('org_id')
        if a.get('connected') and isinstance(org, str) and org and org not in keys:
            keys.append(org)
    for rw in lately + listed:
        for org in (rw[1], rw[2]):
            if org and org not in keys:
                keys.append(org)
    if any(rw[1] == '' for rw in lately + listed):     # read whole, and its file names no account
        keys.append('')

    accounts = []
    for org in keys:
        on_it = [rw for rw in lately if rw[1] == org]
        started = [rw for rw in lately if org and rw[2] == org]
        moved = {}
        for rw in started:
            if rw[1] and rw[1] != org:
                moved[rw[1]] = moved.get(rw[1], 0) + 1
        lasts = [rw[3]['by'].get(org) for rw in rows if _num(rw[3]['by'].get(org))]
        w = who(org)
        accounts.append({'email': w['email'] if w else None, 'kept': bool(w and w['kept']), 'known': bool(w),
                         'org': None if not w or w['email'] else w.get('org'),
                         'active': bool(w and w['email'] and w['email'] == active),
                         'on': len(on_it), 'working': sum(1 for rw in on_it if rw[0]['state'] == 'working'),
                         'started': len(started), 'elsewhere': sum(moved.values()),
                         'moved_to': [{'account': who(o), 'n': k} for o, k in sorted(moved.items(), key=lambda kv: -kv[1])],
                         'last': max(lasts) if lasts else None,
                         'turns': sum(k for b, k in ((side.get('turns') or {}).get(org) or {}).items()
                                      if int(b) * BUCKET >= now - RECENT),
                         'connectors': [{'name': n, 'at': t} for n, t in sorted((conns.get(org) or {}).items())]
                         if org else []})

    look_ = side.get('look') if isinstance(side.get('look'), dict) else {}
    of = look_.get('chats') if _count(look_.get('chats')) else 0
    return {'v': 1, 'state': 'ready' if done else 'reading', 'at': index.get('at') or None, 'now': now,
            'days': days, 'active': active, 'since': since,
            'reading': {'done': bool(look_) and not look_.get('left'), 'read': look_.get('read') or 0, 'of': of},
            'accounts': accounts, 'chats': [rw[0] for rw in listed]}


def counts(v):
    """{email: {'on', 'working', 'elsewhere'}}: each kept account's block in GET /api/state, from a view."""
    out = {}
    for a in (v or {}).get('accounts') or []:
        if a.get('kept') and a.get('email'):
            out[a['email']] = {'on': a['on'], 'working': a['working'], 'elsewhere': a['elsewhere']}
    return out


def build(st=None, days=DAYS, now=None, folder=None):
    """The view of what the last look saved, joined with what the session hooks noted (pages, own logins)."""
    st = core.load() if st is None else st
    index, side = load(folder)
    return view(index, side, st, now=now, days=days, published=published_by_chat(now, folder),
                own=own_logins(folder))


# ---------- `switchboard chats` ----------

def _options(args):
    """{'all', 'days', 'account', 'json'} from the command's words, or a line saying what is wrong."""
    opts = {'all': False, 'days': None, 'account': None, 'json': False}
    i = 0
    while i < len(args):
        a = args[i]
        if a == '--all':
            opts['all'] = True
        elif a == '--json':
            opts['json'] = True
        elif a in ('--days', '--account'):
            if i + 1 >= len(args):
                return '%s needs a value.' % a
            i += 1
            if a == '--account':
                opts['account'] = args[i]
            else:
                try:
                    opts['days'] = float(args[i])
                except ValueError:
                    opts['days'] = 0
                if not 0 < opts['days'] <= 366:
                    return '--days needs a number of days, like --days 7.'
        else:
            return 'Unknown word: %s.' % a
        i += 1
    return opts


def _which(st, name):
    """The email of the account someone typed: its email, short name or number."""
    want = name.strip().lower()
    for email, a in (st.get('accounts') or {}).items():
        if want in (email.lower(), (a.get('alias') or '').lower(), str(a.get('slot'))):
            return email
    raise core.SwitchError('No account called "%s". Run `switchboard` to see them.' % name)


def only(v, email):
    """The view with one account's group and the chats on it."""
    return dict(v, account=email, accounts=[a for a in v['accounts'] if a.get('email') == email],
                chats=[c for c in v['chats'] if (c.get('on') or {}).get('email') == email])


def main(args, out=None, now=None):
    """`switchboard chats`: a bounded look of its own (the page's job need not run), then the view. Exits 0, or 2
    for words it doesn't know."""
    from . import term
    out = out or sys.stdout
    opts = _options(args)
    if not isinstance(opts, dict):
        out.write('%s\n%s\n' % (opts, USAGE))
        return 2
    shown = opts['days'] or (DAYS if opts['all'] else RECENT / 86400)
    if core.DEMO:
        from . import demo
        v = demo.chats(days=shown)
        st = demo.STATE
    else:
        st = core.load()
        look(st=st, now=now, days=max(DAYS, opts['days'] or 0), max_bytes=COMMAND_BYTES,
             max_seconds=COMMAND_SECONDS)
        v = build(st, days=shown, now=now)
    if opts['account']:
        v = only(v, _which(st, opts['account']))
    if opts['json']:
        out.write(json.dumps(v, indent=1) + '\n')
        return 0
    width = None
    try:
        if out.isatty():
            width = shutil.get_terminal_size((100, 24)).columns
    except (AttributeError, ValueError, OSError):
        width = None
    colour = term.colour_ok(out, os.environ)
    for line in term.chats_lines(v, width=width, colour=colour):
        out.write(line + '\n')
    return 0
