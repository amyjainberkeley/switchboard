"""Claude Code activity and obligations: busy or idle, and which account owns open docs, artifacts and watches.

Busy or idle (an optional switch waits for "no turn for 5 minutes"):
- busy while any transcript under Claude Code's projects folder (chats, helpers, workflow agents) was written in the
  last 5 minutes; or a turn is still open (its last request has no reply, or its last tool call no result) and its
  transcript was written in the last 15 minutes (an open turn older than that is stuck: a question nobody answered,
  a crash); or the status line's numbers moved in the last 5 minutes (a reply used some of the limit).
- idle_for_s: how long since the last of those (None when nothing was ever seen).
- `claude` processes are counted for the page, never used for busy: an open editor panel keeps one for days.

Obligations (what an optional switch would strand):
- an artifact published (a tool result starting "Published ... at <link>", "Created a new Artifact at <link>" or
  "Updated the Artifact at <link>") in the last 30 minutes;
- a Claude Doc born (the docs connector's create answer carrying the doc's link) in the last 30 minutes;
- a watch started (the Artifact or ArtifactComments tool's own answer, or a publish answer, that says the session now
  watches an artifact, with its link) and not ended, while its session wrote in the last 6 hours. Another tool's
  output that quotes a link beside the same words (a Bash or Read result, a fetched chat log) is no watch: each
  transcript's tool calls to those tools are remembered by id, so their answers can be told apart;
- a claude.ai connector used (a tool call named mcp__claude_ai_*, kind 'connector', with the connector's name) in the
  last 30 minutes, the newest use of each connector. A Claude Docs call whose answer is a doc born is that doc's
  obligation only, never a connector one too (matched by the call's id).
Each belongs to the account the chat's own file says it was on at that time (chatfile: Claude Code writes each change
of login into it; settled, so a flutter is no move), as `org`, an organization id (autoloop._snapshot names the
account); and to the account Claude Code was on then by the timeline the loop keeps ([(since, account id)],
oldest first), as `account_id`, for when the chat's file doesn't say. Within 30 s before a switch the timeline counts it
for the newer account (a running Claude Code may already be on the new login; holding a switch costs nothing).

What is read: file times, and of recent transcripts only the lines that carry an artifact link inside a tool result
(for the result's first words and its link), the assistant lines that call a claude.ai connector (for its name, the
call's id and the time) and the two lines that name an account (chatfile.parse_line: the organization and the time).
Nothing else from a transcript is kept or logged. Each look is bounded (MAX_FILES files, MAX_BYTES bytes) and each
file is read on from where the last look stopped.

The exact words of the Artifact tool's watch line were not checked against a real transcript, so WATCH_ON_RE and
WATCH_OFF_RE are deliberately broad.
"""
import json
import os
import re
import subprocess
import threading

from . import usage
from .. import awake, chatfile, core

IDLE_AFTER = 300.0
STUCK_CAP = 900.0
OBLIGATION_KEEP = 1800.0
WATCH_KEEP = 6 * 3600.0
SETTLE = 30.0
STATUS_MOVED = 300.0
MAX_FILES = 64
MAX_TURNS = 32
MAX_BYTES = 8 * 1024 * 1024
FIRST_TAIL = 1024 * 1024
LOOK_BACK = 4 * 1024 * 1024     # how far before a first read's start a watch tool's call is looked for (only when needed)
LONG_LINE = 4 * 1024 * 1024
PROCS_EVERY = 60.0
PS = ['/bin/ps', '-axo', 'comm=']

ART_URL = r'(https://claude\.ai/(?:code/)?artifact/[A-Za-z0-9_-]{16,})'
HEAD_RE = re.compile(r'(?:Published .+? at|Created a new Artifact at|Updated the Artifact at) ' + ART_URL)
DOC_RE = re.compile(r'"frame":\{"slug":"[^"]+","url":"' + ART_URL + '"')
URL_RE = re.compile(ART_URL)
WATCH_ON_RE = re.compile(r'\b(?:now watch(?:es|ing)|is watching|started watching|watching (?:this|the|that|it)\b|'
                         r'subscribed to)', re.I)
# (no bare "rate limit": a per-minute 429 from the API says that too, and it is not the account's usage limit)
LIMIT_RE = re.compile(r'\b(?:limit reached|hit your (?:usage |session |weekly )?limit|usage limit|'
                      r'out of (?:usage|extra usage))', re.I)
# Words that say a notice is NOT the account's limit, taken out before LIMIT_RE looks: Claude Code's notice for the
# server's own short throttle reads "Server is temporarily limiting requests (not your usage limit) · Rate limited".
NOT_LIMIT_RE = re.compile(r"\b(?:not|isn't|is not) (?:your |a |the )?(?:usage |session |weekly )?limit\b", re.I)
LIMIT_FRESH = 600.0          # a limit notice this recent means the account it was written under is refused now
WATCH_TOOLS = ('Artifact', 'ArtifactComments')     # the tools whose own answer says a session watches an artifact
TOOLS_KEPT = 256                                    # tool call ids remembered per transcript
CONNECTOR_MARK = b'mcp__claude_ai_'                 # a line that may call a claude.ai connector
ACCOUNT_MARKS = (b'"credential_org"', b'"session-owner"')   # a line that may name the account (chatfile.parse_line)
STEPS_KEPT = 256                                    # account changes remembered per transcript
WATCH_OFF_RE = re.compile(r"\b(?:not watching|no longer watch\w*|stopped watching|does not watch|doesn't watch|"
                          r"isn't watching|not subscribed|unsubscribed|watch(?:ing)? (?:has )?ended)", re.I)


def count_claude_processes(timeout=3.0):
    """How many `claude` processes run, or None when ps can't say. Bounded by `timeout`."""
    try:
        out = subprocess.run(PS, capture_output=True, text=True, timeout=timeout).stdout or ''
    except (OSError, subprocess.SubprocessError):
        return None
    return sum(1 for line in out.splitlines() if os.path.basename(line.strip()) == 'claude')


def owner_at(timeline, t):
    """The account Claude Code was on at time t, from [(since, account id)] oldest first; None when not known. Within
    SETTLE seconds before a switch, the newer account."""
    who = None
    for since, aid in timeline:
        if since is not None and since - SETTLE <= t:
            who = aid
        else:
            break
    return who


def _limit_notice(line, fallback_t):
    """[(time, 'limit', None)] when the line is Claude Code's own notice that a usage limit stopped a reply (an
    assistant entry of model "<synthetic>", or marked as an API error, whose text says a limit was reached)."""
    try:
        d = json.loads(line)
    except ValueError:
        return []
    if not isinstance(d, dict) or d.get('type') != 'assistant':
        return []
    msg = d.get('message') if isinstance(d.get('message'), dict) else {}
    if msg.get('model') != '<synthetic>' and d.get('isApiErrorMessage') is not True:
        return []
    content = msg.get('content')
    text = ' '.join(b.get('text') for b in content if isinstance(b, dict) and isinstance(b.get('text'), str)) \
        if isinstance(content, list) else content if isinstance(content, str) else ''
    if not LIMIT_RE.search(NOT_LIMIT_RE.sub(' ', text or '')):
        return []
    return [(usage.parse_iso(d.get('timestamp')) or fallback_t, 'limit', None)]


def _note_tools(line, tools):
    """Remember the ids of this assistant line's calls to the watch tools (WATCH_TOOLS), at most TOOLS_KEPT."""
    try:
        d = json.loads(line)
    except ValueError:
        return
    msg = d.get('message') if isinstance(d, dict) and isinstance(d.get('message'), dict) else {}
    content = msg.get('content') if isinstance(msg.get('content'), list) else []
    for block in content:
        if isinstance(block, dict) and block.get('type') == 'tool_use' and block.get('name') in WATCH_TOOLS \
                and isinstance(block.get('id'), str):
            tools[block['id']] = block['name']
    while len(tools) > TOOLS_KEPT:
        tools.pop(next(iter(tools)))


def _connector_calls(line, fallback_t):
    """[(time, 'connector', (connector, tool call id or None))] for an assistant line's calls to claude.ai connectors
    (chatfile.connector names them: 'Google Drive' for mcp__claude_ai_Google_Drive__search)."""
    try:
        d = json.loads(line)
    except ValueError:
        return []
    msg = d.get('message') if isinstance(d, dict) and isinstance(d.get('message'), dict) else {}
    out = []
    for block in msg.get('content') if isinstance(msg.get('content'), list) else []:
        name = chatfile.connector(block.get('name')) if isinstance(block, dict) and block.get('type') == 'tool_use' \
            else None
        if name is not None:
            uid = block.get('id') if isinstance(block.get('id'), str) else None
            out.append((usage.parse_iso(d.get('timestamp')) or fallback_t, 'connector', (name, uid)))
    return out


def events_in_line(line, fallback_t, tools=None, unknown=None, born=None):
    """[(time, kind, url)] from one transcript line (bytes): kind 'artifact', 'doc', 'watch', 'unwatch', 'limit'
    (Claude Code's own notice that a usage limit stopped a reply; url None) or 'connector' (a call to a claude.ai
    connector; in place of a url, (its name, the call's id)). tools: this transcript's remembered calls to the watch
    tools ({tool call id: name}, filled from assistant lines here); a watch or its end counts only in their answers, or
    in a publish answer (without `tools`: only in a publish answer). unknown: a list that gets (tool call id, time,
    kind, url) for an answer with watch words whose call is not in `tools` (it may lie before where a first read began;
    the Scanner looks there). born: a list that gets the call id of each answer that is a Claude Doc born."""
    if (b'<synthetic>' in line or b'isApiErrorMessage' in line) and (b'imit' in line or b'out of' in line):
        return _limit_notice(line, fallback_t)          # (LIMIT_RE's "out of usage" carries no "limit")
    if b'tool_use' in line and b'tool_result' not in line and \
            (CONNECTOR_MARK in line or (tools is not None and b'Artifact' in line)):
        if tools is not None and b'Artifact' in line:
            _note_tools(line, tools)
        return _connector_calls(line, fallback_t) if CONNECTOR_MARK in line else []
    if b'tool_result' not in line or b'claude.ai/' not in line or b'artifact/' not in line:
        return []
    try:
        d = json.loads(line)
    except ValueError:
        return []
    if not isinstance(d, dict) or d.get('type') != 'user':
        return []
    msg = d.get('message') if isinstance(d.get('message'), dict) else {}
    content = msg.get('content')
    if not isinstance(content, list):
        return []
    t = usage.parse_iso(d.get('timestamp')) or fallback_t
    out = []
    for block in content:
        if not isinstance(block, dict) or block.get('type') != 'tool_result' or block.get('is_error'):
            continue
        text = block.get('content')
        if isinstance(text, list):
            text = '\n'.join(x.get('text') for x in text if isinstance(x, dict) and isinstance(x.get('text'), str))
        if not isinstance(text, str):
            continue
        m = HEAD_RE.match(text)
        own = m is not None or (tools is not None and tools.get(block.get('tool_use_id')) in WATCH_TOOLS)
        if m:
            out.append((t, 'artifact', m.group(1)))
        elif text.startswith('{"verdict"') and '"created":' in text[:4000]:
            m = DOC_RE.search(text)
            if m:
                out.append((t, 'doc', m.group(1)))
                if born is not None and isinstance(block.get('tool_use_id'), str):
                    born.append(block['tool_use_id'])
        u = URL_RE.search(text) if own or unknown is not None else None
        kind = 'unwatch' if u and WATCH_OFF_RE.search(text) else 'watch' if u and WATCH_ON_RE.search(text) else None
        if kind and own:
            out.append((t, kind, u.group(1)))
        elif kind and unknown is not None and tools is not None and isinstance(block.get('tool_use_id'), str):
            unknown.append((block['tool_use_id'], t, kind, u.group(1)))
    return out


def _calls_before(path, end, want, limit):
    """{tool call id: name} for the calls to the watch tools among `want`, in the `limit` bytes of path before `end`
    (the line that crosses `end` included). Bounded: it reads at most `limit` bytes."""
    found = {}
    start = max(0, end - limit)
    try:
        with open(path, 'rb') as f:
            f.seek(start)
            if start:
                f.readline(LONG_LINE)                   # the end of a line that began before the window: not an entry
            while f.tell() < end and len(found) < len(want):
                line = f.readline(LONG_LINE)
                if not line:
                    break
                if line.endswith(b'\n') and b'tool_use' in line and b'Artifact' in line:
                    calls = {}
                    _note_tools(line, calls)
                    found.update((i, name) for i, name in calls.items() if i in want)
    except OSError:
        pass
    return found


class Scanner(object):
    """Looks at Claude Code's activity. Keeps what it read between looks (one per process; the loop's lives long)."""

    def __init__(self, root=None, clock=None, procs=None, live_folder=None):
        self.root = root or awake.projects_root()
        self.found = awake.Transcripts(self.root)
        self.clock = clock or usage.Clock()
        self.procs = procs or count_claude_processes
        self.live_folder = live_folder
        self.offsets = {}       # path -> (inode, offset read up to, inside a line being skipped)
        self.events = {}        # path -> [(t, kind, url)]
        self.tools = {}         # path -> {tool call id: name} of its calls to the watch tools
        self.cuts = {}          # path -> where its first read began (0: from the start)
        self.accounts = {}      # path -> chatfile.Read: the account lines read so far (its steps)
        self.born = {}          # path -> {tool call id: True} of the calls whose answer was a Claude Doc born
        self.turns = {}         # path -> (mtime, open/closed/unknown)
        self.proc_count = None
        self.proc_at = None
        self.lock = threading.Lock()

    def _forget(self, path):
        for d in (self.offsets, self.events, self.tools, self.cuts, self.accounts, self.born):
            d.pop(path, None)

    def _session_of(self, path):
        rel = os.path.relpath(path, self.root).split(os.sep)
        if len(rel) < 2:
            return rel[0]
        return rel[0] + '/' + (rel[1][:-6] if rel[1].endswith('.jsonl') else rel[1])

    def _turn(self, path, mtime):
        known = self.turns.get(path)
        if known and known[0] == mtime:
            return known[1]
        state = awake.turn_state(path)
        self.turns[path] = (mtime, state)
        return state

    def _status_moved(self, now):
        """The newest time the status line saved new numbers (the file is written only when they change)."""
        folder = self.live_folder or core.data_path(usage.LIVE_DIR)
        newest = None
        try:
            with os.scandir(folder) as it:
                for e in it:
                    if not e.name.endswith('.json'):
                        continue
                    try:
                        t = e.stat(follow_symlinks=False).st_mtime
                    except OSError:
                        continue
                    if t <= now + usage.AHEAD and (newest is None or t > newest):
                        newest = t
        except OSError:
            return None
        return newest

    def _read_new(self, path, mtime, budget):
        """Read a transcript on from where the last look stopped (a new file: its last FIRST_TAIL bytes), collecting
        events. A line longer than LONG_LINE (a big tool output) is skipped, across looks if it takes more than one
        budget. Returns the bytes read."""
        try:
            st = os.stat(path)
        except OSError:
            self._forget(path)
            return 0
        inode, off, skipping = self.offsets.get(path, (None, None, False))
        first = off is None or inode != st.st_ino or st.st_size < off
        if first:
            off, skipping = max(0, st.st_size - FIRST_TAIL), st.st_size > FIRST_TAIL
            self.events[path] = []
            self.tools[path] = {}
            self.cuts[path] = off                   # where the first read began (0: the whole file was read)
            self.accounts[path] = chatfile.Read()
            self.born[path] = {}
        if st.st_size == off:
            self.offsets[path] = (st.st_ino, off, skipping)
            return 0
        used = 0
        found = self.events.setdefault(path, [])
        tools = self.tools.setdefault(path, {})
        account = self.accounts.setdefault(path, chatfile.Read())
        born = self.born.setdefault(path, {})
        cut = self.cuts.get(path, 0)
        unknown = [] if cut else None               # answers whose call may lie before the cut
        pos = off
        try:
            with open(path, 'rb') as f:
                f.seek(off)
                while used < budget:
                    line = f.readline(LONG_LINE)
                    if not line:
                        break
                    used += len(line)
                    if skipping or not line.endswith(b'\n'):
                        if not line.endswith(b'\n') and len(line) < LONG_LINE:
                            break                    # still being written: read again next time
                        skipping = not line.endswith(b'\n')     # the end of a cut or huge line: not an entry
                        pos = f.tell()
                        continue
                    if len(line) <= chatfile.LINE_MAX and (ACCOUNT_MARKS[0] in line or ACCOUNT_MARKS[1] in line):
                        account.take(chatfile.parse_line(line.decode('utf-8', 'replace'), json.loads))
                    made = []
                    found.extend(events_in_line(line, mtime, tools, unknown, made))
                    for uid in made:
                        born[uid] = True
                    pos = f.tell()
        except OSError:
            return used
        del account.steps[:-STEPS_KEPT]
        while len(born) > TOOLS_KEPT:
            born.pop(next(iter(born)))
        if unknown:
            # a watch tool's answer whose call came before the first read began: look for those calls there, once
            want = set(i for i, _, _, _ in unknown)
            calls = _calls_before(path, cut, want, LOOK_BACK)
            used += min(cut, LOOK_BACK)
            tools.update(calls)
            found.extend((t, kind, url) for i, t, kind, url in unknown if i in calls)
        self.offsets[path] = (st.st_ino, pos, skipping)
        return used

    def look(self, timeline=(), now=None, light=False):
        """{'busy', 'idle_for_s', 'sessions', 'processes', 'obligations', 'partial'} (see the module's docstring).
        light: under load or on a low battery, only file times are looked at (no transcript is read, no ps run)."""
        with self.lock:
            return self._look(list(timeline or ()), self.clock.wall() if now is None else now, light)

    def _look(self, timeline, now, light):
        recent = self.found.recent(now, WATCH_KEEP)            # [(mtime, path)], newest first
        newest = recent[0][0] if recent else None
        busy = newest is not None and now - newest < IDLE_AFTER
        open_turn = False
        if not busy and not light:
            for mtime, path in recent[:MAX_TURNS]:
                if now - mtime > STUCK_CAP:
                    break
                if self._turn(path, mtime) == awake.OPEN:
                    open_turn = busy = True
                    break
        moved = self._status_moved(now)
        if moved is not None and now - moved < STATUS_MOVED:
            busy = True
        last = max([t for t in (newest, moved) if t is not None], default=None)
        idle_for = 0.0 if busy else (None if last is None else max(0.0, now - last))

        partial = False
        if not light:
            budget = MAX_BYTES
            for mtime, path in recent[:MAX_FILES]:
                if budget <= 0:
                    partial = True
                    break
                budget -= self._read_new(path, mtime, budget)
            live = set(p for _, p in recent)
            for path in [p for p in self.events if p not in live]:
                self._forget(path)
            for path in [p for p in self.turns if p not in live]:
                self.turns.pop(path, None)

        session_last = {}
        for mtime, path in recent:
            key = self._session_of(path)
            session_last[key] = max(session_last.get(key, 0.0), mtime)
        obligations = self._obligations(timeline, now, session_last)
        limits = [t for evs in self.events.values() for t, kind, _ in evs
                  if kind == 'limit' and now - LIMIT_FRESH <= t <= now + usage.AHEAD]

        if not light and (self.proc_at is None or now - self.proc_at >= PROCS_EVERY or now < self.proc_at):
            try:
                self.proc_count = self.procs()
            except Exception:  # noqa: BLE001 - a count is only shown
                self.proc_count = None
            self.proc_at = now
        sessions = [{'last_write_s': round(max(0.0, now - t), 1)}
                    for _, t in sorted(session_last.items(), key=lambda kv: -kv[1])[:10]]
        if sessions and open_turn:
            sessions[0]['open_turn'] = True
        return {'busy': busy, 'idle_for_s': idle_for, 'sessions': sessions, 'processes': self.proc_count,
                'obligations': obligations, 'partial': partial, 'limited_at': max(limits) if limits else None}

    def _obligations(self, timeline, now, session_last):
        out = {}
        watches = {}
        for path, evs in self.events.items():
            session = self._session_of(path)
            read = self.accounts.get(path)
            steps = chatfile.settle(read.steps) if read is not None else []
            born = self.born.get(path) or {}
            for t, kind, url in evs:
                if kind == 'limit':
                    continue
                if kind in ('artifact', 'doc', 'connector'):
                    if not now - OBLIGATION_KEEP <= t <= now + usage.AHEAD:
                        continue
                    if kind == 'connector':
                        name, uid = url
                        if uid is not None and uid in born:
                            continue                    # a Claude Doc born: its doc obligation, not this too
                        key, what = ('connector', name), {'connector': name}
                    else:
                        key, what = (kind, url), {'url': url}
                    if key not in out or out[key]['at'] < t:
                        out[key] = dict(what, account_id=owner_at(timeline, t), org=chatfile.on_at(steps, t),
                                        kind=kind, at=t, until=t + OBLIGATION_KEEP)
                else:
                    k = (session, url)
                    if k not in watches or watches[k][0] <= t:
                        watches[k] = (t, kind, chatfile.on_at(steps, t))
        for (session, url), (t, kind, org) in watches.items():
            if kind != 'watch':
                continue
            until = max(t, session_last.get(session, t)) + WATCH_KEEP
            if until > now and t <= now + usage.AHEAD:
                key = ('watch', url)
                if key not in out or out[key]['at'] < t:
                    out[key] = {'account_id': owner_at(timeline, t), 'org': org, 'kind': 'watch', 'url': url,
                                'at': t, 'until': until}
        return sorted(out.values(), key=lambda o: -o['at'])


_SCANNER = []
_LOCK = threading.Lock()


def activity(timeline=(), now=None, light=False):
    """One look with this process's Scanner (made once, kept between looks)."""
    with _LOCK:
        if not _SCANNER:
            _SCANNER.append(Scanner())
        scanner = _SCANNER[0]
    return scanner.look(timeline, now, light)


def _reset_for_tests():
    with _LOCK:
        del _SCANNER[:]

