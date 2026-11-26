"""One Claude Code chat file, read for the account the chat was on.

Claude Code itself writes, into each chat's own file (<Claude Code's folder>/projects/<project>/<chat id>.jsonl, and
each helper's file beside it), two kinds of line that name an account by its organization id:

  {"type": "session-owner", "organizationUuid": ..., "accountUuid": ..., "sessionId": ...}
      the login the chat started under. It carries no time, never changes, and is repeated through the file.

  {"type": "attachment", "attachment": {"type": "credential_org", "organizationUuid": ...},
   "timestamp": "2026-11-18T02:47:45.569Z", "sessionId": ..., "cwd": ..., "version": ..., ...}
      the login the chat is on, written near its start and again when that changes (seen from Claude Code 2.1.296
      to 2.1.325; an older Claude Code writes neither line, and then read() simply finds nothing).

So a chat's own file says which account it began on and every account it has been on since, with times, whatever
Switchboard was told or worked out from the clock. That is what this module reads, and all it reads: of each line
only its kind, the organization id, the time, and the chat's id and folder. No message, no title, no tool call.

Two things to know about the second line (anthropics/claude-code issue 102201):
- Claude Code takes "the organization now" from the account block of its settings file, so a line is written at the
  first request after the block changed: close to the moment the login in use changed, not exactly at it.
- another program rewriting that block makes a long chat write lines back and forth within seconds. settle() folds
  such a flutter into the account the chat came to rest on.

Nothing here imports more than os, so Claude Code's hooks can use it on their hot path: json is imported only when no
reader is handed in (statusline's small reader does as well).
"""
import os

ORG_MARK = '"credential_org"'
OWNER_MARK = '"session-owner"'
LINE_MAX = 8192            # both lines are a few hundred bytes; a longer line that carries the words is something else
SETTLE = 90.0              # changes closer together than this are one move, to the last account of the run
TAIL = 4 << 20

_DAYS_BEFORE = (0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334)


def parse_ts(text):
    """Seconds since 1970 for Claude Code's own times ("2026-11-18T02:47:45.569Z", always UTC), or None."""
    if not isinstance(text, str) or len(text) < 19:
        return None
    try:
        if text[4] != '-' or text[7] != '-' or text[10] not in 'T ' or text[13] != ':' or text[16] != ':':
            return None
        y, mo, d = int(text[0:4]), int(text[5:7]), int(text[8:10])
        h, mi, s = int(text[11:13]), int(text[14:16]), int(text[17:19])
        rest = text[19:]
        frac = 0.0
        if rest[:1] == '.':
            i = 1
            while i < len(rest) and rest[i].isdigit():
                i += 1
            frac = float('0' + rest[:i]) if i > 1 else 0.0
            rest = rest[i:]
        if rest not in ('Z', '', '+00:00'):
            return None
        if not (1970 <= y <= 9999 and 1 <= mo <= 12 and 1 <= d <= 31 and h < 24 and mi < 60 and s < 61):
            return None
    except (ValueError, IndexError):
        return None
    years = y - 1970
    leaps = (y - 1969) // 4 - (y - 1901) // 100 + (y - 1601) // 400
    days = years * 365 + leaps + _DAYS_BEFORE[mo - 1] + (d - 1)
    if mo > 2 and (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)):
        days += 1
    return days * 86400 + h * 3600 + mi * 60 + s + frac


def _json_loads():
    import json
    return json.loads


def _org(x):
    return x if isinstance(x, str) and 8 <= len(x) <= 64 else None


def parse_line(line, loads=None):
    """What one line of a chat file says about the account, or None (nearly every line).

    ('owner', organization id, account id or None, chat id or None)
    ('org', organization id, time or None, chat id or None, folder or None)
    """
    if len(line) > LINE_MAX:
        return None
    is_org = ORG_MARK in line
    if not is_org and OWNER_MARK not in line:
        return None
    try:
        d = (loads or _json_loads())(line)
    except Exception:  # noqa: BLE001 - a torn or foreign line says nothing
        return None
    if not isinstance(d, dict):
        return None
    sid = d.get('sessionId') if isinstance(d.get('sessionId'), str) else None
    if d.get('type') == 'session-owner':
        org = _org(d.get('organizationUuid'))
        if org is None:
            return None
        acct = d.get('accountUuid') if isinstance(d.get('accountUuid'), str) else None
        return ('owner', org, acct, sid)
    att = d.get('attachment')
    if d.get('type') == 'attachment' and isinstance(att, dict) and att.get('type') == 'credential_org':
        org = _org(att.get('organizationUuid'))
        if org is None:
            return None
        cwd = d.get('cwd') if isinstance(d.get('cwd'), str) else None
        return ('org', org, parse_ts(d.get('timestamp')), sid, cwd)
    return None


class Read(object):
    """What a chat file said: the account it started under, and each account it was on, oldest first."""

    __slots__ = ('owner', 'owner_account', 'steps', 'session', 'cwd', 'end', 'size')

    def __init__(self):
        self.owner = None           # organization id of the login the chat started under
        self.owner_account = None   # that login's account id
        self.steps = []             # [(time or None, organization id)], changes only
        self.session = None         # the chat's id, as Claude Code wrote it
        self.cwd = None             # the folder the chat last said it was in
        self.end = 0                # where the next read should start (after the last whole line read)
        self.size = 0               # the file's size when read

    def take(self, found):
        if found is None:
            return
        if found[0] == 'owner':
            if self.owner is None:
                self.owner, self.owner_account = found[1], found[2]
            if found[3] and not self.session:
                self.session = found[3]
            return
        _, org, t, sid, cwd = found
        if sid and not self.session:
            self.session = sid
        if cwd:
            self.cwd = cwd
        if not self.steps or self.steps[-1][1] != org:
            self.steps.append((t, org))


def read(path, start=0, loads=None, max_bytes=None, into=None):
    """Read a chat file from byte `start` (0 = its beginning) for its account lines.

    Returns a Read; hand a Read back as `into`, with its `end` as `start`, to read on from where the last look
    stopped. Only whole lines are taken: a last line still being written is left for the next look. A file that is
    shorter than `start` (replaced, or cut) is read from its beginning into a fresh Read. Never raises for a file
    that is missing or can't be read: the Read then says nothing new.
    """
    out = into if into is not None else Read()
    try:
        size = os.path.getsize(path)
    except OSError:
        return out
    if start > size:
        out = Read()
        start = 0
    out.size = size
    if start == size:
        out.end = size
        return out
    loads = loads or _json_loads()
    try:
        with open(path, 'rb') as fh:
            fh.seek(start)
            pos = start
            left = max_bytes
            carry_first = start > 0 and not _at_line_start(fh, start)
            for raw in fh:
                if not raw.endswith(b'\n'):
                    break                                   # still being written: the next look takes it whole
                pos += len(raw)
                if carry_first:
                    carry_first = False                     # the rest of a line the earlier look stopped inside
                    continue
                if len(raw) <= LINE_MAX and (b'"credential_org"' in raw or b'"session-owner"' in raw):
                    out.take(parse_line(raw.decode('utf-8', 'replace'), loads))
                if left is not None:
                    left -= len(raw)
                    if left <= 0:
                        break
            out.end = pos
    except OSError:
        pass
    return out


def _at_line_start(fh, start):
    """Whether byte `start` begins a line (the byte before it is a line end). Leaves the file at `start`."""
    fh.seek(start - 1)
    before = fh.read(1)
    return before == b'\n'


def tail(path, nbytes=TAIL, loads=None):
    """The last `nbytes` of a chat file, read the same way, without reading a long chat from its start.

    A tail only says what changed inside it. A chat can go for hours, and many megabytes, without changing account,
    so `steps` is often empty here: that means "this tail doesn't say", never "still on the account it started
    under" (on real chats the owner was the wrong answer for most long ones). Ask on_now(steps) with no owner, and
    when it is None use a longer read, or a saved Read carried on with read(start=its end, into=it). `owner` is
    filled when a session-owner line falls inside the tail (Claude Code repeats it, so it usually does)."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return Read()
    return read(path, start=max(0, size - nbytes), loads=loads)


def settle(steps, quiet=SETTLE):
    """The same steps with each flutter folded: changes that follow one another within `quiet` seconds become one
    move, at the first change's time, to the account the chat came to rest on. A fold that ends where it began
    (A, then B and back to A within the minute) is no move at all."""
    out = []
    i, n = 0, len(steps)
    while i < n:
        t0, org = steps[i]
        j = i
        while j + 1 < n and steps[j + 1][0] is not None and steps[j][0] is not None \
                and steps[j + 1][0] - steps[j][0] < quiet:
            j += 1
        org = steps[j][1]
        if not out or out[-1][1] != org:
            out.append((t0, org))
        i = j + 1
    return out


def on_at(steps, t, owner=None):
    """The organization a chat was on at time `t`: the last step at or before it; before the first step (or with
    no steps at all) the login it started under."""
    found = owner
    for when, org in steps:
        if when is not None and when > t:
            break
        found = org
    return found


def on_now(steps, owner=None):
    """The organization a chat is on now, as far as its own file says."""
    return steps[-1][1] if steps else owner


def moves(steps, owner=None):
    """How many times the chat changed account (after settle): the steps past the first, plus one when the first
    step is already another account than the one it started under."""
    s = settle(steps)
    if not s:
        return 0
    n = len(s) - 1
    if owner is not None and s[0][1] != owner:
        n += 1
    return n


# ---- what the page's look at every chat leaves for the other parts (hooks, the terminal) ------------------------
#
# <Switchboard's folder>/chats.json, written whole and atomically by switchboard/chats.py:
#
#   {"v": 1,
#    "at": 1794000000.0,                                  when it was written
#    "orgs": {"<organization id>": "<account email>"},    the accounts Switchboard keeps, by organization
#    "chats": {"<chat id>": {
#        "file": "<the chat's file>",
#        "end": 123456,                                   how far that file was read (read() carries on from here)
#        "owner": "<organization id>" or null,            the login it started under
#        "owner_account": "<account id>" or null,         that login's account id (missing in files from earlier builds)
#        "steps": [[time or null, "<organization id>"]],  each account it was on, oldest first, as read (not settled);
#                                                         its newest few hundred when it has more
#        "cut": {"moves": 12, "org": "<organization id>", "t": time or null},
#                                                         (optional) the older steps left out: how many moves they
#                                                         held (moves() over "steps" from "org" adds the rest), the
#                                                         organization they ended on and its time; before "t" the
#                                                         account it was on is not known
#        "cwd": "<folder>" or null,
#        "last": 1794000000.0}},                          when the chat last wrote
#    "connectors": {"<organization id>": {"<connector>": 1794000000.0}},
#                                                         per account, each claude.ai connector that answered while
#                                                         a chat was on it, and when it last did
#    "done": true,                                        (optional) there only while the look that wrote this file
#                                                         read every chat written in its window whole (a last line
#                                                         still being written counts as read); without it, a
#                                                         connector missing from "connectors" is not known, not
#                                                         "never answered"
#    "since": 1793400000.0}                               (optional, with "done") that window's start: "connectors"
#                                                         holds every answer from then up to "at"
#
# It holds no message, no title and no tool input. A reader treats a missing, old or unreadable file as "not known"
# and falls back to the chat's own file.

INDEX_NAME = 'chats.json'
INDEX_V = 1
CONNECTOR_PREFIX = 'mcp__claude_ai_'


def index_path(data_dir):
    return os.path.join(data_dir, INDEX_NAME)


def load_index(path, loads=None):
    """The saved look at every chat, or None when there is none that can be read (see the shape above)."""
    try:
        with open(path, 'rb') as fh:
            raw = fh.read(64 << 20)
        doc = (loads or _json_loads())(raw.decode('utf-8', 'replace'))
    except Exception:  # noqa: BLE001 - missing, torn or foreign: not known
        return None
    if not isinstance(doc, dict) or doc.get('v') != INDEX_V or not isinstance(doc.get('chats'), dict):
        return None
    return doc


def resume_from(entry):
    """A Read carried on from one chat's entry in the saved look, ready for read(file, start=r.end, into=r)."""
    r = Read()
    if not isinstance(entry, dict):
        return r
    r.owner = _org(entry.get('owner'))
    acct = entry.get('owner_account')
    r.owner_account = acct if r.owner and isinstance(acct, str) and 0 < len(acct) <= 128 else None
    for step in entry.get('steps') or ():
        if isinstance(step, (list, tuple)) and len(step) == 2 and _org(step[1]):
            t = step[0] if isinstance(step[0], (int, float)) and not isinstance(step[0], bool) else None
            if not r.steps or r.steps[-1][1] != step[1]:
                r.steps.append((t, step[1]))
    r.cwd = entry.get('cwd') if isinstance(entry.get('cwd'), str) else None
    end = entry.get('end')
    r.end = end if isinstance(end, int) and not isinstance(end, bool) and end >= 0 else 0
    return r


def connector(tool_name):
    """The claude.ai connector a tool belongs to, as Claude Code names it in its own messages ("Google Drive" for
    mcp__claude_ai_Google_Drive__search, whose errors say 'MCP server "claude.ai Google Drive" ...'), or None for
    any other tool. Connectors belong to the account that added them on claude.ai."""
    if not isinstance(tool_name, str) or not tool_name.startswith(CONNECTOR_PREFIX):
        return None
    name = tool_name[len(CONNECTOR_PREFIX):].split('__', 1)[0].replace('_', ' ').strip()
    return name or None
